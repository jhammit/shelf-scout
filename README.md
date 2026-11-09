# Shelf Scout

Photograph a bookshelf. Shelf Scout reads every spine, works out which edition each book most likely is, prices it on Amazon and eBay, and tells you which books to **pull**, which to **look closer at**, and the **most you should pay** for each one.

It's built for the thrift-store / library-sale / estate-sale workflow: scan a whole shelf from your phone in one shot, pull only the books worth your time, then type the ISBN of anything marked *Look closer* for an exact check.

```
phone photo ──► tiles ──► Claude vision ──► spines (title, author, imprint, format, edition clues, position)
                                              │
                                              ▼
                         Open Library + Google Books ──► candidate editions (ISBNs), scored against the spine
                                              │
                                              ▼
                         Keepa (Amazon) + eBay Browse ──► price, sales velocity, competing offers
                                              │
                                              ▼
                   fees + shipping ──► net if sold ──► chance it sells in your window ──► max buy price
                                              │
                                              ▼
                                   Pull / Look closer / Skip, ranked
```

## Try it in two minutes (demo mode)

With no API keys, the app runs on a built-in sample shelf with simulated prices so you can see the whole flow.

```bash
cd shelf-scout
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` on the computer, or `http://<your-computer's-local-IP>:8000` on your phone (same Wi-Fi). Take or choose any photo; `samples/demo-shelf.jpg` lines up with the demo results.

Run the tests with `pytest`.

## Turning on real data

Copy `.env.example` to `.env` and fill in what you have. Each source is independent; anything missing falls back gracefully.

