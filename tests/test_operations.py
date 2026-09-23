#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部コマンドの実行と危険度のテスト。

ここから先、このツールは利用者のファイルシステムを変更しうるコマンドを扱う。
守りたい約束は 3 つで、どれも「呼び出し側の良心に任せない」形にしてある。

- 何を実行するのかが必ず分かること
- 危険度が申告されること
- **危険な操作は、確認を取るまで実行できないこと**
"""

import pytest

from btrfs_timeline.core import operations


def test_describe_does_not_run_anything(tmp_path):
    """記述するだけでは何も起きない。画面はこれを見せてから確認を取る。"""
    marker = tmp_path / 'created'
    operation = operations.describe(
        ['touch', str(marker)], risk=operations.CAUTION,
        summary_key='operation.unknown')
    assert not marker.exists()
    assert operation.argv == ['touch', str(marker)]


def test_unknown_risk_is_rejected():
    """危険度を書き忘れたり綴りを間違えたりしたまま通さない。"""
    with pytest.raises(ValueError):
        operations.describe(['true'], risk='probably-fine')


def test_dangerous_operations_need_confirmation(tmp_path):
    """**確認を取り忘れた呼び出しは通らない。** 既定で拒否する。"""
    marker = tmp_path / 'created'
    operation = operations.describe(
        ['touch', str(marker)], risk=operations.DANGEROUS,
        summary_key='operation.unknown')

    with pytest.raises(PermissionError):
        operations.run(operation)
    assert not marker.exists()

    operations.run(operation, confirmed=True)
    assert marker.exists()


def test_safe_and_caution_run_without_confirmation(tmp_path):
    """読むだけ・戻せる操作まで確認を求めると、確認そのものが軽く見られる。"""
    for risk in (operations.SAFE, operations.CAUTION):
        marker = tmp_path / risk
        operation = operations.describe(
            ['touch', str(marker)], risk=risk, summary_key='operation.unknown')
        assert operations.run(operation).ok
        assert marker.exists()


def test_result_reports_failure():
    operation = operations.describe(['false'], summary_key='operation.unknown')
    result = operations.run(operation)
    assert result.ok is False
    assert result.returncode != 0


def test_output_is_captured():
    operation = operations.describe(['echo', 'hello'], summary_key='operation.unknown')
    assert operations.run(operation).stdout.strip() == 'hello'


def test_display_quotes_what_needs_quoting():
    """画面に出す文字列は、そのまま読んで意味が通る必要がある。"""
    operation = operations.describe(
        ['btrfs', 'device', 'remove', '/mnt/my disk'], summary_key='operation.unknown')
    assert operation.display() == "btrfs device remove '/mnt/my disk'"


def test_display_leaves_ordinary_paths_alone():
    operation = operations.describe(
        ['btrfs', '--format', 'json', 'device', 'stats', '/home'],
        summary_key='operation.unknown')
    assert operation.display() == 'btrfs --format json device stats /home'


def test_to_dict_carries_everything_a_screen_needs():
    """画面は「何を」「どれくらい危ないか」「root が要るか」を全部出せる。"""
    operation = operations.describe(
        ['btrfs', 'device', 'stats', '/home'], risk=operations.SAFE,
        needs_root=False, summary_key='operation.device-stats', path='/home')
    payload = operation.to_dict()
    assert payload['argv'] == ['btrfs', 'device', 'stats', '/home']
    assert payload['command'] == 'btrfs device stats /home'
    assert payload['risk'] == operations.SAFE
    assert payload['needs_root'] is False
    assert '/home' in payload['summary']
