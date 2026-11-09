import math

import pytest

from app.config import FeeSchedule
from app.isbn import to_isbn10, to_isbn13
from app.models import BuyerProfile, MarketData
from app.profit import (best_channel, estimate_channels, fba_fulfillment_fee, media_mail,
                        rank_to_monthly_sales, sell_probability)

FEES = FeeSchedule()


def test_isbn_roundtrip():
    assert to_isbn13("0-465-02656-7") == "9780465026562"
    assert to_isbn13("9780465026562") == "9780465026562"
    assert to_isbn10("9780465026562") == "0465026567"
    assert to_isbn13("9780465026563") == ""  # bad check digit
    assert to_isbn13("080442957X") == "9780804429573"


def test_media_mail_rounds_up_by_pound():
    assert media_mail(0.4, FEES) == FEES.media_mail_first_lb
    assert media_mail(1.0, FEES) == FEES.media_mail_first_lb
    assert media_mail(1.01, FEES) == round(FEES.media_mail_first_lb + FEES.media_mail_addl_lb, 2)


def test_fba_fee_prefers_keepa_and_applies_surcharge():
    assert fba_fulfillment_fee(2.0, 5.00, FEES) == round(5.00 * 1.035, 2)
    light, heavy = fba_fulfillment_fee(0.4, None, FEES), fba_fulfillment_fee(4.0, None, FEES)
    assert light < heavy
    # the fallback table is continuous across the 3 lb break
    assert fba_fulfillment_fee(3.01, None, FEES) - fba_fulfillment_fee(3.0, None, FEES) < 0.5


def test_rank_curve_shape():
    assert rank_to_monthly_sales(100_000) == pytest.approx(30, rel=0.15)
    assert rank_to_monthly_sales(1_000_000) == pytest.approx(1, rel=0.2)
    assert rank_to_monthly_sales(None) == 0


def test_sell_probability_behaves():
    assert sell_probability(0, 5, 90) == 0
    assert sell_probability(30, 5, 90) > 0.99
    assert sell_probability(1, 20, 90) < sell_probability(1, 2, 90)
    assert sell_probability(1, 5, 30) < sell_probability(1, 5, 180)


def test_fba_math_by_hand():
    m = MarketData(isbn13="9780465026562", amazon_used_good_landed=20.00, weight_lb=1.0,
                   rank_drops_30=30, used_offer_count=10)
    prof = BuyerProfile(channels=["amazon_fba"], cost_per_book=1, min_profit=5, min_roi=1)
    (c,) = estimate_channels(m, prof, FEES)
    fees = 20 * 0.15 + 1.80 + round(3.77 * 1.035, 2) + 0.50 * 1.0 + 0.30
    assert c.net_if_sold == pytest.approx(20 - fees, abs=0.01)
    exp_net = c.sell_prob * c.net_if_sold
    assert c.max_buy == pytest.approx(max(0, min(exp_net - 5, exp_net / 2)), abs=0.01)
    assert c.profit_if_sold_at_cost == pytest.approx(c.net_if_sold - 1, abs=0.01)


def test_fbm_gets_shipping_credit_and_pays_referral_on_it():
    m = MarketData(isbn13="x", amazon_used_good_landed=15.99, weight_lb=0.8, rank_drops_30=10, used_offer_count=3)
    (c,) = estimate_channels(m, BuyerProfile(channels=["amazon_fbm"]), FEES)
    assert c.gross == pytest.approx(15.99, abs=0.01)
    assert c.sale_price == pytest.approx(12.00, abs=0.01)
    assert c.fees["referral"] == pytest.approx(15.99 * 0.15, abs=0.01)


def test_ebay_prefers_sold_comps_over_asks():
    m = MarketData(isbn13="x", ebay_sold_median=30.0, ebay_sold_count_90d=9,
                   ebay_active_median_low5=50.0, ebay_active_count=6, weight_lb=1)
    (c,) = estimate_channels(m, BuyerProfile(channels=["ebay"]), FEES)
    assert c.sale_price == 30.0
    assert c.monthly_sales_est == 3.0
    assert c.fees["order_fee"] == FEES.ebay_order_fee


def test_slow_seller_lowers_max_buy():
    fast = MarketData(isbn13="a", amazon_used_good_landed=30, rank_drops_30=40, used_offer_count=5, weight_lb=1)
    slow = fast.model_copy(update={"rank_drops_30": None, "sales_rank": 3_000_000, "used_offer_count": 25})
    prof = BuyerProfile()
    assert best_channel(estimate_channels(slow, prof, FEES)).max_buy < best_channel(estimate_channels(fast, prof, FEES)).max_buy


def test_condition_multiplier():
    m = MarketData(isbn13="a", amazon_used_good_landed=20, rank_drops_30=10, used_offer_count=2, weight_lb=1)
    good = estimate_channels(m, BuyerProfile(channels=["amazon_fba"], condition="good"), FEES)[0]
    acc = estimate_channels(m, BuyerProfile(channels=["amazon_fba"], condition="acceptable"), FEES)[0]
    assert math.isclose(acc.sale_price, good.sale_price * 0.75, abs_tol=0.01)
