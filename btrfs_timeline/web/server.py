#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スタンドアロン Web UI のサーバー。

画面はブラウザ側で組み立てる (``static/app.js``)。サーバーが HTML を組み立てないのは
**Cockpit モジュールにはサーバーが存在しない** ためで、HTML にサーバー側のデータを
埋め込んでしまうと、同じ画面を Cockpit 版で再利用できなくなる。
データの取得経路だけを ``static/transport.js`` に閉じ込めてあり、Cockpit 版では
そのファイルだけを ``cockpit.spawn`` 版に差し替える。

JSON の形は ``cli`` が持つものをそのまま使う。各フロントエンドで組み立て直すと、
「CLI の ``--json`` が公開契約」という前提が崩れるため。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .. import i18n
from ..cli import (config_payload, entry_to_dict, filesystem_to_dict,
                   scrub_to_dict, snapshot_to_dict, version_to_dict)
from ..core import browse as browse_module
from ..core import devices as devices_module
from ..core import diff as diff_module
from ..core import maintenance as maintenance_module
from ..core import operations as operations_module
from ..core import history as history_module
from ..core import mounts as mounts_module
from ..core import restore as restore_module
from ..core import snapshots as snapshots_module
from .auth import Credentials, is_same_origin, make_auth_plugin

logger = logging.getLogger(__name__)

STATIC_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8088

#: プレビューで読み出す上限。これを超える分は切り捨てる。
#: 履歴の確認に必要なのは冒頭だけであり、巨大なファイルを丸ごとブラウザに
#: 送ってもメモリを食うだけで役に立たない。
DEFAULT_PREVIEW_LIMIT = 256 * 1024


class Settings:
    """サーバーの動作設定。"""

    def __init__(self, root: Optional[str] = None, read_only: bool = False,
                 preview_limit: int = DEFAULT_PREVIEW_LIMIT) -> None:
        """設定を初期化する。

        Args:
            root: 閲覧を許すディレクトリ。None なら制限しない。
            read_only: True なら復元 API を無効にする。
            preview_limit: プレビューで読み出す最大バイト数。
        """
        self.root = os.path.realpath(root) if root else None
        self.read_only = read_only
        self.preview_limit = preview_limit


