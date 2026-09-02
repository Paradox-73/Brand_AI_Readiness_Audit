"""What eight sites the four rules were not derived from found.

Two of the four rules had bugs of their own, and both are here: the sentence
meant to stop understated scope understated scope itself, and the guard meant
to stop invented values both let one through and suppressed a real one. The
rest are one more instance of a class already named - our own limits reported
as the site's defects.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import make_soup  # noqa: E402
from page_extract import _iframes, _meta_refresh  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMPOSE = _module(os.path.join(SCRIPTS, "compose_report.py"), "compose_for_round4")
SD = _module(os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py"),
             "sd_for_round4")


# --------------------------------------------------------------------------
# Rule 2's own bug: the denominator was read from the truncated display list
# --------------------------------------------------------------------------

def test_the_scale_sentence_uses_the_real_count_not_the_display_sample():
    """`affected_pages` is capped at five for display.

    Counting it made the sentence added to fix understated scope understate
    scope itself: seven findings across two sites read "Seen on 5 of the 60"
    when the true reach was 6, 11, 15, 27, 32 and twice 60.
    """
    finding = {"evidence": "Pages missing an Open Graph tag: a, b, c, d, e.",
               "affected_pages": ["/a", "/b", "/c", "/d", "/e"],
               "affected_page_count": 32}
    assert "Seen on 32 of the 60" in COMPOSE.state_the_denominator(finding, 60)


def test_a_finding_smaller_than_the_display_cap_is_unaffected():
    finding = {"evidence": "Pages with no H1: a, b, c.",
               "affected_pages": ["/a", "/b", "/c"], "affected_page_count": 3}
    assert "Seen on 3 of the 60" in COMPOSE.state_the_denominator(finding, 60)


# --------------------------------------------------------------------------
# Rule 4's own bugs: one value slipped through, one real value was suppressed
# --------------------------------------------------------------------------

SNAPSHOT = {
    "origin": "https://example.test", "site": "example.test",
    "brand": {"name": "Monmouth"},
    "pages": [{
        "url": "https://example.test/", "title": "Monmouth", "og": {}, "jsonld": [],
        "text": "Coffee roasted since 1978. Mug 18.00 GBP. York YO1 9TT.",
        "contact_facts": {"declared_phones": ["+442072323010"]},
    }],
}


def test_a_number_that_only_occurs_inside_another_number_is_not_observed():
    """`"price": "1"` reached a snippet as fact because the character "1"
    occurs in "1978". A price is short and damaging to get wrong, so it has to
    match as a whole token."""
    out, invented = COMPOSE.hold_back_invented_values(
        '{ "price": "1" }', COMPOSE.observed_values(SNAPSHOT))
    assert invented and "price" in invented[0]
    assert '"price": "1"' not in out


def test_a_real_price_still_survives():
    out, invented = COMPOSE.hold_back_invented_values(
        '{ "price": "18.00" }', COMPOSE.observed_values(SNAPSHOT))
    assert invented == [] and '"price": "18.00"' in out


def test_a_telephone_published_only_as_a_tel_link_counts_as_observed():
    """The guard called a real, correct number a guess and replaced it with a
    placeholder - the same defect as inventing one, pointing the other way."""
    out, invented = COMPOSE.hold_back_invented_values(
        '{ "telephone": "+442072323010" }', COMPOSE.observed_values(SNAPSHOT))
    assert invented == []
    assert "+442072323010" in out


def test_an_invented_postcode_is_still_caught():
    out, invented = COMPOSE.hold_back_invented_values(
        '{ "postalCode": "99999" }', COMPOSE.observed_values(SNAPSHOT))
    assert invented and "postalCode" in invented[0]


# --------------------------------------------------------------------------
# A fallback for readers without JavaScript is not a redirect
# --------------------------------------------------------------------------

def test_a_meta_refresh_inside_noscript_is_not_followed():
    """A language-learning site ships one. The crawl followed it, collapsed six
    seed URLs into one legacy stub, finished with three pages instead of sixty,
    and the report told the owner to make the redirect permanent - which would
    send every visitor to the stub for good.
    """
    html = ('<html><head><noscript>'
            '<meta http-equiv="refresh" content="0; url=/nojs/splash">'
            '</noscript></head><body><p>the real page</p></body></html>')
    assert _meta_refresh(make_soup(html), "https://example.test/") is None


def test_a_real_meta_refresh_is_still_followed():
    html = ('<html><head><meta http-equiv="refresh" content="0; url=/moved">'
            '</head><body></body></html>')
    assert _meta_refresh(make_soup(html), "https://example.test/") == {
        "delay": 0, "url": "https://example.test/moved"}


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
    the report printed as `about:blank`."""
    frames = _iframes(make_soup(FRAMES), "https://example.test/")
    by_src = {f["src"]: f for f in frames}
    assert by_src["https://www.googletagmanager.com/ns.html?id=GTM-X"]["is_invisible"]
    assert by_src["about:blank"]["is_invisible"]


def test_a_real_embedded_application_is_not_marked_invisible():
    frames = _iframes(make_soup(FRAMES), "https://example.test/")
    booking = next(f for f in frames if "booking" in f["src"])
    assert booking["is_invisible"] is False


# --------------------------------------------------------------------------
# A price range is not a missing price
# --------------------------------------------------------------------------

def test_an_aggregate_offer_is_graded_on_the_properties_it_has():
    """A coffee roaster's valid range pricing was reported as an offer
    "missing price", and the fix would have replaced a correct range with a
    single made-up number - which this report's own advice tells people not to
    do."""
    aggregate = {"@type": "AggregateOffer", "lowPrice": "8.75",
                 "highPrice": "9.75", "priceCurrency": "GBP"}
    assert SD._missing_props(aggregate, SD._offer_props(aggregate)) == []


def test_a_flat_offer_is_still_graded_on_price():
    flat = {"@type": "Offer", "priceCurrency": "GBP"}
    assert "price" in SD._missing_props(flat, SD._offer_props(flat))


@pytest.mark.parametrize("node,expected", [
    ({"@type": "AggregateOffer"}, "lowPrice"),
    ({"@type": "Offer"}, "price"),
])
def test_the_property_list_follows_the_declared_type(node, expected):
    assert expected in SD._offer_props(node)
