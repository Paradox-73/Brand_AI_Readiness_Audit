"""Address, postcode and telephone, extracted from the page.

These feed two things: `nap-inconsistency`, which reports a brand as
contradicting itself when the same fact is written two ways across its site,
and the LocalBusiness snippet the report hands a developer to paste.

Nobody had looked at the values until a developer was asked to actually use
them. On a contact page reading

    41 Walmgate, York, YO1 9TT. 01904 555 812.

the extractor produced no street at all, a postcode of "01904" - the phone's
area code - and a telephone of "1904 555 812" with the leading zero missing.

Each of those is its own bug:

  - The street pattern required a street-type word (Street, Road, Avenue). Much
    of the UK has none: the type is part of the name (Walmgate, Petergate) or
    absent entirely.
  - The postcode alternation tried the bare five-digit US form first, and a
    five-digit run is also what a phone area code looks like.
  - `\\b` before the phone pattern skipped a leading zero, producing a number
    that no longer matched the same number written elsewhere on the site.
"""

from __future__ import annotations

import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from page_extract import (  # noqa: E402
    PHONE_RE, POSTCODE_HINT_RE, _contact_facts, _street_hint, make_soup,
)


def _first(pattern, text):
    match = pattern.search(text)
    return match.group(0).strip() if match else None


# --------------------------------------------------------------------------
# The case that started it
# --------------------------------------------------------------------------

WALMGATE = "41 Walmgate, York, YO1 9TT. 01904 555 812."


def test_a_uk_address_with_no_street_type_word_is_found():
    assert _street_hint(WALMGATE).group(0) == "41 Walmgate, York"


def test_the_postcode_is_the_postcode_not_the_phone_area_code():
    assert _first(POSTCODE_HINT_RE, WALMGATE) == "YO1 9TT"


def test_the_telephone_keeps_its_leading_zero():
    """Without it, the same number written twice on one site disagrees."""
    assert _first(PHONE_RE, WALMGATE) == "01904 555 812"


# --------------------------------------------------------------------------
# Formats that must keep working
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1600 Amphitheatre Parkway, Mountain View, CA 94043", "94043"),
    ("350 Fifth Avenue, New York, NY 10118", "10118"),
    ("Suite 400, 200 Queen Street West, Toronto, M5V 3K9", "M5V 3K9"),
    ("12 MG Road, Bengaluru 560001", "560001"),
    ("41 Walmgate, York, YO1 9TT", "YO1 9TT"),
])
def test_postcodes_across_countries(text, expected):
    assert _first(POSTCODE_HINT_RE, text) == expected


@pytest.mark.parametrize("text,expected", [
    ("1600 Amphitheatre Parkway, Mountain View, CA 94043", "1600 Amphitheatre Parkway"),
    ("350 Fifth Avenue, New York, NY 10118", "350 Fifth Avenue"),
    ("Suite 400, 200 Queen Street West, Toronto, M5V 3K9", "200 Queen Street"),
    ("12 MG Road, Bengaluru 560001", "12 MG Road"),
])
def test_streets_across_countries(text, expected):
    assert (_street_hint(text).group(0) if _street_hint(text) else "") == expected


# --------------------------------------------------------------------------
# What must NOT be found
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Reach us on 0800 123 4567 any weekday.",
    "Sales: 020 7946 0018",
    "Call 01904 555 812 to order.",
])
def test_a_phone_number_alone_never_produces_a_postcode(text):
    """This is the bug in its general form: a run of digits is not a postcode."""
    assert POSTCODE_HINT_RE.search(text) is None


@pytest.mark.parametrize("text", [
    "Reach us on 0800 123 4567 any weekday.",
    "Founded in 2011 by two potters.",
    "Over 5000 pieces made since we opened.",
])
def test_prose_with_numbers_is_not_read_as_an_address(text):
    assert _street_hint(text) is None


@pytest.mark.parametrize("text,expected", [
    ("Reach us on 0800 123 4567 any weekday.", "0800 123 4567"),
    ("Sales: 020 7946 0018", "020 7946 0018"),
])
def test_phone_numbers_keep_their_full_form(text, expected):
    assert _first(PHONE_RE, text) == expected


# --------------------------------------------------------------------------
# Numbers that are not addresses
#
# Both of these shipped. Both produced a high-severity "the site contradicts
# its own contact details" finding, and one of them put its invented value
# into a paste-ready Organization snippet, offering the site a postal code
# scraped out of a price as the thing to publish.
# --------------------------------------------------------------------------

PRICING_PAGE = (
    "Pricing that scales. $0.00005 / event after the first 1 million rows. "
    "Volume discounts apply above 20 million events per month."
)
PRODUCT_PAGE = (
    "3in Round 3-Ring Binder for Lifeguarding Manual. Item ID 374819. "
    "In stock. Ships in 2 business days."
)


def test_a_price_is_not_a_postal_code():
    """"$0.00005 / event" was read as postal code 00005."""
    assert POSTCODE_HINT_RE.search(PRICING_PAGE) is None, (
        "a decimal fraction must not match the postal-code shape")


def test_a_row_count_is_not_a_street_address():
    """"1 million rows" was published as a streetAddress in a fix snippet.

    The capitalised-words branch is the only thing separating a street name
    from any other phrase opening with a number, and `re.I` had switched that
    requirement off.
    """
    assert _street_hint(PRICING_PAGE) is None


def test_an_item_number_outside_an_address_block_is_not_a_postal_code():
    """A retailer's SKU was reported as the postal code its markup disagreed with."""
    soup = make_soup("<html><body><p>{}</p></body></html>".format(PRODUCT_PAGE))
    facts = _contact_facts(PRODUCT_PAGE, soup)
    assert facts["postcode_hint"] == "", (
        "a six-digit run with no address around it is not an address")
    assert facts["has_address"] is False


def test_a_postal_code_inside_an_address_block_is_still_read():
    """The fix must not cost us the addresses that are real."""
    html = ("<html><body><address>Acme Ltd, 41 Walmgate, York YO1 9TT</address>"
            "</body></html>")
    soup = make_soup(html)
    facts = _contact_facts(soup.get_text(" "), soup)
    assert facts["postcode_hint"] == "YO1 9TT"
    assert facts["has_address"] is True


def test_a_postal_code_with_a_street_beside_it_is_still_read_without_an_address_block():
    text = "Visit us at 1600 Pennsylvania Avenue, Washington 20500 for a tour."
    soup = make_soup("<html><body><p>{}</p></body></html>".format(text))
    facts = _contact_facts(text, soup)
    assert facts["postcode_hint"] == "20500"
