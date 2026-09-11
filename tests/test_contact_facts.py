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
    ("Suite 400, 200 Queen Street West, Toronto, M5V 3K9", "M5V 3K9"),
    ("12 MG Road, Bengaluru 560001", "560001"),
])
def test_postcodes_across_countries(text, expected):
    assert _first(POSTCODE_HINT_RE, text) == expected


@pytest.mark.parametrize("text,expected", [
    ("1600 Amphitheatre Parkway, Mountain View, CA 94043", "1600 Amphitheatre Parkway"),
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
    "Call 01904 555 812 to order.",
])
def test_a_phone_number_alone_never_produces_a_postcode(text):
    """This is the bug in its general form: a run of digits is not a postcode."""
    assert POSTCODE_HINT_RE.search(text) is None


@pytest.mark.parametrize("text", [
    "Founded in 2011 by two potters.",
    "Over 5000 pieces made since we opened.",
])
def test_prose_with_numbers_is_not_read_as_an_address(text):
    assert _street_hint(text) is None


@pytest.mark.parametrize("text,expected", [
    ("Reach us on 0800 123 4567 any weekday.", "0800 123 4567"),
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


# --------------------------------------------------------------------------
# A number the words disown is not a postcode
# --------------------------------------------------------------------------

from page_extract import _postcode_hint, _street_hint  # noqa: E402


@pytest.mark.parametrize("html,expected", [
    # A bank's footer. Every regulator requires a number printed there, which
    # is why shape alone read one as the postcode and published it inside
    # paste-ready PostalAddress markup.
    ("<footer><p>Authorised and regulated by the Financial Conduct Authority. "
     "Financial Services Register number 730427.</p></footer>", None),
    ("<footer><p>VAT no. 123456</p></footer>", None),
    ("<footer><p>Item ID 374819</p></footer>", None),
    # A label that says postcode settles it the other way.
    ("<footer><p>Postcode 110001</p></footer>", "110001"),
    ("<footer><address>41 Walmgate, York YO1 9TT</address></footer>", "YO1 9TT"),
])
def test_a_number_labelled_as_something_else_is_not_a_postcode(html, expected):
    soup = make_soup(html)
    text = soup.get_text(" ")
    match = _postcode_hint(text, soup, _street_hint(text))[0]
    assert (match.group(0).strip() if match else None) == expected


def test_a_real_postcode_beside_a_registration_number_still_reads():
    html = ("<footer><p>Company registration number 084213. Registered office: "
            "41 Walmgate, York YO1 9TT.</p></footer>")
    soup = make_soup(html)
    text = soup.get_text(" ")
    match = _postcode_hint(text, soup, _street_hint(text))[0]
    assert match is not None and match.group(0).strip() == "YO1 9TT"


# --------------------------------------------------------------------------
# An address written the way its own country writes one
# --------------------------------------------------------------------------

def test_a_japanese_postal_address_is_a_postal_address():
    """A postal group's contact page carries one and the report said no postal
    address was found anywhere on the site."""
    from page_extract import _postcode_in
    text = "所在地 〒100-8791 東京都千代田区大手町二丁目3番1号"
    assert _street_hint(text) is not None
    assert _postcode_in(text) is not None


def test_the_digits_inside_a_web_address_are_not_a_telephone_number():
    """A recipe blog's stated contact detail was 3922587923, which is the
    middle of a photo-sharing URL."""
    from page_extract import text_without_urls
    text = "More at photos.example.org/photos/someone/39225879230/ today"
    assert "39225879230" not in text_without_urls(text)
    facts = _contact_facts(text, make_soup("<html><body>{}</body></html>".format(text)))
    assert not facts["has_phone"]


def test_a_commit_identifier_is_not_a_telephone_number():
    """A documentation tree prints commit identifiers in its page footers. The
    digits inside one were reported to that project as its telephone number,
    which marked the "states a contact method" check as passed and suppressed
    the finding that the site states no way to reach anybody at all.

    Both sides are read, because a run can start a token as easily as end one.
    """
    from audit_common import not_a_telephone_number

    assert not_a_telephone_number("1234567890", before="Built from commit a1b2c",
                                  after="def0 on 4 June")
    assert not_a_telephone_number("1234567890", before="rev ", after="abcd")
    # A number written to be dialled survives, extension and all.
    assert not_a_telephone_number("020 7946 0958", before="Call us on ", after=".") == ""
    assert not_a_telephone_number("555 0142", before="Reception ", after=" x22") == ""


def test_the_digits_inside_a_commit_line_never_reach_the_snapshot():
    text = "Documentation built from commit a1b2c1234567890def0 on 4 June 2026."
    facts = _contact_facts(text, make_soup("<html><body>{}</body></html>".format(text)))
    assert not facts["has_phone"], facts


# --------------------------------------------------------------------------
# A number published as a click-to-chat link
#
# An Indonesian shoe brand's homepage carries
# `<a href="https://wa.me/6282127323136">`, and the report said, verbatim:
# "no email address, telephone number, contact form, mailto: or tel: link and
# no ContactPoint markup was found on any crawled page." The number is in the
# delivered bytes of a page the crawl fetched.
#
# This is not an edge case in the population the audit is written for. A
# click-to-chat link is how a very large share of businesses in South and
# South-East Asia publish their telephone number, and for many of them it is
# the only place it appears.
#
# Widening the text patterns could never have reached it: `text_without_urls`
# takes web addresses out before any text scan, deliberately, because a photo
# id inside a URL was once published as a recipe blog's telephone number. The
# number here is in the href.
# --------------------------------------------------------------------------

CLICK_TO_CHAT_PAGE = (
    '<html><body><p>Order via chat.</p>'
    '<a href="https://wa.me/6282127323136">Chat on WhatsApp</a>'
    '</body></html>'
)


def test_a_whatsapp_click_to_chat_link_states_a_telephone_number():
    facts = _contact_facts("Order via chat.", make_soup(CLICK_TO_CHAT_PAGE))
    assert facts["has_phone"], facts
    assert facts["phones"] == ["+6282127323136"]
    # Declared, not inferred: the site put it in a link, exactly as a `tel:`
    # href is a declaration. Consumers that compare numbers between pages read
    # this list and must be able to trust it.
    assert facts["declared_phones"] == ["+6282127323136"]


def test_the_number_is_written_in_the_international_format_the_url_means():
    """The `+` is not decoration.

    The path is the number in international format with the `+` left off, so
    reading it verbatim gives 6282127323136 - thirteen unbroken digits, which
    `not_a_telephone_number` rejects as a barcode. Every Indonesian mobile
    number in this shape is thirteen digits.
    """
    from audit_common import not_a_telephone_number
    from page_extract import _phone_from_click_to_chat

    assert not_a_telephone_number("6282127323136")
    assert not_a_telephone_number("+6282127323136") == ""
    assert _phone_from_click_to_chat("https://wa.me/6282127323136") == "+6282127323136"


@pytest.mark.parametrize("href,expected", [
    # The shapes whose URL slot is defined to hold a number.
    ("https://wa.me/6282127323136", "+6282127323136"),
    ("https://wa.me/6282127323136?text=Halo", "+6282127323136"),
    ("//wa.me/6282127323136", "+6282127323136"),
    ("https://api.whatsapp.com/send?phone=919910901961&text=hi", "+919910901961"),
    ("https://web.whatsapp.com/send?phone=6591234567", "+6591234567"),
    ("whatsapp://send?phone=447700900123", "+447700900123"),
    ("viber://chat?number=%2B6282127323136", "+6282127323136"),
    ("sms:+6282127323136?body=hi", "+6282127323136"),
    # A share button. `wa.me/?text=` has an empty path and the `send`
    # endpoint's share form carries no `phone`, so neither holds a number -
    # which is the whole reason the reading is of a defined number slot and
    # never of a host.
    ("https://wa.me/?text=Look%20at%20this", ""),
    ("https://api.whatsapp.com/send?text=Look%20at%20this", ""),
    # Handles, not numbers. Inventing a number out of one of these is how a
    # report would publish somebody's group invite as their phone number.
    ("https://wa.me/message/CJ7K2QABCD", ""),
    ("https://chat.whatsapp.com/EnnnnInvite1234", ""),
    ("https://m.me/some-page", ""),
    ("https://t.me/someone", ""),
    # Too short to be a number carrying a country code.
    ("https://wa.me/123", ""),
])
def test_which_chat_links_state_a_number_and_which_do_not(href, expected):
    from page_extract import _phone_from_click_to_chat
    assert _phone_from_click_to_chat(href) == expected


def test_a_share_button_alone_leaves_the_site_stating_no_number():
    """The failure this reading must not introduce: a page whose only WhatsApp
    link is a share button has published no telephone number, and saying it has
    would suppress the true finding that the site states no contact route."""
    html = ('<html><body><p>Tell a friend.</p>'
            '<a href="https://wa.me/?text=Look">Share on WhatsApp</a></body></html>')
    facts = _contact_facts("Tell a friend.", make_soup(html))
    assert not facts["has_phone"], facts
    assert facts["declared_phones"] == []


def test_one_number_written_twice_is_one_number():
    """A footer carrying both `tel:` and a chat link states one number.

    Recording two is what turns one contact route into a self-contradiction
    downstream, where the numbers a site declares are compared between its
    pages. The trunk zero in the local form is what the country code replaces.
    """
    html = ('<html><body><footer>'
            '<a href="tel:082127323136">Call</a>'
            '<a href="https://wa.me/6282127323136">WhatsApp</a>'
            '</footer></body></html>')
    facts = _contact_facts("", make_soup(html))
    assert facts["declared_phones"] == ["082127323136"]
    # And the footer is the site speaking for itself, so the same number is
    # recorded as the site's own rather than as one the page merely mentions.
    assert facts["chrome_phones"] == ["082127323136"]


def test_a_chat_link_in_the_footer_is_the_site_speaking_for_itself():
    html = ('<html><body><footer><p>Hubungi kami</p>'
            '<a href="https://wa.me/6282127323136">WhatsApp</a>'
            '</footer></body></html>')
    facts = _contact_facts("Hubungi kami", make_soup(html))
    assert facts["chrome_phones"] == ["+6282127323136"]


def test_the_page_records_how_many_chat_links_state_a_number():
    """Counted beside `tel_count` rather than folded into it: the contact-route
    check names which kind of link it found, and a reader checks the page."""
    from page_extract import _links

    links = _links(make_soup(CLICK_TO_CHAT_PAGE), "https://kasut.test/", "kasut.test")
    assert links["click_to_chat_count"] == 1
    assert links["tel_count"] == 0
