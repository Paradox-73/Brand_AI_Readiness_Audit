"""Whose address a page is printing, and the prices hidden in its menus.

Two readings, both of which turned a fact about somebody else into a fact
about the audited site.

*Whose address.* A street-shaped run is not the audited site's street. A
footwear retailer's product pages carry the legal disclosure that trade
requires - who manufactured the item, who markets it, and the premises of each
- and the manufacturer's factory was read off nine of them as one of the
brand's own places. Because the factories differ from one another the report
said the site "describes a place to visit without saying so in markup", called
it "what a business with separate branches looks like", and handed over a
paste-ready per-branch `LocalBusiness` block. Following it would publish a
supplier's factory as a shop. The same runs fed the contradiction check, which
reported thirteen conflicts against the head office of which one was real; and
on an open-source project a consultancy listed on the support-vendors page was
recorded as the project's own location, which marked the fact present and hid
the true finding that the project publishes no address at all.

Emails and telephone numbers already had the rule that fixes this - see
`chrome_emails`, added when a partners page's contact details were recorded as
the audited site's - and the street never did.

*Prices in a menu.* On a storefront the variant row is a `<select>`, and every
variant price but the default one is written inside an `<option>`. A product
page declaring an Offer price of 569.00 was compared against the only figure a
prose reader could see, and correct markup was reported at high severity as
contradicting the page. The option text is now recorded, apart from
`body_text`, because forty size labels are not prose.

Every brand, place and host below is invented.
"""

from __future__ import annotations

import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from page_extract import (  # noqa: E402
    CONTROL_OPTION_MAX, _contact_facts, _control_text, extract_page, make_soup,
)

HOST = "http://invented-shop.test"


def _facts(html, page_type=""):
    soup = make_soup(html)
    return _contact_facts(soup.get_text(" "), soup, page_type)


def _record(path, html):
    url = HOST + path
    return extract_page(url, url, 200, {}, html, [], 0, 0, "seed", HOST)


# --------------------------------------------------------------------------
# 1. An address the prose attributes to somebody else
# --------------------------------------------------------------------------

# The shape every such disclosure has, in any market and any language: a name,
# a relationship, and the premises of the party named. The two addresses are
# different places, which is exactly what made the report call them branches.
DISCLOSURE = (
    '<html><body><header><a href="/">Invented Footwear</a></header>'
    "<main><h1>Ladies sandal</h1><p>MRP 609 inclusive of all taxes.</p>"
    "<p>Manufactured by Sundry Shoe Makers (Plot #18/47 Begum Deori, "
    "Maithan, Argrapur - 282003, Country) marketed by Invented Footwear "
    "Retail (24 Kelvin Row, New Township, Portborough - 700156).</p>"
    "</main></body></html>"
)


def test_a_suppliers_premises_are_not_the_brands_address():
    """Nine product pages were reported as nine places to visit, and the fix
    offered was a `LocalBusiness` block per factory."""
    facts = _facts(DISCLOSURE, page_type="product")
    assert facts["street_hint"] == "", (
        "an address the page attributes to a named third party was recorded as "
        "the site's own: {!r}".format(facts["street_hint"]))
    assert facts["has_address"] is False
    assert "282003" not in (facts["postcode_hint"] or ""), (
        "the manufacturer's postal code was compared against the head office "
        "and produced twelve conflicts that were not conflicts")


def test_the_same_page_with_a_footer_states_the_head_office_instead():
    """The rule is about whose address it is, not about refusing to read one.
    The head office is in the furniture, so it is the site's by construction -
    and it is the same on every page, which is what stops nine product pages
    from counting as nine branches."""
    html = DISCLOSURE.replace(
        "</body>",
        "<footer><p>Invented Footwear Retail, 24 Kelvin Row, New Township, "
        "Portborough - 700156.</p></footer></body>")
    facts = _facts(html, page_type="product")
    assert facts["street_hint"] == "24 Kelvin Row"
    assert facts["street_source"] == "in the site header or footer"
    assert facts["postcode_hint"] == "700156"


