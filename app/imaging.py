"""Photo preparation: orientation fix and tiling.

A phone photo of a shelf is 12+ MP, but the vision model downsizes anything
larger than ~2576px / ~4,784 visual tokens. Spine text is small, so instead of
letting one big photo get shrunk, we cut it into a few overlapping tiles that
each fit the model's native resolution, then merge what comes back.
"""
from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass

from PIL import Image, ImageOps

PATCH = 28              # vision models bill images in 28x28 px patches
MAX_VISUAL_TOKENS = 4784
OVERLAP_X = 0.18        # vertical cuts fall between spines; overlap must exceed a thick spine
OVERLAP_Y = 0.22        # horizontal cuts may slice through a shelf row; overlap more


@dataclass
class Tile:
    index: int
    x0: int
    y0: int
    w: int
    h: int
    jpeg_b64: str
    sent_w: int
    sent_h: int


def load_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)  # phones store rotation in EXIF
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def _scale_for(tw: float, th: float, max_edge: int) -> float:
    edge = max_edge / max(tw, th)
    tokens = math.sqrt(MAX_VISUAL_TOKENS * PATCH * PATCH / (tw * th))
    return min(1.0, edge, tokens)


def plan_grid(width: int, height: int, max_tiles: int, max_edge: int) -> tuple[int, int]:
    """Pick cols x rows (<= max_tiles) that keeps the most pixels per spine."""
    best = (1, 1)
    best_scale = _scale_for(width, height, max_edge)
    for cols in range(1, max_tiles + 1):
        for rows in range(1, max_tiles // cols + 1):
            tw = width / cols * (1 + OVERLAP_X) if cols > 1 else width
            th = height / rows * (1 + OVERLAP_Y) if rows > 1 else height
            s = _scale_for(min(tw, width), min(th, height), max_edge)
            # require a real gain (>8%) before paying for another tile
            if s > best_scale * 1.08:
                best, best_scale = (cols, rows), s
    return best


def _spans(total: int, parts: int, overlap: float) -> list[tuple[int, int]]:
    if parts == 1:
        return [(0, total)]
    size = min(total, int(round(total / parts * (1 + overlap))))
    step = (total - size) / (parts - 1)
    return [(int(round(i * step)), int(round(i * step)) + size) for i in range(parts)]


def _encode(img: Image.Image, quality: int = 90) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def make_tiles(img: Image.Image, max_tiles: int = 3, max_edge: int = 2576) -> list[Tile]:
    W, H = img.size
    cols, rows = plan_grid(W, H, max(1, max_tiles), max_edge)
    tiles: list[Tile] = []
    idx = 0
    for y0, y1 in _spans(H, rows, OVERLAP_Y):
        for x0, x1 in _spans(W, cols, OVERLAP_X):
            crop = img.crop((x0, y0, x1, y1))
            s = _scale_for(crop.width, crop.height, max_edge)
            if s < 1.0:
                crop = crop.resize(
                    (max(1, int(crop.width * s)), max(1, int(crop.height * s))),
                    Image.Resampling.LANCZOS,
                )
            tiles.append(
                Tile(idx, x0, y0, x1 - x0, y1 - y0, _encode(crop), crop.width, crop.height)
            )
            idx += 1
    return tiles
