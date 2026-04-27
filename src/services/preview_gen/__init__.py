"""Preview image generation — A.3 of the V2 pipeline.

Consumes pack_series.metadata_json.preview_briefs and produces one PNG
per brief via AIRouter.image_generate (gpt-image-1 on AiHubMix).

Splitting into per-sticker images (image_edit) is a separate phase
handled by the same service in W2.4.
"""

from src.services.preview_gen.service import (
    PreviewGenService,
    get_preview_gen_service,
)

__all__ = ["PreviewGenService", "get_preview_gen_service"]
