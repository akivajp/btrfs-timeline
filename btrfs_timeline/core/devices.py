#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ファイルシステムとデバイスの状態。

**``btrfs filesystem show`` は使わない。** あれは生のブロックデバイスを開くため
root を要求する (非特権では ``cannot open /dev/...: Permission denied``)。
代わりに 2 つの情報源を使う。どちらも通常のパーミッションで読める。

- ``/sys/fs/btrfs/<UUID>/`` … ラベル、RAID プロファイル、割り当て量、
  実行中の排他操作、デバイス毎の欠損/書込可否
- ``btrfs device stats --format json`` … **devid とデバイス名の対応**、
  およびエラーカウンタ

devid の対応をコマンドから取るのには理由がある。``devinfo/<devid>/`` には
デバイス名が無く、``devices/`` は名前順に並ぶだけで devid の順ではない。
実機では devid=1 が ``nvme3n1p1`` で、名前順に対応させると **全デバイスの情報が
入れ替わる**。エラーカウンタの持ち主を取り違えるのは、このダッシュボードで
最も避けたい種類の誤りである。

読めない値は None にして、表示側が「取れなかった」と示せるようにする。
権限やカーネルのバージョンで欠ける項目があっても、画面全体が落ちてはいけない。
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import List, NamedTuple, Optional

from . import mounts as mounts_module
from . import operations

SYSFS_ROOT = '/sys/fs/btrfs'
SYSFS_BLOCK = '/sys/class/block'

#: 割り当ての種類。btrfs はこの 3 つを別々のプロファイルで持てる
ALLOCATION_KINDS = ('data', 'metadata', 'system')

#: ``btrfs device stats`` が報告するカウンタの名前。
#: sysfs 側は ``write_errs`` のように少し違う名前を使うが、利用者が
#: ``btrfs device stats`` で目にするのはこちらなので、こちらに揃える。
ERROR_COUNTERS = ('write_io_errs', 'read_io_errs', 'flush_io_errs',
                  'corruption_errs', 'generation_errs')


class Device(NamedTuple):
    """ファイルシステムを構成する 1 台。"""

    devid: int
    """btrfs 内での番号"""

    name: str
    """ブロックデバイス名 (例: ``nvme0n1p1``)"""

    path: str
    """``/dev/`` 以下のパス"""

    size: Optional[int]
    """デバイスの容量 (バイト)。読めなければ None"""

    missing: bool
    """欠損している (見つかっていない) か"""

    writeable: bool
    """書き込み可能か"""

    replace_target: bool
    """``btrfs replace`` の受け側として参加しているか"""

    errors: dict
    """エラーカウンタ。``{'read_errs': 0, ...}``"""

    @property
    def has_errors(self) -> bool:
        """1 つでもエラーが記録されているか。

        **0 かどうかだけが重要である。** 値が増えていること自体が、
        ディスクかケーブルか電源を疑う理由になる。
        """
        return any(value for value in self.errors.values())


class Allocation(NamedTuple):
    """data / metadata / system それぞれの割り当て状況。"""

    kind: str
    """``data`` / ``metadata`` / ``system``"""

    profile: Optional[str]
    """RAID プロファイル (``single`` / ``raid1`` / ``raid0`` など)"""

    total_bytes: Optional[int]
    """確保済みの量"""

    used_bytes: Optional[int]
    """そのうち使っている量"""


class Filesystem(NamedTuple):
    """1 つの btrfs ファイルシステム。"""

    uuid: str
    label: str
    mount_points: List[str]
    devices: List[Device]
    allocations: List[Allocation]

    stats_command: Optional[operations.Operation]
    """デバイス情報を取るために実行したコマンド。

    **裏で何が動いたかを隠さない** ための項目で、そのまま画面に出せる。
    """

    exclusive_operation: Optional[str]
    """実行中の排他操作 (``none`` / ``balance`` / ``device add`` など)。

    btrfs は balance・replace・デバイス操作を同時に走らせられない。
    ここが ``none`` でなければ、何かが進行中である。
    """

    @property
    def degraded(self) -> bool:
        """欠損デバイスを抱えているか。"""
        return any(device.missing for device in self.devices)


def _read(path: str) -> Optional[str]:
    """sysfs の値を 1 つ読む。読めなければ None。"""
    try:
        with open(path, encoding='utf-8') as handle:
            return handle.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    value = _read(path)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _profile(directory: str) -> Optional[str]:
    """割り当てディレクトリの下にある、プロファイル名のサブディレクトリを探す。

    ``allocation/data/raid0/`` のように、プロファイル名がそのままディレクトリ名に
    なっている。フラグの数値から逆算するより、これを読む方が確実で、
    新しいプロファイルが増えても勝手に追随する。
    """
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return None
    for name in entries:
        if os.path.isdir(os.path.join(directory, name)) and name not in ('size_classes',):
            return name
    return None


