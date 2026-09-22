#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""btrfs-timeline のコアロジック。

Web フレームワークにも CLI にも依存しない純粋な処理だけを置く。
このコアは CLI (``btrfs_timeline.cli``) を通じて JSON として公開され、
スタンドアロン Web UI / Cockpit モジュール / デスクトップ GUI の
いずれからも同じ形で利用できるようにしてある。
"""

from .history import Version, list_versions, probe_snapshots
from .mounts import MountPoint, find_containing_mount, read_mounts
from .restore import RestorePlan, RestoreResult, clone_file, execute, plan_restore
from .snapshots import Snapshot, discover

__all__ = [
    'MountPoint', 'RestorePlan', 'RestoreResult', 'Snapshot', 'Version',
    'clone_file', 'discover', 'execute', 'find_containing_mount', 'list_versions',
    'plan_restore', 'probe_snapshots', 'read_mounts',
]
