#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ファイル履歴の組み立てのテスト。"""

import os

from btrfs_timeline.core import history, mounts, snapshots

INFO_XML = '''<?xml version="1.0"?>
<snapshot><type>single</type><num>{num}</num><date>{date}</date><description></description></snapshot>
'''


def _make_mount(path):
    return mounts.MountPoint(
        mount_point=str(path), fs_type='btrfs', device='/dev/test',
        subvol='/@home', root='/@home',
    )


def _add_snapshot(root, num, date, files):
    """スナップショットを 1 つ作る。``files`` は ``{相対パス: 内容 or None}``。

    内容が None の場合はそのファイルを作らない (その時点で存在しなかったことを表す)。
    """
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
        # 内容が同じ版は mtime も同じにする (btrfs のスナップショットは mtime を保つ)
        stamp = 1000000 + len(content)
        os.utime(target, (stamp, stamp))
    return content_root


def test_identical_versions_are_collapsed(tmp_path):
    """内容が変わっていない期間は 1 つの版に畳まれる。

    これが無いと、スナップショットの数だけ同じ行が並んでしまう
    (実測では 35 スナップショットに対してユニークな版が 1 つ、ということが普通に起きる)。
    """
    for num, date in ((1, '2026-01-01 00:00:00'), (2, '2026-02-01 00:00:00'),
                      (3, '2026-03-01 00:00:00')):
        _add_snapshot(tmp_path, num, date, {'doc.txt': 'same'})

    target = tmp_path / 'doc.txt'
    target.write_text('same', encoding='utf-8')
    os.utime(target, (1000004, 1000004))

    versions = history.list_versions(str(target), mount=_make_mount(tmp_path))
    snapshot_versions = [v for v in versions if not v.is_live]
    assert len(snapshot_versions) == 1
    assert snapshot_versions[0].snapshot_count == 3
    assert snapshot_versions[0].first_snapshot_id == '1'
    assert snapshot_versions[0].last_snapshot_id == '3'


def test_changed_content_creates_new_version(tmp_path):
    """内容が変わればそこで版が分かれる。"""
    _add_snapshot(tmp_path, 1, '2026-01-01 00:00:00', {'doc.txt': 'v1'})
    _add_snapshot(tmp_path, 2, '2026-02-01 00:00:00', {'doc.txt': 'v2-longer'})
    _add_snapshot(tmp_path, 3, '2026-03-01 00:00:00', {'doc.txt': 'v2-longer'})

    target = tmp_path / 'doc.txt'
    target.write_text('v2-longer', encoding='utf-8')

    versions = [v for v in history.list_versions(str(target), mount=_make_mount(tmp_path))
                if not v.is_live]
    assert len(versions) == 2
    assert versions[0].snapshot_count == 1
    assert versions[1].snapshot_count == 2


def test_absent_period_is_recorded(tmp_path):
    """存在しなかった期間も 1 つの版として残る。

    これを落とすと、削除されてから復活したファイルの履歴が繋がって見えてしまう。
    """
    _add_snapshot(tmp_path, 1, '2026-01-01 00:00:00', {'doc.txt': None})
    _add_snapshot(tmp_path, 2, '2026-02-01 00:00:00', {'doc.txt': 'created'})
    _add_snapshot(tmp_path, 3, '2026-03-01 00:00:00', {'doc.txt': None})

    target = tmp_path / 'doc.txt'
    versions = [v for v in history.list_versions(str(target), mount=_make_mount(tmp_path))
                if not v.is_live]
    assert [v.exists for v in versions] == [False, True, False]


def test_symlink_in_snapshot_does_not_escape_to_live(tmp_path):
    """スナップショット内の絶対シンボリックリンクがライブのファイルを指していても、
    ライブ側の内容を「過去の版」として報告しない。

    ``os.stat`` を使うとリンクを追ってライブのファイルを読んでしまい、
    利用者は「この時点で既にこの内容だった」と誤読する。
    """
    live = tmp_path / 'real.txt'
    live.write_text('CURRENT CONTENT THAT IS LONG', encoding='utf-8')

    entry = tmp_path / '.snapshots' / '1'
    content_root = entry / 'snapshot'
    content_root.mkdir(parents=True)
    entry.joinpath('info.xml').write_text(
        INFO_XML.format(num=1, date='2026-01-01 00:00:00'), encoding='utf-8')
    # スナップショット内に、ライブのファイルを指す絶対リンクを置く
    os.symlink(str(live), str(content_root / 'doc.txt'))

    versions = [v for v in history.list_versions(str(tmp_path / 'doc.txt'),
                                                 mount=_make_mount(tmp_path)) if not v.is_live]
    assert len(versions) == 1
    # リンク自体のサイズ (リンク先パスの文字列長) であり、ライブの内容長ではない
    assert versions[0].size != len('CURRENT CONTENT THAT IS LONG')


def test_live_version_is_appended(tmp_path):
    """ライブ版が最後に必ず 1 行付く。"""
    _add_snapshot(tmp_path, 1, '2026-01-01 00:00:00', {'doc.txt': 'old'})
    target = tmp_path / 'doc.txt'
    target.write_text('new content', encoding='utf-8')

    versions = history.list_versions(str(target), mount=_make_mount(tmp_path))
    assert versions[-1].is_live is True
    assert versions[-1].exists is True
    assert versions[-1].size == len('new content')


def test_live_can_be_excluded(tmp_path):
    """``include_live=False`` ならライブ版を含めない。"""
    _add_snapshot(tmp_path, 1, '2026-01-01 00:00:00', {'doc.txt': 'old'})
    target = tmp_path / 'doc.txt'
    target.write_text('new', encoding='utf-8')

    versions = history.list_versions(str(target), include_live=False, mount=_make_mount(tmp_path))
    assert all(not v.is_live for v in versions)
