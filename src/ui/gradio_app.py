"""
Gradio Web UI for AI Sticker Generator

提供三个主要功能：
1. 贴纸包生成 - 根据主题批量生成贴纸
2. 风格分析 - 分析现有贴纸的风格特征
3. 变种生成 - 基于现有贴纸生成变种
"""

import gradio as gr
from pathlib import Path
from typing import List, Tuple, Optional
import json
from datetime import datetime
import asyncio

from src.services.sticker import PackGenerator, StyleAnalyzer
from src.services.blog import BlogAgent
from src.core.constants import VariationDegree
from src.core.logger import get_logger
from src.core.config import Config

logger = get_logger(__name__)


class StickerGeneratorUI:
    """贴纸生成器 UI 主类"""
    
    def __init__(self):
        """初始化 UI"""
        self.config = Config()
        self.pack_generator = PackGenerator()
        # 默认使用 gemini 进行图片分析
        self.style_analyzer = StyleAnalyzer(analysis_model="claude")
        self.blog_agent = BlogAgent()

        # 输出目录
        self.output_dir = Path(self.config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Gradio UI 初始化完成")
    
    def generate_sticker_pack(
        self,
        theme: str,
        count: int,
        text_ratio: float,
        element_ratio: float,
        combined_ratio: float,
        max_workers: int,
        progress=gr.Progress()
    ) -> Tuple[List[str], str]:
        """
        生成贴纸包
        
        Args:
            theme: 主题
            count: 数量
            text_ratio: 纯文字比例
            element_ratio: 纯元素比例
            combined_ratio: 组合比例
            max_workers: 并发数
            progress: Gradio 进度对象
            
        Returns:
            (图片路径列表, 状态信息)
        """
        try:
            logger.info(f"开始生成贴纸包: theme={theme}, count={count}")
            
            # 进度回调
            def progress_callback(message: str, current: int, total: int):
                progress((current, total), desc=message)
            
            # 生成贴纸包
            pack = self.pack_generator.generate(
                theme=theme,
                count=count,
                text_ratio=text_ratio,
                element_ratio=element_ratio,
                combined_ratio=combined_ratio,
                max_workers=max_workers,
                progress_callback=progress_callback
            )
            
            # 收集成功的图片路径
            image_paths = [
                sticker.image_path 
                for sticker in pack.stickers 
                if sticker.image_path
            ]
            
            # 生成状态信息
            status = self._format_pack_status(pack)
            
            logger.info(f"贴纸包生成完成: {pack.success_count}/{pack.total_count}")
            return image_paths, status
            
        except Exception as e:
            error_msg = f"❌ 生成失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return [], error_msg
    
    def analyze_sticker_style(
        self,
        image: str,
        analysis_model: str,
        save_result: bool = True
    ) -> Tuple[str, str]:
        """
        分析贴纸风格

        Args:
            image: 图片路径
            analysis_model: 分析模型选择 ("Gemini" 或 "Claude")
            save_result: 是否保存结果

        Returns:
            (分析结果文本, 状态信息)
        """
        try:
            if not image:
                return "", "⚠️ 请上传图片"

            logger.info(f"开始分析风格: {image}, 使用模型: {analysis_model}")

            # 根据选择重新初始化 analyzer
            model_key = analysis_model.lower()
            if self.style_analyzer.analysis_model != model_key:
                self.style_analyzer = StyleAnalyzer(analysis_model=model_key)
                logger.info(f"切换分析模型为: {model_key}")

            # 分析风格
            analysis = self.style_analyzer.analyze(
                image_path=image,
                save_result=save_result
            )

            # 格式化分析结果
            result_text = self._format_analysis_result(analysis)
            status = f"✅ 分析完成 (使用 {analysis_model})"

            logger.info("风格分析完成")
            return result_text, status

        except Exception as e:
            error_msg = f"❌ 分析失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return "", error_msg
    
    def generate_variants(
        self,
        image: str,
        variant_count: int,
        variation_degree: str,
        max_workers: int,
        progress=gr.Progress()
    ) -> Tuple[List[str], str]:
        """
        生成变种贴纸
        
        Args:
            image: 原始图片路径
            variant_count: 变种数量
            variation_degree: 变化程度
            max_workers: 并发数
            progress: Gradio 进度对象
            
        Returns:
            (变种图片路径列表, 状态信息)
        """
        try:
            if not image:
                return [], "⚠️ 请上传图片"
            
            logger.info(f"开始生成变种: count={variant_count}, degree={variation_degree}")
            
            # 转换变化程度
            degree_map = {
                "轻微": VariationDegree.LOW,
                "中等": VariationDegree.MEDIUM,
                "较大": VariationDegree.HIGH
            }
            degree = degree_map.get(variation_degree, VariationDegree.MEDIUM)
            
            # 进度回调
            def progress_callback(message: str, current: int, total: int):
                progress((current, total), desc=message)
            
            # 一站式处理：分析 + 生成
            result = self.style_analyzer.analyze_and_generate(
                image_path=image,
                variant_count=variant_count,
                variation_degree=degree,
                max_workers=max_workers,
                progress_callback=progress_callback
            )
            
            # 收集变种图片路径
            variant_paths = result.get("variant_paths", [])
            
            # 生成状态信息
            success_count = result.get("success_count", 0)
            total_count = result.get("total_count", 0)
            duration = result.get("duration_seconds", 0)
            
            status = f"""✅ 变种生成完成
            
📊 统计信息：
- 成功: {success_count}/{total_count}
- 耗时: {duration:.1f}秒
- 平均: {duration/total_count if total_count > 0 else 0:.1f}秒/个
"""
            
            logger.info(f"变种生成完成: {success_count}/{total_count}")
            return variant_paths, status

        except Exception as e:
            error_msg = f"❌ 生成失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return [], error_msg

    def generate_blog_post(
        self,
        topic: str,
        keywords_str: str,
        word_count: int,
        image_count: int,
        progress=gr.Progress()
    ) -> Tuple[str, str, Optional[str]]:
        """
        生成博客文章

        Args:
            topic: 博客主题
            keywords_str: SEO关键词（逗号分隔）
            word_count: 目标字数
            image_count: 图片数量
            progress: Gradio 进度对象

        Returns:
            (进度文本, 预览内容, 下载文件路径)
        """
        try:
            logger.info(f"开始生成博客: topic={topic}, keywords={keywords_str}")

            # 解析关键词
            keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
            if not keywords:
                return "❌ 请输入至少一个关键词", "", None

            # 进度回调
            progress_text = ""
            def progress_callback(message: str):
                nonlocal progress_text
                progress_text = message
                progress(0, desc=message)

            # 运行异步生成
            result = asyncio.run(
                self.blog_agent.generate_blog_post(
                    topic=topic,
                    seo_keywords=keywords,
                    word_count_target=word_count,
                    image_count=image_count,
                    progress_callback=progress_callback
                )
            )

            if result.success and result.blog_post:
                # 读取生成的markdown文件
                with open(result.markdown_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                status = f"""✅ 博客生成完成

📊 统计信息：
- 主题: {topic}
- 字数: ~{word_count} 词
- 图片: {len(result.blog_post.image_paths)} 张
- 关键词: {', '.join(keywords)}

📁 保存位置：
{result.markdown_path}

💡 提示：
- 文章已保存为 Markdown 格式
- 图片已嵌入到文章中
- 可以直接复制到博客平台使用
"""
                return status, content, result.markdown_path
            else:
                error_msg = f"❌ 生成失败: {result.error}"
                logger.error(error_msg)
                return error_msg, "", None

        except Exception as e:
            error_msg = f"❌ 生成失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return error_msg, "", None

    def _format_pack_status(self, pack) -> str:
        """格式化贴纸包状态信息"""
        return f"""✅ 贴纸包生成完成

📊 统计信息：
- 主题: {pack.theme}
- 成功: {pack.success_count}/{pack.total_count}
- 失败: {pack.failed_count}
- 耗时: {pack.duration_seconds:.1f}秒
- 平均: {pack.duration_seconds/pack.total_count if pack.total_count > 0 else 0:.1f}秒/个

📁 保存位置：
{pack.output_dir}

💡 提示：
- 成功的贴纸已保存到输出目录
- 可以在"风格分析"标签页分析生成的贴纸
- 可以在"变种生成"标签页生成更多变种
"""
    
    def _format_analysis_result(self, analysis) -> str:
        """格式化分析结果"""
        return f"""🎨 风格分析结果

## 基本信息
- 分析时间: {analysis.analyzed_at}
- 图片路径: {analysis.image_path}

## 视觉风格
**{analysis.visual_style.value}**

{analysis.style_description}

## 色彩方案
**{analysis.color_palette.value}**

主要颜色: {', '.join(analysis.dominant_colors)}

## 设计元素
- 主要元素: {', '.join(analysis.key_elements)}
- 情感基调: {analysis.emotional_tone}
- 目标受众: {analysis.target_audience}

## 技术特征
- 复杂度: {analysis.complexity_level}
- 细节程度: {analysis.detail_level}

## 建议
{analysis.suggestions}

---
💡 可以使用此分析结果在"变种生成"标签页生成相似风格的变种
"""
    
    def create_app(self) -> gr.Blocks:
        """创建 Gradio 应用"""

        with gr.Blocks(title="AI 贴纸生成器") as app:
            
            gr.Markdown("""
            # 🎨 AI 贴纸生成器
            
            使用 AI 技术生成高质量贴纸，支持主题生成、风格分析和变种创作
            """)
            
            # 标签页 1: 贴纸包生成
            with gr.Tab("📦 贴纸包生成"):
                gr.Markdown("### 根据主题批量生成贴纸")
                
                with gr.Row():
                    with gr.Column(scale=1):
                        theme_input = gr.Textbox(
                            label="主题",
                            placeholder="例如：AI人工智能、可爱猫咪、科技未来...",
                            lines=2
                        )
                        
                        count_slider = gr.Slider(
                            minimum=1,
                            maximum=100,
                            value=40,
                            step=1,
                            label="生成数量"
                        )
                        
                        with gr.Accordion("高级选项", open=False):
                            text_ratio = gr.Slider(
                                minimum=0,
                                maximum=1,
                                value=0.3,
                                step=0.1,
                                label="纯文字比例"
                            )
                            element_ratio = gr.Slider(
                                minimum=0,
                                maximum=1,
                                value=0.4,
                                step=0.1,
                                label="纯元素比例"
                            )
                            combined_ratio = gr.Slider(
                                minimum=0,
                                maximum=1,
                                value=0.3,
                                step=0.1,
                                label="组合比例"
                            )
                            workers_slider = gr.Slider(
                                minimum=1,
                                maximum=5,
                                value=3,
                                step=1,
                                label="并发数"
                            )
                        
                        generate_btn = gr.Button("🚀 开始生成", variant="primary", size="lg")
                    
                    with gr.Column(scale=2):
                        pack_status = gr.Textbox(
                            label="状态",
                            lines=15,
                            max_lines=20
                        )
                        pack_gallery = gr.Gallery(
                            label="生成结果",
                            columns=4,
                            height="auto"
                        )
                
                # 绑定事件
                generate_btn.click(
                    fn=self.generate_sticker_pack,
                    inputs=[
                        theme_input,
                        count_slider,
                        text_ratio,
                        element_ratio,
                        combined_ratio,
                        workers_slider
                    ],
                    outputs=[pack_gallery, pack_status]
                )
            
            # 标签页 2: 风格分析
            with gr.Tab("🔍 风格分析"):
                gr.Markdown("### 分析贴纸的风格特征")

                with gr.Row():
                    with gr.Column(scale=1):
                        analyze_image = gr.Image(
                            label="上传贴纸",
                            type="filepath",
                            height=300
                        )

                        analysis_model_choice = gr.Radio(
                            choices=["Gemini", "Claude"],
                            value="Gemini",
                            label="分析模型",
                            info="Gemini: 免费稳定 | Claude: 需要官方API"
                        )

                        save_analysis = gr.Checkbox(
                            label="保存分析结果",
                            value=True
                        )

                        analyze_btn = gr.Button("🔍 开始分析", variant="primary", size="lg")

                    with gr.Column(scale=2):
                        analysis_result = gr.Textbox(
                            label="分析结果",
                            lines=25,
                            max_lines=30
                        )
                        analysis_status = gr.Textbox(
                            label="状态",
                            lines=2
                        )

                # 绑定事件
                analyze_btn.click(
                    fn=self.analyze_sticker_style,
                    inputs=[analyze_image, analysis_model_choice, save_analysis],
                    outputs=[analysis_result, analysis_status]
                )
            
            # 标签页 3: 变种生成
            with gr.Tab("🎭 变种生成"):
                gr.Markdown("### 基于现有贴纸生成变种")
                
                with gr.Row():
                    with gr.Column(scale=1):
                        variant_image = gr.Image(
                            label="上传原始贴纸",
                            type="filepath",
                            height=300
                        )
                        
                        variant_count = gr.Slider(
                            minimum=1,
                            maximum=20,
                            value=5,
                            step=1,
                            label="变种数量"
                        )
                        
                        variation_degree = gr.Radio(
                            choices=["轻微", "中等", "较大"],
                            value="中等",
                            label="变化程度"
                        )
                        
                        variant_workers = gr.Slider(
                            minimum=1,
                            maximum=5,
                            value=3,
                            step=1,
                            label="并发数"
                        )
                        
                        variant_btn = gr.Button("🎭 生成变种", variant="primary", size="lg")
                    
                    with gr.Column(scale=2):
                        variant_status = gr.Textbox(
                            label="状态",
                            lines=10
                        )
                        variant_gallery = gr.Gallery(
                            label="变种结果",
                            columns=4,
                            height="auto"
                        )
                
                # 绑定事件
                variant_btn.click(
                    fn=self.generate_variants,
                    inputs=[
                        variant_image,
                        variant_count,
                        variation_degree,
                        variant_workers
                    ],
                    outputs=[variant_gallery, variant_status]
                )

            # 标签页 4: Blog生成器
            with gr.Tab("📝 Blog生成器"):
                gr.Markdown("### 生成SEO优化的博客文章")

                with gr.Row():
                    with gr.Column(scale=1):
                        blog_topic = gr.Textbox(
                            label="Blog主题",
                            placeholder="例如: AI Stickers for Social Media Marketing",
                            lines=2
                        )

                        blog_keywords = gr.Textbox(
                            label="SEO关键词 (逗号分隔)",
                            placeholder="例如: ai stickers, custom stickers, sticker design",
                            lines=2
                        )

                        blog_word_count = gr.Slider(
                            minimum=800,
                            maximum=3500,
                            value=1800,
                            step=100,
                            label="目标字数"
                        )

                        blog_image_count = gr.Slider(
                            minimum=1,
                            maximum=10,
                            value=4,
                            step=1,
                            label="图片数量"
                        )

                        blog_generate_btn = gr.Button("📝 生成Blog", variant="primary", size="lg")

                    with gr.Column(scale=2):
                        blog_progress = gr.Textbox(
                            label="生成进度",
                            lines=12,
                            max_lines=15
                        )
                        blog_preview = gr.Markdown(
                            label="预览",
                            value="生成的博客内容将在这里显示..."
                        )
                        blog_download = gr.File(
                            label="下载Markdown文件"
                        )

                # 绑定事件
                blog_generate_btn.click(
                    fn=self.generate_blog_post,
                    inputs=[
                        blog_topic,
                        blog_keywords,
                        blog_word_count,
                        blog_image_count
                    ],
                    outputs=[blog_progress, blog_preview, blog_download]
                )

            # 页脚
            gr.Markdown("""
            ---
            💡 **使用提示**：
            - 贴纸包生成：输入主题后点击"开始生成"，等待 AI 创作
            - 风格分析：上传贴纸图片，AI 会分析其风格特征
            - 变种生成：上传贴纸，AI 会生成相似风格的变种
            - Blog生成器：输入主题和关键词，生成SEO优化的博客文章

            ⚙️ **技术栈**：Claude (创意+分析) + Gemini Imagen (图片生成)
            """)
        
        return app


def launch_app(
    server_name: str = "0.0.0.0",
    server_port: int = 7860,
    share: bool = False,
    debug: bool = False,
):
    """
    启动 Gradio 应用

    Args:
        server_name: 服务器地址
        server_port: 端口号（如果被占用会自动尝试其他端口）
        share: 是否创建公共链接
        debug: 是否启用调试模式
    """
    ui = StickerGeneratorUI()
    app = ui.create_app()

    logger.info(f"启动 Gradio 应用: {server_name}:{server_port}")

    # 启动应用，如果端口被占用会自动尝试其他端口
    app.launch(
        server_name=server_name,
        server_port=server_port,
        share=share,
        show_error=True,
        debug=debug,
        max_threads=40,
        theme=gr.themes.Soft(),  # Gradio 6.0 要求在 launch() 中设置
        css="""
        .gradio-container {
            max-width: 1200px !important;
        }
        """
    )


if __name__ == "__main__":
    launch_app()