def create_app(settings: Settings, credentials: Optional[Credentials] = None):
    """bottle アプリケーションを組み立てて返す。

    ``bottle`` はこの関数の中で import する。CLI 本体は依存ゼロで動かしたいので、
    モジュールの読み込み時点では必要にならないようにしてある。
    """
    import bottle

    app = bottle.Bottle()
    app.install(make_auth_plugin(credentials, bottle))

    def fail(status: int, message) -> 'bottle.HTTPResponse':
        """JSON のエラー応答を作る。"""
        return bottle.HTTPResponse(
            status=status,
            body=json.dumps({'error': str(message)}, ensure_ascii=False),
            content_type='application/json; charset=utf-8',
        )

    def checked_path(raw: Optional[str]) -> str:
        """クエリで渡されたパスを検証して絶対パスにする。

        ``--root`` の外を指していれば弾く。**全ての入口でこれを通すこと。**
        1 つでも通し忘れると閉じ込めが成立しない。
        """
        if not raw:
            raise ValueError(i18n.translate('error.path-required'))
        path = os.path.abspath(os.path.expanduser(raw))
        browse_module.ensure_within(path, settings.root)
        return path

    def require_same_origin() -> None:
        """書き込み系のリクエストがクロスサイトでないことを確かめる。"""
        if not is_same_origin(bottle.request.get_header('Origin'),
                              bottle.request.get_header('Host')):
            raise fail(403, i18n.translate('error.cross-origin'))

    # ------------------------------------------------------------------
    # 画面と静的ファイル
    # ------------------------------------------------------------------

    def asset(name):
        """静的ファイルを、**毎回検証させる** 形で返す。

        ``Cache-Control: no-cache`` は「キャッシュするな」ではなく
        「使う前に必ず問い合わせろ」という意味で、変わっていなければ 304 が返る。

        これを付けないと、ブラウザは HTML だけを取り直して ``app.js`` は
        ヒューリスティックに古いまま使う (ナビゲーションは再検証するが、
        サブリソースはしない)。その結果 **新しい画面に古いコードが載る** という、
        一番分かりにくい壊れ方をする。更新のたびにスーパーリロードを
        要求することになるので、ここで断つ。
        """
        response = bottle.static_file(name, root=STATIC_DIRECTORY)
        response.set_header('Cache-Control', 'no-cache')
        return response

    @app.route('/')
    def index():
        """画面本体。サーバー側では一切書き換えない (Cockpit 版と同じファイル)。"""
        return asset('index.html')

    # index.html は ``./app.js`` のように相対パスで参照する。Cockpit モジュールでは
    # 同じディレクトリに並ぶので、そちらでも同じ HTML がそのまま動く。
    # そのためルート直下でも配信する必要がある。
    # index.html も名前で配れるようにする。Cockpit には「ディレクトリ」が無く
    # ``./`` は解決できないため、ページ間のリンクは実ファイル名を指す
    for name in ('app.js', 'devices.js', 'transport.js', 'i18n.js', 'pages.js',
                 'style.css', 'index.html', 'devices.html'):
        app.route('/' + name, callback=(lambda target=name: asset(target)))

    @app.route('/static/<path:path>')
    def static_files(path):
        return asset(path)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    @app.route('/api/config')
    def api_config():
        """起動時にブラウザが必要とするものを 1 回でまとめて返す。

        翻訳カタログもここに含める。CLI と同じ JSON カタログをそのまま使えるのが、
        gettext ではなく JSON を選んだ理由そのものである。
        """
        # 組み立ては cli が持っている。Cockpit モジュールは同じものを
        # ``btrfs-timeline config --json`` として受け取るので、ここで作り直さない
        return config_payload(
            bottle.request.query.get('lang') or None,
            read_only=settings.read_only, root=settings.root)

    @app.route('/api/mounts')
    def api_mounts():
        return {'mounts': [m._asdict() for m in mounts_module.btrfs_mounts()]}

    @app.route('/api/browse')
    def api_browse():
        """ディレクトリの内容を返す。

        ``snapshot`` を指定すると **その時点の内容** を返す。現在は削除されている
        項目もそこに現れる (``exists_now`` が False)。現在の一覧だけを見ていても
        削除されたものには辿り着けないため、これが唯一の入口になる。
        """
        snapshot_id = bottle.request.query.get('snapshot') or None
        # 既定は「隠さない」(コアの既定に合わせる)。画面側は毎回明示して渡す
        show_hidden = bottle.request.query.get('show_hidden', '1') != '0'
        try:
            path = checked_path(bottle.request.query.get('path'))
            snapshot = None
            if snapshot_id:
                mount = mounts_module.find_containing_mount(path)
                if mount is None:
                    return fail(404, i18n.translate('error.no-btrfs-mount', path=path))
                snapshot = next(
                    (s for s in snapshots_module.discover(mount) if s.id == snapshot_id),
                    None)
                if snapshot is None:
                    return fail(404, i18n.translate('error.snapshot-not-found',
                                                    id=snapshot_id))
                entries = browse_module.list_directory_at(
                    path, snapshot, mount=mount, show_hidden=show_hidden,
                    root=settings.root)
            else:
                entries = browse_module.list_directory(
                    path, show_hidden=show_hidden, root=settings.root)
        except PermissionError as error:
            return fail(403, error)
        except (FileNotFoundError, NotADirectoryError) as error:
            return fail(404, error)
        except (OSError, ValueError) as error:
            return fail(400, error)
        return {
            'path': path,
            'parents': browse_module.parents(path, root=settings.root),
            'entries': [entry_to_dict(e) for e in entries],
            'snapshot': snapshot_to_dict(snapshot) if snapshot else None,
        }

    @app.route('/api/snapshots')
    def api_snapshots():
        try:
            path = checked_path(bottle.request.query.get('path'))
        except (PermissionError, ValueError) as error:
            return fail(403 if isinstance(error, PermissionError) else 400, error)
        mount = mounts_module.find_containing_mount(path)
        if mount is None:
            return fail(404, i18n.translate('error.no-btrfs-mount', path=path))
        found = snapshots_module.discover(mount)
        return {
            'mount_point': mount.mount_point, 'subvol': mount.subvol,
            'snapshots': [snapshot_to_dict(s) for s in found],
        }

    @app.route('/api/history')
    def api_history():
        try:
            path = checked_path(bottle.request.query.get('path'))
            versions = history_module.list_versions(path)
        except PermissionError as error:
            return fail(403, error)
        except LookupError as error:
            return fail(404, error)
        except (OSError, ValueError) as error:
            return fail(400, error)
        return {
            'target': path,
            'versions': [version_to_dict(v, i) for i, v in enumerate(versions, start=1)],
        }

    @app.route('/api/preview')
    def api_preview():
        """ある版の中身を返す。どれを戻すかは中身を見ないと決められない。"""
        try:
            path = checked_path(bottle.request.query.get('path'))
            index = bottle.request.query.get('index')
            versions = history_module.list_versions(path)
            number, version = _pick(versions, index)
        except PermissionError as error:
            return fail(403, error)
        except LookupError as error:
            return fail(404, error)
        except (OSError, ValueError) as error:
            return fail(400, error)

        content = diff_module.read_text(version.path, settings.preview_limit)
        return {
            'path': version.path,
            'index': number,
            'size': version.size,
            'binary': content.binary,
            'truncated': content.truncated,
            'text': content.text,
        }

    @app.route('/api/diff')
    def api_diff():
        """2 つの版の差分を返す。

        ``to`` を省略すると現在のファイル (live 版) と比べる。似た内容が並ぶ中から
        戻す版を選ぶとき、知りたいのは中身そのものより **何が変わったか** である。
        """
        try:
            path = checked_path(bottle.request.query.get('path'))
            versions = history_module.list_versions(path)
            before_index, before = _pick_any(versions, bottle.request.query.get('from'))
            after_index, after = _pick_any(versions, bottle.request.query.get('to'),
                                           default_live=True)
        except PermissionError as error:
            return fail(403, error)
        except LookupError as error:
            return fail(404, error)
        except (OSError, ValueError) as error:
            return fail(400, error)

        result = diff_module.unified(
            before.path, after.path,
            before_label='#{0}'.format(before_index),
            after_label='#{0}'.format(after_index),
            limit=settings.preview_limit,
        )
        return {
            'path': path,
            'from': before_index,
            'to': after_index,
            'text': result.text,
            'identical': result.identical,
            'binary': result.binary,
            'truncated': result.truncated,
        }

    @app.route('/api/devices')
    def api_devices():
        """ファイルシステムとデバイスの状態。

        **読むだけである。** scrub や balance の開始は root を要するが、この
        サーバーは利用者の権限で動くので、ここから実行できるようにはしない。
        代わりに「端末で実行すべきコマンド」をその危険度とともに返す。
        押しても必ず失敗するボタンを置くより、そのほうが役に立つ。
        """
        found = devices_module.discover()
        return {
            'filesystems': [filesystem_to_dict(f) for f in found],
            # 画面が「これを端末で実行してください」と示すための材料
            'suggestions': _suggestions(found),
        }

    @app.route('/api/scrub')
    def api_scrub():
        """scrub の状態。これだけは非特権で読めるので、画面に出せる。"""
        path = bottle.request.query.get('path') or ''
        if not path:
            return fail(400, i18n.translate('error.path-required'))
        operation = maintenance_module.scrub_status_operation(path)
        try:
            result = operations_module.run(operation, timeout=10)
        except OSError as error:
            return fail(500, error)
        payload = scrub_to_dict(maintenance_module.parse_scrub_status(result.stdout))
        payload['path'] = path
        payload['command'] = operation.to_dict()
        payload['ok'] = result.ok
        if not result.ok:
            payload['error'] = result.stderr.strip()
        return payload

    @app.route('/api/restore', method='POST')
    def api_restore():
        """過去の版を復元する。

        既定は CLI と同じく非破壊 (兄弟ファイルに書き出す)。``in_place`` を
        明示したときだけ上書きし、そのときも必ず退避を取る
        (``--no-backup`` に相当する指定は HTTP 越しには **公開しない**)。
        """
        if settings.read_only:
            return fail(403, i18n.translate('error.read-only'))
        require_same_origin()

        try:
            payload = bottle.request.json or {}
        except ValueError:
            return fail(400, i18n.translate('error.bad-request'))

        try:
            path = checked_path(payload.get('path'))
            versions = history_module.list_versions(path)
            number, version = _pick(versions, payload.get('index'))
            # 書き込み先は **必ず realpath で解決してから** 決める。CLI も同じ。
            # シンボリックリンクのまま in_place で上書きすると、リンクそのものを
            # 通常ファイルで置き換えてしまい、dotfiles 運用を壊す。
            # 非破壊の場合も、実体のある場所に兄弟ファイルを作る方が辿りやすい。
            resolved = os.path.realpath(path)
            browse_module.ensure_within(resolved, settings.root)
            plan = restore_module.plan_restore(
                resolved, version.path, moment=version.first_seen,
                in_place=bool(payload.get('in_place')),
            )
            result = restore_module.execute(plan, dry_run=bool(payload.get('dry_run')))
        except PermissionError as error:
            return fail(403, error)
        except LookupError as error:
            return fail(404, error)
        except (OSError, ValueError) as error:
            return fail(400, error)

        return {
            'target': path,
            'index': number,
            'source': plan.source,
            'destination': plan.destination,
            'in_place': plan.in_place,
            'backup': plan.backup,
            'is_symlink': plan.is_symlink,
            'size': plan.size,
            'method': result.method,
            'dry_run': result.dry_run,
        }

    return app


