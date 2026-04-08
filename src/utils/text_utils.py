"""文本工具函数"""

import re


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """清理文件名，移除非法字符
    
    Args:
        filename: 原始文件名
        max_length: 最大长度
        
    Returns:
        str: 清理后的文件名
    """
    # 移除非法字符
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    
    # 移除控制字符
    filename = re.sub(r'[\x00-\x1f\x7f]', '', filename)
    
    # 限制长度
    if len(filename) > max_length:
        name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
        max_name_length = max_length - len(ext) - 1
        filename = f"{name[:max_name_length]}.{ext}" if ext else name[:max_length]
    
    return filename.strip()


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """截断文本
    
    Args:
        text: 原始文本
        max_length: 最大长度
        suffix: 后缀
        
    Returns:
        str: 截断后的文本
    """
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix


def normalize_whitespace(text: str) -> str:
    """规范化空白字符
    
    Args:
        text: 原始文本
        
    Returns:
        str: 规范化后的文本
    """
    # 将多个空白字符替换为单个空格
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_keywords(text: str, min_length: int = 2) -> list:
    """提取关键词
    
    Args:
        text: 原始文本
        min_length: 最小关键词长度
        
    Returns:
        list: 关键词列表
    """
    # 移除标点符号
    text = re.sub(r'[^\w\s]', ' ', text)
    
    # 分词
    words = text.split()
    
    # 过滤短词
    keywords = [w for w in words if len(w) >= min_length]
    
    return keywords


def slugify(text: str, separator: str = "-") -> str:
    """将文本转换为 URL 友好的 slug
    
    Args:
        text: 原始文本
        separator: 分隔符
        
    Returns:
        str: slug 字符串
    """
    # 转小写
    text = text.lower()
    
    # 移除非字母数字字符
    text = re.sub(r'[^\w\s-]', '', text)
    
    # 将空白字符替换为分隔符
    text = re.sub(r'[\s_]+', separator, text)
    
    # 移除多余的分隔符
    text = re.sub(f'{separator}+', separator, text)
    
    return text.strip(separator)


def count_words(text: str) -> int:
    """统计单词数
    
    Args:
        text: 文本
        
    Returns:
        int: 单词数
    """
    return len(text.split())


def format_duration(seconds: float) -> str:
    """格式化时长
    
    Args:
        seconds: 秒数
        
    Returns:
        str: 格式化的时长字符串
    """
    if seconds < 60:
        return f"{seconds:.1f}秒"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}分钟"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}小时"
