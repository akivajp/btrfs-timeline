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

from .. import __version__, i18n
from ..cli import entry_to_dict, snapshot_to_dict, version_to_dict
from ..core import browse as browse_module
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


def _is_binary(chunk: bytes) -> bool:
    """先頭を見てバイナリかどうかを判定する。

    NUL バイトが含まれていればテキストではない、という古典的な判定で十分。
    ここで厳密さを追ってもプレビューの役には立たない。
    """
    return b'\x00' in chunk


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

    @app.route('/')
    def index():
        """画面本体。サーバー側では一切書き換えない (Cockpit 版と同じファイル)。"""
        return bottle.static_file('index.html', root=STATIC_DIRECTORY)

    # index.html は ``./app.js`` のように相対パスで参照する。Cockpit モジュールでは
    # 同じディレクトリに並ぶので、そちらでも同じ HTML がそのまま動く。
    # そのためルート直下でも配信する必要がある。
    for asset in ('app.js', 'transport.js', 'style.css'):
        app.route('/' + asset, callback=(
            lambda name=asset: bottle.static_file(name, root=STATIC_DIRECTORY)))

    @app.route('/static/<path:path>')
    def static_files(path):
        return bottle.static_file(path, root=STATIC_DIRECTORY)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    @app.route('/api/config')
    def api_config():
        """起動時にブラウザが必要とするものを 1 回でまとめて返す。

        翻訳カタログもここに含める。CLI と同じ JSON カタログをそのまま使えるのが、
        gettext ではなく JSON を選んだ理由そのものである。
        """
        language = i18n.detect_language(bottle.request.query.get('lang') or None)
        catalog = dict(i18n.load_catalog(i18n.FALLBACK_LANGUAGE))
        catalog.update(i18n.load_catalog(language))
        return {
            'version': __version__,
            'language': language,
            'languages': i18n.available_languages(),
            'catalog': catalog,
            'read_only': settings.read_only,
            'root': settings.root,
            'start_path': settings.root or os.path.expanduser('~'),
        }

    @app.route('/api/mounts')
    def api_mounts():
        return {'mounts': [m._asdict() for m in mounts_module.btrfs_mounts()]}

    @app.route('/api/browse')
    def api_browse():
        try:
            path = checked_path(bottle.request.query.get('path'))
            entries = browse_module.list_directory(path, root=settings.root)
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

        try:
            with open(version.path, 'rb') as handle:
                chunk = handle.read(settings.preview_limit + 1)
        except OSError as error:
            return fail(403, error)

        truncated = len(chunk) > settings.preview_limit
        chunk = chunk[:settings.preview_limit]
        binary = _is_binary(chunk)
        return {
            'path': version.path,
            'index': number,
            'size': version.size,
            'binary': binary,
            'truncated': truncated,
            'text': '' if binary else chunk.decode('utf-8', errors='replace'),
        }

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
