# -*- coding: utf-8 -*-
"""Which words on a page are the page's own, and what is read out of them.

Almost every check downstream reads one string: the text this audit decided the
page is made of. Get that string wrong and the wrong findings arrive in
batches, all confident, all about a page that is fine.

Both directions have shipped:

  a `<header>` inside `<main>`        21 product names and prices stripped as
                                      chrome, 2,696 characters down to 105, and
                                      four findings about the wreckage
  `[class*=announcement]`             a CMS content type named
                                      `node-announcement` deleted, and the page
                                      reported both as text locked in images
                                      and as too thin to quote
  549 reader comments                 counted as 30,184 words of the author's
                                      own writing
  a testimonial saying "$30,000"      recorded as a software company's pricing
  a hidden "leaving our site" modal   offered as the sentence an assistant
                                      would quote, on thirteen pages
  `<body style="display:none">`       every character deleted, then four
                                      findings saying the site states no
                                      founding year, no definition, no contact
                                      and no headings

One of them was not a wrong finding but a crash: stripping hidden markup killed
the run outright on three sites, with no report at all.

The last sections are what gets read back out of that text - prices, sizes and
frames - where the same rule holds: a number on the page is only the page's
number if the page is saying it.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    content_soup, extraction_looks_incomplete, find_prices, main_text,
    main_text_and_region, make_soup, price_value, tidy_spacing, visible_soup,
    visible_text, without_other_peoples_words, word_count,
)
from page_extract import (  # noqa: E402
    _contact_facts, _document_title, _headings, _iframes,
)
from page_extract import extract_page  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RR = _module(os.path.join(ROOT, "skills", "render-readability-audit",
                          "scripts", "check.py"), "rr_for_page_text")
COMPOSE = _module(os.path.join(SCRIPTS, "compose_report.py"), "compose_for_page_text")


def _shell_reasons(page):
    """Why this page is reported as a shell, or `[]` when it is not one.

    The skill asks `shell_verdict` for both halves at once, because two checks
    calling the detector separately is how they came to contradict each other
    about one page. This reads the same pair the way the assertions below want
    it, and lives here rather than in the skill, where nothing called it.
    """
    verdict, reasons = RR.shell_verdict(page)
    return reasons if verdict == RR.SHELL else []


# --------------------------------------------------------------------------
# The crash
# --------------------------------------------------------------------------

def test_a_hidden_block_containing_a_styled_child_does_not_crash_the_crawl():
    """`select` lists every match before anything is removed.

    Decomposing a parent leaves its already-matched descendants in the list
    with `.attrs` set to None, and calling `.get` on one of those raised
    `AttributeError: 'NoneType' object has no attribute 'get'`. That killed the
    whole run on two major retailers and a hospital group - no report at all,
    just a stack trace. The markup is ordinary: a hidden banner with an
    inline-styled child inside it.
    """
    html = ('<html><body><div style="display:none">'
            '<span style="color:red">hidden inner</span></div>'
            '<main><p>' + ("Real readable content. " * 20) + '</p></main></body></html>')
    text = main_text(make_soup(html))
    assert "hidden inner" not in text
    assert "Real readable content" in text


# --------------------------------------------------------------------------
# Hidden markup, in every extractor rather than one
# --------------------------------------------------------------------------

MODAL_PAGE = (
    '<html><body><header><div class="external-modal" aria-hidden="true">'
    '<h2>Leaving our site</h2>'
    '<p>You are about to leave our website and enter a third party website.</p>'
    '</div></header><main><h1>Walmgate mug</h1><p>'
    + ("Wheel-thrown stoneware made in the Walmgate studio. " * 8)
    + '</p></main></body></html>')


def test_a_hidden_modal_reaches_none_of_the_text_fields():
    """It was the sentence offered as the quote for thirteen pages.

    `main_text` stripped it; the paragraph, section and heading extractors read
    the raw document and did not, so the citation table, the answer-first check
    and the heading list all still carried it.
    """
    record = extract_page("https://example.test/", "https://example.test/", 200, {},
                          MODAL_PAGE, [], 10, 0, "seed", "https://example.test")
    assert "third party" not in (record.get("body_text") or "")
    assert "third party" not in str(record.get("paragraphs") or [])
    assert "third party" not in str(record.get("sections") or [])
    assert "Leaving our site" not in str(record.get("headings") or {})
    assert "Walmgate mug" in str(record.get("headings") or {})


def test_hiding_the_whole_page_is_not_a_declaration_that_it_is_hidden():
    """A store theme wraps its body in `display:none` and reveals it with
    script. Taking that literally deleted every character, after which four
    findings said the site states no founding year, no definition, no contact
    and no headings - about a page that says all four."""
    hidden_all = ('<body><div style="display:none"><h1>Acme</h1>'
                  '<p>Founded in 2012 in York.</p></div></body>')
    assert "Founded in 2012" in visible_text(visible_soup(make_soup(hidden_all)))


def test_a_hidden_banner_is_still_removed():
    part = ('<body><p>Real content here, and a decent amount of it.</p>'
            '<div style="display:none">Cookie banner</div></body>')
    assert "Cookie banner" not in visible_text(visible_soup(make_soup(part)))


# --------------------------------------------------------------------------
# The announcement bar, and the content type that shares its name
# --------------------------------------------------------------------------

BANNER_PAGE = (
    '<html><body><div class="promo-bar__text">'
    'Free standard shipping on orders $40+ | $60 CAD+ | $110 AUD+ | R$670+'
    '</div><main><h1>Balm Dotcom</h1><p>'
    + ("A lip balm in a tube. " * 12) + '$16.00</p></main></body></html>')


def test_a_shipping_banner_is_not_the_prices_on_the_page():
    """The retailer's own correct $16 markup was reported as contradicting
    "the prices shown on the page (40, 60, 110, 670)" - four free-shipping
    thresholds, one per locale, stacked in the delivered HTML and switched
    client-side. The same report's appendix called that markup complete.
    """
    text = main_text(make_soup(BANNER_PAGE))
    assert find_prices(text) == ["$16.00"]
    assert "CAD" not in text


@pytest.mark.parametrize("class_name", [
    "announcement-bar", "announce-bar__inner", "marquee-text",
])
def test_every_common_banner_class_is_treated_as_chrome(class_name):
    html = ('<html><body><div class="' + class_name + '">Free shipping over $40</div>'
            '<main><p>' + ("Real content. " * 20) + '</p></main></body></html>')
    assert "$40" not in main_text(make_soup(html))


def _announcement_page(count=10, chars=300):
    body = "".join(
        '<article class="node-announcement"><h2>Item {}</h2><p>{}</p></article>'.format(
            i, "x" * chars) for i in range(count))
    return ("<html><body><header><nav>Home About</nav></header>"
            "<main>" + body + "</main><footer>c</footer></body></html>")


def test_a_cms_content_type_whose_class_says_announcement_survives():
    """`[class*=announcement]` was written for the free-shipping bar above the
    header. On a public radio programme's site it matched
    `<article class="node-announcement">` - the CMS's own name for the content
    type - and deleted the page. Ten dated announcements, 3,228 characters,
    became 34, and the audit reported the page twice: once as text locked in
    images and once as too thin to quote. Both findings were false and both
    were about our own selector.
    """
    text = main_text(make_soup(_announcement_page()))
    assert len(text) > 2500, "the page was stripped to {} chars".format(len(text))
    assert "Item 7" in text


def test_what_the_page_declares_as_chrome_comes_out_whatever_its_size():
    """The bound applies to guesses from a class name, not to a landmark
    element or an `aria-hidden` attribute. Those are the page saying so."""
    html = ('<html><body><nav>' + "n" * 4000 + '</nav>'
            '<main><p>the real content</p></main></body></html>')
    text = main_text(make_soup(html))
    assert "nnnn" not in text and "the real content" in text


# --------------------------------------------------------------------------
# A landmark is scoped to its section, not to the page
# --------------------------------------------------------------------------

def _card_page():
    """A storefront listing: every product's name and price in its own header."""
    cards = "".join(
        '<a href="/p/{0}"><article><header class="my-4">'
        'The Product {0} - Selvedge £{1}</header></article></a>'.format(n, 300 + n)
        for n in range(1, 22))
    return ('<html><body>'
            '<header class="site"><nav>Shop Story Guides</nav></header>'
            '<main><header class="col-span-full"><h1>Denim</h1>'
            '<p>Premium denim handcrafted in one town.</p></header>'
            + cards + '</main>'
            '<footer><nav>Join our newsletter for private offers</nav></footer>'
            '</body></html>')


