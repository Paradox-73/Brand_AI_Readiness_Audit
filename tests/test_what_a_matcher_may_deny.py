# -*- coding: utf-8 -*-
"""A matcher may report what it found. It may not report that a thing is absent.

Two independent reviews of two sets of sites raised four findings, and all four
are one move: a finite list did not match, and the report said the site does
not have the thing.

  "no founding year appears in the      `/our-story` states "<Brand>(R) was
   page text or in the page markup"      founded in 1998 in <city>". The crawl
                                         read the page and the report's own
                                         "what an assistant would quote" table
                                         prints that sentence.
  "no postal address appears in the     the contact page prints an Indonesian
   page text or in the page markup"      RT/RW-and-plus-code address. The
                                         street matcher wants a house number.
  "No sentence of the form '<the        the about page reads "<Legal name> is a
   search-engine copy> is a ...'         leading manufacturer and supplier of
   appears"                              ...". The legal name sits in the
                                         site's own <title> and was never
                                         tried as a subject.
  "Pages carrying no date a reader      the page carries 54,990 characters
   or a machine could use"               including four Thai Buddhist-era
                                         dates.

Widening the lists is not the fix and this file contains no widening: two of
these four lists were already widened in an earlier fix (Buddhist-era dates,
non-English founding words) and both defects came back in the next language.
What is pinned here is the rule that survives an incomplete list:

  a check may deny only what a reading that cannot miss came back empty on;
  where a finite list decided, the report says what this audit could not find.

Part 1 holds the rule for every check in the marketplace, including ones
written later. Parts 2 to 4 hold the three claims `fact-extractability-audit`
raises, in both directions - the false denial must go, and the true finding
must stay, because a rule that only ever silences is a rule that trades false
positives for misses. Part 5 holds the two loose readings the denials now have
to fail against.

Every site, name and address here is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    ABSENCE_CAUSES, ABSENCE_DECIDED_BY_A_VOCABULARY, DENIAL_RESTS_ON_A_FINITE_LIST,
    make_finding, reads_like_an_address, ROOT_CAUSES, SkillResult,
    some_subject_is_defined,
)


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACTS = _module("fact-extractability-audit", "facts_for_denial_tests")

HOST = "https://an-invented-host.test"


def _page(url, **fields):
    page = {
        "url": url,
        "page_type": "other",
        "status": 200,
        "body_text": "",
        "headings": {},
        "headings_in_markup": {"h1": ["a heading"]},
        "jsonld": [],
        "jsonld_types": [],
        "links": {},
        "sections": [],
        "prices": [],
        "contact_facts": {},
        "readability": {},
        "word_count": 0,
        "meta_description": "",
    }
    page.update(fields)
    return page


def _titles(result):
    return {finding["title"] for finding in result.findings}


def _reasons(result, check):
    return " ".join(item["reason"] for item in result.not_applicable
                    if item["check"] == check)


_A_FINDING = dict(
    id_hint="x", title="t", severity="high", confidence="high",
    evidence="Nothing was found.", mechanism="C", summary="s",
    how_to_fix=["step"], effort="low", rationale="r", owner="developer",
)


# ==========================================================================
# Part 1 - the rule, held for every check rather than for these three
# ==========================================================================

def test_the_vocabulary_set_is_part_of_the_absence_vocabulary():
    """If it held a cause that asserts nothing the rule would be decoration,
    and if it held every absence cause it would be the absence gate again
    under a second name."""
    assert ABSENCE_DECIDED_BY_A_VOCABULARY
    assert not ABSENCE_DECIDED_BY_A_VOCABULARY - ABSENCE_CAUSES
    assert ABSENCE_CAUSES - ABSENCE_DECIDED_BY_A_VOCABULARY
    assert not ABSENCE_DECIDED_BY_A_VOCABULARY - ROOT_CAUSES


@pytest.mark.parametrize("cause", sorted(ABSENCE_DECIDED_BY_A_VOCABULARY))
def test_a_denial_a_list_decided_says_so_in_the_evidence(cause):
    """The sentence goes in `evidence`, which is the field both renderers print
    directly under the claim. A reader who has read the claim as a fact has
    already taken it as one, so a caveat anywhere else is too late."""
    finding = make_finding(
        root_cause=cause,
        checked=("one finite list", "a second finite list"),
        **_A_FINDING)
    assert DENIAL_RESTS_ON_A_FINITE_LIST.strip() in finding["evidence"]
    assert finding["denial_rests_on"] == "a finite list"


@pytest.mark.parametrize("cause", sorted(ABSENCE_DECIDED_BY_A_VOCABULARY))
def test_two_finite_lists_are_still_finite_lists(cause):
    """`make_finding` already caps a one-source denial at medium. Two sources
    bought `high` - and the real address was missed by the text scan and the
    markup scan at once, which is two empty readings and one incomplete idea of
    what an address looks like."""
    finding = make_finding(
        root_cause=cause,
        checked=("one finite list", "a second finite list"),
        **_A_FINDING)
    assert finding["confidence"] == "medium"


def test_a_denial_nothing_can_miss_is_left_alone():
    """The rule is not "hedge everything". A page has one `<html lang>`
    attribute and one `<h1>`; a check that read the delivered markup has looked
    in the only place either could be, and a caveat there would be noise that
    teaches a reader to skip the caveats that mean something."""
    finding = make_finding(
        root_cause="missing-lang",
        checked=("the `lang` attribute of every crawled page",),
        **_A_FINDING)
    assert DENIAL_RESTS_ON_A_FINITE_LIST.strip() not in finding["evidence"]
    assert "denial_rests_on" not in finding
    assert "missing-lang" not in ABSENCE_DECIDED_BY_A_VOCABULARY


def test_the_sentence_states_no_count_of_its_own():
    """The report composer appends a denominator to any finding that does not
    size itself, and it decides whether one is needed by looking for "N of M"
    in the evidence. A caveat carrying digits would either suppress that
    sentence or be counted as one."""
    assert not any(ch.isdigit() for ch in DENIAL_RESTS_ON_A_FINITE_LIST)


# The verbs that say a thing is not on the page, as against the verbs that say
# this audit did not read one. Both reviews quoted the first kind back.
_ASSERTS_THE_SITE_LACKS_IT = (
    "appears in the page", "appears anywhere", "there is no", "the site has no",
    "the page has no", "carries no",
)


def test_a_denial_a_list_decided_is_worded_as_a_reading():
    """"No founding year appears in the page text" is a statement about the
    site. "No sentence naming a founding year was recognised in the page text"
    is a statement about the reading, and the reading is the only thing a
    pattern that did not fire has.

    The distinction is not cosmetic and it was measured: all three of these
    findings were already published in the tier a reader is told to verify,
    and an independent review counted them as false positives anyway, because
    what a reader acts on is the sentence."""
    home = _page(HOST + "/", page_type="home",
                 body_text="Nightjar Press. Welcome to the shop.",
                 contact_facts={"emails": ["hello@an-invented-host.test"],
                                "chrome_emails": ["hello@an-invented-host.test"]})
    about = _page(HOST + "/about", page_type="about",
                  body_text="About us. We care deeply about quality and always have.")
    pages = [home, about]

    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, "Nightjar Press")
    FACTS._check_entity_definition(result, {"pages": pages}, pages, "Nightjar Press")

    denials = [f for f in result.findings
               if f["root_cause"] in ABSENCE_DECIDED_BY_A_VOCABULARY]
    assert len(denials) >= 3, _titles(result)
    for finding in denials:
        # The report composer appends its own coverage sentences afterwards, so
        # only the check's own words are read here.
        evidence = finding["evidence"].replace(DENIAL_RESTS_ON_A_FINITE_LIST, "").lower()
        for phrase in _ASSERTS_THE_SITE_LACKS_IT:
            assert phrase not in evidence, "{}: {!r}".format(finding["title"], phrase)
        assert "recognised" in evidence, finding["evidence"]


def test_the_rule_reaches_the_findings_the_skill_actually_emits():
    """Built by running the check rather than by calling `make_finding`, so a
    check that stopped going through the shared constructor would fail here."""
    home = _page(HOST + "/", page_type="home",
                 body_text="Nightjar Press. Welcome to the shop.",
                 contact_facts={"emails": ["hello@an-invented-host.test"],
                                "chrome_emails": ["hello@an-invented-host.test"]})
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": [home]}, [home], "Nightjar Press")
    denials = [f for f in result.findings
               if f["root_cause"] in ABSENCE_DECIDED_BY_A_VOCABULARY]
    assert denials, _titles(result)
    for finding in denials:
        assert DENIAL_RESTS_ON_A_FINITE_LIST.strip() in finding["evidence"], finding["title"]
        assert finding["denial_rests_on"] == "a finite list"


# ==========================================================================
# Part 2 - a founding year the audit read as nobody's
# ==========================================================================

# The whole sentence is on the page. What failed was deciding whose it is:
# `speaks_about_itself` compares against the name `resolve_brand` picked, and
# on this site that is the shop's trading style rather than the name the
# story is written under.
OUR_STORY = ("Our story. Wrenfoot Compass(R) was founded in 1998 in Selatan, and has "
             "made hand-stitched bags in the same workshop ever since.")

NO_FOUNDING_YEAR = ("Our story. We have been making hand-stitched bags in the same "
                    "workshop for as long as anyone here can remember.")


def _story_site(story_text):
    home = _page(HOST + "/", page_type="home",
                 body_text="Nightjar Bags. Hand-stitched bags.",
                 contact_facts={"emails": ["hello@an-invented-host.test"],
                                "chrome_emails": ["hello@an-invented-host.test"]})
    story = _page(HOST + "/our-story", page_type="about", body_text=story_text)
    return [home, story]


def test_a_founding_year_the_audit_cannot_attribute_stops_the_denial():
    """The report said "no founding year appears in the page text or in the
    page markup" while its own citation table quoted the sentence."""
    pages = _story_site(OUR_STORY)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, "Nightjar Bags")
    assert ("The site never states its founding facts in plain text"
            not in _titles(result)), _titles(result)
    # And it is not silence: the appendix says what was seen and what could not
    # be concluded from it.
    reason = _reasons(result, "core-fact-founding-facts")
    assert "1998" in reason, reason
    assert "would be false" in reason, reason


