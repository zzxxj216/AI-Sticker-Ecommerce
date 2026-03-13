"""文件工具函数"""

import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional

from src.core.exceptions import FileError


def ensure_dir(path: str) -> Path:
    """确保目录存在
    
    Args:
        path: 目录路径
        
    Returns:
        Path: 目录路径对象
    """
    dir_path = Path(path)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def get_timestamp(format: str = "%Y%m%d_%H%M%S") -> str:
    """获取时间戳字符串
    
    Args:
        format: 时间格式
        
    Returns:
        str: 时间戳字符串
    """
    return datetime.now().strftime(format)


def generate_unique_id(prefix: str = "") -> str:
    """生成唯一 ID
    
    Args:
        prefix: ID 前缀
        
    Returns:
        str: 唯一 ID
    """
    unique_id = str(uuid.uuid4())[:8]
    if prefix:
        return f"{prefix}_{unique_id}"
    return unique_id


def save_json(data: Dict[str, Any], filepath: str, indent: int = 2) -> None:
    """保存 JSON 文件
    
    Args:
        data: 数据字典
        filepath: 文件路径
        indent: 缩进空格数
        
    Raises:
        FileError: 保存失败时
    """
    try:
        file_path = Path(filepath)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 自定义 JSON 编码器处理 datetime
        class DateTimeEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                return super().default(obj)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=indent, cls=DateTimeEncoder)
    except Exception as e:
        raise FileError(f"保存 JSON 文件失败: {e}", filepath=filepath)


def load_json(filepath: str) -> Dict[str, Any]:
    """加载 JSON 文件
    
    Args:
        filepath: 文件路径
        
    Returns:
        Dict[str, Any]: 数据字典
        
    Raises:
        FileError: 加载失败时
    """
    try:
        file_path = Path(filepath)
        if not file_path.exists():
            raise FileError(f"文件不存在: {filepath}", filepath=filepath)
        
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise FileError(f"JSON 解析失败: {e}", filepath=filepath)
    except Exception as e:
        raise FileError(f"加载 JSON 文件失败: {e}", filepath=filepath)


def get_file_size(filepath: str) -> int:
    """获取文件大小（字节）
    
    Args:
        filepath: 文件路径
        
    Returns:
        int: 文件大小
    """
    return Path(filepath).stat().st_size


def file_exists(filepath: str) -> bool:
    """检查文件是否存在
    
    Args:
        filepath: 文件路径
        
    Returns:
        bool: 是否存在
    """
    return Path(filepath).exists()


def delete_file(filepath: str) -> None:
    """删除文件
    
    Args:
        filepath: 文件路径
    """
    file_path = Path(filepath)
    if file_path.exists():
        file_path.unlink()


def list_files(directory: str, pattern: str = "*") -> list:
    """列出目录中的文件
    
    Args:
        directory: 目录路径
        pattern: 文件模式（如 "*.png"）
        
    Returns:
        list: 文件路径列表
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return []
    
    return [str(f) for f in dir_path.glob(pattern) if f.is_file()]
