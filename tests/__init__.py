"""测试包：把 src/ 加入 sys.path，测试可以直接 import 项目模块。"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
