#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cockpit モジュールの設置のテスト。

一番大事なのは「画面のファイルは共有していて、差し替えるのは transport だけ」
という主張が、設置されたものでも成り立っていることを確かめること。
ここが崩れると、片方だけで起きる不具合が生まれる。

削除は取り返しがつかないので、設置したもの以外を消さないことも押さえる。
"""

import json
import os

import pytest

from btrfs_timeline import cli, cockpit


@pytest.fixture
def target(tmp_path, monkeypatch):
    """設置先を一時ディレクトリに向ける。"""
    monkeypatch.setattr(cockpit, 'USER_DIRECTORY', str(tmp_path / 'cockpit'))
    return os.path.join(str(tmp_path / 'cockpit'), cockpit.PACKAGE_NAME)


def test_install_places_everything_cockpit_needs(target):
    directory = cockpit.install()
    assert directory == target
    for name in ('manifest.json', 'index.html', 'app.js', 'i18n.js', 'style.css',
                 'transport.js', 'command.js'):
        assert os.path.isfile(os.path.join(directory, name)), name


def test_shared_files_are_byte_identical(target):
    """画面のコードは共有している。コピーであって、作り替えではない。"""
    directory = cockpit.install()
    for name in cockpit.SHARED_FILES:
        with open(os.path.join(cockpit.STATIC_DIRECTORY, name), encoding='utf-8') as a:
            with open(os.path.join(directory, name), encoding='utf-8') as b:
                assert a.read() == b.read(), name


def test_only_the_transport_differs(target):
    """**差し替えるのは transport だけ。** これがこの設計の主張そのもの。"""
    directory = cockpit.install()
    with open(os.path.join(cockpit.STATIC_DIRECTORY, 'transport.js'), encoding='utf-8') as a:
        standalone = a.read()
    with open(os.path.join(directory, 'transport.js'), encoding='utf-8') as b:
        installed = b.read()
    assert installed != standalone
    assert 'cockpit.spawn' in installed
    assert 'fetch(' not in installed


def test_manifest_is_valid_for_cockpit(target):
    directory = cockpit.install()
    with open(os.path.join(directory, 'manifest.json'), encoding='utf-8') as handle:
        manifest = json.load(handle)
    assert manifest['tools']['index']['path'] == 'index.html'
    assert 'cockpit' in manifest['requires']


def test_command_file_records_an_absolute_path(target):
    """``cockpit.spawn`` の PATH にこのコマンドがあるとは限らないので、絶対パスにする。"""
    directory = cockpit.install()
    with open(os.path.join(directory, 'command.js'), encoding='utf-8') as handle:
        text = handle.read()
    assert text.startswith('//')
    payload = text[text.index('['):text.rindex(']') + 1]
    argv = json.loads(payload)
    assert argv and os.path.isabs(argv[0])


def test_install_is_repeatable(target):
    """入れ直しても壊れない (更新はこれで行う)。"""
    cockpit.install()
    directory = cockpit.install()
    assert os.path.isfile(os.path.join(directory, 'manifest.json'))


def test_uninstall_removes_what_we_installed(target):
    cockpit.install()
    assert cockpit.uninstall() == target
    assert not os.path.exists(target)


def test_uninstall_on_nothing_is_not_an_error(target):
    assert cockpit.uninstall() is None


def test_uninstall_refuses_a_directory_we_did_not_install(target):
    """同名の別物を消してしまうと取り返しがつかない。"""
    os.makedirs(target)
    with open(os.path.join(target, 'important.txt'), 'w', encoding='utf-8') as handle:
        handle.write('someone else')

    with pytest.raises(PermissionError):
        cockpit.uninstall()
    assert os.path.isfile(os.path.join(target, 'important.txt'))


def test_is_ours_needs_both_markers(tmp_path):
    """manifest だけ、あるいは transport だけでは、うちのものとは見なさない。"""
    directory = str(tmp_path)
    assert cockpit.is_ours(directory) is False
    with open(os.path.join(directory, 'manifest.json'), 'w', encoding='utf-8') as handle:
        json.dump({'tools': {'index': {}}}, handle)
    assert cockpit.is_ours(directory) is False
    open(os.path.join(directory, 'transport.js'), 'w', encoding='utf-8').close()
    assert cockpit.is_ours(directory) is True


def test_cli_reports_where_it_would_go(target, capsys):
    assert cli.main(['cockpit', 'path']) == 0
    assert capsys.readouterr().out.strip() == target


def test_cli_installs_and_removes(target, capsys):
    assert cli.main(['cockpit', 'install']) == 0
    assert os.path.isfile(os.path.join(target, 'manifest.json'))
    capsys.readouterr()

    assert cli.main(['cockpit', 'uninstall']) == 0
    assert not os.path.exists(target)


# --------------------------------------------------------------------------
# メニューに出る名前の翻訳
# --------------------------------------------------------------------------

def test_manifest_label_matches_the_english_catalog(target):
    """``manifest.json`` の label は、訳を引くときのキーそのものである。

    Cockpit は原文をキーにして ``po.manifest.<言語>.js`` から訳を探すので、
    ここがカタログとずれると、訳があるのに引かれないという状態になる。
    """
    from btrfs_timeline import i18n

    directory = cockpit.install()
    with open(os.path.join(directory, 'manifest.json'), encoding='utf-8') as handle:
        label = json.load(handle)['tools']['index']['label']
    assert label == i18n.load_catalog('en')[cockpit.MENU_LABEL_KEY]


def test_menu_label_is_translated_from_the_same_catalog(target):
    """翻訳を追加した人が、メニュー名だけ別の場所で訳し直さずに済む。"""
    from btrfs_timeline import i18n

    directory = cockpit.install()
    path = os.path.join(directory, 'po.manifest.ja.js')
    assert os.path.isfile(path)
    with open(path, encoding='utf-8') as handle:
        text = handle.read()

    assert text.startswith('cockpit.locale({')
    assert '"language": "ja"' in text
    assert json.dumps(i18n.load_catalog('en')[cockpit.MENU_LABEL_KEY]) in text
    assert i18n.load_catalog('ja')[cockpit.MENU_LABEL_KEY] in text


def test_no_translation_file_for_the_source_language(target):
    """英語は原文なので、訳のファイルは要らない。"""
    directory = cockpit.install()
    assert not os.path.exists(os.path.join(directory, 'po.manifest.en.js'))


def test_a_translation_file_exists_for_every_translated_language(target):
    """言語を足したら、メニュー名の訳も自動的に出来る。"""
    from btrfs_timeline import i18n

    directory = cockpit.install()
    english = i18n.load_catalog('en')[cockpit.MENU_LABEL_KEY]
    for code in i18n.available_languages():
        if code == 'en':
            continue
        if i18n.load_catalog(code).get(cockpit.MENU_LABEL_KEY) == english:
            continue  # 訳さない選択をした言語
        assert os.path.isfile(os.path.join(directory, 'po.manifest.{0}.js'.format(code)))
