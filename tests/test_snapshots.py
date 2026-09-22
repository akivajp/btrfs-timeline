#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スナップショットのレイアウト検出のテスト。"""

import datetime
import os

from btrfs_timeline.core import mounts, snapshots

INFO_XML = '''<?xml version="1.0"?>
<snapshot>
  <type>single</type>
  <num>{num}</num>
  <date>{date}</date>
  <description>{description}</description>
</snapshot>
'''


def _make_mount(path):
    """テスト用の MountPoint を組み立てる。"""
    return mounts.MountPoint(
        mount_point=str(path), fs_type='btrfs', device='/dev/test',
        subvol='/@home', root='/@home',
    )


def test_detects_snapper_layout(tmp_path):
    """``<N>/snapshot`` + ``info.xml`` を snapper レイアウトとして検出する。"""
    base = tmp_path / '.snapshots'
    for num, date in ((1, '2026-01-01 00:00:00'), (2, '2026-02-01 12:30:00')):
        entry = base / str(num)
        (entry / 'snapshot').mkdir(parents=True)
        (entry / 'info.xml').write_text(
            INFO_XML.format(num=num, date=date, description='timeline'), encoding='utf-8')

    found = snapshots.discover(_make_mount(tmp_path))
    assert [s.id for s in found] == ['1', '2']
    assert all(s.layout == 'snapper' for s in found)
    assert found[0].description == 'timeline'
    assert found[0].root == str(base / '1' / 'snapshot')


def test_snapper_date_is_treated_as_utc(tmp_path):
    """snapper の info.xml の日時はタイムゾーン無しで書かれるが UTC である。

    ローカル時刻として読むと時差の分だけずれるため、ここを取り違えないようにする。
    """
    entry = tmp_path / '.snapshots' / '10'
    (entry / 'snapshot').mkdir(parents=True)
    (entry / 'info.xml').write_text(
        INFO_XML.format(num=10, date='2026-09-22 16:00:00', description=''), encoding='utf-8')

    found = snapshots.discover(_make_mount(tmp_path))
    assert found[0].taken_at == datetime.datetime(
        2026, 9, 22, 16, 0, 0, tzinfo=datetime.timezone.utc)


def test_detects_flat_layout(tmp_path):
    """``<name>`` が直接内容を持つ形 (btrbk 等) も検出する。"""
    base = tmp_path / '.snapshots'
    (base / 'home.20260919T190100').mkdir(parents=True)
    found = snapshots.discover(_make_mount(tmp_path))
    assert len(found) == 1
    assert found[0].layout == 'flat'
    assert found[0].taken_at == datetime.datetime(
        2026, 9, 19, 19, 1, 0, tzinfo=datetime.timezone.utc)


def test_broken_info_xml_falls_back_to_mtime(tmp_path):
    """info.xml が壊れていても、ディレクトリの mtime で日時を補う。"""
    entry = tmp_path / '.snapshots' / '7'
    (entry / 'snapshot').mkdir(parents=True)
    (entry / 'info.xml').write_text('<not-xml', encoding='utf-8')
    found = snapshots.discover(_make_mount(tmp_path))
    assert len(found) == 1
    assert found[0].taken_at is not None


def test_missing_snapshot_dir_returns_empty(tmp_path):
    """``.snapshots`` が無ければ空を返す (例外にしない)。"""
    assert snapshots.discover(_make_mount(tmp_path)) == []