def test_a_vendor_listed_on_a_support_page_is_not_the_projects_address():
    """`core-facts-present` printed a consultancy's street as the project's
    location, which marked the fact present and suppressed the true finding
    that the project publishes no address anywhere."""
    html = (
        '<html><body><header><a href="/">Invented Toolkit</a></header>'
        "<main><h1>Commercial support</h1>"
        "<p>These consultancies offer paid support. They are not part of the "
        "project.</p><ul><li>Alguma Consultancy, 152 Jundiary Avenue, "
        "Saopedro 13201, Country</li></ul></main>"
        "<footer><p>Released under a permissive licence.</p></footer>"
        "</body></html>"
    )
    facts = _facts(html)
    assert facts["street_hint"] == ""
    assert facts["has_address"] is False


# --------------------------------------------------------------------------
# 2. The ways a page does say an address is its own
# --------------------------------------------------------------------------

def test_an_address_element_beats_the_site_footer():
    """A branch page's own address is the branch's. Reading the furniture
    first would hand every branch page the head office, collapse a chain to
    one place, and put the whole multi-location branch of the report in the
    dark - which is what `postcode_source` was added to stop."""
    html = ("<html><body><main><h1>Ashcombe shop</h1>"
            "<address>27 Harbour Lane, Ashcombe KT7 4LP</address></main>"
            "<footer><p>Invented Bakers, 8 Kelvin Row, Ashcombe SE16 4RA.</p>"
            "</footer></body></html>")
    facts = _facts(html)
    assert facts["street_hint"] == "27 Harbour Lane"
    assert facts["street_source"] == "in an address region"
    assert facts["postcode_hint"] == "KT7 4LP"


def test_a_page_about_the_site_states_its_address_as_its_subject():
    """A branch page under `/shops/<name>` is typed `location`, and stating
    where it is is the whole job of the page."""
    html = ("<html><body><main><h1>Ashcombe shop</h1>"
            "<p>Open seven days a week.</p>"
            "<p>27 Harbour Lane, Ashcombe KT7 4LP.</p></main>"
            "<footer><p>Invented Bakers, 8 Kelvin Row, Ashcombe SE16 4RA.</p>"
            "</footer></body></html>")
    facts = _facts(html, page_type="location")
    assert facts["street_hint"] == "27 Harbour Lane"
    assert facts["street_source"] == "in the text of a page about the site"
    assert facts["postcode_hint"] == "KT7 4LP"


def test_a_labelled_address_in_body_prose_is_the_pages_own():
    """The positive-label discipline `_PHONE_LABEL_RE` already uses: where a
    page names one of its runs as the address, that run is the address. The
    label is not adjacent to what it labels - the building's name sits between
    them - so the sentence is the window."""
    html = ("<html><body><main><p>Receptionist and general emails. "
            "Address: Invented Museum, Thornbury Road, Ashcombe KT7 4LP. "
            "Visitor Experience.</p></main></body></html>")
    facts = _facts(html)
    assert "Thornbury R" in facts["street_hint"]
    assert facts["street_source"] == "behind an address label in the page text"
    assert facts["has_address"] is True


def test_a_sentence_in_the_first_person_states_the_sites_own_address():
    html = ("<html><body><main><p>Come and see us at 27 Harbour Lane, "
            "Ashcombe KT7 4LP.</p></main></body></html>")
    facts = _facts(html)
    assert facts["street_hint"] == "27 Harbour Lane"
    assert facts["street_source"] == "in a sentence that speaks for the site"


def test_where_the_street_was_found_is_always_recorded_with_it():
    """`postcode_source` exists so a consumer can ask whether a code belongs to
    the page or to the template. The street needs the same answer, and a
    consumer that reads one and not the other pairs a page-level street with a
    site-level code."""
    for html, expected in (
            (DISCLOSURE, ""),
            ("<html><body><main><p>Come and see us at 27 Harbour Lane, "
             "Ashcombe KT7 4LP.</p></main></body></html>",
             "in a sentence that speaks for the site"),
    ):
        facts = _facts(html)
        assert facts["street_source"] == expected
        assert bool(facts["street_hint"]) == bool(expected)


