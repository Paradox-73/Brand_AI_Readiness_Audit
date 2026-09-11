# -*- coding: utf-8 -*-
u"""Four defects measured on real sites, all in `page_extract.py`.

1.    A city site's report opened its identity sentence with the site's own
      JavaScript: `document.write("<br><br><hr width="80%" ...></div></body>
      </html>")` and the string concatenation beside it, printed as "Its
      homepage says". Removing `<script>` elements never reached it, because
      an unescaped `</script>` inside a JavaScript string ends the element and
      the rest of the source becomes ordinary document text.

2.    Product detail pages collapsing into listings on every retail stack, two
      mechanisms. `_nothing_was_delivered` returned `False` the moment
      `jsonld_types` held anything, and every shell page on the site under
      test shipped `['organization', 'searchaction', 'website']` in its head,
      so the address-based promotion written for that shape was dead code on
      it. And the shelf test counted every link on the page, so a mega-menu
      made any product page a shelf. Product markup was assessed on 2 of 13
      product pages on one shop; 0 of 3 `/product/` URLs typed `product` on
      another.

3.    A brand's own tagline invisible to the definition check: a homepage's
      `<p><brand> is a proudly ... lifestyle brand.</p>` and its footer line
      are both in the snapshot's `text` and in neither `body_text` nor
      `paragraphs`, because the crawl's site-wide boilerplate strip removes
      any block that repeats across the site - which is what a footer tagline
      does by construction. The report printed "Nothing quotable" for a
      homepage with two quotable definitions.

4.    Off-site profiles counting the wrong things: a noodle shop's social
      account named as a city's own, a package-registry entry for a different
      project named as a documentation site's own, a subdomain of the audited
      site named as third-party corroboration of it, and a repository link in
      the audited site's own footer missed entirely.

Every host and brand below is invented.
"""

from __future__ import annotations

import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

import crawl  # noqa: E402
from audit_common import make_soup, some_subject_is_defined  # noqa: E402
from page_extract import (  # noqa: E402
    _nothing_was_delivered, _off_site_profiles, _social_profiles,
    extract_page, reads_as_source_not_prose,
)

SHOP = "https://kadam-bags.test"
CITY = "https://an-invented-city.test"


def _extract(origin, path, html):
    return extract_page(origin + path, origin + path, 200,
                        {"content-type": "text/html"}, html, [], 10, 1, "link", origin)


def _links(*urls):
    return [{"url": url, "text": ""} for url in urls]


# --------------------------------------------------------------------------
# Source that escaped its `<script>` element is not the page's prose
# --------------------------------------------------------------------------

# The shape that leaks, reduced to its mechanism: the string written by
# `document.write` contains a `</script>`, which ends the element there. Every
# parser and every browser does the same thing with it. Everything after it -
# the concatenation that builds the reservoir-level sentence, and the second
# `document.write` - is document text by the time this audit sees the page.
LEAKING_SCRIPT_PAGE = u"""<html><head><title>An invented city</title></head>
<body><div id="main">
<h1>An invented city</h1>
<script language="JavaScript">
document.write("<script src='reservoir.js'></script>");
yyyymmddhh = data[0] + "年" + data[1] + "月" + data[2] + "日"
             + data[3] + "時現在の貯水率";
</script>
<p>An invented city is a coastal city of 1.5 million people in an invented
country, and this page publishes its reservoir levels every hour.</p>
</div></body></html>"""


def test_source_that_escaped_its_script_element_is_not_the_pages_prose():
    """The defect as found, through the real extractor. `body_text` is what the
    identity sentence is chosen from, and it must not carry program source."""
    page = _extract(CITY, "/", LEAKING_SCRIPT_PAGE)
    assert "document.write" not in page["body_text"]
    assert "yyyymmddhh" not in page["body_text"]
    assert "<" not in page["body_text"] and ">" not in page["body_text"]
    for block in page["prose_blocks"]:
        assert "document.write" not in block


def test_the_delivered_document_still_holds_what_was_taken_out_of_the_prose():
    """Reattributed, never lost - the same claim `_strip_sitewide_boilerplate`
    makes about the blocks it moves. A check asking what the server actually
    sent reads `text`, and it is unchanged."""
    page = _extract(CITY, "/", LEAKING_SCRIPT_PAGE)
    assert "yyyymmddhh" in page["text"]


