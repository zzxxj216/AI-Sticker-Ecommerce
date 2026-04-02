"""
CLI commands for AI Sticker E-commerce system.
"""

import asyncio
import os
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt

from src.core.logger import get_logger

console = Console()
logger = get_logger(__name__)


@click.group()
@click.version_option(version="2.0.0")
def cli():
    """AI Sticker E-commerce CLI - Generate stickers, blogs, and more."""
    pass


@cli.command()
@click.option("-t", "--topic", prompt="Blog topic", help="Blog topic from trend research")
@click.option(
    "-k", "--keywords", prompt="SEO keywords (comma-separated)",
    help="SEO keywords (comma-separated)",
)
@click.option(
    "--writer-llm",
    type=click.Choice(["claude", "gemini"]),
    default="gemini",
    help="LLM provider for Writer Agent",
)
@click.option(
    "--reviewer-llm",
    type=click.Choice(["claude", "gemini"]),
    default="gemini",
    help="LLM provider for Reviewer Agent",
)
@click.option(
    "--max-iterations", default=5, type=int,
    help="Max generate-review iterations",
)
@click.option(
    "--pass-threshold", default=80.0, type=float,
    help="Score threshold (0-100) to auto-pass",
)
@click.option(
    "--auto", is_flag=True, default=False,
    help="Full auto mode: revise until pass or max iterations, no prompts",
)
@click.option(
    "--language", default="en",
    help="Output language (e.g. en, zh)",
)
@click.option(
    "--extra", default="",
    help="Additional instructions for Writer",
)
@click.option(
    "--planner-llm",
    type=click.Choice(["claude", "gemini"]),
    default="gemini",
    help="LLM provider for Planner Agent",
)
@click.option(
    "--skip-planner", is_flag=True, default=False,
    help="Skip the planning phase (go straight to writing)",
)
@click.option(
    "--no-images", is_flag=True, default=False,
    help="Skip image generation (keep [Image: ...] placeholders)",
)
@click.option(
    "--publish", is_flag=True, default=False,
    help="Push to Shopify as draft after generation",
)
@click.option(
    "--publish-live", is_flag=True, default=False,
    help="Push to Shopify and publish immediately (public)",
)
def blog(
    topic, keywords, writer_llm, reviewer_llm, planner_llm, skip_planner,
    max_iterations, pass_threshold, auto, language, extra, no_images,
    publish, publish_live,
):
    """Generate SEO blog with multi-agent Writer + Reviewer loop."""
    keywords_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not keywords_list:
        console.print("[bold red]Error: At least one keyword is required[/bold red]")
        return

    should_publish = publish or publish_live
    publish_live_flag = publish_live

    try:
        asyncio.run(
            _run_blog_generate(
                topic=topic,
                keywords_list=keywords_list,
                writer_llm=writer_llm,
                reviewer_llm=reviewer_llm,
                planner_llm=planner_llm,
                skip_planner=skip_planner,
                max_iterations=max_iterations,
                pass_threshold=pass_threshold,
                auto_mode=auto,
                language=language,
                extra=extra,
                generate_images=not no_images,
                publish=should_publish,
                publish_live=publish_live_flag,
            )
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted.[/yellow]")
    except Exception as e:
        logger.error(f"blog command failed: {e}", exc_info=True)
        console.print(f"[bold red]Error: {e}[/bold red]")


async def _run_blog_generate(
    topic: str,
    keywords_list: list[str],
    writer_llm: str,
    reviewer_llm: str,
    planner_llm: str,
    skip_planner: bool,
    max_iterations: int,
    pass_threshold: float,
    auto_mode: bool,
    language: str,
    extra: str,
    generate_images: bool = True,
    publish: bool = False,
    publish_live: bool = False,
):
    """Async entry point for blog command."""
    from src.models.blog import BusinessProfile, BlogInput, BlogDraft, ReviewResult
    from src.services.blog import WriterAgent, ReviewerAgent, BlogOrchestrator
    from src.services.blog.planner_agent import PlannerAgent
    from src.services.blog.plan_reviewer_agent import PlanReviewerAgent
    from src.services.blog.blog_image_generator import BlogImageGenerator
    from src.services.blog.shopify_publisher import ShopifyPublisher
    from src.services.ai.claude_service import ClaudeService
    from src.services.ai.gemini_service import GeminiService

    profile = BusinessProfile.from_yaml()
    images_label = "On" if generate_images else "Off"
    planner_label = "Off" if skip_planner else planner_llm
    publish_label = "Shopify LIVE" if publish_live else ("Shopify Draft" if publish else "Off")
    console.print(Panel(
        f"[bold]Topic:[/bold] {topic}\n"
        f"[bold]Keywords:[/bold] {', '.join(keywords_list)}\n"
        f"[bold]Planner:[/bold] {planner_label}  |  [bold]Writer:[/bold] {writer_llm}  |  [bold]Reviewer:[/bold] {reviewer_llm}\n"
        f"[bold]Threshold:[/bold] {pass_threshold}  |  [bold]Max iterations:[/bold] {max_iterations}\n"
        f"[bold]Mode:[/bold] {'Auto' if auto_mode else 'Interactive'}  |  [bold]Images:[/bold] {images_label}\n"
        f"[bold]Publish:[/bold] {publish_label}",
        title="Multi-Agent Blog Generator",
        border_style="blue",
    ))

    BLOG_WRITER_TIMEOUT = 600
    BLOG_REVIEWER_TIMEOUT = 300
    BLOG_PLANNER_TIMEOUT = 300
    gemini_text_model = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.1-pro-preview")

    def _make_llm(name: str, timeout: int):
        if name == "claude":
            return ClaudeService(timeout=timeout)
        return GeminiService(timeout=timeout, model=gemini_text_model)

    writer_service = _make_llm(writer_llm, BLOG_WRITER_TIMEOUT)
    reviewer_service = _make_llm(reviewer_llm, BLOG_REVIEWER_TIMEOUT)

    image_generator = None
    if generate_images:
        gemini_for_images = GeminiService()
        image_generator = BlogImageGenerator(gemini_for_images)

    writer = WriterAgent(writer_service, profile)
    reviewer = ReviewerAgent(reviewer_service, profile)

    planner = None
    plan_reviewer = None
    if not skip_planner:
        planner_service = _make_llm(planner_llm, BLOG_PLANNER_TIMEOUT)
        planner = PlannerAgent(planner_service, profile)
        plan_reviewer = PlanReviewerAgent(planner_service, profile)

    blog_input = BlogInput(
        topic=topic,
        seo_keywords=keywords_list,
        additional_instructions=extra,
        language=language,
    )

    def on_review(draft: BlogDraft, review: ReviewResult, iteration: int) -> str:
        _display_review(draft, review, iteration, max_iterations)

        if review.passed:
            console.print(
                f"\n[bold green]PASSED[/bold green] "
                f"(score {review.overall_score:.0f} >= threshold {pass_threshold})"
            )
            if auto_mode:
                return "accept"
            choice = Prompt.ask(
                "Accept or continue revising?",
                choices=["accept", "revise", "abort"],
                default="accept",
            )
            return "auto_revise" if choice == "revise" else choice

        console.print(
            f"\n[bold yellow]NEEDS REVISION[/bold yellow] "
            f"(score {review.overall_score:.0f} < threshold {pass_threshold})"
        )
        if auto_mode:
            return "auto_revise"
        choice = Prompt.ask(
            "What would you like to do?",
            choices=["revise", "accept", "abort"],
            default="revise",
        )
        return "auto_revise" if choice == "revise" else choice

    def progress(msg: str):
        console.print(f"  [dim]{msg}[/dim]")

    store_domain = profile.platform.get("domain", "") or os.getenv("SHOPIFY_STORE_DOMAIN", "")
    store_url = f"https://{store_domain}" if store_domain else "https://your-store.myshopify.com"

    publisher = None
    if publish:
        try:
            publisher = ShopifyPublisher(
                shop_domain=store_domain or None,
                blog_handle=os.getenv("SHOPIFY_BLOG_HANDLE", "blog"),
            )
            console.print("[green]Shopify publisher initialized[/green]")
        except ValueError as e:
            console.print(f"[bold red]Shopify publish disabled: {e}[/bold red]")
            console.print("[dim]Set SHOPIFY_STORE_DOMAIN and SHOPIFY_ACCESS_TOKEN in .env[/dim]")

    orchestrator = BlogOrchestrator(
        writer=writer,
        reviewer=reviewer,
        planner=planner,
        plan_reviewer=plan_reviewer,
        max_iterations=max_iterations,
        pass_threshold=pass_threshold,
        image_generator=image_generator,
        store_url=store_url,
        publisher=publisher,
        publish_live=publish_live,
    )

    final_draft = await orchestrator.run(blog_input, on_review, progress)

    if final_draft:
        console.print(Panel(
            f"[bold green]Blog generation complete![/bold green]\n\n"
            f"Title: {final_draft.meta_title}\n"
            f"Slug: {final_draft.url_slug}\n"
            f"Iteration: {final_draft.iteration}\n"
            f"Words: {len(final_draft.content.split())}\n\n"
            f"[bold]Shopify paste-ready HTML[/bold] -> output/blogs/shopify_ready/\n"
            f"Open the .html file in browser, select content, paste into Shopify editor.",
            title="Result",
            border_style="green",
        ))
    else:
        console.print("[yellow]No blog was saved.[/yellow]")


def _display_review(
    draft, review, iteration: int, max_iterations: int,
):
    """Render the review result as a rich table."""
    console.print()
    console.rule(f"[bold]Iteration {iteration}/{max_iterations} - Review Results[/bold]")
    console.print()

    console.print(f"  [bold]Title:[/bold]  {draft.meta_title}")
    console.print(f"  [bold]Slug:[/bold]   {draft.url_slug}")
    console.print(f"  [bold]Words:[/bold]  {len(draft.content.split())}")
    console.print()

    table = Table(
        show_header=True,
        header_style="bold",
        expand=True,
        title="Review Dimensions",
    )
    table.add_column("Dimension", style="cyan", ratio=2)
    table.add_column("Score", justify="center", ratio=1)
    table.add_column("Weight", justify="center", ratio=1)
    table.add_column("Issues", ratio=3)
    table.add_column("Top Suggestion", ratio=3)

    for dim in review.dimensions:
        score_color = "green" if dim.score >= 8 else "yellow" if dim.score >= 6 else "red"
        score_text = Text(f"{dim.score}/10", style=score_color)
        top_issue = dim.issues[0][:60] + "..." if dim.issues else "-"
        top_suggestion = dim.suggestions[0][:60] + "..." if dim.suggestions else "-"

        table.add_row(
            dim.name,
            score_text,
            f"{dim.weight:.0%}",
            top_issue,
            top_suggestion,
        )

    console.print(table)

    overall_color = "green" if review.passed else "yellow"
    console.print(
        f"\n  [bold]Overall:[/bold] [{overall_color}]{review.overall_score:.0f}/100[/{overall_color}]"
        f"  |  {review.summary}"
    )


@cli.command()
def version():
    """Show version information."""
    console.print("[bold]AI Sticker E-commerce System[/bold]")
    console.print("Version: 2.0.0")
    console.print("Components:")
    console.print("  - Sticker Pack Generator")
    console.print("  - Style Analyzer")
    console.print("  - Blog Generator (multi-agent)")
    console.print("  - Feishu Bot Integration")


if __name__ == "__main__":
    cli()
