"""全局常量定义"""

from enum import Enum
from typing import Final

# ============================================================
# 版本信息
# ============================================================
VERSION: Final[str] = "2.0.0"
APP_NAME: Final[str] = "AI Sticker Generator"

# ============================================================
# 贴纸类型
# ============================================================
class StickerType(str, Enum):
    """贴纸类型枚举"""
    TEXT = "text"           # 纯文本贴纸
    ELEMENT = "element"     # 元素贴纸
    COMBINED = "combined"   # 组合贴纸


# ============================================================
# 风格变化程度
# ============================================================
class VariationDegree(str, Enum):
    """风格变化程度枚举"""
    LOW = "low"         # 低变化（90%相似）
    MEDIUM = "medium"   # 中等变化（70%相似）
    HIGH = "high"       # 高变化（50%相似）


# ============================================================
# AI 模型配置
# ============================================================
class AIModel(str, Enum):
    """AI 模型枚举"""
    CLAUDE_OPUS = "claude-opus-4-6"
    CLAUDE_SONNET = "claude-sonnet-4-6"
    GEMINI_FLASH = "gemini-3.1-flash-image-preview"
    GEMINI_PRO = "gemini-3-pro-image-preview"


# ============================================================
# 默认配置值
# ============================================================
# 贴纸生成
DEFAULT_STICKER_COUNT: Final[int] = 40
DEFAULT_STICKER_TYPE_RATIO: Final[dict] = {
    StickerType.TEXT: 0.3,
    StickerType.ELEMENT: 0.4,
    StickerType.COMBINED: 0.3,
}

# 风格分析
DEFAULT_VARIANT_COUNT: Final[int] = 5
DEFAULT_VARIATION_DEGREE: Final[VariationDegree] = VariationDegree.MEDIUM

# API 配置
DEFAULT_TIMEOUT: Final[int] = 300
DEFAULT_MAX_RETRIES: Final[int] = 3
DEFAULT_RETRY_DELAY: Final[int] = 2

# 并发配置
DEFAULT_MAX_WORKERS: Final[int] = 5
DEFAULT_BATCH_SIZE: Final[int] = 10

# ============================================================
# 文件路径
# ============================================================
DEFAULT_OUTPUT_DIR: Final[str] = "./data/output"
DEFAULT_CACHE_DIR: Final[str] = "./data/cache"
DEFAULT_TEMP_DIR: Final[str] = "./data/temp"
DEFAULT_LOG_DIR: Final[str] = "./logs"

# ============================================================
# 图片配置
# ============================================================
IMAGE_FORMAT: Final[str] = "PNG"
IMAGE_MAX_SIZE: Final[tuple] = (1024, 1024)
IMAGE_MIN_SIZE: Final[tuple] = (256, 256)
SUPPORTED_IMAGE_FORMATS: Final[tuple] = (".png", ".jpg", ".jpeg", ".webp")

# ============================================================
# 日志配置
# ============================================================
LOG_FORMAT: Final[str] = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"
LOG_MAX_BYTES: Final[int] = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT: Final[int] = 5

# ============================================================
# 状态码
# ============================================================
class StatusCode(int, Enum):
    """状态码枚举"""
    SUCCESS = 200
    CREATED = 201
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    NOT_FOUND = 404
    INTERNAL_ERROR = 500
    SERVICE_UNAVAILABLE = 503


# ============================================================
# 错误消息
# ============================================================
ERROR_MESSAGES: Final[dict] = {
    "invalid_theme": "主题名称无效",
    "invalid_count": "生成数量必须在 1-100 之间",
    "invalid_image": "图片文件无效或不支持",
    "api_error": "API 调用失败",
    "config_error": "配置错误",
    "file_not_found": "文件不存在",
    "permission_denied": "权限不足",
}