def _pick(versions, index):
    """リクエストで指定された版番号を解釈して版を選ぶ。

    選択そのものはコアの ``select_version`` が行う。CLI と同じ規則で選ばれることが
    重要で、ここで独自に選ぶと「CLI で確認してから Web で戻す」が成立しなくなる。
    """
    if index in (None, ''):
        return history_module.select_version(versions, None)
    try:
        number = int(index)
    except (TypeError, ValueError):
        raise ValueError(i18n.translate('error.version-not-found', index=index))
    return history_module.select_version(versions, number)


def _suggestions(filesystems) -> list:
    """端末で実行してもらう想定のコマンドを組み立てる。

    ``smartctl`` は **エラーが記録されているデバイスにだけ** 出す。
    何もかも並べると、本当に見るべきものが埋もれる。
    """
    found = []
    for filesystem in filesystems:
        for mount in filesystem.mount_points[:1]:
            # デバイスごとの内訳は root でしか取れない。画面に出せない以上、
            # 「これを叩けば分かる」と示すのが次善になる
            found.append(_suggestion(devices_module.usage_operation(mount)))
            found.append(_suggestion(
                maintenance_module.scrub_start_operation(mount)))
        for device in filesystem.devices:
            if device.has_errors and device.path:
                found.append(_suggestion(
                    devices_module.smart_operation(device.path)))
    return found


