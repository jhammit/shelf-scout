"""Spine reading with Claude vision + structured outputs.

Each tile of the shelf photo goes to Claude in parallel. The response is
constrained to a JSON schema, so we always get parseable spine records back.
Results from overlapping tiles are merged so a book cut across two tiles is
reported once.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from rapidfuzz import fuzz

from .config import settings
from .imaging import Tile
from .models import SpineRead

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert used-book scout helping a reseller decide which books on a shelf are worth pulling. You read book spines in photos with care and never invent text that is not there."""

INSTRUCTIONS = """Read every book spine visible in this photo of a bookshelf.

Rules:
- One entry per physical book. Go shelf by shelf from the top, left to right.
- Spine text often runs vertically (top-to-bottom or bottom-to-top). Read it in whichever direction makes sense.
- title / author: transcribe what is printed. Fix obvious letter-case only. Do not guess at text you cannot see.
  - readability = "clear" when you can read it outright.
  - readability = "partial" when only some words are legible (give what you can read).
  - readability = "guess" when you are completing a partial read from your own knowledge of the book (e.g. "HARRY POTTER AND THE C..." + "ROWLING").
- Leave out spines with no legible text at all; count them in unreadable_count instead.
- publisher_or_imprint: the name or logo at the foot of the spine (Penguin, Vintage, Knopf, Tor, Norton, Folio Society, a university press...). Empty string if none is visible.
- series_or_volume: series name, volume number, or textbook edition ("12th Edition") if printed. Empty string if none.
- format_guess, from physical cues:
  - hardcover_dust_jacket: glossy or printed paper jacket over boards
  - hardcover_no_jacket: cloth or printed boards, stamped lettering, no jacket
  - trade_paperback: larger softcover (about 8-9 in tall)
  - mass_market_paperback: small pocket softcover (about 7 in tall), pulp paper
  - library_binding: reinforced binding, call-number label or library sticker
  - leather_fine_binding: leather or faux leather, gilt lettering, raised bands (Easton Press, Franklin Library)
  - unknown: cannot tell
- edition_clues: short tags for visible evidence that affects resale value, for example "book club edition", "First Edition printed on spine", "signed", "limited edition", "ex-library", "Folio Society", "Easton Press", "Franklin Library", "textbook", "boxed set", "advance reader copy", "movie tie-in", "large print", "Reader's Digest condensed". Empty list if none.
- condition_notes: visible wear only (torn jacket, sun-faded spine, water stain, broken spine). Empty string if it looks fine.
- confidence: 0 to 1, how sure you are that title and author are right.
- x_center_pct / y_center_pct: center of the spine as a percent of this image's width / height (0 = left / top edge, 100 = right / bottom edge).
- shelf_row: 1 for the topmost shelf visible in this image, 2 for the next, and so on.
- image_notes: one short sentence on anything that limits reading (glare, blur, books lying flat, spines too small). Empty string if none."""

SPINE_SCHEMA = {
    "type": "object",
    "properties": {
        "spines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "publisher_or_imprint": {"type": "string"},
                    "series_or_volume": {"type": "string"},
                    "format_guess": {
                        "type": "string",
                        "enum": [
                            "hardcover_dust_jacket",
                            "hardcover_no_jacket",
                            "trade_paperback",
                            "mass_market_paperback",
                            "library_binding",
                            "leather_fine_binding",
                            "unknown",
                        ],
                    },
                    "edition_clues": {"type": "array", "items": {"type": "string"}},
                    "condition_notes": {"type": "string"},
                    "readability": {"type": "string", "enum": ["clear", "partial", "guess"]},
                    "confidence": {"type": "number"},
                    "x_center_pct": {"type": "number"},
                    "y_center_pct": {"type": "number"},
                    "shelf_row": {"type": "integer"},
                },
                "required": [
                    "title", "author", "publisher_or_imprint", "series_or_volume",
                    "format_guess", "edition_clues", "condition_notes", "readability",
                    "confidence", "x_center_pct", "y_center_pct", "shelf_row",
                ],
                "additionalProperties": False,
            },
        },
        "unreadable_count": {"type": "integer"},
        "image_notes": {"type": "string"},
    },
    "required": ["spines", "unreadable_count", "image_notes"],
    "additionalProperties": False,
}


class VisionError(RuntimeError):
    pass


