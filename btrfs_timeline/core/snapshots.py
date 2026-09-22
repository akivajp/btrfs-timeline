#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スナップショットのレイアウト検出。

btrfs には ZFS の ``.zfs/snapshot`` のような「スナップショットが必ずここに現れる」
という規約が無く、配置は運用ツールごとにばらばらである。そのため、
**どのレイアウトを使っているかを検出する層** を独立させてある。

対応レイアウト:

- snapper     … ``<mount>/.snapshots/<番号>/snapshot`` + ``info.xml``
- 汎用 (flat) … ``<mount>/.snapshots/<名前>`` が直接スナップショットの内容を持つ
                 (btrbk / Timeshift / 手動運用の多くがこの形)

いずれも root 権限を必要としない (ディレクトリを読むだけ)。
"""

from __future__ import annotations

import datetime
import os
import re
import xml.etree.ElementTree as ElementTree
from typing import NamedTuple, Optional

from .mounts import MountPoint

DEFAULT_SNAPSHOT_DIRNAME = '.snapshots'

# snapper の info.xml が持つ日時の書式。タイムゾーンは付かないが **UTC** である
# (ディレクトリの mtime と比較すると分かる。ローカル時刻と誤読すると時差分ずれる)
SNAPPER_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# btrbk 等が使う、名前の中に日時を埋め込む形式 (例: ``home.20260919T1901``)
RE_TIMESTAMP_IN_NAME = re.compile(r'(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?')


class Snapshot(NamedTuple):
    """1 つのスナップショット。"""

    id: str
    """識別子 (snapper なら番号、汎用ならディレクトリ名)"""

    root: str
    """スナップショットの内容のルートとなる実パス"""

    taken_at: Optional[datetime.datetime]
    """取得日時 (UTC, tz-aware)。判定できなければ None"""

    description: str
    """説明 (snapper の description。無ければ空文字)"""

    layout: str
    """検出したレイアウト名 (``snapper`` / ``flat``)"""


def _parse_snapper_info(info_path: str):
    """snapper の ``info.xml`` から取得日時と説明を読む。

    読めない・壊れている場合は ``(None, '')`` を返し、呼び出し側が
    ディレクトリの mtime 等で補えるようにする。
    """
    try:
        tree = ElementTree.parse(info_path)
    except (OSError, ElementTree.ParseError):
        return None, ''
    root = tree.getroot()
    taken_at = None
    date_node = root.find('date')
    if date_node is not None and date_node.text:
        try:
            naive = datetime.datetime.strptime(date_node.text.strip(), SNAPPER_DATE_FORMAT)
            # snapper は UTC で記録するので、ここで明示的に UTC を付ける
            taken_at = naive.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            taken_at = None
    desc_node = root.find('description')
    description = (desc_node.text or '').strip() if desc_node is not None else ''
    return taken_at, description


def _timestamp_from_name(name: str):
    """ディレクトリ名に埋め込まれた日時を取り出す (btrbk 形式等)。"""
    match = RE_TIMESTAMP_IN_NAME.search(name)
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        return datetime.datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second or 0),
            tzinfo=datetime.timezone.utc,
        )
    except ValueError:
        return None


def _mtime_utc(path: str):
    """ディレクトリの mtime を UTC の datetime として返す。"""
    try:
        return datetime.datetime.fromtimestamp(os.stat(path).st_mtime, datetime.timezone.utc)
    except OSError:
        return None


def discover(mount: MountPoint, snapshot_dirname: str = DEFAULT_SNAPSHOT_DIRNAME) -> list:
    """マウントポイント配下のスナップショット群を検出して返す。

    取得日時の昇順で返す (日時が不明なものは末尾)。
    """
    base = os.path.join(mount.mount_point, snapshot_dirname)
    if not os.path.isdir(base):
        return []

    snapshots = []
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return []

    for name in entries:
        entry_path = os.path.join(base, name)
        if not os.path.isdir(entry_path):
            continue

        # --- snapper レイアウト: <N>/snapshot が実体、<N>/info.xml がメタデータ
        nested = os.path.join(entry_path, 'snapshot')
        if os.path.isdir(nested):
            taken_at, description = _parse_snapper_info(os.path.join(entry_path, 'info.xml'))
            if taken_at is None:
                taken_at = _mtime_utc(entry_path)
            snapshots.append(Snapshot(
                id=name, root=nested, taken_at=taken_at,
                description=description, layout='snapper',
            ))
            continue

        # --- 汎用 (flat) レイアウト: ディレクトリそのものがスナップショットの内容
        taken_at = _timestamp_from_name(name) or _mtime_utc(entry_path)
        snapshots.append(Snapshot(
            id=name, root=entry_path, taken_at=taken_at,
            description='', layout='flat',
        ))

    # 日時不明 (None) を末尾に送りつつ昇順に並べる
    snapshots.sort(key=lambda s: (s.taken_at is None, s.taken_at or datetime.datetime.min.replace(
        tzinfo=datetime.timezone.utc)))
    return snapshots
