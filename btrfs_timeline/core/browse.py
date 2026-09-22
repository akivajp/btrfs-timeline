#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ライブのファイルシステム側のディレクトリ一覧。

履歴を見たいファイルに辿り着くための移動を担う。スナップショット側の走査は
``history`` が持っているので、ここには入れない。

``--root`` による閉じ込めもここで扱う。Web UI はファイルの内容まで返すため、
「このディレクトリより外は見せない」という制限を 1 箇所に集約しておかないと、
エンドポイントが増えるたびに漏れが生まれる。
"""

from __future__ import annotations

import datetime
import os
from typing import NamedTuple, Optional

from .. import i18n


class Entry(NamedTuple):
    """ディレクトリ内の 1 項目。"""

    name: str
    """表示名"""

    path: str
    """絶対パス"""

    is_directory: bool
    """ディレクトリか (シンボリックリンクなら、その先がディレクトリか)"""

    is_symlink: bool
    """シンボリックリンクそのものか"""

    size: Optional[int]
    """バイト数。読めなければ None"""

    mtime: Optional[datetime.datetime]
    """更新時刻 (UTC)。読めなければ None"""


def is_within(path: str, root: Optional[str]) -> bool:
    """``path`` が ``root`` の内側にあるかを判定する。

    ``root`` が None なら制限なしとみなして常に True。
    比較は ``realpath`` 同士で行う。シンボリックリンクを辿った先が外に出る
    ケースを許すと、閉じ込めの意味が無くなるため。
    """
    if not root:
        return True
    root = os.path.realpath(root)
    target = os.path.realpath(path)
    return target == root or target.startswith(root.rstrip('/') + '/')


def ensure_within(path: str, root: Optional[str]) -> None:
    """``root`` の外を指していれば ``PermissionError`` を送出する。"""
    if not is_within(path, root):
        raise PermissionError(i18n.translate('error.outside-root', path=path))


def _entry_info(entry) -> Entry:
    """``os.scandir`` の項目を ``Entry`` に変換する。

    ``lstat`` を使うのは履歴側と同じ理由による。リンク自体の情報を見せたいのであって、
    リンク先の情報を混ぜると一覧が実態とずれる。ただし「ディレクトリとして開けるか」
    だけは移動のために必要なので、そこだけはリンクを辿って判定する。
    """
    try:
        info = entry.stat(follow_symlinks=False)
        size = info.st_size
        mtime = datetime.datetime.fromtimestamp(info.st_mtime, datetime.timezone.utc)
    except OSError:
        size = None
        mtime = None
    try:
        is_directory = entry.is_dir()
    except OSError:
        # リンク切れなどで判定できない場合は、開けないものとして扱う
        is_directory = False
    try:
        is_symlink = entry.is_symlink()
    except OSError:
        is_symlink = False
    return Entry(
        name=entry.name, path=entry.path, is_directory=is_directory,
        is_symlink=is_symlink, size=size, mtime=mtime,
    )


def list_directory(path: str, show_hidden: bool = True,
                   root: Optional[str] = None) -> list:
    """ディレクトリの内容を返す。

    ディレクトリを先に、そのあと名前順で並べる (ファイルマネージャの慣習)。

    Args:
        path: 一覧するディレクトリ。
        show_hidden: ``.`` で始まる項目を含めるか。
        root: 閉じ込め先。この外を指していれば ``PermissionError``。

    Raises:
        PermissionError: ``root`` の外、または読み取り権限が無い。
        NotADirectoryError: ディレクトリではない。
        FileNotFoundError: 存在しない。
    """
    path = os.path.abspath(path)
    ensure_within(path, root)

    if not os.path.exists(path):
        raise FileNotFoundError(i18n.translate('error.path-not-found', path=path))
    if not os.path.isdir(path):
        raise NotADirectoryError(i18n.translate('error.not-a-directory', path=path))

    try:
        with os.scandir(path) as scanner:
            entries = [_entry_info(entry) for entry in scanner]
    except PermissionError:
        raise PermissionError(i18n.translate('error.permission-denied', path=path))

    if not show_hidden:
        entries = [e for e in entries if not e.name.startswith('.')]

    entries.sort(key=lambda e: (not e.is_directory, e.name.lower(), e.name))
    return entries


def parents(path: str, root: Optional[str] = None) -> list:
    """パンくず用に、祖先のパスを浅い順で返す (``path`` 自身を含む)。

    ``root`` が指定されていれば、そこより上は返さない。
    """
    path = os.path.abspath(path)
    boundary = os.path.realpath(root) if root else None

    chain = []
    current = path
    while True:
        chain.append(current)
        if boundary and os.path.realpath(current) == boundary:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    chain.reverse()
    return chain