def test_the_year_is_not_recorded_as_this_site_s_founding_year():
    """The loose reading refuses a denial. It may not promote a sentence it
    cannot attribute into a fact about the brand - that is the other error, and
    it is the one that puts a stranger's date in a report."""
    pages = _story_site(OUR_STORY)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, "Nightjar Bags")
    assert "founding facts" not in (result.signals.get("core_facts_found") or [])
    assert "founding facts" in (result.signals.get("core_facts_not_checked") or [])


def test_a_site_that_states_no_founding_year_is_still_told_so():
    """The direction that pays for the widening. Silence is a miss, and a miss
    counts against the report exactly as a false positive does."""
    pages = _story_site(NO_FOUNDING_YEAR)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, "Nightjar Bags")
    assert ("The site never states its founding facts in plain text"
            in _titles(result)), _titles(result)


# ==========================================================================
# Part 3 - an address written as a hierarchy rather than as a street
# ==========================================================================

# An invented address in the shape a very large number of Indonesian
# businesses use: a plus code, an administrative sub-unit, two districts, a
# city and a province. No house number, no postal code in the line.
HIERARCHY_ADDRESS = ("Hubungi kami. QR7X+M42, RT.04/RW.09, West Selatan, Selatan, "
                     "North Bandaran City, Bandaran.")

