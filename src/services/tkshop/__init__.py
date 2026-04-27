"""TKShop product management — C.1 / C.2 / C.3 / C.4 of the V2 pipeline.

C.1: AI-generate English product detail (title, description, selling
     points, keywords) via two-step generation
C.2: manage main + secondary product images (manual + optional AI)
C.3: publish to TKShop server (POST to ops server at 192.168.121.1)
C.4: status sync via the same server
"""

from src.services.tkshop.service import TKShopService, get_tkshop_service

__all__ = ["TKShopService", "get_tkshop_service"]
