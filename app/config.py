"""Runtime configuration.

Everything is read from environment variables (or a local `.env` file) so the
same code runs on a laptop, a phone-reachable home server, or a cloud host.
Fee numbers are defaults for US marketplaces as of late 2026 — they change,
so every one of them can be overridden without touching code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (KEY=VALUE lines) so we don't need python-dotenv."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _float(name: str, default: float) -> float:
    v = _env(name)
    return float(v) if v else default


def _int(name: str, default: int) -> int:
    v = _env(name)
    return int(v) if v else default


def _bool(name: str, default: bool) -> bool:
    v = _env(name).lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class FeeSchedule:
    """Marketplace fee assumptions. All money in USD."""

    # Amazon (Books category)
    amazon_referral_pct: float = 0.15          # 15% of total sale price incl. shipping
    amazon_closing_fee: float = 1.80           # per-unit media closing fee
    fba_fuel_surcharge_pct: float = 0.035      # surcharge on FBA fulfillment fee (Apr 2026)
    fba_inbound_per_lb: float = 0.50           # what it costs you to ship units into FBA
    fba_prep_per_unit: float = 0.30            # labels, poly bags, your time
    fbm_shipping_credit: float = 3.99          # Amazon pays this to you for media orders
    # eBay (Books & Magazines, no Store)
    ebay_fvf_pct: float = 0.153
    ebay_order_fee_small: float = 0.30         # orders <= $10
    ebay_order_fee: float = 0.40               # orders > $10
    ebay_promoted_pct: float = 0.0             # set e.g. 0.03 if you promote listings
    # Shipping (USPS Media Mail retail, Oct 2026 Notice 123)
    media_mail_first_lb: float = 4.39
    media_mail_addl_lb: float = 0.74
    packaging_per_unit: float = 0.50
    default_weight_lb: float = 1.2             # used when weight is unknown

    @classmethod
    def from_env(cls) -> "FeeSchedule":
        kw = {}
        for f in cls.__dataclass_fields__:
            v = _env("FEE_" + f.upper())
            if v:
                kw[f] = float(v)
        return cls(**kw)


@dataclass(frozen=True)
class Settings:
    # --- Vision (Claude) ---
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    vision_model: str = field(default_factory=lambda: _env("VISION_MODEL", "claude-sonnet-5-5"))
    vision_max_tiles: int = field(default_factory=lambda: _int("VISION_MAX_TILES", 3))
    # High-resolution tier models accept up to 2576px on the long edge.
    vision_tile_edge: int = field(default_factory=lambda: _int("VISION_TILE_EDGE", 2576))

    # --- Pricing sources ---
    keepa_api_key: str = field(default_factory=lambda: _env("KEEPA_API_KEY"))
    keepa_domain: int = field(default_factory=lambda: _int("KEEPA_DOMAIN", 1))  # 1 = amazon.com
    ebay_client_id: str = field(default_factory=lambda: _env("EBAY_CLIENT_ID"))
    ebay_client_secret: str = field(default_factory=lambda: _env("EBAY_CLIENT_SECRET"))
    ebay_marketplace: str = field(default_factory=lambda: _env("EBAY_MARKETPLACE", "EBAY_US"))
    # Marketplace Insights (sold comps) is a limited-release eBay API.
    ebay_use_insights: bool = field(default_factory=lambda: _bool("EBAY_USE_INSIGHTS", False))

    # --- Catalog lookup ---
    google_books_api_key: str = field(default_factory=lambda: _env("GOOGLE_BOOKS_API_KEY"))
    user_agent: str = field(
        default_factory=lambda: _env("HTTP_USER_AGENT", "ShelfScout/1.0 (book scouting tool)")
    )

    # --- Behaviour ---
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", False))
    price_candidates: int = field(default_factory=lambda: _int("PRICE_CANDIDATES", 2))
    concurrency: int = field(default_factory=lambda: _int("LOOKUP_CONCURRENCY", 6))
    app_password: str = field(default_factory=lambda: _env("APP_PASSWORD"))
    cache_path: Path = field(
        default_factory=lambda: Path(_env("CACHE_PATH", str(ROOT / "data" / "cache.sqlite")))
    )
    fees: FeeSchedule = field(default_factory=FeeSchedule.from_env)

    @property
    def vision_enabled(self) -> bool:
        return bool(self.anthropic_api_key) and not self.demo_mode

    @property
    def keepa_enabled(self) -> bool:
        return bool(self.keepa_api_key) and not self.demo_mode

    @property
    def ebay_enabled(self) -> bool:
        return bool(self.ebay_client_id and self.ebay_client_secret) and not self.demo_mode

    @property
    def mock_pricing(self) -> bool:
        return self.demo_mode or not (self.keepa_enabled or self.ebay_enabled)

    def status(self) -> dict:
        return {
            "vision": f"claude:{self.vision_model}" if self.vision_enabled else "demo",
            "amazon": "keepa" if self.keepa_enabled else ("demo" if self.mock_pricing else "off"),
            "ebay": ("browse+insights" if self.ebay_use_insights else "browse")
            if self.ebay_enabled
            else ("demo" if self.mock_pricing else "off"),
            "catalog": "demo" if self.demo_mode else "openlibrary+googlebooks",
            "demo_mode": self.demo_mode or not self.vision_enabled or self.mock_pricing,
            "password_required": bool(self.app_password),
        }


settings = Settings()
