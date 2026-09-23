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

    model: Optional[str] = None
    """ディスクの型番。読めなければ None"""

    temperature: Optional[float] = None
    """温度 (摂氏)。読めなければ None"""

    temperature_critical: Optional[float] = None
    """メーカーが申告する危険域 (摂氏)。読めなければ None。

    **閾値をこちらで決め打ちしない。** 何度から危ないかはデバイスが知っており、
    hwmon がそれを教えてくれる。勝手な数字で警告を出すと、外れたときに
    信用されなくなる。
    """

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
    """マウント先のパス"""

    mounts: List[mounts_module.MountPoint]
    """マウントの詳細。**どのサブボリュームがどこに出ているか** が分かる。

    btrfs では 1 つのファイルシステムが複数の場所に現れる。``/home`` の正体が
    ``subvol=/@home`` であることが見えないと、一覧を見ても関係が掴めない。
    """

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
    model, temperature, critical = _disk_info(name) if name else (None, None, None)
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
        model=model,
        temperature=temperature,
        temperature_critical=critical,
    )


def _whole_disk(name: str) -> Optional[str]:
    """パーティション名から、ディスク本体の sysfs パスを返す。

    ``nvme0n1p1`` → ``.../nvme0n1``、``sda1`` → ``.../sda``。
    もともとディスク全体なら、そのまま返す。温度や型番はディスク単位の情報で、
    パーティションの側には無い。
    """
    path = os.path.realpath(os.path.join(SYSFS_BLOCK, name))
    if not os.path.isdir(path):
        return None
    # パーティションには partition ファイルがある。あれば 1 つ上がディスク本体
    if os.path.exists(os.path.join(path, 'partition')):
        return os.path.dirname(path)
    return path


def _hwmon_value(directory: str, filename: str) -> Optional[float]:
    """hwmon の温度を摂氏で返す (sysfs はミリ度で持っている)。"""
    value = _read_int(os.path.join(directory, filename))
    return None if value is None else value / 1000.0


def _disk_info(name: str):
    """型番と温度を返す。読めない項目は None。

    **どちらも通常のパーミッションで読める。** NVMe なら
    ``/sys/class/nvme/<controller>/hwmon*/`` に、SATA でも ``drivetemp`` が
    読み込まれていれば ``device/hwmon*/`` に温度が出る。``smartctl`` は root を
    要するが、温度と型番のためだけにそれを求める必要は無い。
    """
    disk = _whole_disk(name)
    if not disk:
        return None, None, None

    device = os.path.join(disk, 'device')
    model = _read(os.path.join(device, 'model'))

    # hwmon はディスク本体の下か、その device (コントローラ) の下にある
    for base in (device, disk):
        try:
            entries = sorted(e for e in os.listdir(base) if e.startswith('hwmon'))
        except OSError:
            continue
        for entry in entries:
            directory = os.path.join(base, entry)
            current = _hwmon_value(directory, 'temp1_input')
            if current is not None:
                return (model or None, current,
                        _hwmon_value(directory, 'temp1_crit'))
    return model or None, None, None


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
        own_mounts = sorted(
            (mount for mount in mount_list
             if os.path.basename(mount.device) in names_in_sysfs),
            key=lambda m: m.mount_point)
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
            mounts=own_mounts,
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


# ---------------------------------------------------------------------------
# デバイスの操作 — ここだけ危険度が跳ね上がる
# ---------------------------------------------------------------------------
#
# 失敗するとプールごと失いうるのはこの 3 つだけで、それ以外の操作とは扱いを変える。
# 合言葉として **失われる側のデバイス名** を要求する。「はい」を押させるだけでは
# 「実行する気が無かった」しか防げず、**対象を取り違えたまま実行する** のを防げない。
# 後者のほうが、実際に起きるし、起きたときの被害が大きい。

def add_operation(device: str, path: str, force: bool = False) -> operations.Operation:
    """デバイスを 1 台加える。

    btrfs は、対象に既存のファイルシステムがあれば ``-f`` 無しでは拒否する。
    その守りがある限り打ち間違いは弾かれるので ``CAUTION`` に留める。
    ``force`` を付けるとその守りを外すことになるため、危険度が上がる。
    """
    argv = ['btrfs', 'device', 'add']
    if force:
        argv.append('-f')
    argv.extend([device, path])
    return operations.describe(
        argv,
        risk=operations.DANGEROUS if force else operations.CAUTION,
        needs_root=True,
        summary_key='operation.device-add-force' if force else 'operation.device-add',
        confirm_token=device if force else None,
        device=device, path=path)


def remove_operation(device: str, path: str) -> operations.Operation:
    """デバイスを 1 台外す。

    外す前に、そのデバイスが持っていたチャンクを他へ書き移す。空きが足りなければ
    途中で失敗し、その最中に別のデバイスが落ちればプールごと失う。
    **冗長性も下がる** (raid1 が 2 台なら single になる)。
    """
    return operations.describe(
        ['btrfs', 'device', 'remove', device, path],
        risk=operations.DANGEROUS, needs_root=True,
        summary_key='operation.device-remove',
        confirm_token=device,
        device=device, path=path)


def replace_operation(source: str, target: str, path: str,
                      force: bool = False) -> operations.Operation:
    """デバイスを置き換える。

    合言葉に使うのは **``target``** である。``source`` はこれから抜ける側で、
    失われるのは ``target`` に今あるもの — 置換は対象を丸ごと上書きする。
    打ち間違いで消えるのはそちらなので、確認させるのもそちらでなければならない。
    """
    argv = ['btrfs', 'replace', 'start']
    if force:
        argv.append('-f')
    # -B を付けず、既定どおり背景で走らせる。進捗は replace status で追える
    argv.extend([source, target, path])
    return operations.describe(
        argv, risk=operations.DANGEROUS, needs_root=True,
        summary_key='operation.device-replace',
        confirm_token=target,
        source=source, target=target, path=path)


def replace_status_operation(path: str) -> operations.Operation:
    """置換の進捗を見る。"""
    return operations.describe(
        ['btrfs', 'replace', 'status', path],
        risk=operations.SAFE, needs_root=True,
        summary_key='operation.device-replace-status', path=path)


def replace_cancel_operation(path: str) -> operations.Operation:
    """走っている置換を止める。元のデバイスはそのまま残る。"""
    return operations.describe(
        ['btrfs', 'replace', 'cancel', path],
        risk=operations.CAUTION, needs_root=True,
        summary_key='operation.device-replace-cancel', path=path)


def smart_operation(device: str) -> operations.Operation:
    """``smartctl`` でディスク自身の健康状態を見る。

    温度と型番は sysfs から非特権で読めるが、**残り寿命や代替セクタ数までは
    読めない**。そこは ``smartctl`` の領分で、こちらは root を要する。
    このツールは代わりに実行せず、実行すべきコマンドとして示すに留める。
    """
    return operations.describe(
        ['smartctl', '-H', '-A', device],
        risk=operations.SAFE, needs_root=True,
        summary_key='operation.smart', device=device)
