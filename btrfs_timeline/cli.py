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
from .core import restore as restore_module
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


def _version_to_dict(version: history_module.Version, index: int) -> dict:
    """``Version`` を JSON 化できる辞書にする。

    ``index`` は ``history`` が表示する版番号であり、``restore --index`` が
    受け取る値でもある。フロントエンドはこの番号をそのまま復元の指定に使える。
    """
    return {
        'index': index,
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

    # 版番号は **絞り込みの前に** 振る。--omit-missing の有無で番号が動くと、
    # 表示された番号を restore --index に渡したときに別の版を指してしまう。
    numbered = list(enumerate(versions, start=1))
    if args.omit_missing:
        numbered = [(i, v) for i, v in numbered if v.exists]

    if args.json:
        _emit_json({
            'target': target,
            'versions': [_version_to_dict(v, i) for i, v in numbered],
        })
        return 0

    if not numbered:
        print('この対象を含むスナップショットが見つかりませんでした: {0}'.format(target))
        return 0

    print('{0}'.format(target))
    print('{0:>3} {1:<20} {2:<20} {3:>10} {4:>6}  {5}'.format(
        '#', 'FIRST SEEN', 'LAST SEEN', 'SIZE', 'SNAPS', 'STATE'))
    for index, version in numbered:
        if version.is_live:
            state = 'live' if version.exists else 'live (欠損)'
            first = last = '-'
        else:
            state = 'ok' if version.exists else '(存在しない)'
            first = _local(version.first_seen)
            last = _local(version.last_seen)
        print('{0:>3} {1:<20} {2:<20} {3:>10} {4:>6}  {5}'.format(
            index, first, last, _human_size(version.size),
            version.snapshot_count or '-', state))
    return 0


def _resolve_restore_source(target, args, mount, snapshot_list):
    """復元元のパスと、その版の日時・説明を決める。

    Returns:
        ``(source, moment, label)``。決められない場合は ``(None, None, 理由)``。
    """
    if args.snapshot:
        snapshot = next((s for s in snapshot_list if s.id == args.snapshot), None)
        if snapshot is None:
            return None, None, 'そのスナップショットが見つかりません: {0}'.format(args.snapshot)
        relative = mounts_module.relative_to_mount(target, mount)
        source = os.path.join(snapshot.root, relative) if relative else snapshot.root
        return source, snapshot.taken_at, 'スナップショット {0} ({1})'.format(
            snapshot.id, _local(snapshot.taken_at))

    versions = history_module.list_versions(target, snapshot_list=snapshot_list, mount=mount)
    numbered = list(enumerate(versions, start=1))

    if args.index is not None:
        selected = [(i, v) for i, v in numbered if i == args.index]
        if not selected:
            return None, None, 'その版番号は存在しません: {0}'.format(args.index)
        index, version = selected[0]
        if version.is_live:
            return None, None, '現在のファイル (live) は復元元に指定できません'
        if not version.exists:
            return None, None, '版 #{0} の時点ではファイルが存在しません'.format(index)
    else:
        # 既定は「存在する最新のスナップショット版」= 直前の内容。
        # 履歴を見ずに実行されても、意図と一致する可能性が最も高い選択。
        candidates = [(i, v) for i, v in numbered if not v.is_live and v.exists]
        if not candidates:
            return None, None, 'スナップショット内にこのファイルが見つかりませんでした'
        index, version = candidates[-1]

    return version.path, version.first_seen, '版 #{0} ({1})'.format(
        index, _local(version.first_seen))


def cmd_restore(args: argparse.Namespace) -> int:
    """過去の版を復元する。既定では元のファイルを上書きしない。"""
    if args.no_backup and not args.force:
        print('error: --no-backup は --force と併せて指定してください '
              '(上書き前の退避を捨てる操作のため)', file=sys.stderr)
        return 2

    target = os.path.realpath(os.path.abspath(args.path))
    mount = mounts_module.find_containing_mount(target)
    if mount is None:
        print('error: btrfs のマウントが見つかりません: {0}'.format(target), file=sys.stderr)
        return 1
    snapshot_list = snapshots_module.discover(mount)

    source, moment, label = _resolve_restore_source(target, args, mount, snapshot_list)
    if source is None:
        print('error: {0}'.format(label), file=sys.stderr)
        return 1

    try:
        plan = restore_module.plan_restore(
            target, source, moment=moment,
            in_place=args.in_place, destination=args.destination,
            backup=not args.no_backup, overwrite=args.force,
        )
        result = restore_module.execute(plan, dry_run=args.dry_run)
    except (OSError, ValueError) as error:
        print('error: {0}'.format(error), file=sys.stderr)
        return 1

    if args.json:
        _emit_json({
            'target': target,
            'source': plan.source,
            'destination': plan.destination,
            'in_place': plan.in_place,
            'backup': plan.backup,
            'is_symlink': plan.is_symlink,
            'size': plan.size,
            'method': result.method,
            'dry_run': result.dry_run,
        })
        return 0

    print('{0}復元元: {1}'.format('[dry-run] ' if args.dry_run else '', plan.source))
    print('  選択: {0}'.format(label))
    print('  復元先: {0}'.format(plan.destination))
    print('  退避: {0}'.format(plan.backup or '(なし)'))
    print('  方法: {0}'.format(result.method))
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

    # --- restore
    restore_parser = subparsers.add_parser(
        'restore', help='restore a previous version of a file')
    restore_parser.add_argument('path', help='file to restore')
    # 版の指定方法は 2 通りあるが、同時に指定されると意味が決まらない
    selector = restore_parser.add_mutually_exclusive_group()
    selector.add_argument('--index', type=int, metavar='N',
                          help='version number as listed by "history" '
                               '(default: the newest version found in snapshots)')
    selector.add_argument('--snapshot', metavar='ID',
                          help='restore the file as it was in this snapshot')
    # 復元先の指定方法も、同時に指定されると意味が決まらない
    destination_group = restore_parser.add_mutually_exclusive_group()
    destination_group.add_argument('--to', dest='destination', metavar='PATH',
                                   help='write here instead of next to the original file')
    destination_group.add_argument('--in-place', action='store_true',
                                   help='overwrite the current file (the current content is '
                                        'kept as a backup unless --no-backup is given)')
    restore_parser.add_argument('--no-backup', action='store_true',
                                help='with --in-place, keep no backup (requires --force)')
    restore_parser.add_argument('--force', action='store_true',
                                help='allow --no-backup, and let --to overwrite an existing file')
    restore_parser.add_argument('--dry-run', action='store_true',
                                help='report what would happen and change nothing')
    restore_parser.add_argument('--json', action='store_true',
                                help='emit machine-readable JSON')
    restore_parser.set_defaults(func=cmd_restore)

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
