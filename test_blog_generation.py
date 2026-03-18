"""
Blog Generation Test Script
测试博客生成功能
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.services.blog import BlogAgent
from src.core.logger import get_logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
logger = get_logger(__name__)


async def test_blog_generation():
    """测试博客生成功能"""

    # 测试数据
    topic = "Top American Traditional Tattoo Sticker Ideas for Your Laptop and Water Bottle"
    keywords = [
        "American Traditional Tattoo stickers",
        "American Traditional Tattoo sticker pack",
        "American Traditional Tattoo vinyl stickers"
    ]
    word_count = 1800
    image_count = 4

    console.print(Panel.fit(
        "[bold cyan]Blog Generation Test[/bold cyan]\n"
        f"Topic: {topic}\n"
        f"Keywords: {', '.join(keywords)}\n"
        f"Target Words: {word_count}\n"
        f"Images: {image_count}",
        title="Test Configuration",
        border_style="cyan"
    ))

    try:
        # 初始化 BlogAgent
        console.print("\n[yellow]Initializing BlogAgent...[/yellow]")
        agent = BlogAgent()
        console.print("[green]✓ BlogAgent initialized[/green]")

        # 进度回调函数
        progress_messages = []

        def progress_callback(message: str):
            progress_messages.append(message)
            console.print(f"[cyan]→ {message}[/cyan]")

        # 生成博客
        console.print("\n[bold yellow]Starting blog generation...[/bold yellow]\n")

        result = await agent.generate_blog_post(
            topic=topic,
            seo_keywords=keywords,
            word_count_target=word_count,
            image_count=image_count,
            progress_callback=progress_callback
        )

        # 显示结果
        console.print("\n" + "="*80 + "\n")

        if result.success:
            console.print(Panel.fit(
                "[bold green]✓ Blog Generation Successful![/bold green]",
                border_style="green"
            ))

            # 创建结果表格
            table = Table(title="Generation Results", show_header=True, header_style="bold magenta")
            table.add_column("Property", style="cyan", width=25)
            table.add_column("Value", style="white")

            if result.blog_post:
                blog = result.blog_post

                # 基本信息
                table.add_row("Topic", blog.topic)
                table.add_row("Title", blog.seo_metadata.title)
                table.add_row("Meta Description", blog.seo_metadata.meta_description[:80] + "...")

                # 内容统计
                word_count_actual = len(blog.full_content.split())
                table.add_row("Word Count", f"{word_count_actual} words")
                table.add_row("Target Word Count", f"{word_count} words")
                table.add_row("Word Count Match",
                             "✓ Yes" if 1500 <= word_count_actual <= 2000 else "✗ No")

                # 图片信息
                table.add_row("Images Generated", f"{len(blog.image_paths)}/{image_count}")
                table.add_row("Images Success Rate",
                             f"{len(blog.image_paths)/image_count*100:.1f}%")

                # SEO信息
                table.add_row("Primary Keyword", blog.seo_keywords[0] if blog.seo_keywords else "N/A")
                table.add_row("Total Keywords", str(len(blog.seo_keywords)))

                # 文件信息
                table.add_row("Output File", result.markdown_path)

                # 检查文件是否存在
                if Path(result.markdown_path).exists():
                    file_size = Path(result.markdown_path).stat().st_size
                    table.add_row("File Size", f"{file_size:,} bytes ({file_size/1024:.1f} KB)")
                    table.add_row("File Exists", "✓ Yes")
                else:
                    table.add_row("File Exists", "✗ No")

            table.add_row("Markdown Path", result.markdown_path)

            console.print(table)

            # 显示生成的图片路径
            if result.blog_post and result.blog_post.image_paths:
                console.print("\n[bold]Generated Images:[/bold]")
                for i, img_path in enumerate(result.blog_post.image_paths, 1):
                    exists = "✓" if Path(img_path).exists() else "✗"
                    console.print(f"  {exists} Image {i}: {img_path}")

            # 显示内容预览
            if result.blog_post:
                console.print("\n[bold]Content Preview (first 500 chars):[/bold]")
                preview = result.blog_post.full_content[:500].replace("\n", " ")
                console.print(f"[dim]{preview}...[/dim]")

            # SEO检查
            console.print("\n[bold]SEO Validation:[/bold]")
            seo_checks = []

            if result.blog_post:
                # 标题长度检查
                title_len = len(result.blog_post.seo_metadata.title)
                seo_checks.append(("Title Length (50-60 chars)",
                                 f"{title_len} chars",
                                 50 <= title_len <= 60))

                # Meta描述长度检查
                desc_len = len(result.blog_post.seo_metadata.meta_description)
                seo_checks.append(("Meta Description (150-160 chars)",
                                 f"{desc_len} chars",
                                 150 <= desc_len <= 160))

                # 关键词数量检查
                keyword_count = len(result.blog_post.seo_keywords)
                seo_checks.append(("Keywords Count (3-5)",
                                 f"{keyword_count} keywords",
                                 3 <= keyword_count <= 5))

                # 字数检查
                word_count_actual = len(result.blog_post.full_content.split())
                seo_checks.append(("Word Count (1500-2000)",
                                 f"{word_count_actual} words",
                                 1500 <= word_count_actual <= 2000))

                # 图片数量检查
                img_count = len(result.blog_post.image_paths)
                seo_checks.append(("Image Count (3-5)",
                                 f"{img_count} images",
                                 3 <= img_count <= 5))

            for check_name, value, passed in seo_checks:
                status = "[green]✓[/green]" if passed else "[red]✗[/red]"
                console.print(f"  {status} {check_name}: {value}")

            # 显示进度消息摘要
            console.print(f"\n[bold]Progress Steps:[/bold] {len(progress_messages)} steps completed")

            # 测试总结
            console.print("\n" + "="*80)
            console.print(Panel.fit(
                "[bold green]Test Completed Successfully![/bold green]\n"
                f"Output: {result.markdown_path}",
                title="Test Summary",
                border_style="green"
            ))

        else:
            console.print(Panel.fit(
                f"[bold red]✗ Blog Generation Failed[/bold red]\n"
                f"Error: {result.error}",
                border_style="red"
            ))

            console.print("\n[bold]Progress Messages:[/bold]")
            for msg in progress_messages:
                console.print(f"  • {msg}")

    except Exception as e:
        logger.error(f"Test failed with exception: {str(e)}", exc_info=True)
        console.print(Panel.fit(
            f"[bold red]✗ Test Failed with Exception[/bold red]\n"
            f"Error: {str(e)}",
            border_style="red"
        ))
        raise


def main():
    """主函数"""
    console.print("\n[bold blue]Starting Blog Generation Test...[/bold blue]\n")

    try:
        asyncio.run(test_blog_generation())
    except KeyboardInterrupt:
        console.print("\n[yellow]Test interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Test failed: {str(e)}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
