"""
UI Module for AI Sticker Generator

提供 Gradio Web UI 和可复用组件
"""

from src.ui.gradio_app import StickerGeneratorUI, launch_app
from src.ui.components import (
    create_theme_input,
    create_count_slider,
    create_ratio_sliders,
    create_workers_slider,
    create_image_upload,
    create_variation_degree_radio,
    create_status_display,
    create_result_gallery,
    create_primary_button,
    create_custom_theme,
    CUSTOM_CSS
)

__all__ = [
    # 主应用
    "StickerGeneratorUI",
    "launch_app",
    
    # 组件
    "create_theme_input",
    "create_count_slider",
    "create_ratio_sliders",
    "create_workers_slider",
    "create_image_upload",
    "create_variation_degree_radio",
    "create_status_display",
    "create_result_gallery",
    "create_primary_button",
    "create_custom_theme",
    "CUSTOM_CSS",
]