def stats_operation(mount_point: str) -> operations.Operation:
    """``btrfs device stats`` の呼び出しを、実行せずに記述する。

    画面はこれをそのまま「裏で実行するコマンド」として表示できる。
    """
    return operations.describe(
        ['btrfs', '--format', 'json', 'device', 'stats', mount_point],
        risk=operations.SAFE, needs_root=False,
        summary_key='operation.device-stats', path=mount_point)


def _device_stats(mount_point: str):
    """devid とデバイス名・エラーカウンタの対応を返す。

    取れなければ空の辞書。``btrfs`` が入っていない環境でも、
    ダッシュボードの残りは表示できるようにする。
    """
    if not mount_point:
        return {}, None
    operation = stats_operation(mount_point)
    try:
        result = operations.run(operation, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {}, operation
    if not result.ok:
        return {}, operation
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return {}, operation

    by_devid = {}
    for entry in payload.get('device-stats') or []:
        try:
            devid = int(entry['devid'])
        except (KeyError, TypeError, ValueError):
            continue
        by_devid[devid] = {
            'path': entry.get('device') or '',
            'errors': {name: entry.get(name) for name in ERROR_COUNTERS},
        }
    return by_devid, operation


def _device(base: str, devid: int, stats: dict) -> Device:
    devinfo = os.path.join(base, 'devinfo', str(devid))
    path = stats.get('path') or ''
    name = os.path.basename(path)
    return Device(
        devid=devid,
        name=name,
        path=path,
        size=_device_size(name) if name else None,
        missing=_read_int(os.path.join(devinfo, 'missing')) == 1,
        # 読めない場合は「書ける」と仮定しない。分からないことを困難側に倒す
        writeable=_read_int(os.path.join(devinfo, 'writeable')) == 1,
        replace_target=_read_int(os.path.join(devinfo, 'replace_target')) == 1,
        errors=stats.get('errors') or {name: None for name in ERROR_COUNTERS},
    )


def _device_size(name: str) -> Optional[int]:
    """ブロックデバイスの容量 (バイト)。

    ``/sys/class/block/<name>/size`` は **512 バイト単位のセクタ数** である。
    論理セクタサイズに関わらず 512 固定なので、そのまま掛ければよい。
    """
    sectors = _read_int(os.path.join(SYSFS_BLOCK, name, 'size'))
    return None if sectors is None else sectors * 512


def _devids(base: str) -> List[int]:
    """sysfs に現れる devid を昇順で返す。"""
    try:
        entries = os.listdir(os.path.join(base, 'devinfo'))
    except OSError:
        return []
    found = []
    for entry in entries:
        try:
            found.append(int(entry))
        except ValueError:
            continue
    return sorted(found)


def discover(mount_list=None) -> List[Filesystem]:
    """このシステムの btrfs ファイルシステムを列挙する。

    マウントされていないものは ``/sys/fs/btrfs`` に現れないため対象外になる。
    それでよい — マウントされていないファイルシステムの履歴は辿れない。
    """
    if mount_list is None:
        mount_list = mounts_module.btrfs_mounts()

    try:
        uuids = sorted(name for name in os.listdir(SYSFS_ROOT)
                       if os.path.isdir(os.path.join(SYSFS_ROOT, name, 'devices')))
    except OSError:
        return []

    found = []
    for uuid in uuids:
        base = os.path.join(SYSFS_ROOT, uuid)

        # devid とデバイス名の対応を取るために、マウント先が 1 つ要る
        names_in_sysfs = _device_names(base)
        mount_points = sorted(
            mount.mount_point for mount in mount_list
            if os.path.basename(mount.device) in names_in_sysfs)
        stats, operation = _device_stats(mount_points[0] if mount_points else '')

        devices = [_device(base, devid, stats.get(devid, {}))
                   for devid in _devids(base)]

        allocations = []
        for kind in ALLOCATION_KINDS:
            directory = os.path.join(base, 'allocation', kind)
            if not os.path.isdir(directory):
                continue
            allocations.append(Allocation(
                kind=kind,
                profile=_profile(directory),
                total_bytes=_read_int(os.path.join(directory, 'total_bytes')),
                used_bytes=_read_int(os.path.join(directory, 'bytes_used')),
            ))

        found.append(Filesystem(
            uuid=uuid,
            label=_read(os.path.join(base, 'label')) or '',
            mount_points=mount_points,
            devices=devices,
            allocations=allocations,
            exclusive_operation=_read(os.path.join(base, 'exclusive_operation')),
            stats_command=operation,
        ))
    return found


def _device_names(base: str) -> set:
    """``devices/`` に並ぶブロックデバイス名。マウントとの突き合わせに使う。"""
    try:
        return set(os.listdir(os.path.join(base, 'devices')))
    except OSError:
        return set()
