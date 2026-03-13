#!/usr/bin/env python3
"""
贴纸风格分析器 - 命令行工具
上传贴纸图片，分析风格并生成变种
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from agents.sticker_style_analyzer import StickerStyleAnalyzer


def interactive_mode():
    """交互式模式"""
    print("\n" + "="*60)
    print("  贴纸风格分析与变种生成器")
    print("="*60)
    print("\n功能：")
    print("  1. 分析贴纸风格特征")
    print("  2. 生成风格一致的变种贴纸")
    print("\n输入 'q' 退出\n")

    analyzer = StickerStyleAnalyzer()

    while True:
        image_path = input("贴纸图片路径 > ").strip()

        if image_path.lower() in ('q', 'quit', 'exit', '退出'):
         print("再见！")
            break

        if not image_path:
            print("请输入有效的图片路径\n")
            continue

        # 检查文件是否存在
        if not Path(image_path).exists():
            print(f"错误: 文件不存在 - {image_path}\n")
            continue

      # 询问变种数量
        variant_input = input("变种数量 (默认5) > ").strip()
      try:
            variant_count = int(variant_input) if variant_input else 5
            if variant_count < 1 or variant_count > 20:
         print("数量应在 1-20 之间，使用默认值 5")
                variant_count = 5
        except ValueError:
            print("无效数量，使用默认值 5")
         variant_count = 5

        # 询问变化程度
        print("\n变化程度:")
        print("  1. small  - 微调（保持90%相似度）")
        print("  2. medium - 适度（保持70%相似度）")
        print("  3. large  - 大幅（保持50%相似度）")
        degree_input = input("选择 (默认2) > ").strip()
        degree_map = {"1": "small", "2": "medium", "3": "large"}
        variation_degree = degree_map.get(degree_input, "medium")

        # 分析风格
        print()
        analysis_result = analyzer.analyze_sticker_style(
         image_path=image_path,
            analysis_language="zh"
        )

        if not analysis_result.get("success"):
            print(f"\n✗ 分析失败: {analysis_result.get('error', '未知错误')}\n")
          continue

        # 显示分析结果
        analysis = analysis_result.get("analysis", {})
        print(f"\n风格分析结果:")
        print(f"  视觉风格: {analysis.get('visual_style', 'N/A')}")
        print(f"  色彩方案: {analysis.get('color_scheme', {}).get('description', 'N/A')}")
        print(f"  主题类型: {analysis.get('theme', 'N/A')}")

        # 生成变种
        print()
        variant_result = analyzer.generate_variants(
            style_analysis=analysis_result,
            variant_count=variant_count,
        variation_degree=variation_degree
        )

        if variant_result.get("success"):
       print(f"\n✓ 成功生成 {variant_result['success_count']}/{variant_count} 个变种")
        else:
            print(f"\n✗ 变种生成失败: {variant_result.get('error', '未知错误')}")

      # 询问是否继续
        continue_input = input("\n继续分析？(y/n) > ").strip().lower()
        if continue_input not in ('y', 'yes', '是', ''):
            print("再见！")
            break
        print()


def quick_analyze(image_path: str, variant_count: int = 5, variation_degree: str = "medium"):
    """快速分析模式"""
    analyzer = StickerStyleAnalyzer()

    # 分析风格
    analysis_result = analyzer.analyze_sticker_style(
        image_path=image_path,
        analysis_language="zh"
    )

    if not analysis_result.get("success"):
      print(f"\n✗ 分析失败: {analysis_result.get('error', '未知错误')}")
        sys.exit(1)

    # 生成变种
    variant_result = analyzer.generate_variants(
        style_analysis=analysis_result,
        variant_count=variant_count,
        variation_degree=variation_degree
    )

    if variant_result.get("success"):
        print(f"\n✓ 分析与生成完成！")
        print(f"  原图: {image_path}")
        print(f"  变种: {variant_result['success_count']}/{variant_count}")
        print(f"  风格: {analysis_result.get('analysis', {}).get('visual_style', 'N/A')}")
    else:
        print(f"\n✗ 变种生成失败: {variant_result.get('error', '未知错误')}")
        sys.exit(1)


def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="贴纸风格分析与变种生成器 - 上传贴纸，AI 分析风格并生成变种",
    formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互式模式
  python style_analyzer_cli.py

  # 快速分析
  python style_analyzer_cli.py --image sticker.png --variants 5

  # 自定义变化程度
  python style_analyzer_cli.py --image sticker.png --variants 10 --degree large
        """
    )

    parser.add_argument(
        "--image",
        help="贴纸图片路径"
    )
    parser.add_argument(
        "--variants",
        type=int,
        default=5,
        help="变种数量（默认5，范围1-20）"
    )
    parser.add_argument(
        "--degree",
        choices=["small", "medium", "large"],
        default="medium",
        help="变化程度（默认medium）"
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="交互式模式"
    )

    args = parser.parse_args()

    # 交互式模式
    if args.interactive or not args.image:
        interactive_mode()
        return

    # 验证参数
    if not Path(args.image).exists():
        print(f"错误: 文件不存在 - {args.image}")
        sys.exit(1)

    if args.variants < 1 or args.variants > 20:
        print("错误: 变种数量应在 1-20 之间")
        sys.exit(1)

    # 快速分析模式
    quick_analyze(
        image_path=args.image,
        variant_count=args.variants,
        variation_degree=args.degree
    )


if __name__ == "__main__":
    main()
