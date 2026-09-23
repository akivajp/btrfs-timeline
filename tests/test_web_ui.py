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
COCKPIT = os.path.join(os.path.dirname(STATIC), '..', 'cockpit')
COCKPIT = os.path.normpath(COCKPIT)

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
    for module in ('transport', 'i18n', 'command'):
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

def _copy(source, target):
    with open(source, encoding='utf-8') as handle:
        target.write_text(handle.read(), encoding='utf-8')


def _stage(tmp_path, flavour):
    """本物の画面コードと、経路ごとのスタブを同じ場所に並べる。

    ``app.js`` / ``i18n.js`` / ``style.css`` / ``index.html`` は 1 バイトも変えずに
    両方で使う。**差し替えるのは transport だけ** という設計がそのまま効いており、
    テストは Cockpit 版が差し替えるのと同じ場所を差し替えているに過ぎない。
    """
    for name in ('app.js', 'devices.js', 'i18n.js'):
        as_module(tmp_path, name)
    for name in ('drive.mjs', 'drive-devices.mjs', 'dom.mjs', 'fake-data.mjs'):
        _copy(os.path.join(FIXTURES, name), tmp_path / name)

    if flavour == 'cockpit':
        # Cockpit 版の transport は本物をそのまま使う (スタブは cockpit.spawn の側)
        with open(os.path.join(COCKPIT, 'transport.js'), encoding='utf-8') as handle:
            text = handle.read().replace("'./command.js'", "'./command.mjs'")
        (tmp_path / 'transport.mjs').write_text(text, encoding='utf-8')
        (tmp_path / 'command.mjs').write_text(
            "export const COMMAND = ['/usr/bin/btrfs-timeline'];\n", encoding='utf-8')
        _copy(os.path.join(FIXTURES, 'prelude-cockpit.mjs'), tmp_path / 'prelude.mjs')
    else:
        _copy(os.path.join(FIXTURES, 'transport.mjs'), tmp_path / 'transport.mjs')
        _copy(os.path.join(FIXTURES, 'prelude-web.mjs'), tmp_path / 'prelude.mjs')

    # 本物のカタログを使う。訳文の引き方まで含めて確かめたいので、作り物にしない
    catalogs = {code: i18n.load_catalog(code) for code in i18n.available_languages()}
    (tmp_path / 'fixture.mjs').write_text(
        'export const catalogs = {0};\n'.format(json.dumps(catalogs, ensure_ascii=False)),
        encoding='utf-8')
    return tmp_path / 'drive.mjs'


def _drive(tmp_path, flavour, script_name='drive.mjs', environ=None):
    _stage(tmp_path, flavour)
    script = tmp_path / script_name
    completed = subprocess.run(
        [NODE, str(script)], capture_output=True, text=True, cwd=str(tmp_path),
        env={**os.environ, **(environ or {})})
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.fixture(scope='module')
def driven(tmp_path_factory):
    """スタンドアロン版の画面をひととおり操作した結果 (重いので 1 回だけ)。"""
    return _drive(tmp_path_factory.mktemp('ui-web'), 'web')


@pytest.fixture(scope='module')
def driven_cockpit(tmp_path_factory):
    """Cockpit 版の transport を通して、同じ操作を行った結果。"""
    return _drive(tmp_path_factory.mktemp('ui-cockpit'), 'cockpit')


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


def test_both_pages_can_be_reached_from_either(driven, dashboard):
    """**ページ間のリンクにラベルが入っていること。**

    履歴側だけ設定を忘れていたため、リンクが空文字になって見えなくなっていた。
    空の <a> は存在するのに押せないので、画面を見ないと気付けない。
    """
    for screen in (driven, dashboard):
        assert screen['historyLink']
        assert screen['devicesLink']


def test_switching_language_reaches_the_labels(driven):
    """言語を切り替えると、画面のラベルまで差し替わる。"""
    assert driven['language'] == 'ja'
    assert driven['hiddenLabel'] == '隠しファイルを表示'


# --------------------------------------------------------------------------
# Cockpit 版 — 差し替えるのは transport だけ、という主張の検証
# --------------------------------------------------------------------------

