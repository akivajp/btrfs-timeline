#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""テキストの読み出しと差分のテスト。

似た内容の版が並ぶとき、戻す版を決める手掛かりは「中身」ではなく
「何が変わったか」であることが多い。ここが壊れると版を選べなくなる。
"""

import os

from btrfs_timeline.core import diff


def _write(path, text):
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)
    return str(path)


# --------------------------------------------------------------------------
# 読み出し
# --------------------------------------------------------------------------

def test_reads_text(tmp_path):
    path = _write(tmp_path / 'a.txt', 'hello\n')
    content = diff.read_text(path)
    assert content.text == 'hello\n'
    assert content.binary is False
    assert content.truncated is False
    assert content.missing is False


def test_detects_binary(tmp_path):
    path = str(tmp_path / 'a.bin')
    with open(path, 'wb') as handle:
        handle.write(b'bin\x00ary')
    content = diff.read_text(path)
    assert content.binary is True
    assert content.text == ''


def test_truncates_at_the_limit(tmp_path):
    path = _write(tmp_path / 'a.txt', 'x' * 100)
    content = diff.read_text(path, limit=10)
    assert content.truncated is True
    assert content.text == 'x' * 10


def test_missing_version_reads_as_empty(tmp_path):
    """その時点で存在しなかった版は「空」として扱う。

    こうしておくと、作成と削除を差分として素直に表現できる。
    """
    assert diff.read_text(None).missing is True
    assert diff.read_text(None).text == ''
    assert diff.read_text(str(tmp_path / 'nowhere')).missing is True


def test_invalid_utf8_does_not_raise(tmp_path):
    """壊れたエンコーディングでも落ちない (置換文字で読む)。"""
    path = str(tmp_path / 'a.txt')
    with open(path, 'wb') as handle:
        handle.write('ok\n'.encode('utf-8') + b'\xff\xfe' + 'ng\n'.encode('utf-8'))
    content = diff.read_text(path)
    assert content.binary is False
    assert 'ok' in content.text and 'ng' in content.text


# --------------------------------------------------------------------------
# 差分
# --------------------------------------------------------------------------

def test_identical_versions_report_no_change(tmp_path):
    a = _write(tmp_path / 'a.txt', 'same\n')
    b = _write(tmp_path / 'b.txt', 'same\n')
    result = diff.unified(a, b)
    assert result.identical is True
    assert result.text == ''


def test_changed_lines_appear_in_the_diff(tmp_path):
    a = _write(tmp_path / 'a.txt', 'one\ntwo\nthree\n')
    b = _write(tmp_path / 'b.txt', 'one\nTWO\nthree\n')
    result = diff.unified(a, b, before_label='#1', after_label='#2')
    assert result.identical is False
    assert '-two' in result.text
    assert '+TWO' in result.text
    assert '#1' in result.text and '#2' in result.text


def test_a_created_file_shows_as_all_added(tmp_path):
    """存在しなかった版との差分は、全行が追加として出る。"""
    b = _write(tmp_path / 'b.txt', 'new\n')
    result = diff.unified(None, b)
    assert '+new' in result.text


def test_a_deleted_file_shows_as_all_removed(tmp_path):
    a = _write(tmp_path / 'a.txt', 'gone\n')
    result = diff.unified(a, None)
    assert '-gone' in result.text


def test_binary_versions_are_reported_not_diffed(tmp_path):
    a = str(tmp_path / 'a.bin')
    with open(a, 'wb') as handle:
        handle.write(b'\x00\x01')
    b = _write(tmp_path / 'b.txt', 'text\n')
    result = diff.unified(a, b)
    assert result.binary is True
    assert result.text == ''


def test_huge_diffs_are_cut_short(tmp_path):
    """全面的に書き換わった巨大ファイルでブラウザを詰まらせない。"""
    a = _write(tmp_path / 'a.txt', ''.join('a{0}\n'.format(i) for i in range(500)))
    b = _write(tmp_path / 'b.txt', ''.join('b{0}\n'.format(i) for i in range(500)))
    result = diff.unified(a, b, max_lines=20)
    assert result.truncated is True
    assert len(result.text.splitlines()) <= 20


def test_label_for_falls_back(tmp_path):
    assert diff.label_for(str(tmp_path / 'notes.md'), 'x') == 'notes.md'
    assert diff.label_for(None, 'missing') == 'missing'
    assert os.path.basename(diff.label_for('/a/b/c.txt', 'x')) == 'c.txt'
