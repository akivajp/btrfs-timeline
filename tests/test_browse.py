#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ディレクトリ閲覧のテスト。

ここで最も重要なのは ``--root`` による閉じ込めである。Web UI はファイルの中身まで
返すため、閉じ込めが破れると「公開したつもりのない領域が読める」ことになる。
"""

import os

import pytest

from btrfs_timeline.core import browse


def _tree(root):
    """テスト用のディレクトリ構成を作る。"""
    (root / 'documents').mkdir()
    (root / 'documents' / 'notes.md').write_text('notes', encoding='utf-8')
    (root / 'archive').mkdir()
    (root / 'readme.txt').write_text('hello', encoding='utf-8')
    (root / '.hidden').write_text('x', encoding='utf-8')


def test_directories_come_first_then_names(tmp_path):
    """ファイルマネージャの慣習に合わせ、ディレクトリを先に並べる。"""
    _tree(tmp_path)
    names = [e.name for e in browse.list_directory(str(tmp_path))]
    assert names == ['archive', 'documents', '.hidden', 'readme.txt']


def test_hidden_entries_can_be_omitted(tmp_path):
    _tree(tmp_path)
    names = [e.name for e in browse.list_directory(str(tmp_path), show_hidden=False)]
    assert '.hidden' not in names


def test_entry_carries_size_and_kind(tmp_path):
    _tree(tmp_path)
    entries = {e.name: e for e in browse.list_directory(str(tmp_path))}
    assert entries['readme.txt'].is_directory is False
    assert entries['readme.txt'].size == len('hello')
    assert entries['documents'].is_directory is True
    assert entries['readme.txt'].mtime is not None


def test_symlink_is_marked_but_still_openable(tmp_path):
    """リンクであることは示しつつ、ディレクトリとして辿れることも示す。"""
    _tree(tmp_path)
    os.symlink(str(tmp_path / 'documents'), str(tmp_path / 'link-to-documents'))
    entries = {e.name: e for e in browse.list_directory(str(tmp_path))}
    link = entries['link-to-documents']
    assert link.is_symlink is True
    assert link.is_directory is True


def test_broken_symlink_does_not_break_the_listing(tmp_path):
    """リンク切れがあっても一覧全体が失敗しない。"""
    os.symlink(str(tmp_path / 'nowhere'), str(tmp_path / 'dangling'))
    entries = {e.name: e for e in browse.list_directory(str(tmp_path))}
    assert entries['dangling'].is_symlink is True
    assert entries['dangling'].is_directory is False


def test_listing_a_file_is_rejected(tmp_path):
    _tree(tmp_path)
    with pytest.raises(NotADirectoryError):
        browse.list_directory(str(tmp_path / 'readme.txt'))


def test_missing_path_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        browse.list_directory(str(tmp_path / 'nowhere'))


# --------------------------------------------------------------------------
# --root による閉じ込め
# --------------------------------------------------------------------------

def test_inside_root_is_allowed(tmp_path):
    _tree(tmp_path)
    assert browse.is_within(str(tmp_path / 'documents'), str(tmp_path)) is True
    assert browse.is_within(str(tmp_path), str(tmp_path)) is True


def test_outside_root_is_refused(tmp_path):
    _tree(tmp_path)
    outside = str(tmp_path.parent)
    assert browse.is_within(outside, str(tmp_path)) is False
    with pytest.raises(PermissionError):
        browse.list_directory(outside, root=str(tmp_path))


def test_sibling_prefix_is_not_mistaken_for_a_child(tmp_path):
    """``/srv/data`` を root にしたとき ``/srv/data-backup`` は外側である。

    単純な文字列の前方一致で判定すると、ここを取り違える。
    """
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data-backup').mkdir()
    assert browse.is_within(str(tmp_path / 'data-backup'), str(tmp_path / 'data')) is False


def test_symlink_cannot_escape_the_root(tmp_path):
    """root の中に外を指すリンクを置いても、その先には出られない。

    ``realpath`` で突き合わせないとここが抜け道になる。
    """
    inside = tmp_path / 'inside'
    inside.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.txt').write_text('secret', encoding='utf-8')
    os.symlink(str(outside), str(inside / 'escape'))

    with pytest.raises(PermissionError):
        browse.list_directory(str(inside / 'escape'), root=str(inside))


# --------------------------------------------------------------------------
# パンくず
# --------------------------------------------------------------------------

def test_parents_are_returned_shallowest_first(tmp_path):
    _tree(tmp_path)
    chain = browse.parents(str(tmp_path / 'documents'))
    assert chain[0] == '/'
    assert chain[-1] == str(tmp_path / 'documents')


def test_parents_stop_at_the_root(tmp_path):
    """閉じ込めているときは、その外側をパンくずに出さない。"""
    _tree(tmp_path)
    chain = browse.parents(str(tmp_path / 'documents'), root=str(tmp_path))
    assert chain == [str(tmp_path), str(tmp_path / 'documents')]
