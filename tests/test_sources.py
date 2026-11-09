"""Parsing of Keepa / eBay payloads, edition matching, and spine merging, all offline."""
import asyncio
import json
import types

from app import vision
from app.catalog import edition_confidence, score_edition
from app.imaging import Tile, make_tiles, plan_grid
from app.models import EditionCandidate, SpineRead
from app.pricing import merge
from app.pricing.ebay import _landed
from app.pricing.keepa import parse_product


def keepa_product():
    cur = [-1] * 36
    cur[0], cur[1], cur[2], cur[3] = -1, 2599, 1450, 48210
    cur[11], cur[12] = 14, 37
    cur[19], cur[20], cur[21] = 2299, 1999, 1849
    cur[29], cur[32] = 2100, 1899
    avg90 = [-1] * 36
    avg90[3] = 51000
    return {
        "asin": "0465026567", "title": "Gödel, Escher, Bach", "binding": "Paperback",
        "eanList": ["9780465026562"], "packageWeight": 1089, "monthlySold": 50,
        "fbaFees": {"pickAndPackFee": 512},
        "stats": {"current": cur, "avg90": avg90, "salesRankDrops30": 33, "salesRankDrops90": 101},
    }


def test_keepa_parse():
    m = parse_product(keepa_product(), "9780465026562")
    assert m.amazon_used_low == 14.50
    assert m.amazon_used_good_landed == 18.49      # cheapest of like-new / VG / good, incl. shipping
    assert m.amazon_used_buybox_landed == 18.99
    assert m.sales_rank == 48210 and m.sales_rank_avg90 == 51000
    assert m.rank_drops_30 == 33 and m.used_offer_count == 37
    assert m.weight_lb == 2.4 and m.fba_pick_pack_fee == 5.12
    assert m.amazon_price is None                   # -1 means Amazon isn't selling it
    assert "0465026567" in m.links["amazon"]


def test_ebay_landed_price():
    assert _landed({"price": {"value": "12.50"}, "shippingOptions": [{"shippingCost": {"value": "3.99"}}]}) == 16.49
    assert _landed({"price": {"value": "10"}, "shippingOptions": [{"shippingCostType": "CALCULATED"}]}) == 14.39
    assert _landed({}) is None


def test_merge_fills_gaps():
    from app.models import MarketData
    a = MarketData(isbn13="1", sources=["keepa"], amazon_used_low=5.0)
    b = MarketData(isbn13="1", sources=["ebay"], ebay_active_low=7.0, amazon_used_low=9.0)
    m = merge(a, b)
    assert m.amazon_used_low == 5.0 and m.ebay_active_low == 7.0 and m.sources == ["keepa", "ebay"]


def test_edition_scoring_prefers_matching_publisher_and_format():
    sp = SpineRead(title="Dune", author="Frank Herbert", publisher_or_imprint="Ace", format_guess="mass_market_paperback")
    ace = EditionCandidate(isbn13="1", title="Dune", authors=["Frank Herbert"], publisher="Ace Books", physical_format="Mass Market Paperback")
    hc = EditionCandidate(isbn13="2", title="Dune", authors=["Frank Herbert"], publisher="Chilton Books", physical_format="Hardcover")
    assert score_edition(sp, ace) > score_edition(sp, hc) + 0.2
    ace.match_score, hc.match_score = score_edition(sp, ace), score_edition(sp, hc)
    assert edition_confidence(sp, [ace, hc]) > 0.6
    sp_vague = sp.model_copy(update={"publisher_or_imprint": "", "format_guess": "unknown", "readability": "guess"})
    a2 = ace.model_copy(update={"match_score": score_edition(sp_vague, ace)})
    h2 = hc.model_copy(update={"match_score": score_edition(sp_vague, hc)})
    assert edition_confidence(sp_vague, [a2, h2]) < 0.55


