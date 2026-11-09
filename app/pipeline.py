"""Orchestration: photo -> spines -> editions -> prices -> decisions."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

import httpx

from .cache import Cache
from .catalog import Catalog, edition_confidence
from .config import settings
from .demo_data import DEMO_NOTE, DEMO_UNREADABLE, demo_editions, demo_lookup, demo_spines
from .imaging import load_image, make_tiles
from .isbn import to_isbn13
from .models import BookResult, BuyerProfile, EditionCandidate, ScanResult, ScanSummary, SpineRead
from .pricing import Market
from .scoring import evaluate, sort_books
from .vision import read_spines

log = logging.getLogger(__name__)


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


class Scanner:
    def __init__(self, http: httpx.AsyncClient, cache: Cache):
        self.catalog = Catalog(http, cache)
        self.market = Market(http, cache)

    @property
    def full_demo(self) -> bool:
        """Everything canned: no keys at all, or DEMO_MODE forced on."""
        return settings.demo_mode or (not settings.vision_enabled and self.market.is_demo)

    def sources(self) -> dict:
        return {**settings.status(), "full_demo": self.full_demo}

    async def _editions(self, spine: SpineRead) -> list[EditionCandidate]:
        if self.full_demo:
            cands = demo_editions(spine)
            from .catalog import score_edition

            for c in cands:
                c.match_score = score_edition(spine, c)
            return sorted(cands, key=lambda c: -c.match_score)
        try:
            return await self.catalog.find_editions(spine)
        except Exception as e:  # one bad lookup should never sink the whole shelf
            log.warning("edition lookup failed for %r: %s", spine.title, e)
            return []

    async def scan(self, image_bytes: bytes, profile: BuyerProfile) -> ScanResult:
        timings: dict[str, int] = {}
        warnings: list[str] = []
        t0 = time.perf_counter()
        img = load_image(image_bytes)
        W, H = img.size

        if settings.vision_enabled:
            tiles = make_tiles(img, settings.vision_max_tiles, settings.vision_tile_edge)
            timings["tiles"] = len(tiles)
            spines, unreadable, note, w = await read_spines(tiles, W, H)
            warnings += w
        else:
            spines, unreadable, note = demo_spines(), DEMO_UNREADABLE, DEMO_NOTE
            warnings.append("Demo mode: these spines are a built-in sample, not your photo. "
                            "Add ANTHROPIC_API_KEY to read real shelves.")
        timings["vision_ms"] = _ms(t0)

        t1 = time.perf_counter()
        cand_lists = await asyncio.gather(*(self._editions(s) for s in spines))
        timings["catalog_ms"] = _ms(t1)

        books: list[BookResult] = []
        alt_for: dict[int, str] = {}
        to_price: list[str] = []
        for i, (sp, cands) in enumerate(zip(spines, cand_lists), start=1):
            b = BookResult(id=i, spine=sp, candidates=cands, edition_confidence=edition_confidence(sp, cands))
            if cands:
                b.chosen_isbn = cands[0].isbn13
                to_price.append(b.chosen_isbn)
                if settings.price_candidates >= 2 and len(cands) > 1 and b.edition_confidence < 0.75:
                    alt_for[i] = cands[1].isbn13
                    to_price.append(cands[1].isbn13)
            books.append(b)

        t2 = time.perf_counter()
        market = await self.market.snapshot(to_price, warnings)

        # Top edition not listed anywhere? Try the next-best candidates once.
        retry: dict[int, list[str]] = {}
        for b in books:
            if b.chosen_isbn and b.chosen_isbn not in market and alt_for.get(b.id) not in market:
                nxt = [c.isbn13 for c in b.candidates[1:4] if c.isbn13 not in market][:2]
                if nxt:
                    retry[b.id] = nxt
        if retry:
            market.update(await self.market.snapshot([i for v in retry.values() for i in v], warnings))
        timings["pricing_ms"] = _ms(t2)

        for b in books:
            if b.chosen_isbn not in market:
                alt = alt_for.pop(b.id, None)
                found = next((i for i in ([alt] if alt else []) + retry.get(b.id, []) if i in market), None)
                if found:
                    b.chosen_isbn = found
                    b.candidates.sort(key=lambda c: c.isbn13 != found)
            b.market = market.get(b.chosen_isbn)
            alt = alt_for.get(b.id)
            if alt and alt != b.chosen_isbn:
                b.alt_market = market.get(alt)
            evaluate(b, profile, settings.fees)

        if self.market.is_demo and settings.vision_enabled:
            warnings.append("Prices are simulated: add KEEPA_API_KEY and/or eBay keys for real numbers.")

        books = sort_books(books)
        summary = ScanSummary(
            pull=sum(b.tier == "pull" for b in books),
            check=sum(b.tier == "check" for b in books),
            skip=sum(b.tier == "skip" for b in books),
            unreadable=unreadable,
            total_spines=len(books) + unreadable,
        )
        timings["total_ms"] = _ms(t0)
        return ScanResult(
            scan_id=uuid.uuid4().hex[:10], image_width=W, image_height=H, books=books,
            summary=summary, image_notes=note, warnings=list(dict.fromkeys(warnings)),
            timings_ms=timings, profile=profile, sources=self.sources(),
        )

    async def check_isbn(self, raw: str, profile: BuyerProfile) -> tuple[BookResult, list[str]]:
        isbn13 = to_isbn13(raw)
        if not isbn13:
            raise ValueError("That doesn't look like a valid ISBN-10 or ISBN-13.")
        warnings: list[str] = []
        if self.full_demo:
            ed = demo_lookup(isbn13)
        else:
            ed = await self.catalog.lookup_isbn(isbn13)
        market = await self.market.snapshot([isbn13], warnings)
        b = BookResult(id=0, candidates=[ed] if ed else [], edition_confidence=1.0,
                       chosen_isbn=isbn13, market=market.get(isbn13))
        if not ed and b.market and b.market.title:
            b.candidates = [EditionCandidate(isbn13=isbn13, title=b.market.title, match_score=1.0, source="market")]
        evaluate(b, profile, settings.fees)
        if self.market.is_demo:
            warnings.append("Prices are simulated (demo mode).")
        return b, warnings


def rescore(res: ScanResult, profile: BuyerProfile) -> ScanResult:
    """Re-apply buying rules to an existing scan without calling any API again."""
    books = [evaluate(b.model_copy(deep=True), profile, settings.fees) for b in res.books]
    books = sort_books(books)
    summary = res.summary.model_copy(update={
        "pull": sum(b.tier == "pull" for b in books),
        "check": sum(b.tier == "check" for b in books),
        "skip": sum(b.tier == "skip" for b in books),
    })
    return res.model_copy(update={"books": books, "summary": summary, "profile": profile})
