#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""コマンドラインインターフェース。

このモジュールは単なる「人間向けの入口」ではなく、
**フロントエンド間で共有される API 境界** として設計されている。

Cockpit モジュールにはサーバーサイドが存在せず、ブラウザ上の JavaScript から
``cockpit.spawn([...], {superuser: "require"})`` でプロセスを起動することしかできない。
そのためコア機能を複数のフロントエンドで共有する唯一の形が
「``--json`` で機械可読な出力を返す CLI」になる。全サブコマンドが ``--json`` を持つのは
そのためであり、出力形式を変える際は Cockpit 側との互換性を意識すること。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import Any, Optional

from . import __version__
from .core import history as history_module
from .core import mounts as mounts_module
from .core import snapshots as snapshots_module


def _isoformat(value: Optional[datetime.datetime]) -> Optional[str]:
    """datetime を ISO 8601 文字列にする。None はそのまま None。"""
    return value.isoformat() if value is not None else None


def _human_size(size: Optional[int]) -> str:
    """バイト数を読みやすい単位に直す (1024 基準)。"""
    if size is None:
        return '-'
    value = float(size)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024.0 or unit == 'TiB':
            return '{0:.0f} {1}'.format(value, unit) if unit == 'B' else '{0:.1f} {1}'.format(value, unit)
        value /= 1024.0
    return '{0:.1f} TiB'.format(value)


def _local(value: Optional[datetime.datetime]) -> str:
    """UTC の datetime をローカル時刻の文字列にする (表示用)。"""
    if value is None:
        return '-'
    return value.astimezone().strftime('%Y-%m-%d %H:%M:%S')


def _version_to_dict(version: history_module.Version) -> dict:
    """``Version`` を JSON 化できる辞書にする。"""
    return {
        'path': version.path,
        'size': version.size,
        'mtime': _isoformat(version.mtime),
        'exists': version.exists,
        'is_live': version.is_live,
        'first_snapshot_id': version.first_snapshot_id,
        'last_snapshot_id': version.last_snapshot_id,
        'first_seen': _isoformat(version.first_seen),
        'last_seen': _isoformat(version.last_seen),
        'snapshot_count': version.snapshot_count,
    }


def _emit_json(payload: Any) -> None:
    """JSON を標準出力に書き出す (機械可読出力の唯一の経路)。"""
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


def cmd_history(args: argparse.Namespace) -> int:
    """指定パスの版の一覧を表示する。"""
    target = os.path.abspath(args.path)
    try:
        versions = history_module.list_versions(target, include_live=not args.no_live)
    except LookupError as error:
        print('error: {0}'.format(error), file=sys.stderr)
        return 1

    if args.omit_missing:
        versions = [v for v in versions if v.exists]

    if args.json:
        _emit_json({'target': target, 'versions': [_version_to_dict(v) for v in versions]})
        return 0

    if not versions:
        print('この対象を含むスナップショットが見つかりませんでした: {0}'.format(target))
        return 0

    print('{0}'.format(target))
    print('{0:<20} {1:<20} {2:>10} {3:>6}  {4}'.format(
        'FIRST SEEN', 'LAST SEEN', 'SIZE', 'SNAPS', 'STATE'))
    for version in versions:
        if version.is_live:
            state = 'live' if version.exists else 'live (欠損)'
            first = last = '-'
        else:
            state = 'ok' if version.exists else '(存在しない)'
            first = _local(version.first_seen)
            last = _local(version.last_seen)
        print('{0:<20} {1:<20} {2:>10} {3:>6}  {4}'.format(
            first, last, _human_size(version.size), version.snapshot_count or '-', state))
    return 0


def cmd_snapshots(args: argparse.Namespace) -> int:
    """指定パスを含むマウントのスナップショット一覧を表示する。"""
    target = os.path.abspath(args.path)
    mount = mounts_module.find_containing_mount(target)
    if mount is None:
        print('error: btrfs のマウントが見つかりません: {0}'.format(target), file=sys.stderr)
        return 1
    found = snapshots_module.discover(mount)

    if args.json:
        _emit_json({
            'mount_point': mount.mount_point,
            'subvol': mount.subvol,
            'snapshots': [{
                'id': s.id, 'root': s.root, 'taken_at': _isoformat(s.taken_at),
                'description': s.description, 'layout': s.layout,
            } for s in found],
        })
        return 0

    print('マウント: {0} (subvol={1})'.format(mount.mount_point, mount.subvol))
    print('検出数: {0}'.format(len(found)))
    for snapshot in found:
        print('  {0:<10} {1:<20} {2:<10} {3}'.format(
            snapshot.id, _local(snapshot.taken_at), snapshot.layout, snapshot.description))
    return 0


def cmd_mounts(args: argparse.Namespace) -> int:
    """btrfs のマウント一覧を表示する。"""
    found = mounts_module.btrfs_mounts()
    if args.json:
        _emit_json({'mounts': [m._asdict() for m in found]})
        return 0
    for mount in found:
        print('{0:<30} {1:<20} {2}'.format(mount.mount_point, mount.subvol or '-', mount.device))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """引数パーサを構築する。"""
    parser = argparse.ArgumentParser(
        prog='btrfs-timeline',
        description='Browse and restore previous versions of files from btrfs snapshots.',
    )
    parser.add_argument('--version', action='version', version='%(prog)s {0}'.format(__version__))
    subparsers = parser.add_subparsers(dest='command')

    # --- history
    history_parser = subparsers.add_parser(
        'history', help='list the versions of a path found in snapshots')
    history_parser.add_argument('path', help='file or directory to inspect')
    history_parser.add_argument('--json', action='store_true', help='emit machine-readable JSON')
    history_parser.add_argument('--no-live', action='store_true',
                                help='omit the current (live) version')
    history_parser.add_argument('--omit-missing', action='store_true',
                                help='omit periods where the path did not exist')
    history_parser.set_defaults(func=cmd_history)

    # --- snapshots
    snapshots_parser = subparsers.add_parser(
        'snapshots', help='list snapshots that cover a path')
    snapshots_parser.add_argument('path', nargs='?', default='.', help='path to inspect')
    snapshots_parser.add_argument('--json', action='store_true', help='emit machine-readable JSON')
    snapshots_parser.set_defaults(func=cmd_snapshots)

    # --- mounts
    mounts_parser = subparsers.add_parser('mounts', help='list btrfs mounts')
    mounts_parser.add_argument('--json', action='store_true', help='emit machine-readable JSON')
    mounts_parser.set_defaults(func=cmd_mounts)

    return parser


def main(argv=None) -> int:
    """エントリポイント。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, 'func', None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
