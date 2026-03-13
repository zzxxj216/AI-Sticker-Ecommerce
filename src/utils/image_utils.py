"""图片工具函数"""

from pathlib import Path
from typing import Tuple, Optional
from PIL import Image

from src.core.exceptions import ImageError
from src.core.constants import (
    IMAGE_FORMAT,
    IMAGE_MAX_SIZE,
    IMAGE_MIN_SIZE,
    SUPPORTED_IMAGE_FORMATS,
)


def validate_image(filepath: str) -> bool:
    """验证图片文件
    
    Args:
        filepath: 图片文件路径
        
    Returns:
        bool: 是否有效
        
    Raises:
        ImageError: 图片无效时
    """
    file_path = Path(filepath)
    
    # 检查文件是否存在
    if not file_path.exists():
        raise ImageError(f"图片文件不存在: {filepath}", image_path=filepath)
    
    # 检查文件扩展名
    if file_path.suffix.lower() not in SUPPORTED_IMAGE_FORMATS:
        raise ImageError(
            f"不支持的图片格式: {file_path.suffix}",
            image_path=filepath
        )
    
    # 尝试打开图片
    try:
        with Image.open(filepath) as img:
            img.verify()
        return True
    except Exception as e:
        raise ImageError(f"图片文件损坏: {e}", image_path=filepath)


def resize_image(
    image: Image.Image,
    max_size: Tuple[int, int] = IMAGE_MAX_SIZE,
    min_size: Tuple[int, int] = IMAGE_MIN_SIZE
) -> Image.Image:
    """调整图片大小
    
    Args:
        image: PIL Image 对象
        max_size: 最大尺寸 (width, height)
        min_size: 最小尺寸 (width, height)
        
    Returns:
        Image.Image: 调整后的图片
    """
    width, height = image.size
    
    # 检查是否需要缩小
    if width > max_size[0] or height > max_size[1]:
        image.thumbnail(max_size, Image.Resampling.LANCZOS)
    
    # 检查是否需要放大
    elif width < min_size[0] or height < min_size[1]:
        scale = max(min_size[0] / width, min_size[1] / height)
        new_size = (int(width * scale), int(height * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
    
    return image


def save_image(
    image: Image.Image,
    filepath: str,
    format: str = IMAGE_FORMAT,
    quality: int = 95
) -> str:
    """保存图片
    
    Args:
        image: PIL Image 对象
        filepath: 保存路径
        format: 图片格式
        quality: 质量（1-100）
        
    Returns:
        str: 保存的文件路径
        
    Raises:
        ImageError: 保存失败时
    """
    try:
        file_path = Path(filepath)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 转换为 RGB 模式（如果需要）
        if format.upper() in ['JPEG', 'JPG'] and image.mode in ['RGBA', 'P']:
            image = image.convert('RGB')
        
        # 保存图片
        image.save(filepath, format=format, quality=quality)
        return str(file_path)
    except Exception as e:
        raise ImageError(f"保存图片失败: {e}", image_path=filepath, operation="save")


def load_image(filepath: str) -> Image.Image:
    """加载图片
    
    Args:
        filepath: 图片文件路径
        
    Returns:
        Image.Image: PIL Image 对象
        
    Raises:
        ImageError: 加载失败时
    """
    try:
        validate_image(filepath)
        return Image.open(filepath)
    except Exception as e:
        raise ImageError(f"加载图片失败: {e}", image_path=filepath, operation="load")


def get_image_size(filepath: str) -> Tuple[int, int]:
    """获取图片尺寸
    
    Args:
        filepath: 图片文件路径
        
    Returns:
        Tuple[int, int]: (width, height)
    """
    with Image.open(filepath) as img:
        return img.size


def convert_image_format(
    input_path: str,
    output_path: str,
    format: str = IMAGE_FORMAT
) -> str:
    """转换图片格式
    
    Args:
        input_path: 输入文件路径
        output_path: 输出文件路径
        format: 目标格式
        
    Returns:
        str: 输出文件路径
    """
    image = load_image(input_path)
    return save_image(image, output_path, format=format)
