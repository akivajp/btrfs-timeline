#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""マウント情報の解析。

``/proc/self/mountinfo`` を読んで btrfs のマウント一覧を得る。

このモジュールが ``btrfs subvolume list`` を使わないのは意図的である。
``subvolume list`` は root 権限を要求する (非特権では ``Operation not permitted``)
のに対し、スナップショット配下のファイル自体は通常のパーミッションで読めるため、
マウント情報とディレクトリ走査だけで組み立てれば **非特権のままファイル履歴を辿れる**。
"""

from __future__ import annotations

import os
from typing import Iterable, NamedTuple, Optional

MOUNTINFO_PATH = '/proc/self/mountinfo'


class MountPoint(NamedTuple):
    """1 つのマウントを表す。"""

    mount_point: str
    """マウント先のパス (例: ``/home``)"""

    fs_type: str
    """ファイルシステム種別 (例: ``btrfs``)"""

    device: str
    """デバイス (例: ``/dev/nvme3n1p1``)"""

    subvol: Optional[str]
    """btrfs のサブボリューム名 (例: ``/@home``)。btrfs 以外では None"""

    root: str
    """マウント元のファイルシステム内パス (mountinfo の 4 番目のフィールド)"""


def _unescape(field: str) -> str:
    """mountinfo 内の 8 進エスケープ (``\\040`` 等) を復号する。

    空白・タブ・改行・バックスラッシュを含むパスは 8 進表記で埋め込まれている。
    """
    out = []
    i = 0
    while i < len(field):
        ch = field[i]
        if ch == '\\' and i + 3 < len(field) and field[i + 1:i + 4].isdigit():
            try:
                out.append(chr(int(field[i + 1:i + 4], 8)))
                i += 4
                continue
            except ValueError:
                pass
        out.append(ch)
        i += 1
    return ''.join(out)


def _parse_subvol(super_options: str) -> Optional[str]:
    """スーパーブロックオプション列から ``subvol=`` の値を取り出す。"""
    for opt in super_options.split(','):
        if opt.startswith('subvol='):
            return opt[len('subvol='):]
    return None


def parse_mountinfo(text: str) -> list:
    """mountinfo の内容を ``MountPoint`` のリストに変換する。

    書式は以下のとおりで、オプションフィールドの個数が可変なため
    セパレータ ``-`` の位置を見てから後半を読む必要がある::

        36 35 0:32 /@home /home rw,relatime shared:1 - btrfs /dev/sda1 rw,subvol=/@home
    """
    mounts = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10 or '-' not in fields:
            continue
        sep = fields.index('-')
        # セパレータの前: 必須フィールド 6 つ + 可変長のオプションフィールド
        # セパレータの後: fstype, source, super options
        if sep < 6 or len(fields) < sep + 4:
            continue
        mounts.append(MountPoint(
            mount_point=_unescape(fields[4]),
            fs_type=fields[sep + 1],
            device=_unescape(fields[sep + 2]),
            subvol=_parse_subvol(fields[sep + 3]),
            root=_unescape(fields[3]),
        ))
    return mounts


def read_mounts(path: str = MOUNTINFO_PATH) -> list:
    """システムの現在のマウント一覧を返す。"""
    with open(path, encoding='utf-8') as handle:
        return parse_mountinfo(handle.read())


def btrfs_mounts(mounts: Optional[Iterable] = None) -> list:
    """btrfs のマウントだけを返す。"""
    if mounts is None:
        mounts = read_mounts()
    return [m for m in mounts if m.fs_type == 'btrfs']


def find_containing_mount(target: str, mounts: Optional[Iterable] = None):
    """``target`` を含む btrfs マウントのうち、最も深いものを返す。

    ``/home/akiva/x`` が ``/`` と ``/home`` の両方に含まれる場合、
    実際にそのファイルを保持しているのは ``/home`` なので最長一致を選ぶ。
    該当が無ければ None。

    なお ``realpath`` でシンボリックリンクを解決してから判定している。
    dotfiles のようにリンクを張って運用している場合、利用者が見たいのは
    リンク自体ではなく **リンク先の実体の履歴** であることが多いため。
    ただしリンクの解決は「現在の」ファイルシステム上で行われる点に注意
    (過去のスナップショット内でリンク先が違っていた可能性までは追えない)。
    """
    target = os.path.realpath(target)
    best = None
    for mount in btrfs_mounts(mounts):
        mp = mount.mount_point
        if target == mp or target.startswith(mp.rstrip('/') + '/'):
            if best is None or len(mp) > len(best.mount_point):
                best = mount
    return best


def relative_to_mount(target: str, mount: MountPoint) -> str:
    """マウントポイントからの相対パスを返す (先頭に ``/`` は付けない)。"""
    target = os.path.realpath(target)
    rel = os.path.relpath(target, mount.mount_point)
    return '' if rel == '.' else rel
