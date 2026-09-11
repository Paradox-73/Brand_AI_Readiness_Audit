# -*- coding: utf-8 -*-
"""Three readers that were calibrated on English-speaking Western sites.

Each one failed the same way: a rule written from what one part of the world
prints was applied as if it were a rule about the world, so a fact the crawl
had already stored became a fact the report said did not exist. All three
produced high-severity findings, and all three were wrong about pages whose
text is in the snapshot.

  money             the currency list carried the rupee sign and not "Rs.",
                    which is what a South Asian shop actually prints. Every
                    product page's price was invisible, and the only figure
                    left was a free-delivery floor out of a site-wide banner -
                    reported nineteen times as an Offer price that "is not
                    among the prices shown on the page", and then again by two
                    engagement checks as the price nineteen pages publish

  addresses         a street had to carry a Western type word or open with a
                    bare house number, so two South Asian footers stating a
                    registered office on every crawled page recorded
                    `has_address: false` - while the same report's "what an
                    assistant would quote" table quoted one of them. And in
                    the other direction, a product code beside a price became
                    a street and a postcode on four pages of a Japanese shop,
                    which the report read as four branches

  off-site accounts a profile was dropped unless its URL contained the brand
                    name, and the brand name was in a script no handle can be
                    written in. Five official accounts, one on each of five
                    crawled pages, all dropped - and the verdict then said
                    "nothing off the site corroborates it" about a city
                    government

Every name, place and handle below is invented. Naming a real site is what
`test_marketplace.py::test_no_real_domain_names_anywhere` exists to stop.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    CURRENCY_SYMBOLS, find_prices, has_price, host_name_forms, price_value,
    quotes_its_own_price, SkillResult,
)
import page_extract as EXTRACT  # noqa: E402


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FRESHNESS = _module("freshness-corroboration-audit", "freshness_locale")


# --------------------------------------------------------------------------
# Reader 1 - a rupee is not a currency
# --------------------------------------------------------------------------

# The banner is site-wide and the price is the page's own, in the order the
# delivered document prints them.
PRODUCT_PAGE_TEXT = (
    "FREE SHIPPING ON ORDERS ABOVE ₹500 "
    "Everyday Slip-On MRP Rs. 399 Rs. 399 Add to cart In stock")


def test_the_price_a_south_asian_shop_prints_is_a_price():
    """The failure itself. Nineteen product pages printing "MRP Rs. 399 Rs.
    399" recorded one figure between them - the 500 from the delivery banner -
    and each was reported at high severity as declaring an Offer price that is
    not among the prices shown on the page.
    """
    prices = find_prices(PRODUCT_PAGE_TEXT)
    assert prices == ["Rs. 399", "Rs. 399"], prices
    assert price_value(prices[0]) == 399.0


def test_the_delivery_floor_is_not_one_of_the_prices_on_the_page():
    """The second half of the same failure. Once the real price is readable
    the threshold still sits in front of it, and a reader that keeps it hands
    the pricing check a figure to disagree with and the engagement checks a
    delivery rule to call the page's price.
    """
    assert find_prices("FREE SHIPPING ON ORDERS ABOVE ₹500") == []
    assert not has_price("Free shipping over $40")
    assert not quotes_its_own_price("Free delivery on all orders above $75.")


def test_a_page_whose_only_figure_is_a_floor_is_not_a_page_that_sells():
    """`quotes_its_own_price` gates the whole pricing family. A shipping banner
    on a page with nothing for sale used to open it."""
    assert not quotes_its_own_price(
        "Free shipping on orders above ₹500. We are open seven days.")
    assert quotes_its_own_price(PRODUCT_PAGE_TEXT)


@pytest.mark.parametrize("written,value", [
    ("Rs. 399", 399.0),          # the abbreviation with its full stop
    ("Rs 1,299", 1299.0),        # and without
    ("RM 500", 500.0),           # Malaysia
    ("Rp 25.000", 25000.0),      # Indonesia, grouped with full stops
    ("RMB 100", 100.0),          # written out rather than as the yuan sign
    ("S$12.50", 12.5),           # a shared sign, said which one it is
    ("HK$99", 99.0),
    ("₨ 1,200", 1200.0),    # the rupee sign with two strokes
    ("৳ 500", 500.0),       # the taka sign
    ("￥ 4,620", 4620.0),    # the fullwidth yen a Japanese page writes
    ("PKR 4,500", 4500.0),
    ("1,499 LKR", 1499.0),
])
def test_money_in_the_notations_these_markets_print(written, value):
    found = find_prices("Our price today " + written + " including tax")
    assert found, written
    assert price_value(found[0]) == value, (written, found)


@pytest.mark.parametrize("text", [
    "We have 5,000 products and 60 years of history",
    "Sleeps 12 rm 4 with a private terrace",     # rooms, not ringgit
    "Reading, writing and arithmetic: the 3 Rs",
    "Delivered in 24 hrs",
    "The motor runs at 1200 rpm",
])
def test_a_run_of_letters_beside_a_number_is_not_money(text):
    """The guard that has to survive widening the pattern. The local
    abbreviations are read case-sensitively and only in front of the figure,
    because "12 rm" is twelve rooms and a lowercase "rs" is a run of letters.
    """
    assert find_prices(text) == [], text


def test_the_currency_signs_come_from_unicode_rather_than_a_list():
    """A hand-written list of symbols was completed three times and was wrong
    three times. The class is `Sc`, the Unicode general category for a currency
    sign, so a sign nobody here has thought of is already covered."""
    for sign in "$€£₹₨৳￥฿₫":
        assert sign in CURRENCY_SYMBOLS, sign
    assert len(CURRENCY_SYMBOLS) > 40


# --------------------------------------------------------------------------
# Reader 2 - an address is not only a Western street line
# --------------------------------------------------------------------------

# Two office lines out of one footer, in the shape the whole subcontinent
# writes: a labelled building number, no street-type word in the second, place
# names between commas, and a six-digit code behind a dash.
FOOTER_TEXT = (
    "Registered Office Anara Buildings, P.B. No. 61, Vellinni Iyer Road, "
    "Chalanam, Kottapuram, Marulam – 686 001 "
    "Corporate Office No. 45-A, 2nd Phase, Peniya Industrial Area, "
    "Bengapuram – 560 058")

CONTACT_PAGE_TEXT = (
    "Corporate Address: Invented Brands Pvt. Ltd. Godown #A5, Sairam Logistic "
    "Hub, Village Anjuram, Bhivandapur, Maharam, India, 421302")


def _address(text):
    street = EXTRACT._street_hint(text)
    postcode = EXTRACT._postcode_beside(text, street) if street else None
    return (street.group(0) if street else None,
            postcode.group(0) if postcode else None)


def test_a_footer_stating_a_registered_office_states_an_address():
    """Forty crawled pages carried this footer and every one recorded
    `has_address: false`. The report's first "start here" item was that no
    postal address appears in the page text or in the page markup, over text
    its own quotable-sentence table quoted."""
    street, postcode = _address(FOOTER_TEXT)
    assert street is not None
    assert "Vellinni Iyer Road" in street
    assert postcode == "686 001"


def test_an_address_with_no_street_type_word_is_still_an_address():
    """"Area", "Phase" and "Hub" are the type words here and none of them is in
    any list of street types. What says this is a building is the label in
    front of the number."""
    street = EXTRACT.STREET_LABELLED_NUMBER_RE.search(FOOTER_TEXT[FOOTER_TEXT.index(
        "Corporate Office"):])
    assert street is not None
    assert street.group(0).startswith("No. 45-A")

    street, postcode = _address(CONTACT_PAGE_TEXT)
    assert street is not None and street.startswith("#A5")
    assert postcode == "421302"


def test_a_postal_code_written_in_two_halves_is_a_postal_code():
    """"686 001" is one six-digit code, not two runs of three."""
    assert EXTRACT._postcode_in("Marulam – 686 001").group(0) == "686 001"
    assert EXTRACT._postcode_in("Bengapuram – 560 058").group(0) == "560 058"


def test_a_telephone_number_is_not_read_as_that_code():
    """The guard on the branch above: a run preceded by a digit and a space is
    the tail of a number somebody dials."""
    assert EXTRACT._postcode_in("Call 0484 566 001 for stockists") is None


# A price and the product code beside it, in the order the delivered document
# prints them. The 950 is the last three digits of the price.
LISTING_LINE = "¥4,950 TULIP CR B169HI 4 colours ¥4,620"


def test_a_product_code_beside_a_price_is_not_a_branch_address():
    """The reverse failure. "950 TULIP CR" was read as a street and "B169HI"
    as its postal code, on four pages of one shop, and the report said four
    pages print their own street address, that each address is different, and
    that the site should publish a LocalBusiness block per branch."""
    assert _address(LISTING_LINE) == (None, None)


def test_the_two_readings_that_reject_it_are_independent():
    """Either one alone leaves the other case live, so both are asserted: the
    number does not continue the figure in front of it, and the code is not a
    postal code."""
    # A code that satisfies the restricted final letters still fails because
    # the number it opens with is the tail of the price.
    assert _address("¥4,950 TULIP CR B169HL 4 colours") == (None, None)
    # And the shape test refuses the code on its own: C, I, K, M, O and V are
    # never used in the last two positions of a UK postal code.
    assert EXTRACT._postcode_in("TULIP CR B169HI") is None
    assert EXTRACT._postcode_in("Nether Wilby BD23 1EW").group(0) == "BD23 1EW"


@pytest.mark.parametrize("text,street", [
    # A named building with no house number, the case the named tier exists for.
    ("Address: The Gallery, Thornby Road, Ditton KT7 4LP", "Thornby Road"),
    # A numbered street with a locality between it and the code.
    ("12 Marish Road, Bengapuram 560001", "12 Marish Road"),
    ("350 5th Avenue, Newtown 10118", "350 5th Avenue"),
    # A sub-unit before the house number, which must not read as a figure the
    # house number continues.
    ("Unit 3, 45 High Street, Nether Wilby YO1 9TT", "45 High Street"),
])
def test_the_addresses_that_already_worked_still_work(text, street):
    assert _address(text)[0] == street


def test_a_japanese_address_still_reads_after_the_figure_guard():
    """The postal mark is followed by digits and a space, which is the shape
    the price guard looks for. It is not a currency figure, and the address
    after it is the one case in the file with nothing else to fall back on."""
    street, _ = _address(
        "所在地 〒100-8791 "
        "東京都千代田区大手町"
        "二丁目3番1号")
    assert street is not None


def test_a_ticket_number_is_still_not_a_house_number():
    """The label rule this file must not undo."""
    assert _address("5204 libssh blocking and infinite loop 12345") == (None, None)


# --------------------------------------------------------------------------
# Reader 3 - a romanised handle is not somebody else's account
# --------------------------------------------------------------------------

# A name written in a script no URL handle can carry. The site's own address
# is the only romanised form of it anywhere in the snapshot, and the leftmost
# label of that address is the department rather than the name.
NON_LATIN_NAME = "北浜市"
CIVIC_HOST = "www.city.kitahama.lg.example"

CIVIC_ACCOUNTS = [
    ("Instagram", "https://www.instagram.com/kitahama_bousai/"),
    ("Facebook", "https://www.facebook.com/kitahama.higashiku/"),
    ("X", "https://x.com/kitahamashigikai"),
    ("Twitter", "https://twitter.com/kitahama_nishiku"),
    ("YouTube", "https://www.youtube.com/@kitahamacity"),
]


def _civic_snapshot(accounts=CIVIC_ACCOUNTS, extra_pages=5):
    """One official account in the body of each of five pages, none in chrome."""
    pages = [{"url": "https://{}/page/{}".format(CIVIC_HOST, index),
              "page_type": "article",
              "social_profiles": {platform: url},
              "declared_profiles": {},
              "links": {"internal": [], "external": []}}
             for index, (platform, url) in enumerate(accounts)]
    pages += [{"url": "https://{}/other/{}".format(CIVIC_HOST, index),
               "page_type": "article", "social_profiles": {},
               "declared_profiles": {}, "links": {"internal": [], "external": []}}
              for index in range(extra_pages)]
    return {"origin": "https://" + CIVIC_HOST, "pages": pages,
            "brand": {"name": NON_LATIN_NAME, "domain_token": "city",
                      "host": CIVIC_HOST}}


def _breadth(snapshot):
    result = SkillResult("freshness-corroboration-audit")
    profiles = FRESHNESS._check_authoritative_profiles(
        result, snapshot, snapshot["pages"])
    return profiles, result


def test_a_governments_own_accounts_are_not_read_as_other_peoples():
    """The failure. Five official accounts in the snapshot, five dropped, and
    the report led "start here" with "Distinct off-site profiles linked from
    the crawled pages or listed in sameAs: none" - then a verdict paragraph
    saying nothing off the site corroborates it, about a city government."""
    profiles, result = _breadth(_civic_snapshot())
    assert len(profiles) == 5, sorted(profiles)
    assert result.signals["profiles_dropped_as_citations"] == []
    # Five is still under the level the study measured, so the finding may
    # stay - what it may not do any more is say there are none of them, at
    # high severity, at the top of what to fix first.
    thin = [f for f in result.findings if f["root_cause"] == "weak-corroboration"]
    for finding in thin:
        assert finding["severity"] != "high", finding["title"]
        assert "sameAs`: none" not in finding["evidence"]


def test_the_name_is_read_from_the_address_the_site_publishes():
    """`domain_token` is the leftmost label, and on a civic host the leftmost
    label is the department. The name is the label after it."""
    assert host_name_forms(CIVIC_HOST) == ["city", "kitahama"]
    # The registry's own labels are not forms of the site's name.
    assert host_name_forms("shop.madeup.gov.example") == ["shop", "madeup"]
    # An encoded non-Latin label is the name enciphered, not romanised, so
    # keeping it would leave a comparable string no handle can ever match.
    assert host_name_forms("xn--eckwd4c7c.example") == []


def test_a_name_no_handle_can_carry_does_not_reject_anything():
    """The rule under the fix, tested where the address gives no help either:
    where every name reduces to no comparable letters, the handle test cannot
    answer, so it must not be the thing that drops the account."""
    snapshot = _civic_snapshot()
    snapshot["brand"] = {"name": NON_LATIN_NAME, "domain_token": "",
                         "host": "xn--eckwd4c7c.example"}
    profiles, _ = _breadth(snapshot)
    assert len(profiles) == 5, sorted(profiles)


def test_somebody_elses_account_is_still_dropped_where_the_test_can_answer():
    """The rule this must not undo. A brand whose name is in the same letters
    as the handles keeps the reading that stopped an article's outbound
    citations being certified as the brand's own accounts."""
    accounts = list(CIVIC_ACCOUNTS[:2]) + [
        ("Bluesky", "https://bsky.app/profile/someone-elses-handle")]
    snapshot = _civic_snapshot(accounts)
    snapshot["brand"] = {"name": "Kitahama", "domain_token": "kitahama",
                         "host": "kitahama.example"}
    profiles, result = _breadth(snapshot)
    assert "Bluesky" not in profiles
    assert result.signals["profiles_dropped_as_citations"] == [
        "https://bsky.app/profile/someone-elses-handle"]