def test_a_section_header_is_not_the_page_masthead():
    """21 product names and prices lived in `<header>` elements inside `<main>`.

    Stripping every element named `header` took the page from 2,696 characters
    to 105, and four findings followed - content locked in images, warm-up
    prose, a JavaScript shell, nothing quotable - all of them false, about a
    plain server-rendered list of products.
    """
    text = main_text(make_soup(_card_page()))
    assert "The Product 1" in text, "the card headers were stripped as chrome"
    assert "£301" in text, "the prices went with them"
    assert "Premium denim handcrafted" in text


def test_the_site_masthead_and_footer_still_go():
    """The fix must not become "keep every landmark"."""
    text = main_text(make_soup(_card_page()))
    assert "Shop Story Guides" not in text
    assert "Join our newsletter" not in text


def test_a_heading_that_is_a_logo_is_still_a_heading():
    soup = make_soup('<html><body><h1><a href="/"><img src="l.gif" alt="Japan Post Holdings">'
                     '</a></h1></body></html>')
    assert _headings(soup)["h1"] == ["Japan Post Holdings"]


def test_an_icon_label_is_not_the_page_title():
    """A `<title>` inside an `<svg>` is the accessible name of an icon. A
    restaurant site ships three and the crawler took the icon's, which
    produced two of that report's findings including its only high one."""
    html = ('<svg><title>Search</title></svg>'
            '<title>Acme | Wheel-thrown stoneware from York</title>')
    assert _document_title(make_soup(html)) == "Acme | Wheel-thrown stoneware from York"