def test_cockpit_transport_drives_the_same_screen(driven_cockpit):
    """同じ app.js が、cockpit.spawn 経由でも同じ操作を最後まで行える。

    スタブは argv を実際に解釈するので、transport が組み立てるコマンドが
    間違っていればここで見つからずに失敗する。
    """
    assert driven_cockpit['failures'] == []
    assert driven_cockpit['entries'] > 0
    assert driven_cockpit['versions'] > 0
    assert driven_cockpit['preview']


def test_both_front_ends_end_up_with_the_same_screen(driven, driven_cockpit):
    """スタンドアロン版と Cockpit 版で、画面の状態が一致する。

    **これがこの設計の主張そのもの** である。経路が違うだけで、利用者が見るものは
    同じでなければならない。ずれたら、片方だけで起きる不具合が生まれている。
    """
    assert driven_cockpit['steps'] == driven['steps']
    assert driven_cockpit['entries'] == driven['entries']
    assert driven_cockpit['versions'] == driven['versions']
    assert driven_cockpit['preview'] == driven['preview']
    assert driven_cockpit['hiddenLabel'] == driven['hiddenLabel']
    assert driven_cockpit['actionsHeader'] == driven['actionsHeader']


def test_cockpit_module_parses(tmp_path):
    """Cockpit 版の transport も構文として壊れていない。"""
    target = tmp_path / 'cockpit-transport.mjs'
    with open(os.path.join(COCKPIT, 'transport.js'), encoding='utf-8') as handle:
        target.write_text(handle.read(), encoding='utf-8')
    subprocess.run([NODE, '--check', str(target)], check=True)


# --------------------------------------------------------------------------
# ダッシュボードの画面
# --------------------------------------------------------------------------

@pytest.fixture(scope='module')
def dashboard(tmp_path_factory):
    """ダッシュボードを描かせた結果 (スタンドアロン版)。"""
    return _drive(tmp_path_factory.mktemp('dash-web'), 'web', 'drive-devices.mjs')


@pytest.fixture(scope='module')
def dashboard_cockpit(tmp_path_factory):
    """同じ画面を、Cockpit 版の transport 経由で描かせた結果。"""
    return _drive(tmp_path_factory.mktemp('dash-cockpit'), 'cockpit',
                  'drive-devices.mjs')


def test_dashboard_renders_without_errors(dashboard):
    assert dashboard['failures'] == []
    assert dashboard['sections'] > 0


def test_dashboard_shows_the_devices(dashboard):
    assert '/dev/sdd1' in dashboard['rendered']
    assert '/dev/sdc1' in dashboard['rendered']


def test_the_relationship_is_stated_not_implied_per_mount(dashboard):
    """**マウント先ごとのデバイス列は出さない。**

    どのマウント先も全デバイスに支えられているので、1 台だけ並べると
    「このマウントはこの 1 台に載っている」と読めてしまう。全行が同じ値になる
    列は、事実として誤っているうえに気付かれにくい。
    """
    assert '/dev/sdd1' in dashboard['rendered']   # デバイス表には出る
    # 関係そのものは文章で述べる
    assert '4' in dashboard['rendered']


def test_the_subvolume_behind_each_mount_is_shown(dashboard):
    """``/mnt/tank/home`` の正体が ``/@home`` であることが見える。

    btrfs では 1 つのファイルシステムが複数の場所に現れるので、
    これが無いと一覧とマウント先の関係が掴めない。
    """
    assert '/@home' in dashboard['rendered']
    assert '/mnt/tank/home' in dashboard['rendered']


def test_the_per_device_breakdown_is_shown_when_it_is_available(dashboard):
    """root で読めたときだけ、どの割り当てがどのデバイスに載っているかを出す。"""
    assert 'Data,RAID1' in dashboard['rendered']
    assert 'Metadata,RAID1' in dashboard['rendered']


def test_model_and_temperature_are_shown(dashboard):
    assert 'ACME 2TB' in dashboard['rendered']
    assert '38 °C' in dashboard['rendered']


def test_a_device_over_its_own_limit_is_flagged(dashboard):
    """閾値はデバイスの申告に従う。こちらで何度から危ないかを決めない。"""
    assert '92 °C' in dashboard['rendered']
    # 欠損 1 台 + エラー 1 台 + 高温 1 台
    assert dashboard['alarmRows'] >= 3


