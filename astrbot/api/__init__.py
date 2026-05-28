"""
AstrBot API compatibility layer.

提供与原 AstrBot API 兼容的 logger、MessageChain、Comp、
AstrBotConfig、AstrMessageEvent 等接口。
"""

import logging
import sys

# ============================================================
# Logger
# ============================================================

_logger = logging.getLogger("astrbot_plugin_disaster_warning")
_logger.setLevel(logging.INFO)

if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    _logger.addHandler(_handler)

logger = _logger

# ============================================================
# 顶层导出：兼容 "from astrbot.api import AstrBotConfig, logger"
# ============================================================
from .star import AstrBotConfig  # noqa: E402, F401

__all__ = ["logger", "AstrBotConfig"]
