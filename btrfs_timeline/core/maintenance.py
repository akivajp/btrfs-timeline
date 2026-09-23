#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scrub と balance — 状態を変える最初の操作。

ここから先は読むだけでは済まない。守る約束は ``operations`` に書いたとおりで、
このモジュールの仕事は **何を実行するかを実行せずに組み立てること** と、
``--format json`` が使えない出力を解釈することの 2 つである。

危険度について:

- scrub は ``CAUTION``。読み取りと、壊れたブロックの別コピーからの修復を行う。
  いつでも中断でき、再開もできる。**データを失う操作ではない。**
- balance も ``CAUTION``。チャンクを書き直すだけで、中断・一時停止ができる。
  ただし全体 balance は全データを読み書きするので、時間も I/O も相応にかかる。

どちらも root を要する。閲覧側と違い、ここはカーネルが ``CAP_SYS_ADMIN`` を
要求するため、非特権では回避のしようがない。``balance status`` までが root 必須で、
``scrub status`` だけは ``/var/lib/btrfs/`` の記録を読むので非特権で見られる。
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

from . import operations

#: ``scrub status`` が報告する状態
SCRUB_STATES = ('running', 'finished', 'aborted', 'interrupted', 'none')

#: ``Bytes scrubbed: 13.76GiB  (48.59%)`` から割合を取る
RE_PERCENT = re.compile(r'\(([\d.]+)%\)')

#: ``0 out of about 12 chunks balanced (1 considered), 100% left``
RE_BALANCE = re.compile(
    r'(\d+)\s+out of about\s+(\d+)\s+chunks balanced\s+\((\d+)\s+considered\),\s*(\d+)%\s*left')


class ScrubStatus(NamedTuple):
    """``btrfs scrub status`` の結果。"""

    state: str
    """``running`` / ``finished`` / ``aborted`` / ``interrupted`` / ``none``"""

    total_bytes: Optional[int]
    scrubbed_bytes: Optional[int]
    percent: Optional[float]

    duration: str
    time_left: str
    eta: str

    error_summary: str
    """``no errors found`` や ``csum=72`` など、btrfs の言葉そのまま"""

    counters: dict
    """``-R`` で出る生のカウンタ。``corrected_errors`` などの内訳"""

    raw: str
    """コマンドの出力そのもの。

    解釈に自信が持てない項目があっても、利用者は原文を確認できる。
    ``--format json`` が使えない以上、これは残しておくべきものである。
    """

    @property
    def running(self) -> bool:
        return self.state == 'running'

    @property
    def has_errors(self) -> bool:
        """修復できなかった誤りがあるか。

        ``corrected_errors`` は「見つけて直した」ので、警戒はしても
        危険とまでは言わない。``uncorrectable_errors`` は別で、
        **別コピーから直せなかった = 実際に失われた** ことを意味する。
        """
        return bool(self.counters.get('uncorrectable_errors'))


class BalanceStatus(NamedTuple):
    """``btrfs balance status`` の結果。"""

    state: str
    """``running`` / ``paused`` / ``none`` / ``unknown``"""

    balanced: Optional[int]
    total: Optional[int]
    considered: Optional[int]
    percent_left: Optional[int]

    raw: str

    @property
    def running(self) -> bool:
        return self.state == 'running'


# ---------------------------------------------------------------------------
# 実行せずに組み立てる
# ---------------------------------------------------------------------------

def scrub_status_operation(path: str) -> operations.Operation:
    """scrub の状態を見る。

    ``/var/lib/btrfs/`` の記録を読むだけなので、これだけは非特権でも通る。
    """
    return operations.describe(
        ['btrfs', 'scrub', 'status', '-R', '--raw', path],
        risk=operations.SAFE, needs_root=False,
        summary_key='operation.scrub-status', path=path)


def scrub_start_operation(path: str, readonly: bool = False) -> operations.Operation:
    """scrub を開始する。

    既定ではバックグラウンドで動く (btrfs 側の既定)。状態は
    ``scrub_status_operation`` で追える。

    Args:
        readonly: 壊れたブロックを見つけても修復しない。確認だけしたいとき。
    """
    argv = ['btrfs', 'scrub', 'start']
    if readonly:
        argv.append('-r')
    argv.append(path)
    return operations.describe(
        argv, risk=operations.CAUTION, needs_root=True,
        summary_key='operation.scrub-start-readonly' if readonly
        else 'operation.scrub-start',
        path=path)


def scrub_cancel_operation(path: str) -> operations.Operation:
    """走っている scrub を止める。進捗は記録され、あとで再開できる。"""
    return operations.describe(
        ['btrfs', 'scrub', 'cancel', path],
        risk=operations.CAUTION, needs_root=True,
        summary_key='operation.scrub-cancel', path=path)


