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
    # The readable pages carry text, because that is what readable means. A
    # 200 that delivered nothing is not a page the site was read on, and a
    # helper that built bare status codes was describing a crawl of empty
    # responses while its parameter said the pages had been read.
    pages = [{"url": "https://example.test/ok%d" % i, "status": 200,
              "body_text": "This page states a fact a reader could act on."}
             for i in range(readable)]
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

    # The same claim stands when the crawl did read the site.
    kept, held = COMPOSE.withhold_absence_claims(
        [_finding("The site never states its founding facts in plain text")],
        _snapshot(readable=58, blocked=2))
    assert len(kept) == 1 and held == []


def test_the_findings_a_block_does_not_touch():
    """The access finding is phrased as an absence too - "does not return HTTP
    200" - and it is the only thing the reader can do anything about. A finding
    that claims no absence was never in scope for the guard."""
    kept, held = COMPOSE.withhold_absence_claims(
        [_finding("The homepage does not return HTTP 200", mechanism="A")],
        _snapshot(readable=0, blocked=60))
    assert len(kept) == 1 and held == []

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
    ("The site never states its pricing", True),
    ("3 pages have no H1", True),
    ("Organization markup is missing sameAs", True),
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


# The population a finding is sized against is the population the check looked
# at, which is not always the crawl.

SNAPSHOT_WITH_TYPES = {
    "origin": "https://x.test", "site": "x.test",
    "pages": (
        [{"url": "https://x.test/a{}".format(i), "status": 200,
          "page_type": "article", "text": "t"} for i in range(7)]
        + [{"url": "https://x.test/p{}".format(i), "status": 200,
            "page_type": "product", "text": "t"} for i in range(53)]),
}


def test_a_finding_about_article_pages_is_sized_against_article_pages():
    """"7 of the 60 pages" reads as a 12% problem when it is a 100% one, and an
    owner triaging by that number does the wrong thing."""
    finding = {"evidence": "Article pages with no Article JSON-LD: a, b, c.",
               "affected_pages": ["https://x.test/a{}".format(i) for i in range(5)],
               "affected_page_count": 7}
    out = COMPOSE.state_the_denominator(finding, 60, SNAPSHOT_WITH_TYPES)
    assert "7 of the 7 article pages" in out


def test_a_finding_spanning_page_types_is_still_sized_against_the_crawl():
    finding = {"evidence": "Pages with no H1: a, b.",
               "affected_pages": ["https://x.test/a0", "https://x.test/p0"],
               "affected_page_count": 9}
    out = COMPOSE.state_the_denominator(finding, 60, SNAPSHOT_WITH_TYPES)
    assert "9 of the 60 pages this crawl read" in out
    # And a finding whose evidence states no scale at all still gets one.
    bare = {"evidence": "The footer address differs from the contact page.",
            "affected_pages": ["https://x.test/a0", "https://x.test/p0"],
            "affected_page_count": 4}
    assert "4 of the 60" in COMPOSE.state_the_denominator(bare, 60, SNAPSHOT_WITH_TYPES)


def test_a_finding_that_already_names_its_population_gets_no_second_one():
    """The appended sentence read "Seen on 2 of the 60 pages this crawl read"
    one clause after the evidence said "Checked 46 content page(s). None
    declares..." - contradicting it inside the same sentence."""
    finding = {"evidence": "Checked 46 content page(s) including /a, /b. None declares an "
                           "Organization-level JSON-LD type.",
               "affected_pages": ["https://x.test/a0", "https://x.test/a1"],
               "affected_page_count": 2}
    out = COMPOSE.state_the_denominator(finding, 60, SNAPSHOT_WITH_TYPES)
    assert "Seen on" not in out
    assert out == finding["evidence"]


