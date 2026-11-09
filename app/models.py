"""Data shapes passed between pipeline stages and returned by the API."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

FormatGuess = Literal[
    "hardcover_dust_jacket",
    "hardcover_no_jacket",
    "trade_paperback",
    "mass_market_paperback",
    "library_binding",
    "leather_fine_binding",
    "unknown",
]

Tier = Literal["pull", "check", "skip"]
Channel = Literal["amazon_fba", "amazon_fbm", "ebay"]


class SpineRead(BaseModel):
    """What the vision model could read off one spine."""

    title: str
    author: str = ""
    publisher_or_imprint: str = ""
    series_or_volume: str = ""
    format_guess: FormatGuess = "unknown"
    edition_clues: list[str] = Field(default_factory=list)
    condition_notes: str = ""
    readability: Literal["clear", "partial", "guess"] = "clear"
    confidence: float = 0.7
    x_center_pct: float = 50.0   # position on the ORIGINAL photo, 0–100
    y_center_pct: float = 50.0
    shelf_row: int = 1


class EditionCandidate(BaseModel):
    isbn13: str
    isbn10: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    publisher: str = ""
    year: Optional[int] = None
    physical_format: str = ""
    pages: Optional[int] = None
    match_score: float = 0.0
    source: str = ""


class MarketData(BaseModel):
    """Normalized market snapshot for one ISBN, merged across sources."""

    isbn13: str
    sources: list[str] = Field(default_factory=list)
    title: str = ""
    binding: str = ""
    weight_lb: Optional[float] = None
    # Amazon (Keepa) — prices in dollars, landed = item + shipping
    amazon_used_low: Optional[float] = None          # lowest used item price (excl. shipping)
    amazon_used_good_landed: Optional[float] = None  # lowest Good-or-better used, incl. shipping
    amazon_used_buybox_landed: Optional[float] = None
    amazon_new_low: Optional[float] = None
    amazon_price: Optional[float] = None             # Amazon itself selling it
    sales_rank: Optional[int] = None
    sales_rank_avg90: Optional[int] = None
    sales_rank_avg180: Optional[int] = None
    rank_drops_30: Optional[int] = None
    rank_drops_90: Optional[int] = None
    monthly_sold: Optional[int] = None               # Amazon "bought in past month" bracket
    used_offer_count: Optional[int] = None
    new_offer_count: Optional[int] = None
    fba_pick_pack_fee: Optional[float] = None
    keepa_ebay_used_landed: Optional[float] = None   # Keepa's own eBay used tracker
    # eBay
    ebay_active_count: Optional[int] = None
    ebay_active_low: Optional[float] = None          # landed
    ebay_active_median_low5: Optional[float] = None  # median of 5 cheapest landed asks
    ebay_sold_count_90d: Optional[int] = None
    ebay_sold_median: Optional[float] = None
    links: dict[str, str] = Field(default_factory=dict)


class ChannelEstimate(BaseModel):
    channel: Channel
    sale_price: float                 # what the buyer pays for the item (excl. shipping credit)
    gross: float                      # everything you receive from the buyer/marketplace
    fees: dict[str, float]
    net_if_sold: float                # cash to you after fees and your shipping costs
    monthly_sales_est: float
    competition: int
    sell_prob: float                  # chance it sells inside your sell window
    expected_net: float               # sell_prob * net_if_sold
    max_buy: float                    # highest price you should pay (risk-adjusted)
    profit_if_sold_at_cost: float
    expected_profit_at_cost: float
    basis: str                        # one-line explanation of where the price came from


class BookResult(BaseModel):
    id: int
    spine: Optional[SpineRead] = None
    candidates: list[EditionCandidate] = Field(default_factory=list)
    edition_confidence: float = 0.0
    chosen_isbn: str = ""
    market: Optional[MarketData] = None
    alt_market: Optional[MarketData] = None
    channels: list[ChannelEstimate] = Field(default_factory=list)
    best: Optional[ChannelEstimate] = None
    tier: Tier = "skip"
    score: float = 0.0
    headline: str = ""
    reasons: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    search_links: dict[str, str] = Field(default_factory=dict)


class BuyerProfile(BaseModel):
    """Per-request knobs from the app's settings panel."""

    cost_per_book: float = 1.00       # what the shelf is asking per book
    min_profit: float = 5.00          # $ you need to clear per book
    min_roi: float = 1.00             # 1.0 = 100% return on cost
    sell_window_days: int = 90
    channels: list[Channel] = Field(default_factory=lambda: ["amazon_fba", "amazon_fbm", "ebay"])
    condition: Literal["like_new", "very_good", "good", "acceptable"] = "good"


class ScanSummary(BaseModel):
    pull: int = 0
    check: int = 0
    skip: int = 0
    unreadable: int = 0
    total_spines: int = 0


class ScanResult(BaseModel):
    scan_id: str
    image_width: int
    image_height: int
    books: list[BookResult]
    summary: ScanSummary
    image_notes: str = ""
    warnings: list[str] = Field(default_factory=list)
    timings_ms: dict[str, int] = Field(default_factory=dict)
    profile: BuyerProfile
    sources: dict = Field(default_factory=dict)
