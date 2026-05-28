"""Product catalog: a hierarchical, skill-style progressive-loading index of
existing products, used by scenario ② to find merge candidates without loading
every product into the model's context at once."""

from src.services.product_catalog.service import (
    ProductCatalog,
    get_product_catalog,
)

__all__ = ["ProductCatalog", "get_product_catalog"]
