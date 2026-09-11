# -*- coding: utf-8 -*-
"""Four symptoms, one cause, and a negative that described a different page.

Both halves come from running the frozen build on five real storefronts.

1. A shoe brand's storefront, built on a Vue framework, delivers 1,088
   characters of static visible text and zero `<nav>` elements: the product copy
   is server-rendered and the header, menu and footer are built in the browser.
   `spa-shell-detection` passed it verbatim - "every crawled content page
   delivered readable text in the initial HTML response" - because every test it
   makes is about the page's *body* and every one of them is correctly answered
   "no" here. Four findings then fired across four other skills, and the point is
   that no off-site profiles, a navigation with zero items, no
   `<h1>` and no contact route "are all the same fact: the header, nav and
   footer are client-rendered... The report never names client-side rendering."
   The owner was handed four unrelated-looking chores instead of one diagnosis.

2. A candle shop's thin-copy finding said "the response carries no framework
   mount point, no state blob and no bulk of script that could be holding the
   copy back until it runs" about a homepage carrying 558,468 characters of
   inline script - 42.9% of a 1,300,857-character document - and the hosted site
   builder's own `viewerModel`, `wixBiSession`, `warmupData` and `siteAssets`.
   Two of the three clauses were false of the page they were about. They were
   false because they were statements about a fixed list of names rather than
   about the document, and the report printed them as facts about the site.

Every host in this file is invented and every address is fictional.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402
from page_extract import SPA_ROOT_SELECTORS, STATE_BLOB_PATTERNS  # noqa: E402


def _module(skill, name):
    scripts = os.path.join(ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _module("render-readability-audit", "render_for_chrome_tests")

SHOP = "https://an-invented-shoe-brand.test"
MENU = ["/", "/collections/sneakers", "/pages/about", "/pages/contact"]


def _reason(result, check):
    return next(n["reason"] for n in result.not_applicable if n["check"] == check)


def _hints(result):
    return {f["id_hint"] for f in result.findings}


# --------------------------------------------------------------------------
# The storefront whose copy arrives and whose chrome does not
# --------------------------------------------------------------------------

def _chrome_in_browser_page(url=SHOP + "/", page_type="home", body=1088, **extra):
    """The delivered document: product copy, and no layout around it.

    1,088 characters of visible text, the whole of it the page's own copy,
    because the header, menu and footer are not in this response at all. The
    framework's mount point is, which is what says where they went.
    """
    page = {
        "url": url, "final_url": url, "status": 200, "page_type": page_type,
        "title": "Sneakers",
        "body_text": "x" * body, "body_text_len": body, "text_len": body,
        "headings": {"h1": [], "h2": ["Materials"]},
        "headings_in_markup": {"h1": [], "h2": ["Materials"]},
        "links": {"nav": [], "footer": [], "internal": [], "external": []},
        "chrome_signature": {"nav_paths": [], "nav_path_count": 0},
        "spa_shell": {"root_selector": "#__nuxt", "root_text_len": body,
                      "state_blobs": [], "state_blob_bytes": 0,
                      "noscript_demands_js": False, "visible_text_len": body},
        "scripts": {"inline_bytes": 21000, "external_count": 8},
    }
    page.update(extra)
    return page


def _server_rendered_page(index, body=900, chrome=700):
    """An ordinary page of the same site: copy, menu, footer and a heading."""
    url = "{}/products/shoe-{}".format(SHOP, index)
    return {
        "url": url, "final_url": url, "status": 200, "page_type": "product",
        "title": "Shoe {}".format(index),
        "body_text": "y" * body, "body_text_len": body, "text_len": body + chrome,
        "headings": {"h1": ["Shoe {}".format(index)]},
        "headings_in_markup": {"h1": ["Shoe {}".format(index)]},
        "links": {"nav": [{"url": SHOP + path, "text": path} for path in MENU],
                  "footer": [{"url": SHOP + "/pages/contact", "text": "Contact"}],
                  "internal": [], "external": []},
        "chrome_signature": {"nav_paths": list(MENU), "nav_path_count": len(MENU)},
        "spa_shell": {"root_selector": "#__nuxt", "root_text_len": body + chrome,
                      "state_blobs": [], "state_blob_bytes": 0,
                      "noscript_demands_js": False, "visible_text_len": body + chrome},
        "scripts": {"inline_bytes": 21000, "external_count": 8},
    }


def _run(pages, render_mode="static"):
    snapshot = {"pages": pages, "crawl": {"render_mode": render_mode}}
    return RENDER.run(snapshot), snapshot


def test_the_page_is_not_a_shell_and_is_not_reported_as_one():
    """The guard in the other direction, first.

    An earlier build called a complete server-rendered page a shell because the
    extractor had missed its text, and offered several days of development as
    the fix. This page's copy did arrive; only its layout did not, so nothing
    here may say the template ships empty.
    """
    page = _chrome_in_browser_page()
    assert RENDER.shell_verdict(page)[0] == ""

    result, _ = _run([page] + [_server_rendered_page(i) for i in range(3)])
    assert "homepage-is-javascript-shell" not in _hints(result)
    assert "content-pages-are-javascript-shells" not in _hints(result)
    assert "pages-too-thin-to-quote" not in _hints(result)


def test_the_missing_chrome_is_named_as_one_defect():
    """The diagnosis the report never printed.

    Four skills each reported a symptom. This is the finding they describe, and
    it says so in as many words so a reader is not sent to fix four things.
    """
    pages = [_chrome_in_browser_page(),
             _chrome_in_browser_page(url=SHOP + "/collections/sneakers",
                                     page_type="category")]
    result, _ = _run(pages)
    finding = next(f for f in result.findings
                   if f["id_hint"] == "chrome-is-client-rendered")

    assert finding["root_cause"] == "js-shell"
    assert finding["severity"] == "high"
    evidence = finding["evidence"]
    assert "#__nuxt" in evidence
    assert "1,088 characters of visible text did arrive" in evidence
    # The sentence the four other skills defer to.
    for symptom in ("empty navigation", "missing <h1>",
                    "off-site profile links", "contact page"):
        assert symptom in evidence, symptom
    assert "four separate ones" in evidence
    assert sorted(finding["affected_pages"]) == sorted(p["url"] for p in pages)


def test_the_shell_check_no_longer_passes_the_page_in_silence():
    """"every crawled content page delivered readable text in the initial HTML
    response" is true of this site and was printed alone. It may still be said;
    it may not be the only thing said."""
    pages = [_chrome_in_browser_page(),
             _chrome_in_browser_page(url=SHOP + "/collections/sneakers",
                                     page_type="category")]
    result, _ = _run(pages)
    reason = _reason(result, "spa-shell-detection")
    assert "delivered readable text" in reason
    assert "client-rendered-chrome" in reason
    assert "no navigation, footer or heading of their own" in reason


def test_a_short_page_with_a_plain_menu_is_not_this():
    """The false positive to avoid. A page can be short, ship a framework and
    still deliver its own menu, and that page is not this defect."""
    page = _server_rendered_page(0, body=120)
    assert RENDER._chrome_built_in_the_browser(page) == []

    result, _ = _run([page] + [_server_rendered_page(i) for i in range(1, 4)])
    assert "chrome-is-client-rendered" not in _hints(result)
    assert "delivers its own navigation, footer or heading" in _reason(
        result, "client-rendered-chrome")


def test_a_menu_written_without_a_nav_element_still_counts_as_a_menu():
    """`page_extract._links` falls back to any block near the top of the
    document holding three or more links, so a site that writes its menu in
    `<div>`s records nav links and no `<nav>`. Reading the element name alone
    would report that site as client-rendered."""
    page = _chrome_in_browser_page()
    page["links"] = {"nav": [{"url": SHOP + path, "text": path} for path in MENU],
                     "footer": [], "internal": [], "external": []}
    assert RENDER._chrome_built_in_the_browser(page) == []


def test_a_screen_reader_only_heading_counts_as_delivered():
    """A CMS theme that ships `<h1 style="display:none">` has delivered a
    heading: `page_extract` records it in `headings_in_markup` and not in
    `headings`, and reading only the visible tree would call it absent."""
    page = _chrome_in_browser_page()
    page["headings_in_markup"] = {"h1": ["Sneakers"]}
    assert RENDER._chrome_built_in_the_browser(page) == []


def test_a_page_with_no_framework_marker_is_left_to_another_skill():
    """A static page with no menu is a site that never built one - a different
    defect with a different owner, and `engagement-audit` raises it. Without a
    mount point or a hydration payload there is nothing here saying a template
    runs in the browser at all."""
    page = _chrome_in_browser_page()
    page["spa_shell"] = {"root_selector": None, "root_text_len": None,
                         "state_blobs": [], "state_blob_bytes": 0,
                         "noscript_demands_js": False, "visible_text_len": 1088}
    assert RENDER._chrome_built_in_the_browser(page) == []


def test_a_hydration_payload_serves_as_the_marker_where_no_mount_point_does():
    """`SPA_ROOT_SELECTORS` matches an id or attribute exactly, so a build that
    names its mount anything outside that list records no root. The payload in
    the markup says the same thing."""
    page = _chrome_in_browser_page()
    page["spa_shell"] = dict(page["spa_shell"], root_selector=None,
                             root_text_len=None, state_blobs=["__NUXT__"])
    reasons = RENDER._chrome_built_in_the_browser(page)
    assert reasons and "__NUXT__" in reasons[1]


def test_one_page_missing_its_menu_is_not_the_site_template():
    """Under half, and the fix for one page is not the fix for a layout."""
    pages = [_chrome_in_browser_page(url=SHOP + "/pages/lookbook",
                                     page_type="category")]
    pages += [_server_rendered_page(i) for i in range(5)]
    result, _ = _run(pages)
    assert "chrome-is-client-rendered" not in _hints(result)
    reason = _reason(result, "client-rendered-chrome")
    assert "1 of the 6 content pages" in reason
    assert "under the half" in reason


def test_a_page_re_read_from_the_browser_cannot_testify_about_the_wire():
    """The crawl replaces a shell's record with the rendered DOM and keeps only
    that page's delivered lengths and shell markers - not its links, headings or
    chrome fingerprint. Reading those three there would be asserting an absence
    from a document that is no longer in the snapshot."""
    adopted = _chrome_in_browser_page(url=SHOP + "/pages/lookbook",
                                      page_type="category")
    adopted["content_from"] = "rendered"
    adopted["static_view"] = {"text_len": 40, "body_text_len": 40,
                              "spa_shell": adopted["spa_shell"],
                              "scripts": adopted["scripts"]}
    assert RENDER._chrome_built_in_the_browser(adopted) == []

    result, _ = _run([adopted] + [_server_rendered_page(i) for i in range(3)])
    assert "chrome-is-client-rendered" not in _hints(result)
    assert "re-read from the browser's document" in _reason(
        result, "client-rendered-chrome")


def test_the_finding_does_not_claim_the_check_that_passed():
    """`SkillResult` sweeps up every check registered by a function on the
    stack when a finding is added. Raised inside `_check_shells`, this finding
    would mark `spa-shell-detection` as having fired on a run where it passed,
    putting one check in two buckets the report presents as exclusive."""
    result, _ = _run([_chrome_in_browser_page(),
                      _chrome_in_browser_page(url=SHOP + "/collections/sneakers",
                                              page_type="category")])
    assert "client-rendered-chrome" in result.fired_checks
    assert "spa-shell-detection" not in result.fired_checks
    assert {n["check"] for n in result.not_applicable} & {"spa-shell-detection"}


# --------------------------------------------------------------------------
# A negative in evidence has to be as checkable as a positive
# --------------------------------------------------------------------------

CANDLES = "https://an-invented-candle-shop.test"
# A hosted site builder's shared header and footer: the same on every page, and
# large. The size matters - the thin check subtracts the template's own
# furniture before it calls a page empty, so the number has to be the same on
# the pages that were written and on the page that was not.
BUILDER_CHROME = 11941
BUILDER_INLINE_SCRIPT = 558468


def _builder_page(url, body, script_bytes=BUILDER_INLINE_SCRIPT):
    return {
        "url": url, "final_url": url, "status": 200, "page_type": "product",
        "title": "Candles",
        "body_text": "z" * body, "body_text_len": body,
        "text_len": body + BUILDER_CHROME,
        "headings": {"h1": ["Candles"]},
        "links": {"nav": [], "footer": [], "internal": [], "external": []},
        "chrome_signature": {"nav_paths": list(MENU), "nav_path_count": len(MENU)},
        # No mount point and no payload name this audit knows: the builder's own
        # `viewerModel`, `wixBiSession`, `warmupData` and `siteAssets` are not in
        # `STATE_BLOB_PATTERNS`, and its root container is not in
        # `SPA_ROOT_SELECTORS`, so both detectors record nothing.
        "spa_shell": {"root_selector": None, "root_text_len": None,
                      "state_blobs": [], "state_blob_bytes": 0,
                      "noscript_demands_js": False,
                      "visible_text_len": body + BUILDER_CHROME},
        "scripts": {"inline_bytes": script_bytes, "external_count": 2},
    }


def _thin_finding():
    written = [_builder_page("{}/candle-{}".format(CANDLES, i), 900)
               for i in range(6)]
    bare = _builder_page(CANDLES + "/wholesale", 59)
    result = SkillResult("render-readability-audit")
    RENDER._check_thin_pages(result, written + [bare], [])
    return next(f for f in result.findings
                if f["id_hint"] == "pages-too-thin-to-quote")


def test_the_two_false_clauses_are_gone():
    """Both were printed about a document carrying 558,468 characters of inline
    script and four of the builder's own state blobs."""
    evidence = _thin_finding()["evidence"]
    assert "no framework mount point" not in evidence
    assert "no state blob" not in evidence
    assert "no bulk of script" not in evidence


