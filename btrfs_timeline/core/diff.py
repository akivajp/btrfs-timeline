#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""テキストの読み出しと版どうしの差分。

「どの版に戻すか」を決めるには、中身を見るだけでは足りないことが多い。
似た内容が並んでいるときに知りたいのは **何が変わったか** であり、
それを出せるかどうかが履歴ツールの実用性を分ける。

読み出しに上限を設けているのは、巨大なファイルを丸ごとメモリに載せないため。
差分の確認に必要なのは冒頭であり、数百 MB のファイルを読み切る意味は無い。
"""

from __future__ import annotations

import difflib
import os
from typing import NamedTuple, Optional

#: 1 つの版から読み出す上限。これを超える分は切り捨てて、その旨を伝える。
DEFAULT_LIMIT = 256 * 1024

#: 差分として返す行数の上限。全体が巨大に変わった場合にブラウザを詰まらせない。
DEFAULT_MAX_DIFF_LINES = 4000


class Content(NamedTuple):
    """読み出した版の中身。"""

    text: str
    """内容。バイナリなら空文字"""

    binary: bool
    """バイナリと判定されたか"""

    truncated: bool
    """上限で切り捨てたか"""

    missing: bool
    """その版にファイルが存在しなかったか"""


class Diff(NamedTuple):
    """2 つの版の差分。"""

    text: str
    """unified diff 形式の本文。差が無ければ空文字"""

    identical: bool
    """内容が同じだったか"""

    binary: bool
    """どちらかがバイナリで、差分を取れなかったか"""

    truncated: bool
    """読み出しか差分行数のどちらかで切り捨てたか"""


def is_binary(chunk: bytes) -> bool:
    """先頭を見てバイナリかどうかを判定する。

    NUL バイトが含まれていればテキストではない、という古典的な判定で十分。
    ここで厳密さを追ってもプレビューや差分の役には立たない。
    """
    return b'\x00' in chunk


def read_text(path: Optional[str], limit: int = DEFAULT_LIMIT) -> Content:
    """版の中身を読み出す。

    ``path`` が None (その時点でファイルが存在しなかった版) の場合は
    「空の内容」として返す。差分を取るときに、追加・削除を素直に表現できる。
    """
    if not path:
        return Content(text='', binary=False, truncated=False, missing=True)
    try:
        with open(path, 'rb') as handle:
            chunk = handle.read(limit + 1)
    except OSError:
        return Content(text='', binary=False, truncated=False, missing=True)

    truncated = len(chunk) > limit
    chunk = chunk[:limit]
    if is_binary(chunk):
        return Content(text='', binary=True, truncated=truncated, missing=False)
    return Content(text=chunk.decode('utf-8', errors='replace'),
                   binary=False, truncated=truncated, missing=False)


def unified(before: Optional[str], after: Optional[str],
            before_label: str = 'before', after_label: str = 'after',
            limit: int = DEFAULT_LIMIT,
            max_lines: int = DEFAULT_MAX_DIFF_LINES) -> Diff:
    """2 つの版の unified diff を作る。

    Args:
        before: 古い側のパス。存在しない版なら None。
        after: 新しい側のパス。存在しない版なら None。
        before_label: 差分ヘッダに出す古い側の名前。
        after_label: 差分ヘッダに出す新しい側の名前。
        limit: それぞれから読み出す上限バイト数。
        max_lines: 差分として返す最大行数。
    """
    left = read_text(before, limit)
    right = read_text(after, limit)

    if left.binary or right.binary:
        return Diff(text='', identical=False, binary=True,
                    truncated=left.truncated or right.truncated)

    if left.text == right.text:
        return Diff(text='', identical=True, binary=False,
                    truncated=left.truncated or right.truncated)

    lines = list(difflib.unified_diff(
        left.text.splitlines(keepends=True),
        right.text.splitlines(keepends=True),
        fromfile=before_label, tofile=after_label,
    ))
    truncated = left.truncated or right.truncated
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True

    text = ''.join(lines)
    # 末尾に改行が無い行があると表示が崩れるので、行末を揃えておく
    if text and not text.endswith('\n'):
        text += '\n'
    return Diff(text=text, identical=False, binary=False, truncated=truncated)


def label_for(path: Optional[str], fallback: str) -> str:
    """差分ヘッダ用のラベル。読みやすさのため、無ければ与えられた名前を使う。"""
    return os.path.basename(path) if path else fallback
