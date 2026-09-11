# -*- coding: utf-8 -*-
"""A page publishing its own template source, and nothing noticing.

This turned up on three unrelated sites, and no check in the marketplace
looked for it:

  `{{getCtrlKey()}}` and     a single-page app served both as visible text - a
  `#native_company#`         template expression and an ad-network merge field,
                             neither ever substituted. render-readability
                             produced 0 findings on it.

  `[wpsl]`                   a store-locator page whose entire visible content
                             is that literal string. The report said "1 page
                             carries too little text to be quoted" and never
                             named the broken WP Store Locator plugin, which is
                             the entire fix.

One defect: the page publishes an instruction to its own template instead of
the content that instruction was supposed to produce. This file pins that the
check finds both, that it says which artefact and where, that a page whose
whole content is a placeholder gets the cause rather than "too little text to
be quoted", and - the half that decides whether this is a check or a
false-positive machine - that a site which publishes code samples does not trip
it.

Every host here is invented and every address is fictional. No plugin, vendor
or product is named: the report names the string the page prints, which is the
handle the owner searches for.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402
from page_extract import extract_page  # noqa: E402


def _module(skill, name):
    scripts = os.path.join(ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _module("render-readability-audit", "render_for_artefact_tests")

SITE = "https://an-invented-hardware-shop.test"
CHROME = ["/", "/about", "/shops", "/contact"]
FURNITURE = "Home About Shops Contact Copyright an invented shop. " * 12


def _page(path, body, title="A page", page_type="content", code_text="",
          code_samples=0, h1=(), meta_description="", chrome=FURNITURE):
    """One crawled page, in the shape `page_extract` records.

    `text` is the whole visible document - menu and footer included - and
    `body_text` is the page's own copy with the site furniture removed, which
    is the pair every reading in this skill has to keep straight.
    """
    url = SITE + path
    return {
        "url": url, "final_url": url, "status": 200, "page_type": page_type,
        "title": title,
        "body_text": body, "body_text_len": len(body),
        "text": (chrome + " " + body).strip(),
        "text_len": len(chrome) + 1 + len(body),
        "headings": {"h1": list(h1)},
        "meta_description": meta_description,
        "code_text": code_text,
        "code_sample_count": code_samples,
        "spa_shell": {"root_selector": None, "root_text_len": None,
                      "state_blobs": [], "state_blob_bytes": 0,
                      "noscript_demands_js": False,
                      "visible_text_len": len(chrome) + 1 + len(body)},
        "scripts": {"inline_bytes": 4000, "external_count": 2},
        "chrome_signature": {"nav_paths": list(CHROME)},
    }


def _written(index, words=90):
    return _page("/guide-{}".format(index),
                 "A paragraph about hardware. " * words,
                 title="Guide {}".format(index))


def _reason(result, check):
    return next(n["reason"] for n in result.not_applicable if n["check"] == check)


def _fire(pages):
    """Run the artefact check over these pages and hand back the result."""
    result = SkillResult("render-readability-audit")
    artefacts = RENDER._template_artefacts_by_page(pages)
    RENDER._check_template_artefacts(result, pages, artefacts)
    return result, artefacts


def _finding(result):
    return next(f for f in result.findings
                if f["id_hint"] == "unrendered-template-artefact")


# --------------------------------------------------------------------------
# The two cases seen in the wild
# --------------------------------------------------------------------------

STORE_LOCATOR = "/store-locator"


def _shortcode_site():
    """The store-locator page whose whole content is one raw shortcode."""
    return [_page(STORE_LOCATOR, "[wpsl]", title="Store locator")] + [
        _written(i) for i in range(5)]


def test_a_shortcode_standing_as_a_pages_whole_content_is_found():
    result, _ = _fire(_shortcode_site())
    finding = _finding(result)
    assert finding["affected_pages"] == [SITE + STORE_LOCATOR]
    assert finding["severity"] == "high"
    # The string itself, because the tag is the handle the owner searches for.
    assert "`[wpsl]`" in finding["evidence"]
    assert "whole of the page's own content" in finding["evidence"]


def test_the_fix_sends_the_owner_to_the_plugin_the_tag_names():
    result, _ = _fire(_shortcode_site())
    steps = " ".join(_finding(result)["suggested_action"]["how_to_fix"])
    assert "handle the plugin registers" in steps
    assert "View Source" in steps
    # Minutes, not a rebuild: this is the fix an owner can make the day they
    # read it, which is why the miss was expensive.
    assert _finding(result)["suggested_action"]["effort"] == "low"


def test_the_thin_finding_gives_the_cause_rather_than_the_symptom():
    """"1 page carries too little text to be quoted" was true and useless."""
    pages = _shortcode_site()
    result = SkillResult("render-readability-audit")
    artefacts = RENDER._template_artefacts_by_page(pages)
    RENDER._check_thin_pages(result, pages, [], None, (), artefacts=artefacts)

    assert not [f for f in result.findings if f["root_cause"] == "thin-html"], (
        "the page was reported as one somebody wrote too little on, which is "
        "the symptom of a template that did not run")
    reason = _reason(result, "thin-html")
    assert SITE + STORE_LOCATOR in reason
    assert "`[wpsl]`" in reason
    assert "writing more copy is not" in reason


def test_an_expression_and_a_merge_field_on_a_single_page_app_are_found():
    """The single-page app: both artefacts served as visible text."""
    app = _page("/", "Sign in {{getCtrlKey()}} to continue. Sponsored by "
                     "#native_company#. " + "Some interface copy here. " * 30,
                title="An app", page_type="home")
    result, _ = _fire([app] + [_written(i) for i in range(3)])
    finding = _finding(result)
    assert finding["affected_pages"] == [SITE + "/"]
    assert "`{{getCtrlKey()}}`" in finding["evidence"]
    assert "`#native_company#`" in finding["evidence"]
    # Not the same problem as one stray field in an inner page's footer: this
    # is the front door, and two different placeholders in one document is a
    # template failing rather than a typo.
    assert finding["severity"] == "medium"
    assert "the homepage" in finding["evidence"]
    # The verb agrees with the count. "1 URL were crawled" is a sentence this
    # marketplace once printed.
    assert "1 of the 4 pages that answered 200 prints" in finding["evidence"]


def test_a_placeholder_in_the_title_or_heading_is_high_severity():
    """A template filling the page's own name with its source is not the same
    problem as one stray field in a footer."""
    page = _page("/offers", "Real copy about offers. " * 40,
                 title="{{ page.title }}", h1=("{{ page.title }}",))
    result, _ = _fire([page] + [_written(i) for i in range(3)])
    finding = _finding(result)
    assert finding["severity"] == "high"
    assert "the page title" in finding["evidence"]


def test_one_stray_field_and_a_template_wide_one_are_not_the_same_problem():
    stray = _page("/contact", "Call us on 000. " * 40 + " ${footerYear}")
    result, _ = _fire([stray] + [_written(i) for i in range(4)])
    assert _finding(result)["severity"] == "low"

    everywhere = [_page("/p-{}".format(i), "Real copy here. " * 40 + " ${footerYear}")
                  for i in range(4)]
    result, _ = _fire(everywhere)
    finding = _finding(result)
    assert finding["severity"] == "medium"
    assert "shared template" in finding["evidence"]


# --------------------------------------------------------------------------
# What keeps a documentation site out of it
# --------------------------------------------------------------------------

def test_a_string_the_site_prints_in_its_own_code_samples_is_never_accused():
    """A templating tutorial, an API reference and this repository's own README
    all contain `{{ value | json }}`. A code sample is not a defect, and the
    crawl already separates the two: `code_text` holds the samples, `body_text`
    holds the prose with exactly those removed."""
    tutorial = _page("/docs/filters",
                     "Write {{ value | json }} to print the object. " * 12,
                     title="Filters", code_text="{{ value | json }}",
                     code_samples=3)
    result, artefacts = _fire([tutorial] + [_written(i) for i in range(4)])
    assert not result.findings
    assert artefacts.get(SITE + "/docs/filters") is None
    reason = _reason(result, "unrendered-template-artefact")
    assert "code samples" in reason


def test_a_documentation_tree_is_read_only_for_a_placeholder_standing_alone():
    """Where most pages carry code samples, the site publishes code for a
    living and an expression in its prose is its subject. The one reading that
    survives is the page whose whole content is a placeholder, because no
    tutorial's entire content is one unexplained string."""
    docs = [_page("/docs/{}".format(i),
                  "Use {{ item.name }} inside a loop to name the value. " * 10,
                  title="Page {}".format(i), code_text="print(x)", code_samples=2)
            for i in range(6)]
    result, _ = _fire(docs)
    assert not result.findings

    broken = _page("/docs/locator", "[wpsl]", title="Locator")
    result, _ = _fire(docs + [broken])
    finding = _finding(result)
    assert finding["affected_pages"] == [SITE + "/docs/locator"]
    assert "code samples of their own" in finding["evidence"]


