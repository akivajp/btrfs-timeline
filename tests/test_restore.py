#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""復元のテスト。

ここで守りたいのは「復元したつもりで現在の内容を壊す/読み違える」系の事故である。
速度 (reflink かコピーか) は環境依存なので、テストでは方法を固定しない。
"""

import argparse
import datetime
import errno
import fcntl
import os

import pytest

from btrfs_timeline.core import mounts, restore

INFO_XML = '''<?xml version="1.0"?>
<snapshot><type>single</type><num>{num}</num><date>{date}</date><description></description></snapshot>
'''


def _make_mount(path):
    return mounts.MountPoint(
        mount_point=str(path), fs_type='btrfs', device='/dev/test',
        subvol='/@home', root='/@home',
    )


def _add_snapshot(root, num, date, files):
    """スナップショットを 1 つ作る。``files`` は ``{相対パス: 内容 or None}``。"""
    entry = root / '.snapshots' / str(num)
    content_root = entry / 'snapshot'
    content_root.mkdir(parents=True)
    entry.joinpath('info.xml').write_text(INFO_XML.format(num=num, date=date), encoding='utf-8')
    for rel, content in files.items():
        if content is None:
            continue
        target = content_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        stamp = 1000000 + len(content)
        os.utime(target, (stamp, stamp))
    return content_root


# --------------------------------------------------------------------------
# 復元先の名前の付け方
# --------------------------------------------------------------------------

def test_sibling_path_keeps_the_extension(tmp_path):
    """日時は拡張子の前に入れる (``notes.md`` → ``notes.<日時>.md``)。

    末尾に付けて ``notes.md.20260401T0900`` にすると、復元したファイルを
    エディタやビューアが開けなくなる。
    """
    moment = datetime.datetime(2026, 4, 1, 0, 0, tzinfo=datetime.timezone.utc)
    result = restore.sibling_path(str(tmp_path / 'notes.md'), moment)
    name = os.path.basename(result)
    assert name.startswith('notes.')
    assert name.endswith('.md')
    assert os.path.dirname(result) == str(tmp_path)


def test_sibling_path_avoids_collisions(tmp_path):
    """同じ秒に 2 回復元しても、既存のファイルを踏まない。"""
    moment = datetime.datetime(2026, 4, 1, 0, 0, tzinfo=datetime.timezone.utc)
    first = restore.sibling_path(str(tmp_path / 'notes.md'), moment)
    open(first, 'w', encoding='utf-8').close()
    second = restore.sibling_path(str(tmp_path / 'notes.md'), moment)
    assert second != first
    assert not os.path.lexists(second)


# --------------------------------------------------------------------------
# 複製そのもの
# --------------------------------------------------------------------------

def test_clone_file_copies_contents_and_metadata(tmp_path):
    """内容・パーミッション・mtime が引き継がれる。

    mtime を引き継ぐのは、復元後のライブファイルがその版と同じ版として
    履歴上に畳まれるのが正しいため (版の判定は mtime + size)。
    """
    source = tmp_path / 'source.txt'
    source.write_text('old content', encoding='utf-8')
    os.chmod(source, 0o640)
    os.utime(source, (1000000, 1000000))

    destination = str(tmp_path / 'restored.txt')
    method = restore.clone_file(str(source), destination)

    assert method in ('reflink', 'copy')
    assert open(destination, encoding='utf-8').read() == 'old content'
    info = os.stat(destination)
    assert info.st_mode & 0o777 == 0o640
    assert int(info.st_mtime) == 1000000


def test_clone_file_falls_back_to_copy(tmp_path, monkeypatch):
    """reflink が使えない環境 (別ファイルシステム等) では通常のコピーに落ちる。"""
    def _refuse(*_args, **_kwargs):
        raise OSError(errno.EXDEV, 'Invalid cross-device link')

    monkeypatch.setattr(fcntl, 'ioctl', _refuse)

    source = tmp_path / 'source.txt'
    source.write_text('payload', encoding='utf-8')
    destination = str(tmp_path / 'restored.txt')

    assert restore.clone_file(str(source), destination) == 'copy'
    assert open(destination, encoding='utf-8').read() == 'payload'


def test_clone_file_restores_symlink_as_symlink(tmp_path):
    """スナップショット内のシンボリックリンクは、辿らずリンクとして復元する。

    ここを ``open()`` で読んでしまうと、絶対リンクの場合に
    **現在のファイルの内容** を「過去の版」として書き込むことになる。
    履歴ツールとしては最悪の部類の事故なので回帰テストで固定する。
    """
    live = tmp_path / 'live.txt'
    live.write_text('CURRENT CONTENT', encoding='utf-8')
    link = tmp_path / 'snapshot-link'
    os.symlink(str(live), link)

    destination = str(tmp_path / 'restored')
    method = restore.clone_file(str(link), destination)

    assert method == 'symlink'
    assert os.path.islink(destination)
    assert os.readlink(destination) == str(live)


def test_clone_file_leaves_no_temporary_on_failure(tmp_path, monkeypatch):
    """途中で失敗しても、一時ファイルを残さない。"""
    source = tmp_path / 'source.txt'
    source.write_text('payload', encoding='utf-8')

    def _explode(*_args, **_kwargs):
        raise OSError('boom')

    monkeypatch.setattr(restore, '_copy_metadata', _explode)

    with pytest.raises(OSError):
        restore.clone_file(str(source), str(tmp_path / 'restored.txt'))

    leftovers = [n for n in os.listdir(tmp_path) if 'btrfs-timeline' in n]
    assert leftovers == []


# --------------------------------------------------------------------------
# 計画と実行
# --------------------------------------------------------------------------

def test_default_restore_does_not_touch_the_original(tmp_path):
    """既定では兄弟ファイルに書き、現在のファイルには触れない。"""
    source = tmp_path / 'old.txt'
    source.write_text('version from 2026', encoding='utf-8')
    target = tmp_path / 'notes.txt'
    target.write_text('CURRENT', encoding='utf-8')

    plan = restore.plan_restore(str(target), str(source))
    result = restore.execute(plan)

    assert plan.in_place is False
    assert plan.backup is None
    assert plan.destination != str(target)
    assert target.read_text(encoding='utf-8') == 'CURRENT'
    assert open(plan.destination, encoding='utf-8').read() == 'version from 2026'
    assert result.dry_run is False


def test_in_place_restore_keeps_a_backup(tmp_path):
    """``in_place`` では上書きするが、現在の内容を退避してから行う。"""
    source = tmp_path / 'old.txt'
    source.write_text('version from 2026', encoding='utf-8')
    target = tmp_path / 'notes.txt'
    target.write_text('CURRENT', encoding='utf-8')

    plan = restore.plan_restore(str(target), str(source), in_place=True)
    restore.execute(plan)

    assert plan.destination == str(target)
    assert plan.backup is not None
    assert target.read_text(encoding='utf-8') == 'version from 2026'
    assert open(plan.backup, encoding='utf-8').read() == 'CURRENT'


def test_in_place_restore_without_existing_file_needs_no_backup(tmp_path):
    """削除済みのファイルを戻すときは、退避するものが無い。"""
    source = tmp_path / 'old.txt'
    source.write_text('deleted by accident', encoding='utf-8')
    target = tmp_path / 'gone.txt'

    plan = restore.plan_restore(str(target), str(source), in_place=True)
    restore.execute(plan)

    assert plan.backup is None
    assert target.read_text(encoding='utf-8') == 'deleted by accident'


def test_dry_run_writes_nothing(tmp_path):
    """``dry_run`` は計画を返すだけで、ファイルシステムを変えない。"""
    source = tmp_path / 'old.txt'
    source.write_text('version from 2026', encoding='utf-8')
    target = tmp_path / 'notes.txt'
    target.write_text('CURRENT', encoding='utf-8')
    before = sorted(os.listdir(tmp_path))

    plan = restore.plan_restore(str(target), str(source), in_place=True)
    result = restore.execute(plan, dry_run=True)

    assert result.method == 'dry-run'
    assert result.dry_run is True
    assert sorted(os.listdir(tmp_path)) == before
    assert target.read_text(encoding='utf-8') == 'CURRENT'


def test_explicit_destination_refuses_to_overwrite(tmp_path):
    """``--to`` が既存のファイルを指していたら、``overwrite`` 無しでは断る。"""
    source = tmp_path / 'old.txt'
    source.write_text('old', encoding='utf-8')
    occupied = tmp_path / 'occupied.txt'
    occupied.write_text('IMPORTANT', encoding='utf-8')

    with pytest.raises(FileExistsError):
        restore.plan_restore(str(tmp_path / 'notes.txt'), str(source),
                             destination=str(occupied))

    plan = restore.plan_restore(str(tmp_path / 'notes.txt'), str(source),
                                destination=str(occupied), overwrite=True)
    restore.execute(plan)
    assert occupied.read_text(encoding='utf-8') == 'old'


def test_directory_restore_is_rejected(tmp_path):
    """ディレクトリの復元は未対応。黙って一部だけ復元するより断る。"""
    directory = tmp_path / 'a-directory'
    directory.mkdir()
    with pytest.raises(IsADirectoryError):
        restore.plan_restore(str(tmp_path / 'notes.txt'), str(directory))


def test_missing_source_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        restore.plan_restore(str(tmp_path / 'notes.txt'), str(tmp_path / 'nope.txt'))


# --------------------------------------------------------------------------
# 版の選択 (CLI 側のロジック)
# --------------------------------------------------------------------------

def _args(**overrides):
    defaults = {'snapshot': None, 'index': None}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _history_fixture(tmp_path):
    """3 つのスナップショットと、2 種類の内容を持つ履歴を作る。"""
    from btrfs_timeline.core import snapshots as snapshots_module

    _add_snapshot(tmp_path, 1, '2026-01-01 00:00:00', {'notes.txt': 'first'})
    _add_snapshot(tmp_path, 2, '2026-02-01 00:00:00', {'notes.txt': 'first'})
    _add_snapshot(tmp_path, 3, '2026-03-01 00:00:00', {'notes.txt': 'second'})
    target = tmp_path / 'notes.txt'
    target.write_text('live', encoding='utf-8')
    mount = _make_mount(tmp_path)
    return str(target), mount, snapshots_module.discover(mount)


def test_default_selection_is_the_newest_snapshot_version(tmp_path):
    """版を指定しない場合は「存在する最新の版」= 直前の内容を選ぶ。"""
    from btrfs_timeline import cli

    target, mount, snapshot_list = _history_fixture(tmp_path)
    source, _moment, label, index = cli._resolve_restore_source(
        target, _args(), mount, snapshot_list)

    assert source is not None
    assert open(source, encoding='utf-8').read() == 'second'
    assert '#' in label
    # 版番号も返す。復元の JSON 出力に載せて、Web UI と形を揃えるため
    assert index == 2


def test_selection_by_snapshot_id(tmp_path):
    """``--snapshot`` はその ID のスナップショット内の実体を直接指す。"""
    from btrfs_timeline import cli

    target, mount, snapshot_list = _history_fixture(tmp_path)
    source, _moment, _label, index = cli._resolve_restore_source(
        target, _args(snapshot='1'), mount, snapshot_list)

    assert open(source, encoding='utf-8').read() == 'first'
    # スナップショット指定では版番号が決まらないので None
    assert index is None


def test_selection_rejects_the_live_version(tmp_path):
    """ライブ版を復元元に指定するのは無意味なので断る。"""
    from btrfs_timeline import cli

    target, mount, snapshot_list = _history_fixture(tmp_path)
    # 版は [first, second, live] の 3 行になるので、3 番がライブ版
    source, _moment, reason, _index = cli._resolve_restore_source(
        target, _args(index=3), mount, snapshot_list)

    assert source is None
    assert 'live' in reason
