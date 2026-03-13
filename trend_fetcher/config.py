"""
全局配置模块 - 从环境变量加载配置
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 依次尝试加载 .env：先找 trend_fetcher/.env，再找项目根目录 .env
_base = Path(__file__).parent
for _env_path in [_base / ".env", _base.parent / ".env"]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break


class Config:
    # API Keys
    NEWS_API_KEY: str = os.getenv("NEWS_API_KEY", "")
    REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
    REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_BASE_URL: str = os.getenv("ANTHROPIC_BASE_URL", "")

    # 目标市场
    TREND_GEO: str = os.getenv("TREND_GEO", "united_states")
    TREND_GEO_CODE: str = "US"  # 用于 Google Trends RSS

    # 输出路径（resolve 确保绝对路径，避免 CWD 依赖问题）
    OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", str(_base / "output"))).resolve()

    # Reddit 爬取配置
    REDDIT_SUBREDDITS: list = [
        "popular",       # 全站热门（跨类别）
        "memes",         # 最新梗
        "dankmemes",     # 高质量梗
        "funny",         # 搞笑内容
        "me_irl",        # 网络文化
        "pics",          # 热门图片
        "aww",           # 可爱动物（贴纸热门类别）
        "wholesomememes", # 温暖系贴纸
        "mademesmile",   # 正能量
    ]
    REDDIT_POST_LIMIT: int = 10  # 每个子版块取几条

    # RSS Feed 列表
    RSS_FEEDS: dict = {
        "Google Trends US": f"https://trends.google.com/trends/trendingsearches/daily/rss?geo=US",
        "Reddit Popular": "https://www.reddit.com/r/popular/.rss",
        "BBC News": "http://feeds.bbci.co.uk/news/rss.xml",
        "CNN Top Stories": "http://rss.cnn.com/rss/edition.rss",
        "The Guardian US": "https://www.theguardian.com/us-news/rss",
        "Product Hunt": "https://www.producthunt.com/feed",
    }

    # pytrends 配置
    PYTRENDS_TIMEOUT: int = 30
    PYTRENDS_RETRIES: int = 2
    PYTRENDS_BACKOFF: float = 1.0

    # Claude 模型
    CLAUDE_MODEL: str = "claude-sonnet-4-5"

    # Nano Banana 图片生成配置
    # IMAGE_API_KEY / IMAGE_BASE_URL 独立于 Claude，用于 Nano Banana 图片生成
    # 注册地址: https://api.apiyi.com  （与 Claude 的 esapi.top 是不同的服务）
    IMAGE_API_KEY: str = os.getenv("IMAGE_API_KEY", os.getenv("ANTHROPIC_API_KEY", ""))
    IMAGE_BASE_URL: str = os.getenv("IMAGE_BASE_URL", os.getenv("ANTHROPIC_BASE_URL", ""))
    IMAGE_MODEL: str = os.getenv("IMAGE_MODEL", "gemini-3.1-flash-image-preview")
    IMAGE_OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", str(_base / "output"))).resolve() / "images"
    IMAGE_TIMEOUT: int = int(os.getenv("IMAGE_TIMEOUT", "120"))
    IMAGE_ASPECT_RATIO: str = os.getenv("IMAGE_ASPECT_RATIO", "1:1")

    # HTTP 请求通用 Headers
    HTTP_HEADERS: dict = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        )
    }


config = Config()
