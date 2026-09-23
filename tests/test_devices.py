#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ファイルシステムとデバイスの状態のテスト。

最も守りたいのは **devid とデバイスの対応** である。``devinfo/<devid>/`` には
デバイス名が無く、``devices/`` は名前順に並ぶだけなので、名前順で突き合わせると
実機で全デバイスの情報が入れ替わる (実際そうなっていた)。エラーカウンタの
持ち主を取り違えるのは、このダッシュボードで最も避けたい誤りである。
"""

import json
import os

import pytest

from btrfs_timeline.core import devices, mounts, operations

UUID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

# devid の順と名前の順がわざと食い違う構成。名前順に対応させると全部ずれる。
LAYOUT = {
    1: {'name': 'sdd1', 'missing': 0, 'writeable': 1},
    2: {'name': 'sdc1', 'missing': 0, 'writeable': 1},
    3: {'name': 'sda1', 'missing': 0, 'writeable': 1},
}


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    """``/sys/fs/btrfs`` と ``/sys/class/block`` を作り物に差し替える。"""
    root = tmp_path / 'btrfs'
    block = tmp_path / 'block'
    base = root / UUID

    _write(str(base / 'label'), 'tank\n')
    _write(str(base / 'exclusive_operation'), 'none\n')
    for devid, info in LAYOUT.items():
        os.makedirs(str(base / 'devices' / info['name']), exist_ok=True)
        _write(str(base / 'devinfo' / str(devid) / 'missing'),
               '{0}\n'.format(info['missing']))
        _write(str(base / 'devinfo' / str(devid) / 'writeable'),
               '{0}\n'.format(info['writeable']))
        _write(str(base / 'devinfo' / str(devid) / 'replace_target'), '0\n')
        # 512 バイトセクタ数。1 GiB 相当
        _write(str(block / info['name'] / 'size'), '2097152\n')

    for kind, profile, total, used in (('data', 'raid1', 1000, 500),
                                       ('metadata', 'raid1', 200, 100),
                                       ('system', 'raid1', 32, 16)):
        directory = base / 'allocation' / kind
        os.makedirs(str(directory / profile), exist_ok=True)
        _write(str(directory / 'total_bytes'), '{0}\n'.format(total))
        _write(str(directory / 'bytes_used'), '{0}\n'.format(used))

    monkeypatch.setattr(devices, 'SYSFS_ROOT', str(root))
    monkeypatch.setattr(devices, 'SYSFS_BLOCK', str(block))
    return base


@pytest.fixture
def stats(monkeypatch):
    """``btrfs device stats --format json`` の出力を差し替える。

    実際に btrfs を呼ばずに、本物と同じ経路 (解析まで) を通す。
    """
    payload = {'device-stats': [
        {'device': '/dev/' + info['name'], 'devid': devid,
         'write_io_errs': 0, 'read_io_errs': 0, 'flush_io_errs': 0,
         'corruption_errs': 7 if devid == 2 else 0, 'generation_errs': 0}
        for devid, info in LAYOUT.items()
    ]}

    def fake_run(operation, confirmed=False, confirmation=None, timeout=None):
        return operations.Result(operation=operation, returncode=0,
                                 stdout=json.dumps(payload), stderr='')

    monkeypatch.setattr(operations, 'run', fake_run)
    return payload


def _mounts():
    return [mounts.MountPoint(mount_point='/mnt/tank', fs_type='btrfs',
                              device='/dev/sda1', subvol='/', root='/')]


def test_devid_comes_from_the_command_not_from_the_name_order(sysfs, stats):
    """**名前順で対応させてはいけない。** devid=1 は sdd1 である。"""
    found = devices.discover(_mounts())[0]
    assert [(d.devid, d.name) for d in found.devices] == [
        (1, 'sdd1'), (2, 'sdc1'), (3, 'sda1')]


def test_error_counters_land_on_the_right_device(sysfs, stats):
    """カウンタの持ち主を取り違えない。ここがずれると診断が逆を向く。"""
    by_devid = {d.devid: d for d in devices.discover(_mounts())[0].devices}
    assert by_devid[2].errors['corruption_errs'] == 7
    assert by_devid[2].has_errors is True
    assert by_devid[1].has_errors is False
    assert by_devid[3].has_errors is False


def test_reports_the_command_it_ran(sysfs, stats):
    """裏で動いたコマンドを隠さない。"""
    found = devices.discover(_mounts())[0]
    assert found.stats_command is not None
    assert found.stats_command.risk == operations.SAFE
    assert found.stats_command.needs_root is False
    assert 'device stats' in found.stats_command.display()


def test_reads_label_and_allocation(sysfs, stats):
    found = devices.discover(_mounts())[0]
    assert found.label == 'tank'
    assert found.uuid == UUID
    by_kind = {a.kind: a for a in found.allocations}
    assert by_kind['data'].profile == 'raid1'
    assert by_kind['data'].total_bytes == 1000
    assert by_kind['data'].used_bytes == 500


def test_matches_mount_points_through_the_device_names(sysfs, stats):
    found = devices.discover(_mounts())[0]
    assert found.mount_points == ['/mnt/tank']


def test_device_size_is_sectors_times_512(sysfs, stats):
    """``/sys/class/block/<name>/size`` は論理セクタサイズに関わらず 512 単位。"""
    assert devices.discover(_mounts())[0].devices[0].size == 2097152 * 512


def test_a_missing_device_makes_the_filesystem_degraded(sysfs, stats):
    _write(str(sysfs / 'devinfo' / '2' / 'missing'), '1\n')
    found = devices.discover(_mounts())[0]
    assert found.degraded is True
    assert {d.devid: d.missing for d in found.devices}[2] is True


def test_a_healthy_filesystem_is_not_degraded(sysfs, stats):
    assert devices.discover(_mounts())[0].degraded is False


def test_exclusive_operation_is_reported(sysfs, stats):
    """balance や replace が走っている最中かどうかは、操作の可否を左右する。"""
    _write(str(sysfs / 'exclusive_operation'), 'balance\n')
    assert devices.discover(_mounts())[0].exclusive_operation == 'balance'


def test_survives_btrfs_being_unavailable(sysfs, monkeypatch):
    """``btrfs`` が入っていなくても、画面の残りは出せる。"""
    def explode(operation, confirmed=False, confirmation=None, timeout=None):
        raise OSError('no such command')

    monkeypatch.setattr(operations, 'run', explode)
    found = devices.discover(_mounts())[0]
    assert found.label == 'tank'
    assert [d.devid for d in found.devices] == [1, 2, 3]
    # 名前は分からないが、devid と欠損の有無は sysfs から分かる
    assert all(d.name == '' for d in found.devices)
    assert found.degraded is False


def test_no_btrfs_filesystems_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(devices, 'SYSFS_ROOT', str(tmp_path / 'nowhere'))
    assert devices.discover([]) == []


# --------------------------------------------------------------------------
# デバイスの操作 — 合言葉で守る
# --------------------------------------------------------------------------

def test_removing_a_device_is_dangerous_and_asks_for_its_name():
    """取り外しは失敗するとプールごと失いうる。対象を打たせる。"""
    operation = devices.remove_operation('/dev/sdb', '/mnt')
    assert operation.argv == ['btrfs', 'device', 'remove', '/dev/sdb', '/mnt']
    assert operation.risk == operations.DANGEROUS
    assert operation.confirm_token == '/dev/sdb'


def test_replace_confirms_the_device_that_gets_overwritten():
    """**合言葉は target。** 消えるのはそちらである。

    source は抜ける側で、中身は移される。target は丸ごと上書きされる。
    打ち間違いで失われるのは target なので、確認させるのも target でなければ
    確認の意味が無い。
    """
    operation = devices.replace_operation('/dev/sdb', '/dev/sdc', '/mnt')
    assert operation.argv == ['btrfs', 'replace', 'start',
                              '/dev/sdb', '/dev/sdc', '/mnt']
    assert operation.risk == operations.DANGEROUS
    assert operation.confirm_token == '/dev/sdc'


def test_adding_a_device_is_only_caution():
    """btrfs は既存のファイルシステムがあれば拒否する。その守りがある間は caution。"""
    operation = devices.add_operation('/dev/sdb', '/mnt')
    assert operation.argv == ['btrfs', 'device', 'add', '/dev/sdb', '/mnt']
    assert operation.risk == operations.CAUTION
    assert operation.confirm_token is None


def test_forcing_an_add_removes_that_protection_so_it_becomes_dangerous():
    """``-f`` は「そこにあるものを消してよい」という意味になる。"""
    operation = devices.add_operation('/dev/sdb', '/mnt', force=True)
    assert operation.argv == ['btrfs', 'device', 'add', '-f', '/dev/sdb', '/mnt']
    assert operation.risk == operations.DANGEROUS
    assert operation.confirm_token == '/dev/sdb'


def test_replace_status_only_reads():
    operation = devices.replace_status_operation('/mnt')
    assert operation.risk == operations.SAFE
    assert operation.needs_root is True


def test_cancelling_a_replace_keeps_the_original():
    operation = devices.replace_cancel_operation('/mnt')
    assert operation.risk == operations.CAUTION


# --------------------------------------------------------------------------
# CLI — 「はい」では通らないこと
# --------------------------------------------------------------------------

@pytest.fixture
def never_runs(monkeypatch):
    attempted = []

    def fake_run(operation, confirmed=False, confirmation=None, timeout=None):
        attempted.append((operation, confirmation))
        return operations.Result(operation=operation, returncode=0,
                                 stdout='', stderr='')

    monkeypatch.setattr('btrfs_timeline.cli.operations_module.run', fake_run)
    monkeypatch.setattr('btrfs_timeline.cli.os.geteuid', lambda: 0)
    monkeypatch.setattr('btrfs_timeline.cli.sys.stdin.isatty', lambda: False)
    return attempted


def test_yes_is_not_enough_for_a_dangerous_operation(never_runs, capsys):
    """**ここが今回の要点。** --yes で危険な操作が通ってはいけない。"""
    from btrfs_timeline import cli

    assert cli.main(['device', 'remove', '/dev/sdb', '--path', '/mnt', '--yes']) == 1
    assert never_runs == []
    assert '/dev/sdb' in capsys.readouterr().err


def test_the_wrong_name_runs_nothing(never_runs, capsys):
    from btrfs_timeline import cli

    assert cli.main(['device', 'remove', '/dev/sdb', '--path', '/mnt',
                     '--confirm', '/dev/sdc']) == 1
    assert never_runs == []


def test_the_exact_name_lets_it_through(never_runs):
    from btrfs_timeline import cli

    assert cli.main(['device', 'remove', '/dev/sdb', '--path', '/mnt',
                     '--confirm', '/dev/sdb']) == 0
    operation, confirmation = never_runs[0]
    assert operation.argv == ['btrfs', 'device', 'remove', '/dev/sdb', '/mnt']
    # 合言葉は実行側にも渡る。確認したことが呼び出しの中で完結しない
    assert confirmation == '/dev/sdb'


def test_replace_asks_for_the_target_not_the_source(never_runs, capsys):
    from btrfs_timeline import cli

    # source を答えても通らない
    assert cli.main(['device', 'replace', '/dev/sdb', '/dev/sdc', '--path', '/mnt',
                     '--confirm', '/dev/sdb']) == 1
    assert never_runs == []

    assert cli.main(['device', 'replace', '/dev/sdb', '/dev/sdc', '--path', '/mnt',
                     '--confirm', '/dev/sdc']) == 0
    assert never_runs[0][1] == '/dev/sdc'


def test_a_typed_answer_is_accepted_interactively(never_runs, monkeypatch):
    from btrfs_timeline import cli

    monkeypatch.setattr('btrfs_timeline.cli.sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt='': '/dev/sdb')
    assert cli.main(['device', 'remove', '/dev/sdb', '--path', '/mnt']) == 0
    assert len(never_runs) == 1


def test_adding_still_only_needs_yes(never_runs):
    """危険でないものにまで合言葉を求めると、合言葉が軽く見られる。"""
    from btrfs_timeline import cli

    assert cli.main(['device', 'add', '/dev/sdb', '--path', '/mnt', '--yes']) == 0
    assert never_runs[0][1] is None