def _suggestion(operation) -> dict:
    """端末で実行してもらう想定のコマンド。

    root が要るものは ``sudo`` を付けた形で見せる。**この画面からは実行しない** —
    表示と実行が同じ文字列であるという約束を、ここでも崩さない。
    """
    shown = operation.with_sudo() if operation.needs_root else operation
    return shown.to_dict()


def _pick_any(versions, index, default_live: bool = False):
    """差分用に版を選ぶ。``_pick`` と違い live 版も選べる。

    復元元には live 版を指定できないが、比較相手としては最もよく使う。
    """
    numbered = list(enumerate(versions, start=1))
    if index in (None, ''):
        if default_live:
            live = [(i, v) for i, v in numbered if v.is_live]
            if live:
                return live[-1]
        return history_module.select_version(versions, None)
    try:
        number = int(index)
    except (TypeError, ValueError):
        raise ValueError(i18n.translate('error.version-not-found', index=index))
    selected = [(i, v) for i, v in numbered if i == number]
    if not selected:
        raise LookupError(i18n.translate('error.version-not-found', index=number))
    return selected[0]


def is_loopback(host: str) -> bool:
    """待ち受けアドレスがループバックかを判定する。"""
    import ipaddress
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # "localhost" は名前解決せずにループバックと見なす
        return host == 'localhost'


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          root: Optional[str] = None, read_only: bool = False,
          auth: Optional[str] = None, allow_no_auth: bool = False,
          debug: bool = False) -> int:
    """サーバーを起動する。戻り値は終了コード。"""
    try:
        import bottle  # noqa: F401
    except ImportError:
        print(i18n.translate('error.web-extra-missing'))
        return 2

    credentials = None
    if auth:
        try:
            credentials = Credentials.parse(auth)
        except ValueError as error:
            print(i18n.translate('error.prefix', message=error))
            return 2

    # 認証なしで外部に公開すると、到達できる誰もがファイルの中身を読め、
    # さらに復元 (= 書き込み) まで行えてしまう
    if credentials is None and not is_loopback(host):
        if not allow_no_auth:
            print(i18n.translate('serve.auth-required', host=host))
            return 2
        print(i18n.translate('serve.no-auth-warning', host=host))

    settings = Settings(root=root, read_only=read_only)
    app = create_app(settings, credentials)

    # flush を明示する。この直後に bottle.run へ入って戻らないため、
    # ログファイルへリダイレクトされているとバッファに残ったまま何も見えなくなる。
    print(i18n.translate('serve.started', url='http://{0}:{1}/'.format(host, port)),
          flush=True)
    if settings.root:
        print(i18n.translate('serve.root', path=settings.root), flush=True)
    if read_only:
        print(i18n.translate('serve.read-only'), flush=True)

    import bottle
    bottle.run(app=app, host=host, port=port, debug=debug, quiet=not debug)
    return 0
