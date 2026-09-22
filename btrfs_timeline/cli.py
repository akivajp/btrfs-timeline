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

人間向けのメッセージは全て ``i18n`` のカタログから引く。``--json`` の出力は
**言語によって変わらない** (キーも値も機械向けなので翻訳しない)。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import unicodedata
from typing import Any, Optional

from . import __version__, i18n
from .core import history as history_module
from .core import mounts as mounts_module
from .core import restore as restore_module
from .core import snapshots as snapshots_module

_ = i18n.translate

# 履歴表の各列の幅 (端末上の表示桁数)。最後の STATE 列は行末なので幅を持たせない
HISTORY_COLUMNS = ((3, '>'), (20, '<'), (20, '<'), (10, '>'), (7, '>'))


def _display_width(text: str) -> int:
    """端末上で何桁を占めるかを返す。

    日本語などの全角文字は 1 文字で 2 桁を占めるため、``len()`` では桁揃えができない。
    ``'{0:<20}'`` のような書式指定も文字数で数えるので、翻訳した途端に表が崩れる。
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in text)


def _pad(text: str, width: int, align: str = '<') -> str:
    """表示桁数を基準に空白を詰める。"""
    padding = ' ' * max(0, width - _display_width(text))
    return padding + text if align == '>' else text + padding


def _row(values, columns=HISTORY_COLUMNS) -> str:
    """表の 1 行を組み立てる。最後の列は幅を指定しない (行末なので詰める必要が無い)。"""
    cells = [_pad(str(value), width, align) for value, (width, align) in zip(values, columns)]
    cells.extend(str(value) for value in values[len(columns):])
    return ' '.join(cells)


def _isoformat(value: Optional[datetime.datetime]) -> Optional[str]:
    """datetime を ISO 8601 文字列にする。None はそのまま None。"""
    return value.isoformat() if value is not None else None


def _human_size(size: Optional[int]) -> str:
    """バイト数を読みやすい単位に直す (1024 基準)。

    単位は翻訳しない。KiB / MiB は言語に依らず通用する表記であり、
    訳し分けると桁数の見積もりが狂うため。
    """
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


def _fail(key: str, **params) -> None:
    """エラーメッセージを標準エラー出力に書く。"""
    print(_('error.prefix', message=_(key, **params)), file=sys.stderr)


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
    except LookupError:
        _fail('error.no-btrfs-mount', path=target)
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
        print(_('history.empty', path=target))
        return 0

    print(target)
    print(_row([
        _('history.column.index'), _('history.column.first-seen'),
        _('history.column.last-seen'), _('history.column.size'),
        _('history.column.snapshots'), _('history.column.state'),
    ]))
    for index, version in numbered:
        if version.is_live:
            state = _('history.state.live' if version.exists else 'history.state.live-missing')
            first = last = '-'
        else:
            state = _('history.state.ok' if version.exists else 'history.state.missing')
            first = _local(version.first_seen)
            last = _local(version.last_seen)
        print(_row([
            index, first, last, _human_size(version.size),
            version.snapshot_count or '-', state,
        ]))
    return 0


def _resolve_restore_source(target, args, mount, snapshot_list):
    """復元元のパスと、その版の日時・表示用ラベルを決める。

    Returns:
        ``(source, moment, label)``。決められない場合は ``(None, None, 翻訳済みの理由)``。
    """
    if args.snapshot:
        snapshot = next((s for s in snapshot_list if s.id == args.snapshot), None)
        if snapshot is None:
            return None, None, _('error.snapshot-not-found', id=args.snapshot)
        relative = mounts_module.relative_to_mount(target, mount)
        source = os.path.join(snapshot.root, relative) if relative else snapshot.root
        return source, snapshot.taken_at, _(
            'restore.label.snapshot', id=snapshot.id, moment=_local(snapshot.taken_at))

    versions = history_module.list_versions(target, snapshot_list=snapshot_list, mount=mount)
    numbered = list(enumerate(versions, start=1))

    if args.index is not None:
        selected = [(i, v) for i, v in numbered if i == args.index]
        if not selected:
            return None, None, _('error.version-not-found', index=args.index)
        index, version = selected[0]
        if version.is_live:
            return None, None, _('error.live-not-restorable')
        if not version.exists:
            return None, None, _('error.version-missing', index=index)
    else:
        # 既定は「存在する最新のスナップショット版」= 直前の内容。
        # 履歴を見ずに実行されても、意図と一致する可能性が最も高い選択。
        candidates = [(i, v) for i, v in numbered if not v.is_live and v.exists]
        if not candidates:
            return None, None, _('error.not-in-snapshots')
        index, version = candidates[-1]

    return version.path, version.first_seen, _(
        'restore.label.version', index=index, moment=_local(version.first_seen))


def cmd_restore(args: argparse.Namespace) -> int:
    """過去の版を復元する。既定では元のファイルを上書きしない。"""
    if args.no_backup and not args.force:
        _fail('error.no-backup-requires-force')
        return 2

    target = os.path.realpath(os.path.abspath(args.path))
    mount = mounts_module.find_containing_mount(target)
    if mount is None:
        _fail('error.no-btrfs-mount', path=target)
        return 1
    snapshot_list = snapshots_module.discover(mount)

    source, moment, label = _resolve_restore_source(target, args, mount, snapshot_list)
    if source is None:
        # label には翻訳済みの理由が入っている
        print(_('error.prefix', message=label), file=sys.stderr)
        return 1

    try:
        plan = restore_module.plan_restore(
            target, source, moment=moment,
            in_place=args.in_place, destination=args.destination,
            backup=not args.no_backup, overwrite=args.force,
        )
        result = restore_module.execute(plan, dry_run=args.dry_run)
    except (OSError, ValueError) as error:
        print(_('error.prefix', message=error), file=sys.stderr)
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

    prefix = _('restore.dry-run-prefix') if args.dry_run else ''
    print(_('restore.source', prefix=prefix, path=plan.source))
    print(_('restore.selected', label=label))
    print(_('restore.destination', path=plan.destination))
    print(_('restore.backup', path=plan.backup or _('restore.backup.none')))
    print(_('restore.method', method=result.method))
    return 0


def cmd_snapshots(args: argparse.Namespace) -> int:
    """指定パスを含むマウントのスナップショット一覧を表示する。"""
    target = os.path.abspath(args.path)
    mount = mounts_module.find_containing_mount(target)
    if mount is None:
        _fail('error.no-btrfs-mount', path=target)
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

    print(_('snapshots.mount', mount=mount.mount_point, subvol=mount.subvol))
    print(_('snapshots.count', count=len(found)))
    for snapshot in found:
        print('  ' + _row(
            [snapshot.id, _local(snapshot.taken_at), snapshot.layout, snapshot.description],
            columns=((10, '<'), (20, '<'), (10, '<')),
        ))
    return 0


def cmd_mounts(args: argparse.Namespace) -> int:
    """btrfs のマウント一覧を表示する。"""
    found = mounts_module.btrfs_mounts()
    if args.json:
        _emit_json({'mounts': [m._asdict() for m in found]})
        return 0
    for mount in found:
        print(_row([mount.mount_point, mount.subvol or '-', mount.device],
                   columns=((30, '<'), (20, '<'))))
    return 0


def _add_language_option(parser: argparse.ArgumentParser) -> None:
    """``--lang`` を追加する。

    サブコマンドにも同じ option を持たせているのは、``restore ... --lang ja`` と
    後ろに書けるようにするため。グローバル option だけにすると
    サブコマンド名より前に置かねばならず、実際には間違えやすい。
    実際の言語決定は ``preselect_language`` が argv 全体を見て行うので、
    ここで受け取った値は使わない (``choices`` による妥当性検査のためだけにある)。
    """
    parser.add_argument('--lang', metavar='CODE', choices=i18n.available_languages(),
                        help=_('cli.option.lang'))


def build_parser() -> argparse.ArgumentParser:
    """引数パーサを構築する。

    ヘルプ文もカタログから引くため、この関数を呼ぶ時点で
    ``i18n`` の言語が決まっている必要がある (``main`` が先に設定する)。
    """
    parser = argparse.ArgumentParser(prog='btrfs-timeline', description=_('cli.description'))
    parser.add_argument('--version', action='version', version='%(prog)s {0}'.format(__version__))
    _add_language_option(parser)
    subparsers = parser.add_subparsers(dest='command')

    # --- history
    history_parser = subparsers.add_parser('history', help=_('history.command'))
    history_parser.add_argument('path', help=_('history.argument.path'))
    history_parser.add_argument('--json', action='store_true', help=_('cli.option.json'))
    history_parser.add_argument('--no-live', action='store_true',
                                help=_('history.option.no-live'))
    history_parser.add_argument('--omit-missing', action='store_true',
                                help=_('history.option.omit-missing'))
    _add_language_option(history_parser)
    history_parser.set_defaults(func=cmd_history)

    # --- restore
    restore_parser = subparsers.add_parser('restore', help=_('restore.command'))
    restore_parser.add_argument('path', help=_('restore.argument.path'))
    # 版の指定方法は 2 通りあるが、同時に指定されると意味が決まらない
    selector = restore_parser.add_mutually_exclusive_group()
    selector.add_argument('--index', type=int, metavar='N', help=_('restore.option.index'))
    selector.add_argument('--snapshot', metavar='ID', help=_('restore.option.snapshot'))
    # 復元先の指定方法も、同時に指定されると意味が決まらない
    destination_group = restore_parser.add_mutually_exclusive_group()
    destination_group.add_argument('--to', dest='destination', metavar='PATH',
                                   help=_('restore.option.to'))
    destination_group.add_argument('--in-place', action='store_true',
                                   help=_('restore.option.in-place'))
    restore_parser.add_argument('--no-backup', action='store_true',
                                help=_('restore.option.no-backup'))
    restore_parser.add_argument('--force', action='store_true', help=_('restore.option.force'))
    restore_parser.add_argument('--dry-run', action='store_true', help=_('restore.option.dry-run'))
    restore_parser.add_argument('--json', action='store_true', help=_('cli.option.json'))
    _add_language_option(restore_parser)
    restore_parser.set_defaults(func=cmd_restore)

    # --- snapshots
    snapshots_parser = subparsers.add_parser('snapshots', help=_('snapshots.command'))
    snapshots_parser.add_argument('path', nargs='?', default='.',
                                  help=_('snapshots.argument.path'))
    snapshots_parser.add_argument('--json', action='store_true', help=_('cli.option.json'))
    _add_language_option(snapshots_parser)
    snapshots_parser.set_defaults(func=cmd_snapshots)

    # --- mounts
    mounts_parser = subparsers.add_parser('mounts', help=_('mounts.command'))
    mounts_parser.add_argument('--json', action='store_true', help=_('cli.option.json'))
    _add_language_option(mounts_parser)
    mounts_parser.set_defaults(func=cmd_mounts)

    return parser


def preselect_language(argv=None) -> Optional[str]:
    """パーサを組み立てる前に ``--lang`` だけ先読みする。

    ヘルプ文もカタログから引くため、パーサの構築時点で言語が決まっている必要がある。
    argparse に解析させてからでは間に合わないので、ここだけ手で拾う。
    値の妥当性は後で argparse の ``choices`` が確認する。
    """
    argv = sys.argv[1:] if argv is None else list(argv)
    for position, item in enumerate(argv):
        if item == '--lang' and position + 1 < len(argv):
            return argv[position + 1]
        if item.startswith('--lang='):
            return item.split('=', 1)[1]
    return None


def main(argv=None) -> int:
    """エントリポイント。"""
    i18n.set_language(i18n.detect_language(preselect_language(argv)))
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, 'func', None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
