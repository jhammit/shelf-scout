"""Canned shelf used when no API keys are configured (or DEMO_MODE=1).

The titles are real books so the flow feels realistic, but every ISBN and
price below is made up (ISBNs use an unassigned 979-8-9999 block). The mix is
deliberate: a few clear buys, a few "look closer" books, and plenty of
fast-selling-but-worthless titles that a scout should walk past.
"""
from __future__ import annotations

import hashlib
import random

from .isbn import isbn13_check_digit
from .models import EditionCandidate, MarketData, SpineRead


def demo_isbn(n: int) -> str:
    core = f"97989999{n:04d}"
    return core + isbn13_check_digit(core)


def S(title, author, pub, fmt, x, y, row, clues=(), read="clear", conf=0.92, series="", cond=""):
    return dict(title=title, author=author, publisher_or_imprint=pub, format_guess=fmt,
                x_center_pct=x, y_center_pct=y, shelf_row=row, edition_clues=list(clues),
                readability=read, confidence=conf, series_or_volume=series, condition_notes=cond)


def E(n, title, publisher, year, fmt, pages=None, **market):
    return dict(n=n, title=title, publisher=publisher, year=year, physical_format=fmt,
                pages=pages, market=market)


DEMO_BOOKS = [
    # ---- top shelf ----
    dict(spine=S("Gödel, Escher, Bach", "Douglas R. Hofstadter", "Basic Books", "trade_paperback", 8, 17, 1),
         editions=[E(1, "Gödel, Escher, Bach: An Eternal Golden Braid", "Basic Books", 1999, "Paperback", 777,
                     amazon_used_good_landed=21.50, amazon_used_low=16.80, sales_rank=38000, sales_rank_avg90=45000,
                     rank_drops_30=34, used_offer_count=42, weight_lb=2.4, fba_pick_pack_fee=5.20,
                     ebay_active_count=31, ebay_active_median_low5=19.90)]),
    dict(spine=S("The Da Vinci Code", "Dan Brown", "Anchor", "mass_market_paperback", 17, 18, 1),
         editions=[E(2, "The Da Vinci Code", "Anchor", 2009, "Mass Market Paperback", 597,
                     amazon_used_good_landed=4.49, amazon_used_low=0.50, sales_rank=120000, sales_rank_avg90=110000,
                     rank_drops_30=21, used_offer_count=380, weight_lb=0.6, ebay_active_count=210, ebay_active_median_low5=4.25)]),
    dict(spine=S("Campbell Biology", "Urry, Cain, Wasserman", "Pearson", "hardcover_no_jacket", 26, 16, 1,
                 clues=["textbook"], series="12th Edition"),
         editions=[E(3, "Campbell Biology (12th Edition)", "Pearson", 2020, "Hardcover", 1488,
                     amazon_used_good_landed=68.00, amazon_used_low=61.00, sales_rank=22000, sales_rank_avg90=19000,
                     rank_drops_30=60, used_offer_count=55, weight_lb=6.8, fba_pick_pack_fee=9.40,
                     ebay_active_count=44, ebay_active_median_low5=62.00),
                   E(4, "Campbell Biology (11th Edition)", "Pearson", 2016, "Hardcover", 1488,
                     amazon_used_good_landed=18.00, amazon_used_low=12.00, sales_rank=160000, sales_rank_avg90=150000,
                     rank_drops_30=9, used_offer_count=70, weight_lb=6.8, fba_pick_pack_fee=9.40)]),
    dict(spine=S("A Pattern Language", "Christopher Alexander", "Oxford", "hardcover_dust_jacket", 36, 17, 1),
         editions=[E(5, "A Pattern Language: Towns, Buildings, Construction", "Oxford University Press", 1977, "Hardcover", 1216,
                     amazon_used_good_landed=38.00, amazon_used_low=31.50, sales_rank=15000, sales_rank_avg90=17000,
                     rank_drops_30=45, used_offer_count=60, weight_lb=3.9, fba_pick_pack_fee=6.10,
                     ebay_active_count=38, ebay_active_median_low5=36.00)]),
    dict(spine=S("Where the Crawdads Sing", "Delia Owens", "Putnam", "hardcover_dust_jacket", 46, 18, 1),
         editions=[E(6, "Where the Crawdads Sing", "G.P. Putnam's Sons", 2018, "Hardcover", 384,
                     amazon_used_good_landed=5.20, amazon_used_low=1.21, sales_rank=9000, sales_rank_avg90=8000,
                     rank_drops_30=95, used_offer_count=600, weight_lb=1.4, ebay_active_count=420, ebay_active_median_low5=5.00)]),
    dict(spine=S("Joy of Cooking", "Rombauer & Becker", "", "hardcover_dust_jacket", 57, 16, 1, read="partial", conf=0.75),
         editions=[E(7, "The Joy of Cooking", "Bobbs-Merrill", 1975, "Hardcover", 915,
                     amazon_used_good_landed=24.00, amazon_used_low=19.00, sales_rank=210000, sales_rank_avg90=230000,
                     rank_drops_30=6, used_offer_count=28, weight_lb=3.6, ebay_active_count=60, ebay_active_median_low5=22.00),
                   E(8, "Joy of Cooking: 2019 Edition Fully Revised and Updated", "Scribner", 2019, "Hardcover", 1200,
                     amazon_used_good_landed=14.00, amazon_used_low=9.50, sales_rank=5000, sales_rank_avg90=4500,
                     rank_drops_30=120, used_offer_count=140, weight_lb=4.2, fba_pick_pack_fee=6.40)]),
    dict(spine=S("Infinite Jest", "David Foster Wallace", "Little, Brown", "hardcover_dust_jacket", 67, 17, 1,
                 clues=["First Edition printed on spine"]),
         editions=[E(9, "Infinite Jest", "Little, Brown and Company", 1996, "Hardcover", 1079,
                     amazon_used_good_landed=14.00, amazon_used_low=9.80, sales_rank=60000, sales_rank_avg90=70000,
                     rank_drops_30=14, used_offer_count=48, weight_lb=3.4, ebay_active_count=55, ebay_active_median_low5=45.00)]),
    dict(spine=S("Reader's Digest Condensed Books Volume 3", "", "Reader's Digest", "hardcover_no_jacket", 78, 18, 1,
                 clues=["Reader's Digest condensed"]),
         editions=[]),
    # ---- middle shelf ----
    dict(spine=S("The Silmarillion", "J.R.R. Tolkien", "Houghton Mifflin", "hardcover_dust_jacket", 10, 50, 2,
                 clues=["book club edition"]),
         editions=[E(10, "The Silmarillion", "Houghton Mifflin (Book Club Edition)", 1977, "Hardcover", 365,
                     amazon_used_good_landed=7.50, amazon_used_low=3.50, sales_rank=410000, sales_rank_avg90=380000,
                     rank_drops_30=3, used_offer_count=35, weight_lb=1.5)]),
    dict(spine=S("Sapiens", "Yuval Noah Harari", "Harper Perennial", "trade_paperback", 20, 51, 2),
         editions=[E(11, "Sapiens: A Brief History of Humankind", "Harper Perennial", 2018, "Paperback", 464,
                     amazon_used_good_landed=6.20, amazon_used_low=2.21, sales_rank=3000, sales_rank_avg90=2800,
                     rank_drops_30=150, used_offer_count=250, weight_lb=0.9, ebay_active_count=180, ebay_active_median_low5=6.50)]),
    dict(spine=S("The Feynman Lectures on Physics", "Feynman, Leighton, Sands", "Basic Books", "hardcover_no_jacket", 31, 49, 2,
                 series="Vol. I"),
         editions=[E(12, "The Feynman Lectures on Physics, Vol. I", "Basic Books", 2011, "Hardcover", 560,
                     amazon_used_good_landed=42.00, amazon_used_low=36.00, sales_rank=25000, sales_rank_avg90=28000,
                     rank_drops_30=30, used_offer_count=35, weight_lb=2.9, fba_pick_pack_fee=5.60,
                     ebay_active_count=22, ebay_active_median_low5=40.00)]),
    dict(spine=S("Dune", "Frank Herbert", "Ace", "mass_market_paperback", 40, 51, 2),
         editions=[E(13, "Dune", "Ace", 1990, "Mass Market Paperback", 535,
                     amazon_used_good_landed=5.00, amazon_used_low=1.01, sales_rank=1500, sales_rank_avg90=1200,
                     rank_drops_30=200, used_offer_count=400, weight_lb=0.6, ebay_active_count=300, ebay_active_median_low5=5.50)]),
    dict(spine=S("The Design of Everyday Things", "Don Norman", "Basic Books", "trade_paperback", 50, 50, 2),
         editions=[E(14, "The Design of Everyday Things: Revised and Expanded Edition", "Basic Books", 2013, "Paperback", 368,
                     amazon_used_good_landed=12.80, amazon_used_low=8.81, sales_rank=4000, sales_rank_avg90=4200,
                     rank_drops_30=110, used_offer_count=180, weight_lb=0.9, ebay_active_count=90, ebay_active_median_low5=12.00)]),
    dict(spine=S("Blood Meridian", "Cormac McCarthy", "Vintage International", "trade_paperback", 60, 51, 2, read="partial", conf=0.8),
         editions=[E(15, "Blood Meridian: Or the Evening Redness in the West", "Vintage", 1992, "Paperback", 351,
                     amazon_used_good_landed=7.50, amazon_used_low=3.80, sales_rank=6000, sales_rank_avg90=6500,
                     rank_drops_30=80, used_offer_count=200, weight_lb=0.7)]),
    # ---- bottom shelf ----
    dict(spine=S("The Art of Electronics", "Horowitz & Hill", "Cambridge", "hardcover_no_jacket", 15, 83, 3,
                 read="guess", conf=0.55),
         editions=[E(16, "The Art of Electronics (Third Edition)", "Cambridge University Press", 2015, "Hardcover", 1224,
                     amazon_used_good_landed=72.00, amazon_used_low=64.00, sales_rank=11000, sales_rank_avg90=12000,
                     rank_drops_30=40, used_offer_count=30, weight_lb=5.6, fba_pick_pack_fee=8.20,
                     ebay_active_count=25, ebay_active_median_low5=70.00),
                   E(17, "The Art of Electronics (Second Edition)", "Cambridge University Press", 1989, "Hardcover", 1125,
                     amazon_used_good_landed=34.00, amazon_used_low=27.00, sales_rank=95000, sales_rank_avg90=90000,
                     rank_drops_30=12, used_offer_count=40, weight_lb=5.1, fba_pick_pack_fee=7.90)]),
    dict(spine=S("Moby-Dick", "Herman Melville", "Easton Press", "leather_fine_binding", 33, 82, 3,
                 clues=["Easton Press"]),
         editions=[]),
]

