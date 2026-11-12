"""测试环境隔离：绝不读取开发者真实的 .env。

配置在 import 时读取 ENV_FILE，所以必须在任何测试模块导入 magnet_harvester
之前覆盖它。指向一个不存在的文件等价于 CI 的无 .env 环境。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ["ENV_FILE"] = str(Path(__file__).with_name("_no_env_for_tests"))
