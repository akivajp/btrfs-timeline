#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""アクセス制御 (BASIC 認証と同一オリジン検証)。

BASIC 認証の資格情報はブラウザが自動で送るため、**認証だけではクロスサイトからの
書き込みを防げない**。悪意のあるページが ``fetch('http://localhost:8088/api/restore')``
を仕込めば、ブラウザは資格情報を付けて送ってしまう。そのため復元のような
書き込み系のエンドポイントでは ``Origin`` ヘッダによる同一オリジン検証を併用する。
"""

from __future__ import annotations

import base64
import functools
import hmac
from typing import Optional
from urllib.parse import urlsplit

from .. import i18n


class Credentials:
    """BASIC 認証の資格情報。"""

    def __init__(self, user: str, password: str) -> None:
        self.user = user
        self.password = password

    @classmethod
    def parse(cls, spec: str) -> 'Credentials':
        """``user:password`` 形式の文字列を解析する。

        パスワードに ``:`` を含めてよいよう、最初の ``:`` だけで分割する。

        Raises:
            ValueError: 形式が不正、またはどちらかが空。
        """
        if ':' not in spec:
            raise ValueError(i18n.translate('error.auth-format'))
        user, password = spec.split(':', 1)
        if not user or not password:
            raise ValueError(i18n.translate('error.auth-format'))
        return cls(user, password)

    def verify(self, user: Optional[str], password: Optional[str]) -> bool:
        """資格情報が一致するかを検証する。

        タイミング攻撃を避けるため ``hmac.compare_digest`` を使い、
        短絡評価で「ユーザー名だけ合っている」ことが分からないよう両方を必ず評価する。
        """
        if user is None or password is None:
            return False
        user_ok = hmac.compare_digest(user.encode('utf-8'), self.user.encode('utf-8'))
        password_ok = hmac.compare_digest(password.encode('utf-8'),
                                          self.password.encode('utf-8'))
        return user_ok and password_ok


def parse_basic_header(value: Optional[str]):
    """``Authorization: Basic ...`` を ``(user, password)`` に分解する。

    解析できなければ ``(None, None)``。bottle の ``request.auth`` とほぼ同じだが、
    テストから直接呼べるよう独立させてある。
    """
    if not value or not value.lower().startswith('basic '):
        return None, None
    try:
        decoded = base64.b64decode(value.split(' ', 1)[1]).decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        return None, None
    if ':' not in decoded:
        return None, None
    user, password = decoded.split(':', 1)
    return user, password


def is_same_origin(origin: Optional[str], host: Optional[str]) -> bool:
    """``Origin`` ヘッダが自ホストと一致するかを判定する。

    ``Origin`` が無い場合は、ブラウザ以外からのリクエスト (curl など) とみなして
    許可する。ブラウザはクロスオリジンの書き込みには必ず ``Origin`` を付けるため、
    「無い＝クロスサイトではない」と判断してよい。
    """
    if not origin:
        return True
    if not host:
        return False
    return urlsplit(origin).netloc == host


def make_auth_plugin(credentials: Optional[Credentials], bottle_module):
    """BASIC 認証を要求する bottle プラグインを作る。

    ``credentials`` が None なら素通しする (ループバック限定で使う場合)。
    """
    def plugin(callback):
        @functools.wraps(callback)
        def wrapper(*args, **kwargs):
            if credentials is not None:
                user, password = parse_basic_header(
                    bottle_module.request.get_header('Authorization'))
                if not credentials.verify(user, password):
                    raise bottle_module.HTTPResponse(
                        status=401,
                        headers={'WWW-Authenticate': 'Basic realm="btrfs-timeline"'},
                        body=i18n.translate('error.unauthorized'),
                    )
            return callback(*args, **kwargs)
        return wrapper

    plugin.name = 'btrfs-timeline-auth'
    plugin.api = 2
    return plugin
