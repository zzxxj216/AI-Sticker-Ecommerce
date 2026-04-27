"""TK video management — B.1 / B.2 / B.3 of the V2 pipeline.

Lifecycle: operator manually uploads a video file → AI generates an
English caption + hashtags via two-step generation → operator schedules
the post → scheduler dispatches to Blotato → metrics refresh polls TK.
"""

from src.services.tk_videos.service import TKVideoService, get_tk_video_service

__all__ = ["TKVideoService", "get_tk_video_service"]
