"""One rule for "does this text state, in one sentence, what the brand is?".

The rule decides two things a reader sees: the `no-entity-definition` finding
in `fact-extractability-audit`, and the `description` of the paste-ready
Organization block in `structured-data-audit`. It was written twice, once in
each skill, because the two cannot import each other - and the two answers
inside one report then disagreed. An open-source project's Organization block
declared that the organisation *is* a point-release announcement lifted off its
own homepage, while three findings above, the other skill printed the identity
sentence from the about page.

It now lives once, in `audit_common.defining_sentence`, which both skills call.
These tests hold that in place: that the second copy is gone, that both callers
route through the shared function, and that the shared function still decides
each sentence shape and each rejection the way the stricter of the two copies
did.

Every name here is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

import audit_common as AC  # noqa: E402


def _module(skill):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("onerule_" + skill.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module("structured-data-audit")
FACTS = _module("fact-extractability-audit")

BRAND = "Kestrel"
BRAND_RECORD = {"name": "Kestrel"}


def _source(skill):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# --------------------------------------------------------------------------
# There is one implementation
# --------------------------------------------------------------------------

# Fragments that only a copy of the rule would carry: the copular verb list,
# the first-person shape, the two-bare-numbers table-row test, and the frozen
# set of nouns a predicate is judged against. Importing a name is not carrying
# a copy, so the last one looks for the definition and not the mention.
RULE_FRAGMENTS = (
    r"is|are|was|remains",
    r"\b(?:I|we)\s+(?:am|are)",
    r"\b\d+\s+\d+\b",
    "CONTENTLESS_NOUNS = frozenset",
)


@pytest.mark.parametrize("skill", ["fact-extractability-audit", "structured-data-audit"])
def test_no_skill_carries_its_own_copy_of_the_rule(skill):
    """A copy in a skill is a copy that drifts, and this one did."""
    source = _source(skill).lower()
    present = [fragment for fragment in RULE_FRAGMENTS if fragment.lower() in source]
    assert not present, "{} still spells out the definition rule: {}".format(skill, present)


def test_the_shared_library_is_where_the_rule_lives():
    with open(os.path.join(SCRIPTS, "audit_common.py"), encoding="utf-8") as handle:
        source = handle.read()
    for fragment in RULE_FRAGMENTS:
        assert fragment.lower() in source.lower(), fragment
    assert callable(AC.defining_sentence)


def test_both_callers_route_through_the_shared_function():
    """Both skills hold the same function object, not two that agree today -
    which is what the two copies did, until one of them drifted.

    This is also why nothing below runs a case through each caller in turn. One
    object cannot disagree with itself, so a per-caller duplicate of every
    sentence in this file would assert nothing that this line does not.
    """
    assert FACTS.defining_sentence is AC.defining_sentence
    assert SD.defining_sentence is AC.defining_sentence
    assert FACTS.brand_forms is AC.brand_forms
    assert SD.brand_forms is AC.brand_forms


# --------------------------------------------------------------------------
# The four shapes a definition comes in
# --------------------------------------------------------------------------

# One case per branch of the rule, not one per sentence anybody might write.
# Where two sentences reach the same branch through different words - "is"
# against "are", "each" against "the", "provides" against "publishes" - only
# the clearer one is here. The branch each case reaches is named beside it.
ACCEPTED = [
    # The copular template, and the parts of it that can be absent.
    ("copula",
     "Kestrel is a payments ledger for small manufacturers."),
    # `(?:an?|the)?` not taken: a predicate with no article in front of it.
    ("copula-no-article",
     "Kestrel is independent software for planning cargo across the region."),
    # The predicate floor is 12 characters, and this leaves 16. The floor
    # replaced a 20-character one that threw this sentence out.
    ("copula-short-predicate",
     "Kestrel is a botanic garden."),
    # `_starts_a_sentence` allowing exactly one leading determiner.
    ("copula-behind-one-determiner",
     "The Kestrel is a retreat for curious programmers in the mountains."),
    # The bracketed-alias group between the name and the verb.
    ("copula-with-bracketed-alias",
     "Kestrel (KST) is a free, non-profit register of charitable trusts."),
    # Appositive, first alternative: a dash, colon or pipe separator.
    ("appositive-em-dash",
     "Kestrel — the payments ledger built for small manufacturers."),
    # Appositive, second alternative: a hyphen with spaces around it. Paired
    # with `hyphen-inside-a-name` below, which is the same hyphen unspaced.
    ("appositive-spaced-hyphen",
     "Kestrel - the payments ledger built for small manufacturers."),
    # Appositive, third alternative: a comma and a required article.
    ("appositive-comma-and-article",
     "Kestrel, a payments ledger for small manufacturers, opened in the spring."),
    # The title-list fallback in `_reads_as_a_sentence`: the one accepted shape
    # with no function word in it for the glue vocabulary to find.
    ("title-line-no-function-words",
     "Kestrel — Professor, Statistics, Example University."),
    # The defining-verb template.
    ("defining-verb",
     "Kestrel provides up-to-date support tables for front-end web technologies."),
    # The first-person template, which never names the brand at all.
    ("first-person",
     "We are a bakery and coffee roastery on the market square."),
    # `finditer` carrying on past the first sentence, and `_DEFINITION_HEAD_RE`
    # finding the start of the second one.
    ("second-sentence-of-the-text",
     "Prices start at forty a month. Kestrel is a payments ledger for small "
     "manufacturers."),
    # `_DEFINITION_TAIL_RE`'s end-of-text alternative: the opening window is cut
    # at a word count, and a definition sitting on the cut is still one.
    ("running-to-the-end-of-the-text",
     "Kestrel is a payments ledger for small manufacturers"),
]

REJECTED = [
    # `_starts_a_sentence`, more than one word in front of the name. The bug
    # this test exists for: the check quoted "Kestrel is monthly per
    # organisation" as the site's definition of itself.
    ("brand-not-the-subject",
     "Billing for Kestrel is monthly per organisation."),
    # `_starts_a_sentence`, the separate digit test.
    ("brand-behind-a-number",
     "In 2019 Kestrel is a payments ledger for small manufacturers."),
    # No shape matches at all: the predicate is under the 12-character floor,
    # so the copular pattern never fires. "Kestrel is a company." and "Kestrel
    # is a bar." are this same branch.
    ("predicate-below-the-length-floor",
     "Kestrel is."),
    # `says_something`. The copular shape does match here - the predicate is
    # well over the floor - and the only thing that rejects it is that every
    # content word in it names no category. This is the branch a character
    # floor could never reach, and the reason there is a vocabulary.
    ("predicate-of-empty-nouns",
     "Kestrel is a leading global platform trusted by innovative teams."),
    # `_reads_as_a_sentence`: the appositive shape matches, and two bare
    # numbers side by side say this is a row of a compatibility matrix.
    ("table-row-through-the-colon-shape",
     "Kestrel: Constructable Stylesheet 10 74"),
    # The first-person template requires an article after the verb, or it
    # would match any sentence a site writes about itself.
    ("first-person-no-article",
     "We are open on Sundays."),
    # `_DEFINITION_OPENING_QUOTE_RE`: a quoted customer is talking about the
    # customer. Nothing else in the rule looks at what precedes a match.
    ("first-person-inside-a-quotation",
     "One customer wrote: \"We are a family of four and this is the third one "
     "we have bought.\""),
    # The spaced-hyphen alternative demands the spaces, so a hyphenated name
    # is not read as a tagline attached to its first word.
    ("hyphen-inside-a-name",
     "Kestrel-and-Ordwell sells clothing"),
    # Nothing about the brand anywhere, and no first person either.
    ("brand-absent",
     "The shop opens at nine and closes when the bread runs out."),
]


@pytest.mark.parametrize("case,text", ACCEPTED, ids=[c for c, _ in ACCEPTED])
def test_a_sentence_that_states_an_identity_is_found(case, text):
    found = AC.defining_sentence(text, BRAND, BRAND_RECORD)
    assert found, "no definition found in: {}".format(text)
    assert found in text or text.startswith(found)


@pytest.mark.parametrize("case,text", REJECTED, ids=[c for c, _ in REJECTED])
def test_a_sentence_that_states_no_identity_is_not_found(case, text):
    assert AC.defining_sentence(text, BRAND, BRAND_RECORD) == ""


# --------------------------------------------------------------------------
# Which name the site is allowed to use for itself
# --------------------------------------------------------------------------

def test_a_short_form_of_a_long_formal_name_still_counts():
    """"Department of Mathematics, University of Example" never appears verbatim
    in the sentence a department writes about itself, and requiring the whole
    string made the check unsatisfiable for most institutions."""
    brand = {"name": "Department of Mathematics, University of Example",
             "domain_token": "department"}
    found = AC.defining_sentence(
        "The Department is one of the largest teaching departments in the country.",
        brand["name"], brand)
    assert found.startswith("Department is one of the largest")


def test_the_longest_matching_form_is_the_one_reported():
    brand = {"name": "Kestrel Holdings Limited", "alternate_names": ["Kestrel"],
             "domain_token": "kestrel"}
    found = AC.defining_sentence(
        "Kestrel Holdings Limited is a payments ledger for small manufacturers.",
        brand["name"], brand)
    assert found.startswith("Kestrel Holdings Limited is")


def test_an_all_digit_domain_token_is_not_a_name():
    """`crawl.py` falls back to the domain token as a brand name, and an IP
    address arrives through the same field."""
    assert "12345" not in AC.brand_forms("Kestrel", {"domain_token": "12345"})


def test_no_brand_name_finds_no_brand_sentence():
    """With nothing to look for, the brand shapes cannot match; the first
    person still can, because it never needed the name."""
    assert AC.defining_sentence(
        "Kestrel is a payments ledger for small manufacturers.", "", {}) == ""
    assert AC.defining_sentence(
        "We are a bakery and coffee roastery on the market square.", "", {})


# --------------------------------------------------------------------------
# What a caller may add on top: a filter, never a shape
# --------------------------------------------------------------------------

def test_the_accept_callback_skips_a_match_and_keeps_searching():
    """One unusable sentence near the top of a page must not hide the usable
    one below it, which is why a rejected match does not end the search."""
    text = ("Kestrel is a major new release of the ledger. "
            "Kestrel is a payments ledger for small manufacturers.")
    seen = []

    def accept(sentence):
        seen.append(sentence)
        return "major new release" not in sentence

    found = AC.defining_sentence(text, BRAND, BRAND_RECORD, accept=accept)
    assert found == "Kestrel is a payments ledger for small manufacturers"
    assert len(seen) == 2


def test_without_a_callback_the_first_match_wins():
    text = ("Kestrel is a major new release of the ledger. "
            "Kestrel is a payments ledger for small manufacturers.")
    assert AC.defining_sentence(text, BRAND, BRAND_RECORD) == \
        "Kestrel is a major new release of the ledger"


NEWS = [
    ("now-available",
     "Kestrel is a payments ledger and it is now available for download."),
    ("iso-date",
     "Kestrel is a payments ledger for small manufacturers from 2024-05-01."),
    ("month-and-day",
     "Kestrel is a payments ledger for small manufacturers, launching Oct 14."),
    ("day-and-month",
     "Kestrel is a payments ledger for small manufacturers, opening 14 October."),
    ("latest-release",
     "Kestrel is a payments ledger and the latest release adds cargo planning."),
]


@pytest.mark.parametrize("case,text", NEWS, ids=[c for c, _ in NEWS])
def test_the_snippet_skips_a_definition_that_is_really_news(case, text):
    """The shared rule accepts every one of these - the brand opens the
    sentence, the predicate names a category, it reads as prose. The snippet's
    own filter rejects them, because a description goes into a site-wide
    template and a release note is not an identity."""
    assert AC.defining_sentence(text, BRAND, BRAND_RECORD)
    lasting = lambda sentence: bool(SD._outlives_the_news(sentence, BRAND))  # noqa: E731
    assert AC.defining_sentence(text, BRAND, BRAND_RECORD, accept=lasting) == ""


def test_a_founding_year_is_not_news():
    """The test is whether the sentence is pinned to a moment, not whether it
    holds a number: a founding year is as true next year as this one, and it is
    the most identity-bearing thing a long-lived business writes."""
    text = "Kestrel is an independent record office for the county since 1934."
    lasting = lambda sentence: bool(SD._outlives_the_news(sentence, BRAND))  # noqa: E731
    assert "since 1934" in AC.defining_sentence(text, BRAND, BRAND_RECORD, accept=lasting)


def test_a_version_number_between_the_name_and_the_verb_is_news_too():
    """"Kestrel 8.1 is a ..." never reaches the callback - a number between the
    name and the copula breaks the shape - so the news filter carries its own
    test for it, and that is what reads a meta description."""
    assert AC.defining_sentence(
        "Kestrel 8.1 is a payments ledger for small manufacturers.",
        BRAND, BRAND_RECORD) == ""
    for text in ("Kestrel 8.1 is a payments ledger for small manufacturers.",
                 "Kestrel v2.0 is a payments ledger for small manufacturers."):
        assert SD._outlives_the_news(text, BRAND) == ""


def test_the_finding_never_applies_the_news_filter():
    """The two callers differ in exactly one place, and only in this direction.
    A sentence announcing a download still states what the brand is, so the
    site has not earned `no-entity-definition` for it; it is only unfit to be
    pasted into a site-wide template."""
    text = "Kestrel is a payments ledger and it is now available for download."
    assert FACTS._find_definition(text, BRAND, dict(BRAND_RECORD))
    assert SD._brand_definition(
        [{"url": "https://x.test/", "page_type": "home", "paragraphs": [text]}],
        dict(BRAND_RECORD)) == ""


# --------------------------------------------------------------------------
# The vocabulary is English, and the answer for other prose is "no sentence"
# --------------------------------------------------------------------------

def test_prose_in_another_language_yields_no_sentence():
    """Four English sentence shapes, English function words and English empty
    nouns. On prose in another language the rule finds nothing rather than
    measuring it, which is the safe direction for a check whose output is an
    accusation - and it is why `fact-extractability-audit` runs this only where
    `prose_checks_apply`. The snippet does not gate and does not need to: an
    empty answer leaves the site's own declared `description` in place.
    """
    assert AC.defining_sentence(
        "Kestrel ist eine Buchhaltung für kleine Hersteller im Norden.",
        BRAND, BRAND_RECORD) == ""
    assert AC.defining_sentence(
        "ケストレルは中小製造業向"
        "けの会計ソフトです。",
        "ケストレル", {"name": "ケストレル"}) == ""
