"""
UI Components for Gradio Interface

可复用的 UI 组件，用于构建统一的界面风格
"""

import gradio as gr
from typing import Callable, Optional, List, Dict, Any


def create_theme_input(
    label: str = "主题",
    placeholder: str = "例如：AI人工智能、可爱猫咪、科技未来...",
    lines: int = 2
) -> gr.Textbox:
    """
    创建主题输入组件
    
    Args:
        label: 标签文本
        placeholder: 占位符文本
        lines: 行数
        
    Returns:
        Gradio Textbox 组件
    """
    return gr.Textbox(
        label=label,
        placeholder=placeholder,
        lines=lines
    )


def create_count_slider(
    minimum: int = 1,
    maximum: int = 100,
    value: int = 40,
    label: str = "生成数量"
) -> gr.Slider:
    """
    创建数量滑块组件
    
    Args:
        minimum: 最小值
        maximum: 最大值
        value: 默认值
        label: 标签文本
        
    Returns:
        Gradio Slider 组件
    """
    return gr.Slider(
        minimum=minimum,
        maximum=maximum,
        value=value,
        step=1,
        label=label
    )


def create_ratio_sliders() -> Dict[str, gr.Slider]:
    """
    创建比例滑块组（文字、元素、组合）
    
    Returns:
        包含三个滑块的字典
    """
    return {
        "text": gr.Slider(
            minimum=0,
            maximum=1,
            value=0.3,
            step=0.1,
            label="纯文字比例",
            info="只包含文字的贴纸比例"
        ),
        "element": gr.Slider(
            minimum=0,
            maximum=1,
            value=0.4,
            step=0.1,
            label="纯元素比例",
            info="只包含图形元素的贴纸比例"
        ),
        "combined": gr.Slider(
            minimum=0,
            maximum=1,
            value=0.3,
            step=0.1,
            label="组合比例",
            info="文字+元素组合的贴纸比例"
        )
    }


def create_workers_slider(
    minimum: int = 1,
    maximum: int = 5,
    value: int = 3,
    label: str = "并发数"
) -> gr.Slider:
    """
    创建并发数滑块
    
    Args:
        minimum: 最小值
        maximum: 最大值
        value: 默认值
        label: 标签文本
        
    Returns:
        Gradio Slider 组件
    """
    return gr.Slider(
        minimum=minimum,
        maximum=maximum,
        value=value,
        step=1,
        label=label,
        info="同时生成的贴纸数量，越大越快但消耗更多资源"
    )


def create_image_upload(
    label: str = "上传图片",
    height: int = 300
) -> gr.Image:
    """
    创建图片上传组件
    
    Args:
        label: 标签文本
        height: 高度
        
    Returns:
        Gradio Image 组件
    """
    return gr.Image(
        label=label,
        type="filepath",
        height=height
    )


def create_variation_degree_radio(
    label: str = "变化程度",
    value: str = "中等"
) -> gr.Radio:
    """
    创建变化程度选择组件
    
    Args:
        label: 标签文本
        value: 默认值
        
    Returns:
        Gradio Radio 组件
    """
    return gr.Radio(
        choices=["轻微", "中等", "较大"],
        value=value,
        label=label,
        info="变化程度越大，生成的变种与原图差异越大"
    )


def create_status_display(
    label: str = "状态",
    lines: int = 10,
    max_lines: Optional[int] = None
) -> gr.Textbox:
    """
    创建状态显示组件
    
    Args:
        label: 标签文本
        lines: 行数
        max_lines: 最大行数
        
    Returns:
        Gradio Textbox 组件
    """
    return gr.Textbox(
        label=label,
        lines=lines,
        max_lines=max_lines or lines + 5,
        interactive=False
    )


def create_result_gallery(
    label: str = "生成结果",
    columns: int = 4,
    height: str = "auto"
) -> gr.Gallery:
    """
    创建结果展示画廊组件
    
    Args:
        label: 标签文本
        columns: 列数
        height: 高度
        
    Returns:
        Gradio Gallery 组件
    """
    return gr.Gallery(
        label=label,
        columns=columns,
        height=height,
        object_fit="contain"
    )


def create_primary_button(
    text: str,
    size: str = "lg"
) -> gr.Button:
    """
    创建主要操作按钮
    
    Args:
        text: 按钮文本
        size: 按钮大小
        
    Returns:
        Gradio Button 组件
    """
    return gr.Button(
        text,
        variant="primary",
        size=size
    )


def create_advanced_options_accordion(
    components: List[gr.Component],
    open: bool = False
) -> gr.Accordion:
    """
    创建高级选项折叠面板
    
    Args:
        components: 包含的组件列表
        open: 是否默认展开
        
    Returns:
        Gradio Accordion 组件
    """
    with gr.Accordion("⚙️ 高级选项", open=open) as accordion:
        for component in components:
            component.render()
    
    return accordion


def create_info_markdown(text: str) -> gr.Markdown:
    """
    创建信息提示 Markdown 组件
    
    Args:
        text: Markdown 文本
        
    Returns:
        Gradio Markdown 组件
    """
    return gr.Markdown(f"💡 **提示**: {text}")


def create_warning_markdown(text: str) -> gr.Markdown:
    """
    创建警告提示 Markdown 组件
    
    Args:
        text: Markdown 文本
        
    Returns:
        Gradio Markdown 组件
    """
    return gr.Markdown(f"⚠️ **注意**: {text}")


def create_success_markdown(text: str) -> gr.Markdown:
    """
    创建成功提示 Markdown 组件
    
    Args:
        text: Markdown 文本
        
    Returns:
        Gradio Markdown 组件
    """
    return gr.Markdown(f"✅ **成功**: {text}")


def create_error_markdown(text: str) -> gr.Markdown:
    """
    创建错误提示 Markdown 组件
    
    Args:
        text: Markdown 文本
        
    Returns:
        Gradio Markdown 组件
    """
    return gr.Markdown(f"❌ **错误**: {text}")


# 预定义的 CSS 样式
CUSTOM_CSS = """
.gradio-container {
    max-width: 1200px !important;
    margin: auto;
}

.primary-button {
    background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
    border: none;
    color: white;
    font-weight: bold;
}

.status-box {
    font-family: monospace;
    background-color: #f5f5f5;
    border-radius: 8px;
    padding: 12px;
}

.result-gallery {
    border-radius: 12px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}
"""


# 预定义的主题
def create_custom_theme() -> gr.themes.Base:
    """
    创建自定义主题
    
    Returns:
        Gradio 主题对象
    """
    return gr.themes.Soft(
        primary_hue="purple",
        secondary_hue="blue",
        neutral_hue="slate",
        font=["Inter", "sans-serif"]
    )