_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import AsyncAnthropic

        _client = AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3)
    return _client


async def _read_tile(tile: Tile, tile_count: int) -> tuple[list[dict], int, str, list[str]]:
    client = _get_client()
    where = (
        f" This image is tile {tile.index + 1} of {tile_count} cut from a larger shelf photo; "
        "books cut off at the edge are fine to skip if their text is not readable."
        if tile_count > 1
        else ""
    )
    resp = await client.messages.create(
        model=settings.vision_model,
        max_tokens=12000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": tile.jpeg_b64},
                    },
                    {"type": "text", "text": INSTRUCTIONS + where},
                ],
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": SPINE_SCHEMA}},
    )
    warnings: list[str] = []
    if resp.stop_reason == "max_tokens":
        warnings.append(f"Tile {tile.index + 1}: response cut off; some spines may be missing.")
    if resp.stop_reason == "refusal":
        raise VisionError("The vision model declined to read this image.")
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:  # only possible on a truncated response
        raise VisionError(f"Could not parse vision output for tile {tile.index + 1}") from e
    return data.get("spines", []), int(data.get("unreadable_count", 0)), data.get("image_notes", ""), warnings


def _to_global(sp: dict, tile: Tile, W: int, H: int) -> dict:
    x = tile.x0 + float(sp.get("x_center_pct", 50)) / 100.0 * tile.w
    y = tile.y0 + float(sp.get("y_center_pct", 50)) / 100.0 * tile.h
    sp = dict(sp)
    sp["x_center_pct"] = round(max(0, min(100, x / W * 100)), 2)
    sp["y_center_pct"] = round(max(0, min(100, y / H * 100)), 2)
    sp["confidence"] = max(0.0, min(1.0, float(sp.get("confidence", 0.5))))
    return sp


_norm_re = re.compile(r"[^a-z0-9 ]+")


def _norm(s: str) -> str:
    s = _norm_re.sub(" ", s.lower())
    s = re.sub(r"^(the|a|an) ", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def merge_spines(spines: list[dict]) -> list[dict]:
    """Collapse duplicates from overlapping tiles; keep separate copies apart."""
    kept: list[dict] = []
    for sp in sorted(spines, key=lambda s: -s["confidence"]):
        t = _norm(sp["title"])
        dup = False
        for k in kept:
            same_title = fuzz.token_set_ratio(t, _norm(k["title"])) >= 88
            same_author = (
                not sp["author"] or not k["author"]
                or fuzz.token_set_ratio(_norm(sp["author"]), _norm(k["author"])) >= 80
            )
            near = (
                abs(sp["x_center_pct"] - k["x_center_pct"]) < 9
                and abs(sp["y_center_pct"] - k["y_center_pct"]) < 14
            )
            if same_title and same_author and near:
                dup = True
                break
        if not dup:
            kept.append(sp)
    # stable reading order: by row band, then left to right
    kept.sort(key=lambda s: (round(s["y_center_pct"] / 12), s["x_center_pct"]))
    return kept


async def read_spines(tiles: list[Tile], width: int, height: int) -> tuple[list[SpineRead], int, str, list[str]]:
    results = await asyncio.gather(
        *(_read_tile(t, len(tiles)) for t in tiles), return_exceptions=True
    )
    all_spines: list[dict] = []
    unreadable = 0
    notes: list[str] = []
    warnings: list[str] = []
    errors = 0
    for tile, res in zip(tiles, results):
        if isinstance(res, Exception):
            errors += 1
            log.exception("vision tile failed", exc_info=res)
            warnings.append(f"Tile {tile.index + 1} failed: {res}")
            continue
        spines, unread, note, warns = res
        warnings.extend(warns)
        unreadable += unread
        if note:
            notes.append(note)
        all_spines.extend(_to_global(s, tile, width, height) for s in spines if s.get("title"))
    if errors == len(tiles):
        raise VisionError("; ".join(warnings) or "vision failed")
    merged = merge_spines(all_spines)
    # tiles overlap, so the raw unreadable count double-counts; scale it down
    if len(tiles) > 1:
        unreadable = round(unreadable / (1 + 0.15 * (len(tiles) - 1)))
    note = " ".join(dict.fromkeys(notes))[:300]
    return [SpineRead(**s) for s in merged], unreadable, note, warnings
