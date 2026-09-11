# -*- coding: utf-8 -*-
"""A clause in evidence has to be a statement about the document it describes.

A candle shop's report said its thin pages carried

    "no framework mount point, no state blob and no bulk of script that could
     be holding the copy back until it runs"

about a homepage whose delivered HTML held `viewerModel` fifteen times,
`wixBiSession` nine times, `warmupData` and `siteAssets`, and 558,468
characters of inline script - 42.9% of a 1,300,857-character document. Two of
the three clauses were false of the page they were about, and the same report
named the hosting platform in its own fix steps two sections later.

They were false because none of them was a statement about the document. "No
state blob" meant "no name from `page_extract.STATE_BLOB_PATTERNS`", and that
list held seven names, all of them from JavaScript frameworks a developer
picks - so it could not see a hosted site builder's payload, which is the shape
this marketplace meets most. "No bulk of script" meant "the three-part ratio in
`shell_verdict` did not fire".

Two halves, and both are needed. Widening the vocabulary stops the page being
called thin at all; printing what was counted means a reader can check the
clause on the next page whose payload nobody has named yet.

Every host and every identifier below is invented or is a platform name the
code already detects.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from page_extract import STATE_BLOB_PATTERNS, _spa_shell, make_soup  # noqa: E402


def _render():
    scripts = os.path.join(ROOT, "skills", "render-readability-audit", "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "render_for_shell_clause_tests", os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _render()

SITE = "https://an-invented-candle-shop.test"


# --------------------------------------------------------------------------
# The vocabulary, widened past one framework's names
# --------------------------------------------------------------------------

# The four payload names the shop's homepage carried, none of which the list
# recognised. `viewerModel` and `wixBiSession` name a platform this audit
# already detects elsewhere; `warmupData` and `siteAssets` are that platform's
# too.
BUILDER_PAYLOADS = ("viewerModel", "wixBiSession", "warmupData", "siteAssets")


def _builder_homepage(payload_chars=558468):
    """A hosted site builder's homepage: a payload, and nothing else readable."""
    blob = '{{"pageId":"c1","data":"{}"}}'.format("d" * payload_chars)
    return (
        "<html><body><div id='SITE_CONTAINER'></div>"
        "<script>window.viewerModel = {};</script>"
        "<script>window.wixBiSession = {{'ts': 1}}; var warmupData = 1;</script>"
        "<script type='application/json' id='siteAssets'>{{}}</script>"
        "<p>Candles</p></body></html>".format(blob)
    )


def test_a_hosted_builders_payload_names_are_recognised_as_state_blobs():
    html = _builder_homepage(payload_chars=2000)
    shell = _spa_shell(make_soup(html), html, "Candles")
    for name in BUILDER_PAYLOADS:
        assert name in shell["state_blobs"], (
            "{} is a hydration payload and this audit cannot see it".format(name))


def test_the_payload_bytes_are_counted_rather_than_reported_as_none():
    """The field used to be one regular expression matching a single
    framework's name, so it returned 0 on every document whose payload is
    somebody else's - including the one it was written for."""
    html = _builder_homepage(payload_chars=50000)
    shell = _spa_shell(make_soup(html), html, "Candles")
    assert shell["state_blob_bytes"] > 50000, shell["state_blob_bytes"]


def test_a_hydration_payload_is_not_counted_twice():
    """A payload is an inline script. Adding the two totals counted every byte
    of it twice, in a number the evidence prints and a reader re-measures."""
    html = _builder_homepage(payload_chars=50000)
    shell = _spa_shell(make_soup(html), html, "Candles")
    inline = sum(len(s.get_text() or "") for s in make_soup(html).find_all("script"))
    counted = RENDER._script_bytes({"scripts": {"inline_bytes": inline},
                                    "spa_shell": shell})
    assert counted == inline, (counted, inline, shell["state_blob_bytes"])


def test_the_framework_names_that_were_already_there_still_match():
    """The widening may not cost the seven names the list already held."""
    for name in ("__NEXT_DATA__", "__NUXT__", "__INITIAL_STATE__",
                 "__APOLLO_STATE__", "__REDUX_STATE__", "__remixContext",
                 "self.__next_f"):
        assert name in STATE_BLOB_PATTERNS, name
    html = '<html><body><script id="__NEXT_DATA__">{"props":{}}</script></body></html>'
    shell = _spa_shell(make_soup(html), html, "")
    assert shell["state_blobs"] == ["__NEXT_DATA__"]
    assert shell["state_blob_bytes"] == len('{"props":{}}')


def test_nuxts_payload_element_is_read_too():
    html = ('<html><body><script type="application/json" id="__NUXT_DATA__">'
            '["state"]</script></body></html>')
    shell = _spa_shell(make_soup(html), html, "")
    assert "__NUXT_DATA__" in shell["state_blobs"]
    assert shell["state_blob_bytes"] == len('["state"]')


def test_an_ordinary_page_carries_no_state_blob():
    """The direction that pays for the widening. Every added name is a
    camel-cased or double-underscored identifier out of a script, so ordinary
    prose must not match one."""
    html = ("<html><body><h1>Beeswax candles</h1>"
            "<p>Our site assets are hand poured in small batches, and the "
            "viewer model of every candle is photographed in daylight.</p>"
            "<script>console.log('hello');</script></body></html>")
    shell = _spa_shell(make_soup(html), html, "Beeswax candles")
    assert shell["state_blobs"] == [], shell["state_blobs"]
    assert shell["state_blob_bytes"] == 0


# --------------------------------------------------------------------------
# The clause, as a statement of what was counted
# --------------------------------------------------------------------------

def _thin_page(url, visible=12, inline=558468, delivered=1300857, blobs=()):
    return {
        "url": url, "final_url": url, "status": 200, "page_type": "content",
        "title": "Candles", "body_text": "x" * visible, "body_text_len": visible,
        "text_len": visible, "html_bytes": delivered,
        "spa_shell": {"root_selector": None, "root_text_len": None,
                      "state_blobs": list(blobs), "state_blob_bytes": 0,
                      "noscript_demands_js": False, "visible_text_len": visible},
        "scripts": {"inline_bytes": inline, "external_count": 2},
    }


def test_each_clause_names_the_number_or_the_vocabulary_behind_it():
    counted = RENDER._shell_signals_counted([_thin_page(SITE + "/")])
    # The script clause: the bytes, the share of the document they are, and the
    # three bars they were tested against.
    assert "558,468 bytes of inline script" in counted, counted
    assert "42.9% of the 1,300,857 bytes delivered" in counted, counted
    # The two vocabulary-limited clauses say they are limited, and name the
    # vocabulary, so "none found" cannot be read as "the document has none".
    assert "mount point from the" in counted and "matches" in counted
    assert "hydration payload names" in counted
    assert "`viewerModel`" in counted, "the vocabulary printed is the one applied"


def test_a_page_that_does_carry_a_blob_says_which_one():
    counted = RENDER._shell_signals_counted(
        [_thin_page(SITE + "/", blobs=["viewerModel", "wixBiSession"])])
    assert "`viewerModel`" in counted and "`wixBiSession`" in counted
    assert "are present" in counted, counted


def test_the_clause_never_claims_the_document_has_no_state_blob():
    """The sentence as shipped. Any wording asserting an absence of the
    thing rather than an absence from the list is the defect coming back."""
    counted = RENDER._shell_signals_counted([_thin_page(SITE + "/")])
    for banned in ("no state blob", "no framework mount point",
                   "no bulk of script"):
        assert banned not in counted, banned