# --------------------------------------------------------------------------
# Text that is on the page and is not the page's own words
# --------------------------------------------------------------------------

BLOG_POST = """<html><body><main>
  <article><h1>Chiffon cake</h1><p>{article}</p></article>
  <div id="comments"><ol class="comment-list">
    <li id="comment-598456">Jayne says at 5:05 pm Reply {chatter}</li>
  </ol></div>
</main></body></html>""".format(article="The cake keeps three days in a tin. " * 8,
                                chatter="I made this twice and it worked. " * 80)


def test_reader_comments_are_not_the_pages_own_prose():
    """549 comments were counted as 30,184 words of the author's writing, and
    the page reported as written in blocks too large to skim."""
    text = main_text(make_soup(BLOG_POST))
    assert "Chiffon cake" in text
    assert "Jayne" not in text


def test_a_commenters_id_is_not_the_sites_postcode():
    facts = _contact_facts(*(lambda s: (s.get_text(" "), s))(
        without_other_peoples_words(make_soup(BLOG_POST))))
    assert "598456" not in (facts["postcode_hint"] or "")


def test_a_page_that_is_only_comments_keeps_them():
    """A forum thread and a question-and-answer page are their user content.
    Removing it there would delete the page rather than read it closely."""
    forum = "<html><body><main><h1>Thread</h1><div id=comments><ol class=comment-list>" \
            "<li>{}</li></ol></div></main></body></html>".format("a reply here. " * 60)
    assert len(main_text(make_soup(forum))) > 300