| Key | What it powers | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Reading spines from your photo | [Claude Console](https://platform.claude.com/) |
| `KEEPA_API_KEY` | Amazon used prices, sales rank, rank drops (sales velocity), offer counts, FBA fee, weight | Keepa → API (paid subscription) |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | eBay active used listings | [developer.ebay.com](https://developer.ebay.com/) → production keyset (App ID / Cert ID) |
| `EBAY_USE_INSIGHTS=1` | eBay **sold** comps (90 days) | Only if eBay approves your app for Marketplace Insights (limited release) |
| `GOOGLE_BOOKS_API_KEY` | Higher Google Books rate limit | Optional; works without |

Why these sources:

- **Keepa, not Amazon's own API.** Amazon's Product Advertising API was deprecated in 2026 in favor of the affiliate-focused Creators API. Keepa supplies the signals scouting actually needs (used prices by condition, sales-rank history, rank drops, offer counts) in one cheap call per ISBN.
- **eBay Browse** needs only an app token and gives active listings. Active asks run higher than what books actually sell for, so the engine discounts them 8%. If you get Marketplace Insights access, sold medians are used instead.
- **BookScouter** doesn't offer a public API, so each result links out to it (and to eBay sold listings) for a manual check.

## Using it in the field

Thrift stores aren't on your home Wi-Fi, so put the server somewhere your phone can reach:

- **Any Docker host** (Render, Railway, Fly.io, a small VPS): `docker build -t shelf-scout . && docker run -p 8000:8000 --env-file .env -v scout-data:/data shelf-scout`. The container honors `$PORT`.
- **Set `APP_PASSWORD`** before exposing it to the internet. The app asks for it once and remembers it; without it, anyone with the URL can spend your API credits.
- On your phone, open the site and use *Add to Home Screen*; it runs full-screen like an app.

Camera capture uses a plain file input, so it works over HTTP on a LAN and over HTTPS when hosted.

## How the numbers work

Everything is in `app/profit.py` and `app/scoring.py`, and every fee is an environment variable (`FEE_*` in `.env.example`).

**Sale price (conservative).** Amazon: the cheapest Good-or-better used offer including shipping. eBay: sold median if available, otherwise 92% of the median of the five cheapest active asks, otherwise 85% of the Amazon price. Your *typical condition* setting scales this (acceptable ×0.75, like new ×1.10).

**Fees (US defaults, Nov 2026).**

| Channel | What's deducted |
|---|---|
| Amazon FBA | 15% referral, $1.80 media closing fee, FBA fulfillment (Keepa's per-ASIN fee when known, else a weight table) + 3.5% fuel surcharge, $0.50/lb to ship it in, $0.30 prep |
| Amazon, you ship | 15% referral on price + the $3.99 shipping credit, $1.80 closing, USPS Media Mail ($4.39 first lb + $0.74/lb), $0.50 mailer |
| eBay | 15.3% final value fee (Books & Magazines), $0.40 order fee ($0.30 at $10 or less), optional promoted %, Media Mail, mailer |

**Demand.** Monthly sales come from Keepa's 30-day sales-rank drops (each drop ≈ a sale), falling back to a rank curve (~30/mo at #100k, ~3/mo at #500k, ~1/mo at #1M). For eBay: sold count, or 30% of Amazon demand when there's no sold data.

**Chance it sells in your window.** Sales are shared across competing used offers, each counting as half a seller because you'll price at the low end: `p = 1 − exp(−(monthly_sales / (1 + 0.5 × offers)) × window_days / 30)`.

**Max buy price.** Risk-adjusted so slow sellers are worth less: `expected_net = p × net_if_sold`, then `max_buy = min(expected_net − min_profit, expected_net ÷ (1 + min_ROI))`. With the defaults ($5 minimum profit, 100% ROI) a book that reliably nets $20 has a max buy of $10.

**Tiers.**

- **Pull**: max buy is at least the shelf price, profit clears your minimum, ≥50% chance to sell in your window, and the edition match is confident.
- **Look closer**: profitable or plausibly profitable, but something needs your eyes: two likely editions with very different values, an uncertain edition match, a slow seller, or collectible clues (first edition, signed, Easton Press, Folio, leather). For uncertain editions the max buy uses the *cheaper* edition.
- **Skip**: everything else, including book-club and condensed editions.

## What to expect from spine reading

- Phone photos are split into up to `VISION_MAX_TILES` overlapping tiles sized to the model's native resolution (2,576 px long edge), so small spine text isn't lost to downscaling. Duplicates from overlapping tiles are merged.
- The edition is an **estimate**. Spines show the publisher/imprint and format, rarely the printing. That's why uncertain books land in *Look closer* with "Check the ISBN", and why the ISBN check exists.
- Best results: stand square to the shelf, one or two rows per photo, tilt slightly to kill glare.

**Rough cost per scan:** each tile is at most ~4,800 image tokens plus a few thousand output tokens, so a 3-tile shelf is roughly 15–25k tokens of vision. Keepa charges about one token per ISBN priced, so one to two per book (set `PRICE_CANDIDATES=1` to halve it). Results are cached in SQLite (catalog 30 days, prices 12 hours), so rescanning a shelf is nearly free.

## API

- `POST /api/scan` multipart: `image` (photo), `profile` (JSON, optional). Returns ranked books with spine read, edition candidates, market data, per-channel estimates, tier, headline, reasons, flags and links.
- `POST /api/isbn` JSON `{"isbn": "...", "profile": {...}}`: exact check for one ISBN.
- `GET /api/status`: which sources are live vs demo.

Profile fields: `cost_per_book` (1.00), `min_profit` (5.00), `min_roi` (1.0 = 100%), `sell_window_days` (90), `condition` (`good`), `channels` (`["amazon_fba","amazon_fbm","ebay"]`). Send the `X-App-Password` header when `APP_PASSWORD` is set.

## Project layout

```
app/
  main.py          FastAPI app, upload handling, password check
  pipeline.py      photo → spines → editions → prices → decisions
  imaging.py       EXIF orientation, tile planning
  vision.py        Claude vision call with a strict JSON schema; tile merge
  catalog.py       Open Library + Google Books lookups; edition scoring and confidence
  pricing/keepa.py Amazon data via Keepa (batched, cached, token-aware)
  pricing/ebay.py  eBay Browse (+ optional Marketplace Insights)
  profit.py        fees, demand, sell probability, max buy
  scoring.py       Pull / Look closer / Skip, headlines, flags
  demo_data.py     canned shelf for demo mode (made-up ISBNs and prices)
static/            the phone web app (no build step)
tests/             offline tests, including a fake Claude client
```

## Ideas for next steps

- Barcode scanning in the ISBN sheet using the browser's `BarcodeDetector` where supported.
- Scan history with CSV export of what you pulled and paid.
- Amazon SP-API (if you're a seller) for exact per-ASIN fees and category/brand restriction checks.
- Tune the rank curve and the half-competitor weighting against your own sell-through.

The Archivo typeface is bundled from Fontsource under the SIL Open Font License 1.1.
