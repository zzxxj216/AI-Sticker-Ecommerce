"""图片工具函数"""

import io
from pathlib import Path
from typing import Tuple, Optional, Union
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


def average_hash(source: Union[str, bytes, Image.Image], hash_size: int = 8) -> int:
    """计算图片的 average-hash（aHash），返回一个整数指纹。

    将图片转灰度、缩到 hash_size×hash_size，每个像素与均值比较得到一位，
    拼成 hash_size² 位的整数。用于判断两张图是否近似（感知哈希），无需额
    外依赖。

    Args:
        source: 图片路径、原始字节、或 PIL Image 对象
        hash_size: 边长（默认 8 → 64 位指纹）

    Returns:
        int: 感知哈希指纹
    """
    if isinstance(source, Image.Image):
        img = source
    elif isinstance(source, (bytes, bytearray)):
        img = Image.open(io.BytesIO(bytes(source)))
    else:
        img = Image.open(source)

    small = img.convert("L").resize((hash_size, hash_size), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for px in pixels:
        bits = (bits << 1) | (1 if px >= avg else 0)
    return bits


def hash_distance(a: int, b: int) -> int:
    """两个 average-hash 指纹之间的汉明距离（不同位的个数）。

    距离越小越相似；0 表示（近乎）相同。
    """
    return bin(a ^ b).count("1")


def read_dimensions(source: Union[str, bytes, Image.Image]) -> Tuple[int, int]:
    """返回图片 (width, height)，接受路径 / 原始字节 / PIL Image。"""
    if isinstance(source, Image.Image):
        return source.size
    if isinstance(source, (bytes, bytearray)):
        with Image.open(io.BytesIO(bytes(source))) as im:
            return im.size
    with Image.open(source) as im:
        return im.size


def compose_reference_grid(
    sources: list,
    *,
    cell: int = 512,
    pad: int = 12,
    max_side: int = 1024,
    bg: Tuple[int, int, int] = (255, 255, 255),
) -> bytes:
    """把多张图片按网格合并到一张白底画布，返回 PNG 字节。

    用于把一个 pack 的多张预览图（sticker sheet）本地聚合成单张「全套」参考图，
    再交给 image-to-image 模型重排成「合集」主图 —— 单图输入可避开多图编辑接口
    的超时，且 100% 取材于真实贴纸。

    Args:
        sources: 路径 / 字节 的列表（无法解码的项自动跳过）
        cell: 每张缩略图的最大边长
        pad: 网格内边距
        max_side: 合并后整图的最大边长（超出则等比缩小）
        bg: 画布底色（默认纯白）

    Returns:
        bytes: 合并图的 PNG 字节
    """
    import math

    imgs = []
    for s in sources:
        try:
            if isinstance(s, (bytes, bytearray)):
                im = Image.open(io.BytesIO(bytes(s)))
            else:
                im = Image.open(s)
            imgs.append(im.convert("RGB"))
        except Exception:
            continue
    if not imgs:
        raise ImageError("compose_reference_grid: 没有可解码的源图片")

    n = len(imgs)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    width = cols * cell + pad * (cols + 1)
    height = rows * cell + pad * (rows + 1)
    canvas = Image.new("RGB", (width, height), bg)
    for i, im in enumerate(imgs):
        thumb = im.copy()
        thumb.thumbnail((cell, cell), Image.Resampling.LANCZOS)
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad) + (cell - thumb.width) // 2
        y = pad + r * (cell + pad) + (cell - thumb.height) // 2
        canvas.paste(thumb, (x, y))

    if max(canvas.size) > max_side:
        canvas.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


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
