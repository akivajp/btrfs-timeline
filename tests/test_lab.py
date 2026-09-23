#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ループバック上の使い捨て btrfs に対する、通しの検証。

デバイスの追加・取り外し・置換は、実機で試した時点でそのアレイを変えてしまう。
そこで ``scripts/loopback-lab.sh`` が用意する使い捨てのファイルシステムを相手に、
**本物の btrfs に対して本当に実行して** 確かめる。

実験室が無ければ全て飛ばす。用意するには::

    sudo ./scripts/loopback-lab.sh setup

デバイスを変更する操作は root を要するので、``sudo -n`` が通る場合だけ実行する
(パスワードを求められる状況では、読み取りだけ確かめて残りは飛ばす)。
"""

import json
import os
import subprocess

import pytest

from btrfs_timeline.core import devices, maintenance, mounts, operations

LAB = os.environ.get('BTRFS_TIMELINE_LAB', '/var/tmp/btrfs-timeline-lab')
MOUNT = os.path.join(LAB, 'mnt')
LOOPS = os.path.join(LAB, 'loops')


def _lab_devices():
    """実験室が用意した loop デバイスの一覧。"""
    try:
        with open(LOOPS, encoding='utf-8') as handle:
            return [line.strip() for line in handle if line.strip()]
    except OSError:
        return []


def _mounted():
    """実験室がマウントされているか。"""
    return any(mount.mount_point == MOUNT
               for mount in mounts.btrfs_mounts())


def _can_sudo():
    """パスワード無しで sudo が通るか。通らなければ変更系は飛ばす。"""
    try:
        return subprocess.run(['sudo', '-n', 'true'],
                              capture_output=True).returncode == 0
    except OSError:
        return False


lab = pytest.mark.skipif(
    not _mounted(),
    reason='実験室がありません (sudo ./scripts/loopback-lab.sh setup)')

writable = pytest.mark.skipif(
    not _can_sudo(),
    reason='パスワード無しの sudo が使えないため、変更系は飛ばします')


def _run(operation, confirmation=None):
    """実験室に対して、sudo 経由で実行する。"""
    return operations.run(operation.with_sudo(), confirmation=confirmation,
                          timeout=120)


@pytest.fixture
def lab_filesystem():
    """実験室のファイルシステムを、このツールの目で見たもの。"""
    found = [f for f in devices.discover() if MOUNT in f.mount_points]
    assert found, '実験室が見つかりません'
    return found[0]


# --------------------------------------------------------------------------
# 読むだけ — 本物のファイルシステムで、見え方が正しいか
# --------------------------------------------------------------------------

@lab
def test_sees_every_device_of_the_lab(lab_filesystem):
    """実験室のデバイスが、数も名前も一致して見える。"""
    expected = set(_lab_devices())
    assert {d.path for d in lab_filesystem.devices} == expected


@lab
def test_devid_mapping_agrees_with_btrfs_itself(lab_filesystem):
    """**このツールの devid 対応が、btrfs の申告と一致する。**

    名前順で対応させていた頃は、ここがずれていた。作り物ではなく本物で確かめる。
    """
    result = subprocess.run(
        ['btrfs', '--format', 'json', 'device', 'stats', MOUNT],
        capture_output=True, text=True)
    authoritative = {entry['devid']: entry['device']
                     for entry in json.loads(result.stdout)['device-stats']}
    assert {d.devid: d.path for d in lab_filesystem.devices} == authoritative


@lab
def test_profile_is_read_from_sysfs(lab_filesystem):
    """スクリプトは raid1 で作る。sysfs から読めていることの確認。"""
    by_kind = {a.kind: a for a in lab_filesystem.allocations}
    assert by_kind['data'].profile == 'raid1'
    assert by_kind['metadata'].profile == 'raid1'


@lab
def test_a_healthy_lab_is_not_degraded(lab_filesystem):
    assert lab_filesystem.degraded is False
    assert lab_filesystem.exclusive_operation == 'none'
    assert all(not d.has_errors for d in lab_filesystem.devices)


@lab
def test_scrub_status_needs_root_once_a_scrub_has_run():
    """**実験室が見つけた事実。**

    ``/var/lib/btrfs/scrub.status.<UUID>`` は root 専用 (0600) で作られる。
    そのため「一度も scrub していない間だけ」非特権で読め、一度走らせた後は
    読めなくなる。実機で確かめたときに通っていたのは、そのファイルシステムで
    一度も scrub していなかったからに過ぎなかった。
    """
    operation = maintenance.scrub_status_operation(MOUNT)
    result = operations.run(operation, timeout=30)

    if result.ok:
        # まだ scrub していない実験室。btrfs 自身が「記録が無い」と答える
        assert maintenance.parse_scrub_status(result.stdout).state == 'none'
    else:
        # 一度でも scrub した後。空の出力を none と言い切らないことが肝心
        assert 'Permission denied' in result.stderr
        assert maintenance.parse_scrub_status(result.stdout).state == 'unknown'


@lab
@writable
def test_scrub_status_is_readable_as_root():
    result = _run(maintenance.scrub_status_operation(MOUNT))
    assert result.ok, result.stderr
    assert maintenance.parse_scrub_status(result.stdout).state in maintenance.SCRUB_STATES


# --------------------------------------------------------------------------
# 変えてみる — 本物の btrfs に対して実際に実行する
# --------------------------------------------------------------------------

@lab
@writable
def test_scrub_runs_to_completion():
    """小さなファイルシステムなので、scrub は一瞬で終わる。"""
    assert _run(maintenance.scrub_start_operation(MOUNT)).ok
    # 状態の読み取りにも root が要る (記録ファイルが root 専用のため)
    result = _run(maintenance.scrub_status_operation(MOUNT))
    assert result.ok, result.stderr
    status = maintenance.parse_scrub_status(result.stdout)
    assert status.state in ('running', 'finished')
    assert status.counters.get('uncorrectable_errors') == 0


@lab
@writable
def test_balance_with_a_usage_filter_runs():
    """``-dusage``/``-musage`` の指定が、本物の btrfs に受け付けられる。"""
    result = _run(maintenance.balance_start_operation(MOUNT, usage=5))
    assert result.ok, result.stderr


@lab
@writable
def test_remove_then_add_a_device(lab_filesystem):
    """**取り外しと追加を本当に行う。**

    raid1 の 4 台から 1 台外し、戻す。外している間は冗長性が落ちるが、
    実験室なので構わない — それが実験室を用意した理由である。
    """
    victim = lab_filesystem.devices[-1].path

    removal = devices.remove_operation(victim, MOUNT)
    assert removal.risk == operations.DANGEROUS

    # 合言葉が合わなければ、本物相手でも実行されない
    with pytest.raises(PermissionError):
        _run(removal, confirmation='no')

    assert _run(removal, confirmation=removal.confirm_token).ok

    after = [f for f in devices.discover() if MOUNT in f.mount_points][0]
    assert victim not in {d.path for d in after.devices}
    assert len(after.devices) == len(lab_filesystem.devices) - 1

    # 戻す
    addition = devices.add_operation(victim, MOUNT, force=True)
    assert _run(addition, confirmation=addition.confirm_token).ok

    restored = [f for f in devices.discover() if MOUNT in f.mount_points][0]
    assert victim in {d.path for d in restored.devices}
    assert len(restored.devices) == len(lab_filesystem.devices)