@pytest.mark.parametrize("evidence,pages,count", [
    ("Checked 12 product pages; none carries a price.", ["https://x.test/a0"], 1),
    ("0 of 12 product pages contain schema.org markup.", ["https://x.test/a0"], 1),
    # A share only sizes a finding when there are enough pages under it for the
    # reader to turn it back into a fraction, so this one spans both page types
    # and is sized against the whole 60-page crawl. The small-population case
    # is the test below.
    ("44% of pages have no H1.", ["https://x.test/a0", "https://x.test/p0"], 26),
])
def test_every_way_a_finding_can_state_its_own_scope_is_recognised(evidence, pages, count):
    finding = {"evidence": evidence, "affected_pages": pages,
               "affected_page_count": count}
    assert COMPOSE.state_the_denominator(finding, 60, SNAPSHOT_WITH_TYPES) == evidence


def test_a_share_over_a_handful_of_pages_is_sized_anyway():
    """"33.3% of crawled pages do not return HTTP 200" on a three-page crawl is
    one artefact wearing a percentage, and the percent sign was all it took to
    suppress the sentence that would have shown the reader so.

    Two pages of the seven article pages is 29%, and the printed share does not
    match, so the share stands as the check wrote it and the count goes beside
    it - two numbers a reader can compare beats one number this file guessed
    at.
    """
    finding = {"evidence": "44% of pages have no H1.",
               "affected_pages": ["https://x.test/a0", "https://x.test/a1"],
               "affected_page_count": 2}
    out = COMPOSE.state_the_denominator(finding, 60, SNAPSHOT_WITH_TYPES)
    assert out.startswith("44% of pages have no H1.")
    assert "2 of the 7 article pages" in out
    # And where the arithmetic does check out, the share is replaced outright
    # rather than printed next to a fraction saying the same thing.
    exact = {"evidence": "29% of pages have no H1.",
             "affected_pages": ["https://x.test/a0", "https://x.test/a1"],
             "affected_page_count": 2}
    assert COMPOSE.state_the_denominator(exact, 60, SNAPSHOT_WITH_TYPES) == \
        "2 of the 7 pages have no H1."


def test_a_scale_written_in_ordinary_english_is_not_restated():
    """"Both forms were found on 54 of the 60 pages crawled. Seen on 54 of the
    60 pages this crawl read." The finding had already said it; `the` between
    the numbers was all it took for the rule not to notice."""
    finding = {"evidence": "Both forms were found on 54 of the 60 pages crawled.",
               "affected_pages": ["https://x.test/a"], "affected_page_count": 54}
    assert COMPOSE.state_the_denominator(finding, 60) == finding["evidence"]


# An absence is not something that was "seen". "No page states X" is attached
# to whichever handful of pages the check hung it on, and that number means
# nothing; what means something is how much was looked at.

def _plain_pages(count=60):
    return {"pages": [{"url": "https://example.test/{}".format(i), "status": 200,
                       "page_type": "other"} for i in range(count)]}


def test_a_site_wide_absence_is_sized_by_what_was_examined():
    """"No page states X" is attached to whichever handful of pages the check
    hung it on, and that number means nothing. What means something is how much
    was looked at."""
    finding = {"evidence": "No page states in one sentence what the brand is.",
               "affected_page_count": 3, "affected_pages": ["https://example.test/1"],
               "root_cause": "no-entity-definition", "mechanism": "B"}
    sized = COMPOSE.state_the_denominator(finding, 60, _plain_pages())
    assert "Checked all 60" in sized
    assert "Seen on" not in sized


def test_a_per_page_absence_keeps_its_own_count():
    """"These 3 pages have no H1" really is about three pages."""
    finding = {"evidence": "3 pages carry no top-level heading.",
               "affected_page_count": 3, "affected_pages": ["https://example.test/1"],
               "root_cause": "heading-structure", "mechanism": "B"}
    sized = COMPOSE.state_the_denominator(finding, 60, _plain_pages())
    assert "Checked 3 of the 60" in sized
    assert "Seen on" not in sized


def test_a_finding_that_reports_an_observation_still_says_it_was_seen():
    finding = {"evidence": "3 internal link targets return an error.",
               "affected_page_count": 3, "affected_pages": ["https://example.test/1"],
               "root_cause": "broken-links", "mechanism": "G"}
    assert "Seen on 3 of the 60" in COMPOSE.state_the_denominator(
        finding, 60, _plain_pages())


