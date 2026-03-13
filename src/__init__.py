"""
AI 贴纸生成系统
~~~~~~~~~~~~~~~

一个完整的 AI 驱动贴纸生成系统，包含贴纸包生成和风格分析功能。

:copyright: (c) 2026
:license: MIT
"""

__version__ = "2.0.0"
__author__ = "AI Sticker Team"

from src.core.config import Config
from src.core.logger import get_logger

__all__ = ["Config", "get_logger", "__version__"]
