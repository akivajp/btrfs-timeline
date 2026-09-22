#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""過去の版の復元。

設計上の原則は 3 つある。

1. **既定では元のファイルを上書きしない。**
   復元先は ``notes.20260919T1901.md`` のような兄弟ファイルにする
   (KDE の dolphin-btrfs-snapshots と同じ流儀)。履歴ツールで最悪の事故は
   「戻したつもりが、まだ必要だった現在の内容を消していた」であり、
   それを既定動作から構造的に排除する。``in_place`` を明示した場合だけ上書きし、
   そのときも現在の内容を自動で退避する。

2. **コピーではなく reflink (``FICLONE`` ioctl) を第一手段にする。**
   同一 btrfs 内ならエクステントを共有するだけなので、ファイルサイズに関わらず
   一瞬で終わり、追加の容量も消費しない。別ファイルシステムを指定された場合などは
   通常のコピーに落とすが、**どちらで行ったかは必ず結果に残す**
   (容量が倍増したのかどうかは利用者にとって意味が違うため)。

3. **書き込みは一時ファイル + ``os.replace`` で原子的に行う。**
   上書き中に中断しても、中途半端な内容のファイルが残らないようにする。
"""

from __future__ import annotations

import datetime
import fcntl
import os
import shutil
import stat
from typing import NamedTuple, Optional

# FICLONE = _IOW(0x94, 9, int)。エクステントを共有する形でファイル全体を複製する。
# btrfs / XFS (reflink 有効時) などで使える。同一ファイルシステム内であることが条件。
FICLONE = 0x40049409

# 兄弟ファイル名に埋め込む日時の書式 (表示と同じくローカル時刻で埋める)
TIMESTAMP_FORMAT = '%Y%m%dT%H%M%S'

# 上書き前の退避ファイルに付けるラベル
BACKUP_LABEL = 'before-restore'


class RestorePlan(NamedTuple):
    """実行前に確定させた復元の計画。

    実行と計画を分けているのは、``--dry-run`` と JSON 出力で
    「何が起きるのか」を副作用なしに提示できるようにするため。
    """

    source: str
    """復元元 (スナップショット内の実パス)"""

    destination: str
    """復元先の実パス"""

    in_place: bool
    """現在のファイルを上書きするか"""

    backup: Optional[str]
    """上書き前に現在の内容を退避する先。退避しない場合は None"""

    is_symlink: bool
    """復元元がシンボリックリンクそのものか"""

    size: Optional[int]
    """復元元のバイト数 (シンボリックリンクならリンク文字列長)"""


class RestoreResult(NamedTuple):
    """復元の実行結果。"""

    plan: RestorePlan
    """実行した計画"""

    method: str
    """実際の複製方法 (``reflink`` / ``copy`` / ``symlink`` / ``dry-run``)"""

    dry_run: bool
    """実際には何も書き込んでいないか"""


def _stamp(moment: Optional[datetime.datetime] = None) -> str:
    """日時を兄弟ファイル名用の文字列にする。

    UTC のまま埋めると利用者が自分のファイル名を読めないので、
    表示系と同じくローカル時刻に直してから整形する。
    """
    moment = moment or datetime.datetime.now(datetime.timezone.utc)
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime(TIMESTAMP_FORMAT)


def sibling_path(path: str, moment: Optional[datetime.datetime] = None,
                 label: Optional[str] = None) -> str:
    """``path`` の隣に作る、衝突しないファイル名を返す。

    拡張子は末尾に残す (``notes.md`` → ``notes.20260919T1901.md``)。
    エディタやビューアが拡張子で種別を判断するため、
    末尾に日時を付けて ``notes.md.20260919T1901`` にすると開けなくなることがある。
    """
    directory, name = os.path.split(path)
    root, ext = os.path.splitext(name)
    parts = [root]
    if label:
        parts.append(label)
    parts.append(_stamp(moment))
    base = '.'.join(parts)

    candidate = os.path.join(directory, base + ext)
    counter = 1
    # 同じ秒に 2 回復元した場合などに備えて連番で回避する
    while os.path.lexists(candidate):
        candidate = os.path.join(directory, '{0}-{1}{2}'.format(base, counter, ext))
        counter += 1
    return candidate


def _temporary_path(destination: str) -> str:
    """``destination`` と同じディレクトリに作る一時ファイルのパス。

    ``os.replace`` で原子的に差し替えるには、同じファイルシステム上
    (= 同じディレクトリ) に置く必要がある。
    """
    directory, name = os.path.split(destination)
    counter = 0
    while True:
        suffix = '' if counter == 0 else '-{0}'.format(counter)
        candidate = os.path.join(
            directory, '.{0}.btrfs-timeline.{1}{2}.tmp'.format(name, os.getpid(), suffix))
        if not os.path.lexists(candidate):
            return candidate
        counter += 1


def _clone_contents(source: str, temporary: str) -> str:
    """通常ファイルの内容を ``temporary`` に複製し、使った方法を返す。

    まず ``FICLONE`` を試し、失敗した場合だけ通常のコピーに落とす。
    失敗の理由は様々 (別ファイルシステム=EXDEV、reflink 非対応=EOPNOTSUPP、
    カーネルが古い=EINVAL 等) だが、いずれも「コピーすれば達成できる」点では同じなので
    まとめて拾う。
    """
    with open(source, 'rb') as src, open(temporary, 'xb') as dst:
        try:
            fcntl.ioctl(dst.fileno(), FICLONE, src.fileno())
            return 'reflink'
        except OSError:
            # FICLONE が失敗した時点では何も書かれていないはずだが、
            # 中途半端な状態から書き足さないよう明示的に空に戻してからコピーする
            dst.seek(0)
            dst.truncate(0)
            shutil.copyfileobj(src, dst)
            return 'copy'


def _copy_metadata(source: str, temporary: str) -> None:
    """パーミッションと mtime を復元元から引き継ぐ。

    mtime を引き継ぐのは意図的である。復元後のライブファイルは
    「その版と同じ内容」なのだから、履歴上も同じ版として畳まれるのが正しい
    (この履歴ツールは mtime + size で版を判定している)。

    所有者は引き継がない。``chown`` は root を要するうえ、
    このツールは非特権で動かせることを前提にしているため。
    """
    info = os.lstat(source)
    os.chmod(temporary, stat.S_IMODE(info.st_mode))
    os.utime(temporary, (info.st_atime, info.st_mtime))


def clone_file(source: str, destination: str) -> str:
    """``source`` を ``destination`` に複製し、使った方法を返す。

    シンボリックリンクはリンクとして作り直す。**リンクを辿ってはいけない。**
    スナップショット内の絶対シンボリックリンクを辿ると、過去の版を復元したつもりで
    現在のファイルの内容を書き込むことになる。
    """
    temporary = _temporary_path(destination)
    try:
        if os.path.islink(source):
            os.symlink(os.readlink(source), temporary)
            method = 'symlink'
        else:
            method = _clone_contents(source, temporary)
            _copy_metadata(source, temporary)
        os.replace(temporary, destination)
    except BaseException:
        # 失敗時に一時ファイルを残さない (残ると次回の復元でゴミが見える)
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return method


def plan_restore(target: str, source: str,
                 moment: Optional[datetime.datetime] = None,
                 in_place: bool = False,
                 destination: Optional[str] = None,
                 backup: bool = True,
                 overwrite: bool = False) -> RestorePlan:
    """復元の計画を組み立てる (副作用なし)。

    Args:
        target: 現在のファイルのパス。復元先の既定位置を決めるのに使う。
        source: 復元元 (スナップショット内のパス)。
        moment: 兄弟ファイル名に埋める日時。通常はその版が観測された日時。
        in_place: True なら ``target`` を上書きする。
        destination: 復元先を明示する場合に指定する。
        backup: ``in_place`` のとき、上書き前に現在の内容を退避するか。
        overwrite: ``destination`` を明示したうえで既存ファイルを上書きしてよいか。

    Raises:
        FileNotFoundError: 復元元が存在しない。
        IsADirectoryError: 復元元がディレクトリ (現時点では非対応)。
        FileExistsError: 明示した復元先が既に存在し、``overwrite`` が False。
        ValueError: 復元元と復元先が同じ。
    """
    if not os.path.lexists(source):
        raise FileNotFoundError('復元元が見つかりません: {0}'.format(source))

    is_symlink = os.path.islink(source)
    if not is_symlink and os.path.isdir(source):
        raise IsADirectoryError(
            'ディレクトリの復元には未対応です (ファイルを指定してください): {0}'.format(source))

    if destination is not None:
        destination = os.path.abspath(destination)
        if os.path.lexists(destination) and not overwrite:
            raise FileExistsError('復元先が既に存在します: {0}'.format(destination))
    elif in_place:
        destination = target
    else:
        destination = sibling_path(target, moment)

    if os.path.abspath(source) == destination:
        raise ValueError('復元元と復元先が同じです: {0}'.format(destination))

    parent = os.path.dirname(destination) or '.'
    if not os.path.isdir(parent):
        raise FileNotFoundError('復元先のディレクトリがありません: {0}'.format(parent))

    # 退避が要るのは「上書きする」かつ「上書きされる中身が実在する」ときだけ。
    # 退避先の日時は版の日時ではなく現在時刻にする (退避したのは今の内容なので)。
    backup_path = None
    if in_place and backup and os.path.lexists(destination):
        backup_path = sibling_path(destination, None, label=BACKUP_LABEL)

    size = None
    try:
        size = os.lstat(source).st_size
    except OSError:
        pass

    return RestorePlan(
        source=source, destination=destination, in_place=bool(in_place),
        backup=backup_path, is_symlink=is_symlink, size=size,
    )


def execute(plan: RestorePlan, dry_run: bool = False) -> RestoreResult:
    """計画を実行する。

    退避 → 復元の順で行う。逆にすると、退避に失敗したときには既に
    元の内容が失われている。
    """
    if dry_run:
        return RestoreResult(plan=plan, method='dry-run', dry_run=True)

    if plan.backup:
        clone_file(plan.destination, plan.backup)

    method = clone_file(plan.source, plan.destination)
    return RestoreResult(plan=plan, method=method, dry_run=False)
