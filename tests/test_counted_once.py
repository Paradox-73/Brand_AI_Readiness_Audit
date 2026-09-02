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

def test_a_redirect_target_is_not_a_second_page():
    """`/` redirects to `/us/eng`, and `/us/eng` was then crawled again.

    The guard only ran when the URL being fetched had itself redirected, so
    the landing URL - which does not redirect - never reached it. The page was
    reported as a duplicate title against itself, and inflated the counts in
    two other findings.
    """
    import io
    source = io.open(SCRIPTS + "/crawl.py", encoding="utf-8").read()
    assert "if landed in fetched_final:" in source
    assert "if landed != url and landed in fetched_final:" not in source, (
        "the guard must not depend on where the request started")


# --------------------------------------------------------------------------
# One template image is one fix, not sixty images
# --------------------------------------------------------------------------

def test_an_explicit_empty_alt_is_correct_and_not_counted():
    soup = make_soup('<img src="/a.png" alt=""><img src="/b.png" alt="A bowl">')
    assert _images(soup, "https://example.test/")["missing_alt_count"] == 0


def test_a_missing_alt_is_counted_and_its_url_recorded():
    soup = make_soup('<img src="/a.png"><img src="/b.png" alt="A bowl">')
    images = _images(soup, "https://example.test/")
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
    "/AddToCart.aspx", "/App_Themes", "/Controls", "/PageError.aspx",
    "/InternationalAddress.aspx", "/apple-app-site-association", "/humans.txt",
    "/cgi", "/ads.txt", "/.well-known/", "/_next/", "/service-worker.js",
])
def test_machinery_is_not_real_content(rule):
    """Each of these was named in a report as a path worth unblocking."""
    assert benign_disallow(rule) is True


@pytest.mark.parametrize("rule", ["/blog", "/products", "/about", "/shop/", "/guides"])
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

@pytest.mark.parametrize("label", ["open source", "open standards", "open data"])
def test_an_adjective_is_not_a_call_to_action(label):
    """A link reading "open source" was reported as a homepage's primary CTA.

    It sat inside a sentence about licensing, while the "Download" link in the
    main navigation was ignored - and the fix suggested "See pricing" to a free
    command-line tool.
    """
    assert _VERB_IS_AN_ADJECTIVE_HERE.match(label)


@pytest.mark.parametrize("label", [
    "open an account", "get started", "download now", "buy now", "read more",
    "watch the demo", "book a table", "build your own",
])
def test_a_real_call_to_action_still_counts(label):
    assert not _VERB_IS_AN_ADJECTIVE_HERE.match(label)
