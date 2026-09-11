"""Counting the right things, once.

Five real reports produced numbers that were wrong not because the observation
was wrong but because of what was being counted: a page counted twice, an
image counted sixty times, a percentage drawn from three pages and described as
the site, a link label read as a verb when it was an adjective.
"""

from __future__ import annotations

import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from page_extract import _VERB_IS_AN_ADJECTIVE_HERE, _images, make_soup  # noqa: E402
from robots_parser import benign_disallow  # noqa: E402


# --------------------------------------------------------------------------
# One page reached by two addresses is one page
# --------------------------------------------------------------------------

from audit_common import fold_declared_duplicates  # noqa: E402


def test_a_url_and_its_declared_canonical_are_counted_once():
    """Two of eleven "duplicate title" pairs on a broadcaster were a URL and
    its own canonical target. The site had already said they are one page, and
    counting both inflated the finding and the denominator underneath it."""
    pages = [{"url": "https://x.test/a", "canonical": "https://x.test/a"},
             {"url": "https://x.test/a/", "canonical": "https://x.test/a"},
             {"url": "https://x.test/b", "canonical": ""}]
    assert [p["url"] for p in fold_declared_duplicates(pages)] == [
        "https://x.test/a", "https://x.test/b"]


def test_a_canonical_the_crawl_never_reached_folds_nothing():
    """Folding on it would hide a page rather than count it, and a canonical
    pointing somewhere unreached is a separate finding of its own."""
    pages = [{"url": "https://x.test/c", "canonical": "https://x.test/elsewhere"}]
    assert len(fold_declared_duplicates(pages)) == 1


# --------------------------------------------------------------------------
# One template image is one fix, not sixty images
# --------------------------------------------------------------------------

def test_a_tracking_pixel_is_not_an_image_missing_alt_text():
    """A report said "2 images have no alt attribute" and advised "write alt
    text that states what the image shows" and "prioritise product photos".
    Both were 1x1 beacons with display:none.

    The guard has to key on a declared 1x1, not on a missing size: `_int_attr`
    returns 0 for an absent attribute, so reading the parsed values alone made
    every image with no width or height a beacon.
    """
    html = ('<img src="/beacon.gif" width="1" height="1" style="display:none">'
            '<img src="/fb.gif" width="1" height="1">'
            '<img src="/pizza.jpg" width="1200" height="800">')
    images = _images(make_soup(html), "https://x.test/")
    assert images["missing_alt_count"] == 1
    assert images["missing_alt_sample"] == ["https://x.test/pizza.jpg"]

    sizeless = _images(make_soup('<img src="/a.png"><img src="/b.png" alt="A bowl">'),
                       "https://x.test/")
    assert sizeless["missing_alt_count"] == 1


def test_only_an_image_with_no_alt_attribute_at_all_is_counted():
    """`alt=""` is the correct way to say a picture is decoration, so counting
    it turned a template's spacer images into a finding."""
    correct = make_soup('<img src="/a.png" alt=""><img src="/b.png" alt="A bowl">')
    assert _images(correct, "https://example.test/")["missing_alt_count"] == 0

    images = _images(make_soup('<img src="/a.png"><img src="/b.png" alt="A bowl">'),
                     "https://example.test/")
    assert images["missing_alt_count"] == 1
    assert images["missing_alt_sample"] == ["https://example.test/a.png"]


def test_enough_urls_are_kept_to_count_distinct_images():
    """Five per page was too few for the site-wide distinct count to mean anything."""
    soup = make_soup("".join('<img src="/{}.png">'.format(i) for i in range(15)))
    assert len(_images(soup, "https://example.test/")["missing_alt_sample"]) == 15


# --------------------------------------------------------------------------
# robots.txt rules that block machinery, not content
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rule", [
    "/AddToCart.aspx", "/App_Themes", "/humans.txt", "/.well-known/", "/_next/",
])
def test_machinery_is_not_real_content(rule):
    """Each of these was named in a report as a path worth unblocking."""
    assert benign_disallow(rule) is True


@pytest.mark.parametrize("rule", ["/blog", "/shop/", "/123abc"])
def test_real_sections_are_still_reported(rule):
    assert benign_disallow(rule) is False


def test_a_wildcard_rule_with_real_path_segments_is_not_automatically_plumbing():
    """`/*/newsletter/existing/` is a live page, excluded for the wrong reason.

    Two wildcards used to be enough on its own to call a rule benign, and the
    report then told the reader the only blocked paths were "standard
    admin/cart/account/search routes", which was not true of what had been
    excluded.
    """
    assert benign_disallow("/*/newsletter/existing/") is False


def test_a_query_filter_is_still_plumbing():
    assert benign_disallow("/*?*add-to-cart=") is True
    assert benign_disallow("/?s=") is True


# --------------------------------------------------------------------------
# A word in a verb list is not always a verb
# --------------------------------------------------------------------------

@pytest.mark.parametrize("label", ["open source", "open data"])
def test_an_adjective_is_not_a_call_to_action(label):
    """A link reading "open source" was reported as a homepage's primary CTA.

    It sat inside a sentence about licensing, while the "Download" link in the
    main navigation was ignored - and the fix suggested "See pricing" to a free
    command-line tool.
    """
    assert _VERB_IS_AN_ADJECTIVE_HERE.match(label)


@pytest.mark.parametrize("label", ["open an account", "get started", "book a table"])
def test_a_real_call_to_action_still_counts(label):
    assert not _VERB_IS_AN_ADJECTIVE_HERE.match(label)


# --------------------------------------------------------------------------
# Text the page has hidden is not the page's content
# --------------------------------------------------------------------------

def test_an_aria_hidden_modal_is_not_page_content():
    """A restaurant chain's "you are leaving our site" modal became the
    opening prose of twelve pages, and the sentence the citation table said an
    assistant would quote from each of them.
    """
    from audit_common import main_text
    soup = make_soup(
        '<html><body>'
        '<div aria-hidden="true"><p>You are now leaving our website. '
        'We are not responsible for the content of external sites.</p></div>'
        '<main><p>' + ("Wheel-thrown stoneware made in the Walmgate studio. " * 8) +
        '</p></main></body></html>')
    text = main_text(soup)
    assert "leaving our website" not in text
    assert "Wheel-thrown stoneware" in text


def test_a_block_the_markup_declares_hidden_is_not_page_content():
    """`display: none` in a style attribute and a bare `hidden` attribute are
    the two other ways a page says a block is not for reading."""
    from audit_common import main_text
    for markup in ('<div style="display: none"><p>Hidden promotional copy.</p></div>',
                   '<div hidden><p>Hidden promotional copy.</p></div>'):
        soup = make_soup('<html><body>' + markup + '<main><p>'
                         + ("Real readable content on the page. " * 12)
                         + '</p></main></body></html>')
        assert "Hidden promotional copy" not in main_text(soup), markup


def test_visible_content_inside_an_ordinary_div_survives():
    """The exclusion must be markup that says "hidden", not anything nested."""
    from audit_common import main_text
    soup = make_soup(
        '<html><body><main><div class="promo"><p>' +
        ("A visible promotional line that belongs to the page. " * 8) +
        '</p></div></main></body></html>')
    assert "visible promotional line" in main_text(soup)
