"""Video narration (dubbing): silent video -> Gemini analysis -> English
voiceover + synced burned-in subtitles -> narrated mp4."""

from src.services.video_narration.service import (
    VideoNarrationService,
    get_video_narration_service,
)

__all__ = ["VideoNarrationService", "get_video_narration_service"]
