"""
Gradio Web UI - 贴纸包生成器
提供友好的网页界面用于生成科技主题贴纸包
"""
import gradio as gr
import json
from pathlib import Path
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from agents.sticker_pack_generator import StickerPackGenerator


class StickerPackWebUI:
    """贴纸包生成器 Web UI"""

    def __init__(self):
        self.generator = StickerPackGenerator()

    def generate_pack_ui(
        self,
        theme: str,
        total_count: int,
        text_ratio: float,
        element_ratio: float,
        hybrid_ratio: float,
        progress=gr.Progress()
    ):
        """Web UI 生成接口"""

        # 验证输入
        if not theme or not theme.strip():
            return "❌ 请输入主题", None, None

        if total_count < 10 or total_count > 100:
            return "❌ 数量应在 10-100 之间", None, None

        total_ratio = text_ratio + element_ratio + hybrid_ratio
        if abs(total_ratio - 1.0) > 0.01:
            return f"❌ 三种类型占比之和必须为1.0，当前为 {total_ratio:.2f}", None, None

        # 生成贴纸包
        progress(0, desc="初始化...")

        try:
            progress(0.1, desc="使用 Claude 生成创意...")
            result = self.generator.generate_pack(
                theme=theme.strip(),
                total_count=total_count,
                text_ratio=text_ratio,
                element_ratio=element_ratio,
                hybrid_ratio=hybrid_ratio
            )

            progress(1.0, desc="完成！")

            if not result.get("success", True):
                return f"❌ 生成失败: {result.get('error', '未知错误')}", None, None

            # 构建结果展示
            status_text = self._build_status_text(result)
            gallery_images = self._build_gallery(result)
            json_output = json.dumps(result, ensure_ascii=False, indent=2)

            return status_text, gallery_images, json_output

        except Exception as e:
            return f"❌ 生成过程出错: {str(e)}", None, None

    def _build_status_text(self, result: dict) -> str:
        """构建状态文本"""
        stats = result.get("statistics", {})

        status = f"""
## ✅ 生成完成！

**主题:** {result['theme']}
**总数:** {stats.get('total', 0)} 张
**成功:** {stats.get('success', 0)} 张
**失败:** {stats.get('failed', 0)} 张
**耗时:** {result.get('elapsed', 0):.1f}s

### 类型分布
- 📝 纯文本: {stats.get('by_type', {}).get('text', 0)} 张
- 🎨 元素: {stats.get('by_type', {}).get('element', 0)} 张
- 🔀 组合: {stats.get('by_type', {}).get('hybrid', 0)} 张

### 文件信息
- 总大小: {stats.get('total_size_kb', 0)} KB
- 平均生成时间: {stats.get('avg_generation_time', 0):.2f}s/张
- 结果文件: `{result.get('result_file', 'N/A')}`
"""
        return status

    def _build_gallery(self, result: dict) -> list:
        """构建图片画廊"""
        gallery_items = []

        for idea in result.get("ideas", []):
            if idea.get("success") and idea.get("image_path"):
                image_path = idea["image_path"]
                if Path(image_path).exists():
                    caption = f"{idea.get('title', 'Untitled')} ({idea.get('type', 'unknown')})"
                    gallery_items.append((image_path, caption))

        return gallery_items if gallery_items else None

    def create_interface(self):
        """创建 Gradio 界面"""

        with gr.Blocks(title="贴纸包生成器", theme=gr.themes.Soft()) as demo:
            gr.Markdown("""
# 🎨 贴纸包自动生成器

输入科技主题，自动生成30-50张精美贴纸！包含三种类型：
- 📝 **纯文本贴纸**: 简短有力的文字
- 🎨 **元素贴纸**: 主题相关的视觉元素
- 🔀 **组合贴纸**: 文字与元素的完美结合
            """)

            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("### 📋 生成配置")

                    theme_input = gr.Textbox(
                        label="科技主题",
                        placeholder="例如：AI人工智能、区块链、元宇宙、量子计算...",
                        lines=1
                    )

                    total_count_input = gr.Slider(
                        label="总数量",
                        minimum=10,
                        maximum=100,
                        value=40,
                        step=1
                    )

                    gr.Markdown("### 🎯 类型占比")

                    text_ratio_input = gr.Slider(
                        label="📝 纯文本占比",
                        minimum=0.0,
                        maximum=1.0,
                        value=0.3,
                        step=0.05
                    )

                    element_ratio_input = gr.Slider(
                        label="🎨 元素占比",
                        minimum=0.0,
                        maximum=1.0,
                        value=0.35,
                        step=0.05
                    )

                    hybrid_ratio_input = gr.Slider(
                        label="🔀 组合占比",
                        minimum=0.0,
                        maximum=1.0,
                        value=0.35,
                        step=0.05
                    )

                    generate_btn = gr.Button("🚀 开始生成", variant="primary", size="lg")

                    gr.Markdown("""
### 💡 使用提示
1. 输入具体的科技主题
2. 调整数量和类型占比（总和应为1.0）
3. 点击生成按钮
4. 等待1-3分钟完成生成
                    """)

                with gr.Column(scale=2):
                    gr.Markdown("### 📊 生成结果")

                    status_output = gr.Markdown(label="状态")

                    gallery_output = gr.Gallery(
                        label="生成的贴纸",
                        columns=4,
                        rows=3,
                        height="auto",
                        object_fit="contain"
                    )

                    with gr.Accordion("📄 详细数据 (JSON)", open=False):
                        json_output = gr.Code(
                            label="完整结果",
                            language="json",
                            lines=10
                        )

            # 预设主题示例
            gr.Markdown("### 🌟 主题示例")
            with gr.Row():
                example_themes = [
                    "AI人工智能",
                    "区块链",
                    "元宇宙",
                    "量子计算",
                    "云计算",
                    "5G通信"
                ]
                for theme in example_themes:
                    gr.Button(theme, size="sm").click(
                        lambda t=theme: t,
                        outputs=theme_input
                    )

            # 绑定生成事件
            generate_btn.click(
                fn=self.generate_pack_ui,
                inputs=[
                    theme_input,
                    total_count_input,
                    text_ratio_input,
                    element_ratio_input,
                    hybrid_ratio_input
                ],
                outputs=[status_output, gallery_output, json_output]
            )

        return demo


def launch_web_ui(share=False, server_port=7860):
    """启动 Web UI"""
    print("\n" + "="*60)
    print("  贴纸包生成器 - Web UI")
    print("="*60)

    ui = StickerPackWebUI()
    demo = ui.create_interface()

    demo.launch(
        share=share,
        server_port=server_port,
        server_name="0.0.0.0",
        show_error=True
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="贴纸包生成器 Web UI")
    parser.add_argument("--share", action="store_true", help="创建公共分享链接")
    parser.add_argument("--port", type=int, default=7860, help="服务器端口（默认7860）")

    args = parser.parse_args()

    launch_web_ui(share=args.share, server_port=args.port)