NO_ADDRESS = "Hubungi kami. Kirim pesan lewat surel dan kami akan membalas."


def _contact_site(contact_text):
    home = _page(HOST + "/", page_type="home",
                 body_text="Nightjar Bags. Hand-stitched bags since the workshop opened.",
                 contact_facts={"emails": ["hello@an-invented-host.test"],
                                "chrome_emails": ["hello@an-invented-host.test"]})
    contact = _page(HOST + "/contact", page_type="contact", body_text=contact_text)
    return [home, contact]


def test_an_address_shape_the_street_matcher_cannot_read_stops_the_denial():
    """The report said "no postal address appears in the page text or in the
    page markup" with that line in its own citation table."""
    pages = _contact_site(HIERARCHY_ADDRESS)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, "Nightjar Bags")
    assert ("The site never states its location or service area in plain text"
            not in _titles(result)), _titles(result)
    reason = _reasons(result, "core-fact-location-or-service-area")
    assert "RT.04/RW.09" in reason, reason
    assert "would be false" in reason, reason


def test_a_site_that_publishes_no_address_is_still_told_so():
    pages = _contact_site(NO_ADDRESS)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, "Nightjar Bags")
    assert ("The site never states its location or service area in plain text"
            in _titles(result)), _titles(result)


# ==========================================================================
# Part 4 - a definition written under a name the audit did not resolve
# ==========================================================================

# The site's `<title>` is "Best hand-stitched bags online | Wrenfoot
# Enterprise", `resolve_brand` takes the copy at the front of it, and the about
# page defines the company under the legal name at the back.
DEFINED_UNDER_ANOTHER_NAME = (
    "About us. Wrenfoot Enterprise is a maker and supplier of hand-stitched "
    "canvas bags for market traders.")

DEFINES_NOTHING = ("About us. We care deeply about quality and we have done for "
                   "as long as we can remember. Our promise to you is simple.")


