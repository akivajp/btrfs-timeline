#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scrub と balance のテスト。

**実際に scrub や balance を走らせることはしない。** テストのために利用者の
ファイルシステムを何時間も回すわけにはいかないし、そもそも root が要る。
確かめるのは 2 つ — 組み立てたコマンドが正しいことと、``--format json`` の
無い出力を正しく解釈できることである。

出力の見本は btrfs-progs のドキュメントと実機の出力から取っている。
"""

import pytest

from btrfs_timeline.core import maintenance, operations

# 一度も scrub していないファイルシステム (実機の出力)
NEVER_SCRUBBED = '''UUID:             e288cd7e-247d-4d5e-87ca-e65361537bbe
\tno stats available
\tdata_extents_scrubbed: 0
\tread_errors: 0
\tcsum_errors: 0
\tuncorrectable_errors: 0
\tcorrected_errors: 0
'''

RUNNING = '''UUID:             e288cd7e-247d-4d5e-87ca-e65361537bbe
Scrub started:    Wed Apr 10 12:34:56 2023
Status:           running
Duration:         0:00:05
Time left:        0:00:05
ETA:              Wed Apr 10 12:35:01 2023
Total to scrub:   30407843840
Bytes scrubbed:   14774142976  (48.59%)
Rate:             2952428800/s
Error summary:    no errors found
\tdata_extents_scrubbed: 12345
\tcorrected_errors: 0
\tuncorrectable_errors: 0
'''

WITH_ERRORS = '''UUID:             e288cd7e-247d-4d5e-87ca-e65361537bbe
Status:           finished
Duration:         1:02:03
Total to scrub:   30407843840
Bytes scrubbed:   30407843840  (100.00%)
Error summary:    csum=72
  Corrected:      2
  Uncorrectable:  72
  Unverified:     0
\tcsum_errors: 72
\tcorrected_errors: 2
\tuncorrectable_errors: 72
'''


# --------------------------------------------------------------------------
# 組み立てたコマンド
# --------------------------------------------------------------------------

def test_scrub_status_is_attempted_without_root():
    """前もって root を要求しない。

    まだ scrub していなければ非特権で通るため、断るより試すほうがよい。
    一度でも scrub すると記録ファイルが root 専用になって読めなくなるが、
    それは実行して分かることで、そのときは sudo 付きのコマンドを示す。
    """
    operation = maintenance.scrub_status_operation('/home')
    assert operation.argv == ['btrfs', 'scrub', 'status', '-R', '--raw', '/home']
    assert operation.risk == operations.SAFE
    assert operation.needs_root is False


def test_balance_status_does_need_root():
    """balance は状態を見るだけでも root が要る。scrub と違う点。"""
    operation = maintenance.balance_status_operation('/home')
    assert operation.risk == operations.SAFE
    assert operation.needs_root is True


def test_scrub_start_is_caution_not_dangerous():
    """scrub はデータを失う操作ではない。中断も再開もできる。

    何でも「危険」にすると、本当に危険なものが目立たなくなる。
    """
    operation = maintenance.scrub_start_operation('/home')
    assert operation.argv == ['btrfs', 'scrub', 'start', '/home']
    assert operation.risk == operations.CAUTION
    assert operation.needs_root is True


def test_readonly_scrub_says_it_repairs_nothing():
    operation = maintenance.scrub_start_operation('/home', readonly=True)
    assert operation.argv == ['btrfs', 'scrub', 'start', '-r', '/home']
    assert 'repair' in operation.summary() or '修復' in operation.summary()


def test_balance_usage_filter_applies_to_data_and_metadata():
    """``-dusage`` だけだとメタデータが残る。両方に掛けるのが定石。"""
    operation = maintenance.balance_start_operation('/home', usage=20)
    assert operation.argv == ['btrfs', 'balance', 'start',
                              '-dusage=20', '-musage=20', '/home']
    assert '20' in operation.summary()


def test_full_balance_says_it_moves_everything():
    operation = maintenance.balance_start_operation('/home')
    assert operation.argv == ['btrfs', 'balance', 'start', '/home']
    assert operation.risk == operations.CAUTION


@pytest.mark.parametrize('builder,expected', [
    (maintenance.scrub_cancel_operation, ['btrfs', 'scrub', 'cancel', '/home']),
    (maintenance.scrub_resume_operation, ['btrfs', 'scrub', 'resume', '/home']),
    (maintenance.balance_cancel_operation, ['btrfs', 'balance', 'cancel', '/home']),
    (maintenance.balance_pause_operation, ['btrfs', 'balance', 'pause', '/home']),
    (maintenance.balance_resume_operation, ['btrfs', 'balance', 'resume', '/home']),
])
def test_control_operations(builder, expected):
    operation = builder('/home')
    assert operation.argv == expected
    assert operation.needs_root is True


# --------------------------------------------------------------------------
# scrub status の解釈
# --------------------------------------------------------------------------

def test_never_scrubbed_is_reported_as_none():
    """一度も走らせていないと ``no stats available`` の 1 行しか出ない。"""
    status = maintenance.parse_scrub_status(NEVER_SCRUBBED)
    assert status.state == 'none'
    assert status.running is False
    assert status.has_errors is False


def test_running_scrub_reports_progress():
    status = maintenance.parse_scrub_status(RUNNING)
    assert status.state == 'running'
    assert status.running is True
    assert status.total_bytes == 30407843840
    assert status.scrubbed_bytes == 14774142976
    assert status.percent == pytest.approx(48.59)
    assert status.time_left == '0:00:05'
    assert status.error_summary == 'no errors found'


def test_uncorrectable_errors_are_what_matter():
    """直せた誤りと直せなかった誤りは意味が違う。

    ``corrected_errors`` は別コピーから復旧できたということで、警戒はしても
    データは無事。``uncorrectable_errors`` は **実際に失われている**。
    """
    status = maintenance.parse_scrub_status(WITH_ERRORS)
    assert status.state == 'finished'
    assert status.counters['corrected_errors'] == 2
    assert status.counters['uncorrectable_errors'] == 72
    assert status.has_errors is True


def test_corrected_errors_alone_are_not_flagged_as_loss():
    status = maintenance.parse_scrub_status(
        RUNNING.replace('corrected_errors: 0', 'corrected_errors: 5'))
    assert status.counters['corrected_errors'] == 5
    assert status.has_errors is False


def test_raw_output_is_kept():
    """解釈しきれない項目があっても、利用者は原文を読める。

    ``--format json`` が使えない相手なので、これは残しておくべきものである。
    """
    assert maintenance.parse_scrub_status(RUNNING).raw == RUNNING


def test_unparseable_output_does_not_raise():
    status = maintenance.parse_scrub_status('something entirely unexpected')
    assert status.state in maintenance.SCRUB_STATES + ('unknown',)
    assert status.raw == 'something entirely unexpected'


def test_no_output_is_not_the_same_as_no_scrub():
    """**空の出力を「scrub したことがない」と言い切らない。**

    記録ファイルは root 専用で作られるため、一度 scrub したあとの非特権では
    出力が空になる。これを ``none`` にすると、画面が事実と逆のことを言う。
    """
    assert maintenance.parse_scrub_status('').state == 'unknown'
    assert maintenance.parse_scrub_status('   ').state == 'unknown'


def test_btrfs_saying_there_are_no_stats_is_none():
    """btrfs 自身が「記録が無い」と答えたときだけ ``none``。"""
    assert maintenance.parse_scrub_status(NEVER_SCRUBBED).state == 'none'


# --------------------------------------------------------------------------
# balance status の解釈
# --------------------------------------------------------------------------

def test_no_balance_running():
    status = maintenance.parse_balance_status("No balance found on '/home'")
    assert status.state == 'none'
    assert status.running is False


def test_running_balance_reports_chunks():
    status = maintenance.parse_balance_status(
        "Balance on '/home' is running\n"
        "3 out of about 12 chunks balanced (5 considered), 75% left\n")
    assert status.state == 'running'
    assert (status.balanced, status.total) == (3, 12)
    assert status.considered == 5
    assert status.percent_left == 75


def test_paused_balance():
    status = maintenance.parse_balance_status(
        "Balance on '/home' is paused\n"
        "1 out of about 12 chunks balanced (2 considered), 92% left\n")
    assert status.state == 'paused'
    assert status.running is False
    assert status.balanced == 1


def test_balance_output_we_do_not_recognise():
    status = maintenance.parse_balance_status('who knows')
    assert status.state == 'unknown'
    assert status.balanced is None


# --------------------------------------------------------------------------
# 確認を取る挙動 (この機能の本体)
# --------------------------------------------------------------------------

@pytest.fixture
def never_runs(monkeypatch):
    """実行を差し替え、何が実行されようとしたかだけを記録する。

    テストが本物の scrub を走らせないため、というだけではない。
    「確認しなかったのに実行された」を検出するには、実行を観測する必要がある。
    """
    from btrfs_timeline.core import operations as operations_module

    attempted = []

    def fake_run(operation, confirmed=False, confirmation=None, timeout=None):
        attempted.append((operation, confirmation))
        return operations_module.Result(operation=operation, returncode=0,
                                        stdout='', stderr='')

    monkeypatch.setattr(operations_module, 'run', fake_run)
    monkeypatch.setattr('btrfs_timeline.cli.operations_module.run', fake_run)
    return attempted


@pytest.fixture
def as_root(monkeypatch):
    """root で動いていることにする (sudo の有無を分けて試すため)。"""
    monkeypatch.setattr('btrfs_timeline.cli.os.geteuid', lambda: 0)


def test_refuses_to_start_without_a_confirmation(never_runs, as_root, monkeypatch, capsys):
    """**確認できない場所では実行しない。** 黙って進むのが一番まずい。"""
    from btrfs_timeline import cli

    monkeypatch.setattr('btrfs_timeline.cli.sys.stdin.isatty', lambda: False)
    assert cli.main(['scrub', 'start', '/home']) == 1
    assert never_runs == []

    output = capsys.readouterr()
    # 何を実行しようとしたのか、どれくらい危ないのかは必ず示す
    assert 'btrfs scrub start' in output.out
    assert '--yes' in output.err


def test_yes_allows_it_to_run(never_runs, as_root, capsys):
    from btrfs_timeline import cli

    assert cli.main(['scrub', 'start', '/home', '--yes']) == 0
    assert [o.argv for o, _c in never_runs] == [['btrfs', 'scrub', 'start', '/home']]
    assert 'btrfs scrub start /home' in capsys.readouterr().out


def test_answering_no_runs_nothing(never_runs, as_root, monkeypatch, capsys):
    from btrfs_timeline import cli

    monkeypatch.setattr('btrfs_timeline.cli.sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt='': 'n')
    assert cli.main(['scrub', 'start', '/home']) == 1
    assert never_runs == []


def test_answering_yes_runs_it(never_runs, as_root, monkeypatch):
    from btrfs_timeline import cli

    monkeypatch.setattr('btrfs_timeline.cli.sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt='': 'y')
    assert cli.main(['scrub', 'start', '/home']) == 0
    assert len(never_runs) == 1


def test_reading_status_asks_nothing(never_runs, monkeypatch):
    """安全な操作まで確認を求めると、確認そのものが軽く見られる。"""
    from btrfs_timeline import cli

    monkeypatch.setattr('btrfs_timeline.cli.sys.stdin.isatty', lambda: False)
    assert cli.main(['scrub', 'status', '/home']) == 0
    assert [o.argv[:3] for o, _c in never_runs] == [['btrfs', 'scrub', 'status']]


def test_without_root_it_says_exactly_what_to_run(never_runs, monkeypatch, capsys):
    """勝手に sudo は付けない。何が必要かを示して止まる。"""
    from btrfs_timeline import cli

    monkeypatch.setattr('btrfs_timeline.cli.os.geteuid', lambda: 1000)
    assert cli.main(['balance', 'start', '/home', '--yes']) == 1
    assert never_runs == []
    assert 'sudo btrfs balance start /home' in capsys.readouterr().err


def test_sudo_is_used_only_when_asked(never_runs, monkeypatch):
    from btrfs_timeline import cli

    monkeypatch.setattr('btrfs_timeline.cli.os.geteuid', lambda: 1000)
    assert cli.main(['balance', 'start', '/home', '--yes', '--sudo']) == 0
    assert never_runs[0][0].argv == ['sudo', 'btrfs', 'balance', 'start', '/home']


def test_what_is_shown_is_what_is_run(never_runs, monkeypatch, capsys):
    """表示したコマンドと実行したコマンドが食い違わない。

    sudo を付けるとここがずれやすい。ずれると、確認の意味が無くなる。
    """
    from btrfs_timeline import cli

    monkeypatch.setattr('btrfs_timeline.cli.os.geteuid', lambda: 1000)
    cli.main(['scrub', 'start', '/home', '--yes', '--sudo'])
    shown = capsys.readouterr().out
    assert never_runs[0][0].display() in shown
