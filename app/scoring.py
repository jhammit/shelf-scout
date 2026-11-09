"""Turn market math into a decision: pull it, check it, or skip it."""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from .config import FeeSchedule
from .models import BookResult, BuyerProfile
from .profit import LABEL, best_channel, estimate_channels

COLLECTIBLE_WORDS = ("first edition", "1st edition", "signed", "limited", "easton", "franklin",
                     "folio society", "leather", "advance reader", "boxed set")
LOW_VALUE_WORDS = ("book club", "reader's digest", "readers digest", "condensed")
EDITION_SURE = 0.55


def _clue_text(book: BookResult) -> str:
    sp = book.spine
    if not sp:
        return ""
    return " ".join(sp.edition_clues + [sp.format_guess.replace("_", " ")]).lower()


def search_links(book: BookResult) -> dict[str, str]:
    if book.chosen_isbn:
        i = book.chosen_isbn
        return {
            "amazon": f"https://www.amazon.com/s?k={i}",
            "ebay_sold": f"https://www.ebay.com/sch/i.html?_nkw={i}&_sacat=267&LH_Sold=1&LH_Complete=1",
            "bookscouter": f"https://bookscouter.com/book/{i}",
        }
    sp = book.spine
    q = quote_plus(f"{sp.title} {sp.author}".strip()) if sp else ""
    return {
        "amazon": f"https://www.amazon.com/s?k={q}&i=stripbooks",
        "ebay_sold": f"https://www.ebay.com/sch/i.html?_nkw={q}&_sacat=267&LH_Sold=1&LH_Complete=1",
        "abebooks": f"https://www.abebooks.com/servlet/SearchResults?kn={q}",
    }


