"""Four rules that hold for every check, including the ones not written yet.

Twenty-six audited sites produced a long list of wrong findings, and fixing
them one at a time fixed one site at a time - the next site broke a different
check the same way. Sorted by what they violate rather than by which function
produced them, almost all of them are four mistakes:

  1. the crawler was not allowed to look, and the report said the thing is
     not there;
  2. a count of what one check happened to examine, presented as a fact about
     the site;
  3. text that repeats on every page, read as what one page is about;
  4. a value nobody observed, printed inside code labelled paste-ready.

Each is now prevented in one place that sees the whole run, so a check written
next month inherits the fix. These tests pin the rules, not the sites.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

import crawl as crawl_module  # noqa: E402


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_structural_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMPOSE = _compose()


# --------------------------------------------------------------------------
# 1. "We could not look" is not "it is not there"
# --------------------------------------------------------------------------

def _finding(title, mechanism="C"):
    return {"title": title, "mechanism": mechanism, "detected_by": "some-skill",
            "root_cause": "thin-html", "evidence": "", "affected_pages": []}


def _snapshot(readable, blocked):
    pages = [{"url": "https://example.test/ok%d" % i, "status": 200} for i in range(readable)]
    pages += [{"url": "https://example.test/no%d" % i, "status": 403} for i in range(blocked)]
    return {"pages": pages, "origin": "https://example.test", "site": "example.test"}


def test_an_absence_claim_is_held_back_when_the_crawl_was_refused():
    """A charity was told it never states its founding facts, from four pages
    of sixty, while the page that states them sat in the same report's crawl
    table marked 403."""
    kept, held = COMPOSE.withhold_absence_claims(
        [_finding("The site never states its founding facts in plain text")],
        _snapshot(readable=2, blocked=58))
    assert kept == []
    assert len(held) == 1
    assert "2 of the 60" in held[0]["reason"]


def test_the_same_claim_stands_when_the_crawl_read_the_site():
    kept, held = COMPOSE.withhold_absence_claims(
        [_finding("The site never states its founding facts in plain text")],
        _snapshot(readable=58, blocked=2))
    assert len(kept) == 1 and held == []


def test_the_access_finding_survives_because_it_is_the_one_to_act_on():
    """It is phrased as an absence too - "does not return HTTP 200" - and it
    is the only thing the reader can do anything about."""
    kept, held = COMPOSE.withhold_absence_claims(
        [_finding("The homepage does not return HTTP 200", mechanism="A")],
        _snapshot(readable=0, blocked=60))
    assert len(kept) == 1 and held == []


def test_a_finding_that_claims_no_absence_survives_a_block():
    kept, held = COMPOSE.withhold_absence_claims(
        [_finding("Structured data contradicts what the page says")],
        _snapshot(readable=1, blocked=59))
    assert len(kept) == 1 and held == []


def test_a_brand_name_is_not_read_as_part_of_the_claim():
    """A medical charity is called "Doctors Without Borders", so every finding
    naming it contained "without" and the whole report was held back."""
    assert COMPOSE.claims_absence(
        {"title": '2 other entities share the name "Doctors Without Borders"'}) is False
    assert COMPOSE.claims_absence(
        {"title": "Doctors Without Borders has no sitemap"},
        brand_name="Doctors Without Borders") is True


@pytest.mark.parametrize("title,expected", [
    ("No XML sitemap is available", True),
    ("The site never states its pricing", True),
    ("3 pages have no H1", True),
    ("Organization markup is missing sameAs", True),
    ("Structured data contradicts what the page says", False),
    ("The homepage is delivered as an empty JavaScript shell", False),
])
def test_what_counts_as_an_absence_claim(title, expected):
    assert COMPOSE.claims_absence({"title": title}) is expected


# --------------------------------------------------------------------------
# 2. Every count carries the scale it was drawn from
# --------------------------------------------------------------------------

def test_a_count_without_a_denominator_gets_one():
    """"3 page(s) have no H1" on a site where fifteen did."""
    finding = {"evidence": "Pages with no H1: /a, /b, /c.",
               "affected_pages": ["/a", "/b", "/c"]}
    out = COMPOSE.state_the_denominator(finding, 60)
    assert "3 of the 60 pages this crawl read" in out


@pytest.mark.parametrize("evidence", [
    "3 of 60 pages have no H1.",
    "48.0% of sections open with warm-up prose.",
    "12/60 pages carry no title.",
])
def test_evidence_that_already_states_its_scale_is_left_alone(evidence):
    finding = {"evidence": evidence, "affected_pages": ["/a"]}
    assert COMPOSE.state_the_denominator(finding, 60) == evidence


def test_a_finding_with_no_pages_is_left_alone():
    """robots.txt and entity ambiguity are about the site, not about pages."""
    finding = {"evidence": "robots.txt disallows GPTBot.", "affected_pages": []}
    assert COMPOSE.state_the_denominator(finding, 60).endswith("GPTBot.")


# --------------------------------------------------------------------------
# 3. Text that repeats across the site is furniture
# --------------------------------------------------------------------------

BANNER = "Free standard shipping on orders $40+ | $60 CAD+ | $110 AUD+ | R$670+"
FOOTER = "Copyright 2026 Example Ltd. All rights reserved. Registered in England."


def _page(index):
    unique = "This page is about item number {} and nothing else at all here.".format(index)
    return {"url": "https://example.test/p{}".format(index), "status": 200,
            "paragraphs": [BANNER, unique, FOOTER],
            "sections": [{"heading": "About", "first_paragraph": unique}],
            "body_text": " ".join([BANNER, unique, FOOTER])}


def test_a_block_on_most_pages_is_removed_from_every_page():
    """No selector list would have caught this one: the retailer ships it in a
    plain div above the header, and the next site uses a different div."""
    pages = [_page(i) for i in range(8)]
    notes = []
    crawl_module._strip_sitewide_boilerplate(pages, notes)
    assert BANNER not in pages[0]["body_text"]
    assert FOOTER not in pages[0]["body_text"]
    assert "item number 0" in pages[0]["body_text"]
    assert pages[0]["paragraphs"] == ["This page is about item number 0 and nothing "
                                      "else at all here."]
    assert notes and "site furniture" in notes[0]


def test_the_pages_own_content_survives():
    pages = [_page(i) for i in range(8)]
    crawl_module._strip_sitewide_boilerplate(pages, [])
    for index, page in enumerate(pages):
        assert "item number {}".format(index) in page["body_text"]
        assert len(page["sections"]) == 1


def test_a_short_repeated_label_is_not_stripped():
    """"Read more" repeats everywhere and is nobody's content, but stripping
    short strings would empty the tables that legitimately repeat cells."""
    pages = []
    for i in range(8):
        pages.append({"url": "https://example.test/p%d" % i, "status": 200,
                      "paragraphs": ["Read more", "Body number %d here." % i],
                      "sections": [], "body_text": "Read more Body number %d here." % i})
    crawl_module._strip_sitewide_boilerplate(pages, [])
    assert "Read more" in pages[0]["body_text"]


def test_a_small_crawl_is_left_alone():
    """On three pages every block is "on most pages"."""
    pages = [_page(i) for i in range(3)]
    crawl_module._strip_sitewide_boilerplate(pages, [])
    assert BANNER in pages[0]["body_text"]


# --------------------------------------------------------------------------
# 4. A snippet may only contain what the audit observed
# --------------------------------------------------------------------------

SNAPSHOT = {
    "origin": "https://example.test", "site": "example.test",
    "brand": {"name": "Coppergate Ceramics"},
    "pages": [{"url": "https://example.test/", "title": "Coppergate Ceramics",
               "text": "41 Walmgate, York YO1 9TT. Call 01904 555812. Mug 18.00 GBP.",
               "jsonld": [], "og": {}}],
}

SNIPPET = (
    '<script type="application/ld+json">\n{\n'
    '  "@context": "https://schema.org",\n'
    '  "@type": "Organization",\n'
    '  "name": "Coppergate Ceramics",\n'
    '  "telephone": "01904 555812",\n'
    '  "streetAddress": "1 million row",\n'
    '  "postalCode": "00005",\n'
    '  "description": "<one sentence saying what you do>"\n'
    '}\n</script>')


def test_a_value_the_site_never_showed_becomes_a_placeholder():
    """A streetAddress from a sentence about database rows, and a postal code
    from the price "$0.00005 / event", both reached real reports inside code
    labelled paste-ready."""
    out, invented = COMPOSE.hold_back_invented_values(
        SNIPPET, COMPOSE.observed_values(SNAPSHOT))
    assert "1 million row" not in out
    assert '"00005"' not in out
    assert len(invented) == 2


def test_values_the_site_did_show_survive():
    out, _ = COMPOSE.hold_back_invented_values(
        SNIPPET, COMPOSE.observed_values(SNAPSHOT))
    assert '"name": "Coppergate Ceramics"' in out
    assert '"telephone": "01904 555812"' in out


def test_placeholders_and_vocabulary_are_left_alone():
    out, invented = COMPOSE.hold_back_invented_values(
        SNIPPET, COMPOSE.observed_values(SNAPSHOT))
    assert '"description": "<one sentence saying what you do>"' in out
    assert '"@context": "https://schema.org"' in out
    assert '"@type": "Organization"' in out
    assert not [i for i in invented if "description" in i or "@" in i]


def test_a_value_only_present_in_the_sites_own_markup_counts_as_observed():
    snapshot = dict(SNAPSHOT)
    snapshot["pages"] = [{"url": "https://example.test/", "title": "", "text": "",
                          "jsonld": [{"@type": "Organization",
                                      "sameAs": ["https://github.com/coppergate"]}],
                          "og": {}}]
    snippet = '{\n  "sameAs": "https://github.com/coppergate"\n}'
    out, invented = COMPOSE.hold_back_invented_values(
        snippet, COMPOSE.observed_values(snapshot))
    assert invented == [] and "github.com/coppergate" in out
