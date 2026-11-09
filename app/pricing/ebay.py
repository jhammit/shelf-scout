"""eBay prices via the Browse API (active listings) and, if your app has been
approved for it, the Marketplace Insights API (sold listings, last 90 days).

Browse needs only an application token (client-credentials grant). Active asks
run higher than what books actually sell for, so the profit engine discounts
them; sold comps from Insights are used as-is when available.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from typing import Optional

import httpx

from ..cache import Cache
from ..config import settings
from ..models import MarketData

log = logging.getLogger(__name__)

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE = "https://api.ebay.com/buy/browse/v1/item_summary/search"
INSIGHTS = "https://api.ebay.com/buy/marketplace_insights/v1_beta/item_sales/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"
SCOPE_INSIGHTS = "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights"
BOOKS_CATEGORY = "267"
TTL = 12 * 3600
ASSUMED_SHIPPING = 4.39  # when a listing says "calculated shipping"


def sold_url(isbn13: str) -> str:
    return f"https://www.ebay.com/sch/i.html?_nkw={isbn13}&_sacat={BOOKS_CATEGORY}&LH_Sold=1&LH_Complete=1"


def _landed(item: dict) -> Optional[float]:
    try:
        price = float(item["price"]["value"])
    except (KeyError, TypeError, ValueError):
        return None
    ship = None
    for opt in item.get("shippingOptions") or []:
        cost = (opt.get("shippingCost") or {}).get("value")
        if cost is not None:
            ship = float(cost)
            break
    return round(price + (ship if ship is not None else ASSUMED_SHIPPING), 2)


class EbayProvider:
    name = "ebay"

    def __init__(self, http: httpx.AsyncClient, cache: Cache):
        self.http = http
        self.cache = cache
        self._tokens: dict[str, tuple[str, float]] = {}
        self._insights_ok = settings.ebay_use_insights
        self.sem = asyncio.Semaphore(4)

    async def _token(self, scope: str) -> str:
        tok = self._tokens.get(scope)
        if tok and tok[1] > time.time() + 60:
            return tok[0]
        r = await self.http.post(
            TOKEN_URL,
            auth=(settings.ebay_client_id, settings.ebay_client_secret),
            data={"grant_type": "client_credentials", "scope": scope},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        j = r.json()
        self._tokens[scope] = (j["access_token"], time.time() + int(j.get("expires_in", 7200)))
        return j["access_token"]

    async def _browse(self, isbn13: str) -> tuple[int, list[float]]:
        token = await self._token(SCOPE)
        headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace}
        base = {"category_ids": BOOKS_CATEGORY, "limit": 50, "sort": "price",
                "filter": "buyingOptions:{FIXED_PRICE},conditions:{USED},deliveryCountry:US"}
        total, prices = 0, []
        for key in ("gtin", "q"):  # product-identifier match first, then ISBN as keyword
            r = await self.http.get(BROWSE, params={**base, key: isbn13}, headers=headers)
            r.raise_for_status()
            j = r.json()
            items = j.get("itemSummaries") or []
            prices = sorted(p for p in (_landed(i) for i in items) if p)
            total = int(j.get("total", len(items)))
            if prices:
                break
        return total, prices

    async def _sold(self, isbn13: str) -> tuple[Optional[int], Optional[float]]:
        token = await self._token(SCOPE_INSIGHTS)
        headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace}
        params = {"gtin": isbn13, "category_ids": BOOKS_CATEGORY, "limit": 50,
                  "filter": "conditions:{USED}"}
        r = await self.http.get(INSIGHTS, params=params, headers=headers)
        r.raise_for_status()
        j = r.json()
        sales = j.get("itemSales") or []
        prices = []
        qty = 0
        for s in sales:
            try:
                prices.append(float(s["lastSoldPrice"]["value"]))
                qty += int(s.get("totalSoldQuantity", 1))
            except (KeyError, TypeError, ValueError):
                continue
        return (qty or int(j.get("total", 0))), (round(statistics.median(prices), 2) if prices else None)

    async def _one(self, isbn13: str, warnings: list[str]) -> Optional[MarketData]:
        hit = self.cache.get(f"eb:{isbn13}")
        if hit is not None:
            return MarketData(**hit) if hit else None
        async with self.sem:
            try:
                total, prices = await self._browse(isbn13)
            except httpx.HTTPError as e:
                warnings.append(f"eBay Browse failed for {isbn13}: {e}")
                return None
            sold_n, sold_med = None, None
            if self._insights_ok:
                try:
                    sold_n, sold_med = await self._sold(isbn13)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (401, 403):
                        self._insights_ok = False
                        warnings.append("eBay Marketplace Insights is not enabled for this app; using active listings only.")
                except httpx.HTTPError as e:
                    warnings.append(f"eBay sold lookup failed: {e}")
        md = MarketData(
            isbn13=isbn13,
            sources=["ebay"],
            ebay_active_count=total,
            ebay_active_low=prices[0] if prices else None,
            ebay_active_median_low5=round(statistics.median(prices[:5]), 2) if prices else None,
            ebay_sold_count_90d=sold_n,
            ebay_sold_median=sold_med,
            links={"ebay_sold": sold_url(isbn13),
                   "ebay": f"https://www.ebay.com/sch/i.html?_nkw={isbn13}&_sacat={BOOKS_CATEGORY}"},
        )
        self.cache.set(f"eb:{isbn13}", md.model_dump(), TTL)
        return md

    async def fetch(self, isbns: list[str], warnings: list[str]) -> dict[str, MarketData]:
        uniq = list(dict.fromkeys(isbns))
        res = await asyncio.gather(*(self._one(i, warnings) for i in uniq))
        return {i: m for i, m in zip(uniq, res) if m}
