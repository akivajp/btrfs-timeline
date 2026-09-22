#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ファイル履歴の組み立て。

指定されたパスについて、各スナップショット内の対応するファイルを調べ、
「いつからいつまで、その内容だったか」という **版 (version) の一覧** を作る。

重複排除が必須である理由: btrfs は CoW なので、変更していないファイルでも
スナップショットの数だけ同じ内容が並ぶ。実測では 35 個のスナップショットに対して
ユニークな版が 1 つ、というのが普通に起きる。素直に列挙すると UI が使い物にならない。
"""

from __future__ import annotations

import datetime
import os
from typing import NamedTuple, Optional

from . import mounts as mounts_module
from . import snapshots as snapshots_module


class Version(NamedTuple):
    """ある期間そのファイルが取っていた内容 (1 つの版)。"""

    path: Optional[str]
    """その版を読み出せる実パス。ライブ版なら元のパス。欠損 (未作成/削除後) なら None"""

    size: Optional[int]
    """バイト数。欠損なら None"""

    mtime: Optional[datetime.datetime]
    """ファイルの更新時刻 (UTC)。欠損なら None"""

    exists: bool
    """その時点でファイルが存在したか"""

    is_live: bool
    """現在のライブファイルか"""

    first_snapshot_id: Optional[str]
    """この版を最初に含むスナップショットの ID"""

    last_snapshot_id: Optional[str]
    """この版を最後に含むスナップショットの ID"""

    first_seen: Optional[datetime.datetime]
    """この版が最初に観測されたスナップショットの取得日時 (UTC)"""

    last_seen: Optional[datetime.datetime]
    """この版が最後に観測されたスナップショットの取得日時 (UTC)"""

    snapshot_count: int
    """この版を含むスナップショットの個数"""


class _Probe(NamedTuple):
    """1 つのスナップショット内での観測結果 (内部用)。"""

    snapshot: snapshots_module.Snapshot
    path: Optional[str]
    size: Optional[int]
    mtime: Optional[datetime.datetime]
    exists: bool


def _stat_utc(path: str):
    """``(size, mtime)`` を返す。読めなければ ``(None, None)``。

    **必ず ``os.lstat`` を使うこと。** スナップショット内のシンボリックリンクが
    絶対パス (例: ``/home/akiva/x``) を指している場合、``os.stat`` はリンクを追って
    **ライブのファイル** を読んでしまう。その結果、過去のスナップショットの行に
    現在の内容が表示され、利用者は「この時点では既にこの内容だった」と誤読する。
    履歴ツールとしては最悪の部類の誤りなので、スナップショット境界を越えない
    ``os.lstat`` で統一する。
    """
    try:
        info = os.lstat(path)
    except OSError:
        return None, None
    return info.st_size, datetime.datetime.fromtimestamp(info.st_mtime, datetime.timezone.utc)


def _identity(probe: _Probe):
    """版の同一性を判定するためのキー。

    httm と同じく mtime + size を使う。内容のハッシュは取らない
    (スナップショット数 × ファイルサイズ分の読み込みが発生してしまうため)。
    """
    if not probe.exists:
        return None
    return (probe.size, probe.mtime)


def probe_snapshots(target: str, snapshot_list=None, mount=None):
    """各スナップショット内での ``target`` の状態を、古い順に観測して返す。"""
    mount = mount or mounts_module.find_containing_mount(target)
    if mount is None:
        raise LookupError('btrfs のマウントが見つかりません: {0}'.format(target))

    if snapshot_list is None:
        snapshot_list = snapshots_module.discover(mount)

    relative = mounts_module.relative_to_mount(target, mount)
    probes = []
    for snapshot in snapshot_list:
        candidate = os.path.join(snapshot.root, relative) if relative else snapshot.root
        size, mtime = _stat_utc(candidate)
        exists = mtime is not None
        probes.append(_Probe(
            snapshot=snapshot,
            path=candidate if exists else None,
            size=size, mtime=mtime, exists=exists,
        ))
    return probes


def list_versions(target: str, include_live: bool = True, snapshot_list=None, mount=None) -> list:
    """``target`` の版の一覧を、古い順に返す。

    連続する同一内容 (mtime + size が同じ) は 1 つの版に畳み、
    その版がいつからいつまで観測されたかを保持する。
    「存在しなかった期間」も 1 つの版 (``exists=False``) として残す。
    これが無いと、削除されて復活したファイルの履歴が繋がって見えてしまう。
    """
    probes = probe_snapshots(target, snapshot_list=snapshot_list, mount=mount)

    versions = []
    for probe in probes:
        key = _identity(probe)
        if versions and versions[-1][0] == key:
            # 直前と同じ内容なので、区間を伸ばすだけ
            versions[-1][1].append(probe)
            continue
        versions.append((key, [probe]))

    result = []
    for _key, group in versions:
        head = group[0]
        result.append(Version(
            path=head.path,
            size=head.size,
            mtime=head.mtime,
            exists=head.exists,
            is_live=False,
            first_snapshot_id=head.snapshot.id,
            last_snapshot_id=group[-1].snapshot.id,
            first_seen=head.snapshot.taken_at,
            last_seen=group[-1].snapshot.taken_at,
            snapshot_count=len(group),
        ))

    if include_live:
        # スナップショット側は realpath で解決した実体を辿っているので、
        # ライブ版も同じ実体を見なければ「最新版と現在」を比較できなくなる
        # (リンク自体を lstat すると、リンク先パスの文字列長がサイズとして出てしまう)
        resolved = os.path.realpath(target)
        size, mtime = _stat_utc(resolved)
        exists = mtime is not None
        # ライブ版が最新の版と同一内容でも、行としては必ず残す
        # (「今のファイルはこの版と同じ」と示せる方が UI として分かりやすい)
        result.append(Version(
            path=resolved if exists else None,
            size=size, mtime=mtime, exists=exists, is_live=True,
            first_snapshot_id=None, last_snapshot_id=None,
            first_seen=None, last_seen=None,
            snapshot_count=0,
        ))

    return result
