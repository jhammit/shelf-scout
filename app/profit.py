"""Profit engine: sale price -> fees -> net -> risk-adjusted max buy price.

For each selling channel we estimate:
  * a realistic sale price (conservative: match the cheapest Good-or-better offer)
  * every fee and shipping cost, so `net_if_sold` is cash in your pocket
  * how fast it sells, from Amazon sales-rank drops (or eBay sold counts)
    shared among competing offers -> chance of selling within your window
  * max_buy = the most you can pay and still clear BOTH your minimum dollar
    profit and your minimum ROI, after discounting for the chance it never sells
"""
from __future__ import annotations

import math
from typing import Optional

from .config import FeeSchedule
from .models import BuyerProfile, ChannelEstimate, MarketData

CONDITION_MULT = {"like_new": 1.10, "very_good": 1.03, "good": 1.0, "acceptable": 0.75}
LABEL = {"amazon_fba": "Amazon FBA", "amazon_fbm": "Amazon (you ship)", "ebay": "eBay"}


def rank_to_monthly_sales(rank: Optional[int]) -> float:
    """Rough US Books sales-rank curve: ~30/mo at #100k, ~3/mo at #500k, ~1/mo at #1M."""
    if not rank or rank <= 0:
        return 0.0
    return min(3000.0, 7.3e8 * rank ** -1.477)


def amazon_monthly_sales(m: MarketData) -> tuple[float, str]:
    if m.rank_drops_30 is not None and m.rank_drops_30 > 0:
        return float(m.rank_drops_30), f"{m.rank_drops_30} rank drops in 30d"
    if m.rank_drops_90 is not None and m.rank_drops_90 > 0:
        return m.rank_drops_90 / 3, f"{m.rank_drops_90} rank drops in 90d"
    rank = m.sales_rank_avg90 or m.sales_rank
    if rank:
        return rank_to_monthly_sales(rank), f"rank #{rank:,}"
    return 0.0, "no sales rank"


def sell_probability(monthly_sales: float, competitors: int, window_days: int) -> float:
    """Chance one more copy sells within the window if sales are shared across offers.

    Pricing at the low end of the market earns a bigger-than-average share, so
    each competitor counts as half a seller.
    """
    if monthly_sales <= 0:
        return 0.0
    rate = monthly_sales / (1 + 0.5 * max(0, competitors))
    return round(1 - math.exp(-rate * window_days / 30), 3)


def media_mail(weight_lb: float, fees: FeeSchedule) -> float:
    lbs = max(1, math.ceil(weight_lb))
    return round(fees.media_mail_first_lb + fees.media_mail_addl_lb * (lbs - 1), 2)


def fba_fulfillment_fee(weight_lb: float, keepa_fee: Optional[float], fees: FeeSchedule) -> float:
    """Keepa's per-ASIN pick & pack fee when known, otherwise an approximate 2026 table."""
    if keepa_fee:
        base = keepa_fee
    elif weight_lb <= 0.5:
        base = 3.22
    elif weight_lb <= 1.0:
        base = 3.77
    elif weight_lb <= 3.0:
        base = 3.77 + 0.40 * math.ceil((weight_lb - 1.0) / 0.5)
    else:
        base = 5.37 + 0.32 * math.ceil((weight_lb - 3.0) / 0.5)
    return round(base * (1 + fees.fba_fuel_surcharge_pct), 2)


def _max_buy(expected_net: float, profile: BuyerProfile) -> float:
    by_profit = expected_net - profile.min_profit
    by_roi = expected_net / (1 + profile.min_roi) if profile.min_roi > -1 else expected_net
    return round(max(0.0, min(by_profit, by_roi)), 2)


def _finish(channel, sale_price, gross, fees_d, monthly, comp, profile, basis) -> ChannelEstimate:
    net = round(gross - sum(fees_d.values()), 2)
    p = sell_probability(monthly, comp, profile.sell_window_days)
    exp_net = round(p * max(net, 0.0), 2)
    return ChannelEstimate(
        channel=channel, sale_price=round(sale_price, 2), gross=round(gross, 2),
        fees={k: round(v, 2) for k, v in fees_d.items()}, net_if_sold=net,
        monthly_sales_est=round(monthly, 1), competition=comp, sell_prob=p, expected_net=exp_net,
        max_buy=_max_buy(exp_net, profile),
        profit_if_sold_at_cost=round(net - profile.cost_per_book, 2),
        expected_profit_at_cost=round(exp_net - profile.cost_per_book, 2),
        basis=basis,
    )