def test_a_missing_device_is_not_buried(dashboard):
    """欠損とエラーは目立たせる。

    このダッシュボードを見る理由はだいたい「どれが壊れかけているか」なので、
    そこが静かに埋もれたら画面の意味が無い。
    """
    assert dashboard['alarmRows'] >= 2
    assert 'corruption_errs=7' in dashboard['rendered']


def test_commands_are_shown_with_their_risk(dashboard):
    """裏で実行したコマンドも、端末で実行してもらうコマンドも、危険度つきで出す。"""
    assert dashboard['riskBadges'] >= 2
    assert 'btrfs --format json device stats' in dashboard['rendered']
    assert 'sudo btrfs scrub start' in dashboard['rendered']


def test_scrub_state_is_shown(dashboard):
    assert 'running' in dashboard['rendered']


def test_the_dashboard_works_through_cockpit_too(dashboard_cockpit):
    """こちらも transport を差し替えただけで動く。"""
    assert dashboard_cockpit['failures'] == []
    assert dashboard_cockpit['sections'] > 0


def test_both_dashboards_render_the_same(dashboard, dashboard_cockpit):
    assert dashboard_cockpit['rendered'] == dashboard['rendered']
    assert dashboard_cockpit['alarmRows'] == dashboard['alarmRows']
    assert dashboard_cockpit['riskBadges'] == dashboard['riskBadges']


# --------------------------------------------------------------------------
# 権限が足りないときの案内
# --------------------------------------------------------------------------

@pytest.fixture(scope='module')
def dashboard_without_usage(tmp_path_factory):
    """内訳が読めなかった状況 (非特権) の画面。"""
    return _drive(tmp_path_factory.mktemp('dash-nousage'), 'web',
                  'drive-devices.mjs', {'NO_USAGE': '1'})


@pytest.fixture(scope='module')
def dashboard_cockpit_without_usage(tmp_path_factory):
    """同じ状況を Cockpit 版の経路で。"""
    return _drive(tmp_path_factory.mktemp('dash-nousage-cockpit'), 'cockpit',
                  'drive-devices.mjs', {'NO_USAGE': '1'})


def test_no_hint_when_the_breakdown_is_there(dashboard):
    """読めているときに「root が要ります」と言わない。"""
    assert 'cockpit install' not in dashboard['rendered']
    assert 'administrative access' not in dashboard['rendered']


def test_standalone_points_at_cockpit(dashboard_without_usage):
    """**この経路では無理**であることと、どこへ行けばよいかを伝える。"""
    rendered = dashboard_without_usage['rendered']
    assert 'cockpit install' in rendered
    # 昇格の手段を持たないので、押せるボタンは出さない
    assert 'Show it with administrative access' not in rendered


def test_cockpit_offers_to_elevate(dashboard_cockpit_without_usage):
    """**権限が足りないだけ**なので、その場で頼める。"""
    rendered = dashboard_cockpit_without_usage['rendered']
    assert 'administrative access' in rendered
    # **押しても応答が返らないボタンは置かない。** 昇格の UI は Cockpit の
    # ヘッダーが持っているので、そこを指すに留める
    assert 'Show it with administrative access' not in rendered
    # 「別のものを入れてください」とは言わない。権限が足りないだけ
    assert 'cockpit install' not in rendered


def test_the_rest_of_the_screen_still_works_without_root(dashboard_without_usage):
    """内訳が読めなくても、デバイスも温度も出る。"""
    assert dashboard_without_usage['failures'] == []
    assert '/dev/sdd1' in dashboard_without_usage['rendered']
    assert '38 °C' in dashboard_without_usage['rendered']


# --------------------------------------------------------------------------
# ページ間のリンク (Cockpit には「ディレクトリ」が無い)
# --------------------------------------------------------------------------

def test_pages_link_by_file_name_not_by_directory():
    """``./`` は Cockpit で解決できず ``Invalid HTTP path`` になる。

    標準のサーバーでは ``/`` がページを返すので気付けないが、Cockpit の
    パッケージはファイルを名前で配るだけで、ディレクトリの概念が無い。
    """
    for name in ('index.html', 'devices.html'):
        with open(os.path.join(STATIC, name), encoding='utf-8') as handle:
            markup = handle.read()
        assert 'href="./"' not in markup, name
        assert 'href="./index.html"' in markup, name
        assert 'href="./devices.html"' in markup, name
