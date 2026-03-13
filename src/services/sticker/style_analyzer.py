"""风格分析服务

提供贴纸风格分析和变种生成功能：
- 图片风格分析（Claude Vision）
- 变种生成（Gemini）
- 批量变种生成
"""

import base64
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.core.config import config
from src.core.logger import get_logger
from src.core.exceptions import GenerationError, ValidationError, ImageError
from src.core.constants import VariationDegree, DEFAULT_VARIANT_COUNT, DEFAULT_VARIATION_DEGREE
from src.models.style import StyleAnalysis, StyleDimension, VariantConfig
from src.models.sticker import Sticker, StickerMetadata
from src.services.ai import ClaudeService, GeminiService
from src.services.ai import build_style_analysis_prompt, build_variant_generation_prompt
from src.utils import validate_image, generate_unique_id, save_json

logger = get_logger("service.style_analyzer")


class StyleAnalyzer:
    """风格分析器"""

    def __init__(
        self,
        claude_service: Optional[ClaudeService] = None,
        gemini_service: Optional[GeminiService] = None,
        output_dir: Optional[Path] = None,
        analysis_model: str = "claude"  # 新增：选择分析模型 "claude" 或 "gemini"
    ):
        """初始化分析器

        Args:
            claude_service: Claude 服务实例（可选）
            gemini_service: Gemini 服务实例（可选）
            output_dir: 输出目录（可选）
            analysis_model: 图片分析使用的模型，可选 "claude" 或 "gemini"（默认 "gemini"）
        """
        self.claude = claude_service or ClaudeService()
        self.gemini = gemini_service or GeminiService()
        self.output_dir = output_dir or Path(config.output_dir) / "style_analysis"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.analysis_model = analysis_model.lower()

        if self.analysis_model not in ["claude", "gemini"]:
            logger.warning(f"未知的分析模型: {analysis_model}，使用默认值 'gemini'")
            self.analysis_model = "gemini"

        logger.info("风格分析器初始化完成")
        logger.info(f"图片分析模型: {self.analysis_model}")
        logger.info(f"输出目录: {self.output_dir}")

    def analyze(
        self,
        image_path: str,
        save_result: bool = True
    ) -> StyleAnalysis:
        """分析图片风格

        Args:
            image_path: 图片路径
            save_result: 是否保存结果

        Returns:
            StyleAnalysis: 风格分析结果

        Raises:
            ImageError: 图片无效
            GenerationError: 分析失败
        """
        # 验证图片
        validate_image(image_path)

        logger.info(f"开始分析风格 - 图片: {image_path}")
        logger.info(f"使用模型: {self.analysis_model}")

        try:
            # 读取图片
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode()

            # 构建 Prompt
            prompt = build_style_analysis_prompt()

            # 根据选择的模型调用不同的服务
            if self.analysis_model == "claude":
                logger.debug("使用 Claude 进行图片分析")
                result = self.claude.analyze_image(
                    image_data=image_data,
                    prompt=prompt,
                    media_type="image/png",
                    max_tokens=4096
                )
            else:  # gemini
                logger.debug("使用 Gemini 进行图片分析")
                result = self.gemini.analyze_image(
                    image_data=image_data,
                    prompt=prompt,
                    media_type="image/png",
                    max_tokens=4096
                )

            # 解析结果
            analysis_data = self._parse_analysis_result(result["text"])

            # 创建 StyleAnalysis 对象
            analysis_id = generate_unique_id("analysis")
            analysis = StyleAnalysis(
                id=analysis_id,
                source_image=image_path,
                visual_style=StyleDimension(**analysis_data["visual_style"]),
                color_palette=StyleDimension(**analysis_data["color_palette"]),
                composition=StyleDimension(**analysis_data["composition"]),
                typography=StyleDimension(**analysis_data["typography"]),
                mood_emotion=StyleDimension(**analysis_data["mood_emotion"]),
                detail_level=StyleDimension(**analysis_data["detail_level"]),
                target_audience=StyleDimension(**analysis_data["target_audience"]),
                overall_description=analysis_data["overall_description"],
                key_features=analysis_data.get("key_features", [])
            )

            logger.info(f"风格分析完成 - ID: {analysis_id}")

            # 保存结果
            if save_result:
                self._save_analysis(analysis)

            return analysis

        except Exception as e:
            logger.error(f"风格分析失败: {e}")
            raise GenerationError(
                f"风格分析失败: {e}",
                stage="style_analysis"
            )

    def generate_variants(
        self,
        analysis: StyleAnalysis,
        count: int = DEFAULT_VARIANT_COUNT,
        degree: VariationDegree = DEFAULT_VARIATION_DEGREE,
        additional_instructions: Optional[str] = None,
        preserve_elements: Optional[List[str]] = None,
        avoid_elements: Optional[List[str]] = None,
        max_workers: int = 3,
        progress_callback: Optional[callable] = None
    ) -> List[Sticker]:
        """生成变种贴纸

        Args:
            analysis: 风格分析结果
            count: 变种数量
            degree: 变化程度
            additional_instructions: 额外指令
            preserve_elements: 保留元素
            avoid_elements: 避免元素
            max_workers: 最大并发数
            progress_callback: 进度回调函数

        Returns:
            List[Sticker]: 变种贴纸列表

        Raises:
            ValidationError: 参数验证失败
            GenerationError: 生成失败
        """
        # 验证参数
        if count < 1 or count > 20:
            raise ValidationError(
                "变种数量必须在 1-20 之间",
                field="count",
                value=count
            )

        if isinstance(degree, str):
            degree = VariationDegree(degree)

        logger.info(
            f"开始生成变种 - "
            f"数量: {count}, "
            f"变化程度: {degree.value}"
        )

        # 创建配置
        variant_config = VariantConfig(
            style_analysis=analysis,
            variation_degree=degree,
            variant_count=count,
            additional_instructions=additional_instructions,
            preserve_elements=preserve_elements or [],
            avoid_elements=avoid_elements or []
        )

        # 创建输出目录
        today = datetime.now().strftime("%Y%m%d")
        session = datetime.now().strftime("%H%M%S")
        image_dir = Path(config.output_dir) / "images" / today / f"variants_{session}"
        image_dir.mkdir(parents=True, exist_ok=True)

        # 生成变种
        stickers = []
        results_dict = {}

        def _gen_one(idx: int) -> tuple:
            """生成单个变种"""
            # 构建 Prompt
            prompt = build_variant_generation_prompt(
                style_analysis=analysis.to_dict(),
                variation_degree=degree,
                variant_index=idx,
                total_variants=count,
                additional_instructions=additional_instructions
            )

            # 生成 image_prompt
            try:
                result = self.claude.generate(
                    prompt=prompt,
                    max_tokens=1024,
                    temperature=0.8
                )
                image_prompt = result["text"].strip()
            except Exception as e:
                logger.error(f"变种 {idx} Prompt 生成失败: {e}")
                return idx, None

            # 创建贴纸对象
            sticker_id = f"variant_{analysis.id}_{idx:03d}"
            sticker = Sticker(
                id=sticker_id,
                type="element",  # 默认类型
                prompt=image_prompt,
                description=f"变种 {idx}/{count}",
                metadata=StickerMetadata(
                    source="style_analyzer",
                    parent_id=analysis.id,
                    tags=[f"variant_{degree.value}"]
                )
            )

            # 生成图片
            filename = f"variant_{idx:03d}.png"
            output_path = image_dir / filename

            logger.debug(f"({idx}/{count}) 生成变种")

            try:
                gen_result = self.gemini.generate_image(
                    prompt=image_prompt,
                    reference_image=analysis.source_image,
                    output_path=output_path
                )

                if gen_result["success"]:
                    sticker.mark_success(str(output_path))
                    logger.debug(f"({idx}/{count}) ✓ 变种生成成功")
                else:
                    sticker.mark_failed(gen_result.get("error", "未知错误"))
                    logger.warning(f"({idx}/{count}) ✗ 变种生成失败")

            except Exception as e:
                logger.error(f"({idx}/{count}) 生成失败: {e}")
                sticker.mark_failed(str(e))

            return idx, sticker

        # 并发生成
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_gen_one, i): i for i in range(1, count + 1)}

            for future in as_completed(futures):
                idx, sticker = future.result()
                if sticker:
                    results_dict[idx] = sticker

                # 更新进度
                completed += 1
                if progress_callback:
                    progress_callback(
                        completed,
                        count,
                        f"生成变种 {completed}/{count}"
                    )

        # 按索引排序
        stickers = [results_dict[i] for i in sorted(results_dict) if i in results_dict]

        success_count = sum(1 for s in stickers if s.status == "success")
        logger.info(f"变种生成完成 - 成功: {success_count}/{count}")

        return stickers

    def analyze_and_generate(
        self,
        image_path: str,
        variant_count: int = DEFAULT_VARIANT_COUNT,
        variation_degree: VariationDegree = DEFAULT_VARIATION_DEGREE,
        **kwargs
    ) -> Dict[str, Any]:
        """分析风格并生成变种（一站式）

        Args:
            image_path: 图片路径
            variant_count: 变种数量
            variation_degree: 变化程度
            **kwargs: 其他参数传递给 generate_variants

        Returns:
            Dict: 包含分析结果和变种列表
        """
        logger.info(f"开始一站式处理 - 图片: {image_path}")

        start_time = time.time()

        # 分析风格
        analysis = self.analyze(image_path)

        # 生成变种
        variants = self.generate_variants(
            analysis=analysis,
            count=variant_count,
            degree=variation_degree,
            **kwargs
        )

        elapsed = time.time() - start_time
        success_count = sum(1 for v in variants if v.status == "success")

        result = {
            "analysis": analysis,
            "variants": variants,
            "success_count": success_count,
            "total_count": len(variants),
            "elapsed": elapsed
        }

        logger.info(
            f"一站式处理完成 - "
            f"成功: {success_count}/{len(variants)}, "
            f"耗时: {elapsed:.1f}s"
        )

        return result

    def _parse_analysis_result(self, text: str) -> Dict[str, Any]:
        """解析分析结果

        Args:
            text: Claude 返回的文本

        Returns:
            Dict: 解析后的数据

        Raises:
            GenerationError: 解析失败
        """
        import json
        import re

        # 记录原始返回文本（用于调试）
        logger.debug(f"Claude 返回文本长度: {len(text)}")
        logger.debug(f"Claude 返回文本前500字符: {text[:500]}")

        # # 检查是否包含拒绝消息
        # refusal_keywords = [
        #     "I'm not designed for",
        #     "I can't",
        #     "I cannot",
        #     "not capable of",
        #     "unable to",
        #     "don't have the ability"
        # ]
        #
        # for keyword in refusal_keywords:
        #     if keyword.lower() in text.lower():
        #         logger.error(f"检测到 Claude 拒绝响应，关键词: {keyword}")
        #         logger.error(f"完整响应: {text}")
        #         raise GenerationError(
        #             f"Claude 拒绝分析图片。返回内容: {text[:200]}",
        #             stage="parse_analysis"
        #         )

        # 提取 JSON
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
            logger.debug("从 ```json``` 代码块中提取 JSON")
        else:
            json_match = re.search(r'(\{.*\})', text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                logger.debug("从文本中提取 JSON 对象")
            else:
                logger.error(f"未找到 JSON 内容，原始文本: {text}")
                raise GenerationError(
                    f"响应中未找到 JSON 格式内容。返回文本: {text[:500]}",
                    stage="parse_analysis"
                )

        try:
            data = json.loads(json_str)

            # 验证必需字段
            required_fields = [
                "visual_style", "color_palette", "composition",
                "typography", "mood_emotion", "detail_level",
                "target_audience", "overall_description"
            ]

            for field in required_fields:
                if field not in data:
                    raise GenerationError(
                        f"分析结果缺少字段: {field}",
                        stage="parse_analysis"
                    )

            # 为每个维度添加 name 字段（如果缺失）
            dimension_fields = [
                ("visual_style", "视觉风格"),
                ("color_palette", "色彩方案"),
                ("composition", "构图布局"),
                ("typography", "文字排版"),
                ("mood_emotion", "情绪氛围"),
                ("detail_level", "细节程度"),
                ("target_audience", "目标受众")
            ]

            for field_key, field_name in dimension_fields:
                if field_key in data and isinstance(data[field_key], dict):
                    if "name" not in data[field_key]:
                        data[field_key]["name"] = field_name

            return data

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            logger.debug(f"原始文本: {text[:500]}")
            raise GenerationError(
                f"分析结果解析失败: {e}",
                stage="parse_analysis"
            )

    def _save_analysis(self, analysis: StyleAnalysis) -> None:
        """保存分析结果

        Args:
            analysis: 风格分析对象
        """
        filename = f"{analysis.id}.json"
        filepath = self.output_dir / filename

        save_json(analysis.to_dict(), str(filepath))

        logger.info(f"分析结果已保存: {filepath}")

    def get_analyzer_info(self) -> Dict[str, Any]:
        """获取分析器信息

        Returns:
            Dict: 分析器信息
        """
        return {
            "analysis_model": self.analysis_model,
            "claude_model": self.claude.model,
            "gemini_model": self.gemini.model,
            "output_dir": str(self.output_dir)
        }
