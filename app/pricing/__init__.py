"""Market data aggregation across pricing sources."""
from __future__ import annotations

import asyncio
import logging

import httpx

from ..cache import Cache
from ..config import settings
from ..demo_data import demo_market
from ..models import MarketData
from .ebay import EbayProvider, sold_url
from .keepa import KeepaProvider

log = logging.getLogger(__name__)


class DemoProvider:
    name = "demo"

    async def fetch(self, isbns: list[str], warnings: list[str]) -> dict[str, MarketData]:
        return {i: demo_market(i) for i in dict.fromkeys(isbns)}


def merge(a: MarketData, b: MarketData) -> MarketData:
    """Fill a's empty fields from b and union sources/links."""
    data = a.model_dump()
    for k, v in b.model_dump().items():
        if k in ("sources", "links", "isbn13"):
            continue
        if data.get(k) in (None, "", []) and v not in (None, "", []):
            data[k] = v
    data["sources"] = list(dict.fromkeys(a.sources + b.sources))
    data["links"] = {**b.links, **a.links}
    return MarketData(**data)


class Market:
    def __init__(self, http: httpx.AsyncClient, cache: Cache):
        self.providers = []
        if settings.mock_pricing:
            self.providers.append(DemoProvider())
        else:
            if settings.keepa_enabled:
                self.providers.append(KeepaProvider(http, cache))
            if settings.ebay_enabled:
                self.providers.append(EbayProvider(http, cache))

    @property
    def is_demo(self) -> bool:
        return any(isinstance(p, DemoProvider) for p in self.providers)

    async def snapshot(self, isbns: list[str], warnings: list[str]) -> dict[str, MarketData]:
        isbns = [i for i in dict.fromkeys(isbns) if i]
        if not isbns:
            return {}
        results = await asyncio.gather(
            *(p.fetch(isbns, warnings) for p in self.providers), return_exceptions=True
        )
        out: dict[str, MarketData] = {}
        for p, res in zip(self.providers, results):
            if isinstance(res, Exception):
                log.exception("provider %s failed", p.name, exc_info=res)
                warnings.append(f"{p.name} pricing failed: {res}")
                continue
            for isbn, md in res.items():
                out[isbn] = merge(out[isbn], md) if isbn in out else md
        for isbn, md in out.items():
            md.links.setdefault("ebay_sold", sold_url(isbn))
            md.links.setdefault("amazon", f"https://www.amazon.com/s?k={isbn}")
            md.links.setdefault("bookscouter", f"https://bookscouter.com/book/{isbn}")
        return out