def evaluate(book: BookResult, profile: BuyerProfile, fees: FeeSchedule) -> BookResult:
    clues = _clue_text(book)
    collectible = any(w in clues for w in COLLECTIBLE_WORDS)
    low_value = any(w in clues for w in LOW_VALUE_WORDS)
    textbook = "textbook" in clues or bool(
        book.spine and re.search(r"\d+\s*(st|nd|rd|th)\s+ed", book.spine.series_or_volume.lower())
    )
    reasons, flags = [], []

    if low_value:
        flags.append("Book club / condensed edition — these rarely resell for more than a dollar or two.")
    if collectible:
        why = [w for w in book.spine.edition_clues if any(c in w.lower() for c in COLLECTIBLE_WORDS)]
        if book.spine.format_guess == "leather_fine_binding" and not why:
            why = ["fine binding"]
        flags.append(f"Possible collectible ({', '.join(why)}) — check the copyright page and eBay sold "
                     "listings; spine-based prices assume a common printing.")
    if "ex-library" in clues or "library binding" in clues:
        flags.append("Ex-library copy — expect a lower price; many Amazon buyers avoid them.")
    if textbook:
        flags.append("Textbook — the edition number decides the price. Verify the ISBN before paying.")

    book.search_links = search_links(book)

    if not book.market:
        book.tier = "check" if collectible else "skip"
        book.score = 1.0 if collectible else -1.0
        if not book.candidates:
            reasons.append("No ISBN match found from the spine — older and fine-binding editions often lack one.")
        else:
            reasons.append("No marketplace data for the likely edition.")
        book.headline = ("Could be a collectible with no ISBN. Check eBay sold listings by title."
                         if collectible else reasons[0])
        book.reasons, book.flags = reasons, flags
        return book

    channels = estimate_channels(book.market, profile, fees)
    best = best_channel(channels)
    alt_best = None
    if book.alt_market:
        alt_best = best_channel(estimate_channels(book.alt_market, profile, fees))

    # If two plausible editions differ a lot in value, decide on the cheaper one.
    spread = False
    m = book.market
    if best and alt_best:
        hi, lo = sorted([best.expected_net, alt_best.expected_net], reverse=True)
        spread = hi - lo > max(3.0, 0.4 * hi)
        if spread:
            flags.append(
                f"Editions differ in value: ${lo:.0f}–${hi:.0f} net. Pull it and enter the ISBN to see which you have."
            )
        if alt_best.expected_net < best.expected_net and book.edition_confidence < 0.75:
            best = alt_best.model_copy(update={"basis": alt_best.basis + " (more conservative edition)"})
            m = book.alt_market

    book.channels, book.best = channels, best
    if not best:
        book.tier, book.score = ("check" if collectible else "skip"), -1.0
        reasons.append("Not enough price data to estimate a resale value.")
        book.headline = "Could be a collectible. Look it up by hand." if collectible else reasons[0]
        book.reasons, book.flags = reasons, flags
        return book

    rank = m.sales_rank_avg90 or m.sales_rank
    demand = f"~{best.monthly_sales_est:g} sales/mo" + (f" (rank #{rank:,})" if rank else "")
    reasons.append(f"{demand} with {best.competition} competing used offers.")
    if book.edition_confidence < EDITION_SURE and book.candidates:
        reasons.append(f"Edition match is uncertain ({book.edition_confidence:.0%}): "
                       "the spine fits more than one printing.")

    profitable = best.max_buy >= profile.cost_per_book and best.profit_if_sold_at_cost >= profile.min_profit
    upside = any(c.profit_if_sold_at_cost >= profile.min_profit and c.sell_prob >= 0.2
                 for c in channels + ([alt_best] if alt_best else []))
    where = LABEL[best.channel]
    odds = f"{best.sell_prob:.0%} chance it sells in {profile.sell_window_days} days"

    if profitable and best.sell_prob >= 0.5 and book.edition_confidence >= EDITION_SURE and not spread and not low_value:
        book.tier = "pull"
        book.headline = f"Nets ${best.net_if_sold:.2f} on {where}, {odds}."
    elif (profitable or upside or collectible) and not (low_value and not collectible):
        book.tier = "check"
        if spread:
            nets = sorted(c.net_if_sold for c in (best_channel(channels), alt_best) if c)
            book.headline = (f"Nets ${nets[0]:.2f} to ${nets[-1]:.2f} depending on the edition. "
                             "Pull it and check the ISBN.")
        elif collectible:
            book.headline = (f"A common copy nets ${best.net_if_sold:.2f}, but this could be a collectible. "
                             "Check the copyright page.")
        elif book.edition_confidence < EDITION_SURE:
            book.headline = f"Probably nets ${best.net_if_sold:.2f} on {where}, but the edition is uncertain."
        elif best.sell_prob < 0.5:
            book.headline = f"Nets ${best.net_if_sold:.2f} on {where}, but only a {odds}."
        else:
            book.headline = f"Nets ${best.net_if_sold:.2f} on {where}. Worth a closer look."
    else:
        book.tier = "skip"
        if low_value:
            book.headline = "Book club or condensed edition. These rarely resell."
        elif best.net_if_sold <= 0:
            book.headline = f"Fees eat the whole ${best.sale_price:.2f} sale price."
        elif best.sell_prob < 0.2:
            book.headline = f"Too slow: {odds}."
        elif best.net_if_sold < profile.min_profit and best.sell_prob >= 0.8:
            book.headline = f"Sells fast, but nets only ${best.net_if_sold:.2f} after fees."
        else:
            book.headline = (f"Nets ${best.net_if_sold:.2f}, short of your ${profile.min_profit:.2f} "
                             "profit target.")

    book.score = round(best.expected_profit_at_cost + (2.0 if collectible else 0.0), 2)
    book.reasons, book.flags = reasons, flags
    return book


TIER_ORDER = {"pull": 0, "check": 1, "skip": 2}


def sort_books(books: list[BookResult]) -> list[BookResult]:
    return sorted(books, key=lambda b: (TIER_ORDER[b.tier], -b.score))
