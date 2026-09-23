#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部コマンドの実行と、その危険度の申告。

このツールはここから先、利用者のファイルシステムを **変更しうる** コマンドを
扱うようになる。そこで守る約束を 3 つ置く。

1. **何を実行するかを必ず示す。** 画面の裏で動くコマンドは隠さない。
   利用者が自分で確かめられるし、後から手作業で追いかけることもできる。
2. **危険度を申告する。** 「何も変えない」「変えるが戻せる」「失敗すると失う」は
   まったく別物で、同じ見た目のボタンに並べてはいけない。
3. **危険なものには念を押す。** ``DANGEROUS`` の操作は、呼び出し側が明示的に
   確認を取るまで実行できない。うっかり押せる場所に置かない。

この 3 つを個々の呼び出し箇所の良心に任せると、必ずどこかで漏れる。
``Operation`` を通さないと実行できない形にしてある。
"""

from __future__ import annotations

import subprocess
from typing import List, NamedTuple, Optional

from .. import i18n

#: 何も変更しない。読むだけ。
SAFE = 'safe'

#: 状態を変えるが、中断できる・やり直せる。scrub や balance の開始など。
CAUTION = 'caution'

#: 失敗するとデータやファイルシステムそのものを失いうる。
#: デバイスの取り外しや置換など。**必ず確認を取ってから実行する。**
DANGEROUS = 'dangerous'

RISKS = (SAFE, CAUTION, DANGEROUS)


class Operation(NamedTuple):
    """実行しようとしている 1 つのコマンド。

    実行前にそのまま画面に出せる形にしてある。「裏で何が起きるのか」を
    利用者が知らないまま進む状況を作らないための型である。
    """

    argv: List[str]
    """実際に実行するコマンド。シェルは通さない"""

    risk: str
    """``SAFE`` / ``CAUTION`` / ``DANGEROUS``"""

    needs_root: bool
    """root 権限を必要とするか"""

    summary_key: str
    """この操作が何をするかを説明する翻訳キー"""

    params: dict
    """``summary_key`` に埋めるパラメータ"""

    confirm_token: Optional[str] = None
    """実行を許すために、利用者が正確に入力しなければならない文字列。

    危険な操作では「はい」を押させるだけでは足りない。**失われる側のデバイス名**を
    打たせることで、対象を取り違えたまま進むことを防ぐ。GitHub がリポジトリの削除で
    名前を打たせるのと同じ考え方で、狙いは手間をかけさせることではなく、
    **何に対して実行しようとしているのかを本人に確認させる**ことにある。
    """

    def display(self) -> str:
        """画面や端末にそのまま出せるコマンド文字列。

        **これをシェルに渡して実行してはいけない。** 表示専用である
        (実行は ``argv`` をそのまま ``subprocess`` に渡す)。
        """
        return ' '.join(_quote(part) for part in self.argv)

    def summary(self) -> str:
        """何をする操作かの一文。"""
        return i18n.translate(self.summary_key, **self.params)

    def with_sudo(self) -> 'Operation':
        """``sudo`` 経由で実行する形にした写しを返す。

        argv と表示が同時に変わるので、**見せたものと実行するものが食い違う**
        状態が作れない。権限昇格は勝手に行わず、呼び出し側が明示したときだけ。
        """
        if self.argv and self.argv[0] == 'sudo':
            return self
        return self._replace(argv=['sudo'] + list(self.argv))

    def to_dict(self) -> dict:
        """JSON 化できる辞書にする (CLI の公開契約)。

        ``summary`` は端末向けに訳したもの。**画面はこれを使ってはいけない。**
        訳すのはこちらの言語であって、見る人が選んだ言語ではない。
        ブラウザは ``summary_key`` と ``params`` を受け取って自分で訳す
        (カタログを持っているのは、まさにそのためである)。
        """
        return {
            'argv': list(self.argv),
            'command': self.display(),
            'risk': self.risk,
            'needs_root': self.needs_root,
            'summary': self.summary(),
            'summary_key': self.summary_key,
            'params': dict(self.params),
            'confirm_token': self.confirm_token,
        }


class Result(NamedTuple):
    """実行結果。"""

    operation: Operation
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _quote(part: str) -> str:
    """表示用に、空白などを含む引数を括る。"""
    if part and all(ch.isalnum() or ch in '-_=/.:,@+' for ch in part):
        return part
    return "'{0}'".format(part.replace("'", "'\\''"))


def describe(argv, risk: str = SAFE, needs_root: bool = False,
             summary_key: str = 'operation.unknown',
             confirm_token: Optional[str] = None, **params) -> Operation:
    """実行せずに ``Operation`` を組み立てる。

    画面は「これから何を実行するか」をこの形で受け取り、確認を取ってから
    実行に進む。``--dry-run`` 相当の表示もこれで賄える。
    """
    if risk not in RISKS:
        raise ValueError('unknown risk level: {0}'.format(risk))
    if risk == DANGEROUS and confirm_token is None:
        # 危険な操作に合言葉が無いのは、たいてい書き忘れである。
        # 「はい」だけで通る危険な操作を作らせない
        raise ValueError('a dangerous operation needs a confirm_token')
    return Operation(argv=list(argv), risk=risk, needs_root=needs_root,
                     summary_key=summary_key, params=params,
                     confirm_token=confirm_token)


def run(operation: Operation, confirmed: bool = False,
        confirmation: Optional[str] = None,
        timeout: Optional[float] = None) -> Result:
    """コマンドを実行する。

    Args:
        operation: 実行する操作。
        confirmed: 合言葉を持たない ``DANGEROUS`` の操作を許すための単純な同意。
        confirmation: 利用者が入力した文字列。``confirm_token`` を持つ操作では
            **これが一致しなければ実行しない**。
        timeout: 秒。超えると ``subprocess.TimeoutExpired``。

    Raises:
        PermissionError: 危険な操作を、確認なしで実行しようとした。

    **既定で拒否するのは意図的である。** 確認を取り忘れた呼び出しが通ってしまうと、
    この仕組み全体が意味を失う。判断を個々の呼び出し箇所に任せない。
    """
    if operation.risk == DANGEROUS:
        if operation.confirm_token is not None:
            if confirmation != operation.confirm_token:
                raise PermissionError(i18n.translate(
                    'error.token-required', token=operation.confirm_token,
                    command=operation.display()))
        elif not confirmed:
            raise PermissionError(i18n.translate(
                'error.confirmation-required', command=operation.display()))

    completed = subprocess.run(
        operation.argv, capture_output=True, text=True, timeout=timeout)
    return Result(operation=operation, returncode=completed.returncode,
                  stdout=completed.stdout, stderr=completed.stderr)
