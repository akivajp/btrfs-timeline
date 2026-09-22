#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多言語化のテスト。

このファイルの主役は「翻訳を追加するコントリビューターのための安全網」である。
訳文の抜け・プレースホルダの食い違い・コード側で使っているのにカタログに無いキーは、
どれもレビューで見落としやすく、実行するまで気付けない。ここで機械的に止める。
"""

import ast
import json
import os
import re
import string

import pytest

from btrfs_timeline import cli, i18n

PACKAGE_DIRECTORY = os.path.dirname(os.path.abspath(cli.__file__))


@pytest.fixture(autouse=True)
def _isolate_language():
    """言語はモジュール全体で共有される状態なので、テストごとに元に戻す。"""
    previous = i18n._current_language
    yield
    i18n._current_language = previous


def _placeholders(template):
    """書式文字列に含まれるプレースホルダ名の集合を返す。"""
    return {name for _text, name, _spec, _conv in string.Formatter().parse(template)
            if name is not None}


def _load(language):
    path = os.path.join(i18n.LOCALE_DIRECTORY, '{0}.json'.format(language))
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def _translated_keys():
    """パッケージ内の ``_(...)`` / ``i18n.translate(...)`` が使っているキーを集める。

    三項演算子でキーを切り替えている箇所 (``_('a' if cond else 'b')``) も拾えるよう、
    呼び出しの引数全体を再帰的に走査する。
    """
    keys = set()
    for directory, _dirs, files in os.walk(PACKAGE_DIRECTORY):
        for name in files:
            if not name.endswith('.py'):
                continue
            path = os.path.join(directory, name)
            with open(path, encoding='utf-8') as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_translate = (
                    (isinstance(func, ast.Name) and func.id == '_')
                    or (isinstance(func, ast.Attribute) and func.attr == 'translate')
                )
                if not is_translate:
                    continue
                for argument in node.args:
                    for inner in ast.walk(argument):
                        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                            keys.add(inner.value)
    return keys


# --------------------------------------------------------------------------
# カタログの整合性 (翻訳追加時の安全網)
# --------------------------------------------------------------------------

def test_english_catalog_is_the_reference():
    """英語カタログは必ず存在し、空でない。ここが全てのフォールバック先になる。"""
    assert i18n.FALLBACK_LANGUAGE in i18n.available_languages()
    assert _load(i18n.FALLBACK_LANGUAGE)


@pytest.mark.parametrize('language', i18n.available_languages())
def test_catalogs_have_the_same_keys(language):
    """全カタログのキー集合が英語と一致する (訳し漏れ・余分なキーを検出)。"""
    reference = set(_load(i18n.FALLBACK_LANGUAGE))
    actual = set(_load(language))
    assert actual - reference == set(), '英語に無いキーがあります'
    assert reference - actual == set(), '訳されていないキーがあります'


@pytest.mark.parametrize('language', i18n.available_languages())
def test_catalogs_use_the_same_placeholders(language):
    """プレースホルダ名が英語と一致する。

    語順は言語によって変わってよいが、名前が違うと実行時に埋まらない。
    プレースホルダを名前付きにしてあるのは、まさに語順を自由にするためである。
    """
    reference = _load(i18n.FALLBACK_LANGUAGE)
    catalog = _load(language)
    for key, template in reference.items():
        assert _placeholders(catalog[key]) == _placeholders(template), key


def test_every_key_used_in_code_exists_in_the_catalog():
    """Python コードが引いているキーが、英語カタログに揃っている。"""
    reference = set(_load(i18n.FALLBACK_LANGUAGE))
    missing = sorted(_translated_keys() - reference)
    assert missing == []


def test_every_key_used_in_the_web_ui_exists_in_the_catalog():
    """ブラウザ側が引いているキーも同じカタログから来る。

    Web UI は Python を通さず ``/api/config`` が返すカタログを直接引くため、
    Python 側だけを検査しても漏れる。同じ安全網を JavaScript にも掛ける。
    """
    reference = set(_load(i18n.FALLBACK_LANGUAGE))
    pattern = re.compile(r"""\bt\(\s*['"]([a-z0-9][a-z0-9.\-]*)['"]""")
    used = set()
    static = os.path.join(PACKAGE_DIRECTORY, 'web', 'static')
    for name in sorted(os.listdir(static)):
        if not name.endswith('.js'):
            continue
        with open(os.path.join(static, name), encoding='utf-8') as handle:
            used.update(pattern.findall(handle.read()))
    assert used, 'JavaScript 側のキーが 1 つも見つかりませんでした (検出漏れの疑い)'
    assert sorted(used - reference) == []


# --------------------------------------------------------------------------
# 言語の決定
# --------------------------------------------------------------------------

def test_explicit_language_wins():
    assert i18n.detect_language('ja', environ={'LANG': 'en_US.UTF-8'}) == 'ja'


def test_dedicated_variable_beats_the_system_locale():
    """システム全体のロケールは変えずに、このツールだけ英語で見たい場面がある。"""
    environ = {'BTRFS_TIMELINE_LANG': 'en', 'LANG': 'ja_JP.UTF-8'}
    assert i18n.detect_language(environ=environ) == 'en'


def test_region_variant_falls_back_to_the_language():
    """``ja_JP.UTF-8`` のような値から ``ja`` に落ちる。"""
    assert i18n.detect_language(environ={'LANG': 'ja_JP.UTF-8'}) == 'ja'


def test_unknown_locale_falls_back_to_english():
    assert i18n.detect_language(environ={'LANG': 'xx_YY.UTF-8'}) == 'en'


def test_c_locale_means_english():
    """``LANG=C`` は「翻訳しない」という意思表示として扱う。"""
    assert i18n.detect_language(environ={'LANG': 'C'}) == 'en'


def test_empty_environment_falls_back_to_english():
    assert i18n.detect_language(environ={}) == 'en'


# --------------------------------------------------------------------------
# 訳文の引き方
# --------------------------------------------------------------------------

def test_translate_uses_the_selected_language():
    i18n.set_language('ja')
    assert i18n.translate('history.state.missing') == '(存在しない)'
    i18n.set_language('en')
    assert i18n.translate('history.state.missing') == '(does not exist)'


def test_named_placeholders_are_filled():
    i18n.set_language('en')
    message = i18n.translate('error.no-btrfs-mount', path='/srv/data')
    assert '/srv/data' in message


def test_unknown_key_returns_the_key():
    """カタログに無いキーでも落ちない。開発中に気付ければよい。"""
    i18n.set_language('ja')
    assert i18n.translate('no.such.key') == 'no.such.key'


def test_untranslated_key_falls_back_to_english(monkeypatch):
    """訳が欠けていても英語で表示される (部分的な翻訳を許す)。"""
    monkeypatch.setitem(i18n._catalogs, 'xx', {})
    i18n.set_language('xx')
    assert i18n.translate('history.state.ok') == 'ok'


def test_broken_placeholder_falls_back_to_english(monkeypatch):
    """訳文のプレースホルダ名が間違っていても、実行時エラーにしない。"""
    monkeypatch.setitem(i18n._catalogs, 'xx', {'error.no-btrfs-mount': '壊れた訳: {pathh}'})
    i18n.set_language('xx')
    message = i18n.translate('error.no-btrfs-mount', path='/srv/data')
    assert '/srv/data' in message


# --------------------------------------------------------------------------
# 表示幅 (翻訳すると表が崩れる問題)
# --------------------------------------------------------------------------

def test_padding_counts_display_width_not_characters():
    """全角文字は 1 文字で 2 桁を占める。文字数で詰めると表が崩れる。"""
    assert cli._display_width('サイズ') == 6
    assert cli._display_width('SIZE') == 4
    assert cli._pad('サイズ', 10) == 'サイズ' + ' ' * 4
    assert cli._pad('SIZE', 10, '>') == ' ' * 6 + 'SIZE'


def test_translated_table_header_stays_aligned():
    """英語と日本語で、ヘッダ行の表示桁数が揃う。"""
    widths = []
    for language in ('en', 'ja'):
        i18n.set_language(language)
        header = cli._row([
            i18n.translate('history.column.index'),
            i18n.translate('history.column.first-seen'),
            i18n.translate('history.column.last-seen'),
            i18n.translate('history.column.size'),
            i18n.translate('history.column.snapshots'),
            i18n.translate('history.column.state'),
        ])
        widths.append(cli._display_width(header.rstrip()) - cli._display_width(
            header.rstrip().split(' ')[-1]))
    assert widths[0] == widths[1]


# --------------------------------------------------------------------------
# CLI との接続
# --------------------------------------------------------------------------

@pytest.mark.parametrize('argv,expected', [
    (['history', '--lang', 'ja', 'x'], 'ja'),
    (['history', '--lang=ja', 'x'], 'ja'),
    (['history', 'x'], None),
    (['--lang'], None),
])
def test_preselect_language(argv, expected):
    """ヘルプ文もカタログから引くため、パーサ構築前に --lang を読む必要がある。"""
    assert cli.preselect_language(argv) == expected


def test_help_text_follows_the_language():
    i18n.set_language('ja')
    assert 'btrfs' in cli.build_parser().format_help()
    i18n.set_language('en')
    assert 'Browse and restore' in cli.build_parser().format_help()