def test_a_page_that_shows_code_keeps_its_own_writing():
    """The removal runs after `_without_code_samples`, so a page that displays
    code has had it taken out already and cannot be reached by the source test.
    A documentation page losing its prose would be this fix causing the defect
    it was written to stop."""
    html = (u"<html><body><main>"
            u"<p>The invented client library opens a session and closes it for "
            u"you, so you never hold a socket open past a deadline.</p>"
            u"<pre><code>session = Client(); session.write(document);</code></pre>"
            u"</main></body></html>")
    page = _extract("https://invented-library.test", "/docs", html)
    assert "invented client library opens a session" in page["body_text"]


@pytest.mark.parametrize("sentence", [
    u'<br><br><hr width="80%" align="center"></div></body></html>',
    u"the level at undefined hours was undefined percent",
    u'"); document.write("' + u"the rest of the source",
])
def test_a_candidate_sentence_carrying_markup_or_source_is_refused(sentence):
    """The prescription, word for word: "reject any candidate sentence
    containing `<`, `>` or a bare `undefined` before it can become the
    defining sentence." Public, because the sentence is chosen in three other
    skills."""
    assert reads_as_source_not_prose(sentence)


@pytest.mark.parametrize("sentence", [
    u"Kadam Bags is a modern lifestyle brand for bags, wallets and luggage.",
    u"We opened in 1998 and we have made every bag by hand since.",
    u"Orders above 2,499 ship free within the country.",
])
def test_an_ordinary_sentence_is_not_refused(sentence):
    """The cost of the rule above, bounded. A site's own writing must pass it,
    or the fix silences the field it was written to fill."""
    assert not reads_as_source_not_prose(sentence)


# --------------------------------------------------------------------------
# A page type decided on what this page says, not what its site ships
# --------------------------------------------------------------------------

MENU = u"".join(u'<li><a href="/collections/{0}">{1}</a></li>'.format(s, s.title())
                for s in ("bags", "wallets", "luggage", "backpacks", "sale"))

# What every page of the site under test shipped in its `<head>`.
SITE_WIDE_MARKUP = (
    u'<script type="application/ld+json">'
    u'{"@context":"https://schema.org","@type":"Organization","name":"Kadam Bags"}'
    u'</script>'
    u'<script type="application/ld+json">'
    u'{"@type":"WebSite","potentialAction":{"@type":"SearchAction"}}</script>')

PRODUCT_SHELL = (u"<html><head><title>Vegan Laptop Bag | Kadam Bags</title>"
                 + SITE_WIDE_MARKUP + u"</head><body>"
                 u"<header><nav><ul>" + MENU + u"</ul></nav></header>"
                 u'<div id="root"></div></body></html>')


def test_a_shell_declaring_only_its_publisher_is_typed_by_its_address():
    """The measurement: `['organization', 'searchaction', 'website']` in the
    head of every shell page made the address-based promotion unreachable, so
    0 of 3 `/product/` URLs typed `product` and the Product/Offer
    recommendation was declined on the grounds that no product page exists."""
    page = _extract(SHOP, "/product/vegan-laptop-bag", PRODUCT_SHELL)
    assert page["jsonld_types"] == ["organization", "searchaction", "website"]
    assert page["page_type"] == "product"


def test_a_page_that_declares_its_own_type_still_answers_for_itself():
    """Only the types whose subject is the publisher are set aside. A page
    declaring `Product` or `Article` has said what it is, and the address must
    not be allowed to overrule it."""
    assert _nothing_was_delivered(
        {"headings": {}, "jsonld_types": ["organization", "website"],
         "spa_shell": {"visible_text_len": 40}})
    assert not _nothing_was_delivered(
        {"headings": {}, "jsonld_types": ["organization", "product"],
         "spa_shell": {"visible_text_len": 40}})