def test_ordinary_writing_is_not_a_template_artefact():
    """The shapes are punctuation around an identifier. A hashtag, a bracketed
    label and a price are none of them."""
    pages = [
        _page("/blog", "Follow #sale# for offers, then [read more] below. " * 20),
        _page("/prices", "Everything from $5 to $500, and item [4] is new. " * 20),
    ] + [_written(i) for i in range(3)]
    result, _ = _fire(pages)
    assert not result.findings


def test_a_bracketed_label_is_not_a_shortcode_even_as_a_short_pages_content():
    """The dominance branch is where the loosest shape is allowed to count, so
    the shape has to be tight. A shortcode tag is one word, optionally followed
    by attributes - words with an `=` in them. `[read more]` is neither."""
    teaser = _page("/new", "Our new range [read more]", title="New range")
    result, _ = _fire([teaser] + [_written(i) for i in range(3)])
    assert not result.findings

    # And the shape with attributes, which is a real one, still counts.
    form = _page("/enquiries", '[contact-form-7 id="4" title="Enquiries"]',
                 title="Enquiries")
    result, _ = _fire([form] + [_written(i) for i in range(3)])
    assert _finding(result)["affected_pages"] == [SITE + "/enquiries"]


def test_the_check_does_not_depend_on_the_language_the_site_is_written_in():
    """No word list decides anything here, so the reading is the same on a page
    with no Latin letters in it."""
    thai = _page("/th/locator", "[wpsl]", title=u"สาขา")
    result, _ = _fire([thai] + [_written(i) for i in range(3)])
    assert _finding(result)["affected_pages"] == [SITE + "/th/locator"]
    # And the finding is not one a language statement would be attached to.
    assert "read in" not in _finding(result)["evidence"]


