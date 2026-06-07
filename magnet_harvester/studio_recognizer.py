"""
StudioRecognizer — 从文件名识别成人厂牌

从 config/adult_studios.json 加载厂牌关键词列表，
在文件名中查找关键词并返回厂牌名 + 下载路径。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
STUDIO_FILE = CONFIG_DIR / "adult_studios.json"

# 默认下载基础路径
ADULT_BASE_PATH = "/volume1/downloads/adult"


def _load_studios() -> List[Dict[str, str]]:
    """从 JSON 文件加载厂牌列表"""
    try:
        if STUDIO_FILE.exists():
            with open(STUDIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("studios", [])
        else:
            log.warning(f"厂牌配置文件不存在: {STUDIO_FILE}")
            return []
    except Exception as e:
        log.error(f"加载厂牌配置失败: {e}")
        return []


class StudioRecognizer:
    """从文件名中识别成人厂牌。

    用法:
        r = StudioRecognizer()
        result = r.recognize("SexArt.26.02.01.Bonnie.XXX.2160p.MP4-WRB")
        # -> {"name": "SexArt", "save_path": "/volume1/downloads/adult/SexArt"}
    """

    def __init__(self, base_path: str = ADULT_BASE_PATH):
        self._base_path = base_path
        self._studios = _load_studios()
        # 编译正则: 关键词必须在文件名开头或点号/空格之后
        self._patterns: List[tuple[re.Pattern, str, str]] = []
        for s in self._studios:
            kw = re.escape(s["keyword"])
            # 匹配文件名开头的关键词，或前面是点号/空格/下划线的关键词
            pattern = re.compile(rf"(?:^|[. _-])(?:{kw})", re.IGNORECASE)
            self._patterns.append((pattern, s["name"], kw))

    def recognize(self, name: str) -> Optional[Dict[str, str]]:
        """识别文件名中的厂牌。

        返回:
            {"name": "SexArt", "save_path": "/volume1/downloads/adult/SexArt"}
            或 None（未匹配）
        """
        n = name.lower()
        # 策略1：文件名以关键词开头（点号分隔）
        for s in self._studios:
            kw = s["keyword"].lower()
            if n.startswith(kw) or n.startswith(kw + ".") or n.startswith(kw + "_"):
                return {
                    "name": s["name"],
                    "save_path": f"{self._base_path}/{s['name']}",
                }

        # 策略2：正则匹配（前面有点号/空格/下划线等分隔符）
        for pattern, studio_name, keyword in self._patterns:
            if pattern.search(name):
                return {
                    "name": studio_name,
                    "save_path": f"{self._base_path}/{studio_name}",
                }

        return None

    def reload(self):
        """重新加载厂牌配置（添加新厂牌后调用）"""
        self._studios = _load_studios()
        self._patterns = []
        for s in self._studios:
            kw = re.escape(s["keyword"])
            pattern = re.compile(rf"(?:^|[. _-])(?:{kw})", re.IGNORECASE)
            self._patterns.append((pattern, s["name"], kw))