def test_a_customers_testimonial_is_not_the_sites_own_price():
    """"We save close to $30,000 per year" was recorded as a software
    company's pricing, on a page that has no price on it."""
    page = "<html><body><main><h1>Plan</h1><p>{}</p>" \
           "<div class=\"testimonial-card\"><p>We save close to $30,000 per year</p></div>" \
           "</main></body></html>".format("Coordinate work across teams. " * 10)
    assert "30,000" not in main_text(make_soup(page))


# --------------------------------------------------------------------------
# The spacing this audit introduced comes back out
# --------------------------------------------------------------------------

def test_a_sentence_broken_by_inline_markup_is_rejoined_cleanly():
    """Joining inline elements with a space is what makes the extracted text
    readable, and it also puts a space in front of every punctuation mark that
    sat outside a `<b>` or an `<a>`. A database project's own tagline came out
    as "a small , fast , self-contained , high-reliability , full-featured ,
    SQL database engine" - and that string went into a paste-ready
    `Organization` description, so an owner following the advice would publish
    their own tagline with six stray spaces in it.
    """
    html = ("<p>It is a <b>small</b>, <b>fast</b>, <b>self-contained</b>, "
            "SQL database engine.</p>")
    assert main_text(make_soup(html)) == (
        "It is a small, fast, self-contained, SQL database engine.")


@pytest.mark.parametrize("before,after", [
    ("see ( the docs ) for 40 % more", "see (the docs) for 40% more"),
    ("Really ? Yes ; truly !", "Really? Yes; truly!"),
])
def test_only_the_spacing_changes_never_the_words(before, after):
    assert tidy_spacing(before) == after


def test_a_hyphen_between_words_is_left_alone():
    """"Mon - Fri" is how the site wrote it, and it is not punctuation this
    audit introduced."""
    assert tidy_spacing("Open Mon - Fri. Call now.") == "Open Mon - Fri. Call now."


# --------------------------------------------------------------------------
# A page we failed to read is not a page with nothing on it
# --------------------------------------------------------------------------

def test_a_page_whose_text_we_could_not_extract_is_not_called_a_shell():
    """A news site's liveblog wraps server-rendered Arabic prose in a nested
    div none of the content selectors reach. The extractor kept 89 of the
    page's 1,660 visible characters, and two findings followed - "delivered as
    an empty JavaScript shell" and "too little text to quote" - about a page
    that is complete and server-rendered. The fix offered was several days of
    development work.
    """
    page = {"url": "https://x.test/live", "text_len": 1660, "body_text_len": 89,
            "spa_shell": {}, "scripts": {"inline_bytes": 0, "external_count": 9}}
    assert extraction_looks_incomplete(page)
    assert _shell_reasons(page) == []


def test_a_genuine_shell_is_still_reported():
    """A framework root with nothing in it, or a state blob holding the
    content, is the site telling us the text arrives later. That evidence
    survives the guard - and so does a page that really is empty, where
    nothing was lost in extraction because there was nothing to lose."""
    blob = {"url": "https://x.test/", "text_len": 120, "body_text_len": 40,
            "spa_shell": {"root_selector": "#__next", "root_text_len": 0,
                          "state_blobs": ["__NEXT_DATA__"], "state_blob_bytes": 90000},
            "scripts": {"inline_bytes": 90000, "external_count": 4}}
    assert _shell_reasons(blob)

    empty = {"url": "https://x.test/", "text_len": 60, "body_text_len": 60,
             "spa_shell": {"root_selector": "#app", "root_text_len": 0},
             "scripts": {"inline_bytes": 0, "external_count": 8}}
    assert not extraction_looks_incomplete(empty)
    assert _shell_reasons(empty)


@pytest.mark.parametrize("page,expected", [
    ({"text_len": 1660, "body_text_len": 1400}, False),
    ({"text_len": 120, "body_text_len": 20}, False),     # too short to judge
    ({"text_len": 0, "body_text_len": 0}, False),
])
def test_the_extraction_shortfall_is_measured_against_the_whole_page(page, expected):
    assert extraction_looks_incomplete(page) is expected


