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

from btrfs_timeline import i18n
from btrfs_timeline.web import server as server_module

NODE = shutil.which('node')
STATIC = server_module.STATIC_DIRECTORY
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'js')

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
        text = handle.read()
    # 拡張子を変えた以上、モジュール間の参照も合わせる必要がある
    for module in ('transport', 'i18n'):
        text = text.replace("'./{0}.js'".format(module), "'./{0}.mjs'".format(module))
    target.write_text(text, encoding='utf-8')
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


# --------------------------------------------------------------------------
# 画面を実際に動かす
# --------------------------------------------------------------------------

def _stage(tmp_path):
    """本物の画面コードと、テスト用の transport を同じ場所に並べる。

    ``transport.js`` だけを差し替えれば画面が動く、という設計がそのまま効いている
    (Cockpit 版が差し替えるのと同じ場所を、テストが差し替えているだけ)。
    """
    for name in ('app.js', 'i18n.js'):
        as_module(tmp_path, name)
    for name in ('transport.mjs', 'drive.mjs'):
        with open(os.path.join(FIXTURES, name), encoding='utf-8') as handle:
            (tmp_path / name).write_text(handle.read(), encoding='utf-8')
    # 本物のカタログを使う。訳文の引き方まで含めて確かめたいので、作り物にしない
    catalogs = {code: i18n.load_catalog(code) for code in i18n.available_languages()}
    (tmp_path / 'fixture.mjs').write_text(
        'export const catalogs = {0};\n'.format(json.dumps(catalogs, ensure_ascii=False)),
        encoding='utf-8')
    return tmp_path / 'drive.mjs'


@pytest.fixture(scope='module')
def driven(tmp_path_factory):
    """画面をひととおり操作した結果を返す (重いので 1 回だけ実行する)。"""
    tmp_path = tmp_path_factory.mktemp('ui')
    script = _stage(tmp_path)
    completed = subprocess.run(
        [NODE, str(script)], capture_output=True, text=True, cwd=str(tmp_path))
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_no_step_reports_an_error(driven):
    """どの操作でもエラー行が出ない。

    app.js の例外は全て run() が status 行に落とすので、ここを見れば
    未定義の参照・変数名の衝突・API の呼び違いはまとめて捕まる。
    """
    assert driven['failures'] == []


def test_every_step_ran(driven):
    """途中で止まらず、想定した操作を最後まで行えている。"""
    performed = [step['step'] for step in driven['steps']]
    assert performed == [
        '起動', 'ファイルを選ぶ', 'プレビュー', '差分に切り替え',
        '言語を切り替え', '隠しファイルを表示', 'ディレクトリへ移動',
        '過去の時点を開く', '現在に戻る',
    ]


def test_the_screen_actually_rendered(driven):
    """描画そのものが行われている (エラーが無いだけでは足りない)。"""
    assert driven['entries'] > 0
    assert driven['versions'] > 0
    assert driven['preview']


def test_empty_header_stays_empty_on_screen(driven):
    """操作列の見出しは空のまま (キー名が出ない)。"""
    assert driven['actionsHeader'] == ''


def test_switching_language_reaches_the_labels(driven):
    """言語を切り替えると、画面のラベルまで差し替わる。"""
    assert driven['language'] == 'ja'
    assert driven['hiddenLabel'] == '隠しファイルを表示'
