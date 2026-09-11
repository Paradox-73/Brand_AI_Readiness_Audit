# -*- coding: utf-8 -*-
"""No claim about words ships without saying what language the words were read in.

The rule: no prose-derived claim ships without the script and language it was
measured in being stated in the evidence.

The reason is in what was measured on real sites. Every wrong finding on a site that is not
in English came from a list of English words run over sentences - a founding
vocabulary of `since`, `founded`, `established` that cannot read
`Fra 07.11.2004`; a date reader anchored on `(?:19|20)\\d{2}` that cannot read a
Thai Buddhist-era year; a definition matcher looking for "X is a" in a language
that does not build sentences that way. Each result was printed as a fact about
the site, and none of them said in which language it had looked.

A reader told "no founding year appears in the page text" cannot judge that.
A reader told "no founding year appears in the page text, which was read as
`no` in the latin script with patterns written for English" can, in a second.
The claim does not have to be right for the report to be honest. It has to be
checkable.

So this file does not test one sentence. It tests a rule over the whole report,
walked across the entire root-cause vocabulary, so that a check written next
month which raises a prose claim without the statement fails the build.

Every site, name and address here is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    EVIDENCE_BASIS, EVIDENCE_BASIS_BY_FINDING, INFERRED, OBSERVED, ROOT_CAUSES,
)


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_language_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose()


# --------------------------------------------------------------------------
# Building blocks. Four sites, each one a case the rule has to answer
# differently, and none of them a real place.
# --------------------------------------------------------------------------

HOME = "https://verksted.test/"

# Norwegian, and long enough for the script detector to have letters to count.
# This is the shape of the real site whose founding date was missed: it states
# its founding date in a language the founding vocabulary was not written for.
NORWEGIAN = (
    "Verkstedet vart skipa i Bergen og reparerer gamle stolar med ny rotting. "
    "( Fra 07.11.2004) Vi tek imot arbeid fra heile fylket og sender ferdige "
    "stolar tilbake med post. Kontoret er ope kvar tysdag og torsdag."
)
ENGLISH = (
    "The workshop was set up in the old harbour and repairs cane chairs. "
    "We take work from the whole county and post the finished chairs back. "
    "The office is open on Tuesdays and Thursdays."
)
# Cyrillic with nothing declared. Cyrillic is Russian, Ukrainian, Bulgarian and
# Serbian, so the audit is entitled to the script and to no language at all.
CYRILLIC = (
    "Майстерня була заснована у старому порту і ремонтує старі стільці. "
    "Ми приймаємо роботу з усього краю і надсилаємо готові стільці поштою. "
    "Офіс відкритий щовівторка та щочетверга для всіх відвідувачів."
)
# Arabic text under a declared `lang="en"`: the declaration and the letters
# disagree, and the letters win.
ARABIC = (
    "تأسست الورشة في الميناء القديم وهي تصلح الكراسي القديمة بخيوط جديدة. "
    "نحن نستقبل العمل من جميع أنحاء المنطقة ونعيد الكراسي الجاهزة بالبريد. "
    "المكتب مفتوح كل يوم ثلاثاء وكل يوم خميس لجميع الزوار."
)


def _page(url, body, lang=None, page_type="content"):
    page = {
        "url": url,
        "final_url": url,
        "status": 200,
        "title": "Ei side",
        "page_type": page_type,
        "body_text": body,
        "paragraphs": [body],
    }
    if lang is not None:
        page["lang"] = lang
    return page


def _snapshot(body, lang=None, **kwargs):
    pages = [_page(HOME, body, lang=lang, page_type="home")]
    pages += [_page("{}s{}".format(HOME, i), body, lang=lang) for i in range(1, 4)]
    snapshot = {
        "site": "verksted.test",
        "origin": "https://verksted.test",
        "brand": {"name": "Verkstedet", "host": "verksted.test"},
        "pages": pages,
        "sitemaps": [],
        "crawl": {"pages_crawled": len(pages), "pages_ok": len(pages),
                  "pages_read": len(pages), "render_mode": "static",
                  "user_agent": "test-agent", "elapsed_s": 1.0, "notes": []},
    }
    snapshot.update(kwargs)
    return snapshot


def _finding(id_hint, root_cause, checked=("the page text",), confidence="medium",
             severity="medium"):
    return {
        "id_hint": id_hint,
        "title": "Something about {}".format(root_cause),
        "severity": severity,
        "confidence": confidence,
        "evidence": "Across the pages this audit read, the thing was not found.",
        "mechanism": "C",
        "root_cause": root_cause,
        "affected_pages": [HOME],
        "affected_page_count": 1,
        "checked": list(checked),
        "suggested_action": {
            "summary": "Do the thing.",
            "effort": "low",
            "owner": "content owner",
            "how_to_fix": ["Step one."],
            "rationale": "Because it works.",
        },
    }


def _result(*findings):
    return {
        "skill": "fact-extractability-audit",
        "findings": list(findings),
        "signals": {},
        "checks_run": [],
        "not_applicable": [],
        "fired_checks": [],
        "extra_requests_made": 0,
    }


def _report(snapshot, *findings):
    return compose.compose(snapshot, [_result(*findings)],
                           audited_at="2026-01-01T00:00:00Z")


def _by_id(report):
    return {f["id"]: f for f in report["findings"]}


# --------------------------------------------------------------------------
# The classification is closed: no cause escapes the question
# --------------------------------------------------------------------------

def _inferred_causes():
    return {cause for cause in ROOT_CAUSES
            if EVIDENCE_BASIS.get(cause, INFERRED) == INFERRED}


def test_every_inferred_cause_is_classified_as_prose_or_not_prose():
    """The rule is only a rule if the vocabulary it ranges over is closed.

    A root cause added next month is INFERRED by default - `evidence_basis`
    says so - and would otherwise fall through this file silently. Every
    inferred cause has to be in exactly one of the two sets, with the reason
    written beside it, so adding a check means answering the question.
    """
    classified = compose.PROSE_DERIVED_CAUSES | compose.NOT_PROSE_DERIVED_CAUSES
    unclassified = _inferred_causes() - classified
    assert not unclassified, (
        "these root causes can reach a report and nobody has said whether their "
        "claim rests on reading words: {}. Add each to PROSE_DERIVED_CAUSES or to "
        "NOT_PROSE_DERIVED_CAUSES in compose_report.py, with the mechanism you "
        "checked.".format(sorted(unclassified)))


def test_the_two_sets_are_disjoint_and_name_only_real_causes():
    both = compose.PROSE_DERIVED_CAUSES & compose.NOT_PROSE_DERIVED_CAUSES
    assert not both, "classified twice, both ways: {}".format(sorted(both))
    unknown = (compose.PROSE_DERIVED_CAUSES | compose.NOT_PROSE_DERIVED_CAUSES) - ROOT_CAUSES
    assert not unknown, "not a root cause: {}".format(sorted(unknown))


def test_nothing_read_straight_off_the_document_is_called_prose_derived():
    """`audit_common` already holds the rule this would break: a vocabulary is
    an identification, so a claim that comes from matching words is INFERRED.
    A cause marked OBSERVED and prose-derived at the same time means one of the
    two files is wrong about the same check, and the sentence a reader gets
    would contradict the tier the finding is published in."""
    wrong = {cause for cause in compose.PROSE_DERIVED_CAUSES
             if EVIDENCE_BASIS.get(cause, INFERRED) == OBSERVED}
    assert not wrong, (
        "these are classified OBSERVED in audit_common - settled by what the "
        "document literally contains - and prose-derived here: {}".format(sorted(wrong)))


def test_every_per_check_override_is_classified_too():
    """`EVIDENCE_BASIS_BY_FINDING` exists because two checks can share a cause
    and not share how they get their evidence. The same is true of prose: the
    check that reads a street out of a sentence and the check that counts
    JSON-LD nodes both raise `no-org-schema`."""
    inferred_hints = {hint for hint, basis in EVIDENCE_BASIS_BY_FINDING.items()
                      if basis == INFERRED}
    classified = compose.PROSE_DERIVED_BY_FINDING | compose.NOT_PROSE_DERIVED_BY_FINDING
    missing = inferred_hints - classified
    assert not missing, (
        "these checks override their cause's evidence basis and nobody has said "
        "whether they read words: {}".format(sorted(missing)))
    both = compose.PROSE_DERIVED_BY_FINDING & compose.NOT_PROSE_DERIVED_BY_FINDING
    assert not both, "classified twice, both ways: {}".format(sorted(both))


# --------------------------------------------------------------------------
# The rule over the whole report
# --------------------------------------------------------------------------

def _every_cause_report(snapshot):
    """One report holding one finding per root cause in the vocabulary.

    Swept rather than sampled, deliberately. A test written against the six
    causes that happen to read prose today passes for a check added next month
    that reads prose and says nothing about the language it read it in; a test
    that walks `ROOT_CAUSES` cannot.
    """
    findings = [_finding("check-for-{}".format(cause), cause)
                for cause in sorted(ROOT_CAUSES)]
    return _report(snapshot, *findings)


def test_on_a_non_english_site_every_published_prose_finding_states_the_language():
    """The rule, over every cause the marketplace can raise. A prose-derived
    finding published on a Norwegian site without the statement fails here."""
    report = _every_cause_report(_snapshot(NORWEGIAN, lang="no"))
    silent = [f["stable_id"] for f in report["findings"]
              if compose.prose_derived(f) and not f.get("read_in")]
    assert not silent, (
        "these findings rest on reading words and shipped without saying what "
        "language the words were read in: {}".format(sorted(silent)))
    # And the other half of the rule: it is not printed where it says nothing.
    noisy = [f["stable_id"] for f in report["findings"]
             if not compose.prose_derived(f) and f.get("read_in")]
    assert not noisy, (
        "these findings never read prose and carry a sentence about language "
        "anyway: {}".format(sorted(noisy)))


def test_the_statement_reaches_both_rendered_documents():
    """report.json is not what a person reads. The sentence has to be in the
    markdown and in the HTML, under the finding it qualifies."""
    report = _every_cause_report(_snapshot(NORWEGIAN, lang="no"))
    prose = [f for f in report["findings"] if compose.prose_derived(f)]
    assert prose, "the sweep produced no prose-derived finding to check"
    markdown = compose.render_markdown(report)
    document = compose.render_html(report)
    for finding in prose:
        assert finding["read_in"] in markdown, finding["stable_id"]
        assert "read as `no`" in finding["read_in"]
    assert "What language this was read in" in markdown
    assert "What language this was read in" in document
    assert "read as `no` in the latin script" in document


def test_the_sentence_sits_under_where_we_looked_and_above_why_it_matters():
    """It is a limit on the looking, so it goes with the looking. A reader who
    has reached "What to do" has already taken the claim as a fact."""
    report = _report(_snapshot(NORWEGIAN, lang="no"),
                     _finding("founding-year", "missing-core-fact"))
    markdown = compose.render_markdown(report)
    looked = markdown.index("**Where we looked.**")
    read_in = markdown.index("**What language this was read in.**")
    matters = markdown.index("**Why it matters.**")
    assert looked < read_in < matters


def test_one_report_gives_one_answer_about_the_language():
    """Derived once for the whole report. Two findings disagreeing about what
    language one site is in would be the report contradicting itself again,
    in a new place."""
    report = _every_cause_report(_snapshot(NORWEGIAN, lang="no"))
    sentences = {f["read_in"] for f in report["findings"] if f.get("read_in")}
    assert len(sentences) == 1, sentences
    assert report["crawl"]["site_language"]["code"] == "no"


# --------------------------------------------------------------------------
# Where it must stay silent
# --------------------------------------------------------------------------

def test_an_english_site_gets_no_sentence_about_language_anywhere():
    """"Read in English" on forty findings of an English report is noise, in a
    report that is already long. The trigger is that
    the audit could not establish the site is in the language the patterns were
    written for - not that a language exists."""
    report = _every_cause_report(_snapshot(ENGLISH, lang="en"))
    assert not [f for f in report["findings"] if f.get("read_in")]
    markdown = compose.render_markdown(report)
    assert "What language this was read in" not in markdown
    assert "What language this was read in" not in compose.render_html(report)


def test_a_site_declaring_nothing_but_writing_english_is_still_silent():
    """`detect_site_language` infers `en` from the function words. That is the
    audit establishing the site is in the language the patterns were written
    for, so there is nothing to warn a reader about."""
    report = _every_cause_report(_snapshot(ENGLISH))
    assert report["crawl"]["site_language"]["code"] == "en"
    assert not [f for f in report["findings"] if f.get("read_in")]


# --------------------------------------------------------------------------
# Never assert a language the audit does not know
# --------------------------------------------------------------------------

def test_a_cyrillic_site_is_given_its_script_and_no_language():
    """Cyrillic is Russian, Ukrainian, Bulgarian and Serbian. An earlier build
    that named one of them put `<html lang="en">` in front of a site that is
    not in English; the record returns no `code` for exactly this
    reason, and this sentence may not invent one."""
    report = _report(_snapshot(CYRILLIC),
                     _finding("founding-year", "missing-core-fact"))
    sentence = _by_id(report)["F-001"]["read_in"]
    assert sentence
    assert "cyrillic script" in sentence
    assert "did not establish which language" in sentence
    for named in ("`ru`", "`uk`", "`bg`", "`sr`", "Russian", "Ukrainian"):
        assert named not in sentence, sentence


def test_a_declaration_the_letters_contradict_leads_with_the_script():
    """The site declares `lang="en"` over Arabic prose. Printing "read as `en`"
    would republish the site's own mistake as this audit's reading, so the
    script leads and no language is named."""
    report = _report(_snapshot(ARABIC, lang="en"),
                     _finding("founding-year", "missing-core-fact"))
    sentence = _by_id(report)["F-001"]["read_in"]
    assert "read as `en`" not in sentence
    assert "arabic script" in sentence
    assert "did not establish which language" in sentence
    # The record's own phrase, so the reader learns the declaration is wrong
    # rather than only that the audit is unsure.
    assert "contradicted by page text" in sentence


def test_a_site_whose_language_is_not_establishable_says_so():
    """Nothing declared, nothing inferable. The audit proceeds as English -
    every version of this code always did - and this is the case where saying
    so is worth most, because the reader is the only one who can tell."""
    report = _report(_snapshot("1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18"),
                     _finding("founding-year", "missing-core-fact"))
    sentence = _by_id(report)["F-001"]["read_in"]
    assert sentence
    assert "not declared and not inferable" in sentence
    assert "written for English" in sentence


# --------------------------------------------------------------------------
# It qualifies findings; it does not change them
# --------------------------------------------------------------------------

def test_the_statement_does_not_move_a_finding_between_tiers():
    """The sentence lives beside `checked`, never in it. `published_tier`
    promotes a `medium` absence claim once it names CORROBORATED_ABSENCE_SOURCES
    places it looked, and `_render_finding` prints "that is one source" at
    exactly one entry - so appending there would have promoted every
    single-source prose absence on a non-English site and deleted its own
    caveat at the same time. Same findings, two languages, identical tiers and
    identical `checked` lists."""
    english = _every_cause_report(_snapshot(ENGLISH, lang="en"))
    norwegian = _every_cause_report(_snapshot(NORWEGIAN, lang="no"))
    assert ([(f["stable_id"], f["tier"], f["checked"]) for f in english["findings"]]
            == [(f["stable_id"], f["tier"], f["checked"]) for f in norwegian["findings"]])


def test_a_single_source_prose_finding_keeps_its_reduced_confidence_caveat():
    report = _report(_snapshot(NORWEGIAN, lang="no"),
                     _finding("founding-year", "missing-core-fact",
                              checked=("the visible page text",)))
    markdown = compose.render_markdown(report)
    assert "That is one source" in markdown
    assert "**What language this was read in.**" in markdown


def test_the_field_is_empty_rather_than_missing_on_an_english_report():
    """`read_in` is always present on a published finding, so a consumer reads
    one field rather than testing for a key that may not be there. An empty
    string means "nothing to say", which is not the same as "not answered"."""
    report = _report(_snapshot(ENGLISH, lang="en"),
                     _finding("founding-year", "missing-core-fact"))
    assert report["findings"][0]["read_in"] == ""


# --------------------------------------------------------------------------
# The sentence was in two wrong places
#
# The same rule was found broken in both directions on one population of
# sites:
#
#   under a tag lookup   the sentence sat under a finding whose measurement is
#                        "links inside the delivered <nav>, <header> or
#                        role=navigation markup". That lookup returned zero
#                        elements, so no word pattern was applied to anything.
#                        Wrong the way the earlier `viewport` case was wrong,
#                        and a different check.
#
#   missing from dates   and it was absent from the date finding, whose own
#                        first source is "dates printed in the visible text of
#                        each page" - a month vocabulary. That is the one
#                        finding on the whole population where the sentence
#                        would have warned the reader.
#
# Both are cases where one root cause covers emitters that do not read the same
# thing, which is what the per-finding override tables are for.
# --------------------------------------------------------------------------

def _report_with(hint, cause, checked=("the page text",)):
    return _report(_snapshot(NORWEGIAN, lang="no"), _finding(hint, cause, checked=checked))


def test_the_date_finding_says_which_month_names_it_looked_for():
    """`content-pages-carry-no-date` reads `page_extract.DATE_TEXT_RE` - `Jan`
    to `Dec` plus the CJK and Thai year-month-day shapes - before it reads any
    date property, and the finding needs both halves empty. A Norwegian or Thai
    page whose month name is not in that list is a page this finding calls
    dateless, which repeats three earlier misreadings of non-English dates in
    one sentence."""
    report = _report_with("content-pages-carry-no-date", "no-date-signal",
                          checked=("dates printed in the visible text of each page",
                                   "<time> elements and datePublished in the markup"))
    finding = report["findings"][0]
    assert compose.prose_derived(finding)
    assert "read as `no`" in finding["read_in"], finding["read_in"]
    assert finding["read_in"] in compose.render_markdown(report)


def test_the_sitemap_half_of_the_same_cause_still_says_nothing():
    """The other emitter under `no-date-signal` is arithmetic over `<lastmod>`
    values. Classifying the cause instead of the check would have put the
    sentence on both."""
    report = _report_with("published-content-has-gone-stale", "stale-content")
    assert not compose.prose_derived(report["findings"][0])
    assert report["findings"][0]["read_in"] == ""


def test_a_navigation_finding_measured_by_counting_elements_says_nothing():
    """The case that prompted this. The claim published is a count of links inside
    `<nav>`, `<header>` and `role=navigation`; the check's two word lists -
    `GENERIC_NAV_LABELS` and `NAV_CONTROL_LABELS` - are matched as whole
    lower-cased English labels, so on every site this sentence prints for they
    match nothing and remove nothing."""
    report = _report_with(
        "primary-navigation-does-not-guide", "no-orientation",
        checked=("links inside the delivered <nav>, <header> or role=navigation markup",))
    assert not compose.prose_derived(report["findings"][0])
    assert report["findings"][0]["read_in"] == ""
    assert "What language this was read in" not in compose.render_markdown(report)


def test_the_viewport_finding_says_nothing_either():
    """The earlier case the navigation one repeats: `<meta name="viewport">` is in the
    delivered head or it is not, and it shares `no-orientation` with four
    emitters that do read words."""
    report = _report_with("missing-mobile-viewport", "no-orientation")
    assert not compose.prose_derived(report["findings"][0])
    assert report["findings"][0]["read_in"] == ""


def test_the_prose_emitters_of_the_same_cause_keep_the_sentence():
    """The two overrides above are per check and may not silence their cause.
    `no-orientation`'s other emitters rest on `CTA_VERBS` and on delivery and
    returns phrase lists, and they are why the cause is classified as prose."""
    for hint in ("homepage-does-not-orient-visitors",
                 "landing-pages-do-not-answer-the-arriving-question",
                 "page-headings-do-not-name-the-page"):
        report = _report_with(hint, "no-orientation")
        assert compose.prose_derived(report["findings"][0]), hint
        assert report["findings"][0]["read_in"], hint
