#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""メッセージの多言語化。

gettext ではなく **JSON のカタログ** を使っている。理由は 2 つある。

1. **ビルド手順を増やさない。** gettext は ``.po`` を ``.mo`` にコンパイルする工程が要る。
   このプロジェクトは「ビルド手順もフロントエンドのツールチェインも持たない」ことを
   前提にしているので、配布物にコンパイル済みバイナリを含める形は取りたくない。
2. **同じカタログをブラウザからも読みたい。** Cockpit モジュールとスタンドアロン
   Web UI はクライアントサイドで描画するため、翻訳は JavaScript 側でも必要になる。
   ``.mo`` はブラウザで扱えないが、JSON ならそのまま ``fetch`` できる。
   CLI と Web UI で訳文が二重管理になるのを防ぐには、形式を揃えるしかない。

翻訳を追加する人がすることは ``locales/<言語コード>.json`` を 1 つ置くことだけで、
コードには一切触れない。``en.json`` が参照カタログであり、
未翻訳のキーは自動的に英語にフォールバックする。

プレースホルダは ``{0}`` ではなく ``{path}`` のような **名前付き** にしてある。
言語によって語順が変わるため、位置指定では翻訳できない箇所が出るからである。
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from typing import Optional

LOCALE_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locales')

#: 参照カタログ兼フォールバック先。このカタログには全てのキーが揃っている。
FALLBACK_LANGUAGE = 'en'

#: 他のどのロケール設定よりも優先される、このツール専用の環境変数
ENVIRONMENT_VARIABLE = 'BTRFS_TIMELINE_LANG'

#: 標準的なロケール環境変数を、優先度の高い順に並べたもの
LOCALE_VARIABLES = ('LC_ALL', 'LC_MESSAGES', 'LANG')

# 読み込み済みカタログのキャッシュ (キー: 言語コード)
_catalogs = {}

# 現在選択されている言語。None なら未設定
_current_language = None

# 要求ごとの上書き。**スレッドごとに独立** させてあるのは、複数の要求を同時に
# 扱うサーバーで、片方の言語がもう片方の応答に混ざらないようにするため。
_override = threading.local()


def available_languages() -> list:
    """同梱されている言語コードの一覧を返す。"""
    try:
        entries = os.listdir(LOCALE_DIRECTORY)
    except OSError:
        return []
    return sorted(name[:-len('.json')] for name in entries if name.endswith('.json'))


def language_names() -> dict:
    """言語コードと、その言語自身による表示名の対応を返す。

    表示名をカタログ自身 (``language.name``) に持たせているのは、
    **翻訳者が自分の言語の呼び名を決められるようにするため**。
    外から与えると "Japanese" のように他言語での呼称になってしまい、
    言語切り替えの UI で自分の言語を見つけにくくなる。
    """
    names = {}
    for code in available_languages():
        names[code] = load_catalog(code).get('language.name') or code
    return names


def _candidates(value: str) -> list:
    """ロケール文字列から、探すべき言語コードの候補を優先度順に返す。

    ``ja_JP.UTF-8`` のような値は ``['ja_jp', 'ja']`` になる。
    地域別のカタログ (``pt_br.json`` 等) を置けるようにしつつ、
    無ければ言語だけのカタログに落ちるようにするための処理。
    """
    if not value:
        return []
    # 文字コード (.UTF-8) と修飾子 (@euro) を落とす
    cleaned = value.split('.')[0].split('@')[0].strip().replace('-', '_').lower()
    if not cleaned or cleaned in ('c', 'posix'):
        # C / POSIX ロケールは「翻訳しない」という意思表示なので英語にする
        return [FALLBACK_LANGUAGE]
    result = [cleaned]
    if '_' in cleaned:
        result.append(cleaned.split('_')[0])
    return result


def detect_language(explicit: Optional[str] = None, environ=None) -> str:
    """使用する言語コードを決める。

    優先度は ``explicit`` (``--lang``) → ``BTRFS_TIMELINE_LANG`` →
    ``LC_ALL`` / ``LC_MESSAGES`` / ``LANG`` → 英語。

    専用の環境変数を最優先に置いているのは、システム全体のロケールは変えたくないが
    このツールの出力だけ英語で見たい (ログに貼る、issue に貼る) という場面が
    現実に多いため。
    """
    environ = os.environ if environ is None else environ
    supported = available_languages()

    sources = [explicit, environ.get(ENVIRONMENT_VARIABLE)]
    sources.extend(environ.get(name) for name in LOCALE_VARIABLES)

    for source in sources:
        for candidate in _candidates(source or ''):
            if candidate in supported:
                return candidate
    return FALLBACK_LANGUAGE


def load_catalog(language: str) -> dict:
    """言語コードに対応するカタログを読む (結果はキャッシュする)。

    壊れている・存在しない場合は空の辞書を返す。翻訳の不備でツールが
    落ちるのは本末転倒なので、ここでは例外を投げない。
    """
    if language in _catalogs:
        return _catalogs[language]
    path = os.path.join(LOCALE_DIRECTORY, '{0}.json'.format(language))
    try:
        with open(path, encoding='utf-8') as handle:
            catalog = json.load(handle)
    except (OSError, ValueError):
        catalog = {}
    _catalogs[language] = catalog
    return catalog


def set_language(language: Optional[str]) -> str:
    """使用する言語を設定し、実際に選ばれた言語コードを返す。"""
    global _current_language
    _current_language = language or FALLBACK_LANGUAGE
    return _current_language


@contextlib.contextmanager
def use_language(language: Optional[str]):
    """このブロックの間だけ言語を差し替える。

    Web サーバーが「要求してきた人の言語」で応答するために使う。
    プロセス全体の設定は変えないので、同時に来た別の要求に影響しない。
    """
    previous = getattr(_override, 'language', None)
    _override.language = language or None
    try:
        yield
    finally:
        _override.language = previous


def current_language() -> str:
    """現在の言語コード。未設定なら環境から決めて設定する。"""
    override = getattr(_override, 'language', None)
    if override:
        return override
    if _current_language is None:
        set_language(detect_language())
    return _current_language


def translate(key: str, **params) -> str:
    """キーに対応するメッセージを、現在の言語で返す。

    訳文が無い場合は英語に、英語にも無い場合はキーそのものに落ちる。
    訳文のプレースホルダが壊れている (原文に無い名前を使っている等) 場合も
    英語に落とす。翻訳の不備が実行時エラーになってはいけない。
    """
    fallback = load_catalog(FALLBACK_LANGUAGE).get(key, key)
    template = load_catalog(current_language()).get(key, fallback)

    for candidate in (template, fallback):
        try:
            return candidate.format(**params) if params else candidate
        except (KeyError, IndexError, ValueError):
            continue
    return key