def test_the_measured_site_wide_set_is_read_where_a_caller_has_one():
    """The constant stands in for a set the extractor cannot measure, because
    it runs a page at a time. A caller that has walked the whole crawl passes
    the types it found on every page, and those are set aside too."""
    evidence = {"headings": {}, "jsonld_types": ["collectionpage"],
                "spa_shell": {"visible_text_len": 40}}
    assert not _nothing_was_delivered(evidence)
    evidence["site_wide_jsonld_types"] = ["collectionpage"]
    assert _nothing_was_delivered(evidence)


def test_a_mega_menu_does_not_make_a_product_page_a_shelf():
    """The second mechanism. The shelf test compares this page's headings
    against the labels of the links leaving it, and a mega-menu supplies a
    label for every category the shop has - so a product page whose heading
    repeats a category name read as a listing."""
    html = (u"<html><head><title>Bags | Kadam Bags</title></head><body>"
            u"<header><nav><ul>" + MENU + u"</ul></nav></header>"
            u"<main><h1>Bags</h1><p>This vegan laptop bag is water resistant "
            u"and holds a fifteen inch machine.</p><p>Rs 2,499</p>"
            u'<form action="/cart/add"><button name="add">Add to cart</button>'
            u"</form></main></body></html>")
    page = _extract(SHOP, "/shop/vegan-laptop-bag", html)
    assert page["page_type"] == "product"


def test_a_grid_of_tiles_is_still_a_listing():
    """The guard that must not be reopened: a grid of 36 links to item pages
    typed `product` would declare a whole shelf to be one item at one price.
    The tiles are in the content region, so narrowing the link count to that
    region cannot hide them."""
    tiles = u"".join(
        u'<div><a href="/shop/item-{0}"><h2>Item {0}</h2></a><span>Rs {1},499'
        u'</span><button name="add" data-add-to-cart>Add to cart</button></div>'
        .format(i, i + 1) for i in range(36))
    html = (u"<html><head><title>Bags | Kadam Bags</title></head><body>"
            u"<header><nav><ul>" + MENU + u"</ul></nav></header>"
            u"<main><h1>Bags</h1>" + tiles + u"</main></body></html>")
    page = _extract(SHOP, "/shop/bags", html)
    assert page["page_type"] == "category"


# --------------------------------------------------------------------------
# The definition the boilerplate strip removes
# --------------------------------------------------------------------------

TAGLINE = (u"Kadam Bags is a modern lifestyle brand for bags, wallets and "
           u"luggage.")

HOMEPAGE = (u"<html><head><title>Kadam Bags</title></head><body>"
            u"<main><h1>Kadam Bags</h1>"
            u"<p>Kadam Bags is a proudly independent and cruelty-free "
            u"lifestyle brand.</p></main>"
            u"<footer><div>" + TAGLINE + u"</div></footer></body></html>")


def test_a_footer_tagline_reaches_a_definition_reading():
    """A `<p>` scan of the content region can never see it: the line is a
    `<div>` and it is in the furniture. Both are where a tagline lives."""
    page = _extract(SHOP, "/", HOMEPAGE)
    assert TAGLINE in page["prose_blocks"]
    assert TAGLINE not in page["paragraphs"]


def test_the_site_wide_strip_empties_paragraphs_and_leaves_the_blocks():
    """Run through the crawl's own strip, which is where the sentences went.
    It rewrites `paragraphs`, `sections`, `body_text` and everything derived
    from them by name - the readability and thin-page checks depend on that -
    and this field is not one of those names."""
    pages = [_extract(SHOP, path, HOMEPAGE)
             for path in ("/", "/about", "/contact", "/shipping", "/returns")]
    notes = []
    crawl._strip_sitewide_boilerplate(pages, notes)
    home = pages[0]
    assert home["paragraphs"] == []
    assert TAGLINE not in home["body_text"]
    assert TAGLINE in home["prose_blocks"]
    assert any("site furniture" in note for note in notes)


