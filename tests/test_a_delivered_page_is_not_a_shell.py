# -*- coding: utf-8 -*-
"""One field read wrong produced five of a report's ten findings.

A server-rendered storefront built on a JavaScript framework was audited.
The tool's own snapshot recorded, for the homepage:

    text_len                      3517
    spa_shell.visible_text_len    3517
    body_text_len                  171

The delivered HTML carries the whole product grid, names and prices included.
The report's first finding, published `critical` and ranked above everything
else, read:

    "https://<the site>/: 173 chars of visible text against 269,238 bytes of
     inline script"

173 is `body_text_len` - the prose left after the site furniture is stripped
out - printed under the words "visible text". The prescribed fix, "server-render
the homepage ... several days of development time", was already done. The same
reading produced the report's `high` finding, "5 of 6 content pages are
delivered as JavaScript shells", about three product pages carrying 2,672 to
3,254 characters of visible text and complete Product, Offer and AggregateRating
markup - and the top-sheet line, and the first two "Start here" items.

`body_text` is the right field for readability: how much the page says in its
own words, which is what `thin-html` grades. It is the wrong field for "did a
page arrive at all", because an extractor that can lose a product grid is not
the witness for that. This file holds `shell_verdict` and the four checks
downstream of it to the visible-text reading, in both directions: the delivered
page must not be called a container, and a container must still be called one.

Every host here is invented and every address is fictional.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(skill, name):
    scripts = os.path.join(ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _module("render-readability-audit", "render_for_delivered_page_tests")

SITE = "https://a-headless-storefront.test"
CHROME = ["/", "/shop", "/about", "/contact"]

# The two measured numbers, kept as named constants so a reader of a failure
# message can see which one the code picked up.
VISIBLE_TEXT = 3517          # spa_shell.visible_text_len, and text_len
EXTRACTED_PROSE = 171        # body_text_len
INLINE_SCRIPT_BYTES = 269238


def _delivered_page(url, visible, prose, page_type="content", **extra):
    """A page of the audited storefront: server-rendered, hydration payload,
    a mount point holding the rendered markup, and an extractor that kept only
    the prose."""
    page = {
        "url": url, "final_url": url, "status": 200, "page_type": page_type,
        "title": "A storefront",
        "body_text": "p" * prose, "body_text_len": prose,
        "text": "v" * visible, "text_len": visible,
        "html_bytes": 620000,
        "spa_shell": {"root_selector": "#__next", "root_text_len": visible - 90,
                      "state_blobs": ["__NEXT_DATA__"], "state_blob_bytes": 60000,
                      "noscript_demands_js": False, "visible_text_len": visible},
        "scripts": {"inline_bytes": INLINE_SCRIPT_BYTES, "external_count": 6},
        "chrome_signature": {"nav_paths": list(CHROME)},
        "links": {"nav": [{"url": SITE + p, "text": p} for p in CHROME],
                  "footer": [{"url": SITE + "/contact", "text": "Contact"}],
                  "internal": [{"url": SITE + "/shop", "text": "Shop"}]},
        "headings": {"h1": ["A storefront"]},
    }
    page.update(extra)
    return page


def _well_read_page(index):
    """A page of the same site whose extraction plainly worked, which is what
    makes the furniture measurable. 700 characters of menu and footer, the
    same on every page, and 1,200 characters of its own."""
    url = "{}/guide-{}".format(SITE, index)
    page = _delivered_page(url, 1900, 1200)
    page["title"] = "Guide {}".format(index)
    return page


def _storefront():
    """The six content pages the real report was about."""
    home = _delivered_page(SITE + "/", VISIBLE_TEXT, EXTRACTED_PROSE,
                           page_type="home")
    products = [_delivered_page("{}/product/{}".format(SITE, n), visible, 180,
                                page_type="product")
                for n, visible in ((1, 2672), (2, 3011), (3, 3254))]
    return [home] + products + [_well_read_page(1), _well_read_page(2)]


def _shell_document(url=SITE + "/app", visible=12):
    """A container: nothing a visitor could read arrived, and the response is
    almost entirely script. No mount node this audit's selector list matches,
    which is the shape that needed the script-versus-text signal to stand on
    its own."""
    return {
        "url": url, "final_url": url, "status": 200, "page_type": "content",
        "title": "Loading", "body_text": "x" * visible, "body_text_len": visible,
        "text": "x" * visible, "text_len": visible, "html_bytes": 240000,
        "spa_shell": {"root_selector": None, "root_text_len": None,
                      "state_blobs": [], "state_blob_bytes": 0,
                      "noscript_demands_js": False, "visible_text_len": visible},
        "scripts": {"inline_bytes": 224637, "external_count": 2},
        "chrome_signature": {"nav_paths": list(CHROME)},
    }


def _causes(result):
    return {f["root_cause"] for f in result.findings}


# --------------------------------------------------------------------------
# The defect, in the measured numbers
# --------------------------------------------------------------------------

def test_the_homepage_the_report_called_a_shell_is_not_one():
    verdict, reasons = RENDER.shell_verdict(
        _delivered_page(SITE + "/", VISIBLE_TEXT, EXTRACTED_PROSE, page_type="home"))
    assert verdict == "", (
        "a homepage delivering {} characters of visible text was called a "
        "container on the strength of its {} characters of extracted prose: {}"
        .format(VISIBLE_TEXT, EXTRACTED_PROSE, reasons))


def test_the_three_product_pages_the_report_called_shells_are_not_shells():
    for visible in (2672, 3011, 3254):
        page = _delivered_page("{}/product/x".format(SITE), visible, 180,
                               page_type="product")
        assert RENDER.shell_verdict(page)[0] == "", visible


def test_no_shell_finding_is_raised_about_the_storefront():
    """The `critical` finding, the `high` finding and the two "Start here"
    items, all four of which came from `shell_verdict` saying yes."""
    pages = _storefront()
    result = SkillResult("render-readability-audit")
    RENDER._check_shells(result, {"pages": pages}, pages, "static")

    assert "js-shell" not in _causes(result), [
        f["title"] for f in result.findings if f["root_cause"] == "js-shell"]
    assert not any("shell" in f["title"].lower() for f in result.findings)


def test_the_page_is_not_reported_as_written_short_instead():
    """The other half of the precedence rule. A page whose grid this audit
    dropped is not a page somebody wrote 171 characters on, and swapping one
    wrong finding for another is not a fix."""
    pages = _storefront()
    result = SkillResult("render-readability-audit")
    RENDER._check_shells(result, {"pages": pages}, pages, "static")
    assert "thin-html" not in _causes(result), [
        f["evidence"] for f in result.findings if f["root_cause"] == "thin-html"]


def test_every_number_a_shell_reason_prints_is_the_visible_text_count():
    """The sentence itself, not only the verdict.

    "173 chars of visible text" was true of nothing: the page held 3,517. Any
    reason quoting the extracted prose count under that wording is the defect
    returning in a page shape this file does not enumerate, so the rule is
    asserted over the field rather than over one string.
    """
    for visible, prose in ((80, 12), (250, 40), (120, 5), (299, 298),
                           (VISIBLE_TEXT, EXTRACTED_PROSE), (2672, 180)):
        page = _delivered_page(SITE + "/x", visible, prose)
        page["spa_shell"]["root_text_len"] = 0
        _, reasons = RENDER.shell_verdict(page)
        printed = " ".join(reasons)
        assert "{} chars of visible text".format(visible) in printed or \
               "visible text" not in printed, printed
        if prose != visible:
            assert "{} chars of visible text".format(prose) not in printed, printed


# --------------------------------------------------------------------------
# The direction that pays for it: a container is still named
# --------------------------------------------------------------------------

def test_a_document_that_delivers_nothing_is_still_a_shell():
    verdict, reasons = RENDER.shell_verdict(_shell_document())
    assert verdict == RENDER.SHELL, reasons
    assert "224,637" in reasons[0]


def test_an_empty_mount_point_beside_no_text_is_still_a_shell():
    page = _delivered_page(SITE + "/app", 120, 40)
    page["spa_shell"]["root_text_len"] = 0
    assert RENDER.shell_verdict(page)[0] == RENDER.SHELL


def test_the_widened_payload_vocabulary_still_reaches_the_verdict():
    """`STATE_BLOB_PATTERNS` went from seven names to sixteen because a hosted
    site builder's payload was invisible to the seven. Narrowing the field the
    blob signal is measured against must not narrow the vocabulary with it."""
    from page_extract import STATE_BLOB_PATTERNS
    assert len(STATE_BLOB_PATTERNS) >= 16, len(STATE_BLOB_PATTERNS)
    page = _delivered_page(SITE + "/builder", 90, 20)
    page["spa_shell"]["root_selector"] = None
    page["spa_shell"]["root_text_len"] = None
    page["spa_shell"]["state_blobs"] = ["viewerModel", "wixBiSession"]
    page["scripts"] = {"inline_bytes": 60000, "external_count": 4}
    verdict, reasons = RENDER.shell_verdict(page)
    assert verdict == RENDER.SHELL, reasons
    assert "viewerModel" in " ".join(reasons)


def test_a_shell_among_delivered_pages_is_still_found():
    """The storefront with one route that really does ship empty. The fix must
    not have turned the check off."""
    pages = _storefront() + [_shell_document()]
    result = SkillResult("render-readability-audit")
    RENDER._check_shells(result, {"pages": pages}, pages, "static")
    assert "js-shell" in _causes(result)
    finding = next(f for f in result.findings if f["root_cause"] == "js-shell")
    assert SITE + "/app" in finding["affected_pages"]
    assert SITE + "/" not in finding["affected_pages"], (
        "the server-rendered homepage was swept into the shell finding")


# --------------------------------------------------------------------------
# The same field, in the four checks downstream
# --------------------------------------------------------------------------

def test_a_dropped_product_grid_is_not_read_as_facts_locked_in_a_photograph():
    """`facts-locked-in-images` gated on `body_text_len` too: 180 characters
    of prose beside a product photograph reads as a page whose facts are in
    the picture, and the names and prices are in the 2,800 characters of
    visible text the extractor did not keep."""
    pages = _storefront()
    for page in pages:
        page["images"] = {"count": 4, "described_count": 0, "large_image_count": 2,
                          "missing_alt_count": 4, "svg_text_nodes": 0, "canvas_count": 0}
    result = SkillResult("render-readability-audit")
    RENDER._check_image_locked(result, pages, [])
    assert "image-locked-facts" not in _causes(result), [
        f["evidence"] for f in result.findings]
    reason = next(n["reason"] for n in result.not_applicable
                  if n["check"] == "facts-locked-in-images")
    assert "more visible text than this audit managed to extract" in reason


def test_a_dropped_product_grid_is_not_read_as_content_delegated_to_a_frame():
    pages = _storefront()
    for page in pages:
        page["iframes"] = [{"src": "https://a-booking-widget.example/w",
                            "is_video": False, "is_map": False, "is_invisible": False}]
    result = SkillResult("render-readability-audit")
    RENDER._check_iframed_content(result, pages, [])
    assert "iframe-content" not in _causes(result)


def test_a_dropped_product_grid_is_not_read_as_a_page_that_only_plays_video():
    pages = _storefront()
    for page in pages:
        page["video"] = {"native_count": 1, "embed_count": 0, "audio_count": 0,
                         "transcript_nearby": False, "track_count": 0,
                         "players": [{"autoplay": False, "muted": False, "loop": False}]}
    result = SkillResult("render-readability-audit")
    RENDER._check_video_transcripts(result, pages)
    assert "no-transcript" not in _causes(result)


def test_a_price_in_the_grid_is_a_price_even_when_the_extractor_lost_it():
    """`facts-locked-in-pdfs` asked `has_price(body_text)`. On this page the
    prices are in the visible text and not in the extracted prose, so the
    finding said the numbers exist only inside the brochure."""
    pages = _storefront()
    priced = pages[1]
    priced["text"] = "Charcoal tote bag Rs. 2,499 Linen shirt Rs. 1,899 " + "v" * 2600
    priced["pdf_links"] = [{"text": "Download the price list", "url": SITE + "/price.pdf"}]
    result = SkillResult("render-readability-audit")
    RENDER._check_pdf_locked(result, {"pages": pages, "sells": True}, pages)
    assert "pdf-locked-facts" not in _causes(result), [
        f["evidence"] for f in result.findings]
