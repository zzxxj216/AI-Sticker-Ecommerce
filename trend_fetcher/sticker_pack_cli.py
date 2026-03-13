#!/usr/bin/env python3
"""
贴纸包生成器 - 命令行工具
快速生成科技主题贴纸包
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from agents.sticker_pack_generator import StickerPackGenerator


def interactive_mode():
    """交互式模式"""
    print("\n" + "="*60)
    print("  贴纸包自动生成器")
    print("="*60)
    print("\n请输入科技主题（例如：AI人工智能、区块链、元宇宙、量子计算）")
    print("输入 'q' 退出\n")

    generator = StickerPackGenerator()

    while True:
        theme = input("主题 > ").strip()

        if theme.lower() in ('q', 'quit', 'exit', '退出'):
            print("再见！")
            break

        if not theme:
            print("请输入有效的主题\n")
            continue

        # 询问数量
        count_input = input(f"数量 (默认40) > ").strip()
        try:
            count = int(count_input) if count_input else 40
            if count < 10 or count > 100:
                print("数量应在 10-100 之间，使用默认值 40")
                count = 40
        except ValueError:
            print("无效数量，使用默认值 40")
            count = 40

        # 生成贴纸包
        print()
        result = generator.generate_pack(theme=theme, total_count=count)

        if result.get("success", True):
            print(f"\n✓ 已保存到: {result['result_file']}\n")
        else:
            print(f"\n✗ 生成失败: {result.get('error', '未知错误')}\n")

        # 询问是否继续
        continue_input = input("继续生成？(y/n) > ").strip().lower()
        if continue_input not in ('y', 'yes', '是', ''):
            print("再见！")
            break
        print()


def quick_generate(theme: str, count: int = 40):
    """快速生成模式"""
    generator = StickerPackGenerator()
    result = generator.generate_pack(theme=theme, total_count=count)

    if result.get("success", True):
        print(f"\n✓ 生成成功！")
        print(f"  主题: {theme}")
        print(f"  数量: {result['total_count']}")
        print(f"  成功: {result['success_count']}")
        print(f"  文件: {result['result_file']}")
    else:
        print(f"\n✗ 生成失败: {result.get('error', '未知错误')}")
        sys.exit(1)


def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="贴纸包自动生成器 - 根据科技主题生成30-50张贴纸",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互式模式
  python sticker_pack_cli.py

  # 快速生成
  python sticker_pack_cli.py --theme "AI人工智能" --count 40

  # 自定义比例
  python sticker_pack_cli.py --theme "区块链" --count 50 --text-ratio 0.4 --element-ratio 0.3 --hybrid-ratio 0.3
        """
    )

    parser.add_argument(
        "--theme",
        help="科技主题（如：AI人工智能、区块链、元宇宙）"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=40,
        help="总贴纸数量（默认40，范围10-100）"
    )
    parser.add_argument(
        "--text-ratio",
        type=float,
        default=0.3,
        help="纯文本贴纸占比（默认0.3）"
    )
    parser.add_argument(
        "--element-ratio",
        type=float,
        default=0.35,
        help="元素贴纸占比（默认0.35）"
    )
    parser.add_argument(
        "--hybrid-ratio",
        type=float,
        default=0.35,
        help="组合贴纸占比（默认0.35）"
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="交互式模式"
    )

    args = parser.parse_args()

    # 交互式模式
    if args.interactive or not args.theme:
        interactive_mode()
        return

    # 验证参数
    if args.count < 10 or args.count > 100:
        print("错误: 数量应在 10-100 之间")
        sys.exit(1)

    total_ratio = args.text_ratio + args.element_ratio + args.hybrid_ratio
    if abs(total_ratio - 1.0) > 0.01:
        print(f"错误: 三种类型占比之和必须为1.0，当前为 {total_ratio}")
        sys.exit(1)

    # 快速生成模式
    generator = StickerPackGenerator()
    result = generator.generate_pack(
        theme=args.theme,
        total_count=args.count,
        text_ratio=args.text_ratio,
        element_ratio=args.element_ratio,
        hybrid_ratio=args.hybrid_ratio
    )

    if result.get("success", True):
        print(f"\n✓ 生成成功！")
        print(f"  结果文件: {result['result_file']}")
    else:
        print(f"\n✗ 生成失败: {result.get('error', '未知错误')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