DEMO_UNREADABLE = 4
DEMO_NOTE = "Demo shelf: add ANTHROPIC_API_KEY to read your own photos."

_BY_TITLE = {b["spine"]["title"].lower(): b for b in DEMO_BOOKS}
_MARKET = {demo_isbn(e["n"]): e for b in DEMO_BOOKS for e in b["editions"]}


def demo_spines() -> list[SpineRead]:
    return [SpineRead(**b["spine"]) for b in DEMO_BOOKS]


def demo_editions(spine: SpineRead) -> list[EditionCandidate]:
    b = _BY_TITLE.get(spine.title.lower())
    if not b:
        return []
    authors = [spine.author] if spine.author else []
    return [EditionCandidate(isbn13=demo_isbn(e["n"]), title=e["title"], authors=authors,
                             publisher=e["publisher"], year=e["year"], physical_format=e["physical_format"],
                             pages=e["pages"], source="demo") for e in b["editions"]]


def demo_lookup(isbn13: str) -> EditionCandidate | None:
    e = _MARKET.get(isbn13)
    if not e:
        return None
    return EditionCandidate(isbn13=isbn13, title=e["title"], publisher=e["publisher"], year=e["year"],
                            physical_format=e["physical_format"], pages=e["pages"], match_score=1.0, source="demo")