# A site-wide sentence says how much of the site the crawl actually read.

PARTIAL_SITEMAPS = [
    {"url": "https://example.com/sitemap-index.xml", "status": 200, "urls": [],
     "url_count": 0, "lastmod_count": 0, "is_index": True,
     "child_sitemaps": ["https://example.com/s-{}.xml".format(n) for n in range(12)]},
]
PARTIAL_SITEMAPS += [{"url": "https://example.com/s-{}.xml".format(n), "status": 200,
                      "urls": [], "url_count": 100, "lastmod_count": 0,
                      "child_sitemaps": []} for n in range(3)]


def test_a_site_wide_claim_states_how_much_of_the_site_was_read():
    """"No Organization markup anywhere on the site", from a 60-page crawl of
    a firm whose sitemap lists thousands of pages."""
    snapshot = {"sitemaps": PARTIAL_SITEMAPS, "pages": []}
    out = COMPOSE.state_the_coverage(
        "No Organization or LocalBusiness markup appears anywhere on the site.",
        snapshot, 60)
    assert "this crawl read 60" in out
    assert "not every page of the site" in out

    # A crawl that did cover the site says nothing extra.
    small = [{"url": "https://example.com/sitemap.xml", "status": 200, "urls": [],
              "url_count": 62, "lastmod_count": 0, "child_sitemaps": []}]
    text = "No Organization markup appears anywhere on the site."
    assert COMPOSE.state_the_coverage(text, {"sitemaps": small}, 60) == text


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


def test_values_placeholders_and_vocabulary_all_survive():
    """Everything the site did show, every deliberate placeholder and every
    schema.org keyword has to come through the filter untouched, or the guard
    against invented values becomes a guard against the snippet."""
    out, invented = COMPOSE.hold_back_invented_values(
        SNIPPET, COMPOSE.observed_values(SNAPSHOT))
    assert '"name": "Coppergate Ceramics"' in out
    assert '"telephone": "01904 555812"' in out
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


ROASTER = {
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
    match as a whole token - and the price the page really shows must survive."""
    out, invented = COMPOSE.hold_back_invented_values(
        '{ "price": "1" }', COMPOSE.observed_values(ROASTER))
    assert invented and "price" in invented[0]
    assert '"price": "1"' not in out

    out, invented = COMPOSE.hold_back_invented_values(
        '{ "price": "18.00" }', COMPOSE.observed_values(ROASTER))
    assert invented == [] and '"price": "18.00"' in out


def test_a_telephone_published_only_as_a_tel_link_counts_as_observed():
    """The guard called a real, correct number a guess and replaced it with a
    placeholder - the same defect as inventing one, pointing the other way. A
    postal code the site never showed still has to be held back."""
    out, invented = COMPOSE.hold_back_invented_values(
        '{ "telephone": "+442072323010" }', COMPOSE.observed_values(ROASTER))
    assert invented == []
    assert "+442072323010" in out

    _out, invented = COMPOSE.hold_back_invented_values(
        '{ "postalCode": "99999" }', COMPOSE.observed_values(ROASTER))
    assert invented and "postalCode" in invented[0]


def test_a_form_the_crawler_read_counts_as_observed():
    """Four sites in one validation pass were told the target and query field in their
    SearchAction snippet "could not be found anywhere on this site", one
    sentence after being told both came from their own search form. The
    haystack was a hand-kept list of field names, and `forms` was not on it."""
    snapshot = {
        "origin": "https://example.test", "site": "example.test",
        "pages": [{
            "url": "https://example.test/",
            "forms": [{"action": "/suche", "query_field": "searchfield", "is_search": True}],
        }],
    }
    seen = COMPOSE.observed_values(snapshot)
    assert "/suche" in seen
    assert "searchfield" in seen


def test_the_haystack_does_not_need_editing_when_a_field_is_added():
    """The rule, rather than one more field name: anything anywhere in a page
    record is something the crawl saw."""
    snapshot = {"pages": [{"a": {"b": [{"c": "a value nobody listed"}]}}]}
    assert "a value nobody listed" in COMPOSE.observed_values(snapshot)
