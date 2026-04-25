"""Hot topic pool service — A.1 of the V2 pipeline."""

from src.services.hot_topics.service import (
    KNOWN_PROVIDERS,
    HotTopicService,
    get_hot_topic_service,
)

__all__ = ["HotTopicService", "KNOWN_PROVIDERS", "get_hot_topic_service"]