def amazon_landed_price(m: MarketData) -> tuple[Optional[float], str]:
    """Price a used copy competes at on Amazon, including shipping."""
    if m.amazon_used_good_landed:
        return m.amazon_used_good_landed, f"lowest Good+ used ${m.amazon_used_good_landed:.2f} incl. shipping"
    if m.amazon_used_low:
        p = m.amazon_used_low + 3.99
        return p, f"lowest used ${m.amazon_used_low:.2f} + $3.99 shipping"
    if m.amazon_used_buybox_landed:
        return m.amazon_used_buybox_landed, f"used Buy Box ${m.amazon_used_buybox_landed:.2f}"
    if m.amazon_new_low:
        p = round(m.amazon_new_low * 0.8, 2)
        return p, f"no used offers; 80% of lowest new ${m.amazon_new_low:.2f}"
    return None, ""


def estimate_channels(m: MarketData, profile: BuyerProfile, fees: FeeSchedule) -> list[ChannelEstimate]:
    out: list[ChannelEstimate] = []
    w = m.weight_lb or fees.default_weight_lb
    cond = CONDITION_MULT.get(profile.condition, 1.0)
    landed, basis = amazon_landed_price(m)
    a_monthly, a_basis = amazon_monthly_sales(m)
    a_comp = m.used_offer_count or 0

    if landed:
        landed *= cond
        if "amazon_fba" in profile.channels:
            price = landed  # FBA ships free to the buyer, so it competes on landed price
            f = {
                "referral": fees.amazon_referral_pct * price,
                "closing": fees.amazon_closing_fee,
                "fba_fulfillment": fba_fulfillment_fee(w, m.fba_pick_pack_fee, fees),
                "inbound_ship": fees.fba_inbound_per_lb * w,
                "prep": fees.fba_prep_per_unit,
            }
            out.append(_finish("amazon_fba", price, price, f, a_monthly, a_comp, profile,
                               f"Amazon {basis}; demand from {a_basis}"))
        if "amazon_fbm" in profile.channels:
            item = max(0.99, landed - fees.fbm_shipping_credit)
            gross = item + fees.fbm_shipping_credit
            f = {
                "referral": fees.amazon_referral_pct * gross,
                "closing": fees.amazon_closing_fee,
                "postage": media_mail(w, fees),
                "packaging": fees.packaging_per_unit,
            }
            out.append(_finish("amazon_fbm", item, gross, f, a_monthly, a_comp, profile,
                               f"Amazon {basis}; you ship Media Mail; demand from {a_basis}"))

    if "ebay" in profile.channels:
        e_price, e_basis = None, ""
        if m.ebay_sold_median:
            e_price, e_basis = m.ebay_sold_median, f"eBay sold median ${m.ebay_sold_median:.2f}"
        elif m.ebay_active_median_low5:
            e_price = m.ebay_active_median_low5 * 0.92
            e_basis = f"92% of eBay's 5 cheapest asks (${m.ebay_active_median_low5:.2f} median)"
        elif m.keepa_ebay_used_landed:
            e_price = m.keepa_ebay_used_landed * 0.92
            e_basis = f"92% of lowest eBay used ${m.keepa_ebay_used_landed:.2f} (via Keepa)"
        elif landed:
            e_price = landed * 0.85
            e_basis = "85% of the Amazon price (no eBay data)"
        if e_price:
            e_price *= cond if not m.ebay_sold_median else 1.0
            if m.ebay_sold_count_90d is not None:
                e_monthly = m.ebay_sold_count_90d / 3
                d_basis = f"{m.ebay_sold_count_90d} sold in 90d"
            else:
                e_monthly = a_monthly * 0.3
                d_basis = "30% of Amazon demand (no eBay sold data)"
            e_comp = m.ebay_active_count if m.ebay_active_count is not None else a_comp
            f = {
                "final_value": fees.ebay_fvf_pct * e_price,
                "order_fee": fees.ebay_order_fee if e_price > 10 else fees.ebay_order_fee_small,
                "postage": media_mail(w, fees),
                "packaging": fees.packaging_per_unit,
            }
            if fees.ebay_promoted_pct:
                f["promoted"] = fees.ebay_promoted_pct * e_price
            out.append(_finish("ebay", e_price, e_price, f, e_monthly, e_comp, profile,
                               f"{e_basis}, free shipping listing; demand from {d_basis}"))
    return out


def best_channel(channels: list[ChannelEstimate]) -> Optional[ChannelEstimate]:
    if not channels:
        return None
    return max(channels, key=lambda c: (c.expected_profit_at_cost, c.sell_prob))
