"""Blog post data models."""

from typing import List, Optional, Dict, Any
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class SEOMetadata(BaseModel):
    """SEO metadata for blog post."""
    title: str = Field(..., description="SEO-optimized title (50-60 chars)")
    meta_description: str = Field(..., description="Meta description (150-160 chars)")
    keywords: List[str] = Field(..., description="Primary and secondary keywords")
    structured_data: Dict[str, Any] = Field(default_factory=dict, description="JSON-LD schema")


class ImagePlacement(BaseModel):
    """Image placement information."""
    section_index: int = Field(..., description="Section index to insert after")
    prompt: str = Field(..., description="Image generation prompt")
    alt_text: str = Field(..., description="SEO alt text")
    caption: Optional[str] = Field(None, description="Optional caption")


class ContentSection(BaseModel):
    """Content section with heading and body."""
    heading: str = Field(..., description="H2/H3 heading")
    content: str = Field(..., description="Paragraph content")
    keywords: List[str] = Field(default_factory=list, description="Keywords to emphasize")


class BlogOutline(BaseModel):
    """Structured blog outline."""
    title: str = Field(..., description="Blog title")
    introduction: str = Field(..., description="Introduction paragraph")
    sections: List[ContentSection] = Field(..., description="Main content sections")
    conclusion: str = Field(..., description="Conclusion paragraph")
    image_placements: List[ImagePlacement] = Field(default_factory=list, description="Image placement suggestions")


class BlogPost(BaseModel):
    """Complete blog post with content and metadata."""
    topic: str = Field(..., description="Blog topic")
    seo_keywords: List[str] = Field(..., description="Target SEO keywords")
    outline: BlogOutline = Field(..., description="Structured outline")
    full_content: str = Field(..., description="Complete markdown content")
    seo_metadata: SEOMetadata = Field(..., description="SEO metadata")
    image_paths: List[str] = Field(default_factory=list, description="Generated image file paths")


class BlogPostResult(BaseModel):
    """Result of blog post generation."""
    success: bool = Field(..., description="Whether generation succeeded")
    blog_post: Optional[BlogPost] = Field(None, description="Generated blog post")
    markdown_path: str = Field(..., description="Output markdown file path")
    error: Optional[str] = Field(None, description="Error message if failed")


# ============================================================
# Multi-Agent Blog System Models
# ============================================================


class BusinessProfile(BaseModel):
    """业务画像 — 从 config/store_profile.yaml 加载，所有 Agent 共享"""
    business: Dict[str, Any] = Field(..., description="Business model info")
    brand: Dict[str, Any] = Field(..., description="Brand voice and tone")
    platform: Dict[str, Any] = Field(..., description="Shopify platform config")
    materials: Dict[str, Any] = Field(..., description="Material specs and claims")

    @classmethod
    def from_yaml(cls, path: str = "config/store_profile.yaml") -> "BusinessProfile":
        """从 YAML 文件加载业务画像"""
        filepath = Path(path)
        if not filepath.exists():
            raise FileNotFoundError(f"Business profile not found: {path}")
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)


class BlogInput(BaseModel):
    """博客生成的用户输入（只需话题+关键词，业务上下文自动注入）"""
    topic: str = Field(..., description="Blog topic from trend research")
    seo_keywords: List[str] = Field(..., description="SEO keywords from trend research")
    additional_instructions: str = Field("", description="Extra instructions")
    language: str = Field("en", description="Output language")


# ============================================================
# Planner Agent Models
# ============================================================


class ImagePlan(BaseModel):
    """Planned image tied to a specific content section."""
    section_title: str = Field(..., description="H2/H3 section this image belongs to")
    placement: str = Field(..., description="Where in the section to place the image")
    description: str = Field(..., description="Detailed image generation prompt")
    alt_text: str = Field(..., description="SEO alt text for the image")
    content_connection: str = Field(..., description="Why this image fits this section")


class ContentPlan(BaseModel):
    """Planner Agent output: article outline with image plans."""
    outline: List[Dict[str, Any]] = Field(
        ..., description="Section structure [{title, key_points, subsections}]"
    )
    image_plans: List[ImagePlan] = Field(
        ..., description="5-7 planned images with content context"
    )
    target_word_count: int = Field(2200, description="Target word count")
    seo_strategy: str = Field(..., description="How keywords will be distributed")
    iteration: int = Field(1, description="Which iteration of the plan")


class PlanReviewResult(BaseModel):
    """Plan Reviewer Agent output: content-image coherence review."""
    coherence_score: int = Field(..., ge=1, le=10, description="Do images match content sections?")
    coverage_score: int = Field(..., ge=1, le=10, description="Are key sections illustrated?")
    specificity_score: int = Field(..., ge=1, le=10, description="Are image descriptions detailed enough?")
    overall_score: float = Field(..., description="Weighted average 0-100")
    issues: List[str] = Field(default_factory=list, description="Issues found")
    suggestions: List[str] = Field(default_factory=list, description="Improvement suggestions")
    passed: bool = Field(..., description="Whether the plan passes the threshold")
    summary: str = Field("", description="One-line summary")


class BlogDraft(BaseModel):
    """Writer Agent 输出的博客草稿"""
    meta_title: str = Field(..., description="SEO meta title (50-60 chars)")
    meta_description: str = Field(..., description="SEO meta description (150-160 chars)")
    url_slug: str = Field(..., description="URL slug for the blog post")
    content: str = Field(..., description="Full markdown content")
    iteration: int = Field(1, description="Which iteration of the draft")
    shopify_article_id: Optional[int] = Field(None, description="Shopify article ID if published")


class ReviewDimension(BaseModel):
    """Reviewer Agent 的单个评审维度"""
    name: str = Field(..., description="Dimension name")
    score: int = Field(..., ge=1, le=10, description="Score 1-10")
    weight: float = Field(..., description="Weight for overall score calculation")
    issues: List[str] = Field(default_factory=list, description="Issues found with references to original text")
    suggestions: List[str] = Field(default_factory=list, description="Actionable improvement suggestions")


class ReviewResult(BaseModel):
    """Reviewer Agent 的完整评审结果"""
    dimensions: List[ReviewDimension] = Field(..., description="All review dimensions")
    overall_score: float = Field(..., description="Weighted overall score 0-100")
    summary: str = Field(..., description="One-line review summary")
    passed: bool = Field(..., description="Whether the draft passes the threshold")
