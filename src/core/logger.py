"""日志管理模块"""

import logging
import sys
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler

from src.core.constants import (
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
    DEFAULT_LOG_DIR,
)


class Logger:
    """日志管理类"""
    
    _loggers = {}
    
    @classmethod
    def get_logger(
        cls,
        name: str,
        level: str = "INFO",
        log_file: Optional[str] = None,
        console: bool = True
    ) -> logging.Logger:
        """获取日志记录器
        
        Args:
            name: 日志记录器名称
            level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            log_file: 日志文件路径（可选）
            console: 是否输出到控制台
            
        Returns:
            logging.Logger: 日志记录器
        """
        # 如果已存在，直接返回
        if name in cls._loggers:
            return cls._loggers[name]
        
        # 创建日志记录器
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, level.upper()))
        
        # 避免重复添加处理器
        if logger.handlers:
            return logger
        
        # 创建格式化器
        formatter = logging.Formatter(
            fmt=LOG_FORMAT,
            datefmt=LOG_DATE_FORMAT
        )
        
        # 控制台处理器
        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.DEBUG)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        
        # 文件处理器
        if log_file:
            # 确保日志目录存在
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            file_handler = RotatingFileHandler(
                filename=log_file,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding='utf-8'
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        
        # 缓存日志记录器
        cls._loggers[name] = logger
        
        return logger
    
    @classmethod
    def set_level(cls, name: str, level: str):
        """设置日志级别
        
        Args:
            name: 日志记录器名称
            level: 日志级别
        """
        if name in cls._loggers:
            cls._loggers[name].setLevel(getattr(logging, level.upper()))
    
    @classmethod
    def clear_handlers(cls, name: str):
        """清除日志处理器
        
        Args:
            name: 日志记录器名称
        """
        if name in cls._loggers:
            logger = cls._loggers[name]
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)


def get_logger(
    name: str = "sticker",
    level: str = "INFO",
    log_file: Optional[str] = None,
    enable_file: bool = False,
) -> logging.Logger:
    """获取日志记录器（便捷函数）

    Args:
        name: 日志记录器名称
        level: 日志级别
        log_file: 日志文件路径（传入则自动启用文件日志）
        enable_file: 是否启用文件日志（默认关闭，避免不必要的磁盘 I/O）
    """
    if log_file is None and enable_file:
        log_dir = Path(DEFAULT_LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = str(log_dir / f"{name}.log")

    return Logger.get_logger(name, level, log_file)


# 预定义的日志记录器
def get_service_logger(service_name: str) -> logging.Logger:
    """获取服务日志记录器"""
    return get_logger(f"service.{service_name}")


def get_api_logger() -> logging.Logger:
    """获取 API 日志记录器"""
    return get_logger("api")


def get_ui_logger() -> logging.Logger:
    """获取 UI 日志记录器"""
    return get_logger("ui")
