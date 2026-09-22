#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``python -m btrfs_timeline`` で CLI を起動するための入口。"""

import sys

from .cli import main

if __name__ == '__main__':
    sys.exit(main())
