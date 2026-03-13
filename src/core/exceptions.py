"""自定义异常类"""

from typing import Optional, Any


class StickerError(Exception):
    """贴纸系统基础异常"""
    
    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        details: Optional[Any] = None
    ):
        self.message = message
        self.code = code
        self.details = details
        super().__init__(self.message)
    
    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "code": self.code,
            "details": self.details,
        }


class APIError(StickerError):
    """API 调用异常"""
    
    def __init__(
        self,
        message: str,
        service: Optional[str] = None,
        status_code: Optional[int] = None,
        response: Optional[Any] = None
    ):
        self.service = service
        self.status_code = status_code
        self.response = response
        super().__init__(
            message=message,
            code="API_ERROR",
            details={
                "service": service,
                "status_code": status_code,
                "response": response,
            }
        )


class ConfigError(StickerError):
    """配置错误异常"""
    
    def __init__(self, message: str, config_key: Optional[str] = None):
        self.config_key = config_key
        super().__init__(
            message=message,
            code="CONFIG_ERROR",
            details={"config_key": config_key}
        )


class ValidationError(StickerError):
    """数据验证异常"""
    
    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        value: Optional[Any] = None
    ):
        self.field = field
        self.value = value
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            details={"field": field, "value": value}
        )


class FileError(StickerError):
    """文件操作异常"""
    
    def __init__(self, message: str, filepath: Optional[str] = None):
        self.filepath = filepath
        super().__init__(
            message=message,
            code="FILE_ERROR",
            details={"filepath": filepath}
        )


class ImageError(StickerError):
    """图片处理异常"""
    
    def __init__(
        self,
        message: str,
        image_path: Optional[str] = None,
        operation: Optional[str] = None
    ):
        self.image_path = image_path
        self.operation = operation
        super().__init__(
            message=message,
            code="IMAGE_ERROR",
            details={"image_path": image_path, "operation": operation}
        )


class GenerationError(StickerError):
    """生成失败异常"""
    
    def __init__(
        self,
        message: str,
        stage: Optional[str] = None,
        retry_count: Optional[int] = None
    ):
        self.stage = stage
        self.retry_count = retry_count
        super().__init__(
            message=message,
            code="GENERATION_ERROR",
            details={"stage": stage, "retry_count": retry_count}
        )


class TimeoutError(StickerError):
    """超时异常"""
    
    def __init__(self, message: str, timeout: Optional[int] = None):
        self.timeout = timeout
        super().__init__(
            message=message,
            code="TIMEOUT_ERROR",
            details={"timeout": timeout}
        )


class RateLimitError(APIError):
    """API 速率限制异常"""
    
    def __init__(
        self,
        message: str = "API 速率限制",
        service: Optional[str] = None,
        retry_after: Optional[int] = None
    ):
        self.retry_after = retry_after
        super().__init__(
            message=message,
            service=service,
            status_code=429
        )
        self.details["retry_after"] = retry_after
