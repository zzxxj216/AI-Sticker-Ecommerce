"""Pack aggregate service — A.4 of the V2 pipeline.

A *pack* is the saved/finalized form of a pack_series. Promoting a series
to a pack mints the packs row (with a chosen cover image and total
sticker count) and is the prerequisite for downstream stages:
  - B.1 TK videos (tk_videos.pack_id)
  - C.1 TKShop products (tkshop_products.pack_id)
"""

from src.services.packs.service import PackService, get_pack_service

__all__ = ["PackService", "get_pack_service"]