def test_the_code_quoted_beside_a_footer_street_comes_out_of_the_footer():
    """The street and the code beside it are two halves of one printed line.
    The street was matched against the footer's own text, so slicing the page
    text at that offset lands somewhere else entirely and quotes a pair the
    page prints nowhere."""
    html = ("<html><body><main><h1>Ladies sandal</h1>"
            "<p>Order reference 90210 for this item. " + "Words about it. " * 30
            + "</p></main>"
            "<footer><p>Invented Bakers, 8 Kelvin Row, Ashcombe SE16 4RA.</p>"
            "</footer></body></html>")
    facts = _facts(html)
    assert facts["street_hint"] == "8 Kelvin Row"
    assert facts["postcode_hint"] == "SE16 4RA"
    assert facts["postcode_source"] == "beside the street"


# --------------------------------------------------------------------------
# 3. Prices written into a dropdown
# --------------------------------------------------------------------------

VARIANTS = (
    '<option value="1">6 - Sold Out - Rs 569.00</option>'
    '<option value="2">7 - Rs 569.00</option>'
    '<option value="3">8 - Rs 599.00</option>'
    '<option value="4">9 - Rs 629.00</option>'
)

PRODUCT_BODY = (
    "<main><h1>Ladies sandal</h1><p>A sandal for everyday wear, made on a "
    "cushioned sole that keeps its shape. MRP 609 inclusive of all taxes.</p>"
    "{}</main>"
)


@pytest.mark.parametrize("select,shape", [
    ('<select name="id">' + VARIANTS + "</select>", "plain"),
    ('<select name="id" style="display:none">' + VARIANTS + "</select>", "hidden"),
    ("<noscript><select name=\"id\">" + VARIANTS + "</select></noscript>", "noscript"),
])
def test_variant_prices_in_a_dropdown_are_recorded(select, shape):
    """Storefront themes deliver the variant menu in three shapes and two of
    them are invisible to every copy of the document that has been cleaned for
    prose. Reading the delivered HTML covers all three at once."""
    record = _record("/products/sandal",
                     "<html><body>" + PRODUCT_BODY.format(select) + "</body></html>")
    for price in ("569.00", "599.00", "629.00"):
        assert price in record["control_text"], (
            "{}: the declared Offer price was compared against the only figure "
            "a prose reader can see and correct markup was reported as "
            "contradicting the page".format(shape))


def test_option_text_is_not_the_pages_prose():
    """A size menu of forty labels is not writing. Measured as sentences it
    reports a product page as written in fragments, and offered to the "what
    an assistant would quote" table it is quoted as the best line on the
    page."""
    select = '<select name="id">' + VARIANTS + "</select>"
    with_menu = _record("/products/sandal",
                        "<html><body>" + PRODUCT_BODY.format(select) + "</body></html>")
    without = _record("/products/sandal",
                      "<html><body>" + PRODUCT_BODY.format("") + "</body></html>")
    assert "Sold Out" not in with_menu["body_text"]
    assert with_menu["body_text"] == without["body_text"]
    assert with_menu["readability"] == without["readability"]
    assert with_menu["paragraphs"] == without["paragraphs"]
    assert with_menu["word_count"] == without["word_count"]


def test_a_menu_of_whole_paragraphs_is_not_read_as_a_menu():
    """A `<select>` used as a layout device holds prose, and prose in a control
    is not a fact about a product."""
    long_label = "word " * 60
    html = "<html><body><select><option>{}</option></select></body></html>".format(
        long_label)
    assert len(long_label) > CONTROL_OPTION_MAX
    assert _control_text(make_soup(html)) == ""


def test_a_page_that_is_only_a_menu_keeps_it():
    """A store locator whose body is one menu of town names has nothing else
    to read. Removing it there does not tidy the page, it empties it - the
    same reading `visible_soup` refuses at the same floor."""
    options = "".join("<option>Placename number {}</option>".format(i)
                      for i in range(60))
    record = _record("/locator", "<html><body><main><h1>Find a shop</h1>"
                                 "<select>{}</select></main></body></html>".format(options))
    assert "Placename number 7" in record["body_text"]


def test_a_long_menu_does_not_dominate_the_snapshot():
    """A country menu is 250 labels and says nothing about the business. Every
    skill loads this file."""
    options = "".join("<option>Placename number {}</option>".format(i)
                      for i in range(400))
    text = _control_text(make_soup(
        "<html><body><select>{}</select></body></html>".format(options)))
    assert 0 < len(text) <= 4200