def scrub_resume_operation(path: str) -> operations.Operation:
    """止めた scrub を続きから再開する。"""
    return operations.describe(
        ['btrfs', 'scrub', 'resume', path],
        risk=operations.CAUTION, needs_root=True,
        summary_key='operation.scrub-resume', path=path)


def balance_status_operation(path: str) -> operations.Operation:
    """balance の状態を見る。**これも root を要する** (scrub と違う点)。"""
    return operations.describe(
        ['btrfs', 'balance', 'status', path],
        risk=operations.SAFE, needs_root=True,
        summary_key='operation.balance-status', path=path)


def balance_start_operation(path: str, usage: Optional[int] = None) -> operations.Operation:
    """balance を開始する。

    Args:
        usage: 指定すると ``-dusage=N -musage=N`` を付け、**使用率が N%% 未満の
            チャンクだけ** を書き直す。空きが断片化しているときの定石で、
            全体 balance より桁違いに軽い。省略すると全チャンクが対象になる。
    """
    argv = ['btrfs', 'balance', 'start']
    if usage is not None:
        argv.extend(['-dusage={0}'.format(usage), '-musage={0}'.format(usage)])
    argv.append(path)
    return operations.describe(
        argv, risk=operations.CAUTION, needs_root=True,
        summary_key='operation.balance-start-usage' if usage is not None
        else 'operation.balance-start',
        path=path, usage=usage)


def balance_cancel_operation(path: str) -> operations.Operation:
    """走っている balance を止める。既に動かしたチャンクはそのまま残る。"""
    return operations.describe(
        ['btrfs', 'balance', 'cancel', path],
        risk=operations.CAUTION, needs_root=True,
        summary_key='operation.balance-cancel', path=path)


def balance_pause_operation(path: str) -> operations.Operation:
    """balance を一時停止する。``resume`` で続きから再開できる。"""
    return operations.describe(
        ['btrfs', 'balance', 'pause', path],
        risk=operations.CAUTION, needs_root=True,
        summary_key='operation.balance-pause', path=path)


def balance_resume_operation(path: str) -> operations.Operation:
    """一時停止した balance を再開する。"""
    return operations.describe(
        ['btrfs', 'balance', 'resume', path],
        risk=operations.CAUTION, needs_root=True,
        summary_key='operation.balance-resume', path=path)


# ---------------------------------------------------------------------------
# 出力の解釈 (--format json が使えないため)
# ---------------------------------------------------------------------------

def _as_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_scrub_status(text: str) -> ScrubStatus:
    """``btrfs scrub status -R --raw`` の出力を解釈する。

    書式は「``名前:`` 値」が並ぶだけで、``-R`` を付けると生のカウンタが
    タブ字下げで続く。**一度も scrub していないと ``no stats available`` の
    1 行だけになる** ので、そこを状態 ``none`` として扱う。
    """
    header = {}
    counters = {}

    for line in (text or '').splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == 'no stats available':
            header.setdefault('Status', 'none')
            continue
        if ':' not in stripped:
            continue
        name, _, value = stripped.partition(':')
        name = name.strip()
        value = value.strip()
        # 生のカウンタは小文字とアンダースコアのみ。見出しは大文字を含む
        if re.fullmatch(r'[a-z_]+', name):
            counters[name] = _as_int(value)
        else:
            header[name] = value

    state = (header.get('Status') or 'none').lower()
    if state not in SCRUB_STATES:
        state = 'unknown'

    scrubbed = header.get('Bytes scrubbed', '')
    percent_match = RE_PERCENT.search(scrubbed)

    return ScrubStatus(
        state=state,
        total_bytes=_as_int(header.get('Total to scrub', '').split()[0])
        if header.get('Total to scrub') else None,
        scrubbed_bytes=_as_int(scrubbed.split()[0]) if scrubbed else None,
        percent=float(percent_match.group(1)) if percent_match else None,
        duration=header.get('Duration', ''),
        time_left=header.get('Time left', ''),
        eta=header.get('ETA', ''),
        error_summary=header.get('Error summary', ''),
        counters=counters,
        raw=text or '',
    )


def parse_balance_status(text: str) -> BalanceStatus:
    """``btrfs balance status`` の出力を解釈する。

    走っていなければ ``No balance found on '/'`` の 1 行で終わる。
    """
    text = text or ''
    lowered = text.lower()
    if 'no balance found' in lowered:
        state = 'none'
    elif 'is paused' in lowered:
        state = 'paused'
    elif 'is running' in lowered:
        state = 'running'
    else:
        state = 'unknown'

    match = RE_BALANCE.search(text)
    if match:
        balanced, total, considered, left = (int(g) for g in match.groups())
    else:
        balanced = total = considered = left = None

    return BalanceStatus(state=state, balanced=balanced, total=total,
                         considered=considered, percent_left=left, raw=text)
