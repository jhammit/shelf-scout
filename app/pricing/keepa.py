"""Amazon prices and demand via the Keepa API.

Keepa is the practical way to get Amazon used-book prices, sales rank and
sales-velocity estimates (rank drops). Each product lookup costs ~1 token;
`history=0` keeps payloads small since we only need the stats block.

Price-type indices into stats.current / avg90 (from Keepa's product object):
  0 AMAZON, 1 NEW, 2 USED, 3 SALES (rank), 11 COUNT_NEW, 12 COUNT_USED,
  19 USED_NEW_SHIPPING, 20 USED_VERY_GOOD_SHIPPING, 21 USED_GOOD_SHIPPING,
  29 EBAY_USED_SHIPPING, 32 BUY_BOX_USED_SHIPPING
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from ..cache import Cache
from ..config import settings
from ..isbn import to_isbn13
from ..models import MarketData

log = logging.getLogger(__name__)

API = "https://api.keepa.com/product"
TTL = 12 * 3600

AMAZON, NEW, USED, SALES = 0, 1, 2, 3
COUNT_NEW, COUNT_USED = 11, 12
USED_LIKE_NEW_SHIP, USED_VG_SHIP, USED_GOOD_SHIP = 19, 20, 21
EBAY_USED_SHIP, BUY_BOX_USED_SHIP = 29, 32


def _at(arr: Optional[list], i: int) -> Optional[int]:
    if not arr or len(arr) <= i:
        return None
    v = arr[i]
    return v if isinstance(v, int) and v > 0 else None


def _price(arr: Optional[list], i: int) -> Optional[float]:
    v = _at(arr, i)
    return round(v / 100, 2) if v else None


def parse_product(p: dict, isbn13: str) -> MarketData:
    st = p.get("stats") or {}
    cur = st.get("current")
    good = [x for x in (_price(cur, USED_LIKE_NEW_SHIP), _price(cur, USED_VG_SHIP),
                        _price(cur, USED_GOOD_SHIP)) if x]
    grams = p.get("packageWeight") or p.get("itemWeight")
    fba = (p.get("fbaFees") or {}).get("pickAndPackFee")
    asin = p.get("asin", "")
    count_used = cur[COUNT_USED] if cur and len(cur) > COUNT_USED and cur[COUNT_USED] >= 0 else None
    count_new = cur[COUNT_NEW] if cur and len(cur) > COUNT_NEW and cur[COUNT_NEW] >= 0 else None
    return MarketData(
        isbn13=isbn13,
        sources=["keepa"],
        title=p.get("title") or "",
        binding=p.get("binding") or "",
        weight_lb=round(grams / 453.6, 2) if grams and grams > 0 else None,
        amazon_used_low=_price(cur, USED),
        amazon_used_good_landed=min(good) if good else None,
        amazon_used_buybox_landed=_price(cur, BUY_BOX_USED_SHIP),
        amazon_new_low=_price(cur, NEW),
        amazon_price=_price(cur, AMAZON),
        sales_rank=_at(cur, SALES),
        sales_rank_avg90=_at(st.get("avg90"), SALES),
        sales_rank_avg180=_at(st.get("avg180"), SALES),
        rank_drops_30=st.get("salesRankDrops30") if (st.get("salesRankDrops30") or -1) >= 0 else None,
        rank_drops_90=st.get("salesRankDrops90") if (st.get("salesRankDrops90") or -1) >= 0 else None,
        monthly_sold=p.get("monthlySold") or None,
        used_offer_count=count_used,
        new_offer_count=count_new,
        fba_pick_pack_fee=round(fba / 100, 2) if fba and fba > 0 else None,
        keepa_ebay_used_landed=_price(cur, EBAY_USED_SHIP),
        links={
            "amazon": f"https://www.amazon.com/dp/{asin}" if asin else f"https://www.amazon.com/s?k={isbn13}",
            "keepa": f"https://keepa.com/#!product/{settings.keepa_domain}-{asin}" if asin else "",
        },
    )


class KeepaProvider:
    name = "keepa"

    def __init__(self, http: httpx.AsyncClient, cache: Cache):
        self.http = http
        self.cache = cache
        self.tokens_left: Optional[int] = None
        self.lock = asyncio.Lock()

    async def fetch(self, isbns: list[str], warnings: list[str]) -> dict[str, MarketData]:
        out: dict[str, MarketData] = {}
        todo = []
        for i in dict.fromkeys(isbns):
            hit = self.cache.get(f"kp:{i}")
            if hit is not None:
                if hit:
                    out[i] = MarketData(**hit)
            else:
                todo.append(i)
        for start in range(0, len(todo), 100):
            batch = todo[start:start + 100]
            data = await self._request(batch, warnings)
            if data is None:
                continue
            found = set()
            for p in data.get("products") or []:
                keys = {to_isbn13(e) for e in (p.get("eanList") or [])}
                keys.add(to_isbn13(p.get("asin", "")))  # a book's ASIN is usually its ISBN-10
                match = next((k for k in batch if k in keys), None)
                if not match or match in found:
                    continue
                found.add(match)
                md = parse_product(p, match)
                out[match] = md
                self.cache.set(f"kp:{match}", md.model_dump(), TTL)
            for miss in set(batch) - found:
                self.cache.set(f"kp:{miss}", {}, TTL / 2)  # remember "not on Amazon"
        return out

    async def _request(self, codes: list[str], warnings: list[str]) -> Optional[dict]:
        params = {
            "key": settings.keepa_api_key,
            "domain": settings.keepa_domain,
            "code": ",".join(codes),
            "stats": 90,
            "history": 0,
        }
        async with self.lock:  # Keepa tokens are a shared bucket; don't stampede it
            for attempt in range(3):
                try:
                    r = await self.http.get(API, params=params, timeout=60)
                except httpx.HTTPError as e:
                    warnings.append(f"Keepa request failed: {e}")
                    return None
                try:
                    data = r.json()
                except ValueError:
                    data = {}
                self.tokens_left = data.get("tokensLeft", self.tokens_left)
                if r.status_code == 200:
                    return data
                if r.status_code == 429:
                    wait_ms = data.get("refillIn", 60000)
                    if wait_ms <= 25000 and attempt < 2:
                        await asyncio.sleep(wait_ms / 1000 + 0.5)
                        continue
                    warnings.append(
                        f"Keepa is out of tokens (refill in ~{round(wait_ms / 1000)}s); "
                        f"{len(codes)} books were not priced on Amazon."
                    )
                    return None
                warnings.append(f"Keepa error HTTP {r.status_code}: {data.get('error', '')}")
                return None
        return None