def test_the_script_clause_states_what_was_counted_on_these_pages():
    """The sentence that tells a reader why a page was not called a shell has to
    describe the document that was measured."""
    evidence = _thin_finding()["evidence"]
    assert "{:,}".format(BUILDER_INLINE_SCRIPT) in evidence
    assert "{:,}".format(59 + BUILDER_CHROME) in evidence
    # And the bar it fell short of, so the reader can redo the arithmetic.
    assert str(RENDER.SCRIPT_BYTES_PER_CHARACTER) in evidence
    assert "{:,}".format(RENDER.HEAVY_SCRIPT_BYTES) in evidence


def test_the_absence_clauses_name_the_lists_that_make_them_negative():
    """"No state blob" means "no name from a list of seven". The list is what
    the claim rests on, so the claim has to name it - a reader cannot otherwise
    tell that a hosted builder's own payload was never looked for."""
    evidence = _thin_finding()["evidence"]
    for name in STATE_BLOB_PATTERNS:
        assert "`{}`".format(name) in evidence, name
    assert str(len(STATE_BLOB_PATTERNS)) in evidence
    assert str(len(SPA_ROOT_SELECTORS)) in evidence
    assert "`{}`".format(SPA_ROOT_SELECTORS[0]) in evidence


def test_the_vocabularies_come_from_the_extractor_that_applies_them():
    """Copied into this skill they would drift the moment either was widened,
    and the evidence line would go on naming a list nobody uses."""
    assert RENDER.STATE_BLOB_PATTERNS is STATE_BLOB_PATTERNS
    assert RENDER.SPA_ROOT_SELECTORS is SPA_ROOT_SELECTORS


def test_a_mount_point_that_is_present_is_reported_with_its_size():
    """The clause is only a negative when the detector found nothing. Where a
    root is there and simply not empty, saying so is the checkable statement."""
    written = [_builder_page("{}/candle-{}".format(CANDLES, i), 900)
               for i in range(6)]
    bare = _builder_page(CANDLES + "/wholesale", 59)
    for page in written + [bare]:
        page["spa_shell"] = dict(page["spa_shell"], root_selector="#app",
                                 root_text_len=page["text_len"])
    result = SkillResult("render-readability-audit")
    RENDER._check_thin_pages(result, written + [bare], [])
    evidence = next(f for f in result.findings
                    if f["id_hint"] == "pages-too-thin-to-quote")["evidence"]
    assert "`#app`" in evidence
    assert "{:,}".format(59 + BUILDER_CHROME) in evidence
    assert str(RENDER.EMPTY_ROOT_TEXT) in evidence