def test_tiling_plans():
    assert plan_grid(1600, 1200, 3, 2576) == (1, 1)
    assert plan_grid(4032, 3024, 3, 2576) == (3, 1)
    assert plan_grid(3024, 4032, 3, 2576) == (1, 3)
    from PIL import Image
    tiles = make_tiles(Image.new("RGB", (4032, 3024)), 3, 2576)
    assert len(tiles) == 3 and tiles[-1].x0 + tiles[-1].w == 4032
    assert all(t.sent_w <= 2576 and t.sent_h <= 2576 for t in tiles)
    assert tiles[0].x0 + tiles[0].w > tiles[1].x0  # tiles overlap


def test_merge_spines_dedupes_overlap_but_keeps_real_copies():
    def sp(t, x, c=0.9):
        return {"title": t, "author": "A", "x_center_pct": x, "y_center_pct": 20, "confidence": c}
    out = vision.merge_spines([sp("Dune", 40.0), sp("DUNE", 42.0, 0.7), sp("Dune", 80.0), sp("Emma", 10)])
    assert [s["title"] for s in out] == ["Emma", "Dune", "Dune"]


def test_read_spines_with_fake_claude(monkeypatch):
    payload = {"spines": [{
        "title": "A Pattern Language", "author": "Christopher Alexander", "publisher_or_imprint": "Oxford",
        "series_or_volume": "", "format_guess": "hardcover_dust_jacket", "edition_clues": [],
        "condition_notes": "", "readability": "clear", "confidence": 0.93,
        "x_center_pct": 50, "y_center_pct": 50, "shelf_row": 1}], "unreadable_count": 2, "image_notes": ""}
    calls = []

    async def create(**kw):
        calls.append(kw)
        return types.SimpleNamespace(stop_reason="end_turn",
                                     content=[types.SimpleNamespace(type="text", text=json.dumps(payload))])

    fake = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    monkeypatch.setattr(vision, "_get_client", lambda: fake)
    tiles = [Tile(0, 0, 0, 1000, 1000, "AAAA", 1000, 1000), Tile(1, 800, 0, 1000, 1000, "AAAA", 1000, 1000)]
    spines, unreadable, _, warnings = asyncio.run(vision.read_spines(tiles, 1800, 1000))
    assert len(calls) == 2
    assert calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert calls[0]["messages"][0]["content"][0]["type"] == "image"
    # same book seen in both tiles at different global x -> two entries (50% of tile 0 = x 500, tile 1 = x 1300)
    assert [round(s.x_center_pct) for s in spines] == [28, 72]
    assert unreadable == 3 and not warnings


def test_open_library_edition_lookup(tmp_path):
    import httpx

    from app.cache import Cache
    from app.catalog import Catalog

    def handler(req):
        u = str(req.url)
        if "search.json" in u:
            return httpx.Response(200, json={"docs": [
                {"key": "OL1W", "title": "Dune", "author_name": ["Frank Herbert"], "edition_count": 180}]})
        if "/works/OL1W/editions.json" in u:
            return httpx.Response(200, json={"entries": [
                {"title": "Dune", "publishers": ["Ace Books"], "publish_date": "1990",
                 "physical_format": "Mass Market Paperback", "isbn_10": ["0441172717"]},
                {"title": "Dune", "publishers": [{"name": "Chilton Books"}], "publish_date": "1965",
                 "physical_format": "Hardcover", "isbn_13": ["9780801950773"]},
                {"title": "Dune", "languages": [{"key": "/languages/spa"}], "isbn_13": ["9788497596824"]},
                {"title": "Dune", "publishers": ["No ISBN Press"]}]})
        if "googleapis" in u:
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404, json={})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            cat = Catalog(http, Cache(tmp_path / "c.sqlite"))
            sp = SpineRead(title="DUNE", author="Frank Herbert", publisher_or_imprint="ACE",
                           format_guess="mass_market_paperback")
            return await cat.find_editions(sp)

    cands = asyncio.run(run())
    assert [c.isbn13 for c in cands] == ["9780441172719", "9780801950773"]
    assert cands[1].publisher == "Chilton Books"
