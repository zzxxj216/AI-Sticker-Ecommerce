"""验证器函数"""

from typing import Optional
from src.core.exceptions import ValidationError
from src.core.constants import VariationDegree


def validate_theme(theme: str) -> str:
    """验证主题名称
    
    Args:
        theme: 主题名称
        
    Returns:
        str: 验证后的主题名称
        
    Raises:
        ValidationError: 主题无效时
    """
    if not theme or not theme.strip():
        raise ValidationError("主题名称不能为空", field="theme", value=theme)
    
    theme = theme.strip()
    
    if len(theme) > 100:
        raise ValidationError("主题名称不能超过100个字符", field="theme", value=theme)
    
    return theme


def validate_count(count: int, min_count: int = 1, max_count: int = 100) -> int:
    """验证数量
    
    Args:
        count: 数量
        min_count: 最小值
        max_count: 最大值
        
    Returns:
        int: 验证后的数量
        
    Raises:
        ValidationError: 数量无效时
    """
    if not isinstance(count, int):
        raise ValidationError(
            f"数量必须是整数，当前类型: {type(count).__name__}",
            field="count",
            value=count
        )
    
    if count < min_count:
        raise ValidationError(
            f"数量不能小于 {min_count}",
            field="count",
            value=count
        )
    
    if count > max_count:
        raise ValidationError(
            f"数量不能大于 {max_count}",
            field="count",
            value=count
        )
    
    return count


def validate_variation_degree(degree: str) -> VariationDegree:
    """验证变化程度
    
    Args:
        degree: 变化程度字符串
        
    Returns:
        VariationDegree: 变化程度枚举
        
    Raises:
        ValidationError: 变化程度无效时
    """
    try:
        return VariationDegree(degree.lower())
    except ValueError:
        valid_values = [d.value for d in VariationDegree]
        raise ValidationError(
            f"无效的变化程度: {degree}，有效值: {', '.join(valid_values)}",
            field="variation_degree",
            value=degree
        )


def validate_image_path(filepath: str) -> str:
    """验证图片路径
    
    Args:
        filepath: 图片文件路径
        
    Returns:
        str: 验证后的路径
        
    Raises:
        ValidationError: 路径无效时
    """
    if not filepath or not filepath.strip():
        raise ValidationError("图片路径不能为空", field="image_path", value=filepath)
    
    from pathlib import Path
    from src.core.constants import SUPPORTED_IMAGE_FORMATS
    
    file_path = Path(filepath)
    
    if not file_path.exists():
        raise ValidationError(
            f"图片文件不存在: {filepath}",
            field="image_path",
            value=filepath
        )
    
    if file_path.suffix.lower() not in SUPPORTED_IMAGE_FORMATS:
        raise ValidationError(
            f"不支持的图片格式: {file_path.suffix}",
            field="image_path",
            value=filepath
        )
    
    return str(file_path)


def validate_api_key(api_key: str, service_name: str) -> str:
    """验证 API Key
    
    Args:
        api_key: API Key
        service_name: 服务名称
        
    Returns:
        str: 验证后的 API Key
        
    Raises:
        ValidationError: API Key 无效时
    """
    if not api_key or not api_key.strip():
        raise ValidationError(
            f"{service_name} API Key 不能为空",
            field="api_key",
            value="<empty>"
        )
    
    api_key = api_key.strip()
    
    if len(api_key) < 10:
        raise ValidationError(
            f"{service_name} API Key 长度不足",
            field="api_key",
            value="<hidden>"
        )
    
    return api_key
