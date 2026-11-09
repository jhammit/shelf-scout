"""Shelf Scout on Streamlit.

Same engine as the FastAPI app (app/), wrapped in a Streamlit UI so it can be
hosted free on Streamlit Community Cloud and opened from your phone anywhere.

Run locally:   streamlit run streamlit_app.py
Secrets:       .streamlit/secrets.toml locally, or the app's Secrets box on Community Cloud
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os

import streamlit as st

# Secrets must be in the environment before app.config is imported.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float, bool)):
            os.environ.setdefault(_k, str(_v))
except Exception:  # no secrets file: demo mode
    pass

import httpx  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app import vision  # noqa: E402
from app.cache import Cache  # noqa: E402
from app.config import settings  # noqa: E402
from app.imaging import load_image  # noqa: E402
from app.models import BookResult, BuyerProfile, ScanResult  # noqa: E402
from app.pipeline import Scanner, rescore  # noqa: E402
from app.profit import LABEL  # noqa: E402

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass

st.set_page_config(page_title="Shelf Scout", page_icon="📚", layout="centered")

TIER = {"pull": "Pull", "check": "Look closer", "skip": "Skip"}
TIER_MD = {"pull": ":green-badge[Pull]", "check": ":orange-badge[Look closer]", "skip": ":gray-badge[Skip]"}
PIN = {"pull": (31, 107, 79), "check": (183, 121, 31), "skip": (138, 144, 150)}


# ---------- password gate ----------
def password_ok() -> bool:
    if not settings.app_password:
        return True
    if st.session_state.get("authed"):
        return True
    pw = st.text_input("App password", type="password")
    if pw and hmac.compare_digest(pw, settings.app_password):
        st.session_state.authed = True
        st.rerun()
    elif pw:
        st.error("That password isn't right.")
    return False


@st.cache_resource
def get_cache() -> Cache:
    return Cache(settings.cache_path)


async def _with_scanner(fn):
    vision._client = None  # each Streamlit run gets a fresh event loop; don't reuse old connections
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": settings.user_agent}) as http:
        return await fn(Scanner(http, get_cache()))


def run_scan(data: bytes, profile: BuyerProfile) -> ScanResult:
    return asyncio.run(_with_scanner(lambda s: s.scan(data, profile)))


def run_isbn(isbn: str, profile: BuyerProfile):
    return asyncio.run(_with_scanner(lambda s: s.check_isbn(isbn, profile)))


# ---------- drawing ----------
def pinned_photo(data: bytes, res: ScanResult) -> Image.Image:
    img = load_image(data)
    img.thumbnail((1600, 1600))
    d = ImageDraw.Draw(img)
    r = max(14, img.width // 45)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(r * 1.1))
    except OSError:
        font = ImageFont.load_default(size=int(r * 1.1))
    n = 0
    for b in res.books:
        if not b.spine:
            continue
        x, y = b.spine.x_center_pct / 100 * img.width, b.spine.y_center_pct / 100 * img.height
        if b.tier == "skip":
            d.ellipse([x - r / 2.5, y - r / 2.5, x + r / 2.5, y + r / 2.5], fill=PIN["skip"], outline="white", width=2)
            continue
        n += 1
        d.ellipse([x - r, y - r, x + r, y + r], fill=PIN[b.tier], outline="white", width=3)
        d.text((x, y), str(n), fill="white", font=font, anchor="mm")
    return img


def money(v) -> str:
    return "–" if v is None else f"${v:,.2f}"


def title_of(b: BookResult) -> str:
    if b.spine:
        return b.spine.title
    c = b.candidates[0] if b.candidates else None
    return (c.title if c else "") or (b.market.title if b.market else "") or b.chosen_isbn


def byline(b: BookResult) -> str:
    c = b.candidates[0] if b.candidates else None
    author = (b.spine.author if b.spine else "") or (", ".join(c.authors) if c else "")
    ed = " ".join(x for x in [c.publisher if c else "", (c.physical_format or "").lower() if c else ""] if x)
    if c and c.year:
        ed += f", {c.year}"
    return "  \n".join(x for x in [f"by {author}" if author else "", ed.strip(", ")] if x)


def book_card(b: BookResult, rank: int | None, open_details: bool = False):
    with st.container(border=True):
        left, right = st.columns([3, 1.3], vertical_alignment="top")
        with left:
            head = f"**#{rank}** " if rank else ""
            st.markdown(f"{head}{TIER_MD[b.tier]}")
            st.markdown(f"### {title_of(b)}")
            if byline(b):
                st.caption(byline(b))
        with right:
            if b.tier != "skip" and b.best:
                st.metric("Pay up to", money(b.best.max_buy) if b.best.max_buy > 0 else "Check ISBN")
        st.write(b.headline)
        with st.expander("Details", expanded=open_details):
            for f in b.flags:
                st.warning(f)
            for r in b.reasons:
                st.markdown(f"- {r}")
            if b.channels:
                st.table([{
                    "Where": LABEL[c.channel], "Net": money(c.net_if_sold),
                    "Sells": f"{c.sell_prob:.0%}", "Max pay": money(c.max_buy),
                } for c in b.channels])
                if b.best:
                    fees = ", ".join(f"{k.replace('_', ' ')} {money(v)}" for k, v in b.best.fees.items())
                    st.caption(f"{LABEL[b.best.channel]}: buyer pays {money(b.best.gross)}; fees {fees}. "
                               f"{b.best.basis}.")
            if b.spine and b.candidates:
                st.markdown("**Likely editions** (pull the book and check the ISBN on the back):")
                for i, c in enumerate(b.candidates[:3]):
                    label = "Best match" if i == 0 else "Also possible"
                    st.markdown(f"- {label}: {c.publisher or c.title} {c.year or ''} — `{c.isbn13}`")
            links = {**(b.market.links if b.market else {}), **b.search_links}
            names = [("amazon", "Amazon"), ("ebay_sold", "eBay sold"), ("bookscouter", "BookScouter"),
                     ("keepa", "Keepa"), ("abebooks", "AbeBooks")]
            st.markdown("  ".join(f"[{n}]({links[k]})" for k, n in names if links.get(k)))


# ---------- page ----------
st.title("Shelf Scout")
if not password_ok():
    st.stop()

with st.sidebar:
    st.header("Buying settings")
    cost = st.number_input("Price per book ($)", 0.0, 500.0, 1.0, 0.25, help="What this shelf is asking")
    min_profit = st.number_input("Minimum profit ($)", 0.0, 500.0, 5.0, 0.5, help="Per book, after fees")
    roi = st.selectbox("Minimum return", [0.5, 1.0, 2.0, 3.0], index=1, format_func=lambda v: f"{v:.0%} of cost")
    window = st.selectbox("Must sell within", [30, 90, 180, 365], index=1, format_func=lambda d: f"{d} days")
    cond = st.selectbox("Typical condition", ["like_new", "very_good", "good", "acceptable"], index=2,
                        format_func=lambda c: c.replace("_", " ").capitalize())
    chans = st.multiselect("Where you sell", list(LABEL), default=list(LABEL), format_func=LABEL.get)
    status = settings.status()
    st.caption(f"Data sources: vision {status['vision']}, Amazon {status['amazon']}, eBay {status['ebay']}")

profile = BuyerProfile(cost_per_book=cost, min_profit=min_profit, min_roi=roi, sell_window_days=window,
                       condition=cond, channels=chans or list(LABEL))

if not settings.vision_enabled:
    st.info("Demo mode: you'll see a built-in sample shelf with simulated prices. "
            "Add ANTHROPIC_API_KEY (and KEEPA_API_KEY / eBay keys) in Secrets for real scans.")

tab_scan, tab_isbn = st.tabs(["Scan a shelf", "Check an ISBN"])

with tab_scan:
    st.caption("On a phone, tap Browse files and choose Take Photo to use the full-resolution camera.")
    up = st.file_uploader("Shelf photo", type=["jpg", "jpeg", "png", "webp", "heic", "heif"],
                          label_visibility="collapsed")
    if up is not None:
        data = up.getvalue()
        key = hashlib.sha256(data).hexdigest()
        if st.session_state.get("scan_key") != key:  # only a new photo costs an API call
            with st.spinner("Reading spines and checking prices. A full shelf takes 20 to 60 seconds."):
                try:
                    st.session_state.scan = run_scan(data, profile)
                    st.session_state.scan_key = key
                    st.session_state.photo = data
                except Exception as e:
                    st.error(f"The scan failed: {e}")
                    st.stop()
        res: ScanResult = st.session_state.scan
        if res.profile != profile:  # settings changed: re-score the same scan for free
            res = st.session_state.scan = rescore(res, profile)
        st.image(pinned_photo(st.session_state.photo, res), width="stretch")
        s = res.summary
        st.markdown(f"#### :green[{s.pull}] to pull, :orange[{s.check}] to look closer at, {s.skip} to skip"
                    + (f"  \n{s.unreadable} spines were unreadable." if s.unreadable else ""))
        show_all = st.toggle("Show skipped books too", value=False)
        rank = 0
        for b in res.books:
            if b.tier != "skip":
                rank += 1
                book_card(b, rank)
            elif show_all:
                book_card(b, None)
        if res.warnings or res.image_notes:
            with st.expander("Notes on this scan"):
                for n in ([res.image_notes] if res.image_notes else []) + res.warnings:
                    st.markdown(f"- {n}")

with tab_isbn:
    with st.form("isbn"):
        isbn = st.text_input("ISBN from the back cover or copyright page", placeholder="978…")
        go = st.form_submit_button("Check", width="stretch")
    if go and isbn.strip():
        try:
            book, warnings = run_isbn(isbn.strip(), profile)
            book_card(book, None, open_details=True)
            for w in warnings:
                st.caption(w)
        except ValueError as e:
            st.error(str(e))