# --------------------------------------------------------------------------
# Through the real extractor, not a hand-written record
# --------------------------------------------------------------------------
#
# Both guards below were wrong until they were measured this way. The page's
# own copy of `{{ value | json }}` comes back as `{{value | json}}` - the
# extractor deletes the space after an opening brace and before a closing one -
# while the code sample keeps it, so an exact comparison matched nothing and
# the documentation guard was silently off. And the store-locator page's
# extracted copy repeats its own `<h1>`, so the placeholder is 6 characters of
# 34 rather than 6 of 6.

def _extracted(path, html):
    url = SITE + path
    return extract_page(url, url, 200, {}, html, [], 10, 1, "link", SITE)


LOCATOR_HTML = """<html lang="en"><head><title>Store locator</title></head><body>
<header><nav><a href="/">Home</a><a href="/shops">Shops</a></nav></header>
<main><h1>Store locator</h1><p>[wpsl]</p></main>
<footer><a href="/contact">Contact</a><p>An invented shop</p></footer></body></html>"""

FILTERS_HTML = """<html lang="en"><head><title>Filters</title></head><body>
<header><nav><a href="/">Home</a></nav></header>
<main><h1>Filters</h1>
<p>Write <code>{{ value | json }}</code> to print the object as JSON. This is the
filter to reach for when a template produces nothing and you cannot see why.</p>
<pre><code>{{ value | json }}</code></pre>
<p>More prose about the filter pipeline and every value you pass through it.</p>
</main></body></html>"""


def test_the_real_shortcode_page_survives_this_marketplaces_own_extractor():
    page = _extracted(STORE_LOCATOR, LOCATOR_HTML)
    result, artefacts = _fire([page] + [_written(i) for i in range(4)])
    record = artefacts[SITE + STORE_LOCATOR]
    assert record["stands_alone"], (
        "the extracted copy repeats the page's own <h1>, so the placeholder is "
        "a smaller share of it than a hand-written record suggests")
    assert _finding(result)["severity"] == "high"


def test_the_documentation_guard_holds_on_the_spelling_the_extractor_produces():
    """`{{ value | json }}` in a paragraph is recorded as `{{value | json}}`;
    the sample beside it keeps its spaces. Compared as they stand, the guard
    matches nothing."""
    page = _extracted("/docs/filters", FILTERS_HTML)
    assert "{{value | json}}" in page["text"]
    assert "{{ value | json }}" in page["code_text"]
    result, artefacts = _fire([page] + [_written(i) for i in range(4)])
    assert not result.findings
    assert SITE + "/docs/filters" not in artefacts


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

def test_a_page_this_audit_could_not_type_is_still_read_for_a_placeholder():
    """`other` is "not recognised", and a store-locator page types `other` on
    any site whose URLs do not use the words the type table knows. The claim
    here is about a literal string in the document, so the type cannot make it
    false."""
    pages = _shortcode_site()
    pages[0]["page_type"] = "other"
    pages[0]["body_text"] = "[wpsl]"
    snapshot = {"pages": pages, "crawl": {"render_mode": "static"}}
    result = RENDER.run(snapshot)
    finding = next(f for f in result.findings
                   if f["id_hint"] == "unrendered-template-artefact")
    assert finding["affected_pages"] == [SITE + STORE_LOCATOR]


def test_the_check_answers_in_exactly_one_place_on_a_clean_site():
    pages = [_written(i) for i in range(5)]
    result = RENDER.run({"pages": pages, "crawl": {"render_mode": "static"}})
    assert "unrendered-template-artefact" in result.checks_run
    assert "unrendered-template-artefact" not in result.fired_checks
    assert "unrendered-template-artefact" in {
        n["check"] for n in result.not_applicable}


def test_a_crawl_that_reached_nothing_prints_no_measurement_over_nothing():
    """"each of the 0 pages this check could assess" is what a sibling check
    printed on a one-page crawl of a shell."""
    result = RENDER.run({"pages": [], "crawl": {"render_mode": "static"}})
    reason = _reason(result, "unrendered-template-artefact")
    assert "no page returned HTTP 200" in reason
    assert "0 pages" not in reason


def test_the_finding_and_the_check_that_raised_it_agree():
    pages = _shortcode_site()
    result = RENDER.run({"pages": pages, "crawl": {"render_mode": "static"}})
    finding = next(f for f in result.findings
                   if f["id_hint"] == "unrendered-template-artefact")
    assert finding["check"] == "unrendered-template-artefact"
    assert "unrendered-template-artefact" in result.fired_checks
    assert "unrendered-template-artefact" not in {
        n["check"] for n in result.not_applicable}
