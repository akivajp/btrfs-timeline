#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ブラウザ側のコードのテスト。

このプロジェクトは JavaScript のツールチェインを持たない方針なので、
テストランナーも入れない。代わりに **Node があれば** 実際のモジュールを
そのまま import して確かめ、無ければ黙って飛ばす。

ここを用意した理由ははっきりしている。訳文の有無を ``||`` で判定していたために、
空文字の訳を持つキーが「未翻訳」と見なされ、画面にキー名がそのまま出ていた。
ブラウザ側に一行もテストが無かったため、実際に開くまで誰も気付けなかった。
"""

import json
import os
import shutil
import subprocess

import pytest

from btrfs_timeline.web import server as server_module

NODE = shutil.which('node')
STATIC = server_module.STATIC_DIRECTORY

pytestmark = pytest.mark.skipif(NODE is None, reason='node が無いので飛ばします')


def as_module(tmp_path, name: str):
    """``static`` のファイルを、そのままの内容で ``.mjs`` として置き直す。

    Node は ``package.json`` が無いディレクトリの ``.js`` を CommonJS と見なすため、
    ``export`` を含むファイルをそのまま import できない。このプロジェクトは
    ビルド手順も ``package.json`` も持たない方針なので、テスト側で拡張子だけ
    変えて渡す (中身は 1 バイトも変えない)。
    """
    source = os.path.join(STATIC, name)
    target = tmp_path / (os.path.splitext(name)[0] + '.mjs')
    with open(source, encoding='utf-8') as handle:
        target.write_text(handle.read(), encoding='utf-8')
    return target


def run_module(tmp_path, body: str):
    """``i18n.js`` を import する小さなスクリプトを実行し、結果を返す。

    スクリプトは ``print(...)`` した JSON を標準出力に出す約束にしてある。
    """
    as_module(tmp_path, 'i18n.js')
    script = tmp_path / 'check.mjs'
    script.write_text(
        "import {{ translate }} from './i18n.mjs';\n"
        "const print = (value) => console.log(JSON.stringify(value));\n"
        "{0}\n".format(body),
        encoding='utf-8',
    )
    completed = subprocess.run(
        [NODE, str(script)], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_empty_translation_is_used_as_is(tmp_path):
    """空文字の訳は「何も表示しない」という指示であって、未翻訳ではない。

    ここを取り違えると、操作列の見出しに ``web.actions`` と出る。
    """
    assert run_module(tmp_path, "print(translate({'web.actions': ''}, 'web.actions'));") == ''


def test_missing_key_falls_back_to_the_key(tmp_path):
    """カタログに無いキーはキー自身を返す (開発中に気付けるように)。"""
    assert run_module(tmp_path, "print(translate({}, 'no.such.key'));") == 'no.such.key'


def test_named_placeholders_are_filled(tmp_path):
    body = "print(translate({'k': 'to {path} now'}, 'k', {path: '/srv'}));"
    assert run_module(tmp_path, body) == 'to /srv now'


def test_unknown_placeholder_is_left_alone(tmp_path):
    """訳文が原文に無い名前を使っていても、例外にせずそのまま残す。"""
    body = "print(translate({'k': '{path} and {other}'}, 'k', {path: '/srv'}));"
    assert run_module(tmp_path, body) == '/srv and {other}'


def test_matches_the_python_side(tmp_path):
    """同じカタログ・同じキーで、Python と JavaScript の結果が一致する。

    CLI と Web UI が同じカタログを共有している以上、引き方がずれてはいけない。
    """
    from btrfs_timeline import i18n

    i18n.set_language('ja')
    catalog = i18n.load_catalog('ja')
    keys = ['web.actions', 'web.restore', 'history.state.ok', 'no.such.key']
    body = "const c = {0};\nprint({1}.map((k) => translate(c, k)));".format(
        json.dumps(catalog, ensure_ascii=False), json.dumps(keys))
    assert run_module(tmp_path, body) == [i18n.translate(k) for k in keys]


def test_every_ui_module_parses(tmp_path):
    """画面のモジュールが構文として壊れていないことだけは確かめる。

    DOM が無いので実行はできないが、構文エラーで真っ白になる事故は防げる。
    """
    for name in ('app.js', 'transport.js', 'i18n.js'):
        subprocess.run([NODE, '--check', str(as_module(tmp_path, name))], check=True)