# --------------------------------------------------------------------------
# A frame nobody can see holds nobody's content
# --------------------------------------------------------------------------

FRAMES = ('<html><body>'
          '<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-X">'
          '</iframe></noscript>'
          '<iframe src="about:blank" style="display:none;width:0px;height:0px"></iframe>'
          '<iframe src="https://app.example.test/booking" width="800" height="600"></iframe>'
          '</body></html>')


def test_tracking_and_zero_size_frames_are_marked_invisible():
    """Two sites were told their main content was trapped in an iframe: one was
    a tag manager's tracking pixel, the other a 0x0 form helper whose source
    the report printed as `about:blank`. The booking widget on the same page is
    a real embedded application and must not be swept up with them."""
    frames = _iframes(make_soup(FRAMES), "https://example.test/")
    by_src = {f["src"]: f for f in frames}
    assert by_src["https://www.googletagmanager.com/ns.html?id=GTM-X"]["is_invisible"]
    assert by_src["about:blank"]["is_invisible"]
    booking = next(f for f in frames if "booking" in f["src"])
    assert booking["is_invisible"] is False


# --------------------------------------------------------------------------
# Money, as the world writes it
# --------------------------------------------------------------------------

@pytest.mark.parametrize("written,expected", [
    ("28,00 EUR", 28.0), ("15,0", 15.0), ("$480", 480.0),
    ("$1,500.00", 1500.0), ("2,800", 2800.0), ("1.234,56", 1234.56),
])
def test_a_decimal_comma_is_a_decimal_point(written, expected):
    """A French bakery's "28,00 EUR" was read as 2800, so its own correct
    structured data was reported as contradicting its own page - four times,
    second in what to fix first, with up to a day of work recommended.
    """
    assert price_value(written) == expected


@pytest.mark.parametrize("text,wanted,where", [
    (u"Online Desde (IVA incl.) 309,74 € 09 - Blanco", u"309,74 €", "Spain"),
    (u"전시 관람료 8,000 원", u"8,000 원", "Korea"),
    (u"Frete grátis acima de R$670", u"R$670", "Brazil"),
])
def test_a_currency_that_follows_its_number_is_still_a_price(text, wanted, where):
    """The pattern required the symbol first and a dot before the cents. Most
    of Europe and Latin America writes neither, so a Spanish product page's
    whole price list came out as `['€ 09']` - the euro sign and a colour
    code - and the report said the page's own declared price "is not among the
    prices shown on the page (9)"."""
    assert wanted in find_prices(text), where


def test_a_count_of_things_is_not_a_price():
    """The guard that has to survive widening the pattern."""
    assert find_prices("We have 5,000 products and 60 years of history") == []


def test_a_price_line_is_quotable_even_though_it_is_short():
    """A software company's pricing page - "Basic $10 per user/month" - was
    listed as having nothing an assistant could quote."""
    assert COMPOSE.PRICE_LINE_RE.search("Basic $10 per user/month")
    assert COMPOSE.CONTACT_LINE_RE.search("For other questions, email us hello@example.com")


# --------------------------------------------------------------------------
# What is measured and what it is measured in
# --------------------------------------------------------------------------

def test_subheadings_are_counted_where_the_words_were_counted():
    """A manufacturer's homepage was reported as "1208 words with a subheading
    only every 1208 words". It carries 38 h2, h3 and h4 elements, and the
    audit's own snapshot lists all of them: the words came from one tree and
    the headings from another, and 26 pages were called walls of text on that
    arithmetic."""
    html = ('<html><body><header><nav>Menu</nav></header><main>'
            + "".join('<section><h2>Part {0}</h2><p>{1}</p></section>'.format(n, "word " * 60)
                      for n in range(1, 6))
            + '</main></body></html>')
    soup = make_soup(html)
    text, region = main_text_and_region(soup)
    assert region is not None, "a <main> holding the whole page should be the region"
    counted = content_soup(region)
    assert len(counted.find_all(["h2", "h3", "h4"])) == 5
    assert word_count(text) > 250
