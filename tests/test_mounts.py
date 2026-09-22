#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""マウント情報の解析のテスト。"""

from btrfs_timeline.core import mounts

# 実環境から採取した mountinfo の抜粋 (オプションフィールドの個数が行ごとに違う点が重要)
SAMPLE = '''\
25 30 0:22 / /proc rw,nosuid,relatime shared:5 - proc proc rw
30 1 0:32 /@ / rw,relatime shared:1 - btrfs /dev/nvme3n1p1 rw,ssd,subvol=/@
36 30 0:32 /@home /home rw,relatime shared:28 - btrfs /dev/nvme3n1p1 rw,ssd,subvol=/@home
42 30 0:32 /@snapshots/home /home/.snapshots rw,relatime shared:31 - btrfs /dev/nvme3n1p1 rw,subvol=/@snapshots/home
48 30 0:32 /@var_log /var/log rw,relatime - btrfs /dev/nvme3n1p1 rw,ssd,subvol=/@var_log
'''


def test_parses_only_valid_lines():
    """proc など btrfs 以外も読めるが、btrfs だけを抽出できる。"""
    parsed = mounts.parse_mountinfo(SAMPLE)
    assert len(parsed) == 5
    assert len(mounts.btrfs_mounts(parsed)) == 4


def test_handles_variable_optional_fields():
    """オプションフィールドが無い行 (``shared:`` 無し) も正しく読める。"""
    parsed = mounts.parse_mountinfo(SAMPLE)
    var_log = [m for m in parsed if m.mount_point == '/var/log'][0]
    assert var_log.fs_type == 'btrfs'
    assert var_log.subvol == '/@var_log'


def test_subvol_is_extracted():
    """``subvol=`` を取り出せる。"""
    parsed = mounts.parse_mountinfo(SAMPLE)
    home = [m for m in parsed if m.mount_point == '/home'][0]
    assert home.subvol == '/@home'
    assert home.device == '/dev/nvme3n1p1'


def test_find_containing_mount_prefers_longest_match(tmp_path):
    """``/`` と ``/home`` の両方に含まれる場合、深い方 (``/home``) を選ぶ。"""
    parsed = mounts.parse_mountinfo(SAMPLE)
    found = mounts.find_containing_mount('/home/someone/file.txt', parsed)
    assert found is not None
    assert found.mount_point == '/home'


def test_find_containing_mount_does_not_match_prefix_only():
    """``/homework`` が ``/home`` にマッチしてしまわないこと。"""
    parsed = mounts.parse_mountinfo(SAMPLE)
    found = mounts.find_containing_mount('/homework/file.txt', parsed)
    assert found is not None
    assert found.mount_point == '/'


def test_unescape_octal_in_path():
    """空白を含むマウントポイントが 8 進エスケープから復元される。"""
    sample = '30 1 0:32 /@ /mnt/my\\040disk rw,relatime - btrfs /dev/sda1 rw,subvol=/@\n'
    parsed = mounts.parse_mountinfo(sample)
    assert parsed[0].mount_point == '/mnt/my disk'
