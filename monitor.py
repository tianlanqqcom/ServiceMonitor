#!/usr/bin/env python3
"""启动入口：python monitor.py [--demo]

把 src 目录加入模块搜索路径，无需安装即可运行。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from service_monitor.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
