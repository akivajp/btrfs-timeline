#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スタンドアロン Web UI のテスト。

守りたいのは 2 種類ある。

- **公開契約**: API が返す JSON の形が CLI の ``--json`` と同じであること。
  食い違うと、UI コードを Cockpit 版と共有できなくなる。
- **安全側の既定**: 閉じ込めの外を読ませない、クロスサイトから復元させない、
  閲覧専用なら書かせない。どれも破れたときの被害が大きい。
"""

import os

import pytest

webtest = pytest.importorskip('webtest')
pytest.importorskip('bottle')

from btrfs_timeline.core import mounts as mounts_module  # noqa: E402
from btrfs_timeline.web import server as server_module  # noqa: E402
from btrfs_timeline.web.auth import Credentials  # noqa: E402

INFO_XML = '''<?xml version="1.0"?>
<snapshot><type>single</type><num>{num}</num><date>{date}</date><description></description></snapshot>
'''


def _add_snapshot(root, num, date, files):
    entry = root / '.snapshots' / str(num)
    content_root = entry / 'snapshot'
    content_root.mkdir(parents=True)
    entry.joinpath('info.xml').write_text(INFO_XML.format(num=num, date=date), encoding='utf-8')
    for rel, content in files.items():
        if content is None:
            continue
        target = content_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        stamp = 1000000 + len(content)
        os.utime(target, (stamp, stamp))


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """``tmp_path`` を btrfs マウントに見せかけ、履歴を持つファイルを用意する。

    実際の btrfs を要求すると、この環境でしかテストできなくなってしまう。
    マウント情報の読み取りだけを差し替えれば、それ以外は本物と同じ経路を通る。
    """
    _add_snapshot(tmp_path, 1, '2026-01-01 00:00:00', {'notes.txt': 'first'})
    _add_snapshot(tmp_path, 2, '2026-02-01 00:00:00', {'notes.txt': 'second'})
    (tmp_path / 'notes.txt').write_text('live', encoding='utf-8')
    (tmp_path / 'documents').mkdir()

    fake = mounts_module.MountPoint(
        mount_point=str(tmp_path), fs_type='btrfs', device='/dev/test',
        subvol='/@home', root='/@home',
    )
    monkeypatch.setattr(mounts_module, 'read_mounts', lambda *a, **k: [fake])
    return tmp_path


def make_app(tree, **options):
    settings = server_module.Settings(root=str(tree), **{
        k: v for k, v in options.items() if k != 'credentials'})
    return webtest.TestApp(
        server_module.create_app(settings, options.get('credentials')))


@pytest.fixture
def app(tree):
    return make_app(tree)


# --------------------------------------------------------------------------
# 画面と設定
# --------------------------------------------------------------------------

def test_index_is_served_without_server_side_rendering(app):
    """HTML はサーバーで書き換えない (Cockpit 版と同じファイルを配る)。"""
    page = app.get('/')
    assert page.status_int == 200
    assert 'btrfs-timeline' in page.text
    # サーバー側で埋め込むデータが無いことの確認
    assert '{{' not in page.text


def test_assets_are_served_at_the_root(app):
    """index.html は ./app.js を相対参照する。Cockpit 版でも同じ形になるため。"""
    for asset in ('/app.js', '/transport.js', '/style.css'):
        assert app.get(asset).status_int == 200


def test_config_carries_the_translation_catalog(app):
    """ブラウザは CLI と同じカタログを引く。JSON カタログにした理由そのもの。"""
    data = app.get('/api/config?lang=ja').json
    assert data['language'] == 'ja'
    assert data['catalog']['web.restore'] == '復元'
    assert 'en' in data['languages']


def test_english_can_be_requested_explicitly(app):
    data = app.get('/api/config?lang=en').json
    assert data['language'] == 'en'
    assert data['catalog']['web.restore'] == 'Restore'


def test_unknown_language_still_returns_a_usable_catalog(app):
    """対応していない言語を渡されても、通常の判定に落ちて画面は成立する。

    ``--lang`` と違い HTTP のクエリは誰でも好きな値を入れられるので、
    弾くのではなく無視する。
    """
    data = app.get('/api/config?lang=xx').json
    assert data['language'] in data['languages']
    assert data['catalog']['web.restore']


# --------------------------------------------------------------------------
# 閲覧
# --------------------------------------------------------------------------

def test_browse_lists_entries(app, tree):
    data = app.get('/api/browse', {'path': str(tree)}).json
    names = [e['name'] for e in data['entries']]
    assert 'notes.txt' in names
    assert data['parents'][0] == str(tree)


def test_browse_refuses_to_leave_the_root(app, tree):
    response = app.get('/api/browse', {'path': str(tree.parent)}, expect_errors=True)
    assert response.status_int == 403


def test_history_matches_the_cli_contract(app, tree):
    """版の形は CLI の ``--json`` と同じでなければならない。"""
    data = app.get('/api/history', {'path': str(tree / 'notes.txt')}).json
    assert data['target'] == str(tree / 'notes.txt')
    first = data['versions'][0]
    assert set(first) == {
        'index', 'path', 'size', 'mtime', 'exists', 'is_live',
        'first_snapshot_id', 'last_snapshot_id', 'first_seen', 'last_seen',
        'snapshot_count',
    }
    assert [v['index'] for v in data['versions']] == list(range(1, len(data['versions']) + 1))
    assert data['versions'][-1]['is_live'] is True


def test_preview_returns_the_content_of_that_version(app, tree):
    data = app.get('/api/preview', {'path': str(tree / 'notes.txt'), 'index': 1}).json
    assert data['text'] == 'first'
    assert data['binary'] is False


def test_preview_marks_binary_content(app, tree, monkeypatch):
    (tree / '.snapshots' / '1' / 'snapshot' / 'notes.txt').write_bytes(b'bin\x00ary')
    data = app.get('/api/preview', {'path': str(tree / 'notes.txt'), 'index': 1}).json
    assert data['binary'] is True
    assert data['text'] == ''


def test_preview_truncates_large_versions(tree):
    app = make_app(tree, preview_limit=4)
    data = app.get('/api/preview', {'path': str(tree / 'notes.txt'), 'index': 1}).json
    assert data['truncated'] is True
    assert data['text'] == 'firs'


# --------------------------------------------------------------------------
# 復元
# --------------------------------------------------------------------------

def test_dry_run_changes_nothing(app, tree):
    before = sorted(os.listdir(tree))
    data = app.post_json('/api/restore', {
        'path': str(tree / 'notes.txt'), 'index': 1, 'dry_run': True}).json
    assert data['dry_run'] is True
    assert data['method'] == 'dry-run'
    assert sorted(os.listdir(tree)) == before


def test_restore_writes_a_sibling_file_by_default(app, tree):
    data = app.post_json('/api/restore', {
        'path': str(tree / 'notes.txt'), 'index': 1}).json
    assert data['in_place'] is False
    assert data['destination'] != str(tree / 'notes.txt')
    assert open(data['destination'], encoding='utf-8').read() == 'first'
    # 現在のファイルには触れていない
    assert (tree / 'notes.txt').read_text(encoding='utf-8') == 'live'


def test_in_place_restore_keeps_a_backup(app, tree):
    data = app.post_json('/api/restore', {
        'path': str(tree / 'notes.txt'), 'index': 1, 'in_place': True}).json
    assert (tree / 'notes.txt').read_text(encoding='utf-8') == 'first'
    assert open(data['backup'], encoding='utf-8').read() == 'live'


def test_restore_refuses_a_cross_site_request(app, tree):
    """BASIC 認証はブラウザが自動送信するため、認証だけでは防げない。"""
    response = app.post_json(
        '/api/restore', {'path': str(tree / 'notes.txt'), 'index': 1},
        headers={'Origin': 'http://evil.example'}, expect_errors=True)
    assert response.status_int == 403
    assert (tree / 'notes.txt').read_text(encoding='utf-8') == 'live'


def test_restore_refuses_to_leave_the_root(app, tree):
    response = app.post_json(
        '/api/restore', {'path': str(tree.parent / 'elsewhere.txt')},
        expect_errors=True)
    assert response.status_int == 403


def test_read_only_server_refuses_to_restore(tree):
    app = make_app(tree, read_only=True)
    assert app.get('/api/config').json['read_only'] is True
    response = app.post_json(
        '/api/restore', {'path': str(tree / 'notes.txt'), 'index': 1},
        expect_errors=True)
    assert response.status_int == 403
    assert (tree / 'notes.txt').read_text(encoding='utf-8') == 'live'


def test_live_version_cannot_be_restored(app, tree):
    versions = app.get('/api/history', {'path': str(tree / 'notes.txt')}).json['versions']
    live_index = versions[-1]['index']
    response = app.post_json(
        '/api/restore', {'path': str(tree / 'notes.txt'), 'index': live_index},
        expect_errors=True)
    assert response.status_int == 404


# --------------------------------------------------------------------------
# 認証
# --------------------------------------------------------------------------

def test_authentication_is_required_when_configured(tree):
    app = make_app(tree, credentials=Credentials('admin', 'secret'))
    assert app.get('/api/config', expect_errors=True).status_int == 401
    app.authorization = ('Basic', ('admin', 'secret'))
    assert app.get('/api/config').status_int == 200


def test_wrong_password_is_refused(tree):
    app = make_app(tree, credentials=Credentials('admin', 'secret'))
    app.authorization = ('Basic', ('admin', 'wrong'))
    assert app.get('/api/config', expect_errors=True).status_int == 401


def test_loopback_check():
    assert server_module.is_loopback('127.0.0.1') is True
    assert server_module.is_loopback('::1') is True
    assert server_module.is_loopback('localhost') is True
    assert server_module.is_loopback('0.0.0.0') is False


def test_restoring_a_symlinked_file_does_not_replace_the_link(app, tree):
    """リンク経由で指定しても、書き込むのは実体の側。

    dotfiles をリンクで運用している環境では、リンクのまま上書きすると
    リンクが通常ファイルに置き換わって運用ごと壊れる。CLI は realpath を
    取っているので、Web だけ挙動が違ってはいけない。
    """
    link = tree / 'link-to-notes.txt'
    os.symlink(str(tree / 'notes.txt'), str(link))

    data = app.post_json('/api/restore', {
        'path': str(link), 'index': 1, 'in_place': True}).json

    assert data['destination'] == str(tree / 'notes.txt')
    assert os.path.islink(str(link)) is True
    assert (tree / 'notes.txt').read_text(encoding='utf-8') == 'first'


# --------------------------------------------------------------------------
# 過去の時点のディレクトリと差分
# --------------------------------------------------------------------------

@pytest.fixture
def deleted_tree(tree):
    """スナップショットにだけ存在するファイルを足す。"""
    snapshot_root = tree / '.snapshots' / '1' / 'snapshot'
    (snapshot_root / 'draft.txt').write_text('draft', encoding='utf-8')
    (snapshot_root / 'old-folder').mkdir()
    return tree


def test_past_listing_reveals_deleted_entries(deleted_tree):
    """現在の一覧には出ないものが、過去の時点では出る。

    削除したファイルに辿り着く経路は、実質これしかない。
    """
    app = make_app(deleted_tree)
    now = [e['name'] for e in
           app.get('/api/browse', {'path': str(deleted_tree)}).json['entries']]
    assert 'draft.txt' not in now

    past = app.get('/api/browse',
                   {'path': str(deleted_tree), 'snapshot': '1'}).json
    entries = {e['name']: e for e in past['entries']}
    assert entries['draft.txt']['exists_now'] is False
    assert entries['old-folder']['exists_now'] is False
    assert entries['notes.txt']['exists_now'] is True
    assert past['snapshot']['id'] == '1'


def test_a_deleted_file_still_has_a_history(deleted_tree):
    """過去の一覧から辿ったパスで、そのまま履歴を引ける。"""
    app = make_app(deleted_tree)
    path = str(deleted_tree / 'draft.txt')
    versions = app.get('/api/history', {'path': path}).json['versions']
    assert any(v['exists'] and not v['is_live'] for v in versions)


def test_a_deleted_file_can_be_restored(deleted_tree):
    app = make_app(deleted_tree)
    path = str(deleted_tree / 'draft.txt')
    data = app.post_json('/api/restore', {'path': path, 'index': 1}).json
    assert open(data['destination'], encoding='utf-8').read() == 'draft'


def test_unknown_snapshot_is_reported(app, tree):
    response = app.get('/api/browse', {'path': str(tree), 'snapshot': 'nope'},
                       expect_errors=True)
    assert response.status_int == 404


def test_directory_history_tracks_entries_coming_and_going(deleted_tree):
    """ディレクトリの履歴は btrfs が保つ mtime から得られる。

    ファイルの内容変更では親の mtime は動かないので、結果として
    「項目の出入りがあった時点」だけが版として出る。欲しいのはその粒度。
    """
    app = make_app(deleted_tree)
    versions = app.get('/api/history', {'path': str(deleted_tree)}).json['versions']
    assert len(versions) >= 2
    assert versions[-1]['is_live'] is True


def test_diff_against_the_current_file_by_default(app, tree):
    data = app.get('/api/diff', {'path': str(tree / 'notes.txt'), 'from': 1}).json
    assert data['identical'] is False
    assert '-first' in data['text']
    assert '+live' in data['text']


def test_diff_between_two_arbitrary_versions(app, tree):
    data = app.get('/api/diff',
                   {'path': str(tree / 'notes.txt'), 'from': 1, 'to': 2}).json
    assert data['from'] == 1 and data['to'] == 2
    assert '-first' in data['text']
    assert '+second' in data['text']


def test_diff_reports_identical_versions(app, tree):
    data = app.get('/api/diff',
                   {'path': str(tree / 'notes.txt'), 'from': 1, 'to': 1}).json
    assert data['identical'] is True
    assert data['text'] == ''


def test_diff_rejects_an_unknown_version(app, tree):
    response = app.get('/api/diff', {'path': str(tree / 'notes.txt'), 'from': 99},
                       expect_errors=True)
    assert response.status_int == 404