def test_the_finding_describes_the_reading_the_code_performs():
    """A claim about method that the code does not implement is worse than a
    wrong count. The finding said it had read "links anywhere on the crawled
    pages, body, header and footer alike", and a body link on one page is
    exactly what it drops."""
    snapshot = _civic_snapshot(CIVIC_ACCOUNTS[:1])
    snapshot["brand"] = {"name": "Kitahama", "domain_token": "kitahama",
                         "host": "kitahama.example"}
    _, result = _breadth(snapshot)
    finding = [f for f in result.findings
               if f["root_cause"] == "weak-corroboration"][0]
    checked = " ".join(finding["checked"])
    assert "anywhere on the crawled pages" not in checked
    assert "header or footer is the site's own" in checked
    assert "body of one page only" in checked


# --------------------------------------------------------------------------
# What the source may contain
# --------------------------------------------------------------------------

@pytest.mark.parametrize("relative", [
    "skills/audit-orchestrator/scripts/audit_common.py",
    "skills/audit-orchestrator/scripts/page_extract.py",
    "skills/freshness-corroboration-audit/scripts/check.py",
])
def test_no_control_character_reaches_the_source(relative):
    """A pattern built by pasting a character rather than naming it puts an
    invisible byte in the file, and the next person to read the line cannot see
    what it matches."""
    with io.open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        text = handle.read()
    bad = sorted({ord(ch) for ch in text
                  if ord(ch) < 0x20 and ch not in "\n\t\r"})
    assert not bad, "{}: control characters {}".format(relative, bad)