def demo_market(isbn13: str) -> MarketData:
    """Fixed numbers for demo ISBNs; a plausible deterministic fake for any other ISBN."""
    links = {"amazon": f"https://www.amazon.com/s?k={isbn13}",
             "ebay_sold": f"https://www.ebay.com/sch/i.html?_nkw={isbn13}&_sacat=267&LH_Sold=1&LH_Complete=1"}
    e = _MARKET.get(isbn13)
    if e:
        return MarketData(isbn13=isbn13, sources=["demo"], title=e["title"], links=links, **e["market"])
    rng = random.Random(int(hashlib.sha256(isbn13.encode()).hexdigest(), 16))
    rank = int(10 ** rng.uniform(3.6, 6.7))
    base = max(3.99, rng.lognormvariate(2.3, 0.75) * (1.4 if rank < 200_000 else 0.8))
    offers = max(1, int(rng.uniform(0.5, 1.5) * 2000 / max(rank, 1) ** 0.3))
    return MarketData(
        isbn13=isbn13, sources=["demo"], links=links,
        amazon_used_good_landed=round(base, 2), amazon_used_low=round(max(0.99, base - 3.99), 2),
        sales_rank=rank, sales_rank_avg90=int(rank * rng.uniform(0.8, 1.3)),
        rank_drops_30=None if rank > 2_000_000 else max(0, int(7.3e8 * rank ** -1.477 * rng.uniform(0.6, 1.2))),
        used_offer_count=offers, weight_lb=round(rng.uniform(0.5, 3.0), 1),
        ebay_active_count=int(offers * rng.uniform(0.3, 1.0)), ebay_active_median_low5=round(base * rng.uniform(0.85, 1.2), 2),
    )
