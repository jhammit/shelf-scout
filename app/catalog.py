"""Title/author -> candidate editions (ISBNs), and edition matching.

A spine tells you the work, rarely the exact edition. We pull every edition
Open Library knows for the work (plus Google Books as a second opinion) and
score each against what the spine shows: publisher/imprint at the foot,
physical format, edition numbers, book-club marks.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx
from rapidfuzz import fuzz

from .cache import DAY, Cache
from .config import settings
from .isbn import to_isbn10, to_isbn13
from .models import EditionCandidate, SpineRead

log = logging.getLogger(__name__)

OL = "https://openlibrary.org"
GB = "https://www.googleapis.com/books/v1/volumes"

_norm_re = re.compile(r"[^a-z0-9 ]+")


def norm(s: str) -> str:
    s = _norm_re.sub(" ", (s or "").lower())
    s = re.sub(r"^(the|a|an) ", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


# Imprint words that tell you little on their own
_PUB_NOISE = {"books", "book", "press", "publishing", "publishers", "inc", "llc", "co", "company",
              "group", "house", "editions", "edition", "ltd", "limited", "usa", "us", "paperbacks"}


def _pub_core(s: str) -> str:
    return " ".join(w for w in norm(s).split() if w not in _PUB_NOISE)


_FORMAT_FAMILY = {
    "hardcover_dust_jacket": "hard",
    "hardcover_no_jacket": "hard",
    "library_binding": "hard",
    "leather_fine_binding": "hard",
    "trade_paperback": "paper",
    "mass_market_paperback": "mass",
}


def _format_family(physical_format: str) -> str:
    f = (physical_format or "").lower()
    if not f:
        return ""
    if "mass" in f:
        return "mass"
    if any(w in f for w in ("hard", "cloth", "library", "leather", "board")):
        return "hard"
    if any(w in f for w in ("paper", "soft", "trade")):
        return "paper"
    return ""


def _year(s: str) -> Optional[int]:
    m = re.search(r"(1[5-9]\d\d|20\d\d)", s or "")
    return int(m.group(1)) if m else None


def _publishers(e: dict) -> str:
    names = [p.get("name", "") if isinstance(p, dict) else str(p) for p in e.get("publishers", [])[:2]]
    return ", ".join(n for n in names if n)


def score_edition(spine: SpineRead, ed: EditionCandidate) -> float:
    title_sim = fuzz.token_set_ratio(norm(spine.title), norm(ed.title)) / 100
    if spine.author and ed.authors:
        author_sim = max(fuzz.token_set_ratio(norm(spine.author), norm(a)) for a in ed.authors) / 100
    else:
        author_sim = 0.6
    if spine.publisher_or_imprint and ed.publisher:
        a, b = _pub_core(spine.publisher_or_imprint), _pub_core(ed.publisher)
        pub = fuzz.partial_ratio(a, b) / 100 if a and b else 0.5
    else:
        pub = 0.5
    want, have = _FORMAT_FAMILY.get(spine.format_guess, ""), _format_family(ed.physical_format)
    fmt = 0.5 if not want or not have else (1.0 if want == have else 0.1)

    misc = 0.5
    clues = " ".join(spine.edition_clues).lower()
    m = re.search(r"(\d+)\s*(st|nd|rd|th)?\s*ed", (spine.series_or_volume + " " + clues).lower())
    if m:
        n = m.group(1)
        ed_text = (ed.title + " " + ed.physical_format).lower()
        misc = 1.0 if re.search(rf"\b{n}(st|nd|rd|th)?\b", ed_text) else 0.2
    if "book club" in clues:
        misc = 1.0 if "club" in ed.publisher.lower() else misc
    return round(0.35 * title_sim + 0.15 * author_sim + 0.25 * pub + 0.17 * fmt + 0.08 * misc, 4)


class Catalog:
    def __init__(self, http: httpx.AsyncClient, cache: Cache):
        self.http = http
        self.cache = cache
        self.sem = asyncio.Semaphore(settings.concurrency)

    async def _get_json(self, url: str, params: dict, key: str, ttl: float = 30 * DAY):
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        async with self.sem:
            r = await self.http.get(url, params=params, follow_redirects=True)
        if r.status_code == 404:
            self.cache.set(key, {}, ttl)
            return {}
        r.raise_for_status()
        data = r.json()
        self.cache.set(key, data, ttl)
        return data

    # ---------- Open Library ----------
    async def _ol_work(self, title: str, author: str) -> Optional[dict]:
        params = {"title": title, "limit": 5,
                  "fields": "key,title,author_name,first_publish_year,edition_count"}
        if author:
            params["author"] = author
        data = await self._get_json(f"{OL}/search.json", params, f"ol:s:{norm(title)}|{norm(author)}")
        docs = data.get("docs", []) if data else []
        if not docs and author:  # spine author reads are often partial; retry title-only
            params.pop("author")
            data = await self._get_json(f"{OL}/search.json", params, f"ol:s:{norm(title)}|")
            docs = data.get("docs", []) if data else []
        best, best_s = None, 0.0
        for d in docs:
            s = fuzz.token_set_ratio(norm(title), norm(d.get("title", ""))) / 100
            if author and d.get("author_name"):
                s = 0.7 * s + 0.3 * max(fuzz.token_set_ratio(norm(author), norm(a)) / 100
                                        for a in d["author_name"])
            s += min(d.get("edition_count", 0), 200) / 4000  # tiny nudge toward the main work
            if s > best_s:
                best, best_s = d, s
        return best if best_s >= 0.65 else None

    async def _ol_editions(self, work: dict) -> list[EditionCandidate]:
        key = work["key"] if work["key"].startswith("/") else f"/works/{work['key']}"
        data = await self._get_json(f"{OL}{key}/editions.json", {"limit": 200}, f"ol:e:{key}")
        out = []
        authors = work.get("author_name", [])
        for e in (data or {}).get("entries", []):
            langs = [l.get("key", "") for l in e.get("languages", [])]
            if langs and "/languages/eng" not in langs:
                continue
            isbns = [to_isbn13(x) for x in e.get("isbn_13", []) + e.get("isbn_10", [])]
            isbns = [x for x in isbns if x]
            if not isbns:
                continue
            title = e.get("title", work.get("title", ""))
            if e.get("edition_name"):
                title = f"{title} ({e['edition_name']})"
            out.append(EditionCandidate(
                isbn13=isbns[0], isbn10=to_isbn10(isbns[0]), title=title, authors=authors,
                publisher=_publishers(e), year=_year(e.get("publish_date", "")),
                physical_format=e.get("physical_format", ""), pages=e.get("number_of_pages"), source="openlibrary",
            ))
        return out

    # ---------- Google Books ----------
    async def _gb(self, q: str) -> list[EditionCandidate]:
        params = {"q": q, "maxResults": 20, "printType": "books"}
        if settings.google_books_api_key:
            params["key"] = settings.google_books_api_key
        data = await self._get_json(GB, params, f"gb:{q}")
        out = []
        for it in (data or {}).get("items", []):
            v = it.get("volumeInfo", {})
            ids = {i.get("type"): i.get("identifier") for i in v.get("industryIdentifiers", [])}
            isbn = to_isbn13(ids.get("ISBN_13", "") or ids.get("ISBN_10", ""))
            if not isbn:
                continue
            title = v.get("title", "") + (f": {v['subtitle']}" if v.get("subtitle") else "")
            out.append(EditionCandidate(
                isbn13=isbn, isbn10=to_isbn10(isbn), title=title, authors=v.get("authors", []),
                publisher=v.get("publisher", ""), year=_year(v.get("publishedDate", "")),
                pages=v.get("pageCount"), source="googlebooks",
            ))
        return out

    # ---------- public ----------
    async def find_editions(self, spine: SpineRead, top_n: int = 4) -> list[EditionCandidate]:
        results: dict[str, EditionCandidate] = {}
        try:
            work = await self._ol_work(spine.title, spine.author)
            if work:
                for c in await self._ol_editions(work):
                    results.setdefault(c.isbn13, c)
        except httpx.HTTPError as e:
            log.warning("openlibrary failed for %r: %s", spine.title, e)
        try:
            q = f'intitle:"{spine.title}"' + (f' inauthor:"{spine.author}"' if spine.author else "")
            for c in await self._gb(q):
                if c.isbn13 in results:
                    ex = results[c.isbn13]  # fill gaps from Google
                    ex.publisher = ex.publisher or c.publisher
                    ex.year = ex.year or c.year
                    ex.pages = ex.pages or c.pages
                else:
                    results[c.isbn13] = c
        except httpx.HTTPError as e:
            log.warning("google books failed for %r: %s", spine.title, e)

        for c in results.values():
            c.match_score = score_edition(spine, c)
        ranked = sorted(results.values(), key=lambda c: (-c.match_score, -(c.year or 0)))
        return [c for c in ranked if c.match_score >= 0.45][:top_n]

    async def lookup_isbn(self, isbn13: str) -> Optional[EditionCandidate]:
        try:
            e = await self._get_json(f"{OL}/isbn/{isbn13}.json", {}, f"ol:i:{isbn13}")
            if e and e.get("title"):
                authors: list[str] = []
                for w in e.get("works", [])[:1]:
                    wk = await self._get_json(f"{OL}{w['key']}.json", {}, f"ol:w:{w['key']}")
                    for a in (wk or {}).get("authors", [])[:3]:
                        ak = a.get("author", {}).get("key")
                        if ak:
                            ad = await self._get_json(f"{OL}{ak}.json", {}, f"ol:a:{ak}")
                            if ad and ad.get("name"):
                                authors.append(ad["name"])
                return EditionCandidate(
                    isbn13=isbn13, isbn10=to_isbn10(isbn13), title=e["title"], authors=authors,
                    publisher=_publishers(e), year=_year(e.get("publish_date", "")),
                    physical_format=e.get("physical_format", ""), pages=e.get("number_of_pages"),
                    match_score=1.0, source="openlibrary",
                )
        except httpx.HTTPError as ex:
            log.warning("openlibrary isbn lookup failed: %s", ex)
        try:
            items = await self._gb(f"isbn:{isbn13}")
            if items:
                c = items[0]
                c.isbn13, c.match_score = isbn13, 1.0
                return c
        except httpx.HTTPError as ex:
            log.warning("google books isbn lookup failed: %s", ex)
        return None


def edition_confidence(spine: SpineRead, cands: list[EditionCandidate]) -> float:
    """How sure we are the top candidate is the edition on the shelf."""
    if not cands:
        return 0.0
    top = cands[0].match_score
    if len(cands) == 1:
        sep = 1.0
    else:
        second = cands[1]
        same_kind = (
            _pub_core(second.publisher) == _pub_core(cands[0].publisher)
            and _format_family(second.physical_format) == _format_family(cands[0].physical_format)
        )
        gap = top - second.match_score
        # two printings from the same publisher in the same format usually price alike
        sep = 1.0 if same_kind else min(1.0, 0.45 + gap / 0.12 * 0.55)
    read = {"clear": 1.0, "partial": 0.8, "guess": 0.6}[spine.readability]
    return round(max(0.0, min(1.0, top * sep * read * (0.5 + 0.5 * spine.confidence))), 3)