def _identity_site(about_text):
    # The title carries the legal name, which is the whole shape of this
    # failure: `resolve_brand` takes the copy at the front of it and the about
    # page defines the company under the name at the back. It also carries the
    # guard - a subject nowhere in the site's own naming is a product being
    # described, not the site describing itself.
    home = _page(HOST + "/", page_type="home",
                 title="Best hand-stitched bags online | Wrenfoot Enterprise",
                 body_text="Best hand-stitched bags online. Shop the new season.")
    about = _page(HOST + "/about", page_type="about", body_text=about_text)
    return [home, about]


def test_a_definition_under_an_unresolved_name_stops_the_denial():
    """The report printed "No sentence of the form '<the copy> is a ...' ...
    appears" over an about page that states exactly that sentence under the
    company's legal name."""
    pages = _identity_site(DEFINED_UNDER_ANOTHER_NAME)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_entity_definition(result, {"pages": pages}, pages,
                                   "Best hand-stitched bags online")
    assert not result.findings, _titles(result)
    reason = _reasons(result, "entity-definition")
    assert "Wrenfoot Enterprise" in reason, reason
    assert "would be false" in reason, reason


def test_a_site_that_defines_nothing_is_still_told_so():
    pages = _identity_site(DEFINES_NOTHING)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_entity_definition(result, {"pages": pages}, pages,
                                   "Best hand-stitched bags online")
    assert [f["root_cause"] for f in result.findings] == ["no-entity-definition"], \
        _titles(result)


# ==========================================================================
# Part 5 - the two loose readings, in both directions
#
# Each is the strict matcher with one identification removed, and each is meant
# to be wrong in the direction that costs a sentence rather than in the
# direction that costs a false statement about somebody's business. Both halves
# are pinned, because a loose reading that stops being loose brings the false
# denial back and one that stops being a reading deletes the finding.
# ==========================================================================

@pytest.mark.parametrize("text", [
    # A hierarchy with a plus code, and one with a block and a lot number.
    u"QR7X+M42, RT.04/RW.09, West Selatan, Selatan, North Bandaran City, Bandaran",
    u"Blk 12 Lot 4, Barangay San Wren, Nightjar City, Metro Bandaran, Selatan",
    # And the shape the strict matcher already reads, which must not stop
    # being recognised here just because something else covers it.
    u"Copyright 2026 Nightjar Ltd, 12 Harbour Lane, Milltown, EC1A 1BB, United Kingdom",
])
def test_a_place_hierarchy_reads_as_an_address(text):
    assert reads_like_an_address(text), text


@pytest.mark.parametrize("text", [
    # Every one of these matched before the discriminator beside it was added.
    u"Open Monday, Tuesday, Wednesday, Thursday, 9 to 5",
    u"Sizes: Small, Medium, Large, Extra Large, 42 EU",
    u"Terms, Privacy, Cookie Policy, Contact Us, 2026",
    u"Home, About Us, Our Services, Contact, 020 7123 4567",
    u"We ship worldwide, we answer fast, and we care about every order we send out.",
    u"Fixed in this release. 11418 CVE-2026-1816 59733 11907 CVE-2026-2044 60021",
])
def test_a_comma_list_that_is_not_an_address_is_not_read_as_one(text):
    assert reads_like_an_address(text) == "", text


@pytest.mark.parametrize("text,subject", [
    (u"Wrenfoot Enterprise is a maker and supplier of hand-stitched canvas bags.",
     u"Wrenfoot Enterprise"),
    (u"Arrowfoot Design is a full-service design studio working on residential projects.",
     u"Arrowfoot Design"),
    (u"The Museum of Modern Craft is a museum of contemporary craft in the old harbour.",
     u"The Museum of Modern Craft"),
    (u"Harbour Weavers, a co-operative of twelve makers, opened in the old sail loft.",
     u"Harbour Weavers"),
])
def test_a_named_subject_with_a_predicate_is_a_definition(text, subject):
    found = some_subject_is_defined(text)
    assert found is not None, text
    assert found[0] == subject, found


@pytest.mark.parametrize("text", [
    # A capital at the start of a sentence is not evidence of a name: every
    # sentence has one. Either of the first two would have silenced the
    # entity-definition finding on most sites in the marketplace.
    u"We are open on Sundays and closed on Mondays throughout the winter.",
    u"Delivery is a flat rate of five pounds on every order placed before noon.",
    # The dash tagline, which is evidence when the brand is the subject and is
    # every bulleted feature line on the internet when it is not.
    u"Free Delivery - on all orders over fifty pounds sent within the country.",
    # A predicate that names no category.
    u"Nightjar Bags is a company.",
])
def test_a_sentence_that_defines_nothing_is_not_read_as_a_definition(text):
    assert some_subject_is_defined(text) is None, text
