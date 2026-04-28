"""Topic synthesis — A.1.5, the missing step between raw search (A.1)
and topic plan (A.2).

Takes N selected hot_topics (raw search results — usually URLs from Etsy /
Redbubble / Pinterest / etc) and asks the AI to cluster them into 1-3
commercially viable sticker pack 题材. Each cluster becomes a new
hot_topic with source='synthesized', carrying a theme summary and
back-references to the source rows.

A synthesized hot_topic is what the operator should typically send to
the topic plan generator — it's a coherent design brief, not a single
URL title.
"""

from src.services.topic_synthesis.service import (
    SynthesisService,
    get_synthesis_service,
)

__all__ = ["SynthesisService", "get_synthesis_service"]
