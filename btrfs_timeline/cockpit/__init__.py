#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cockpit モジュールの組み立てと設置。

Cockpit のパッケージは **静的ファイルと ``manifest.json`` だけ** で、サーバーサイドが
存在しない。そのため、ここでやることは「画面の共有部分」と「Cockpit 版の
``transport.js``」を 1 つのディレクトリに並べることだけである。

pip で入れた場合、画面のファイルは site-packages の中にあって Cockpit からは見えない。
その橋渡しをするのが ``btrfs-timeline cockpit install`` で、Cockpit が探す場所へ
必要なファイルを配置する。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import List, Optional

from .. import i18n

#: Cockpit がパッケージとして認識する名前 (URL は /cockpit/<この名前>/ になる)
PACKAGE_NAME = 'btrfs-timeline'

#: ログイン中の利用者だけに入れる場所。root を必要としない
USER_DIRECTORY = os.path.expanduser('~/.local/share/cockpit')

#: システム全体に入れる場所。書き込みに root が要る
SYSTEM_DIRECTORY = '/usr/share/cockpit'

SOURCE_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
STATIC_DIRECTORY = os.path.join(os.path.dirname(SOURCE_DIRECTORY), 'web', 'static')

#: スタンドアロン Web UI とそのまま共有するファイル。
#: **transport.js だけは共有しない** — 差し替えるのがそこだけ、という設計である。
SHARED_FILES = ('index.html', 'app.js', 'devices.html', 'devices.js',
                'i18n.js', 'pages.js', 'style.css')

#: Cockpit 版に固有のファイル
OWN_FILES = ('manifest.json', 'transport.js')

#: メニューに出る名前と、その訳を引くカタログのキー。
#: **値は ``manifest.json`` の label と一致していなければならない**
#: (Cockpit は原文をキーにして訳を引くため)。
MENU_LABEL_KEY = 'cockpit.menu-label'
MENU_LABEL_KEYS = ('cockpit.menu-label', 'cockpit.menu-label-devices')


def target_directory(system: bool = False) -> str:
    """設置先のディレクトリを返す。"""
    base = SYSTEM_DIRECTORY if system else USER_DIRECTORY
    return os.path.join(base, PACKAGE_NAME)


def command() -> List[str]:
    """Cockpit から起動するコマンド列を決める。

    ``cockpit.spawn`` が受け取る PATH は、このコマンドを入れた環境とは限らない
    (pipx や venv に入れていれば、まず通っていない)。そのため **絶対パスに解決して
    書き出す**。同じ環境の実行ファイルが見つからない場合は、モジュールとして
    起動する形に落とす。こちらは必ず動く。
    """
    candidate = os.path.join(os.path.dirname(sys.executable), 'btrfs-timeline')
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return [candidate]
    found = shutil.which('btrfs-timeline')
    if found:
        return [found]
    return [sys.executable, '-m', 'btrfs_timeline']


def _write_command_file(directory: str) -> None:
    """``transport.js`` が読み込むコマンド定義を書き出す。"""
    path = os.path.join(directory, 'command.js')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(
            '// btrfs-timeline cockpit install が生成する。手で編集しても、\n'
            '// 入れ直すと上書きされる。\n'
            'export const COMMAND = {0};\n'.format(json.dumps(command())))


def _write_manifest_translations(directory: str) -> None:
    """メニューに出る名前の訳を、Cockpit が読む形で書き出す。

    Cockpit は ``manifest.json`` の文字列を直接は訳さない。同じディレクトリの
    ``po.manifest.<言語>.js`` が ``cockpit.locale()`` を呼び、英語の原文から訳語への
    対応を渡す、という仕組みになっている (Cockpit 本体のパッケージも同じ形)。

    訳語は **このツールのカタログから取る**。翻訳を追加した人が、メニューの名前だけ
    別の場所で改めて訳す必要が無いようにするため。
    """
    english = i18n.load_catalog(i18n.FALLBACK_LANGUAGE)
    for code in i18n.available_languages():
        if code == i18n.FALLBACK_LANGUAGE:
            continue  # 原文なので訳は要らない
        catalog = i18n.load_catalog(code)
        pairs = [(english.get(key), catalog.get(key)) for key in MENU_LABEL_KEYS]
        pairs = [(source, translated) for source, translated in pairs
                 if source and translated and source != translated]
        if not pairs:
            continue
        path = os.path.join(directory, 'po.manifest.{0}.js'.format(code))
        with open(path, 'w', encoding='utf-8') as handle:
            # Cockpit 本体が出力するものと同じ形にしてある。plural-forms は
            # このファイルが訳すのがメニュー名 1 つだけで複数形を含まないため
            # 使われないが、読み込み側が期待する可能性を考えて省かない。
            entries = ''.join(
                ' %s: [null, %s],\n' % (json.dumps(source, ensure_ascii=False),
                                         json.dumps(translated, ensure_ascii=False))
                for source, translated in pairs)
            handle.write(
                'cockpit.locale({\n'
                ' "": {\n'
                '  "plural-forms": (n) => 0,\n'
                '  "language": %s,\n'
                '  "language-direction": %s\n'
                ' },\n'
                '%s'
                '});\n' % (
                    json.dumps(code),
                    json.dumps(catalog.get('language.direction', 'ltr')),
                    entries,
                ))


def install(system: bool = False) -> str:
    """Cockpit モジュールを設置し、設置先のパスを返す。"""
    directory = target_directory(system)
    os.makedirs(directory, exist_ok=True)

    for name in SHARED_FILES:
        shutil.copyfile(os.path.join(STATIC_DIRECTORY, name),
                        os.path.join(directory, name))
    for name in OWN_FILES:
        shutil.copyfile(os.path.join(SOURCE_DIRECTORY, name),
                        os.path.join(directory, name))
    _write_command_file(directory)
    _write_manifest_translations(directory)
    return directory


def is_ours(directory: str) -> bool:
    """そのディレクトリが、このツールが設置したものかを判定する。

    削除する前に必ず確かめる。同名の別物を消してしまうと取り返しがつかない。
    """
    manifest = os.path.join(directory, 'manifest.json')
    if not os.path.isfile(manifest):
        return False
    try:
        with open(manifest, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return False
    return 'index' in (data.get('tools') or {}) and os.path.isfile(
        os.path.join(directory, 'transport.js'))


def uninstall(system: bool = False) -> Optional[str]:
    """設置したモジュールを取り除く。

    Returns:
        消したパス。元から無ければ None。

    Raises:
        PermissionError: そこにあるのが、このツールが設置したものではない。
    """
    directory = target_directory(system)
    if not os.path.isdir(directory):
        return None
    if not is_ours(directory):
        raise PermissionError(i18n.translate('error.not-our-module', path=directory))
    shutil.rmtree(directory)
    return directory