def test_each_block_carries_the_subject_its_own_sentence_defines():
    """Why the blocks are separate entries rather than one flattened string.

    A craft site whose brand name is also a common noun was offered a sentence
    about memorial stone carving, lifted from an article, as its own identity.
    One block is one sentence, so `some_subject_is_defined` reports a subject
    per block and the consumer can refuse a subject the site does not assert
    for itself. Flattened into one string the two sentences below run together
    and the article's subject reaches the brand's identity line."""
    article = (u"<html><body><main>"
               u"<p>Memorial Stone Carving is a craft that takes a decade to "
               u"learn, and few workshops in the country still teach it.</p>"
               u"<p>Invented Stoneworks is a workshop of four carvers who cut "
               u"lettering by hand for churches and memorials.</p>"
               u"</main></body></html>")
    page = _extract("https://invented-stoneworks.test", "/journal/carving", article)
    subjects = [some_subject_is_defined(block) for block in page["prose_blocks"]]
    found = [s[0] for s in subjects if s]
    assert "Memorial Stone Carving" in found
    assert "Invented Stoneworks" in found


# --------------------------------------------------------------------------
# An account is this site's only where something attributes it
# --------------------------------------------------------------------------

def test_a_subdomain_of_the_audited_site_is_not_an_off_site_profile():
    """A government site's report named `gis.<the audited domain>` - the site
    itself - as third-party corroboration of the site. `same_site` compares
    whole hostnames, so every subdomain arrives in `links["external"]` and
    nothing between there and the profile list asked again."""
    found = _off_site_profiles(
        _links(CITY + "/", "https://gis.an-invented-city.test/portal/home"),
        [], make_soup(u"<p>x</p>"), "https://www.an-invented-city.test/")
    assert [entry["url"] for entry in found] == []


def test_an_account_nothing_attributes_is_not_counted():
    """The case that prompted this: a noodle shop's social account and a shop directory
    named as a city's own profiles, and a package-registry entry for a
    different project named as a documentation site's own. Both have the shape
    of an account. Shape says an address names an account; it cannot say
    whose."""
    found = _off_site_profiles(
        _links("https://a-social-host.test/an-invented-noodle-shop",
               "https://a-registry-host.test/project/a-different-project"),
        [], make_soup(u"<p>x</p>"), CITY + "/")
    assert found and not any(entry["attributed"] for entry in found)
    assert _social_profiles(
        _links("https://a-social-host.test/an-invented-noodle-shop"),
        [], make_soup(u"<p>x</p>"), CITY + "/") == {}


def test_an_account_the_site_declares_is_counted():
    """The three attributions that survive: the site says so in `sameAs`, the
    page publishes a row of its own accounts, or the address spells a name the
    site publishes about itself. Removing any of them would re-open a defect
    fixed earlier."""
    declared = [{"@type": "Organization",
                 "sameAs": ["https://a-social-host.test/an-invented-city-hall"]}]
    found = _social_profiles([], declared, make_soup(u"<p>x</p>"), CITY + "/")
    assert found == {"a-social-host.test":
                     "https://a-social-host.test/an-invented-city-hall"}


def test_an_account_named_by_a_number_is_not_rejected_by_a_name_test():
    """A test that cannot pass must not be the thing that rejects. An archive
    accession number spells no name however plainly the record belongs to this
    site, and dropping it would re-open the miss where the tool recommended a
    documentation tree go and get a registry entry it already had."""
    found = _off_site_profiles(
        _links("https://an-invented-archive.test/records/1234567"),
        [], make_soup(u"<p>x</p>"), "https://invented-library.test/")
    assert [entry["attributed"] for entry in found] == [True]


def test_a_repository_owner_that_spells_the_site_name_counts_from_one_link():
    """A link labelled "source" in the audited site's own footer, recorded in
    the crawl's own `links.external`, and reported as no repository at all -
    because the owner-repetition rule wants two links and a one-page site
    links its repository once."""
    found = _social_profiles(
        _links("https://github.com/invented-library-app/invented-library"),
        [], make_soup(u"<p>x</p>"), "https://invented-library.test/")
    assert found == {"GitHub": "https://github.com/invented-library-app"}


def test_a_strangers_repository_still_needs_two_links():
    """The rule the case above steps around, unchanged where the name does not
    match: one link to somebody else's repository is a dependency or a credit,
    and counting it would put a stranger's account in the brand's total."""
    assert _social_profiles(
        _links("https://github.com/a-different-owner/a-library"),
        [], make_soup(u"<p>x</p>"), "https://invented-library.test/") == {}
