"""配置管理模块"""

import os
import yaml
from pathlib import Path
from typing import Any, Optional, Dict
from dotenv import load_dotenv

from src.core.exceptions import ConfigError
from src.core.constants import (
    DEFAULT_STICKER_COUNT,
    DEFAULT_VARIANT_COUNT,
    DEFAULT_VARIATION_DEGREE,
    DEFAULT_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_WORKERS,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_CACHE_DIR,
    DEFAULT_TEMP_DIR,
    AIModel,
)


class Config:
    """配置管理类（单例模式）"""
    
    _instance: Optional['Config'] = None
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._load_config()
            self._initialized = True
    
    def _load_config(self):
        """加载配置"""
        # 加载环境变量（override=True 确保 .env 优先于系统/IDE 预设的环境变量）
        load_dotenv(override=True)

        # Cursor IDE injects ANTHROPIC_AUTH_TOKEN which the anthropic
        # library uses for an Authorization header, conflicting with
        # our own API key. Remove it so only x-api-key is sent.
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        
        # 加载 YAML 配置文件
        self._load_yaml_config()
        
        # 加载环境变量配置
        self._load_env_config()
        
        # 验证配置
        self._validate_config()
    
    def _load_yaml_config(self):
        """加载 YAML 配置文件"""
        config_dir = Path("config")
        
        # 默认配置
        default_config_path = config_dir / "default.yaml"
        if default_config_path.exists():
            with open(default_config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
        else:
            self._config = {}
        
        # 环境特定配置
        env = os.getenv("ENV", "development")
        env_config_path = config_dir / f"{env}.yaml"
        if env_config_path.exists():
            with open(env_config_path, 'r', encoding='utf-8') as f:
                env_config = yaml.safe_load(f) or {}
                self._merge_config(self._config, env_config)
    
    def _load_env_config(self):
        """从环境变量加载配置"""
        # AI 服务配置
        if not hasattr(self, '_config'):
            self._config = {}
        
        if 'ai' not in self._config:
            self._config['ai'] = {}
        
        # Claude 配置
        if 'claude' not in self._config['ai']:
            self._config['ai']['claude'] = {}
        
        self._config['ai']['claude']['api_key'] = os.getenv('ANTHROPIC_API_KEY', '')
        self._config['ai']['claude']['base_url'] = os.getenv('ANTHROPIC_BASE_URL', 'https://api.anthropic.com')
        self._config['ai']['claude']['model'] = os.getenv('CLAUDE_MODEL', AIModel.CLAUDE_OPUS.value)
        
        # Gemini 配置
        if 'gemini' not in self._config['ai']:
            self._config['ai']['gemini'] = {}
        
        self._config['ai']['gemini']['api_key'] = os.getenv('IMAGE_API_KEY', '')
        self._config['ai']['gemini']['base_url'] = os.getenv('IMAGE_BASE_URL', 'https://generativelanguage.googleapis.com')
        self._config['ai']['gemini']['model'] = os.getenv('IMAGE_MODEL', AIModel.GEMINI_FLASH.value)

        # OpenAI 配置
        if 'openai' not in self._config['ai']:
            self._config['ai']['openai'] = {}

        self._config['ai']['openai']['api_key'] = os.getenv('OPENAI_API_KEY', '')
        self._config['ai']['openai']['base_url'] = os.getenv('OPENAI_BASE_URL', '')
        self._config['ai']['openai']['model'] = os.getenv('OPENAI_MODEL', 'gpt-4o')

        # Feishu Bot
        if 'feishu' not in self._config:
            self._config['feishu'] = {}
        self._config['feishu']['app_id'] = os.getenv('FEISHU_APP_ID', '')
        self._config['feishu']['app_secret'] = os.getenv('FEISHU_APP_SECRET', '')
        self._config['feishu']['verification_token'] = os.getenv('FEISHU_VERIFICATION_TOKEN', '')
        self._config['feishu']['encrypt_key'] = os.getenv('FEISHU_ENCRYPT_KEY', '')
    
    def _merge_config(self, base: dict, override: dict):
        """合并配置字典"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_config(base[key], value)
            else:
                base[key] = value
    
    def _validate_config(self):
        """验证配置（仅做基础检查，API Key 由各 Service 自行校验）"""
        pass
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值（支持点号分隔的嵌套键）
        
        Args:
            key: 配置键，支持 "ai.claude.model" 格式
            default: 默认值
            
        Returns:
            配置值
        """
        keys = key.split('.')
        value = self._config
        
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any):
        """设置配置值
        
        Args:
            key: 配置键
            value: 配置值
        """
        keys = key.split('.')
        config = self._config
        
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        config[keys[-1]] = value
    
    def reload(self):
        """重新加载配置"""
        self._initialized = False
        self._load_config()
        self._initialized = True
    
    # ============================================================
    # 便捷属性访问
    # ============================================================
    
    @property
    def claude_api_key(self) -> str:
        """Claude API Key"""
        return self.get('ai.claude.api_key', '')
    
    @property
    def claude_base_url(self) -> str:
        """Claude Base URL"""
        return self.get('ai.claude.base_url', 'https://api.anthropic.com')
    
    @property
    def claude_model(self) -> str:
        """Claude 模型"""
        return self.get('ai.claude.model', AIModel.CLAUDE_OPUS.value)
    
    @property
    def openai_api_key(self) -> str:
        """OpenAI API Key"""
        return self.get('ai.openai.api_key', '')

    @property
    def openai_base_url(self) -> str:
        """OpenAI Base URL"""
        return self.get('ai.openai.base_url', '')

    @property
    def openai_model(self) -> str:
        """OpenAI 模型"""
        return self.get('ai.openai.model', 'gpt-4o')

    @property
    def gemini_api_key(self) -> str:
        """Gemini API Key"""
        return self.get('ai.gemini.api_key', '')
    
    @property
    def gemini_base_url(self) -> str:
        """Gemini Base URL"""
        return self.get('ai.gemini.base_url', 'https://generativelanguage.googleapis.com')
    
    @property
    def gemini_model(self) -> str:
        """Gemini 模型"""
        return self.get('ai.gemini.model', AIModel.GEMINI_FLASH.value)
    
    @property
    def default_sticker_count(self) -> int:
        """默认贴纸生成数量"""
        return self.get('sticker.pack.default_count', DEFAULT_STICKER_COUNT)
    
    @property
    def default_variant_count(self) -> int:
        """默认变种数量"""
        return self.get('sticker.style.default_variant_count', DEFAULT_VARIANT_COUNT)
    
    @property
    def default_variation_degree(self) -> str:
        """默认变化程度"""
        return self.get('sticker.style.default_degree', DEFAULT_VARIATION_DEGREE.value)
    
    @property
    def timeout(self) -> int:
        """API 超时时间"""
        return self.get('ai.timeout', DEFAULT_TIMEOUT)
    
    @property
    def max_retries(self) -> int:
        """最大重试次数"""
        return self.get('ai.max_retries', DEFAULT_MAX_RETRIES)
    
    @property
    def max_workers(self) -> int:
        """最大并发数"""
        return self.get('concurrency.max_workers', DEFAULT_MAX_WORKERS)
    
    @property
    def output_dir(self) -> str:
        """输出目录"""
        return self.get('storage.output_dir', DEFAULT_OUTPUT_DIR)
    
    @property
    def cache_dir(self) -> str:
        """缓存目录"""
        return self.get('storage.cache_dir', DEFAULT_CACHE_DIR)
    
    @property
    def temp_dir(self) -> str:
        """临时目录"""
        return self.get('storage.temp_dir', DEFAULT_TEMP_DIR)

    @property
    def feishu_app_id(self) -> str:
        return self.get('feishu.app_id', '')

    @property
    def feishu_app_secret(self) -> str:
        return self.get('feishu.app_secret', '')

    @property
    def feishu_verification_token(self) -> str:
        return self.get('feishu.verification_token', '')

    @property
    def feishu_encrypt_key(self) -> str:
        return self.get('feishu.encrypt_key', '')
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self._config.copy()


# 全局配置实例
config = Config()
