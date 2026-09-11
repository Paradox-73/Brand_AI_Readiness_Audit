#!/usr/bin/env python3
"""Merge sub-skill findings into report.json and report.md.

Responsibilities that belong to the entrypoint and nowhere else:
  - deduplicate observations two skills both noticed
  - assign stable finding IDs
  - compute priority as severity x reach / effort
  - add proactive recommendations whose conditions are met
  - simulate which sentence an assistant would quote from each key page
  - render the human-readable report

Usage:
    python compose_report.py --snapshot snapshot.json \
        --findings a.json b.json ... --out-json report.json --out-md report.md
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import html
import os
import re
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The one test for "is this string writing or is it program source", kept in
# the extractor because that is where the leak happens and applied here
# because this is where a string becomes a sentence a report prints back.
from page_extract import reads_as_source_not_prose  # noqa: E402

from audit_common import (
    sitemap_scope, sitemap_total_phrase, words_are_separated,  # noqa: E402
    ABSENCE_CAUSES, CORROBORATED_ABSENCE_SOURCES,
    EFFORT_DIVISOR, MECHANISMS, SEVERITY_RANK, SEVERITY_WEIGHT,
    STUDY_SAMPLE_PHRASE, VERSION,
    LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION, PROJECT, PUBLIC_BODY,
    PERSONAL_OR_ACADEMIC, PUBLICATION, site_kind,
    OBSERVED, evidence_basis,
    # The language a check read the page text in, and the language every word
    # list in this marketplace is written in. Imported rather than restated so
    # the sentence a reader sees cannot drift from the gate the checks consult.
    LANGUAGE_SCRIPTS, PROSE_LANGUAGE, language_of,
    load_snapshot, pages_of, plural, read_json, sells_something, sentences,
    SUB_SKILLS, truncate, write_json,
    # How many crawled URLs were pages; and whether a line opens by
    # addressing the reader, the way campaign copy does.
    pages_the_crawl_read, addresses_the_reader,
    # The one page every skill means by "the homepage".
    site_homepage,
    # The one rule for "does this sentence say what the brand is", and the
    # forms of the brand's name it reads.
    defining_sentence, brand_forms, brand_pattern,
)

# Sub-skill order. Earlier skills own overlapping observations, so when two
# skills see the same root cause on the same pages the earlier one's wording
# and fix survive and the later one's evidence is merged into it.
# The dedup precedence, which is the run order: an earlier skill owns an
# observation two skills both make. One definition, in `audit_common`, because
# four copies of this list were in the tree and reordering any one of them
# would have silently changed which skill owns a shared root cause.
SKILL_ORDER = list(SUB_SKILLS)

# Priority and severity are different questions - how bad is this, versus what
# should you do first - and they answered in the same four words. A report that
# lists a finding under "Medium findings" and then says "Priority critical" two
# lines later reads as a contradiction, whatever the arithmetic underneath. The
# bands now say what they mean as an instruction.
PRIORITY_BANDS = ((2.0, "do first"), (1.0, "do soon"), (0.45, "schedule"), (0.0, "when convenient"))

# Measured in the within-category study: brands assistants name carry a date on
# 34% of crawled pages, comparable brands they ignore on 17%. The floor sits
# between the two. See references/cited-vs-uncited-study.md.
DATE_COVERAGE_FLOOR = 0.25

# The two tiers a finding can be published in.
#
# A validation pass over six unseen sites produced 92 findings. An independent
# review, checking every finding against the live page, marked 6 of them wrong
# and 11 overstated - 22% of everything published was defective - and the
# single wrongest finding on one site was ranked first in
# "Start here". Every finding already carries a `confidence`, and it already
# meant what is needed here: 41 of the 92 said `medium`, and the field gated
# nothing, so a reading the audit itself calls uncertain was printed beside a
# direct one, in the same list, under the same heading, at the same weight.
# That is what makes a fifth of the report being soft feel like the whole
# report is unreliable.
#
# That got the soft readings out of the graded body. It did not get the wrong
# ones out. A later run over eight unseen sites scored 84.6% in the confident
# tier against 82.9% in the lower one: the split was honest and it separated
# nothing, because `confidence` is a value each check writes about itself.
# It is an author's opinion, so sorting on it sorts by how bold each check's
# author felt, and all three of the worst errors in that run sat in the
# confident tier while two plainly-true findings sat below them.
#
# So there are now two gates, asked in this order, and they are different
# questions:
#
#   how the evidence was obtained  `audit_common.evidence_basis`, derived from
#                                  the root cause and the check rather than
#                                  authored. A reading that rests on this audit
#                                  having correctly decided what something IS -
#                                  a page type, a street in prose, whose
#                                  account a URL is - never leads the report,
#                                  whatever it says about itself.
#   how sure the check says it is  `certainty_tier`, unchanged and still a rule
#                                  about one field. A check calling its own
#                                  reading `low` is telling the reader
#                                  something no map can tell them.
#
# Severity is still no part of either. Severity and certainty are different
# axes, and the high-severity, weakly-evidenced finding is the dangerous one
# precisely because it leads "Start here" while being the kind of reading most
# likely to be wrong.
CONFIDENT = "confident"
WORTH_CHECKING = "worth checking"

# Confidence levels the graded body of the report is drawn from. `medium` and
# `low` are the audit saying "probably", and a report may not print a probably
# and a certainty in the same list without saying which is which.
CONFIDENT_LEVELS = frozenset({"high"})


def certainty_tier(finding):
    """What the check that raised this finding says about its own certainty.

    One field, and it stays one field. This is the second of the two gates in
    `published_tier` below, not the whole tier rule: it answers "how sure does
    the check say it is", which is a different question from "how was the
    evidence obtained". Keeping it a rule about `confidence` alone is what
    stops it growing into a list of special cases, and the list would be one
    short on the next site audited.
    """
    return (CONFIDENT if finding.get("confidence") in CONFIDENT_LEVELS
            else WORTH_CHECKING)


def published_tier(finding):
    """Which tier this finding is actually published in.

    The evidence basis first, because it is derived rather than authored. An
    inferred reading is one whose claim only holds if this audit correctly
    decided what something is, and every finding the independent review
    marked wrong in the confident tier was one of those.

    Then `certainty_tier`, and between the two sits `medium`, which is where
    that validation pass's other failure lived. Two findings that were plainly true -
    a postal address that really was missing, a founding year that really was
    missing - sat in the lower tier because the check that raises them
    hard-codes `medium`, while three wrong readings led the report at `high`.
    A `medium` reading taken directly off the document is promoted, on the one
    condition that already governs claims of absence everywhere else here: it
    named at least `CORROBORATED_ABSENCE_SOURCES` independent places that came
    back empty. One detector with a finite list of shapes is still a guess with
    a number on it, whatever it read.

    `low` is never promoted. A check that calls its own reading `low` knows
    something about that reading which no map of causes can know.

    The source floor asks for a second place that came back empty, and that
    only makes sense where a second place exists. "37 of 43 crawled
    pages have no H1" - read straight out of the markup and true on every
    page opened to check it - was sitting in the tier a reader is told to verify,
    because the check named one place it looked and there is exactly one place
    to look. `SINGLE_PLACE_ABSENCES` below is the exemption and says why.

    A demoted finding is still in `findings[]`, still counted in the summary
    and still rendered in full - the goal is few false positives *and* few
    misses, and moving a finding down is not a miss while deleting it is.
    """
    # Asked before either of the two gates, because it is the only test here
    # that knows what the *rest of the report* says. A finding another
    # published finding contradicts about the same page may not sit beside the
    # readings the audit took straight off the document, whatever its own
    # evidence basis and whatever the check says about its own certainty.
    # `flag_opposite_claims` has already decided which of the two this audit is
    # entitled to assert; this line is where that decision reaches the reader,
    # and it is what keeps the weaker half out of "Start here".
    if finding.get("disputed_by"):
        return WORTH_CHECKING
    if evidence_basis(finding) != OBSERVED:
        return WORTH_CHECKING
    if certainty_tier(finding) == CONFIDENT:
        # No source-count test on this branch: `make_finding` already caps a
        # single-source claim of absence at medium before it can reach here, so
        # repeating the test would only be a second way of spelling the cap.
        return CONFIDENT
    if finding.get("confidence") != "medium":
        return WORTH_CHECKING
    if (finding.get("root_cause") in ABSENCE_CAUSES
            and finding.get("root_cause") not in SINGLE_PLACE_ABSENCES
            and len(finding.get("checked") or []) < CORROBORATED_ABSENCE_SOURCES):
        return WORTH_CHECKING
    return CONFIDENT


# Absences with exactly one place to look, so naming one source is naming all
# of them.
#
# The source floor exists for the absence of a *fact*: a founding year can be
# in the prose, in the markup or on an about page, and a check that looked in
# one of the three has not established it is missing. An element is not a fact.
# A page has one `<html lang>` attribute, one `<h1>`, one set of Open Graph
# tags and one head; there is no second place a missing one could be hiding,
# and demanding a second source demands a source that cannot exist. Every
# member below is also classified OBSERVED, so it reached this test having
# already been settled by what the document literally contains.
#
# Measured, the cost of not having this list: the two tiers came back at
# 78% and 79% accurate on one population, meaning the split had stopped sorting
# anything, and the clearest example of a fact demoted for want of an
# impossible second source was a count of missing H1 elements true on every
# page they checked.
#
# The test for membership is "could this value legitimately live somewhere
# else, so that a check reading one place has not looked everywhere?" - and it
# is a stricter test than "is it cheap to check". Every entry below is an
# element with exactly one spelling, and the causes deliberately left out are
# more interesting than the ones in:
#
#   meta-hygiene            a page's summary is also carried in
#   open-graph-incomplete   `og:description` and in the Twitter card tags, and
#                           the crawler records all three separately. A check
#                           that read one of the three has looked in one of
#                           three places, which is what the floor is for.
#   missing-schema-props    a property missing from a JSON-LD node can be
#                           declared in microdata or RDFa instead, both of
#                           which the crawler reads separately.
#   alt-missing             an image's text alternative can be an `alt`, an
#                           `aria-label` or a caption beside it.
#   missing-core-fact       the archetype: a founding year can be in the prose,
#   no-entity-definition    in the markup or on a page the crawl did not reach.
#   weak-corroboration      This is the case the floor was written for and it
#                           stays exactly as it was.
SINGLE_PLACE_ABSENCES = frozenset({
    # There is no second element that is a page's H1, and no second attribute
    # that declares its language. A check that read the delivered markup has
    # looked in the only place either could be.
    "heading-structure",
    "missing-lang",
})

# How many pages have to show a gap before a recommendation may describe it as
# a property of the template rather than of one page.
#
# Two. One page is a page; two independent pages showing the same gap is the
# template. The clean fixture - the one that must produce nothing at all - has
# exactly one deep page whose only onward links are the site's own menu, and a
# recommendation gated on "the list is not empty" told that site to rebuild its
# wayfinding. `engagement-audit` applies the same reasoning one level down with
# its own three-page floor before it will describe a template at all; this is
# the weaker bar that fits proactive advice rather than a defect claim.
TEMPLATE_EVIDENCE_MINIMUM = 2

EFFORT_TIME = {
    "low": "under an hour",
    "medium": "half a day to a day",
    # No discipline in the phrase. This entry read "several days of development
    # time" for every owner, and it was printed against a finding
    # owned by `content owner`: the report named a writer and then priced their
    # work in developer-days. The discipline now comes from the owner, one line
    # below, so the two halves of the sentence cannot name two different jobs.
    "high": "several days",
}

# The kind of work each owner does, used to finish the effort sentence.
#
# Read off `suggested_action.owner` rather than declared beside the effort,
# because the owner is the field the skills already set per finding and the
# only one that knows whose diary the time comes out of.
EFFORT_WORK = {
    "developer": "development time",
    "content owner": "writing and editing time",
    "marketing": "someone's time",
}

# Owners whose work means editing the files the site is built from rather than
# typing into an admin screen.
#
# This is the distinction the report never drew. Across four real reports 7
# to 9 findings of every 12 to 14 were typed `low` / `developer`, and for a
# shop owner with no developer "low effort" is not an hour of their evening -
# it is a person they do not have. Counting them once, at the top, tells the
# reader what they are up against before they start rather than after they
# have read nine fixes they cannot carry out.
TEMPLATE_EDITOR_OWNERS = frozenset({"developer"})


def effort_time(effort, owner=""):
    """"under an hour", or "several days of development time".

    The noun comes from the owner so a report cannot price a writer's work in
    developer-days, which `EFFORT_TIME["high"]` did unconditionally.
    """
    phrase = EFFORT_TIME.get(effort, effort or "")
    work = EFFORT_WORK.get(owner)
    # Only the long end takes the noun. "Under an hour of writing and editing
    # time" is padding; "several days" with no discipline is the sentence
    # that misled, because several days is the figure a reader budgets for.
    if work and effort == "high":
        return "{} of {}".format(phrase, work)
    return phrase

NUMERIC_RE = re.compile(r"\d|[$€£¥₹]")
# A line that is a price, and a line that is a way to reach somebody. Both are
# shorter than a sentence and both are what people ask assistants for.
PRICE_LINE_RE = re.compile(
    r"[$€£¥₹]\s?\d|\b\d[\d,.]*\s?(?:USD|EUR|GBP|INR|AUD|CAD|SGD|AED|JPY)\b", re.I)
CONTACT_LINE_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]{2,}"
    r"|\+\d[\d\s().-]{7,}\d"
    r"|\b(?:call|email|phone|contact) (?:us|our)\b", re.I)
# How short a sentence may be and still be worth quoting. The second is for
# scripts that write without spaces between words, where the same amount of
# meaning fits in far fewer characters.
QUOTE_MIN_CHARS = 40
QUOTE_MIN_CHARS_UNSPACED = 16
CONCRETE_RE = re.compile(
    r"\d|[$€£¥₹]|\b(?:is|are)\s+(?:an?|the)\b|\b(?:means|refers to|defined as)\b", re.I)


# --------------------------------------------------------------------------
# Merge and score
# --------------------------------------------------------------------------

def load_skill_results(paths):
    results = []
    for path in paths:
        data = read_json(path)
        results.append(data)
    order = {name: i for i, name in enumerate(SKILL_ORDER)}
    results.sort(key=lambda r: order.get(r.get("skill", ""), 99))
    return results


def _action_depth(action):
    """How much of a remedy a suggested action actually is.

    Number of concrete steps first, then whether it hands over a paste-ready
    block, then how much the summary says. "Step one." loses to a four-step
    fix carrying a snippet, which is the comparison this exists to make.
    """
    return (len(action.get("how_to_fix") or []),
            1 if action.get("snippet") else 0,
            len(action.get("summary") or ""))


def dedupe(results):
    """Collapse findings that describe the same root cause on the same pages.

    Two skills legitimately notice one problem from different angles. Reporting
    it twice makes the report look padded and inflates the severity counts,
    so the earlier skill keeps the finding and the later one's evidence is
    appended to it.

    On a healthy build this never fires, and that is the point rather than a
    defect. `test_no_root_cause_has_two_owners` fails the build if any two
    skills emit the same root cause, so the cross-skill branch below is
    unreachable while the decomposition holds. It is a backstop against a
    future skill reaching into another's territory, not a working feature, and
    `merged_duplicates` being empty in every report is the evidence that the
    six skills are still separate. `test_dedupe_is_a_backstop` pins both
    halves of that: the branch is exercised on synthetic input, and the
    production impossibility is asserted.
    """
    merged = {}
    order = []
    duplicates = []
    for result in results:
        skill = result.get("skill", "unknown")
        for finding in result.get("findings", []):
            key = (finding["mechanism"], finding["root_cause"],
                   tuple(sorted(finding.get("affected_pages") or [])))
            existing = merged.get(key)
            # Merge only across skills. A single skill emitting several findings
            # that share a root cause is doing so deliberately - robots.txt can
            # block answer crawlers, training crawlers and content paths, and
            # those are three different decisions with three different fixes.
            if existing is not None and existing["detected_by"] != skill:
                existing.setdefault("also_detected_by", []).append(skill)
                existing["evidence"] = "{} Also observed by {}: {}".format(
                    existing["evidence"], skill, finding["evidence"])
                record = {"skill": skill, "id_hint": finding["id_hint"],
                          "merged_into": existing["id_hint"]}
                # Only `evidence` used to survive the merge, so the loser's
                # severity and remedy were thrown away with it. Reproduced: an
                # earlier skill's `low` finding absorbed a later skill's
                # `critical` one, and the report kept `low` and a one-line
                # "Step one." while the fix that actually resolved the problem
                # went in the bin. Two skills seeing one root cause disagree
                # about how bad it is; the more serious reading is the one an
                # owner has to act on, so it wins.
                # `.get` throughout. This branch is a backstop that no real run
                # reaches, so the only thing that exercises it is a test whose
                # findings carry the merge key and nothing else - and reading a
                # field straight killed composition rather than merging.
                theirs = finding.get("severity") or "info"
                ours = existing.get("severity") or "info"
                if SEVERITY_RANK.get(theirs, 0) < SEVERITY_RANK.get(ours, 0):
                    record["severity_raised_from"] = ours
                    record["severity_raised_to"] = theirs
                    existing["severity"] = theirs
                if _action_depth(finding.get("suggested_action") or {}) >                         _action_depth(existing.get("suggested_action") or {}):
                    record["fix_taken_from"] = skill
                    existing["suggested_action"] = dict(finding["suggested_action"])
                duplicates.append(record)
                continue
            record = dict(finding)
            record["detected_by"] = skill
            # Same skill, same key: keep both, but give the later one a distinct
            # slot so it is not silently overwritten.
            slot = key if existing is None else key + (finding["id_hint"],)
            merged[slot] = record
            order.append(slot)
    return [merged[k] for k in order], duplicates


# Pairs of root causes that are one job on one template, done in one edit.
#
# `dedupe` above merges two skills that noticed the *same* cause. This is the
# other case: two causes, two skills, one thing to do. Both of these were
# printed as separate findings on one report - "Add a visible breadcrumb
# trail to deep page templates" at medium and "Add BreadcrumbList JSON-LD to
# deep page templates" at low, over the same templates - each listing the other
# as its own third step, and a proactive recommendation saying it a third time.
# Three entries for one edit inflates the finding count and makes the reader
# read the same instruction three times to discover it is one instruction.
#
# Keyed on the pair rather than on a general rule, because the general rule
# would be "merge findings whose fix steps overlap", and fix text overlapping
# is not evidence that two problems are one problem.
#
#   visible, visible_check   the cause whose finding survives the merge, and
#                            the check that raises it. The visible trail is the
#                            half a person can see, so it leads.
#   markup, markup_check     the cause folded into it, and its check.
#   step_about_markup        the phrase that marks a step as an instruction to
#   step_about_the_trail     do the *other* half. Where only one of the two
#                            fired, that step is dropped: the other check ran
#                            and stayed quiet, which means the site already has
#                            that half. One report told a site
#                            to "Mirror it in BreadcrumbList structured data"
#                            three lines under its own evidence saying 11 of
#                            its pages already carry it.
#
# The phrases are matched with the spaces taken out and lowercased, so a step
# reworded across two lines still matches.
ONE_JOB_PAIRS = (
    {"visible": "no-breadcrumbs", "visible_check": "breadcrumb-navigation",
     "markup": "no-breadcrumb-markup", "markup_check": "breadcrumb-markup",
     "step_about_markup": "breadcrumblist",
     "step_about_the_trail": "novisiblebreadcrumb"},
)


def _mentions(step, phrase):
    return phrase in (step or "").replace(" ", "").lower()


def fold_one_job_findings(findings, checks_that_ran=(), checks_that_fired=()):
    """Merge the pairs above, and drop cross-references the site does not need.

    Only the orchestrator can do either. Each half is raised by a different
    skill, and neither skill can see whether the other one fired.

    `checks_that_ran` and `checks_that_fired` are `(skill, check)` pairs. A
    cross-reference is only dropped where the other half's check actually ran
    and stayed quiet. A check that never ran has established nothing, and
    deleting the step would then be this file asserting an absence it has no
    evidence for - the mistake the whole absence rule exists to stop.
    """
    ran = {check for _, check in checks_that_ran}
    fired = {check for _, check in checks_that_fired}
    by_cause = {}
    for finding in findings:
        by_cause.setdefault(finding.get("root_cause"), []).append(finding)

    out = list(findings)
    folded = []
    for pair in ONE_JOB_PAIRS:
        visible = by_cause.get(pair["visible"]) or []
        markup = by_cause.get(pair["markup"]) or []
        if visible and markup:
            keep, drop = visible[0], markup[0]
            action = keep.get("suggested_action") or {}
            other = drop.get("suggested_action") or {}
            # The union of both fix lists with the cross-references removed,
            # because once the two are one finding the cross-reference is a
            # step telling the reader to read the finding they are reading.
            steps = [s for s in (action.get("how_to_fix") or [])
                     if not _mentions(s, pair["step_about_markup"])]
            for step in other.get("how_to_fix") or []:
                if step not in steps and not _mentions(
                        step, pair["step_about_the_trail"]):
                    steps.append(step)
            action["how_to_fix"] = steps
            # The paste-ready block belongs to the markup half and is the part
            # a person cannot write from the instruction alone, so it survives.
            if other.get("snippet") and not action.get("snippet"):
                action["snippet"] = other["snippet"]
                if other.get("snippet_warning"):
                    action["snippet_warning"] = other["snippet_warning"]
            if SEVERITY_RANK.get(drop.get("severity"), 99) < SEVERITY_RANK.get(
                    keep.get("severity"), 99):
                keep["severity"] = drop["severity"]
            keep.setdefault("also_detected_by", []).append(drop.get("detected_by"))
            keep["evidence"] = "{} Also observed by {}: {}".format(
                keep.get("evidence", ""), drop.get("detected_by"),
                drop.get("evidence", ""))
            keep["affected_pages"] = sorted(
                set(keep.get("affected_pages") or []) |
                set(drop.get("affected_pages") or []))
            out.remove(drop)
            folded.append({"skill": drop.get("detected_by"),
                           "id_hint": drop.get("id_hint"),
                           "merged_into": keep.get("id_hint"),
                           "reason": "because both are one edit to one template"})
            continue
        # Only one half fired. Drop its instruction to do the other half where
        # the other half's check ran and found nothing to report.
        for cause, other_check, phrase in (
                (pair["visible"], pair["markup_check"], pair["step_about_markup"]),
                (pair["markup"], pair["visible_check"], pair["step_about_the_trail"])):
            if other_check not in ran or other_check in fired:
                continue
            for finding in by_cause.get(cause) or []:
                action = finding.get("suggested_action") or {}
                action["how_to_fix"] = [
                    s for s in (action.get("how_to_fix") or [])
                    if not _mentions(s, phrase)]
    return out, folded


# Pairs of findings that are one defect on one set of pages, billed twice.
#
# `fold_one_job_findings` merges two causes that are one edit. These are two
# checks, in two skills or in one, each measuring the same broken thing from
# its own side, and each printed as its own row in the severity table:
#
#   one dead address     a link to a page that answers 404 is a broken link to
#                        the engagement skill and a failed fetch to the access
#                        skill. One report billed one 404 twice, high and
#                        medium, with two fixes for one link.
#   one canonical tag    a page whose canonical names another host is also the
#                        reason the site's canonicals are split across hosts.
#                        One tag, two rows.
#   one set of twins     addresses that differ only in their query string and
#                        declare no canonical are, read by their text, the same
#                        page at several addresses. The same eight addresses,
#                        two rows, two copies of one fix.
#
# Each entry names the two id hints and how "the same pages" is decided, since
# that is different for each: the dead address by the address, the canonical
# tag by which pages carry the stray host, and the twins by their page lists.
#
# Two ways of making them one. Two findings with one root cause are merged
# into one, because nothing the report names is lost. Two with different
# root causes are both kept, and the lesser is marked `follows_from` the
# other: it leaves "Start here" and the severity totals, and its root cause
# stays in `findings[]` for anything that reads them.
SAME_DEFECT_PAIRS = (
    # `take_steps`: whether the merged finding's fix adds anything - the twin
    # finding's step naming the address to declare is the one line the other
    # lacks.
    {"hints": ("broken-internal-links", "high-non-200-rate"), "match": "dead-address",
     "take_steps": False,
     "why": "the same dead address, reached once as a link and once as a fetch"},
    {"hints": ("canonical-points-off-domain", "mixed-canonical-hosts"),
     "match": "stray-canonical-host", "take_steps": False,
     "why": "the same canonical tag: the page naming another host is the whole of the split"},
    {"hints": ("the-same-page-at-several-addresses-with-no-canonical",
               "filter-parameters-produce-pages-with-no-canonical"),
     "match": "same-pages", "take_steps": True,
     "why": "the same set of addresses serving one page with no canonical"},
)

# How many pages two lists of twins may differ by and still be one set. One: a
# check that groups by title and a check that groups by text disagree about the
# one address whose title the template wrote differently.
SAME_PAGES_SLACK = 1


def _host_of(url):
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _complete_pages(finding):
    pages = set(finding.get("affected_pages") or [])
    total = finding.get("affected_page_count") or len(pages)
    return pages if pages and total <= len(pages) else None


def _one_defect(pair, keep, drop, snapshot, signals):
    """Do `keep` and `drop` describe the same defect on the same pages?"""
    if pair["match"] == "dead-address":
        link = keep if (keep.get("id_hint") or keep.get("stable_id")) == pair["hints"][0] \
            else drop
        fetch = drop if link is keep else keep
        # The dead targets, from the engagement skill's own record of them:
        # its page list now names the pages carrying the links, which are the
        # pages to edit, and the targets are what the fetch finding lists.
        targets = set(((signals or {}).get("broken_link_targets") or {}).keys()) \
            or set(link.get("affected_pages") or [])
        fetched = _complete_pages(fetch)
        return bool(fetched) and fetched == targets
    if pair["match"] == "stray-canonical-host":
        tagged = keep if (keep.get("affected_pages") or []) else drop
        split = drop if tagged is keep else keep
        if split.get("affected_pages"):
            return False
        site = _host_of(snapshot.get("origin") or "")
        stray = {page["url"] for page in snapshot.get("pages") or []
                 if (page.get("canonical") or "").strip()
                 and _host_of(page["canonical"]) not in ("", site)}
        return bool(stray) and stray <= set(tagged.get("affected_pages") or [])
    first, second = _complete_pages(keep), _complete_pages(drop)
    if not first or not second or len(first) < 2 or len(second) < 2:
        return False
    small, large = sorted((first, second), key=len)
    return small <= large and len(large) - len(small) <= SAME_PAGES_SLACK


def _same_defect_pairs(findings, snapshot, signals=None):
    """`[(pair, keep, drop)]` - the pairs above found in `findings`, one defect each.

    The finding kept is the more severe, then the one naming more pages.
    """
    by_hint = {}
    for finding in findings:
        by_hint.setdefault(finding.get("id_hint") or finding.get("stable_id"), []).append(finding)
    out = []
    for pair in SAME_DEFECT_PAIRS:
        first, second = (by_hint.get(h) or [] for h in pair["hints"])
        if len(first) != 1 or len(second) != 1 or first[0] is second[0]:
            continue
        keep, drop = sorted(
            (first[0], second[0]), key=lambda f: (SEVERITY_RANK.get(f.get("severity"), 99),
                                                  -len(f.get("affected_pages") or [])))
        if _one_defect(pair, keep, drop, snapshot, signals):
            out.append((pair, keep, drop))
    return out


def defer_to_the_same_defect(findings, snapshot, signals=None):
    """Mark the lesser of each pair above with different root causes `follows_from`
    the other, and return the records written.

    Runs after the ids exist and after the page-level deferrals, and leaves a
    finding already attributed there alone. Nothing is removed: a 404 the
    access skill fetched keeps its own root cause in `findings[]`, and stops
    being counted a second time beside the broken link that points at it.
    """
    records = []
    for pair, keep, drop in _same_defect_pairs(findings, snapshot, signals):
        if keep.get("root_cause") == drop.get("root_cause"):
            continue
        if drop.get("follows_from") or drop.get("partly_follows_from") \
                or keep.get("follows_from") or not keep.get("id"):
            continue
        pages = list(drop.get("affected_pages") or [])
        record = {"id": keep["id"],
                  "stable_id": keep.get("stable_id") or keep.get("id_hint"),
                  "why": pair["why"], "basis": "same-defect",
                  "pages": pages[:DEFERRED_PAGES_SHOWN],
                  "page_count": drop.get("affected_page_count") or len(pages)}
        drop["follows_from"] = record
        records.append(dict(record, deferred=drop.get("id")))
    return records


def merge_one_defect_billed_twice(findings, snapshot, signals=None):
    """`(findings, merged)` - each pair above with one root cause, made one finding.

    The finding kept takes the other's evidence, its fix steps where they add
    something, and the union of the two page lists where both list the same
    kind of page. The merged check stays named, through `merged_from`, so the
    appendix still accounts for it. Pairs with two root causes are left to
    `defer_to_the_same_defect`, which keeps both.
    """
    out, merged = list(findings), []
    for pair, keep, drop in _same_defect_pairs(findings, snapshot, signals):
        if keep.get("root_cause") != drop.get("root_cause"):
            continue
        if keep not in out or drop not in out:
            continue
        action = keep.setdefault("suggested_action", {})
        steps = list(action.get("how_to_fix") or [])
        for step in ((drop.get("suggested_action") or {}).get("how_to_fix") or []
                     if pair["take_steps"] else []):
            if step not in steps:
                steps.append(step)
        action["how_to_fix"] = steps
        other = drop.get("suggested_action") or {}
        if other.get("snippet") and not action.get("snippet"):
            action["snippet"] = other["snippet"]
            if other.get("snippet_warning"):
                action["snippet_warning"] = other["snippet_warning"]
        keep["evidence"] = "{} The same defect, as {} reads it: {}".format(
            (keep.get("evidence") or "").rstrip(), drop.get("detected_by") or "another check",
            drop.get("evidence") or "")
        if pair["match"] == "same-pages":
            keep["affected_pages"] = sorted(set(keep.get("affected_pages") or [])
                                            | set(drop.get("affected_pages") or []))
            keep["affected_page_count"] = len(keep["affected_pages"])
        if drop.get("detected_by") and drop.get("detected_by") != keep.get("detected_by"):
            keep.setdefault("also_detected_by", []).append(drop["detected_by"])
        keep.setdefault("merged_from", []).append(
            {"skill": drop.get("detected_by") or "", "check": drop.get("check"),
             "id_hint": drop.get("id_hint")})
        out.remove(drop)
        merged.append({"skill": drop.get("detected_by"), "id_hint": drop.get("id_hint"),
                       "merged_into": keep.get("id_hint"),
                       "reason": "because both describe {}".format(pair["why"])})
    return out, merged


# Words that make a finding a claim about something not being there.
#
# The single largest class of wrong finding across twenty-six audited sites was
# this one shape: the crawler was not allowed to look, and the report said the
# thing was not there. It appeared as "No XML sitemap is available" when the
# sitemap returned 429; as "no rules to evaluate, which means nothing is
# disallowed" about a robots.txt that returned 403 and contains a crawl delay;
# as "the site never states its founding facts" drawn from four pages of sixty
# while the page that states them sat in the same report's crawl table marked
# 403; as "no product detail pages were detected" about a retailer whose
# product pages were blocked. Four different skills, four separate fixes, and
# the next blocked site would have found a fifth.
#
# So it is caught once, here, where the whole report and the whole crawl are
# both in view - which means it also covers every check written after this one.
_ABSENCE_RE = re.compile(
    r"\b(no|not|never|none|missing|without|absent|lacks?|zero)\b", re.I)

# Below this share of readable pages, an absence claim is a claim about the
# crawler's reception rather than about the site.
#
# Half. Above it the sample is most of the site and silence means something;
# below it the unread half is exactly where an about page, a contact page or a
# product catalogue would be. Measured across the validation sites:
# unblocked crawls sit at 0.93 to 1.00 readable, and the blocked ones that
# produced the false findings sit at 0.00, 0.03 and 0.07. Nothing observed
# falls between 0.07 and 0.93.
#
# A share, and deliberately not the flat `VERDICT_MIN_READABLE_PAGES` count the
# verdict uses. The two questions are different. A verdict is a claim about a
# whole site's health drawn from a sample, so it needs a sample; an absence
# claim is answerable on a site that has one page, as long as the page was
# read. A count floor here would tell the owner of a single-page site that
# nothing about their site could be established, on a crawl that read all of
# it.
READABLE_SHARE_FOR_ABSENCE = 0.5


def claims_absence(finding, brand_name=""):
    """Does this finding assert that something is not on the site?

    The brand's own name is not part of the claim, and reading it as one is a
    live trap: a real medical charity carries the word "without" in its own
    registered name, so every finding naming it read as a claim of absence and
    the whole report was held back as unverifiable. Names arrive quoted in these titles, so the
    quoted spans come out before the sentence is read, and the brand name comes
    out whether it is quoted or not.
    """
    title = re.sub(r'"[^"]*"', " ", finding.get("title", ""))
    if brand_name:
        title = re.sub(re.escape(brand_name), " ", title, flags=re.I)
    return bool(_ABSENCE_RE.search(title))


# The `skipped` reason `crawl.py` writes for a response whose own content type
# proved it was never a page. Matched as a fallback for a snapshot written
# before `not_a_page` existed; new crawls set the flag.
_NOT_A_PAGE_REASON = "non-HTML content type"


def page_shaped_urls(snapshot):
    """The URLs this crawl went looking for a page at, and could have found one.

    The denominator of every "how much of this site was read" figure in this
    file. It is not `len(snapshot["pages"])`, and the difference is not
    cosmetic: a crawl of sixteen URLs downloaded eight release archives and a
    patch file, all of which answered 200, and all nine were then counted as
    URLs the crawl "reached but could not read". The readable share came out
    at 7/16, under `READABLE_SHARE_FOR_ABSENCE`, and eight true findings -
    each of them checkable from `lang`, `og`, `jsonld` and `has_viewport` on
    the seven pages that *were* read - were held back as claims about the
    crawler's reception. Nothing had refused this crawler. Every one of the
    sixteen answered 200.

    So the rule is: a URL leaves the denominator only when the response itself
    proved it was never a page. A content type that is not HTML is that proof
    and is currently the only one. Everything else stays, because everything
    else is a page-shaped URL the audit asked for and did not get:
    a 403, a bot-manager challenge, a body this client could not decompress, a
    200 with nothing on it, a redirect off the origin. Those are exactly the
    cases the share exists to catch, and dropping them would disarm it.
    """
    out = []
    for page in snapshot.get("pages") or []:
        status = page.get("status")
        # The `skipped` string is the fallback for a snapshot written before
        # the flag existed, and it carries the same 2xx condition the flag
        # does: a refusal that answered with a JSON error body is a URL this
        # crawler was refused a page at, whatever the content type said.
        proved_not_a_page = (
            page.get("not_a_page")
            or (page.get("skipped") == _NOT_A_PAGE_REASON
                and status is not None and 200 <= status < 300))
        if not proved_not_a_page:
            out.append(page)
    return out


def readable_counts(snapshot):
    """(URLs the crawl reached, how many of them came back readable).

    One definition of "readable", `readable_text_pages` below, used by
    everything in this file that asks the question: holding back absence
    claims, stopping the verdict opening with "a machine can reach this site
    and read it" on a crawl where three of four URLs came back 403, and
    deciding whether the crawl saw enough to observe anything twice.

    "Came back readable" used to mean only that the response was a 200 that
    was not skipped and not a bot-manager challenge, which counts a page that
    answered perfectly and carried nothing. A crawl of two URLs, one of them a
    200 with no text at all, scored 0.5 readable and cleared the half-share bar
    below - so eight claims that the site never states its location, its
    contact method or its founding facts were published as defects about a
    crawl that had read no text whatsoever. A page nothing could be read from
    is not a page the site was read on, whatever its status code.
    """
    return len(page_shaped_urls(snapshot)), len(readable_text_pages(snapshot))


def readable_share(snapshot):
    """What share of the URLs the crawl reached could actually be read."""
    attempted, readable = readable_counts(snapshot)
    if not attempted:
        return None
    return readable / float(attempted)


# How many pages the audit has to have actually read before it may state a
# confident verdict about the site.
#
# Two, and the floor is on pages that came back with text, not on
# `crawl.pages_crawled`. Twice now the same failure has shipped: a crawl
# recorded one page, that page carried no readable text - once because the
# only URL redirected off the origin and was skipped, once because the
# response could not be decoded - and the verdict computed from the severity
# counts alone read "No blocking or significant problems were found". The
# audit read nothing and told the owner they were fine, which is the most
# damaging sentence this tool can produce.
#
# `pages_crawled` cannot carry this floor: it counted 1 on both of those runs.
# What separates them from every run that produced a defensible report is how
# many pages came back with something on them. Measured over one validation pass:
# the runs that produced usable reports read 11, 55, 57, 59 and 60 pages of
# text, and the two damaging ones read 0. Nothing observed sits between 0 and
# 11, so any floor in that gap separates them; two is the smallest number that
# also states the principle rather than a site size. One page is not a site,
# and `price_one_observation_once` a few functions below already treats a
# single readable page as too little to observe anything twice. Reading one
# page tells you about that page; the verdict is a claim about the site.
VERDICT_MIN_READABLE_PAGES = 2


def enough_to_grade(readable_pages, readable_share):
    """May this run grade the site at all? The one coverage rule.

    There were two, and they contradicted each other in the report. The
    verdict asked "did the audit read at least `VERDICT_MIN_READABLE_PAGES`",
    `withhold_absence_claims` asked "did it read at least
    `READABLE_SHARE_FOR_ABSENCE` of what it reached", and a crawl of sixteen
    URLs where seven came back readable answered no to the second and yes to
    the first. That report held back eight findings on the grounds that too
    little of the site had been read, and then printed "No blocking problems."
    Coverage cannot be too low to assert an absence and high enough to award a
    clean bill; a reader acting on either sentence is misled by the other.

    So one rule decides, and it is the conjunction: grading needs a sample
    (the count) *and* needs that sample to be most of what was reached (the
    share). Both parts still earn their place. The count alone let the crawl
    above through. The share alone would tell the owner of a one-page site
    that read cleanly that nothing could be established, which is why the
    share is not applied to absence claims as a count - see the comment on
    `READABLE_SHARE_FOR_ABSENCE`.

    `None` for either means the caller did not measure it, which keeps callers
    that exercise one branch of the ladder in isolation working. `compose`
    measures both.
    """
    if readable_pages is not None and readable_pages < VERDICT_MIN_READABLE_PAGES:
        return False
    return readable_share is None or readable_share >= READABLE_SHARE_FOR_ABSENCE


def readable_text_pages(snapshot):
    """Pages that answered 200, were not skipped or challenged, and had text.

    The text bar is `QUOTE_MIN_CHARS`, this file's existing answer to "how
    short may a passage be and still be worth quoting". A page delivering less
    than that delivered nothing an assistant could use and nothing this audit
    can grade, whatever status code it carried.
    """
    out = []
    for page in snapshot.get("pages") or []:
        if page.get("status") != 200 or page.get("skipped") or page.get("challenge"):
            continue
        if len((page.get("body_text") or "").strip()) < QUOTE_MIN_CHARS:
            continue
        out.append(page)
    return out


def why_little_was_read(snapshot):
    """A clause naming why the URLs the crawl reached came back unreadable.

    Generic by construction: it counts the reasons the crawl itself recorded
    rather than naming any site, platform or cause this file knows about, so a
    refusal shape nobody has seen yet is described as accurately as the two
    that have already done damage.
    """
    reasons = {}
    read = {id(page) for page in readable_text_pages(snapshot)}
    # The same set the share is computed over. Counting a release archive here
    # would make the clause explain a ratio it is not part of.
    for page in page_shaped_urls(snapshot):
        if id(page) in read:
            continue
        if page.get("skipped"):
            reason = str(page["skipped"])
        elif page.get("challenge"):
            reason = "answered with a bot-manager verification page"
        elif page.get("status") is None:
            # No status is no answer. "answered HTTP None" was printed about a
            # host whose TLS handshake failed before any response existed.
            reason = "did not answer"
        elif page.get("status") != 200:
            reason = "answered HTTP {}".format(page.get("status"))
        else:
            reason = "answered with no readable text"
        reasons[reason] = reasons.get(reason, 0) + 1
    if not reasons:
        return ""
    ordered = sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))[:2]
    # No count in front of a single URL: "1 redirects off this origin" is what
    # a template writes, not what a person reads.
    return "; ".join(reason if count == 1 else "{} {}".format(count, reason)
                     for reason, count in ordered)


# Whole-site scope, and what each phrase becomes once the claim is cut back to
# the pages the crawl read.
#
# The left-hand sides are the phrases `_SITE_WIDE_RE` already recognises as a
# sentence claiming the site rather than the sample. The right-hand sides are
# the ones that survive the cut with the same meaning: "No Organization or
# LocalBusiness markup anywhere on the site" says the same thing about the
# thirteen documents that were read when it says "on any of the 13 pages read",
# and it stops saying anything about the forty-six that were not.
#
# The phrases deliberately absent are the ones with no such rewrite. "The site
# never states its founding facts" cannot be cut back, because the fact only
# has to appear once and the about page nobody read may be where it appears -
# so restating it over the read pages would produce a true sentence that is not
# the defect. A wording this table does not know is held back, which is what
# every wording was before it existed.
_READ_SCOPE_REWRITES = (
    (re.compile(r"\banywhere on the site\b", re.I), "on any of the {read} pages read"),
    (re.compile(r"\bacross the site\b", re.I), "across the {read} pages read"),
    (re.compile(r"\bsite-wide\b", re.I), "on the {read} pages read"),
    (re.compile(r"\bon any crawled page\b", re.I), "on any of the {read} pages read"),
    (re.compile(r"\bno page\b", re.I), "none of the {read} pages read"),
)


def restate_against_the_pages_read(finding, read_urls, readable, attempted):
    """The same claim measured over the pages that were read, or None.

    Two things have to hold before an absence claim survives a thin crawl, and
    a finding that fails either has no restatement and is held back.

    First, every page the finding names has to be one the crawl read, and there
    has to be at least one. Those documents are the population the count was
    taken over, and nothing in a URL that answered 429 can change what is in
    them. A finding naming no read page measured nothing over the sample - "No
    FAQ markup anywhere on the site", attached to pages that all came back
    refused - so there is no population to restate it against.

    Every, not any, and the difference is a fixture. A site that delivers its
    text with JavaScript answers 200 on all five URLs and hands this crawler 13
    characters on the homepage, 37 on the about page and 13 on the work index.
    Two of the five clear `QUOTE_MIN_CHARS`, and the identity, wayfinding and
    Open Graph checks all count over the whole five, so their counts include
    three documents nobody read. Restating such a count over the read pages
    would print a number that was never taken over them - and the site behind
    that fixture publishes an Organization block and a stated location, both of
    which a browser sees and this crawler did not.

    Second, where the claim's own wording ranges over the whole site, that
    scope has to be replaceable by the read population - `_READ_SCOPE_REWRITES`
    above is the list of wordings where it can be, and why the rest cannot.
    This is the condition "the site never states its founding facts" fails: the
    check attaches the pages such a fact would live on, several of which were
    read, and the claim is still about a fact that only has to appear once.

    The evidence then carries the denominator: the read population by name, and
    the count that could not be read beside it, so a reader can see what the
    figure covers without going to the crawl table.
    """
    named = finding.get("affected_pages") or []
    if not named or any(url not in read_urls for url in named):
        return None
    title = finding.get("title") or ""
    if _SITE_WIDE_RE.search(title):
        for pattern, replacement in _READ_SCOPE_REWRITES:
            title = pattern.sub(replacement.format(read=readable), title)
        # A second scope this table has no rewrite for is still an unsupported
        # claim about the site, so the finding goes back to being held.
        if _SITE_WIDE_RE.search(title):
            return None
        title = title[:1].upper() + title[1:]
    restated = dict(finding, title=title)
    restated["evidence"] = (
        "{} Counted on the {} this crawl read: {} of the {} URLs it reached could not be "
        "read, so this figure covers those {} documents and not the rest of the site."
        .format((finding.get("evidence") or "").rstrip(), plural(readable, "page"),
                attempted - readable, attempted, readable).lstrip())
    return restated


def withhold_absence_claims(findings, snapshot):
    """Hold back "it is not there" when the crawler was not allowed to look.

    Returns the findings to report and the ones held back. A held-back finding
    is not deleted: it is shown under its own heading, as a question the audit
    could not answer and why, which is a different and more useful thing to
    tell a reader than a defect they do not have.

    Coverage decides whether this function runs. It does not decide what
    happens to a finding once it has. An independent review read a report on a
    site where 45 of its 59 URLs answered HTTP 429, and every one of these was
    withheld while being true of the 13 pages that were read: "47 images have
    no description at all", "13 pages do not declare a language", "3 of 3
    article pages have no Article markup", "No Organization or LocalBusiness
    markup anywhere on the site", "Heading structure does not describe the page
    reliably". `title-and-description` was published from those same 13 pages in
    the same report. Their diagnosis was that the gate "keys on crawl coverage
    instead of on whether a finding is a presence observation (safe to report)
    or an absence inference (not safe)".

    A thin crawl invalidates a claim that something is nowhere on the site,
    because the pages that would have carried it may be the unread ones. It
    does not invalidate a count of what was read: "13 of the 13 pages read
    declare no language" is a measurement of 13 documents whatever the other 46
    hold. So every claim is offered to `restate_against_the_pages_read` first,
    and only the ones with no such restatement are held back.
    """
    brand_name = (snapshot.get("brand") or {}).get("name") or ""
    attempted, readable = readable_counts(snapshot)
    if readable and readable >= attempted * READABLE_SHARE_FOR_ABSENCE:
        return findings, []
    # `not attempted` used to return early here, which published every absence
    # claim on a crawl that reached nothing at all - the one crawl that can
    # support none of them.
    if not attempted and not readable:
        why = "the crawl reached no page of this site"
    else:
        # What actually happened, counted off the crawl's own records rather
        # than asserted. The sentence used to name a refusal and a challenge
        # with no test for either: on a crawl where all sixteen URLs answered
        # 200 it told the reader the unread ones "were refused or challenged".
        # Nothing was refused. `why_little_was_read` counts the reasons the
        # crawl wrote down instead - the `skipped` sentence, which is where a
        # body that could not be decompressed says so; `challenge`; the status
        # code; and a 200 that carried no text - so a cause nobody has seen
        # yet is named as accurately as the two that have already done damage.
        why = "only {} of the {} URLs the crawl reached could be read ({})".format(
            readable, attempted,
            why_little_was_read(snapshot) or "no reason was recorded")

    keep, held = [], []
    read_urls = {page.get("url") for page in readable_text_pages(snapshot)}
    for finding in findings:
        # The access findings are the ones explaining the block. They are the
        # only thing the reader can act on, and several of them are phrased as
        # absences themselves ("the homepage does not return HTTP 200").
        if finding.get("mechanism") == "A" or not claims_absence(finding, brand_name):
            keep.append(finding)
            continue
        restated = restate_against_the_pages_read(
            finding, read_urls, readable, attempted)
        if restated is not None:
            keep.append(restated)
            continue
        held.append({
            "title": finding.get("title"),
            "detected_by": finding.get("detected_by"),
            # The check whose finding this was, so a consumer of `unverifiable`
            # can tie the held-back question back to what asked it. The
            # appendix no longer reads this: a finding held back is one its
            # skill raised and this report does not publish, which
            # `skills_whose_every_finding_is_published` sees by counting, and
            # counting also catches the siblings that fired with it - which
            # this single name never could.
            "check": finding.get("check"),
            "root_cause": finding.get("root_cause"),
            "reason": ("{}, so this is a fact about what this crawler was shown rather than "
                       "about the site. It is held back rather than reported as a "
                       "defect".format(why)),
        })
    return keep, held


def checks_left_unanswered(not_applicable, readable_pages, attempted):
    """Every check that declined, when the crawl gave it nothing to decline on.

    `withhold_absence_claims` above feeds the `unverifiable` channel from
    individual absence claims, which is one of the two ways this audit can end
    up with nothing to say. The other is the crawl itself failing, and nothing
    fed the channel for it: on a run that read one page of no text, 64 of 72
    checks came back not-applicable, `unverifiable` was an empty list, and the
    appendix presented those 64 declines as though each were a reasoned
    judgement about the site.

    When the crawl is below `VERDICT_MIN_READABLE_PAGES`, the premise every
    decline rests on - that there was a page to look at - did not hold, so no
    decline from that run is a statement about the site. They are echoed here
    as questions that went unanswered. Echoed, not moved: they stay in
    `not_applicable` so the appendix's four-bucket arithmetic still adds up.
    """
    if readable_pages >= VERDICT_MIN_READABLE_PAGES:
        return []
    # "it did not run" would be false for some of these. A few checks make
    # their own requests and decline on a real measurement; most decline
    # because the crawl handed them nothing. What is true of all of them is
    # that their silence rests on a crawl that read nothing, so that is what
    # the sentence says.
    return [{
        "title": item["check"],
        "check": item["check"],
        "detected_by": item.get("skill"),
        "reason": ("this check stayed quiet because {}. With {} of the {} URLs the crawl "
                   "reached coming back with any readable text, its silence is a fact about "
                   "what this crawler was shown rather than about the site, so it is listed "
                   "here instead of being counted as a pass".format(
                       item.get("reason") or "the crawl gave it nothing to look at",
                       readable_pages, attempted)),
    } for item in not_applicable]


# A finding's evidence already states its denominator when it contains one of
# these shapes: "3 of 60", "12/60", "48.0%".
_DENOMINATOR_RE = re.compile(
    # "3 of 12", "3 of the 12", "3/12", "25%" - the finding sizing itself
    # outright. `the` is allowed between, because a finding that wrote its own
    # scale in ordinary English got a second, identical sentence appended after
    # it: "found on 54 of the 60 pages crawled. Seen on 54 of the 60 pages this
    # crawl read."
    r"\b\d+\s*(?:of|/)\s*(?:the\s+)?\d+\b|\d+(?:\.\d+)?%"
    # "Checked 46 content page(s)", "across 12 product pages", "of 7 article
    # pages" - the finding naming the population it looked at, in words the
    # count-pair pattern does not see. Three verification agents ranked this
    # their top or second fix: the appended sentence read "Seen on 2 of the 60
    # pages this crawl read" one clause after the evidence said "Checked 46
    # content page(s). None declares...", contradicting it in the same
    # sentence and understating a site-wide gap by fifteen times.
    r"|\b(?:checked|examined|across|among|of)\s+\d{1,4}\s+[a-z-]*\s?page",
    re.I)

# How many pages a share has to be computed over before it may be printed as a
# share at all.
#
# Ten, because a reader can hold a fraction of ten in their head and check it
# against the crawl table; below that a percentage magnifies one page into a
# figure with a decimal point in it and hides the thing that would settle it.
# A real report printed "33.3% of crawled pages do not return HTTP 200" on a
# two-page site - one artefact, printed as a third of the site, with no way for the
# reader to see that it rests on a single URL. The same report has been caught
# printing a title that counts one set over evidence listing another; a share
# whose fraction is not recoverable is that defect one step further on.
#
# The rule is about how small the denominator is, not about which check wrote
# the sentence, so it also covers checks not yet written.
SHARE_NEEDS_A_POPULATION_OF = 10

# "48%", "33.3%", "7.25 %" - a share stated in the evidence. The trailing
# "of" is captured so the replacement can eat it: "33.3% of crawled pages"
# becomes "1 of the 3 crawled pages" rather than "1 of 3 of crawled pages".
_SHARE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%(\s+of\b)?")


def restate_a_share_as_a_count(evidence, count, population):
    """Turn "33.3%" into "1 of 3" where the fraction is small and provable.

    Only where the arithmetic checks out. The share is replaced when it equals
    `count / population` to the precision it was printed at, because that is
    the one case where this function knows what the percentage meant. Where it
    does not match, the share is left exactly as the check wrote it and the
    count sentence is appended after it instead - two numbers a reader can
    compare beats one number this file guessed at.

    Above `SHARE_NEEDS_A_POPULATION_OF` pages nothing is touched: a share over
    sixty pages is the more readable of the two.
    """
    if not population or population >= SHARE_NEEDS_A_POPULATION_OF:
        return evidence, False
    if not count or count > population:
        return evidence, False
    replaced = [False]

    def swap(match):
        printed = match.group(1)
        # Compare at the precision the check printed. "33.3%" and "33%" are
        # both 1 of 3 and both have to match, and rounding the true value to
        # the printed number of decimals is the only way to say so without
        # asserting a tolerance nothing measured.
        places = len(printed.split(".")[1]) if "." in printed else 0
        true_share = round(count * 100.0 / population, places)
        if abs(true_share - float(printed)) > 10 ** -(places + 6):
            return match.group(0)
        replaced[0] = True
        if match.group(2):
            return "{} of the {}".format(count, population)
        return "{} of {}".format(count, population)

    return _SHARE_RE.sub(swap, evidence), replaced[0]


def a_share_too_small_to_print(evidence, population):
    """True when the evidence prints a share over too few pages to be checked.

    Used to stop `_DENOMINATOR_RE` accepting such a share as the finding having
    already stated its scale. A percentage the reader cannot turn back into a
    fraction has not sized anything.
    """
    return (bool(_SHARE_RE.search(evidence or ""))
            and bool(population) and population < SHARE_NEEDS_A_POPULATION_OF)


def state_the_denominator(finding, pages_crawled, snapshot=None):
    """Append "on N of the M pages crawled" when the evidence names no scale.

    The second-largest class of wrong finding was a real observation sized
    wrongly by the reader, because the number that would have sized it was
    missing. "3 page(s) have no H1" on a site where fifteen did; "2 pages do
    not declare a language" where fifty-seven did not; "the site never states
    its pricing" from four pages of sixty. In each case the count was of what
    the check happened to look at, and nothing said so.

    Fixing them one at a time fixed three checks. Stating the scale of every
    finding, here, fixes the shape - including for checks not yet written,
    which is the only version of this that stays fixed.
    """
    evidence = finding.get("evidence") or ""
    # `affected_pages` is the display list and is capped at five. Counting it
    # made the sentence meant to fix understated scope understate scope itself:
    # seven findings across two sites read "Seen on 5 of the 60" when the true
    # reach was 6, 11, 15, 27, 32 and twice 60. `affected_page_count` is the
    # number that was not truncated.
    count = finding.get("affected_page_count")
    if count is None:
        count = len(finding.get("affected_pages") or [])
    if not count or not pages_crawled:
        return evidence
    # The denominator is the population the check looked at, not the size of
    # the crawl. A finding about article pages that says "7 of the 60 pages"
    # reads as a 12% problem when it is a 100% one, and an owner triaging by
    # that number does the wrong thing. Where every affected page is the same
    # kind of page, that kind is the population; where they are mixed, the
    # finding really is about the whole crawl.
    population, unit = _population_for(finding, snapshot, pages_crawled)
    # Read before the denominator test below, because a share over a handful
    # of pages is exactly what that test used to accept as the finding having
    # sized itself. "33.3% of crawled pages do not return HTTP 200" on a
    # two-page site is one artefact wearing a percentage, and the sentence that
    # would have shown the reader so was suppressed by the percent sign.
    if a_share_too_small_to_print(evidence, population):
        evidence, swapped = restate_a_share_as_a_count(evidence, count, population)
        # The share was the whole of the scale claim and is now a fraction, so
        # there is nothing left to append.
        if swapped:
            return evidence
        # The arithmetic did not check out, so the share stays as written and
        # the count sentence goes next to it - unless the evidence sizes
        # itself some other way as well, in which case it already has two
        # numbers and does not need a third.
        if _DENOMINATOR_RE.search(_SHARE_RE.sub("", evidence)):
            return evidence
    elif _DENOMINATOR_RE.search(evidence):
        return evidence
    # A count bigger than the population is not this sentence's count. A
    # heading check counts sections, an image check counts images, and both
    # attach the pages they were seen on - so "Seen on 60 of the 3 pages this
    # crawl read" reached report.md, sizing sixty sections against three pages.
    # Nothing here can tell what the sixty were, so the evidence stands as the
    # check wrote it rather than being sized against the wrong denominator.
    if count > population:
        return evidence
    # "Seen on" is the wrong verb for a finding that says something is not
    # there. A statistics charity was told "No page states in one sentence what
    # the brand is ... Seen on 3 of the 60 pages this crawl read" - seen where,
    # on which three?
    #
    # And for a claim about the whole site the numerator is wrong as well as
    # the verb. `affected_page_count` is the pages the finding is attached to,
    # which for "no page states X" is an arbitrary handful; the number that
    # means something is how many were examined, and for a site-wide claim that
    # is the population. A per-page absence - "these 3 pages have no H1" - is
    # genuinely about its three pages and keeps its own count.
    if finding.get("root_cause") in ABSENCE_CAUSES:
        if _SITE_WIDE_RE.search(evidence):
            return "{} Checked all {} {}.".format(
                evidence.rstrip(), population, unit)
        return "{} Checked {} of the {} {}.".format(
            evidence.rstrip(), count, population, unit)
    return "{} Seen on {} of the {} {}.".format(
        evidence.rstrip(), count, population, unit)


# Sentences that claim the whole site rather than the pages that were read.
_SITE_WIDE_RE = re.compile(
    r"anywhere on the site|across the site|site-wide|\bno page\b|\bnowhere\b"
    r"|the site never|never states|on any crawled page|any page", re.I)

# Below this the crawl is a fair sample of the site and the sentence would be
# noise. A crawl that read sixty of sixty-two pages has read the site.
COVERAGE_DISCLOSURE_RATIO = 2.0


def state_the_coverage(evidence, snapshot, pages_crawled):
    """Append how much of the site the crawl saw, to a claim about all of it.

    `state_the_denominator` sizes a finding against the pages this audit read.
    This sizes the pages it read against the site, which is a different number
    and the one a reader assumes when a sentence says "anywhere on the site".

    A global law firm's report said "No Organization or LocalBusiness markup
    anywhere on the site" and "too few pages carry an address to treat this as
    a business with separate places to visit". The crawl read 60 pages. The
    firm's own sitemap lists 2,692 lawyer profiles and its site lists 31
    offices in 26 countries, none of which the crawl reached. Both sentences
    were true of the sample and neither was true of the site.

    A gym chain's report was the same shape from the other end: 276 branch
    pages exist, the crawl reached one, and the report described per-location
    markup as though it had seen the chain.
    """
    if not evidence or not pages_crawled or not _SITE_WIDE_RE.search(evidence):
        return evidence
    scope = sitemap_scope(snapshot.get("sitemaps") if snapshot else None)
    listed = scope["urls_counted"]
    if not listed or listed < pages_crawled * COVERAGE_DISCLOSURE_RATIO:
        return evidence
    # Not `plural(pages_crawled, "of them")`: the helper appends an "s" for the
    # plural form, so every site with a sitemap bigger than its crawl budget
    # got "this crawl read 60 of thems" printed into report.md.
    return "{} The sitemap lists {}; this crawl read {} of them, so this describes the pages " \
           "it reached and not every page of the site.".format(
               evidence.rstrip(), sitemap_total_phrase(scope, "URLs"), pages_crawled)


def _population_for(finding, snapshot, pages_crawled):
    """(how many pages this check could have looked at, what to call them).

    The unit is pluralised against the population, not hardcoded: a
    single-page population gave "Seen on 1 of the 1 home pages this crawl
    read", which report.md prints verbatim to a marketing manager.
    """
    word = "page" if pages_crawled == 1 else "pages"
    types = _types_of(finding.get("affected_pages") or [], snapshot)
    if len(types) == 1 and snapshot is not None:
        page_type = next(iter(types))
        same = sum(1 for page in pages_of(snapshot)
                   if page.get("page_type") == page_type)
        if same:
            return same, "{} {} this crawl read".format(
                page_type, "page" if same == 1 else "pages")
    return pages_crawled, "{} this crawl read".format(word)


def _types_of(urls, snapshot):
    if snapshot is None:
        return set()
    by_url = {page.get("url"): page.get("page_type") for page in pages_of(snapshot)}
    return {by_url[url] for url in urls if by_url.get(url)}


# --------------------------------------------------------------------------
# The language a claim about words was read in
#
# A validation pass over six deliberately awkward sites found one pattern. Every
# wrong finding on a site that is not in English reduces to one move: a list of
# English words was run over the page text, nothing matched, and the report
# stated the result as a fact about the site.
#
#   a Norwegian site   prints "( Fra 07.11.2004)" under its own name and was
#                      told "no founding year appears in the page text or in
#                      the page markup". The founding vocabulary was `since`,
#                      `founded`, `established`.
#   a Thai temple      prints its article dates as 08/12/2561 and was told its
#                      pages carry "no date a reader or a machine could use".
#   a Japanese page    measured as one sentence, because the sentence splitter
#                      looks for a full stop and a following capital letter.
#
# Each of those checks has since been widened, and widening is not the rule
# this section exists for. The next list will be incomplete in the next
# language, and the report will say the same kind of thing again. What a reader
# cannot do today is *discount* it: told "no founding year appears in the page
# text", they have to take it or open the site. Told "no founding year appears
# in the page text, which was read as `no` in the latin script with word
# patterns written for English", they can discount it in a second.
#
# So the rule is not that the claim must be right. It is that a claim resting
# on reading prose must state what it read the prose as: no prose-derived
# claim ships without the script and language it was measured in being stated
# in the evidence.
#
# **Where the sentence goes, and where it does not.** Not into `checked`, even
# though `checked` is the list of places a finding looked and this is a limit
# on that looking. `len(checked)` is load-bearing twice - `published_tier`
# promotes a `medium` absence claim only once it names
# `CORROBORATED_ABSENCE_SOURCES` places, and `_render_finding` prints "that is
# one source, so this is reported at reduced confidence" at exactly one entry.
# Appending a sentence about language would have promoted every single-source
# prose absence on a non-English site into the confident tier and deleted its
# caveat, which is the opposite of the point. It is its own field, printed
# directly under "Where we looked" in both renderers and published as
# `read_in` in report.json so a consumer never has to parse the prose.
#
# **Never assert a language this audit does not know.** `detect_site_language`
# deliberately returns no `code` for a non-Latin script with nothing declared,
# because Cyrillic is Russian, Ukrainian, Bulgarian and Serbian, and printing a
# guessed code once put `<html lang="en">` in front of a site
# that is not in English. Where only the script is known this says only the
# script. The language is printed as the code the record holds rather than a
# language name: a code-to-name table is a second vocabulary that can be wrong
# about a site, and the fact the reader needs - that this is not the language
# the patterns were written for - is carried by the code alone.
# --------------------------------------------------------------------------

# Which findings the sentence is about.
#
# `evidence_basis`'s INFERRED set is close to the answer and is not the answer.
# INFERRED means "this claim only holds if the audit correctly decided what
# something is", and a good half of those decisions are made about markup, a
# link graph or a set of URLs, where nothing was read in any language and a
# sentence about language would be noise. Every entry below was read against
# the check that raises it rather than assumed from the cause's name:
#
#   the two are not the same question   `nap-inconsistency` is INFERRED and
#                                       compares a digit-normalised telephone
#                                       number in JSON-LD against a `tel:`
#                                       href. No word is read, so it is out.
#                                       `dead-end` is INFERRED and its "no call
#                                       to action" rests on `page_extract`'s
#                                       `CTA_VERBS`, an English verb list
#                                       matched against link labels, so it is
#                                       in - and that list is why a site whose
#                                       links read "Join the mailing list" and
#                                       "Read papers" was told it had none.
#
# Choosing the population counts. Where a finding reads "N of M product pages
# have no Product markup" and the M was chosen by matching "add to cart" and
# "free shipping" against the page text, the claim rests on prose however
# literal the tag it then counts.
#
# Measuring words counts too, not only matching them. `readability` and
# `long-sentences` match no vocabulary at all; they count words and sentences,
# and `tests/test_language.py` records a Hindi page measured at 3,541 words
# against a real 2,254 and a whole Japanese page measured as one sentence. A
# figure that moves by half with the script is a figure read in a script.
PROSE_DERIVED_CAUSES = frozenset({
    # The PDFs are chosen by matching "price", "tariff", "menu", "agenda",
    # "minutes" against the anchor text that links to them.
    "pdf-locked-facts",
    # Asserts a load-more control exists by matching a phrase list against the
    # page text; without that assertion there is no finding.
    "uncrawlable-pagination",
    # The Product-markup count is literal. Which pages are product pages comes
    # from a price shape plus `add to cart` / `in stock` / `free shipping` in
    # the page text.
    "no-product-schema",
    # Fires on pages whose H2s match a question pattern - `what`, `why`, `how`,
    # `can`, `do` - with an answer under them. The `@type` lookup is the
    # trivial half.
    "no-faq-schema",
    # One of its three emitters asks whether a name declared in JSON-LD appears
    # in the visible text, which is a search for a word in prose, and where a
    # word begins and ends is a property of the writing system.
    "schema-text-mismatch",
    # The definition matcher: the brand as the subject of "is a" or another
    # defining verb, in the opening of the homepage.
    "no-entity-definition",
    # Tests the first sentence of each section against a list of deferring
    # phrases - greetings, scene-setting, restating the question.
    "fluff-first",
    # Founding year and team come from English word lists (`founded`,
    # `established`, `our team`, `led by`) matched sentence by sentence. This
    # is how `( Fra 07.11.2004)` under a site's own name was reported absent.
    "missing-core-fact",
    # A sentence-length distribution, so both units - where a sentence ends,
    # what counts as a word - are decided by the script.
    "long-sentences",
    # Five emitters share this cause. Three read prose: the nav labels are
    # scored against a generic-label word list, the landing-page emitters match
    # delivery and returns phrase lists against the page text, and the homepage
    # emitter's call to action is `CTA_VERBS`. The one that reads only
    # attributes is excluded by name below.
    "no-orientation",
    # `CTA_VERBS` again, and it is the half of the claim a reader disputes.
    "dead-end",
    # Tokenises the title into content words and measures what share of them
    # reappear in the body. Word segmentation, in both directions.
    "title-body-drift",
    # Words per subheading and characters per paragraph. No vocabulary, and
    # still a measurement of prose in whatever script it is written in.
    "readability",
})

# The other half of the INFERRED set: claims that rest on this audit having
# decided what something is, where the deciding was done over markup, URLs, a
# link graph or an off-site API rather than over words. A sentence about
# language on any of these would be a sentence about nothing.
NOT_PROSE_DERIVED_CAUSES = frozenset({
    # Counts `<img>` elements, `alt` and `figcaption` presence, and the page's
    # character count. No word is read.
    "image-locked-facts",
    # `<video>`, `<audio>` and `<track>` element counts. Its one phrase list
    # only exempts a page from the finding.
    "no-transcript",
    # Counts non-video, non-map `<iframe>` elements against a byte floor.
    "iframe-content",
    # Typed from the URL slug, a dated-permalink shape and JSON-LD `@type`.
    "no-article-schema",
    # Reads a search form's `action` and query field, and the homepage's
    # `WebSite` node.
    "no-website-schema",
    # Compares the name strings the site declares - Organization `name`,
    # `og:site_name`, the title suffix, logo alt text - against each other.
    # Declared values, not prose.
    "name-inconsistency",
    # Counts distinct off-site platforms in outbound link hosts and `sameAs`.
    "weak-corroboration",
    # Sends the brand name to an off-site API and compares the labels that come
    # back. The site's own text never enters it.
    "entity-ambiguity",
    # Digit-normalised phone numbers and whitespace-normalised postcodes,
    # markup against `tel:` hrefs.
    "nap-inconsistency",
    # Compares header and footer link sets between pages.
    "inconsistent-chrome",
    # Element presence, a link to the page's own parent path, and URL depth.
    "no-breadcrumbs",
    # Fires only on a blocking overlay in the delivered markup, and explicitly
    # refuses to read component names as a signal.
    "intrusive-interstitial",
    # Counts fields and required fields on a form element.
    "form-friction",
})

# Where one cause is raised by checks that do not read the same thing, keyed on
# `id_hint` exactly as `EVIDENCE_BASIS_BY_FINDING` is. Short on purpose: every
# entry is a claim that the cause-level answer is wrong for one check.
PROSE_DERIVED_BY_FINDING = frozenset({
    # Raises `no-org-schema`, which is OBSERVED at cause level because it
    # counts JSON-LD nodes over every crawled page. This one counts only the
    # pages a street-shaped regular expression fired on, and then decides the
    # street is the site's own from address labels, first-person wording or the
    # brand name in the surrounding sentence.
    "branch-pages-carry-no-location-markup",
    # Raises `heading-structure`, which is an H1 being present. This grades an
    # H2's vocabulary against the rest of the page text.
    "headings-are-slogans",
    # Raises `stale-content`, which is date arithmetic. This reads sentences
    # and decides they claim the copy is current as of a year.
    "copy-references-an-old-year",
    # The one finding on a whole validation population where the sentence
    # would have warned the reader. Raises
    # `no-date-signal`, and its first named source is "dates printed in the
    # visible text of each page" - `page_extract.DATE_TEXT_RE`, whose month
    # names are `Jan`..`Dec` plus the year-month-day shapes written with CJK
    # and Thai units. A page whose date is written in a month vocabulary this
    # audit does not hold is a page this finding calls dateless, which is
    # exactly the dateless-page defect seen before. The `<time>` and
    # `datePublished` half is literal;
    # the finding needs *both* halves empty, so the prose half decides it -
    # which is the reading `audit_common.ABSENCE_DECIDED_BY_A_VOCABULARY`
    # already applies to this same cause, in its own words: "the visible-text
    # half is a list of formats, and the finding is one claim over both, so the
    # weaker half governs it".
    "content-pages-carry-no-date",
})

NOT_PROSE_DERIVED_BY_FINDING = frozenset({
    # Counts distinct <h1> elements on one page. Its cause,
    # `no-orientation`, is prose-derived because most of its emitters read
    # words; this one reads elements, and would otherwise carry the "what
    # language this was read in" sentence on a non-English site - which is
    # exactly the defect the entries above describe, seen again later.
    "page-declares-many-subjects",
    # Also `stale-content`. Reads `datePublished`, `<time>` and sitemap
    # `<lastmod>` over pages typed from their URLs.
    "published-content-has-gone-stale",
    # The one emitter under `no-orientation` that reads no words: an element
    # carrying `aria-hidden` or `hidden`. Named here so a prose cause does not
    # drag a markup claim along with it.
    "primary-navigation-is-hidden-from-crawlers",
    # Raises `no-orientation`, a prose cause, and the sentence was found
    # under a finding whose measurement is "links inside the
    # delivered <nav>, <header> or role=navigation markup" - which returned
    # zero elements, so no word pattern was applied to anything.
    #
    # The check does hold two English word lists, and neither can reach the
    # population this sentence prints for. `read_in` is empty wherever the
    # audit established the site is written in the language the patterns are
    # written for, so every report carrying it is a report where the site's
    # words are not English - and `GENERIC_NAV_LABELS` and `NAV_CONTROL_LABELS`
    # are matched as whole lower-cased labels, so on those sites they match
    # nothing, remove nothing, and the published claim is a count of elements.
    "primary-navigation-does-not-guide",
    # The earlier case the entries above repeat: `<meta name="viewport">`
    # is present in the delivered <head> or it is not. It raises
    # `no-orientation` with its four prose siblings and there is no vocabulary
    # anywhere in it.
    "missing-mobile-viewport",
})


def prose_derived(finding):
    """Does this finding's claim rest on reading the page's words?

    Same shape as `evidence_basis`, and for the same reason: the check first,
    because two checks can share a cause and not share what they read, then the
    cause. A cause nobody has classified falls back to the evidence basis,
    which defaults to INFERRED - so an unclassified check says where it looked
    rather than saying nothing, and `test_language_of_a_prose_claim.py` fails
    the build until somebody classifies it either way.
    """
    hint = finding.get("id_hint") or finding.get("stable_id")
    if hint in PROSE_DERIVED_BY_FINDING:
        return True
    if hint in NOT_PROSE_DERIVED_BY_FINDING:
        return False
    cause = finding.get("root_cause")
    if cause in PROSE_DERIVED_CAUSES:
        return True
    if cause in NOT_PROSE_DERIVED_CAUSES:
        return False
    return evidence_basis(finding) != OBSERVED


def _declaration_is_contradicted(code, script):
    """Does the writing system rule out the language the record names?

    `detect_site_language` keeps a declared code even when the letters
    contradict it, and says so in `source`. This is the same question asked as
    a boolean, so the sentence below can lead with the script - which is what
    is actually known - instead of printing "read as `en`" over Arabic text.
    """
    expected = LANGUAGE_SCRIPTS.get(code or "")
    return bool(expected and script and script not in expected)


def language_note(language):
    """One sentence naming the script and language prose was read in, or "".

    Empty on a site this audit established is written in the language the word
    patterns are written for. That is the case where the sentence carries no
    information, and a report already long enough to draw complaints may not
    spend forty lines saying "read in English" on an English site.
    """
    if not isinstance(language, dict):
        return ""
    code = language.get("code") or ""
    script = language.get("script") or ""
    source = language.get("source") or ""
    if code == PROSE_LANGUAGE and language.get("prose_checks_apply"):
        return ""
    # `source` is already written as a phrase for printing - "declared",
    # "inferred from the page text", "declared as en, contradicted by page text
    # written in the arabic script" - so it is printed rather than reworded.
    said = source or "no declaration and no reading recorded"
    tail = " Those patterns are written for English (`{}`).".format(PROSE_LANGUAGE)
    if code and not _declaration_is_contradicted(code, script):
        where = ("read as `{}` in the {} script".format(code, script) if script
                 else "read as `{}`".format(code))
        return ("This finding comes from matching word patterns against the page text, "
                "which was {} ({}).{}".format(where, said, tail))
    if script:
        # Either only the script is known, or a code is known and the letters
        # say it is wrong. Both print the script and no language, for the one
        # reason: this audit cannot name the language, and naming one anyway
        # once reached a site owner as an instruction.
        return ("This finding comes from matching word patterns against the page text, "
                "which is written in the {} script; this audit did not establish which "
                "language that is ({}).{}".format(script, said, tail))
    return ("This finding comes from matching word patterns against the page text, and "
            "this audit established neither the language nor the writing system it is "
            "in ({}).{}".format(said, tail))


def reconcile_the_example_with_the_claim(finding):
    """Keep the per-page list and the count it is printed under in agreement.

    This used to read a number out of the evidence prose and throw the whole
    per-page list away whenever the two differed. Three of four audited sites
    lost a correct list to it. One finding counted 67 unheaded *sections*
    across 9 pages, and the 9 pages - the only part of the finding an owner
    could click - were suppressed because 67 is not 9. Another counted 2
    offending pages and listed 40, and the 40 were discarded. A number in a
    sentence is not a page count and cannot be used as one.

    The only two numbers that are both about pages are the finding's own
    `affected_page_count` field and the length of `affected_pages`, and
    `affected_pages` is truncated to five for display, so a count larger than
    the list is the normal, expected case. The list is trusted: where the
    field claims fewer pages than the list actually holds, the field is the
    one that is wrong and is corrected to the length of the list.

    Returns "" always now, and keeps the signature because callers record it.
    """
    pages = finding.get("affected_pages") or []
    count = finding.get("affected_page_count")
    if pages and isinstance(count, int) and 0 < count < len(pages):
        finding["affected_page_count"] = len(pages)
    return ""

# Values a snippet may contain without the site having to show them: the
# vocabulary of the format itself, and the shape of a placeholder.
_SCHEMA_VOCABULARY = frozenset({
    "https://schema.org", "http://schema.org", "organization", "localbusiness",
    "postaladdress", "product", "productgroup", "offer", "aggregateoffer",
    "article", "blogposting", "newsarticle", "faqpage", "question", "answer",
    "breadcrumblist", "listitem", "website", "searchaction", "entrypoint",
    "imageobject", "person", "https://schema.org/instock",
    "https://schema.org/outofstock", "instock", "outofstock", "gbp", "usd",
    "eur", "true", "false",
    # `query-input` has exactly one legal shape, fixed by schema.org and not
    # read off the site at all. Checked as though it were a fact about the
    # brand, it was never found - so a snippet whose sentence one line above
    # says "the target and query field this page's own search form declares"
    # was immediately followed by "2 values could not be found anywhere on this
    # site". Both cannot be true, and the reader has no way to know which is.
    "required name=search_term_string",
})

# A template token in a value the site could not have written out. The
# SearchAction target is assembled here - the site's own form action, plus the
# placeholder a consumer substitutes into - so the whole string appears nowhere
# on the site by construction, while the part that came from the site does.
_TEMPLATE_TOKEN_RE = re.compile(r"[?&]?[\w.-]*=?\{[a-z_]+\}", re.I)
# `"key": <value>`, where the value is one of the three shapes JSON-LD snippets
# actually carry. Only the first was ever matched, so `"price": 90` and every
# element of `"sameAs": [...]` went into a paste-ready block unchecked - both of
# them named in `hold_back_invented_values`'s own docstring as the defects it
# exists to stop. The array alternative refuses `{` and `}` on purpose: an array
# of objects would hand this the objects' own field names as if they were
# values read off the site.
_SNIPPET_VALUE_RE = re.compile(
    r'"([^"\\]{2,120})"\s*:\s*'
    r'(?:"([^"\\]{1,200})"'
    r'|(\[[^\[\]{}]{0,2000}\])'
    # No comma inside the number: a greedy `[\d.,]*` swallowed the comma that
    # separates `"price": 90,` from the next field, and the replacement then
    # left the object without it, so a snippet that had one invented value came
    # out as JSON that will not parse.
    r'|(-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?|true|false|null)(?=\s*[,}\]\n]|$))')
_ARRAY_ELEMENT_RE = re.compile(r'"([^"\\]{1,200})"')

# Fields whose value is a claim about the world, checked however short it is.
# A price, a postcode or a phone number is exactly the kind of value that is
# both short and damaging to get wrong.
_ALWAYS_VERIFY = frozenset({
    "price", "lowprice", "highprice", "pricecurrency", "postalcode",
    "telephone", "streetaddress", "addresslocality", "addressregion",
    "addresscountry", "sku", "gtin", "email", "faxnumber", "vatid",
    "foundingdate", "datepublished", "datemodified",
})


def observed_values(snapshot):
    """Everything this crawl actually saw, as one lowercase haystack.

    Read from the page record whole, not from a list of field names. The list
    was maintained by hand, and every check that started reading a new field
    had to remember to add it here - which is a thing nobody remembers.

    It had already been extended once, for a telephone number published as
    `tel:+442072323010` and never written that way in the visible text: the
    guard called a real number a guess and replaced it with a placeholder,
    which is the same defect as inventing one, pointing the other way.

    Then `forms` was added to the crawl, and the SearchAction snippet began
    reading the target and query field off the page's own search form - and
    four sites in one validation pass were told, in the sentence after being told the
    values came from their own form, that those values "could not be found
    anywhere on this site". A museum's `action=/MUSEUM/contents/search.do`
    with `name=query`, a research society's `/suche` with `name=searchfield`,
    a library's `x23.xml` with `name=terms`: all correct, all read off the
    page, all replaced with `<fill this in>`.

    So the rule is now the one the guard always meant: anything the crawl
    captured is something the site showed. A field added next month is
    covered without anyone editing this function.
    """
    parts = [str(snapshot.get("origin") or ""), str(snapshot.get("site") or "")]
    brand = snapshot.get("brand") or {}
    parts.extend(str(v) for v in (brand.get("name"), brand.get("host")) if v)
    for page in snapshot.get("pages") or []:
        _collect_strings(page, parts)
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


# Deep enough for the nested records the crawl writes (a page holds `links`,
# which holds `external`, which holds dicts), and bounded so a cyclic or
# pathological structure cannot spin here.
_COLLECT_MAX_DEPTH = 8


def _collect_strings(value, out, depth=0):
    """Append every string anywhere inside a crawl record."""
    if depth > _COLLECT_MAX_DEPTH:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_strings(item, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_strings(item, out, depth + 1)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        out.append(str(value))


# Profile addresses this run fetched and found dead, as a set.
#
# One skill probes the brand's off-site profile links and reports the ones that
# 404; a different skill builds the paste-ready `Organization` block and puts
# every profile it found into `sameAs`. Neither can see the other: the crawl
# records no status for an off-site link, and the sub-skill that measures it
# runs two places later in the sequence and writes to its own file.
#
# So a school's report told it, in one finding, that a channel URL "returns
# HTTP 404" and that "a `sameAs` target that returns 404 is a claim the site
# makes that does not check out - delete the entry", and in a snippet five
# sections above, to publish that exact URL. Two instructions, opposite, one
# document.
#
# Here is the only place that holds every skill's findings and every skill's
# snippets at once, which is why the filter lives beside
# `hold_back_invented_values` rather than in either skill.
def profile_addresses_that_do_not_resolve(findings):
    dead = set()
    for finding in findings or []:
        if finding.get("root_cause") == "dead-profile-link":
            dead.update(finding.get("affected_pages") or [])
    return dead


def drop_dead_profile_links(snippet, dead):
    """`(snippet, dropped)` - the block with dead `sameAs` entries removed.

    Textual, on the snippet's own lines, because a snippet is emitted as text
    and reparsing it as JSON to edit one array would reformat a block whose
    exact shape is what a reader pastes. A `sameAs` entry is one address on one
    line; dropping the line and mending the comma on the line before it is the
    whole edit.
    """
    if not snippet or not dead:
        return snippet, []
    lines, out, dropped = snippet.split("\n"), [], []
    for line in lines:
        hit = next((url for url in dead if '"{}"'.format(url) in line), None)
        if hit is None:
            out.append(line)
            continue
        dropped.append(hit)
        # The entry before this one now ends the array, so it may not keep its
        # comma - a trailing comma is the one thing that stops JSON-LD parsing,
        # and this block exists to be pasted into a page.
        if out and out[-1].rstrip().endswith(","):
            out[-1] = out[-1].rstrip()[:-1]
    return "\n".join(out), dropped


# The shortest value, whitespace removed, that may match without regard to
# spacing. See `hold_back_invented_values`.
WHITESPACE_BLIND_MIN = 12

# Keys whose value is a schema.org type or vocabulary address rather than a
# fact about the site, and the shape such a value has: a type name written the
# way schema.org writes them, or that name under the schema.org address.
_TYPE_VALUED_KEYS = frozenset({"@type", "@context", "additionalType"})
_SCHEMA_TYPE_RE = re.compile(r"^(?:https?://schema\.org/?)?(?:[A-Z][A-Za-z0-9]{1,60})?$")

_SQUEEZED = {}


def _without_whitespace(text):
    """`text` with every whitespace run removed, kept for the last text asked."""
    if text not in _SQUEEZED:
        _SQUEEZED.clear()
        _SQUEEZED[text] = re.sub(r"\s+", "", text)
    return _SQUEEZED[text]


def hold_back_invented_values(snippet, haystack):
    """Replace any value in a paste-ready snippet the site never showed.

    This is the class of defect that does real damage, because a snippet is the
    part of a report a reader copies rather than reads. What reached real
    reports: a streetAddress of "1 million row" taken from a sentence about
    database rows; a postal code of "00005" taken from the price
    "$0.00005 / event"; another taken from a cosmetic ingredient's colour-index
    code; a $90 price on a $10 product; `sameAs` claiming a co-founder's
    personal account and an encyclopedia article about ACID transactions; a
    shop's own name spelt without its apostrophe.

    Each was fixed at its source, and each source was a different function. The
    property they violate is one property: a snippet may only contain what the
    audit observed. Enforced here, once, over every snippet any check produces
    now or later - so the next extractor that guesses wrong produces a
    placeholder and a note, not a fact the owner publishes.
    """
    if not snippet:
        return snippet, []

    invented = []

    def observed(key, value):
        """Did the crawl see this value? None means "not a value to check"."""
        stripped = value.strip()
        if not stripped:
            return True
        # A placeholder is already honest about being one.
        if "<" in stripped and ">" in stripped:
            return True
        low = stripped.lower()
        if low in _SCHEMA_VOCABULARY or low.startswith("@"):
            return True
        # A schema.org type is vocabulary, not a fact the site states. The
        # audit chose it from what it measured - "this site reads as a software
        # project, so the block below declares the program itself as a
        # `SoftwareApplication`" - and the guard then replaced it with
        # `<@type - not found on the site, fill this in>` two lines under that
        # sentence, because no site writes its own type anywhere. Exempt by
        # key and by shape, at any depth of the block, so a nested node's type
        # passes too and a name in a type-valued key does not.
        if key in _TYPE_VALUED_KEYS and _SCHEMA_TYPE_RE.match(stripped):
            return True
        squeezed_haystack = _without_whitespace(haystack)
        # A value carrying a `{token}` is part site and part format. Verify the
        # part the site supplied and let the token stand: it is a placeholder,
        # which is the very thing this function replaces values with.
        if "{" in low and "}" in low:
            low = _TEMPLATE_TOKEN_RE.sub("", low).rstrip("?&").strip()
            if not low:
                return True
            # Assembled from two observed strings, and so never observed as
            # one. A search target is built from the form's `action` and its
            # query field name: `https://site.test/?s=` appears nowhere on the
            # site, because the site publishes `action="https://site.test/"`
            # and `name="s"` separately. Checking the join replaced a correct
            # target with a placeholder on every site that has a search form,
            # while the finding's own sentence said the target came from the
            # site's form - the report contradicting itself inside one finding.
            # So a URL with a query is verified the way it was built: the
            # address, then each field name.
            if "?" in low:
                address, _, query = low.partition("?")
                pieces = [address, address.rstrip("/")]
                if not any(piece and piece in haystack for piece in pieces):
                    return False
                fields = [pair.partition("=")[0].strip()
                          for pair in query.split("&") if pair.strip()]
                return all(not field or field in haystack for field in fields)
        # Substring matching is how a fabricated value passes. `"price": "1"`
        # went into a snippet as fact, with no warning, because the character
        # "1" occurs in every page of every site - inside a year, a phone
        # number, an address. That is the exact failure this function exists to
        # stop, so the fields that carry a claim about the world have to match
        # as a whole token, not as a fragment of a longer one.
        if key.lower() in _ALWAYS_VERIFY:
            return bool(re.search(
                r"(?<![\w.]){}(?![\w])".format(re.escape(low)), haystack))
        if low in haystack or low.rstrip("/") in haystack:
            return True
        # Spacing is not a value. A national library's meta description is
        # written in Japanese, which puts no space after "。"; the snippet
        # built from it joined its sentences with one, and the guard replaced
        # the library's own description with a placeholder saying it "could
        # not be found anywhere on this site". A long enough value that
        # matches once whitespace is removed from both sides was observed.
        # Not for the fields above, which match as whole tokens, and not for
        # short values, where removing a space can join two unrelated words.
        squeezed = re.sub(r"\s+", "", low)
        return len(squeezed) >= WHITESPACE_BLIND_MIN and squeezed in squeezed_haystack

    def placeholder(key):
        return '"<{} - not found on the site, fill this in>"'.format(key)

    def replace(match):
        key, quoted, array, bare = match.group(1), match.group(2), match.group(3), match.group(4)
        if quoted is not None:
            if observed(key, quoted):
                return match.group(0)
            invented.append("{}={!r}".format(key, truncate(quoted.strip(), 40)))
            return '"{}": {}'.format(key, placeholder(key))
        if array is not None:
            # `sameAs` is the field that claimed a co-founder's personal
            # account and an encyclopedia article about ACID transactions.
            # Every element is a separate claim, so every element is checked
            # separately and only the unobserved ones are replaced.
            def replace_element(element):
                text = element.group(1)
                if observed(key, text):
                    return element.group(0)
                invented.append("{}={!r}".format(key, truncate(text.strip(), 40)))
                return placeholder(key)
            return '"{}": {}'.format(key, _ARRAY_ELEMENT_RE.sub(replace_element, array))
        # An unquoted scalar. `"price": 90` on a ten-pound product passed
        # through untouched because the pattern only ever saw quoted strings.
        # The replacement is a quoted placeholder, which changes the JSON type:
        # that is the point. A placeholder is not a number and must not look
        # like one that could be published as it stands.
        if observed(key, bare):
            return match.group(0)
        invented.append("{}={!r}".format(key, truncate(bare.strip(), 40)))
        return '"{}": {}'.format(key, placeholder(key))

    return _SNIPPET_VALUE_RE.sub(replace, snippet), invented


def _snippet_warning(invented):
    """The note printed above a snippet that had a value replaced.

    "1 value(s) below" is a note from a programmer to themselves, and this line
    sits directly above the block a marketing manager is about to copy, which
    is the worst place in the report to make the reader decode anything.
    """
    return ("{} below could not be found anywhere on this site and {} replaced with a "
            "placeholder ({}). Something guessed {}; fill {} in yourself rather than "
            "publishing a guess.".format(
                plural(len(invented), "value", "values"),
                "was" if len(invented) == 1 else "were",
                ", ".join(sorted(invented)[:4]),
                "it" if len(invented) == 1 else "them",
                "it" if len(invented) == 1 else "them"))


def price_one_observation_once(findings, snapshot):
    """One blocked request is one problem, however many checks noticed it.

    A bank's homepage refused the crawler once. The report carried two
    critical findings about that single fetch - one from the challenge-page
    check, one from the user-agent comparison - each with its own "do first"
    and its own several-days estimate, and a summary line reading "2 critical
    problems". A site whose homepage is blocked has one problem.

    The rule is general and does not name any check: when the crawl read fewer
    pages than it takes to observe anything twice, the access findings are all
    describing the same event, so the first stands as written and the rest are
    folded into it as corroboration.
    """
    # The same definition of "readable" the rest of this file uses. This had a
    # third one inline - a 200 that was not skipped, not even excluding a
    # bot-manager challenge - so on the sites where an edge answers every
    # request with a verification page, the count said the crawl had read
    # plenty and the access findings were left un-folded.
    _, readable = readable_counts(snapshot)
    if readable > 1:
        return findings

    access = [f for f in findings if f.get("mechanism") == "A"]
    if len(access) < 2:
        return findings

    primary, rest = access[0], access[1:]
    primary["evidence"] = "{} The same refusal was reached independently by {}.".format(
        primary["evidence"].rstrip(),
        ", ".join(sorted({f.get("detected_by") or "another check" for f in rest})))
    keep = []
    for finding in findings:
        if finding in rest:
            continue
        keep.append(finding)
    return keep


def score(finding, pages_crawled):
    """priority = severity weight x reach / effort.

    Reach is the share of crawled pages the finding touches, floored at 0.25 so
    a critical problem on one page still outranks cosmetic issues everywhere.
    A finding with no attributed pages is site-wide by construction (robots,
    entity ambiguity, boilerplate) and gets full reach.
    """
    count = finding.get("affected_page_count", 0)
    if count == 0:
        reach = 1.0
    else:
        reach = max(0.25, min(1.0, count / float(max(pages_crawled, 1))))
    weight = SEVERITY_WEIGHT[finding["severity"]]
    effort = EFFORT_DIVISOR[finding["suggested_action"]["effort"]]
    return round(weight * reach / effort, 3), round(reach, 3)


# However cheap and however narrow, a severe problem is not a "when
# convenient". The score decides the ordering; the label is an instruction to a
# person, and reach was overwhelming severity in it. A `high` finding on one
# page of seven scores 0.5 and read as "schedule", while a `low` one on every
# page scores 1.0 and read as "do soon" - so the report told the owner to do
# the small thing before the serious one, directly under headings that said the
# opposite.
PRIORITY_FLOOR = {"critical": "do first", "high": "do soon", "medium": "schedule"}
_LABEL_ORDER = ["do first", "do soon", "schedule", "when convenient"]


def priority_label(value, severity=None):
    label = "when convenient"
    for threshold, candidate in PRIORITY_BANDS:
        if value >= threshold:
            label = candidate
            break
    floor = PRIORITY_FLOOR.get(severity)
    if floor and _LABEL_ORDER.index(label) > _LABEL_ORDER.index(floor):
        return floor
    return label


def first_affected_url(finding):
    """The lowest of a finding's affected pages, or "" for a site-wide one.

    Sorted rather than taken as written. Two runs can attach the same pages in
    a different order, and a name that moves with page order would be the
    defect this whole block exists to remove, one level down.
    """
    pages = sorted(str(p) for p in (finding.get("affected_pages") or []) if p)
    return pages[0] if pages else ""


def _url_tail(url):
    """The path of a URL, for telling two findings of one check apart.

    The path and not the host: every page in one report is on one site, so the
    host is the same forty characters in front of every name it would be used
    in. A query string is kept where there is one, because on a shop that is
    frequently the whole difference between two pages.
    """
    text = str(url or "")
    without_scheme = text.split("://", 1)[-1]
    slash = without_scheme.find("/")
    return without_scheme[slash:] if slash >= 0 else "/"


def stable_names(ordered):
    """Give every finding a `stable_id` that names it and is unique here.

    `id` is a position. One site run twice gave 16 findings and then 19, and
    F-014 was sitemap-excludes-private-paths in run A and article-markup in
    run B - every id after the first insertion referred to a different
    problem. `crawl.py` now produces one page set per site, which removes the
    cause; this is the second lock, and it is a
    different question from the first. Two ids are needed because two people
    need them: F-014 is what somebody reads aloud to a colleague and has to be
    short, and a name that says which problem is what a developer diffs two
    runs on. A digest of the check and the URL would be stable and unique and
    nobody can read one out.

    So the display id stays sequential - `tests/test_runtime.py` requires
    F-001 upward with no gaps, and `start_here`, `worth_checking` and
    `leads_the_verdict` are lists of it - and the identity moves to
    `stable_id`, which the report already published and which nothing
    guaranteed. Two gaps had to be closed for it to be an id at all:

      it was not unique   `stable_id` was `id_hint`, the check's own name for
                          what it found. A check that fires per page raises
                          two findings under one hint, and a name that points
                          at two problems is the same defect one level down.
                          Where a hint is shared, the first affected URL's
                          path separates them - `check + first affected URL`,
                          appended rather than
                          hashed, so `article-markup` stays readable and
                          `article-markup@/blog/two` still is.
      it was not printed  it existed in report.json and in neither rendered
                          document, so the reader who was comparing two runs
                          by eye had nothing to compare on. Both renderers now
                          print it beside the F-number.

    Two findings sharing a hint *and* a first page are genuinely
    indistinguishable from the outside, and they get a numeric suffix in the
    order this list is already in. That is positional again, deliberately and
    at the smallest possible scale: it separates two rows nothing else can
    separate, rather than naming every row in the report.
    """
    by_hint = {}
    for finding in ordered:
        by_hint.setdefault(finding["id_hint"], []).append(finding)
    for hint, group in by_hint.items():
        if len(group) == 1:
            group[0]["stable_id"] = hint
            continue
        used = {}
        for finding in group:
            name = hint
            url = first_affected_url(finding)
            if url:
                name = "{}@{}".format(hint, _url_tail(url))
            seen = used.get(name, 0) + 1
            used[name] = seen
            finding["stable_id"] = name if seen == 1 else "{}#{}".format(name, seen)
    return ordered


def assign_ids(findings):
    """Sequential display ids, and a name that does not move, for each finding.

    Sorted by severity, mechanism, id_hint and then the first affected URL,
    and deliberately not by priority, so a finding keeps the same position
    when a later crawl changes how many pages it touches.

    The first affected URL joins the sort key for the same reason it joins the
    name below: without it, two findings a check raised under one hint sorted
    equal and their order fell through to the order the skills' JSON happened
    to arrive in, which is one more thing that can differ between two runs of
    one site.
    """
    ordered = sorted(findings, key=lambda f: (
        SEVERITY_RANK[f["severity"]], f["mechanism"], f["id_hint"],
        first_affected_url(f)))
    for index, finding in enumerate(ordered, start=1):
        finding["id"] = "F-{:03d}".format(index)
    return stable_names(ordered)


# Printed once, above the first finding, in both documents. The F-number is
# the handle a reader says out loud and the name is the handle a second run is
# compared on, and a reader who is not told which is which will compare on the
# number - which is exactly what happened, and what produced the defect.
#
# No markdown emphasis: report.html escapes what it prints, so a pair of
# asterisks would reach that reader as asterisks.
ID_NOTE = (
    "Each finding below carries two labels. F-001, F-002 and so on are positions in this "
    "report: they run in order and they move when the report changes, so the same F-number "
    "in a later audit is usually a different problem. The short name printed beside it is "
    "the problem's own name and does not move - quote that one when you compare two audits "
    "of this site, or when you tell us a finding is wrong."
)


# --------------------------------------------------------------------------
# One page, one answer
#
# Four validation passes turned up four pairs of published findings asserting
# opposite things about one page:
#
#   "no page defines the brand" beside "the opening text does explain what
#   this is", about one homepage;
#   one page called "1 page of questions and answers" by one finding and "too
#   little text to be quoted" by another;
#   a shell finding denying a page carries "no state blob and no bulk of
#   script" while the same report names the platform whose state blobs it
#   carries;
#   a finding ordering a name changed while its own snippet republished the
#   old name.
#
# Each was fixed inside the check that raised it, one at a time, and the shape
# came back in the next pass. It has to: no check can see what another check
# published, so no check can be the place this is caught. Only this file sees
# every finding at once, and only after they are final. So the guard is asked
# of every pair, whatever raised either half, and a check written next month
# inherits it.
#
# Two of the four are in scope here and two are not, and the line between them
# is what a finding *asserts*. A title and an evidence sentence are claims
# about the page as it is now. A fix step and a paste-ready snippet describe a
# page that does not exist yet - "Replace the H1 with a line naming what this
# is" is an instruction, not a statement that the line is there - so reading
# them as claims turns every well-written fix into a contradiction of its own
# evidence. The snippet case is fixed where the snippet is built; the
# fix-step case belongs to the check whose steps they are.
#
# Nothing is deleted. A miss costs a reader as much as a false positive
# and one of two disagreeing findings is usually right, so the weaker half
# keeps its severity, its place and its full text. What changes is that it
# moves to the tier the reader is told to confirm, and it carries a sentence
# naming the finding that disagrees with it. A reader who meets both now meets
# a stated disagreement instead of an unstated one.
# --------------------------------------------------------------------------

CLAIM_PRESENT = "present"
CLAIM_ABSENT = "absent"

# Words that turn a clause naming a subject into a denial of it.
#
# Wider than `_ABSENCE_RE`, which reads titles only and was written for a
# different question. "Too little text to be quoted" is one of the real
# wordings and contains none of `no not never none missing without absent
# lacks zero`.
_CLAIM_DENIAL_RE = re.compile(
    r"\b(?:no|not|never|none|nothing|nowhere|neither|missing|absent|without"
    r"|lacks?|lacking|zero|cannot|unable|fails?\s+to"
    r"|too\s+(?:little|few|short|thin))\b", re.I)

# Where one clause of a report sentence stops and the next begins.
#
# A comma splits only in front of a conjunction. Splitting on every comma cut
# 'No sentence of the form "X is a ...", "We are a ..." or "I am a ..." appears
# in the first 120 words' into three pieces, separating its "No" from its
# subject and reading the remainder as an assertion that the sentence is there.
_CLAIM_CLAUSE_RE = re.compile(
    r"[.;:!?]+|,\s*(?=(?:and|but|though|although|while|whereas|so|which|rather)\b)",
    re.I)

# Spans that are the site's words rather than the audit's claim. Same reason
# `claims_absence` strips them: a charity whose registered name carries a denial word puts
# `without` into the text of every finding that names it.
_CLAIM_QUOTED_RE = re.compile(r'"[^"]*"|`[^`]*`|“[^”]*”')

# The subjects two findings can take opposite views of.
#
# A table rather than a general rule, and the general rule is the reason: "two
# findings whose wording overlaps" is not a contradiction, it is two findings
# about one area of the site, which is normal and desirable. What makes a
# contradiction is one subject and two polarities, so the subject has to be
# named. Each entry is one question a page answers yes or no, written in words
# the reader gets to see when the disagreement is printed.
#
# Polarity is not in these patterns. It is read from the clause the pattern
# matched, by `_stance_on` below, so a check that words its finding a new way
# next month is covered without touching this table.
CONTRADICTION_SUBJECTS = (
    {
        "subject": "whether this page says, in one sentence, what the site is",
        "matches": re.compile(
            r"what (?:this|it|the brand|the site|the business) is"
            r"|says? what\b|state[sd]? what\b|defines? the brand|explains? what\b",
            re.I),
    },
    {
        "subject": "whether this page carries text worth quoting",
        "matches": re.compile(
            r"quotable|to be quoted|quote from (?:it|this|the page)"
            r"|readable text|words of (?:text|prose)"
            r"|questions and answers|paragraphs? of text", re.I),
    },
    {
        "subject": "whether this page delivers its text without JavaScript",
        "matches": re.compile(
            r"until javascript runs|without (?:running )?javascript|state blobs?", re.I),
    },
)

# How many shared pages a disagreement names before it says "and N more". The
# same reason as `AFFECTED_PAGES_SHOWN`: a sentence carrying sixty URLs is not
# a sentence.
DISPUTE_PAGES_SHOWN = 3


def _stance_on(text, pattern):
    """`present`, `absent` or None: what `text` asserts about one subject.

    Read clause by clause, because one report sentence carries two claims. "The
    opening text does explain what this is, so this is a headings and markup
    problem" asserts a presence in its first clause and says nothing about the
    subject in its second, and read whole it reads as neither.

    Only the text *before* the subject counts as a denial of it, which is how
    negation scopes in English and is what separates the two failures this
    settled:

      "delivers its text without JavaScript"   the denial word is inside the
                                               subject phrase and denies
                                               nothing. Reading it as a denial
                                               inverted the answer outright.
      "a page of questions and answers         one clause, two subjects. The
       carries no FAQ markup"                  `no` governs the markup, which
                                               is not what was matched, and
                                               reading it as a denial of the
                                               questions and answers made two
                                               opposite findings agree.

    A clause set that says both about one subject returns None. A finding that
    contradicts itself is not a claim this file can weigh against another
    finding, and guessing which half it meant is how a guard like this starts
    producing the false positives it exists to remove.
    """
    stances = set()
    for clause in _CLAIM_CLAUSE_RE.split(_CLAIM_QUOTED_RE.sub(" ", str(text or ""))):
        match = pattern.search(clause)
        if not match:
            continue
        stances.add(CLAIM_ABSENT if _CLAIM_DENIAL_RE.search(clause[:match.start()])
                    else CLAIM_PRESENT)
    return stances.pop() if len(stances) == 1 else None


def _claim_text(finding):
    """The part of a finding that asserts something about the page as it is.

    Title and evidence. Not the fix steps and not the snippet: both describe a
    page that does not exist yet, and reading an instruction as a statement
    makes every finding contradict its own remedy.
    """
    return "{}. {}".format(finding.get("title") or "", finding.get("evidence") or "")


def _pages_both_speak_about(absent_side, present_side):
    """The pages a disagreement is actually about, or an empty list.

    A finding naming no page is a claim about the whole site, and the two
    directions are not symmetrical. A site-wide claim that something is nowhere
    is disproved by one page carrying it, so it is compared against every page
    the other finding names. A site-wide claim that something is present is not
    a claim about the one page the other finding names - a site can carry a
    thing on most of its pages and not on one - so it is not.
    """
    absent_pages = set(absent_side.get("affected_pages") or [])
    present_pages = set(present_side.get("affected_pages") or [])
    if absent_pages and present_pages:
        return sorted(absent_pages & present_pages)
    if not absent_pages and present_pages:
        return sorted(present_pages)
    return []


def _which_claim_stands(absent_side, present_side):
    """(loser, winner, why): which of two opposite claims the audit may assert.

    Two steps, asked in this order.

    1. A direct reading beats an inferred one. `evidence_basis` is derived from
       the root cause and the check rather than written by whoever wrote the
       check, so it is the one comparison available here that is not somebody's
       opinion of their own work.
    2. Otherwise, seeing beats not seeing. A finding saying the thing is on the
       page has pointed at it. A finding saying it is not there has reported
       that its own detector did not match, which is a weaker claim about the
       same page - and every false positive this marketplace has made on a real
       site was one detector with a finite list of shapes reported as a fact.
       It is the same asymmetry `withhold_absence_claims` runs the whole crawl
       through, applied to one page instead of to the sample.

    There is no third step. Two findings that agree on polarity are not
    opposite and never reach here, so step 2 always decides.
    """
    absent_basis = evidence_basis(absent_side)
    present_basis = evidence_basis(present_side)
    if absent_basis == OBSERVED and present_basis != OBSERVED:
        return present_side, absent_side, (
            "The finding that disagrees read its answer off the document; this one holds "
            "only if this audit decided correctly what the page says.")
    if present_basis == OBSERVED and absent_basis != OBSERVED:
        return absent_side, present_side, (
            "The finding that disagrees read its answer off the document; this one holds "
            "only if this audit decided correctly what the page says.")
    return absent_side, present_side, (
        "The finding that disagrees points at something it found on the page. This one "
        "reports that its own detector did not find it, which is the weaker of two claims "
        "about the same page.")


def flag_opposite_claims(findings):
    """Mark the weaker half of every pair that contradicts itself on one page.

    Runs after `assign_ids` and reads the evidence the reader will see, because
    the promise is about the document rather than about an intermediate state:
    no two *published* findings may assert opposite things about one page.

    Writes `disputed_by` on the loser and returns the records it wrote. The
    loser is then published in the lower tier - `published_tier` reads this key
    first - so it can never lead "Start here", and both renderers print the
    disagreement inside it. It is not deleted, not reordered and not stripped
    of its severity: the audit does not know which of the two is wrong, only
    which one it is entitled to assert, and a deleted true finding is a
    miss.

    One dispute per finding. A finding that has already yielded to another is
    left alone rather than accumulating notes, and the first is the one that
    matters because the pairs are walked in report order.
    """
    disputes = []
    texts = [_claim_text(f) for f in findings]
    for index, one in enumerate(findings):
        for offset, other in enumerate(findings[index + 1:]):
            other_index = index + 1 + offset
            for subject in CONTRADICTION_SUBJECTS:
                pattern = subject["matches"]
                ours = _stance_on(texts[index], pattern)
                theirs = _stance_on(texts[other_index], pattern)
                if not ours or not theirs or ours == theirs:
                    continue
                absent_side, present_side = (
                    (one, other) if ours == CLAIM_ABSENT else (other, one))
                pages = _pages_both_speak_about(absent_side, present_side)
                if not pages:
                    continue
                loser, winner, why = _which_claim_stands(absent_side, present_side)
                if loser.get("disputed_by"):
                    continue
                record = {
                    "id": winner["id"],
                    "stable_id": winner.get("stable_id") or winner.get("id_hint"),
                    "subject": subject["subject"],
                    "pages": pages[:DISPUTE_PAGES_SHOWN],
                    "page_count": len(pages),
                    "why": why,
                }
                loser["disputed_by"] = record
                disputes.append(record)
    return disputes


def dispute_note(finding):
    """The disagreement, in one sentence, or "".

    One function, called by report.md and report.html alike. The two
    documents, checked against each other on a different section, were found
    disagreeing; a section whose whole subject is two halves of this report
    disagreeing may not be the next one.
    """
    dispute = finding.get("disputed_by")
    if not dispute:
        return ""
    pages = dispute.get("pages") or []
    total = dispute.get("page_count") or len(pages)
    more = "" if total <= len(pages) else " and {} more".format(total - len(pages))
    return ("Two findings in this report disagree about {}. {} says the opposite of this one "
            "about {}{}. {} So this one is published as a weaker reading: open the page and "
            "look before you spend anything on it.".format(
                dispute["subject"], dispute["id"],
                ", ".join(pages) if pages else "the same page", more, dispute["why"]))


# --------------------------------------------------------------------------
# One root cause, four bills
#
# One real audit was of a JavaScript-rendered storefront. The report diagnosed the
# rendering correctly, ranked it critical and first, named the mechanism, the
# fix and the way to check it - and then billed the owner three more times for
# the same defect:
#
#   F-007  "never states its founding facts"   both listing the same two pages
#   F-008  "never states its location"         that F-001 and F-009 declare
#                                              deliver no text at all, and both
#                                              prescribing a content fix - "put
#                                              the full postal address in text
#                                              on the contact page" - for a
#                                              rendering fault.
#   F-010  "no H1"                             its page list opening with the
#                                              homepage, the same fact a third
#                                              time.
#
# Their verdict: "root cause found and ranked first - but three to four
# findings per site are that root cause wearing different hats, presented as
# independent work items."
#
# **Why this is a deferral and not a fold.** `_fold_absent_markup` in
# structured-data replaced six "no X markup" findings with one, and that worked
# because the six had one fix between them: publish JSON-LD. These four do not.
# Once the page renders on the server, "put the address in the page text" is
# still work somebody has to do, raised by a different skill, with a different
# owner and a different remedy - and a deleted true finding is a miss that
# costs exactly as much as a false positive. Nothing is merged, nothing is
# dropped, every count and every page stays where it was.
#
# **Why not a demotion either.** The lower tier means "this audit is not sure -
# open the page and look". These findings are not uncertain: there really is no
# H1 and no address in what the server sent. What is wrong is only that they
# are presented as separate jobs, so what changes is what the reader is told
# about their order, not how sure the report is of them.
#
# So: the finding keeps its severity, its tier, its counts and its place, and
# gains one sentence naming the finding it follows from - and it is dropped
# from "Start here", which is the list that says what to do first and is the
# one place the four items competed with their own cause for the reader's three
# slots.
#
# **What may defer to what.** Two readings, and they do not cover the same
# ground:
#
#   the document did not arrive   `spa-shell-detection` and its two siblings
#                                 say this page delivered almost nothing. Every
#                                 absence claim about that page is then a
#                                 reading of an empty document, whatever the
#                                 absent thing is.
#   the layout did not arrive     `client-rendered-chrome` says the body copy
#                                 *did* arrive and the header, menu, footer and
#                                 H1 did not. A missing founding year on such a
#                                 page is a real gap in text that was delivered,
#                                 so this reading defers only the claims its own
#                                 evidence names: an empty navigation, a missing
#                                 heading, no off-site profile links, no route
#                                 to a contact page.
#
# `thin-html` is deliberately not a cause here. "This page carries almost
# nothing" is true of a page that was written thin, and "write the address on
# it" is then exactly the right instruction rather than one that cannot work.
# --------------------------------------------------------------------------

# The findings that declare a page's whole document undelivered. Keyed on
# `id_hint` rather than on the `js-shell` root cause, because
# `chrome-is-client-rendered` carries that same cause and means something
# narrower, and the difference is the whole of the rule above.
DOCUMENT_DID_NOT_ARRIVE = frozenset({
    "homepage-is-javascript-shell",
    "content-pages-are-javascript-shells",
    "javascript-supplies-most-page-text",
})

LAYOUT_DID_NOT_ARRIVE = "chrome-is-client-rendered"

# What a client-rendered layout accounts for, taken from the sentence that
# finding already prints: "any check on this site reporting an empty
# navigation, a missing <h1>, no off-site profile links or no route to a
# contact page is reading this same absence rather than four separate ones".
LAYOUT_EXPLAINS = frozenset({
    # The navigation half: menu links, orientation, the site-wide chrome.
    "no-orientation",
    "inconsistent-chrome",
    "no-breadcrumbs",
    # The heading half.
    "heading-structure",
    # The contact-route half.
    "dead-end",
    # The off-site profile half: the profile links live in the footer, which is
    # the part of the page this cause says is not delivered.
    "weak-corroboration",
})

DEFERRED_PAGES_SHOWN = 3

# Site-wide absence claims whose evidence sits in the header, the footer and
# the homepage copy: off-site profile links, a one-sentence definition, the
# identity block. On a site whose homepage is a JavaScript shell those parts
# did not arrive, so the claim may be the shell read again.
SITE_WIDE_CLAIMS_A_SHELL_HIDES = frozenset({
    "weak-corroboration", "no-entity-definition", "no-org-schema",
})


def _pages_a_finding_says_did_not_arrive(findings):
    """`({url: finding}, {url: finding})` - documents, then layouts.

    Read off the published findings' own `affected_pages`, not re-derived from
    the snapshot. Deciding here whether a page is a shell would be a second
    reader of the same question, and two readers of one question is how the
    shell test and the thin test came to contradict each other about one page.
    """
    documents, layouts = {}, {}
    for finding in findings:
        hint = finding.get("id_hint") or finding.get("stable_id")
        if hint in DOCUMENT_DID_NOT_ARRIVE:
            target = documents
        elif hint == LAYOUT_DID_NOT_ARRIVE:
            target = layouts
        else:
            continue
        for url in finding.get("affected_pages") or []:
            target.setdefault(url, finding)
    return documents, layouts


# The render skill's list of pages whose own copy is not in the delivered HTML,
# published only where that skill found shells. One reader of the question -
# the skill that measures delivery - and this file only reads its answer.
COPY_DID_NOT_ARRIVE_SIGNAL = "pages_whose_copy_did_not_arrive"

# Which shell finding a page on that list is attributed to when no finding
# names it: the one about content pages first, because the pages on the list
# are content the site meant to publish.
SITE_SHELL_FINDINGS = ("content-pages-are-javascript-shells",
                       "homepage-is-javascript-shell",
                       "javascript-supplies-most-page-text")


def _the_shell_finding(findings):
    """The published finding that establishes the site builds pages in the browser."""
    by_hint = {}
    for finding in findings:
        by_hint.setdefault(finding.get("id_hint") or finding.get("stable_id"), finding)
    return next((by_hint[hint] for hint in SITE_SHELL_FINDINGS if hint in by_hint), None)


def defer_to_the_page_that_did_not_arrive(findings, signals=None):
    """Mark every finding that is one already-reported defect seen again.

    Writes `follows_from` on the consequence and returns the records written.
    Runs after `assign_ids`, because the sentence names an F-number the reader
    can go and read, and before `ranked` and "Start here", because dropping it
    out of that list is half of what this does.

    Three conditions, and each one is there to stop a real miss:

      the claim is an absence   a finding that reports something the page *has*
                                is not explained by the page being empty.
      it names its pages, all   `affected_page_count` above the listed length
      of them undelivered       means pages this file cannot see, so the
                                subset test cannot be made. A finding with no
                                page list is a site-wide claim and is left
                                alone. One page outside the undelivered set and
                                the finding stands on its own: it is then about
                                a page that did arrive as well.
      it is not an access       mechanism A findings are the ones explaining
      finding                   the block, which is what the reader acts on.
                                Same carve-out, and the same reason, as
                                `withhold_absence_claims`.
    """
    documents, layouts = _pages_a_finding_says_did_not_arrive(findings)
    if not documents and not layouts:
        return []
    # Wider than the shell findings' own page lists, and only on a site one of
    # them was published for. A shop delivered its homepage and four content
    # pages as shells, and its thirteen category pages the same way - but
    # those were typed `other`, so no shell finding listed them, and "32 of 34
    # pages have no H1" and "no structured data at all" matched nothing here
    # and went to "Start here" as separate jobs. The render skill now
    # publishes every page whose own copy did not arrive, and that list is
    # read here as it stands. `signals` is None for the older callers.
    undelivered, site_cause = set(), None
    if documents:
        undelivered = set((signals or {}).get(COPY_DID_NOT_ARRIVE_SIGNAL) or [])
        site_cause = _the_shell_finding(findings)
    records = []
    # The homepage shell, for the site-wide claims below.
    home_shell = next((f for f in findings
                       if (f.get("id_hint") or f.get("stable_id"))
                       == "homepage-is-javascript-shell"), None)
    for finding in findings:
        hint = finding.get("id_hint") or finding.get("stable_id")
        if hint in DOCUMENT_DID_NOT_ARRIVE or hint == LAYOUT_DID_NOT_ARRIVE:
            continue
        if finding.get("mechanism") == "A":
            continue
        if finding.get("root_cause") not in ABSENCE_CAUSES:
            continue
        pages = list(finding.get("affected_pages") or [])
        if not pages:
            # A claim about the whole site, naming no page, on a site whose
            # homepage did not arrive. "No off-site profile is linked" and "no
            # page says what the brand is" read the header, the footer and the
            # homepage - which is what a shell does not deliver - and on a
            # storefront built that way the profile finding took a place in
            # "Start here" beside the shell. It stays whole and says why it may
            # be the shell read again.
            if (home_shell is not None and home_shell is not finding
                    and finding.get("root_cause") in SITE_WIDE_CLAIMS_A_SHELL_HIDES):
                hit = sorted(set(documents) | undelivered)
                record = {
                    "id": home_shell["id"],
                    "stable_id": home_shell.get("stable_id") or home_shell.get("id_hint"),
                    "pages": hit[:DEFERRED_PAGES_SHOWN],
                    "page_count": len(hit),
                    "site_wide": True,
                }
                finding["partly_follows_from"] = record
                records.append(dict(record, deferred=finding["id"], partial=True))
            continue
        total = finding.get("affected_page_count") or len(pages)
        complete = total <= len(pages)
        basis = "document"
        if complete and all(url in documents for url in pages):
            cause, why = documents[pages[0]], (
                "delivers almost no text to a client that does not run JavaScript")
        elif (complete and finding.get("root_cause") in LAYOUT_EXPLAINS
              and all(url in layouts for url in pages)):
            cause, why, basis = layouts[pages[0]], (
                "builds its header, navigation, footer and heading in the browser rather "
                "than delivering them in the HTML"), "layout"
        elif (complete and site_cause is not None and undelivered
              and all(url in documents or url in undelivered for url in pages)):
            cause, why, basis = site_cause, "", "copy"
        else:
            # Some of its pages did not arrive and some did. The claim stays
            # whole - it is true of the pages that arrived - and says which is
            # which, so the reader does the rendering fix first and then the
            # copy fix only where it still applies. Half or more of its whole
            # count, counted off the pages it lists, before it leaves "Start
            # here": below that the finding is mostly about pages that
            # arrived.
            hit = [url for url in pages if url in documents or url in undelivered]
            if site_cause is None or not hit or len(hit) * 2 < total:
                continue
            if site_cause is finding or site_cause.get("id") == finding.get("id"):
                continue
            rest = [url for url in pages if url not in documents and url not in undelivered]
            record = {
                "id": site_cause["id"],
                "stable_id": site_cause.get("stable_id") or site_cause.get("id_hint"),
                "pages": hit[:DEFERRED_PAGES_SHOWN],
                "page_count": len(hit),
                "of": total,
                "at_least": not complete,
                "rest": rest[:DEFERRED_PAGES_SHOWN],
            }
            finding["partly_follows_from"] = record
            records.append(dict(record, deferred=finding["id"], partial=True))
            continue
        if cause is finding or cause.get("id") == finding.get("id"):
            continue
        record = {
            "id": cause["id"],
            "stable_id": cause.get("stable_id") or cause.get("id_hint"),
            "why": why,
            "basis": basis,
            "pages": pages[:DEFERRED_PAGES_SHOWN],
            "page_count": len(pages),
        }
        finding["follows_from"] = record
        records.append(dict(record, deferred=finding["id"]))
    return records


# --------------------------------------------------------------------------
# One page, one cause
#
# The rule above, for a page whose own defect is not a shell. Three shapes, and
# each one had several findings billed for it separately:
#
#   a script redirect      a one-page site whose homepage is a script sending
#                          the browser on. "Nothing on this site can be reached
#                          from its homepage" prescribed the server redirect;
#                          "set one H1" and "give the site three menu items"
#                          were printed under it about the same empty page -
#                          a page the thin-text check had already set aside
#                          as a redirect.
#   a cover page           a root address that lands on a splash page: no
#                          text, two picture links to two editions. Three
#                          findings described it, none called it a cover page.
#   a frame                two virtual-tour pages that carry nothing but the
#                          frame the tour runs in. Five findings from three
#                          skills, each asking for something the frame's page
#                          cannot hold until the frame is dealt with.
#
# In each, one finding names what the page *is*, and that finding is the
# cause. Every other finding whose whole page list is those pages is the same
# page read again, and is marked `follows_from` it through the same record,
# the same note and the same exclusion from "Start here" as the shell rule.
# The skills say which pages these are; this file only reads their answer.
# --------------------------------------------------------------------------

# The render skill's list of short pages that are a script redirect.
SCRIPT_REDIRECT_SIGNAL = "short_pages_that_redirect_by_script"
# The engagement skill's reading of the homepage as a cover page.
COVER_PAGE_SIGNAL = "homepage_is_a_cover_page"

# The findings that name what a page is, with what the sentence says of it.
# The redirect one is the front-door finding, which names the script redirect
# and prescribes the server redirect when the homepage is one.
_PAGE_LEVEL_CAUSES = (
    ("homepage-reaches-no-other-page", SCRIPT_REDIRECT_SIGNAL,
     "sends the browser on to another address with a script and carries no content of "
     "its own"),
    ("homepage-is-a-cover-page", COVER_PAGE_SIGNAL,
     "is a cover page - almost no text, and a few links into the site - rather than a "
     "page of the site"),
    ("main-content-in-iframe", None,
     "carries nothing of its own beyond a frame holding another document"),
)


def defer_to_the_page_level_cause(findings, signals=None):
    """Mark every finding that is one of those pages read again.

    Runs after `defer_to_the_page_that_did_not_arrive` and leaves alone every
    finding that has already been attributed there. Same three conditions,
    for the same reasons: the finding names its pages, all of them, and every
    one is a page the cause names; and it is not an access finding. Returns
    the records written.
    """
    signals = signals or {}
    by_page = {}
    for hint, signal, why in _PAGE_LEVEL_CAUSES:
        for cause in findings:
            if (cause.get("id_hint") or cause.get("stable_id")) != hint:
                continue
            pages = list(cause.get("affected_pages") or [])
            if (cause.get("affected_page_count") or len(pages)) > len(pages):
                continue
            if signal is not None:
                named = signals.get(signal) or []
                named = {named} if isinstance(named, str) else set(named)
                pages = [url for url in pages if url in named]
            for url in pages:
                by_page.setdefault(url, (cause, why))
    if not by_page:
        return []
    causes = {id(cause) for cause, _ in by_page.values()}
    records = []
    for finding in findings:
        if id(finding) in causes or finding.get("mechanism") == "A":
            continue
        if finding.get("follows_from") or finding.get("partly_follows_from"):
            continue
        pages = list(finding.get("affected_pages") or [])
        if not pages or (finding.get("affected_page_count") or len(pages)) > len(pages):
            continue
        if not all(url in by_page for url in pages):
            continue
        cause, why = by_page[pages[0]]
        if any(by_page[url][0] is not cause for url in pages):
            continue
        record = {
            "id": cause["id"],
            "stable_id": cause.get("stable_id") or cause.get("id_hint"),
            "why": why,
            "basis": "page",
            "pages": pages[:DEFERRED_PAGES_SHOWN],
            "page_count": len(pages),
        }
        finding["follows_from"] = record
        records.append(dict(record, deferred=finding["id"]))
    return records


def follows_another(finding):
    """The id of the finding this one is a consequence of, wholly or partly, or ""."""
    return (finding.get("follows_from") or finding.get("partly_follows_from") or {}).get("id") or ""


def severity_table(report):
    """`(rows, follows)` - the severity table both documents print.

    `rows` is `[(label, confident, worth checking)]` over the findings that
    stand on their own. `follows` is `[(cause id, [ids], confident, worth
    checking)]`: the findings that are another one's defect read again, on a
    row of their own, so the table neither drops them nor counts one defect
    five times. One real table said "Medium: 6 + 8" over a museum whose one
    page pair was billed as five medium findings.
    """
    by_id = {f["id"]: f for f in report.get("findings") or []}
    demoted = set(report.get("worth_checking") or [])
    standing = [f for f in by_id.values() if not follows_another(f)]
    rows = [(label,
             sum(1 for f in standing if f["severity"] == key and f["id"] not in demoted),
             sum(1 for f in standing if f["severity"] == key and f["id"] in demoted))
            for key, label, _ in SEVERITY_HEADINGS]
    groups = collections.OrderedDict()
    for finding in sorted(by_id.values(), key=lambda f: f["id"]):
        cause = follows_another(finding)
        if cause:
            groups.setdefault(cause, []).append(finding)
    follows = [(cause, [f["id"] for f in group],
                sum(1 for f in group if f["id"] not in demoted),
                sum(1 for f in group if f["id"] in demoted))
               for cause, group in groups.items()]
    return rows, follows


def _follows_row_label(cause, ids):
    return "Follows from {}, not counted again ({})".format(cause, ", ".join(ids))


def deferral_note(finding):
    """The consequence, in one sentence, or "".

    One function, called by report.md and report.html alike, for the reason
    `dispute_note` above gives: two documents disagreeing about which findings
    are one job would be a worse failure than the one this fixes.
    """
    follows = finding.get("follows_from")
    if not follows:
        return ""
    pages = follows.get("pages") or []
    total = follows.get("page_count") or len(pages)
    more = "" if total <= len(pages) else " and {} more".format(total - len(pages))
    # The pages, named here, are the finding's page list: a consequence prints
    # no second list under its note.
    which = "Every page this names - {}{} - is".format(", ".join(pages), more) if pages \
        else "Every page this names is"
    if follows.get("basis") == "same-defect":
        # Two checks, two root causes, one broken thing. Both are kept, so
        # the reader is told which one carries the fix.
        return ("{} reports {}, read by another check. The fix is the one under {}; do "
                "that and re-run this audit. If this is still here afterwards, the fix below "
                "is the one to make.".format(follows["id"], follows["why"], follows["id"]))
    if follows.get("basis") == "page":
        # A page whose own defect is what it is - a redirect, a cover page, a
        # frame - rather than a text that did not arrive, so the closing
        # sentence names the page and not the text.
        return ("{} a page {} reports {}. This is the same page read again, not separate "
                "work: do {} first and re-run this audit. If this is still here afterwards, "
                "the fix below is the one to make.".format(
                    which, follows["id"], follows["why"], follows["id"]))
    if follows.get("basis") == "copy":
        # Pages the render skill measured as delivering none of their own copy
        # without any shell finding listing them, so the sentence names the
        # measurement rather than claiming the finding reports these pages.
        return ("{} a page that delivered almost none of its own copy in the HTML, on a site "
                "{} reports building its pages in the browser. This is that defect read "
                "again, not separate work: do {} first and re-run this audit. If this is "
                "still here afterwards, it is a real gap in the text and the fix below is the "
                "one to make.".format(which, follows["id"], follows["id"]))
    return ("{} a page {} reports {}. This is that defect read again, not separate work: do "
            "{} first and re-run this audit. If this is still here afterwards, it is a real "
            "gap in the text and the fix below is the one to make.".format(
                which, follows["id"], follows["why"], follows["id"]))


def partial_deferral_note(finding):
    """The split, in two sentences, for a finding only some of whose pages arrived.

    Shared by report.md and report.html for the same reason as `deferral_note`.
    """
    part = finding.get("partly_follows_from")
    if not part:
        return ""
    if part.get("site_wide"):
        count = part.get("page_count") or 0
        return ("This is a claim about the whole site, on a site whose homepage {} reports "
                "delivering almost no text to a client that does not run JavaScript{}. The "
                "profile links, the definition and the identity block this looks for usually "
                "sit in the header, the footer and the homepage copy, which are what did not "
                "arrive - so do {} first and re-run this audit before acting on this one. If "
                "it is still here afterwards, the fix below is the one to make.".format(
                    part["id"],
                    "; {} this crawl read delivered almost none of their own copy, such as "
                    "{}".format(plural(count, "page", "pages"),
                                ", ".join(part.get("pages") or []))
                    if count > 1 else "",
                    part["id"]))
    count = part.get("page_count") or len(part.get("pages") or [])
    rest = part.get("rest") or []
    lead = "{}{} of the {} pages this names".format(
        "At least " if part.get("at_least") else "", count, part.get("of") or count)
    after = ("The fix below is for the pages that did deliver their copy, such as {}.".format(
        ", ".join(rest)) if rest else
        "Which of the rest delivered their copy is not listed here; re-run this audit after "
        "{} to see what remains.".format(part["id"]))
    return ("{} delivered almost none of {} own copy in the HTML, on a site {} reports "
            "building its pages in the browser - for example {}. On {} this is that defect "
            "read again rather than separate work, so do {} first. {}".format(
                lead, "its" if count == 1 else "their", part["id"],
                ", ".join(part.get("pages") or []),
                "that page" if count == 1 else "those pages", part["id"], after))


# --------------------------------------------------------------------------
# Citation simulation
# --------------------------------------------------------------------------


# How many pages have to carry a sentence before it is furniture rather than
# content. Three, not the half-the-site threshold the whole-page boilerplate
# stripper uses: a block repeated across one subsection is that subsection's
# chrome even when it is a tenth of the crawl.
QUOTE_REPEAT_LIMIT = 3


# A sentence's closing mark, with any closing quote or bracket after it. Latin,
# CJK full-width and the ellipsis.
_SENTENCE_END_RE = re.compile(u"[.!?。！？…][\"'”’）)\\]」』]*\\s*$")
_SENTENCE_END_ANYWHERE_RE = re.compile(u"[.!?](?:\\s|$)|[。！？]")
# How many finished sentences a page needs before an unfinished one is read
# as a headline rather than as the way the page writes.
FINISHED_SENTENCES_TO_ASK = 3
# A teaser the page cut short: "...", "…", or either before a "[read more]".
_CUT_SHORT_RE = re.compile(u"(?:\\.\\.\\.|…)[\\W_]*(?:\\[[^\\]]{1,20}\\])?\\s*$")


def _finished(sentence):
    """Does this sentence end the way a sentence the page finished ends?"""
    return bool(_SENTENCE_END_RE.search(sentence or "")) and not _CUT_SHORT_RE.search(
        sentence or "")


def _quote_key(sentence):
    return re.sub(r"[^a-z0-9]+", " ", (sentence or "").lower()).strip()


def _sentences_seen_more_than_once(snapshot):
    """Sentences repeated across pages, which no page can claim as its own."""
    counts = {}
    for page in pages_of(snapshot):
        for paragraph in (page.get("paragraphs") or [])[:25]:
            for sentence in sentences(paragraph):
                key = _quote_key(sentence)
                if len(key) < 25:
                    continue
                counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count >= QUOTE_REPEAT_LIMIT}

# A run of words written all in capitals this long is a banner or an offer
# strip - "YOUR WEEK JUST GOT 20% BETTER!", "WEEKEND BONUS: EXTRA $70 OFF" - and
# a run of words this long almost every one of which starts with a capital is
# a menu read out in order. Neither is a sentence anybody quotes.
FURNITURE_CAPS_RUN = 3
FURNITURE_LABEL_TOKENS = 8
FURNITURE_LABEL_SHARE = 0.75
_WORD_TOKEN_RE = re.compile(r"[^\s|•·–—]+")


def reads_as_page_furniture(sentence):
    """Is this "sentence" a strip of labels, a banner or a menu rather than prose?

    The table of what an assistant would quote printed, for a furniture shop's
    contact page, "Contact Us | <Brand> AU WEEKEND BONUS: EXTRA $70 OFF* | CODE:
    SPRING70 Interior Styling Service Our Showrooms Reviews Refer a Friend...",
    and for a wine shop's about page "... Skip to content YOUR WEEK JUST GOT 20%
    BETTER!". The page's title bar, a promotion and the menu, flattened into one
    run with no full stop in it. Read by shape, not by a word list: a script
    with no capital letters at all is never judged here.
    """
    tokens = _WORD_TOKEN_RE.findall(sentence or "")
    run = 0
    for token in tokens:
        letters = [c for c in token if c.isalpha()]
        if len(letters) >= 2 and all(c.isupper() for c in letters):
            run += 1
            if run >= FURNITURE_CAPS_RUN:
                return True
        elif letters:
            run = 0
    cased = [t for t in tokens[1:] if t[:1].isalpha() and (t[:1].isupper() or t[:1].islower())]
    if len(cased) < FURNITURE_LABEL_TOKENS:
        return False
    capitalised = sum(1 for t in cased if t[:1].isupper())
    return capitalised >= FURNITURE_LABEL_SHARE * len(cased)


def simulate_citations(snapshot, brand_name):
    """The sentence an assistant would most likely lift from each key page.

    Makes the extractability findings concrete: a reader who sees "none found"
    beside a page sees immediately what the problem is.
    """
    out = []
    key_types = ("home", "about", "pricing", "faq", "product", "service", "contact", "location")
    pattern = r"\s+".join(re.escape(t) for t in brand_name.split()) if brand_name else None

    # A sentence that appears on several pages is that site's furniture, not
    # any one page's distinct content, and a table showing the same words twice
    # is answering a different question from the one it asks. Two real cases:
    # a court subsection's postal address, repeated verbatim across six pages
    # and offered as what an assistant would quote from each; and a footnote
    # joke - "*[product] is a web product and cannot be installed by CD" -
    # offered for both the homepage and the pricing page, over the actual
    # headline and the actual prices.
    #
    # The whole-site boilerplate stripper works at half the crawl and cannot
    # see either: six pages of sixty is ten per cent. Repetition still decides
    # it; the denominator is just the pages that carry it.
    repeated = _sentences_seen_more_than_once(snapshot)
    used = set()

    for page in pages_of(snapshot, content_only=True):
        if page["page_type"] not in key_types:
            continue
        # Quote from paragraphs, not from the flattened page text: a heading
        # and the sentence after it run together when the whole page is
        # flattened, and the result is not a sentence anyone would quote.
        #
        # And from the document the server delivered, not the one a browser
        # produced. The crawl re-reads a JavaScript shell from the rendered DOM
        # so the other checks judge the page a visitor sees, which is right for
        # them and wrong here: this table answers "what would a fetcher that
        # does not run JavaScript quote", and most assistant fetchers are
        # exactly that. Reading the hydrated copy made the table print a
        # confident quote directly under a critical finding saying the page
        # delivers no text at all.
        if page.get("content_from") == "rendered":
            out.append({
                "url": page["url"],
                "page_type": page["page_type"],
                "likely_citation": None,
                "why": "this page delivers no readable text until JavaScript runs, so a "
                       "fetcher that does not execute it has nothing to quote. The text is "
                       "there for one that does",
            })
            if len(out) >= 12:
                break
            continue
        candidates = []
        for paragraph in (page.get("paragraphs") or [])[:25]:
            candidates.extend(sentences(paragraph))
        if not candidates:
            # The first "sentence" of the flattened text is never one: it is
            # the title, the logo and the menu run into whatever the first
            # full stop ends. A national museum's homepage has no paragraphs,
            # and its row - and the verdict above it - quoted "<museum name>
            # previous next opening hours Mon/Tue/Thu ..." as the site's own
            # sentence, carousel buttons included.
            candidates = sentences(page.get("body_text", ""))[1:]
        # A sentence the page ends is one it finished. Where the page's own
        # writing uses full stops, a line without one is a headline or a
        # notice title - a university homepage's row quoted a list of
        # admission-document titles as what an assistant would lift. Thai and
        # other writing that does not end sentences with a mark is not asked.
        finishes = len(_SENTENCE_END_ANYWHERE_RE.findall(
            page.get("body_text", ""))) >= FINISHED_SENTENCES_TO_ASK
        # Source is not a sentence, and this is the boundary where a sentence
        # becomes something a report prints back to a site as its own words.
        # `reads_as_source_not_prose` is the extractor's own test: an angle
        # bracket, a bare `undefined`, or two of the marks that only appear in
        # program source. One real report printed `city "); yyyymmddhh = data[0] + ...`
        # under the heading "Its homepage says".
        candidates = [c for c in candidates[:60]
                      if _quote_key(c) not in repeated and _quote_key(c) not in used
                      and not reads_as_source_not_prose(c)
                      and not reads_as_page_furniture(c)][:40]
        best = None
        reason = ""
        # Priority 1: a definition naming the brand. That is what an assistant
        # needs before it can say anything else.
        # The shared definition rule, not a verb list of its own. "Today,
        # <Brand> is proud to be B Corp certified" carries the brand and
        # "is", and a furniture shop's verdict quoted it as the one sentence
        # saying what the brand is; `defining_sentence` rejects "proud to"
        # and every other shape `fact-extractability-audit` rejects, so the
        # table and that finding cannot disagree about one sentence.
        if pattern:
            for sentence in candidates:
                if re.search(pattern, sentence, re.I) and defining_sentence(sentence, brand_name):
                    best, reason = sentence, "names the brand and says what it is"
                    break
        # How long a sentence has to be before it is worth quoting depends on
        # the script it is written in. Forty characters is a clause in
        # English and a whole sentence in Japanese, and the floor rejected
        # complete, fact-stating Japanese sentences on three pages of one
        # site, each reported as having nothing quotable.
        floor = QUOTE_MIN_CHARS if words_are_separated(
            page.get("body_text", "")) else QUOTE_MIN_CHARS_UNSPACED

        # Priority 2: a price or a contact detail. Both are short by nature
        # and both are exactly what somebody asks an assistant for. A
        # software company's pricing page - "Basic $10 per user/month" - and
        # its contact page - "For other questions, email us hello@..." - were
        # both reported as having nothing an assistant could quote, in a table
        # printed directly under a finding about the site being hard to quote.
        if best is None:
            for sentence in candidates:
                if len(sentence) > 300:
                    continue
                if PRICE_LINE_RE.search(sentence):
                    best, reason = sentence, "states a price a reader could act on"
                    break
                if CONTACT_LINE_RE.search(sentence):
                    best, reason = sentence, "states a way to make contact"
                    break
        # Priority 3: a self-contained sentence carrying a number.
        if best is None:
            for sentence in candidates:
                if _CUT_SHORT_RE.search(sentence) or (finishes and not _finished(sentence)):
                    continue
                if floor <= len(sentence) <= 300 and NUMERIC_RE.search(sentence):
                    best, reason = sentence, "states a number a reader could act on"
                    break
        # Priority 4: any self-contained sentence that defines something.
        if best is None:
            for sentence in candidates:
                if _CUT_SHORT_RE.search(sentence) or (finishes and not _finished(sentence)):
                    continue
                if floor <= len(sentence) <= 300 and CONCRETE_RE.search(sentence):
                    best, reason = sentence, "defines something, though it states no figure"
                    break
        if best is not None:
            used.add(_quote_key(best))
        out.append({
            "url": page["url"],
            "page_type": page["page_type"],
            "likely_citation": truncate(best, 260) if best else None,
            "why": reason if best else
                   "no sentence on this page both stands alone and states a fact, so an "
                   "assistant fetching it would have nothing to quote",
        })
        if len(out) >= 12:
            break
    return sorted(out, key=lambda c: c["url"])


# --------------------------------------------------------------------------
# Proactive recommendations
# --------------------------------------------------------------------------

def _plural(count, singular, plural):
    """`1 page crawled`, not `1 pages crawled`.

    It is the first line of the report a person reads.
    """
    return "{} {}".format(count, singular if count == 1 else plural)


# --------------------------------------------------------------------------
# Advice that fits the site
#
# The findings had been gated on `site_kind` for some time and this catalogue
# never was, and the advice showed it: "Corroborate the
# brand" read "Claim or update: LinkedIn company page, Google Business Profile,
# Crunchbase" identically for a shoe shop, a municipal government and a program
# with no company and no premises. A reader who meets one step written for
# somebody else stops reading, and that costs the report every true entry
# underneath it.
#
# Two rules, and they are the ones `freshness-corroboration-audit` already
# follows for its own fix text:
#
#   `might_be(...)` gates. It answers True for a site nothing was decided about
#                   and for a weak determination, so an unplaced site gets
#                   exactly the catalogue it gets today and a wrong guess can
#                   never delete advice.
#   `is_certainly(...)` chooses wording, and only wording. Naming the wrong
#                   platform is a smaller failure than hiding the entry, so the
#                   default list stands wherever the classifier is quiet.
#
# Written as things to go and claim rather than as brand names, so a step stays
# followable by somebody who knows their own country's equivalent and stays
# true when a platform is renamed.
# --------------------------------------------------------------------------

_CLAIM_PLATFORMS = (
    (LOCAL_BUSINESS,
     "the mapping service people search in, the review sites your customers already use, "
     "your trade association's directory, and the two big social networks"),
    (ONLINE_SELLER,
     "the social platforms your buyers browse, the review site they check before ordering, "
     "and every marketplace you already sell on"),
    # Not trade and standards bodies: an organisation here is anything with a
    # name and no till - a scholarship network, a society, a charity - and
    # "industry directories" reached one that belongs to no industry.
    (ORGANISATION,
     "the professional network your staff are on, a video platform, and the registers and "
     "directories where organisations like this one are listed"),
    (PROJECT,
     "the repository host your code lives on, the package index people install it from, "
     "Wikidata, and the forum or chat where your users already talk"),
    (PUBLIC_BODY,
     "Wikidata and the encyclopedia article about you, the official register of public "
     "bodies that lists you, your open-data portal, and the channels you already publish "
     "notices on"),
    (PERSONAL_OR_ACADEMIC,
     "your researcher identifier, your institution's staff directory, the repository host "
     "your code or data lives on, and Wikidata"),
    (PUBLICATION,
     "Wikidata and the encyclopedia article about you, the indexes and aggregators that "
     "list publications, your serial number record, and the platforms your writers already "
     "post on"),
)

# What every site got before any of this existed, and what a site the
# classifier cannot place still gets.
_CLAIM_PLATFORMS_WHATEVER_THE_SITE_IS = (
    "the professional network, the two big social networks, a video platform, and your "
    "industry's directories")

# Where the one paragraph of identity has to be pasted, which is a different
# list for something with no press office and no app.
_BOILERPLATE_DESTINATIONS = (
    (PROJECT,
     "the site, the Organization `description`, the repository's own description and README, "
     "the package index entry, and the Wikidata description"),
    (PUBLIC_BODY,
     "the site, the Organization `description`, the register entry that lists you, your "
     "open-data portal profile, and every notice channel you publish on"),
    (PERSONAL_OR_ACADEMIC,
     "the site, the Organization `description`, your institution's staff directory entry, "
     "your researcher profile, and every conference or speaker biography"),
    (PUBLICATION,
     "the site, the Organization `description`, your masthead, the indexes that list you, "
     "and every syndication partner's about box"),
)
_BOILERPLATE_DESTINATIONS_DEFAULT = (
    "the site, the Organization `description`, your profile on the professional network, and "
    "every directory that lists you")

# How many of the site's own profiles the destination sentence will name.
#
# The list this replaced ended "the press kit, app store listings and every
# directory entry", and it was wrong for all four sites in one validation pass:
# none of the four has a press kit and none of them ships an app. A list of
# places somebody else's business has is where a reader stops, and it is worse
# here than anywhere because this is the one entry that fires on every site.
#
# Four, because the sentence has to stay a sentence. Where the crawl read more
# profiles than that the rest are covered by "and the others you have claimed",
# which is true and does not need the reader to count.
BOILERPLATE_DESTINATIONS_NAMED = 4


def profiles_this_site_publishes(snapshot):
    """The off-site platforms this site links to, by name, in a stable order.

    Read off the pages that were crawled rather than from a list of platforms
    somebody expects a brand to be on, so the destination sentence names the
    accounts the owner actually has to go and edit.
    """
    seen = {}
    for page in pages_of(snapshot):
        for platform in (page.get("declared_profiles") or {}):
            seen[platform] = None
    return sorted(seen)

# The first sentence of a boilerplate. "When you started" is a founding date,
# which a program does not have, and "who you serve" is customers, which a
# public body and a program do not have either.
_BOILERPLATE_FIRST_STEP = (
    (PROJECT,
     "Write one paragraph: what the software does, who it is for, what problem it replaces, "
     "and when the project started."),
    (PUBLIC_BODY,
     "Write one paragraph: what you are responsible for, which area you cover, who you "
     "answer to, and when you were established."),
    (PERSONAL_OR_ACADEMIC,
     "Write one paragraph: what you work on, where you are based, and what you are known "
     "for."),
)
_BOILERPLATE_FIRST_STEP_DEFAULT = (
    "Write one paragraph: what you are, who you serve, where you are, when you started.")

# Who a page of reusable facts is for, and what it holds. A program has no
# spokesperson and no headcount; a public body has both and neither is a
# marketing asset.
_PRESS_PAGE_WORDING = (
    (PUBLIC_BODY,
     "Give journalists and researchers a single page of approved facts so third parties "
     "repeat the same ones.",
     "Include the exact figures you want repeated: the area you cover, the services you "
     "run, when you were established, and the office to contact."),
    (PUBLICATION,
     "Give other publications and aggregators a single page of approved facts so they "
     "repeat the same ones.",
     "Include the exact figures you want repeated: founding year, circulation or "
     "readership, editorial staff and the sections you publish."),
    # "Headcount, founding year, locations, customer count" went to four
    # consumer brands with between two and eight people in them: in effect,
    # "corporate figures for four-person businesses".
    # Every figure in that list is one a small business either does not have or
    # would not publish, and asking for four of them in one line is how a
    # reader concludes the report was written for somebody else.
    #
    # What a shop or a studio does have is what it makes, where it is, who
    # makes it and what it costs - which is also what a third party writing
    # about it needs, so nothing is lost by asking for those instead.
    (LOCAL_BUSINESS,
     "Put the facts somebody writing about you would otherwise guess at on one page, so "
     "they copy yours.",
     "Include what you actually publish: what you make or do, where you are, who runs it, "
     "when you opened, and one number you are happy to be quoted on."),
    (ONLINE_SELLER,
     "Put the facts somebody writing about you would otherwise guess at on one page, so "
     "they copy yours.",
     "Include what you actually publish: what you sell, what it is made of and where, who "
     "is behind it, when you started, and where you ship."),
)
_PRESS_PAGE_WORDING_DEFAULT = (
    "Put the facts somebody writing about you would otherwise guess at on one page, so "
    "they copy yours.",
    "Include the facts you want repeated: what you do, where you are, who is behind it, "
    "when you started, and any figure you are happy to be quoted on.")

# Who answers the questions an FAQ page is built from. There is no sales team
# behind a program and no support desk behind a municipal register.
_FAQ_SOURCE = (
    (PROJECT, "Collect the ten questions your issue tracker, forum or chat answers most "
              "often."),
    (PUBLIC_BODY, "Collect the ten questions your enquiry line and counter staff answer "
                  "most often."),
    (PERSONAL_OR_ACADEMIC, "Collect the ten questions you are asked most often by email, "
                           "after talks and by students."),
    (PUBLICATION, "Collect the ten questions your readers and your subscriptions desk ask "
                  "most often."),
    # The sales-and-support wording, only where the site is established to be
    # a business. It was the default, and it reached a one-page essay and a
    # scholarship network whose kind nobody had determined.
    (ONLINE_SELLER, "Collect the ten questions your sales or support team answers most "
                    "often."),
    (LOCAL_BUSINESS, "Collect the ten questions your staff answer most often, on the phone "
                     "and in person."),
    (ORGANISATION, "Collect the ten questions people most often write to you about."),
)
_FAQ_SOURCE_DEFAULT = (
    "Collect the ten questions you are asked most often, by email, in messages or in "
    "person.")


def _worded_for(kind, table, default):
    """The entry `kind` is certainly for, or `default`.

    `is_certainly`, so a site the classifier could not place or placed weakly
    keeps the wording it gets today. Naming the wrong platform is a smaller
    failure than a step nobody can follow, but only a determination worth
    defending may change one.
    """
    for row in table:
        if kind.is_certainly(row[0]):
            return row[1] if len(row) == 2 else row[1:]
    return default


# Property names that make a marked-up thing something other than a name, a
# price and a picture. These are the three `R-BRAND-TERMINOLOGY` asks for in
# its own third step, so the condition and the fix cannot disagree about what
# counts as having done it.
_DISTINCTIVE_PRODUCT_PROPS = frozenset({"additionalproperty", "material"})
_DISTINCTIVE_PRODUCT_TYPE = "propertyvalue"


def _names_a_distinctive_property(node, depth=0):
    """A JSON-LD node, or anything under it, naming a property of its own."""
    if depth > 6:
        return False
    if isinstance(node, dict):
        for key, value in node.items():
            name = str(key).lower().lstrip("@")
            if name in _DISTINCTIVE_PRODUCT_PROPS:
                return True
            # `@type: PropertyValue` is the third shape the fix names, and it
            # has to be matched on the declared type rather than on the string
            # appearing anywhere: a description reading "made of recycled
            # material" is prose, not a property, and matching it would make
            # the condition quietly untestable.
            if name == "type":
                types = value if isinstance(value, list) else [value]
                if any(str(t).lower() == _DISTINCTIVE_PRODUCT_TYPE for t in types):
                    return True
            if _names_a_distinctive_property(value, depth + 1):
                return True
    elif isinstance(node, list):
        return any(_names_a_distinctive_property(item, depth + 1) for item in node)
    return False


def products_name_nothing_distinctive(snapshot):
    """Does nothing this site marks up name a property of its own?

    `R-BRAND-TERMINOLOGY` used to fire on "a product, service or pricing page
    exists", which is one of the three entries found firing on six
    sites out of six: having a product page at all was the whole condition,
    and that is a condition wearing a condition's clothes. This is the measured
    version of what the entry actually describes - things coming back flattened
    into generic specifications because nothing in the markup distinguishes
    them - and it goes quiet on a site whose products already carry a named
    property, which is the site that has already taken this advice.

    A site with no such markup at all answers True, which is the answer it gets
    today: nothing there names anything either.
    """
    for page in pages_of(snapshot, content_only=True):
        if _names_a_distinctive_property(page.get("jsonld")):
            return False
    return True


# Page types that serve one audience or one place each.
_AUDIENCE_PAGE_TYPES = ("location", "product", "service")


def _differentiating_text(page):
    """What this page says that another page of the same kind would not.

    The H1 and the first paragraph, normalised. Those are the two blocks the
    advice below asks somebody to write, so they are the two blocks that
    settle whether it has been written.
    """
    headings = page.get("headings") or {}
    h1 = " ".join(headings.get("h1") or [])
    paragraphs = page.get("paragraphs") or []
    first = paragraphs[0] if paragraphs else ""
    return re.sub(r"\s+", " ", "{} {}".format(h1, first)).strip().lower()


def audience_pages_running_one_template(snapshot):
    """Audience or location pages whose H1 and opening paragraph are the same.

    This is the inversion that prompted it. The gate counted location pages and
    then recommended creating location pages: a studio publishing three named
    branch pages, typed `location` by this audit's own quote table, was told to
    "give each distinct audience or place its own page" and then, in the
    printed steps, to "list the three audiences or locations that matter most".
    The site had done the thing; the gate read the evidence of it as the
    problem. The entry's stated intent was always to split a shared template,
    and that intent never reached the steps.

    So the condition is now the template rather than the pages: two or more
    pages of the same audience kind whose H1 and first paragraph are identical,
    which is a template printed several times with only the URL changing. A
    site whose branch pages each name their own place is silent here, which is
    the correct answer for the site that prompted this.

    Returns the URLs, so the entry can name them rather than assert them.
    """
    by_text = {}
    for page in pages_of(snapshot, content_only=True):
        if page.get("page_type") not in _AUDIENCE_PAGE_TYPES:
            continue
        text = _differentiating_text(page)
        # A page with no H1 and no paragraph tells us nothing about whether it
        # differentiates, and grouping those together would report a crawl that
        # read no text as a duplicated template. That is the render failure,
        # and it has its own finding.
        if not text:
            continue
        by_text.setdefault((page["page_type"], text), []).append(page["url"])
    shared = []
    for urls in by_text.values():
        if len(urls) >= TEMPLATE_EVIDENCE_MINIMUM:
            shared += sorted(urls)
    return sorted(shared)


def _as_a_question(title):
    """An instruction reworded as something to check.

    Every title in this catalogue opens with a verb in the imperative, which is
    exactly what a recommendation resting on an absence may not do when the
    absence was never established.
    """
    return "Do you already {}{}?".format(
        title[0].lower() + title[1:], "" if title.endswith("?") else "")


def coverage_caveat(snapshot):
    """The sentence that says why an absence could not be established here."""
    attempted, readable = readable_counts(snapshot)
    # `_plural`, because "0 of the 1 URLs it reached" is what a template writes
    # and this sentence sits at the top of advice a marketing manager reads.
    return ("This audit read {} of the {} it reached ({}), so it could not establish that "
            "this is missing - only that the pages it saw do not have it. If you already "
            "publish this, nothing here needs doing.".format(
                readable, _plural(attempted, "URL", "URLs"),
                why_little_was_read(snapshot) or "no reason was recorded"))


def build_recommendations(snapshot, signals, findings):
    """Recommendations beyond the defects found, included only when relevant.

    Every entry states its condition, so nothing generic is ever appended just
    to make the report look longer.
    """
    # Every condition below is the absence of a signal, so on a site we never
    # managed to read, every signal is absent and almost every recommendation
    # fires. Audit a domain that does not resolve and the report offered six
    # improvements for it, including publishing a file at its root.
    #
    # Absence of evidence is not evidence of absence, and the entrypoint's own
    # procedure already says an unreachable homepage makes every other check
    # meaningless. That rule was honoured by the findings and not by these.
    if not pages_of(snapshot):
        return []

    root_causes = {f["root_cause"] for f in findings}
    page_types = {p["page_type"] for p in pages_of(snapshot, content_only=True)}
    # `origin` was read here and never used, and reading it made the whole
    # function raise KeyError on a snapshot that carries no `origin` key.
    brand = (snapshot.get("brand") or {}).get("name") or snapshot["site"]
    # Read once. `might_be` gates entries below and `is_certainly` chooses
    # wording; see the block above `_CLAIM_PLATFORMS` for why those are
    # different calls and never interchangeable.
    kind = site_kind(snapshot)
    # The coverage rule the findings already obey, applied to the
    # advice.
    #
    # Most of this catalogue is absence-shaped - no press page among the pages
    # crawled, no FAQ markup anywhere, fewer than six profiles seen - and on a
    # thin crawl every one of those absences is a fact about the crawl. One
    # report reached a single page of an education site and recommended
    # publishing a press page and FAQ content; both exist, at `/about/press`
    # and on a full support site, each answering 200. Findings already hold
    # this line: `withhold_absence_claims` moves an absence claim into the
    # unanswered channel and `enough_to_grade` gates the verdict. The
    # recommendations did not, so the same report withheld the findings and
    # printed the instructions.
    #
    # Reworded rather than deleted. An absence-shaped recommendation is still
    # worth putting to somebody who knows the site - they can settle it in a
    # second - and unlike a finding it accuses nobody. What it may not do is
    # instruct. `enough_to_grade` is the same predicate, so the two halves of
    # the report cannot now disagree about whether the crawl saw enough.
    read_here = len(readable_text_pages(snapshot))
    graded = enough_to_grade(read_here, readable_share(snapshot))
    out = []

    # Every entry below says what it was measured on, on this site. Four
    # reports out of five printed "Design newsletters text-first" and "Write
    # the comparison that concedes something" word for word, and seven sites
    # got the same llms.txt, boilerplate, dates and "Read next" paragraphs:
    # advice that reads the same everywhere reads as not measured anywhere.
    def cited(causes):
        """The ids of the published findings carrying any of these causes."""
        return [f["id"] for f in findings if f.get("root_cause") in causes and f.get("id")]

    def naming(ids):
        return " ({} {} the pages)".format(", ".join(ids[:3]), "lists" if len(ids) == 1
                                           else "list") if ids else ""

    def add(rec_id, title, condition, summary, steps, mechanism, why, effort, owner,
            snippet=None, rests_on_absence=False):
        """`rests_on_absence`: the condition is "the crawl did not see X".

        Set it per entry rather than inferring it from the wording, because
        several conditions are mixed - `R-DATE-SIGNALS` fires either on dates
        that were seen and are stale or on dates that were not seen at all -
        and only the caller knows which half fired on this run.
        """
        if not condition:
            return
        entry = {
            "id": rec_id, "type": "proactive", "title": title, "summary": summary,
            "how_to_do_it": steps, "mechanism": mechanism, "why_this_works": why,
            "effort": effort, "owner": owner,
        }
        if rests_on_absence and not graded:
            entry["conditioned_on"] = "absence"
            entry["title"] = _as_a_question(title)
            entry["summary"] = "{} {}".format(coverage_caveat(snapshot), summary)
        if snippet:
            entry["snippet"] = snippet
        out.append(entry)

    key_pages = llms_txt_key_pages(snapshot, signals)
    # No `rests_on_absence`. This condition is one request to one URL at the
    # site root that answered, and how many content pages the crawl reached has
    # no bearing on it: the file is there or it is not. The tri-state below
    # already covers the case where the request never happened.
    add("R-LLMS-TXT", "Publish /llms.txt and /llms-full.txt",
        # `False`, not falsy. The signal is now tri-state: `True` published,
        # `False` the request answered and there is no file, `None` the
        # request was refused, disallowed for this auditor, or never made.
        # Reading `None` as absent recommended publishing a file on sites
        # where the audit had been told it may not look, and on hosts that
        # never answered at all.
        signals.get("llms_txt_present") is False,
        "Add a short plain-text file at the site root summarising what this is, its key pages "
        "and its canonical facts. This crawl asked for {}/llms.txt and got no such file.{}"
        .format(
            (snapshot.get("origin") or "").rstrip("/"),
            " The starter below names {} this crawl read that {} with content of {} own: "
            "{}.".format(
                plural(len(key_pages), "page", "pages"),
                "answered" if len(key_pages) == 1 else "each answered",
                "its" if len(key_pages) == 1 else "their",
                ", ".join(url for _, url in key_pages))
            if key_pages else
            " No page this crawl read qualified as a key page - one that answered with "
            "content of its own rather than a redirect or a form - so the starter below "
            "leaves the list for you to fill in."),
        # "a one-line brand definition" was the wording, and a program, a
        # weather service and a heritage archive do not have a brand to define.
        # The instruction is the same for all of them once it says what it
        # means, so this is a reword rather than a gate.
        ["Create /llms.txt with a one-line definition of what this is, the canonical "
         "boilerplate, and a linked list of your most important pages with one line of "
         "description each.",
         "Add /llms-full.txt containing the full text of those key pages if they are short.",
         "Keep it under 200 lines and update it whenever the boilerplate changes."],
        "B",
        "It hands a machine the exact summary you want quoted, instead of leaving it to "
        "assemble one from whichever page it happened to fetch.",
        "low", "marketing",
        _llms_txt_template(snapshot, brand, signals))

    # `R-ANSWER-FIRST` was retired here, and the deletion is the change.
    #
    # It fired on `signals["fluff_first"]`, which is set by the same check that
    # raises the `fluff-first` finding, and it then asked for what that
    # finding's own fix asks for: a short declarative block under the H1. The
    # reader met one instruction twice, in two sections, under two headings,
    # with two owners and two effort estimates. Rule 5 of
    # `references/proactive-recommendations.md` has forbidden that since the
    # catalogue was written; this entry predated the rule and was never
    # measured against it.
    #
    # Nothing is lost: the finding names the pages, quotes their openings and
    # ships the fix. What the catalogue is for is the thing no check can call a
    # defect, and a page that opens with a paragraph of scene-setting is a
    # defect this audit already reports.

    # Only when there is no FAQ content at all. A site that has an FAQ page and
    # has not marked it up already gets `no-faq-schema` as a finding, with the
    # page named and a snippet carrying its real questions - and this told the
    # same owner to "add a page of real questions" they had already written.
    # Two sections of one report, disagreeing about whether a page exists.
    has_faq_page = bool(pages_of(snapshot, types=("faq",)))
    # A question-and-answer block on an ordinary page is FAQ content too.
    # `structured-data-audit` now finds those and quotes the real questions
    # back; without this test the recommendations section told the same owner,
    # in the same report, to go and write the questions it had just quoted.
    add("R-FAQ-SCHEMA", "Publish FAQ content with FAQPage schema",
        not signals.get("has_faq_schema") and not has_faq_page
        and not signals.get("pages_with_unmarked_qa"),
        "Add a page of real questions, phrased the way people ask assistants, marked up as "
        "FAQPage. None of the {} this crawl read is an FAQ page, carries FAQPage markup or "
        "prints answers under question headings.".format(
            plural(read_here, "page", "pages")),
        # Only the first step moves. There is no sales team behind a program
        # and no support desk behind a municipal register, and a step naming
        # one is a step nobody there can carry out - but every kind of site has
        # somebody who answers the same ten questions over and over.
        [_worded_for(kind, _FAQ_SOURCE, _FAQ_SOURCE_DEFAULT),
         'Phrase each heading as the reader asks it ("How much does X cost?"), not as an '
         "internal topic name.",
         "Answer each in two or three sentences, leading with the actual value.",
         "Wrap the whole set in FAQPage JSON-LD with the answer text matching the page exactly."],
        "B",
        "Question-and-answer pairs are already the shape of the thing an assistant is trying "
        "to produce, so they are unusually cheap for it to reuse.",
        "medium", "content owner",
        # Three absences over the pages that were crawled. The report that
        # prompted this told a site with a whole support site to go and write
        # some questions.
        rests_on_absence=True)

    # Two reports told an owner "Your Wikidata item already exists (Q...). Link
    # it from Organization `sameAs`" in a finding, and "Create a Wikidata item"
    # in this recommendation, on the same page. `entity-ambiguity` looks the
    # item up and knows the answer; the recommendation never asked it.
    own_item = signals.get("wikidata_own_entity")
    # Three states, not two. A museum publishes a Wikidata link and the lookup
    # never had to run, so `wikidata_own_entity` is None while an item plainly
    # exists - and the recommendation still said "create one". `True` means an
    # item is known to exist, `False` that the lookup ran and found none, and
    # `None` that nothing was looked up.
    item_exists = signals.get("wikidata_item_exists") is True or bool(own_item)
    if own_item:
        wikidata_step = (
            "Link your existing Wikidata item ({}) from the Organization `sameAs` array, and "
            "give it the same one-sentence description you use on the site.".format(own_item))
    elif item_exists:
        wikidata_step = (
            "You already publish a Wikidata or Wikipedia link. List it in the Organization "
            "`sameAs` array too, and make sure it carries the same one-sentence description "
            "you use on the site.")
    elif signals.get("wikidata_item_exists") is None:
        # The lookup never happened. Three states were not enough: `False`
        # meant "the lookup ran and found nothing" and `None` was being
        # treated the same way, so on two sites whose robots.txt asked for a
        # 10- and a 30-second crawl delay - honoured in full, which spent the
        # wall-clock budget and left the sub-skills with no network - the
        # report told the owner to create a Wikidata item for an organisation
        # that has one. A recommendation resting on a measurement may not be
        # written as though the measurement happened.
        wikidata_step = (
            # "the brand", until a program and a weather service were both
            # told to search Wikidata for theirs. The name is the thing being
            # searched for, on every kind of site.
            "Search Wikidata for the name before creating anything: this audit could not "
            "run that lookup, so whether an item already exists is unknown here. If one "
            "exists, link it from the Organization `sameAs` array; if not, create it with "
            "the same one-sentence description you use on the site.")
    else:
        wikidata_step = (
            "Create a Wikidata item using the same one-sentence description you use on the "
            "site.")
    # Breadth alone, and no longer `or not has_wikidata_or_wikipedia`.
    #
    # That second clause is contradicted by the study this catalogue cites two
    # entries below: the gap between brands assistants name and brands they
    # ignore "is spread across ordinary platforms - not concentrated in
    # Wikipedia or Crunchbase". Almost no brand site links a Wikidata item, so
    # the clause fired on every site whatever its footprint - including two in
    # one validation pass that link 8 and 10 profiles, comfortably above the 7.2
    # mean measured for the brands that do get named. Telling those two to go
    # and claim more profiles is generic advice, and the catalogue was
    # weaker for it. `profile_breadth < 6` is the number the study
    # produced and the same one `weak-corroboration` passes at, so the
    # recommendation and the finding cannot now disagree about who needs this.
    # The entry that showed most plainly that the advice was not gated: it
    # read "Claim or update: LinkedIn company page, Google Business Profile,
    # Crunchbase" identically for a shoe shop, a municipal government and a
    # program with no company and no premises. Two of those three cannot claim
    # a business listing at all, and a step nobody can carry out is where a
    # reader stops.
    #
    # The finding this recommendation pairs with - `weak-corroboration` - has
    # named its platforms by kind since it was written, and its own fix text
    # is where the wording below comes from. The two are now saying the same
    # thing to the same site.
    claim_platforms = _worded_for(kind, _CLAIM_PLATFORMS,
                                  _CLAIM_PLATFORMS_WHATEVER_THE_SITE_IS)
    sameas_steps = ["Claim or update the profiles that apply to you: {}.".format(
        claim_platforms), wikidata_step]
    # `is_certainly`, not `might_be`. This used to fire on every site nothing
    # was decided about, and it told a central bank, a scholarship network and
    # a one-page essay to claim "a business database entry and an employer
    # profile". The profile step above already reaches every site; this one is
    # for a site established to have a company behind it.
    if kind.is_certainly(LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION, PUBLICATION):
        sameas_steps.append(
            "Claim the company record too - a business database entry and an employer "
            "profile - so an assistant asked who is behind this finds an answer that is not "
            "your own site.")
    sameas_steps += [
        "List every profile URL in the Organization `sameAs` array.",
        # The half `weak-corroboration` does not ask for, and the half that
        # decides whether any of the rest works. Retired `R-AUTHOR-PAGES`
        # contributed the credential clause: a named person with something
        # checkable behind them is what turns a set of accounts into evidence
        # that somebody is there.
        "Make each profile link back to the site, and name a real person on at least one of "
        "them with something checkable next to their name - a qualification, a registration "
        "number, how long they have done this. A set of accounts nobody is named on "
        "corroborates that the accounts exist, not that anybody is behind them."]
    # The measured sentence. This entry has been marked down twice for reading
    # like a brochure, and the fix that worked for `R-DATE-SIGNALS` was to put
    # the site's own number next to the number it is being compared against.
    # `profile_breadth` is already the count the condition is made of, so
    # printing it costs nothing and makes the threshold arguable rather than
    # asserted.
    breadth_now = signals.get("profile_breadth")
    breadth_sentence = "" if breadth_now is None else (
        " This crawl found {} linked from the pages it read. In {}, brands assistants name "
        "link a mean of 7.2 and comparable brands they ignore link 4.6. That study is ours, "
        "so treat it as a strong association and not a proven cause.".format(
            plural(breadth_now, "off-site profile", "off-site profiles"),
            STUDY_SAMPLE_PHRASE))
    add("R-SAMEAS-WIKIDATA", "Corroborate the brand across independent sources",
        signals.get("profile_breadth", 0) < 6,
        "Claim the authoritative profiles, {} a Wikidata item, and list them all in "
        "sameAs.{}".format(
            "link" if item_exists
            else "check for and then link or create"
            if signals.get("wikidata_item_exists") is None else "create",
            breadth_sentence),
        sameas_steps,
        "D",
        "One site saying something is a claim. Several independent, verifiable sources saying "
        "the same thing is what makes it safe to repeat.",
        "medium", "marketing",
        # `profile_breadth` counts the profiles found on the pages that were
        # read. A crawl of one page has seen one page's outbound links, and
        # "fewer than six" is then a statement about the crawl.
        rests_on_absence=True)

    # Still unconditional, and the reason is not laziness. The only measurement
    # available to narrow it - `distinct_org_descriptions`, the count of
    # distinct Organization descriptions the crawl read - came back 0 on every
    # one of fifteen real sites, so gating on it would fire everywhere and
    # merely look measured. What changes here is the wording: "when you
    # started" is a founding date a program does not have and "who you serve"
    # is customers a public body does not have, and the list of places to paste
    # it named a press kit and app store listings to sites with neither.
    #
    # It read as filler on four sites out of four, on two grounds. The
    # first - unconditional - stands, for the reason above and because it is
    # the one entry a site with no defects at all still benefits from. The
    # second - "its destination list is wrong for all four" - was correct and
    # is fixed below: where the crawl read the site's own profile links, those
    # are the destinations, because they are the accounts somebody has to open
    # and edit. A generic list of places to paste it is where this entry lost
    # its reader, and it is the entry every reader gets.
    boilerplate_destinations = _worded_for(
        kind, _BOILERPLATE_DESTINATIONS, _BOILERPLATE_DESTINATIONS_DEFAULT)
    published_profiles = profiles_this_site_publishes(snapshot)
    # Only the default is replaced. The four kind-specific lists name a
    # repository README, a public register entry, a staff directory and a
    # masthead - destinations no crawl of the brand's own site will ever
    # produce, and each of them the reason its row exists.
    if published_profiles and boilerplate_destinations is _BOILERPLATE_DESTINATIONS_DEFAULT:
        named = published_profiles[:BOILERPLATE_DESTINATIONS_NAMED]
        boilerplate_destinations = "the site, the Organization `description`, and {}{}".format(
            ", ".join(named),
            " and the others you have claimed"
            if len(published_profiles) > len(named) else "")
    # What there is to start from, read off this site: its own definition, or
    # its homepage's description. Named, so the entry says something about this
    # site rather than about sites in general.
    says = what_the_site_says_it_is(snapshot, signals)
    if says and says.get("source") == "definition":
        starting_point = (' Start from the sentence the site already uses: "{}". This crawl '
                          "found no Organization `description` carrying it.".format(
                              truncate(says["text"], 160)))
    elif says:
        starting_point = (' The nearest thing this crawl found is the homepage\'s {}: "{}". '
                          "Start from it, and make it say what this is rather than what the "
                          "page is.".format(says["source"], truncate(says["text"], 160)))
    else:
        starting_point = (" This crawl found nothing on the homepage - no definition, no meta "
                          "description, no og:description - that says what this is, so there "
                          "is no sentence to start from yet.")
    add("R-BOILERPLATE", "Write one canonical boilerplate and reuse it verbatim",
        True,  # always: the cheapest corroboration win there is
        "Agree a single paragraph describing what this is and use it, unchanged, "
        "everywhere.{}".format(starting_point),
        [_worded_for(kind, _BOILERPLATE_FIRST_STEP, _BOILERPLATE_FIRST_STEP_DEFAULT),
         "Paste it, character for character, into {}.".format(boilerplate_destinations),
         "Change it in one place and propagate; never let two versions coexist.",
         "Review it once a year rather than rewriting it per channel."],
        "D",
        "Identical wording across independent sources is the strongest agreement signal "
        "available, and it costs nothing but discipline.",
        # No `rests_on_absence`, and it is the one entry where that follows from
        # the condition rather than from a judgement: there is no absence in it
        # to be wrong about. Agreeing one paragraph and using it unchanged is
        # advice a site that already has a boilerplate is not harmed by.
        "low", "marketing")

    # Second of the two entries that fired on essentially every site, and it
    # was gated the way `R-COMPARISON-PAGES` used to be: on a page type almost
    # no crawl ever assigns, which is "always" wearing a condition's clothes.
    # It fired on 12 of the 13 validation sites that got any recommendations.
    #
    # The measured version is the thing the page would fix. A press page exists
    # so third parties copy the right facts rather than inventing them, and it
    # is worth building when there is something wrong with the facts to copy.
    # Two measurements say so, either of them enough:
    #
    #   core_facts_missing            the facts this audit could not find
    #                                 stated in plain text anywhere on the
    #                                 site. Nothing for a journalist to copy.
    #   nap- or name-inconsistency    the site states them and disagrees with
    #                                 itself, so whichever page a third party
    #                                 copied from, somebody else copied the
    #                                 other one. Deciding which version is
    #                                 canonical is exactly what this page is.
    #
    # A site whose four core facts are all present and all agree already has
    # the reusable facts; it just does not have them on one page, which is a
    # much smaller thing than this entry claims. That is three of thirteen real
    # sites, so this is a narrowing rather than a rewrite - said plainly here
    # because a condition nearly every site meets is still most of the way to
    # "always", and the honest number belongs next to the change.
    #
    # `is None`, not falsy. An empty list means the check ran and found nothing
    # missing; a missing key means the check never ran, and a measurement that
    # did not happen may not silence advice.
    core_facts_missing = signals.get("core_facts_missing")
    press_wording, press_figures = _worded_for(
        kind, _PRESS_PAGE_WORDING, _PRESS_PAGE_WORDING_DEFAULT)
    # "Publish a press page" is what this asked for, and it read as
    # "corporate figures for four-person businesses". The mechanism is right
    # and the artifact was wrong: what makes third parties copy the same facts
    # is one page carrying them, not a press office. A shop with two people in
    # it has no spokesperson and no logo pack and will not build either, so an
    # entry naming both is one the reader files under "not us" - and the facts
    # it wanted on that page are ones they could publish this afternoon.
    #
    # The title still opens with "Publish", because `_as_a_question` turns
    # these titles into questions where the crawl was too thin to establish the
    # absence, and it needs a verb to work with.
    add("R-PRESS-PAGE", "Publish one page of the facts you want others to copy",
        "press" not in page_types
        and (core_facts_missing is None or bool(core_facts_missing)
             or bool({"nap-inconsistency", "name-inconsistency"} & root_causes))
        # A program has no press office and a personal site has no press kit,
        # and both were told to publish one. `might_be`, so a site the
        # classifier cannot place is asked for it exactly as it is today.
        and kind.might_be(LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION,
                          PUBLIC_BODY, PUBLICATION),
        press_wording + (
            " The facts this crawl could not find stated in plain text: {}.".format(
                ", ".join(core_facts_missing)) if core_facts_missing else
            " The site states its facts in more than one version{}.".format(
                naming(cited({"nap-inconsistency", "name-inconsistency"})))
            if {"nap-inconsistency", "name-inconsistency"} & root_causes else ""),
        ["Publish one page carrying the canonical boilerplate and the facts below. It can be "
         "a section of your about page; it does not need to be called a press page.",
         press_figures,
         "Name a person on it who can be contacted, with a way to reach them. Anonymous "
         "facts get checked against somebody else's version.",
         "Link it from the footer, so anybody writing about you finds it without asking."],
        "D",
        "Third-party coverage is where corroboration comes from, and whoever writes about you "
        "will state these facts whether or not you have. One page they can copy from is the "
        "difference between their version and yours.",
        "low", "marketing",
        # `"press" not in page_types` is a claim about the pages crawled, and
        # `core_facts_missing` is a claim about the text read. On a one-page
        # crawl this told a site to publish the page sitting at `/about/press`.
        rests_on_absence=True)

    # The single highest-value recommendation we can make, and the only one
    # backed by a measured difference between sites assistants cite and sites
    # they ignore. Fires on a dating problem of any kind: no dates at all,
    # stale dates, or simply low coverage across the site.
    # 25%, not 50%. An earlier threshold came from comparing brand sites against
    # encyclopedias, where dated coverage runs at 85%. Compared within category,
    # brands assistants name date 34% of pages and brands they ignore date 17%.
    # A 50% bar would have fired on most of the brands that *are* named.
    date_coverage = signals.get("date_coverage")
    # Mixed, so the flag is computed rather than declared. A stale date that was
    # read is something seen and the advice stands on it; "no date signals" and
    # a low coverage share are both counts over the pages the crawl reached, and
    # on a thin crawl they say more about the crawl than about the site.
    dates_were_seen = bool({"no-date-signal", "stale-content"} & root_causes)
    # A measured share at or above the floor settles the undated half. A
    # national library dates 96% of its crawled pages, and this recommendation
    # fired anyway - on a sitemap missing `<lastmod>` - telling it to put a
    # date on every page in the same sentence that printed "96% of the pages
    # crawled here carry any date signal". Where the share was never measured
    # the root cause still decides. Stale dates are the other half of this
    # entry - "keep the dates honest" - and a site dating every page with
    # dates years old needs exactly that, so they fire whatever the share.
    coverage_is_enough = (date_coverage is not None
                          and date_coverage >= DATE_COVERAGE_FLOOR)
    add("R-DATE-SIGNALS", "Date your content, and keep the dates honest",
        "stale-content" in root_causes
        or (not coverage_is_enough and (
            signals.get("no_date_signals")
            or dates_were_seen
            or (date_coverage is not None and date_coverage < DATE_COVERAGE_FLOOR))),
        "Put a visible published-or-updated date on every substantive page, mirror it in "
        "structured data, and review the top pages on a schedule."
        # Rounded, not truncated: a site dating 999 of 1000 pages was told it
        # dates 99% of them, one line above a threshold of 25%.
        + ("" if date_coverage is None else
           " {}% of the pages crawled here carry any date signal.".format(
               int(round(date_coverage * 100))))
        + naming(cited({"no-date-signal", "stale-content"})),
        ["Add \"Last updated <date>\" to every substantive page, wrapped in "
         "<time datetime=\"YYYY-MM-DD\">.",
         "Mirror it in `dateModified` in structured data.",
         "Put a quarterly review of the top ten pages in someone's calendar.",
         "Only move the date when the content actually changed. A date that always changes "
         "carries no information and gets discounted.",
         "Do not date pages where a date would mislead, such as a contact page."],
        "D",
        # The printed figures were 85% against 35%, taken from the earlier
        # study this file's own comment twenty lines above records as wrong:
        # 85% is encyclopedias, not brands, and 35% appears nowhere in the
        # data. The within-category means are 34% and 17%, which is also the
        # pair the 25% threshold sits between, so the sentence and the
        # threshold under it now come from the same measurement.
        "Freshness is one of the few quality signals a machine can check cheaply, and it is "
        "the tiebreaker when two sources make the same claim. This is also the one measure "
        "that separated the two cohorts in " + STUDY_SAMPLE_PHRASE + ": compared inside "
        "the same product category, brands assistants name carry a date on 34% of their "
        "pages and comparable brands they ignore on 17%. Of everything in this catalogue "
        "it is the cheapest change with the clearest evidence behind it.",
        "low", "content owner",
        rests_on_absence=not dates_were_seen)

    # Not `"comparison" not in page_types` alone. Almost no site has a page
    # this audit types as a comparison, so that condition is "always" wearing
    # a condition's clothes: it fired on six sites out of six in the graded
    # round, among them a national weather service and a computing history
    # archive, neither of which has an alternative anybody weighs it against.
    # Handing those two a page of "X vs Y" is the brochure entry this
    # catalogue's own docstring forbids. `sells_something` is a measurement -
    # a product page, a pricing page, commerce markup, or a price on a page
    # whose job is prices - rather than a page-type label, and it is the same
    # test the pricing checks use to decide whether a site has a buyer at all.
    #
    # The steps are reordered rather than rewritten. This entry splits into
    # a filler half - "write comparison pages", which is advice anybody
    # would give - and one line of real insight: "a comparison
    # that never concedes anything is not treated as a source." That line was
    # step two, under a step telling the reader to go and list competitors.
    # A reader who stops after the first step gets only the obvious half, so
    # the half worth having leads.
    selling = [p["url"] for p in pages_of(snapshot, content_only=True)
               if sells_something(snapshot, [p])]
    add("R-COMPARISON-PAGES", "Write the comparison that concedes something",
        sells_something(snapshot) and "comparison" not in page_types,
        'Cover the framings people actually search - "X vs Y", "X for <use case>" - and be '
        "honest in them, because the honesty is what makes them usable as a source. {} this "
        "crawl read {}, and none of the {} it read compares what is on offer with an "
        "alternative.".format(
            plural(len(selling), "page", "pages") if selling else "The pages",
            "sells something, such as {}".format(selling[0]) if len(selling) == 1 else
            "sell something, such as {}".format(", ".join(selling[:2])) if selling else
            "sell something",
            plural(read_here, "page", "pages")),
        ["Say plainly who each option suits better, including where somebody else's is the "
         "right answer. A comparison that concedes nothing reads as marketing and is "
         "discounted; the version that concedes is the one that gets quoted, and it gets "
         "quoted on the part where you win.",
         "Write one page per alternative people actually mention to you, not one page per "
         "competitor you can think of.",
         "Put the concrete differences in a table - price, what it does, what it costs to "
         "start - so the comparison can be read without reading the prose."],
        "E",
        "Assistants answer the question as it was asked, and most buying questions are asked "
        "as a comparison. A brand with content only for its own framing is absent from all of "
        "them however good its own page is - and a page that only flatters itself is read as "
        "a claim rather than as evidence, so it is discounted even when it is found.",
        "medium", "content owner",
        # The `sells_something` half is a measurement; the half that makes this
        # fire is "no page crawled was typed as a comparison", which a crawl
        # that read one page cannot establish.
        rests_on_absence=True)

    # The gate was inverted: it fired *because* the site had
    # already done the thing. It counted location, product and service pages
    # and then told a studio publishing three named branch pages - typed
    # `location` by this audit's own quote table, printed in the same report -
    # to "give each distinct audience or place its own page", with a first step
    # reading "list the three audiences or locations that matter most". The
    # entry's stated intent was always to split a shared template, and having
    # several pages is evidence of the split, not evidence of the problem.
    #
    # `audience_pages_running_one_template` is the measurement of the thing the
    # entry is about: pages of the same kind whose H1 and opening paragraph are
    # identical, which is one template printed several times with only the URL
    # changing. It names the URLs, so the entry says which pages rather than
    # asserting a template nobody can point at. A site whose branch pages each
    # name their own place is silent here.
    shared_template = audience_pages_running_one_template(snapshot)
    # No `rests_on_absence`: the condition is two or more pages having been read
    # and found to carry the same words, which is a presence. A thin crawl
    # silences this on its own.
    add("R-USE-CASE-PAGES", "Give each page on this template its own facts",
        bool(shared_template)
        # "the audiences or locations that matter commercially" is a sentence
        # about a business. A program's audiences are platforms and a
        # researcher's are fields, and neither reads the step as written.
        and kind.might_be(LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION,
                          PUBLIC_BODY, PUBLICATION),
        "{} pages here open with the same heading and the same first paragraph as another "
        "page of the same kind, so they are one template printed several times: {}.".format(
            len(shared_template), ", ".join(shared_template[:4])),
        # Every step is about the pages that exist. The old first step -
        # "list the three audiences or locations that matter most" - is the one
        # that did the damage: it asked a site with three named branch pages to
        # go and decide which three places matter.
        ["Rewrite the H1 and the first paragraph of each of these pages to name the place, "
         "audience or use case it is for.",
         "Put one fact in each that is true of it and false of the others: its own address "
         "and opening hours, its own price, the name of the person who runs it.",
         "Leave the shared parts shared. The template is not the problem; the parts of it "
         "that should differ and do not are."],
        "E",
        "Answers are shaped by the asker's context, and a machine comparing two of these "
        "pages finds the same sentences on both. Pages that do not differ from each other "
        "are treated as one page, so only one of them is ever surfaced - and which one is "
        "not your choice.",
        "medium", "content owner")

    # No `rests_on_absence`: a `pdf-locked-facts` finding fired, which means a
    # PDF and a page that does not state its numbers were both read.
    add("R-HTML-FOR-PDF", "Publish HTML versions of PDF-only content",
        "pdf-locked-facts" in root_causes,
        "Move the numbers out of the PDF and onto a page.{}".format(
            naming(cited({"pdf-locked-facts"}))),
        ["Create an HTML page carrying the same table or figures as the PDF.",
         "Keep the PDF as a download from that page, not as the only source.",
         "Generate the PDF from the HTML so the two cannot diverge."],
        "C",
        "PDFs are fetched inconsistently and quoted reluctantly. The same numbers in HTML are "
        "read every time.",
        "medium", "content owner")

    # No `rests_on_absence`: an email capture form was found on a page.
    signing_up = [p["url"] for p in pages_of(snapshot) if p.get("newsletter_signup")]
    add("R-EMAIL-TEXT-FIRST", "Design newsletters and transactional email text-first",
        signals.get("newsletter_signup"),
        "Put the substance in the first two lines of readable text, never in an image.{}"
        .format(" This crawl found an email sign-up form on {}, such as {}, so this site "
                "sends email a machine will summarise.".format(
                    plural(len(signing_up), "page", "pages"), ", ".join(signing_up[:2]))
                if signing_up else ""),
        ["Lead every email with two lines of plain text stating what it is and what changed.",
         "Never put the main message in an image; use images to support text, not replace it.",
         "Give every email a subject line that states the fact rather than teasing it.",
         "Include a plain-text alternative part in every send."],
        "F",
        "Inbox summaries are built from the text of a message. Substance carried in an image, "
        "or buried under filler, is simply absent from the summary the recipient reads.",
        "low", "marketing")

    # This read as filler on all four sites in one validation pass, because it
    # restated a finding printed elsewhere in the same report. That was
    # true of the steps and not of the condition. Two of the three
    # bullets were the fixes of findings that had already fired - `dead-end`,
    # `orphan-pages`, `no-breadcrumbs` and `broken-links` each print their own
    # remedy, and "add breadcrumbs to every deep template" was a third copy of
    # the pair `fold_one_job_findings` has since merged into one.
    #
    # So the steps lose everything a finding already asks for, and what is left
    # is the one thing no check can raise: a related-content module. Nothing
    # about a page without one is a defect - it is a page that works and ends -
    # which is exactly what this catalogue is for and what rule 5 in
    # `references/proactive-recommendations.md` now says in as many words: a
    # finding may *trigger* a recommendation, and may not be *restated* by one.
    #
    # No `rests_on_absence`. Every branch names something that was read.
    furniture_only = signals.get("deep_pages_offering_only_site_wide_links") or []
    wayfinding_ids = cited({"dead-end", "orphan-pages", "no-breadcrumbs", "broken-links"})
    wayfinding_measured = ""
    if len(furniture_only) >= TEMPLATE_EVIDENCE_MINIMUM:
        wayfinding_measured = (" On this site {} offer only the links every page carries, "
                               "such as {}.".format(plural(len(furniture_only), "deep page",
                                                           "deep pages"),
                                                    ", ".join(sorted(furniture_only)[:2])))
    elif wayfinding_ids:
        wayfinding_measured = (" This rests on {} above, each a page a visitor reaches and "
                               "cannot go on from.".format(", ".join(wayfinding_ids[:3])))
    elif signals.get("search_box_queries_another_site"):
        wayfinding_measured = (" This site's search box sends the visitor to another site's "
                               "search, so the page itself is the only way on.")
    add("R-WAYFINDING", 'Give deep pages a "Read next" of their own',
        # Also when the site's search box submits to a web search engine.
        # That is not on-site search, and the check that noticed could only
        # say so in the appendix - so a site whose only way to find anything
        # is somebody else's index got no recommendation to build one.
        bool({"dead-end", "orphan-pages", "no-breadcrumbs", "broken-links"} & root_causes)
        or bool(signals.get("search_box_queries_another_site"))
        # The measured version. Those four root causes are about links that are
        # broken or missing; this signal names the deep pages whose only onward
        # links are the ones every page carries, which is exactly the site with
        # no "Read next" module and nothing else wrong. Counted, not merely
        # non-empty: one such page is a page, and the clean fixture has one.
        or len(signals.get("deep_pages_offering_only_site_wide_links") or []
                ) >= TEMPLATE_EVIDENCE_MINIMUM,
        "Add a related-content block to the article and product templates, so a page that "
        "answered the question has somewhere to send the reader that is not the back "
        "button.{}".format(wayfinding_measured),
        ['Add a "Read next" block to the article and product templates carrying 2-4 links '
         "chosen for this page, not the same four on every page.",
         "Pick them by what the page is about rather than by what is newest, so the block is "
         "worth following and not furniture.",
         "Write the link text as what the reader gets, not as the page's title."],
        "G",
        "A visitor sent by an assistant's answer lands deep with no journey behind them: no "
        "homepage, no category, nothing they browsed to get here. Every other route through "
        "the site assumes a journey that did not happen, so the only way onward is whatever "
        "this one page offers.",
        "medium", "developer")

    # Was `bool({"product", "service", "pricing"} & page_types)` - the third of
    # the three entries firing on six sites out of six, where a `/plans` page
    # on a free newsletter was enough to trigger advice about naming product
    # components. Two measurements replace it, and both are of the thing the
    # entry is actually about:
    #
    #   pages that sell, counted            `sells_something` asked of one page
    #                                       at a time, so the definition cannot
    #                                       drift from the one the pricing
    #                                       checks use. Counted rather than
    #                                       tested, with the same floor as
    #                                       `R-WAYFINDING` above: this entry
    #                                       asks for a change to a template -
    #                                       "use the identical term everywhere:
    #                                       site, spec sheets, packaging" - and
    #                                       one page is not a template. On a
    #                                       six-page crawl of an open-source
    #                                       project a single stray figure was
    #                                       enough to make the whole site a
    #                                       seller.
    #   products_name_nothing_distinctive   nothing this site marks up carries
    #                                       an `additionalProperty`, a
    #                                       `material` or a named
    #                                       `PropertyValue` - which is exactly
    #                                       the flattening described below, and
    #                                       exactly what step three asks for.
    #                                       A site that has already done this
    #                                       stops being told to do it.
    pages_that_sell = sum(1 for p in pages_of(snapshot, content_only=True)
                          if sells_something(snapshot, [p]))
    add("R-BRAND-TERMINOLOGY", "Name your own components so machines must use your words",
        pages_that_sell >= TEMPLATE_EVIDENCE_MINIMUM
        and products_name_nothing_distinctive(snapshot),
        "Give the things that make the product distinctive proper names, and define those "
        "names in both the page text and the structured data. {} this crawl read sell "
        "something, and nothing in their markup names a property of its own.".format(
            plural(pages_that_sell, "page", "pages")),
        # Step three leads now, and the four steps are three.
        #
        # Read line by line, this entry had one non-obvious thing in it -
        # "carry the same term into structured data: Product
        # `additionalProperty`, `material`, or a named `PropertyValue`" - and
        # it was the non-obvious half, buried under four steps of naming advice. Naming things well is advice
        # every brand has already had. Putting the name in a place a machine
        # reads as an assertion rather than as prose is the part nobody says,
        # and it is the part `products_name_nothing_distinctive` measures, so
        # it is also the step that turns this entry off once it is done.
        #
        # This step used to carry a coined product name and a distance figure
        # verbatim - "the Aero-Flyte sole, a recycled EVA midsole rated to
        # 800km" - into every report. It read as jarring when the
        # audited site was a browser-compatibility database and a statistics
        # charity, because it reads as a fact the audit found about the brand.
        # The illustration is now a shape to fill in rather than an example
        # product.
        ["Put the thing that makes your product different into the markup as a property, not "
         "into the copy as an adjective: Product `additionalProperty`, `material`, or a named "
         "`PropertyValue` carrying the name and the figure that proves it. Nothing this crawl "
         "read declares one, which is why the products come back described in anybody's "
         "words.",
         'Give it a name first, and define the name in the same sentence you first use it: '
         '"<the generic phrase you use today>" becomes "<Your Coined Name>, <one clause '
         'saying exactly what it is, with the figure that proves it>". An undefined coined '
         "word is noise and gets discarded; three defended terms beat twenty.",
         "Then use that exact string everywhere the product is described - the site, the spec "
         "sheet, the packaging, the feeds you send retailers. Two spellings of one component "
         "are two components to anything reading them."],
        "B",
        "Assistants extract explicit factual entities and discard the marketing copy around "
        "them, which is why brands come back flattened into generic specifications. A named, "
        "defined component is a fact rather than an adjective, so it survives extraction and "
        "the brand's own vocabulary has to be used to describe the product accurately.",
        "medium", "marketing",
        # The selling-pages half is a measurement. The half that makes this
        # fire is `products_name_nothing_distinctive`, which is "nothing in
        # the markup this crawl read names a property of its own" - an absence
        # over whatever pages the crawl happened to reach.
        rests_on_absence=True)

    # Gated on two measurements of the pages themselves, and deliberately not
    # on `page_types` containing `product`. That is the shape that weakened
    # this catalogue: three of fourteen entries fired on six sites out
    # of six because having a product page at all was the whole condition. Both
    # signals below name specific URLs the engagement checks measured, so this
    # entry cannot fire on a site nobody measured - a crawl too small to
    # describe a template emits empty lists and this stays quiet.
    #
    # `landing_pages_stating_only_a_price` is the page that publishes a price
    # and answers none of the three questions left after it; `..._bury_the_price`
    # is the page that answers the question further down than a visitor reads.
    # Counted rather than merely non-empty, for the same reason as R-WAYFINDING
    # above: one page is a page, two is the template.
    # No `rests_on_absence`: both signals name specific URLs the engagement
    # checks measured, so a crawl too thin to describe a template emits empty
    # lists and this stays quiet without any help from the coverage rule.
    add("R-LANDING-PAGE-ANSWERS-FIRST",
        "Reorder the landing template around the question the visitor arrived with",
        len(signals.get("landing_pages_stating_only_a_price") or []
            ) >= TEMPLATE_EVIDENCE_MINIMUM
        or len(signals.get("landing_pages_that_bury_the_price") or []
               ) >= TEMPLATE_EVIDENCE_MINIMUM,
        "Put the price, the stock state and the delivery promise above the brand copy on the "
        "pages an answer sends people to, and mirror all three in the markup.{}".format(
            "".join(" {} {}, such as {}.".format(plural(len(urls), "page", "pages"), what,
                                                 ", ".join(sorted(urls)[:2]))
                    for urls, what in (
                        (signals.get("landing_pages_stating_only_a_price") or [],
                         "state a price and nothing a buyer asks next"),
                        (signals.get("landing_pages_that_bury_the_price") or [],
                         "put the price below the first screen"))
                    if len(urls) >= TEMPLATE_EVIDENCE_MINIMUM)),
        ["Move the price, whether it is in stock and when it arrives into the first screen of "
         "the product or service template, above the brand story and above the carousel.",
         "Add all three to the template itself rather than to individual pages, so a new "
         "product cannot ship without them.",
         "Mirror the same three in `Offer`: `price`, `availability` and either "
         "`shippingDetails` or `deliveryTime`, so a machine reads the same answer the "
         "visitor does.",
         "State the returns window in a sentence on the page, not only as a link to a "
         "policy page.",
         "Add a related-items block to the template with two to four specific alternatives, "
         "so a visitor whose answer was almost right has somewhere to go that is not the "
         "back button."],
        "G",
        "Somebody who arrives from an assistant's answer has already been told what this is; "
        "they clicked to settle one question, usually what it costs, whether it is in stock "
        "or when it would arrive. A template that opens with the brand story answers a "
        "question they stopped asking three steps ago, and they leave to ask the assistant "
        "again. This is the cheapest change on the list because it moves blocks that already "
        "exist rather than writing anything new.",
        "medium", "developer")

    # `R-AUTHOR-PAGES` was retired here, and the deletion is the change.
    #
    # In one line: any web developer says "byline your blog". Advice has to be
    # relevant *and* non-obvious, and an instruction anybody would have given
    # without running this audit adds nothing on the second count however true
    # it is on the first. It fired on
    # four sites out of four.
    #
    # Its condition also read `articles_without_author`, which is the signal
    # behind the byline finding, so on the sites where it fired the report
    # already named the articles and shipped the `author` markup for them. Same
    # rule as `R-ANSWER-FIRST` above: a recommendation may be triggered by a
    # finding and may not repeat what that finding asks for.
    #
    # The one part that was not in the finding - an author page per writer,
    # carrying a verifiable credential - belongs with the corroboration entry,
    # which is where a claim about who is behind the site is made.

    return out


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------

def not_page_records(snapshot):
    """How many of the crawl's records were responses that proved they are no page.

    The README promises a record the crawl skipped as a non-HTML content type
    never enters a count, and the crawl's own `pages_ok` and `pages_crawled`
    include them. A museum's crawl fetched 105 addresses and 45 of them were
    images and documents: the header said "105 pages crawled" and one finding
    said "Seen on 5 of the 105 pages" about a site of 60 pages. Every count
    this file prints subtracts them, through `page_shaped_urls`, the one
    definition this file already has of a URL that could have been a page.
    """
    pages = snapshot.get("pages") or []
    return len(pages) - len(page_shaped_urls(snapshot)) if pages else 0


def compose(snapshot, skill_results, audited_at=None):
    crawl = snapshot.get("crawl") or {}
    not_pages = not_page_records(snapshot)
    # The shared count of crawled URLs that were pages, where the snapshot
    # carries its page records; the crawl's own figure otherwise, as before.
    pages_crawled = max(pages_the_crawl_read(snapshot) if snapshot.get("pages") else
                        (crawl.get("pages_ok") or crawl.get("pages_crawled") or 0), 1)
    brand = snapshot.get("brand") or {}

    signals = {}
    checks_run, not_applicable = [], []
    extra_requests = 0
    fired_checks = set()
    unresolved_checks = set()
    skills_that_did_not_run = []
    for result in skill_results:
        signals.update(result.get("signals") or {})
        # `or ""`, not `.get("skill")`. A skill result without a `skill` key
        # put None in here, and `sorted(checks_run, key=...)` below then raised
        # TypeError comparing None to a string, which killed the whole report
        # over one missing field.
        skill_name = result.get("skill") or ""
        for check in result.get("checks_run", []):
            checks_run.append({"skill": skill_name, "check": check})
        for check in result.get("fired_checks", []):
            fired_checks.add((skill_name, check))
        # Registered and unanswered, which is not the same as executed and
        # clean. `SkillResult.unresolved` explains why the skill cannot tell
        # them apart; `_checks_that_had_nothing_to_run_against` below is where
        # this is resolved. A result file written before the key existed omits
        # it, and an absent key means nothing is claimed either way.
        for check in result.get("unresolved_checks") or []:
            unresolved_checks.add((skill_name, check))
        for decline in result.get("not_applicable", []):
            # A skill that never ran is not a check that decided not to fire,
            # and it may not be printed under a heading reading "these were run
            # and found nothing to report" - its own reason says the opposite
            # in the same paragraph. Echoed into `unverifiable` rather than
            # moved, the same way `checks_left_unanswered` echoes a decline the
            # crawl gave nothing to decline on: the entry stays in
            # `not_applicable` so the appendix's buckets still add up to the
            # number of checks it says ran, and the renderers list it under the
            # unanswered questions instead of under the declines.
            if _is_a_skill_that_did_not_run(result, decline):
                skills_that_did_not_run.append(
                    dict(decline, skill=decline.get("skill") or skill_name))
            not_applicable.append(decline)
        extra_requests += result.get("extra_requests_made", 0)

    findings, duplicates = dedupe(skill_results)
    # After `dedupe` and before anything scores or ranks, because a folded pair
    # is one finding with one score and one place in the order. Run here rather
    # than in either skill: each half is raised by a different skill and
    # neither can see whether the other fired.
    findings, folded = fold_one_job_findings(
        findings,
        [(c["skill"], c["check"]) for c in checks_run],
        fired_checks)
    duplicates += folded
    # The same place and the same reason: one defect seen by two checks is one
    # finding with one score, one row in the severity table and one fix.
    findings, billed_twice = merge_one_defect_billed_twice(findings, snapshot, signals)
    duplicates += billed_twice
    findings, unverifiable = withhold_absence_claims(findings, snapshot)
    findings = price_one_observation_once(findings, snapshot)
    for finding in findings:
        value, reach = score(finding, pages_crawled)
        finding["suggested_action"]["priority"] = priority_label(value, finding["severity"])
        finding["suggested_action"]["priority_score"] = value
        finding["reach"] = reach
    haystack = observed_values(snapshot)
    # Read once for the whole report, not once per finding: it is a property of
    # the crawl, and forty findings deriving it separately is forty chances for
    # two of them to say different things about one site.
    site_language = language_of(snapshot)
    reading = language_note(site_language)
    # Read once, before any snippet is touched: the profile addresses this run
    # fetched and found dead. One skill measures them and a different skill
    # builds the block that would publish them, and neither can see the other.
    dead_profiles = profile_addresses_that_do_not_resolve(findings)
    for finding in findings:
        # Runs before the denominator sentence is appended, so it reads the
        # check's own count rather than one this file just wrote.
        reconcile_the_example_with_the_claim(finding)
        finding["evidence"] = state_the_coverage(
            state_the_denominator(finding, pages_crawled, snapshot),
            snapshot, pages_crawled)
        # Empty on a site established to be in English, and empty on a finding
        # whose claim never touched the prose. Set here rather than inside any
        # check because the rule is a property of the report: one place decides
        # it, so a check written next month cannot ship a prose claim without
        # it. `withhold_absence_claims` has already run, so nothing is
        # annotated that is not going to be published.
        finding["read_in"] = reading if prose_derived(finding) else ""
        action = finding["suggested_action"]
        if action.get("snippet"):
            action["snippet"], invented = hold_back_invented_values(
                action["snippet"], haystack)
            # A profile another finding in this same report proves is dead may
            # not be offered here for publishing. See
            # `profile_addresses_that_do_not_resolve`.
            action["snippet"], dropped = drop_dead_profile_links(
                action["snippet"], dead_profiles)
            if dropped:
                action["snippet_warning"] = (
                    (action.get("snippet_warning", "") + " ").lstrip()
                    + "{} dropped from `sameAs` below: this audit fetched {} and got "
                      "a response saying the page is not there, which is reported "
                      "separately. Publishing a profile address that does not resolve "
                      "is a claim the site makes that does not check out.".format(
                          _plural(len(dropped), "address was", "addresses were"),
                          "it" if len(dropped) == 1 else "them")).strip()
            if invented:
                action["snippet_warning"] = _snippet_warning(invented)
    findings = assign_ids(findings)
    # After the ids and after the evidence sentences are final, because the
    # promise is about the document rather than about an intermediate state: no
    # two *published* findings may assert opposite things about one page. Runs
    # before the tier loop below and before `ranked`, so the weaker half of a
    # disagreement is demoted in the counts, in the order and in "Start here"
    # rather than only in its own paragraph.
    flag_opposite_claims(findings)
    # Beside the contradiction guard and for the same reason: it is a rule
    # about what the whole document says, not about any one check, and it needs
    # the ids because the sentence it writes names one. The case: four findings
    # billed for one rendering fault. Nothing is merged or dropped here; the
    # record it writes is `follows_from`, on the finding itself, which is what
    # both renderers and report.json read.
    defer_to_the_page_that_did_not_arrive(findings, signals)
    # The same rule for a page that is a redirect, a cover page or a frame.
    defer_to_the_page_level_cause(findings, signals)
    # And for one defect two checks each report under their own root cause:
    # one dead address, one canonical tag. See `SAME_DEFECT_PAIRS`.
    defer_to_the_same_defect(findings, snapshot, signals)
    for finding in findings:
        finding["tier"] = published_tier(finding)

    # Severity first, then the priority score inside each band.
    #
    # Ranking on the score alone inverted the report against itself. `reach`
    # floors at 0.25 for a finding that lists the pages it affects, while a
    # finding listing none is treated as site-wide and gets 1.0 - so on a
    # sixty-page crawl, naming your evidence was a four-fold penalty. The
    # result on a real site: both `high` findings ranked below two `medium`
    # ones, and "add og:image" (low, site-wide, cheap) outranked "this site has
    # no machine-readable identity". The report told the owner to do the small
    # thing first, under a heading that said the opposite.
    #
    # The score still decides order within a band, which is where it earns its
    # keep: two mediums, the cheaper and wider one first.
    ranked = sorted(findings,
                    key=lambda f: (SEVERITY_RANK[f["severity"]],
                                   -f["suggested_action"]["priority_score"], f["id"]))
    # "Start here" tells an owner what to do first, so it may only ever draw
    # from the tier the audit is confident in. One real run put a
    # high-severity, medium-confidence finding first on one site and it was the
    # wrongest finding in the report: the first instruction the owner read was
    # to spend a day fixing something that was not wrong.
    # Filtered here rather than inside `_start_here_ids`, the same way the tier
    # is, so both rules about what may lead the report are visible where the
    # list is built. A finding that follows from another one in this same
    # report is not a thing to do first - its own fix cannot be carried out
    # until the cause is - and on one real storefront four of them were
    # competing with their own cause for three slots.
    # A finding half or more of whose pages did not arrive is, in its headline
    # count, the same fault as well, and competes with it for the same slots.
    confident = [f for f in ranked
                 if f["tier"] == CONFIDENT and not f.get("follows_from")
                 and not f.get("partly_follows_from")]
    # The one exception to that rule, and it is the verdict's. Where the
    # opening paragraph names a root cause and says most of the rest follows
    # from it, that finding leads this list whichever tier it is in - because a
    # report whose prose names the main problem and whose action list omits it
    # has failed at prioritising in the most visible place there is.
    counts = {level: sum(1 for f in findings if f["severity"] == level)
              for level in ("critical", "high", "medium", "low", "info")}
    # What the verdict weighs, and what the severity table counts: the findings
    # that stand on their own. A page read again from four angles is one
    # defect, and "12 medium-severity items" over a report whose four of them
    # say "this is the same defect as F-005" counted it five times.
    standing_counts = {level: sum(1 for f in findings
                                  if f["severity"] == level and not follows_another(f))
                       for level in counts}

    # Built before the verdict, because the verdict's closing sentence points
    # the reader at this section and must not do so when it is empty. The
    # snippets go through the same invented-value guard as the findings': a
    # paste-ready block is a paste-ready block whichever section it sits in.
    recommendations = build_recommendations(snapshot, signals, findings)
    for rec in recommendations:
        if rec.get("snippet"):
            rec["snippet"], invented = hold_back_invented_values(rec["snippet"], haystack)
            if invented:
                rec["snippet_warning"] = _snippet_warning(invented)

    read_pages = len(readable_text_pages(snapshot))
    # URLs that could have been pages, not every record. A release archive
    # that answered 200 is not a URL the crawl was refused a page at, and
    # counting it as one is what pushed this ratio under the bar.
    attempted = len(page_shaped_urls(snapshot))
    share = readable_share(snapshot)
    # Before the verdict, which reads it: the verdict may not say an assistant
    # has nothing to repeat above a table quoting what it would repeat.
    citations = simulate_citations(snapshot, brand.get("name") or "")
    # Written before "Start here", because which findings lead that list is a
    # question about what the verdict says, and the only way to know what it
    # says is to have written it.
    verdict = _verdict(standing_counts, findings, crawl, signals,
                       (snapshot.get("brand") or {}).get("name") or "",
                       readable_share=share,
                       has_recommendations=bool(recommendations),
                       readable_pages=read_pages,
                       pages_reached=attempted,
                       why_unread=why_little_was_read(snapshot),
                       origin_redirect=snapshot.get("origin_redirect"),
                       site_not_serving=snapshot.get("site_not_serving"),
                       quotable=what_an_assistant_can_repeat(signals, citations,
                                                             brand.get("name") or ""))
    # The findings the verdict names as the cause of the rest - and only when
    # it does name them. On a storefront delivered as a JavaScript shell the
    # verdict opened on the shell, and "Start here" still held a place for the
    # off-site-profile finding "because the verdict above names it as the
    # cause of the rest"; the verdict named nothing of the kind, and 13 of the
    # 17 findings were the shell read again.
    leads_the_verdict = verdict_leads(verdict, findings, signals)
    # The verdict's exception may not bring back a finding the render verdict
    # has already explained. On a shop whose pages arrive in the browser, "this
    # site publishes no structured data at all" held second place through this
    # exception, above the shell finding its own missing pages came from.
    explained = {f.get("id_hint") for f in findings
                 if f.get("follows_from") or f.get("partly_follows_from")}
    start_here = _start_here_ids(confident,
                                 leads_the_verdict=set(leads_the_verdict) - explained,
                                 all_findings=ranked)

    # Counted once, over both halves of the report, because the reader has to
    # carry out both halves. 7 to 9 of every 12 to 14 findings were
    # typed `low` / `developer` across four real reports and no report said so
    # anywhere; the words "your theme", "your developer" and "whoever built
    # the site" appear nowhere in 2,795 lines of output. For an owner with no
    # developer, "low effort" is not an hour of their evening - it is a person
    # they do not have, and finding that out on action nine is the point at
    # which they stop reading.
    actions = [f["suggested_action"] for f in findings
               if f["severity"] != "info"] + list(recommendations)
    needs_a_template_editor = sum(
        1 for a in actions if a.get("owner") in TEMPLATE_EDITOR_OWNERS)

    # `read_pages`, `attempted` and `share` were measured above, for the verdict.
    # `and not site_not_serving`, explicitly, rather than trusting the page
    # counts to fall through the floor on their own. A maintenance page served
    # with a 503 can carry a paragraph of readable text on every address, and
    # a crawl of enough of those would clear a floor that only counts pages -
    # then grade the readability of an outage notice as though it were a site.
    enough_to_judge = (enough_to_grade(read_pages, share)
                       and not snapshot.get("site_not_serving"))
    unverifiable = unverifiable + checks_left_unanswered(
        sorted(not_applicable, key=lambda n: (n.get("skill") or "", n["check"])),
        read_pages, attempted)
    # Listed first among the unanswered questions, and deliberately: an absence
    # claim held back names one thing the audit could not settle, and a skill
    # that did not run names everything that skill would have looked at.
    unverifiable = [{
        "title": "{}: this skill did not run".format(item.get("skill") or "a sub-skill"),
        "check": item.get("check"),
        "detected_by": item.get("skill"),
        # The stand-in's own sentence, which names how long it had and what to
        # change to give it more. Nothing is composed here that `run_audit.py`
        # already said better.
        "reason": item.get("reason") or (
            "this skill did not run, so everything it examines is unreported rather than "
            "clean"),
    } for item in skills_that_did_not_run] + unverifiable
    # Resolved here rather than in the skills, because the fact that settles it
    # - whether the crawl produced anything to examine - is only visible from
    # here. Empty on every run that read pages, so nothing about a healthy
    # report changes.
    nothing_to_run_against = checks_with_nothing_to_run_against(
        unresolved_checks, not_applicable, fired_checks, findings, read_pages)
    # Which skills this report can speak for completely. Read after every step
    # that can remove a finding, because that is the fact it counts, and used
    # only by the two appendix buckets below.
    published_whole = skills_whose_every_finding_is_published(skill_results, findings)
    unexecuted_pairs = {(c["skill"], c["check"]) for c in nothing_to_run_against}
    # The (skill, check) pairs a dropped skill's stand-in registered. Not added
    # to `nothing_to_run_against`: they are already counted once under
    # `not_applicable`, and counting them twice would make the appendix
    # paragraph a reader can add up stop adding up. This set is what tells the
    # renderers to print them under the unanswered questions instead.
    did_not_run_pairs = sorted(
        {(item.get("skill") or "", item.get("check") or "")
         for item in skills_that_did_not_run if item.get("check")})

    report = {
        "site": snapshot["site"],
        "audited_at": audited_at or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            # Every tier, as before. The counts are what a schema consumer and
            # the contest read, and splitting the report into two tiers may not
            # quietly shrink them: a demoted finding is published, not dropped,
            # so it is still counted here and still in `findings[]` below.
            "total_findings": len(findings),
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "info": counts["info"],
            # How those totals split across the two tiers, so a reader of the
            # numbers alone can see how much of the report is a firm reading.
            "confident": sum(1 for f in findings if f["tier"] == CONFIDENT),
            "worth_checking": sum(1 for f in findings if f["tier"] == WORTH_CHECKING),
            # Of the totals above, how many are another finding's defect read
            # again. The severity table prints them on a row of their own
            # rather than counting them a second time.
            "follow_from_another_finding": sum(1 for f in findings if follows_another(f)),
            # Both halves of the report, counted together, because the reader
            # has to carry out both halves.
            "actions": len(actions),
            "actions_needing_a_template_editor": needs_a_template_editor,
            "verdict": verdict,
        },
        # One array, both tiers, each finding carrying `tier`.
        #
        # The alternative was a second top-level array, and it fails the thing
        # the split is for. A consumer that reads `findings[]` and does not
        # know about the new key - the contest's own schema floor is exactly
        # such a consumer - would see the demoted findings vanish from the
        # report entirely, which counts as a
        # miss. Keeping them here means an unaware reader sees exactly the
        # report they saw before, an aware one gets the split, and
        # `summary.total_findings` needs no asterisk. The tier is carried
        # alongside as an id list, the way `start_here` already is, so a
        # renderer never has to re-derive it.
        "findings": [_public_finding(f) for f in findings],
        "start_here": start_here,
        # Ids of the findings the verdict's opening paragraph names as the
        # cause of the rest. They lead `start_here` whichever tier they are in,
        # so a consumer that re-ranks the list has to know which entries are
        # there for that reason - and one of them arriving from the lower tier
        # is an entry to confirm before anything is spent on it.
        "leads_the_verdict": [f["id"] for f in ranked
                              if f.get("id_hint") in leads_the_verdict],
        # Ids of the findings in the lower tier, in the same ranked order the
        # report prints them.
        "worth_checking": [f["id"] for f in ranked if f["tier"] == WORTH_CHECKING],
        "recommendations": recommendations,
        "citation_simulation": citations,
        "crawl": {
            "origin": snapshot["origin"],
            "brand_name": brand.get("name"),
            "brand_name_source": brand.get("source"),
            # Which markup declared a name for this site, whether or not the
            # audit adopted one of them. The top sheet once told a reader
            # "nothing in its markup - no og:site_name, no Organization name -
            # outranked the title" on a site carrying an Organization name on
            # 48 of its 60 crawled pages. "Nothing outranked it" and "there is
            # nothing" are different facts and the sheet had only the first.
            "brand_name_declared_in": list(brand.get("authoritative_sources") or []),
            # Minus the responses that proved they are no page - an image, a
            # PDF, a stylesheet answering 200. See `not_page_records`: the
            # README says those never enter a count, and these two are the
            # counts every "of the N pages" sentence below is sized against.
            # The header's "N pages crawled" is every address the crawl asked
            # for a page at, and a host whose handshake failed was still asked:
            # "1 page crawled, 0 readable" is the true first line there. What
            # leaves it is only a response that proved it was never a page.
            "pages_crawled": max(0, crawl.get("pages_crawled", 0) - not_pages),
            "pages_ok": max(0, crawl.get("pages_ok", 0) - not_pages),
            "non_page_responses": not_pages,
            # Three counts of the same crawl, narrowing, and each answers a
            # different question. They must never be read as alternatives.
            #
            #   pages_ok      a 200 came back, whatever it was: a PDF, a body
            #                 this client could not decode, an off-origin
            #                 redirect stub. Says nothing about content.
            #   pages_read    the crawler's own count of 200s it did not skip,
            #                 so the audit has the document. "Did a page come
            #                 back at all."
            #   readable_pages  those, minus bot-manager challenges, minus any
            #                 page carrying less than one quotable sentence.
            #                 "Did anything come back that can be read."
            #
            # The floor gates on the third and deliberately not the second,
            # because the sentence this whole guard exists to stop - "no
            # blocking or significant problems were found" - rests on what the
            # checks could read, not on what the socket returned. The third is
            # always the smallest of the three, so the report can never claim
            # more coverage than the crawler recorded.
            "pages_read": crawl.get("pages_read", read_pages),
            "readable_pages": read_pages,
            # The denominator `state_the_denominator` divides a finding by
            # when the finding did not size itself: the pages that answered.
            # Published because it is a fourth number, distinct from the three
            # above, and adding up one report found the top sheet
            # naming 49 and the body dividing by 53 with nothing saying which
            # was which. The sheet now states the bound rather than this
            # figure - a check that sized itself keeps its own count and this
            # value does not govern it - and a consumer reading `crawl{}` can
            # see the one the orchestrator used.
            "pages_counted_against": pages_crawled,
            "enough_to_judge": enough_to_judge,
            # What was typed, next to what answered. `site` and `origin` name
            # the host the pages actually came from, which is honest and, on
            # its own, silent about the fact that a person asked about a
            # different one.
            "requested_origin": snapshot.get("requested_origin") or snapshot.get("origin"),
            "requested_seed_url": snapshot.get("requested_seed_url"),
            "origin_redirect": snapshot.get("origin_redirect"),
            # Null on an ordinary site. When set, every address this crawl
            # reached answered the same status and that status speaks for the
            # service rather than the address, so the snapshot carries no
            # evidence about content and cannot support a verdict of any kind,
            # a clean one included. Same standing as `origin_redirect` with
            # `followed: false`, and carried here for the same reason: a
            # consumer reading `crawl{}` has to be able to see it without
            # parsing the verdict prose.
            "site_not_serving": snapshot.get("site_not_serving"),
            "budget_exhausted": bool(crawl.get("budget_exhausted")),
            # What fixed this crawl's page count, written by `crawl.py`:
            # `planned_pages` and the measured cost it came from,
            # `deterministic` (false when the wall clock or a rate limiter
            # ended the crawl rather than the plan) and `truncated_at`.
            # Republished here because the appendix's claim about whether a
            # second audit of this site would agree with this one has to be
            # read off it rather than asserted, and because a consumer of
            # report.json is entitled to the same answer without opening the
            # snapshot.
            "page_plan": crawl.get("page_plan") or {},
            # The language record every prose claim in this report was read
            # under: `code` (empty where only the script is known), `script`,
            # and `source`, which says how it was arrived at. Published once
            # here so a consumer can read it without parsing the sentence
            # `findings[].read_in` prints, and so the whole report has exactly
            # one answer to the question.
            "site_language": site_language,
            "render_mode": crawl.get("render_mode", "static"),
            "elapsed_s": crawl.get("elapsed_s"),
            "extra_requests_made": extra_requests,
            "user_agent": crawl.get("user_agent"),
            "notes": notes_without_a_repeatability_promise(crawl.get("notes") or []),
        },
        "checks_run": sorted(checks_run, key=lambda c: (c["skill"], c["check"])),
        # `checks_registered` and `checks_not_reached`. See
        # `checks_the_run_did_not_reach`.
        **checks_the_run_did_not_reach(checks_run),
        # What the site says it is, in its own words, and what kind of site
        # this audit took it for. Both printed on the top sheet; see
        # `what_the_site_says_it_is` and `site_kind_record`.
        "site_says": what_the_site_says_it_is(snapshot, signals),
        "site_kind": site_kind_record(snapshot),
        # Every check now lands in exactly one of three buckets: it fired, it
        # declined and said why, or it ran and was clean. Twelve used to fall
        # through all three and appear nowhere - the ones that confirm robots.txt
        # was fetched and the homepage answered, which are precisely what a
        # worried reader is looking for.
        "checks_passed": _checks_passed(checks_run, not_applicable, fired_checks,
                                        findings, unexecuted_pairs),
        # The fifth bucket. A check here was registered and never answered, on
        # a crawl with nothing for it to answer about, so it may not be printed
        # as clean - the failure being a store answering HTTP 402 on every URL
        # whose report listed `homepage-reachable` among the checks that "ran
        # against this site and were clean".
        "checks_with_nothing_to_run_against": nothing_to_run_against,
        # Sub-skills that did not run at all, as (skill, check) pairs. Their
        # declines are still in `not_applicable` above, because that is where
        # the appendix counts them; this names them so the renderers can print
        # them under "Questions this audit could not answer" rather than under
        # a heading saying the checks below were run.
        "skills_that_did_not_run": [{"skill": skill, "check": check}
                                    for skill, check in did_not_run_pairs],
        # Checks a finding came out of, listed so that every check which ran is
        # in exactly one of four places. Without this, a check registered
        # alongside the one a finding names appeared in none of them: not a
        # finding, not a decline, not a pass. Four of seventy-three on one real
        # site, and the README promises that never happens.
        "checks_that_found_something": _checks_that_found_something(
            checks_run, not_applicable, fired_checks, findings, published_whole),
        # The half of that bucket with no finding above it: the check fired,
        # and what it found was held back or dropped rather than published.
        # Printed under its own heading, because filing it under "contributed
        # to a finding above" was a sentence a reader could and did disprove by
        # looking for the finding.
        "checks_that_found_nothing_published": _checks_that_found_nothing_published(
            checks_run, not_applicable, fired_checks, findings, published_whole),
        # `or ""` for the same reason as `checks_run`: a present-but-null skill
        # is not caught by a default, and sorting None against a string raises.
        "not_applicable": sorted(not_applicable,
                                 key=lambda n: (n.get("skill") or "", n["check"])),
        "merged_duplicates": duplicates,
        # Questions the audit could not answer, kept separate from defects it
        # found. A blocked crawl used to turn every one of these into a
        # confident claim about the site.
        "unverifiable": unverifiable,
        "auditor": {"name": "brand-ai-readiness-audit", "version": VERSION},
    }
    return report


def _public_finding(finding):
    """Required keys first, extensions after. Ordering is for readers, not code."""
    action = finding["suggested_action"]
    out = {
        "id": finding["id"],
        "title": finding["title"],
        "severity": finding["severity"],
        "evidence": finding["evidence"],
        "suggested_action": {
            "summary": action["summary"],
            "priority": action["priority"],
            "priority_score": action["priority_score"],
            "effort": action["effort"],
            "owner": action["owner"],
            "how_to_fix": action["how_to_fix"],
            "rationale": action["rationale"],
        },
        "confidence": finding["confidence"],
        # How this finding's evidence was obtained: `observed` means the claim
        # is settled by what the document or the HTTP exchange literally
        # contains, `inferred` that it also rests on this audit having decided
        # what something is. Published beside the tier because it is the reason
        # for the tier, and because a reader deciding whether to act on a
        # finding wants the reason rather than the verdict.
        "evidence_basis": evidence_basis(finding),
        # Which tier of the report this is published in, decided by
        # `published_tier` from the evidence basis and the check's own
        # confidence. Carried on the finding so a consumer reading `findings[]`
        # alone can tell a firm reading from a weaker one.
        "tier": finding["tier"],
        "mechanism": finding["mechanism"],
        "mechanism_description": MECHANISMS[finding["mechanism"]],
        "root_cause": finding["root_cause"],
        # `id` is F-001, F-002 ... assigned in order, so fixing one renumbers
        # every finding after it and the same problem has a different id in the
        # next run. A developer comparing a before and an after had to match on
        # root_cause by hand. This is the handle to compare on: it names the
        # specific problem and does not move.
        #
        # Written by `stable_names`, not read off `id_hint` here. The two are
        # the same string for every finding whose check raised exactly one, and
        # differ where a check raised two - which is the case that made this a
        # label rather than an id. Both renderers print it.
        "stable_id": finding.get("stable_id") or finding["id_hint"],
        "check": finding.get("check"),
        # What a claim of absence rests on. A finding that says the site does
        # not have something is only as good as the places it looked, and
        # every false positive this marketplace has made on a real site was
        # one detector with a finite list of shapes reported as a fact. The
        # reader gets to see the list.
        "checked": finding.get("checked") or [],
        # The script and language this finding's prose was read in, where that
        # is not the language the word patterns are written for. Beside
        # `checked` rather than inside it: `len(checked)` decides the tier of a
        # `medium` absence claim, so a sentence appended there would promote
        # readings instead of qualifying them. Empty string on a finding that
        # read no prose and on a site established to be in English, so an
        # English report carries exactly the fields it carried before.
        "read_in": finding.get("read_in") or "",
        # `.get`, like the three other readers of these two keys. A sub-skill
        # that omits `affected_page_count` - a site-wide finding with no page
        # list is the obvious case - killed composition outright with a
        # KeyError, so one skill's omission cost the whole report.
        "affected_pages": finding.get("affected_pages") or [],
        "affected_page_count": finding.get("affected_page_count") or 0,
        "reach": finding["reach"],
        "detected_by": finding["detected_by"],
    }
    # One of this tool's claims was disproved by hand, and the reader who did
    # it could not check the claim from the report: the title said 48 pages and
    # `affected_pages` held five, with nothing in report.json saying that the
    # five were a sample or how they were chosen. Both numbers were already
    # published and a consumer could compare their lengths; a person reading
    # the file could not, and neither could tell which five.
    #
    # The truncation itself is `make_finding` in `audit_common.py`, which
    # writes `sorted(set(pages))[:5]`, so by the time a finding reaches this
    # file the other 43 addresses no longer exist anywhere in the run. What can
    # be said here is what the five are and where the count comes from, which
    # is what makes the sample reproducible: the reader knows they are the
    # first five in ascending order and not, say, the five worst.
    #
    # The key is absent whenever the list is the whole population, so a report
    # in which nothing was sampled carries nothing new.
    listed, total = len(out["affected_pages"]), out["affected_page_count"]
    if total > listed:
        out["affected_pages_are_a_sample"] = (
            "{} of {}, and not a ranking: they are the first {} of this finding's affected "
            "addresses in ascending alphabetical order. The full count is "
            "`affected_page_count`; the audit records the {} listed here and no "
            "more.".format(listed, plural(total, "page"), listed, listed))
    if "snippet" in action:
        out["suggested_action"]["snippet"] = action["snippet"]
    if action.get("snippet_warning"):
        out["suggested_action"]["snippet_warning"] = action["snippet_warning"]
    if finding.get("also_detected_by"):
        out["also_detected_by"] = finding["also_detected_by"]
    # Published, not just acted on. A consumer reading `findings[]` has to be
    # able to see that two entries in the array it is reading disagree about
    # one page, and which of the two this audit is entitled to assert. Absent
    # on every finding nothing contradicts, so a report with no disagreement in
    # it carries no new key.
    if finding.get("disputed_by"):
        out["disputed_by"] = finding["disputed_by"]
    # Published for the same reason: a consumer reading `findings[]` has to be
    # able to see that this entry is a consequence of another one in the array
    # rather than a separate job, without parsing the prose. Absent on every
    # finding that stands on its own.
    if finding.get("follows_from"):
        out["follows_from"] = finding["follows_from"]
    if finding.get("partly_follows_from"):
        out["partly_follows_from"] = finding["partly_follows_from"]
    return out


NON_PUBLIC_HOST_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0"
    r"|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+"
    r"|[^/\s]*\.(?:local|test|localhost|invalid|internal))", re.I)


# One wording, printed by report.md and report.html alike. It used to exist
# only in the markdown renderer, so an HTML reader was handed a paste-ready
# block full of `http://localhost:8000/` with nothing saying so.
NON_PUBLIC_HOST_WARNING = (
    "Change the URLs before you use this. It is filled in with the address that was audited, "
    "which is not a public one, so the `url`, `logo` and any `offers.url` values below point "
    "somewhere nobody else can reach.")


def _non_public_host(text):
    """Is this snippet filled in with an address nobody else can reach?"""
    return bool(NON_PUBLIC_HOST_RE.search(text or ""))


# A whole skill can be missing from a run. `run_audit.py` no longer throws a
# completed crawl and five completed skills away when the sixth overruns - it
# writes a stand-in result and carries on, which turned one site's exit code 1
# after 273 seconds with no report at all into a report - but the stand-in
# arrives shaped like a decline, and a decline prints under "Checks that did
# not apply, and why: each of these ran". Its own reason says the opposite of
# that heading, in the same paragraph.
#
# A skill that did not run is the largest gap an audit can have, so it goes to
# the `unverifiable` channel and the "Questions this audit could not answer"
# section, first in that list, with the same standing as an absence claim held
# back for want of coverage. Echoed there rather than moved: the decline stays
# in `not_applicable` because that is where the appendix counts it, and the
# renderers list it under the unanswered questions and say so under the
# declines, so a reader adding the bullets up is never one short.
#
# Three ways to say so, checked in order of how much I would like them to
# exist. A flag on the result, a flag on the decline, and last the check name -
# named here as a constant rather than typed into a condition, because a magic
# string agreed between two files and written down in neither is exactly the
# coupling that rots. The first two cost `run_audit.py` one key.
SKILL_DID_NOT_RUN_CHECK = "skill-did-not-run"


def _is_a_skill_that_did_not_run(result, decline):
    """Is this decline a whole skill reporting that it never ran?"""
    return bool(result.get("skill_did_not_run")
                or decline.get("did_not_run")
                or decline.get("check") == SKILL_DID_NOT_RUN_CHECK)


def _checks_passed(checks_run, not_applicable, fired, findings=(), unexecuted=()):
    """Checks that ran, produced no finding, and had no reason to decline.

    The README promises that every check which stays quiet says why. That was
    true of the checks which declined and false of the ones that simply passed:
    they appeared in neither list, so a reader could not tell that robots.txt
    had been fetched at all.

    A check a finding names is excluded even when its skill did not mark it as
    fired. It did not find nothing wrong - a finding above says what it found -
    and counting it here as well as in the appendix's "named by a finding"
    total is how the four buckets came to add up to more checks than ran.

    `unexecuted` is the fifth bucket, and it is excluded here rather than
    merged: a store answering HTTP 402 on every URL, with zero pages fetched,
    printed `homepage-reachable` and `non-200-rate` as clean. Registered and
    executed are different facts and only the second can become a pass.
    """
    declined = {(n.get("skill") or "", n["check"]) for n in not_applicable}
    named = named_check_pairs(findings)
    passed = [c for c in checks_run
              if (c["skill"], c["check"]) not in declined
              and (c["skill"], c["check"]) not in fired
              and (c["skill"], c["check"]) not in named
              and (c["skill"], c["check"]) not in unexecuted]
    return sorted(passed, key=lambda c: (c["skill"], c["check"]))


def checks_with_nothing_to_run_against(unresolved, not_applicable, fired,
                                       findings, readable_pages):
    """Registered, unanswered, on a crawl that read nothing to answer with.

    A skill records a check as registered before it looks at anything, so a
    check whose data never arrived is indistinguishable, inside that skill,
    from one that examined its data and was satisfied. What settles it is a
    fact only this file holds: whether the crawl produced any readable page at
    all. Below `VERDICT_MIN_READABLE_PAGES` the premise every pass rests on -
    that there was something to be clean about - did not hold.

    The same floor and the same reasoning as `checks_left_unanswered` above,
    which does this for the checks that declined. One rule, two lists, so the
    appendix cannot say one thing about a decline and another about a silence
    on the same run.

    "It did not run" would be false for a few of these: the checks that fetch
    robots.txt or probe a sitemap make their own requests and do reach an
    answer. What is true of all of them is that nothing they found is a
    statement about the site, which is what the appendix's wording says.

    Above the floor this returns nothing, and an unresolved check is published
    as a pass exactly as it is today. A check that never executed on a crawl
    that did read pages is the skill's own to declare with `skip()`; this is
    the guard for the case where there was nothing to declare on.
    """
    if readable_pages >= VERDICT_MIN_READABLE_PAGES:
        return []
    declined = {(n.get("skill") or "", n["check"]) for n in not_applicable}
    named = named_check_pairs(findings)
    out = [{"skill": skill, "check": check}
           for skill, check in unresolved
           if (skill, check) not in declined
           and (skill, check) not in fired
           and (skill, check) not in named]
    return sorted(out, key=lambda c: (c["skill"], c["check"]))



def _fired_but_unnamed(checks_run, not_applicable, fired, findings):
    """Checks marked as fired that no published finding names.

    A finding carries the one check that was current when it was raised. Its
    siblings - registered in the same function, any of which the finding might
    equally be about - are marked as having fired, which correctly keeps them
    out of the passed list and then left them nowhere at all. They are the
    fourth bucket, and with it every check that ran is in exactly one.
    """
    named = named_check_pairs(findings)
    declined = {(n.get("skill") or "", n["check"]) for n in not_applicable}
    out = [c for c in checks_run
           if (c["skill"], c["check"]) in fired
           and (c["skill"], c["check"]) not in declined
           and (c["skill"], c["check"]) not in named]
    return sorted(out, key=lambda c: (c["skill"], c["check"]))


def skills_whose_every_finding_is_published(skill_results, findings):
    """Skills all of whose findings survived into this report, and at least one.

    Counted rather than tracked, on purpose. A finding raised by a skill can
    leave the report at four points - merged into another skill's by `dedupe`,
    folded into its pair by `fold_one_job_findings`, held back by
    `withhold_absence_claims`, collapsed by `price_one_observation_once` - and
    a hand-kept list of those points is the thing that drifts the moment a
    fifth is added. Comparing what each skill emitted against what the report
    publishes for it needs no list and cannot go stale.
    """
    raised = collections.Counter(
        result.get("skill") or ""
        for result in skill_results for _ in result.get("findings") or [])
    published = collections.Counter(f.get("detected_by") or "" for f in findings)
    # Folded into another finding as one defect, and printed there whole.
    published.update(m.get("skill") or "" for f in findings for m in f.get("merged_from") or [])
    return {skill for skill, count in raised.items()
            if count and published.get(skill) == count}


def _checks_that_found_something(checks_run, not_applicable, fired, findings,
                                 skills_published_whole=()):
    """Fired, unnamed, and there provably is a finding above they belong to.

    The heading over this list says "Checks that contributed to a finding
    above" and the appendix under it says "with them, every check that ran is
    accounted for". One real report had `sitemap-present`,
    `sitemap-parses` and `unknown-paths-return-200` sat under that heading and
    the report contained no such finding. The accounting was whole; the
    sentence describing this bucket was false, which is worse, because a
    reader who checks one claim and finds it wrong stops trusting the ones
    they cannot check.

    What a check in this bucket actually is: `SkillResult.add` marks every
    check its finding's own function registered, so a check here fired
    *alongside* some finding that skill raised. Which one is not recorded
    anywhere - the skill result carries `fired_checks` as one flat set - so
    this file cannot name the finding a given check belongs to, and an earlier
    attempt to file by "the skill published something" left the real case
    exactly where it was: `crawl-access-audit` published two findings on a
    catch-all origin, so its sitemap checks were called contributors whether
    or not the sitemap finding was among them.

    The rule that needs no such attribution: a check may be filed here only
    where **every** finding its skill raised is published above. Then whichever
    of them swept this check is one a reader can find, and the heading is true
    of every entry without this file having to say which. Where a skill has a
    finding that did not survive - merged, folded, held back - none of its
    unnamed checks can be placed, and they all go to
    `_checks_that_found_nothing_published`, whose blurb says so.

    The exact fix is one key: `add()` already computes the group it stamps, so
    writing it onto the finding as `fired_with` would let this be answered per
    check rather than per skill. That is a change to `audit_common.SkillResult`,
    which this file does not own.

    Nothing leaves the appendix either way: the two lists together are what
    this one used to be, so the arithmetic still adds up.
    """
    whole = set(skills_published_whole)
    return [c for c in _fired_but_unnamed(checks_run, not_applicable, fired, findings)
            if c["skill"] in whole]


def _checks_that_found_nothing_published(checks_run, not_applicable, fired, findings,
                                         skills_published_whole=()):
    """The other half: fired, and no finding in this report is filed under them."""
    contributed = {(c["skill"], c["check"]) for c in _checks_that_found_something(
        checks_run, not_applicable, fired, findings, skills_published_whole)}
    return [c for c in _fired_but_unnamed(checks_run, not_applicable, fired, findings)
            if (c["skill"], c["check"]) not in contributed]


def named_check_pairs(findings):
    """The (skill, check) pairs a finding in this report names.

    Keyed on the pair, not on the bare check name, because the other three
    buckets in the appendix are keyed on the pair and the four are added
    together in one sentence. Counting distinct names against pairs printed
    "2 checks were run across 2 skills: 1 named by a finding above, 0 that did
    not apply, 0 that ran clean, 0 that contributed", where 1 has to be 2; and
    it over-counted the other way too, giving 3 named against 2 run, when two
    skills registered a check of the same name.
    """
    named = {(f.get("detected_by") or "", f.get("check"))
             for f in findings if f.get("check")}
    # A finding `merge_one_defect_billed_twice` folded into another is still
    # printed, inside the one it joined, so its check is still named.
    named |= {(m.get("skill") or "", m.get("check"))
              for f in findings for m in f.get("merged_from") or [] if m.get("check")}
    return named

# The kinds of page a key-pages list leads with, in order: what the site is,
# how to reach it, what it offers, how to use it. Pricing last, because on a
# site that sells nothing the crawl's "pricing" is whatever page mentions a fee.
LLMS_KEY_PAGE_TYPES = ("about", "contact", "product", "service", "documentation", "faq",
                       "pricing")
# The least a key page has to say in its own words.
LLMS_KEY_PAGE_MIN_TEXT = 300
# A form this many fields long, that is neither a search box nor a newsletter
# box, is the page's whole job - a sign-up or an application - unless the page
# is the contact page, whose form is the point.
LLMS_FORM_PAGE_FIELDS = 6


def llms_txt_key_pages(snapshot, signals=None):
    """`[(label, url)]` - pages worth naming as a site's key pages, one per kind.

    A page that answered 200 with content of its own. Not a page that sends
    the browser on by script, and not a sign-up form: one report's starter
    file named "[Pricing](.../membership.do)" as a museum's key page - a
    member sign-up that redirects by script, which the same report set aside
    as a page with no content.
    """
    signals = signals or {}
    redirects = set(signals.get(SCRIPT_REDIRECT_SIGNAL) or [])
    titles = {}
    for page in pages_of(snapshot):
        title = (page.get("title") or "").strip()
        if title:
            titles[title] = titles.get(title, 0) + 1

    def has_content(page):
        if page["url"] in redirects or page.get("script_redirect"):
            return False
        if (page.get("body_text_len") or 0) < LLMS_KEY_PAGE_MIN_TEXT:
            return False
        if page.get("page_type") != "contact" and any(
                (form.get("field_count") or 0) >= LLMS_FORM_PAGE_FIELDS
                and not form.get("is_search") and not form.get("is_newsletter")
                for form in page.get("forms") or []):
            return False
        return True

    seen, out = set(), []
    for page_type in LLMS_KEY_PAGE_TYPES:
        for page in pages_of(snapshot, types=(page_type,)):
            if page["url"] in seen or not has_content(page):
                continue
            seen.add(page["url"])
            # Not the page's <title> when titles are duplicated across the
            # site: this template labelled a product page "Coppergate Ceramics"
            # and the FAQ page "About", because it copied the very duplicate
            # titles the same report flags as a defect. A report that
            # propagates the bug it is reporting is worse than one that says
            # nothing.
            title = (page.get("title") or "").strip()
            label = title if title and titles.get(title, 0) == 1 else \
                page_type.upper()[:1] + page_type[1:]
            out.append((truncate(label, 60), page["url"]))
            break
    return out


def _llms_txt_template(snapshot, brand, signals=None):
    """A starter /llms.txt listing pages that actually exist on this site.

    It used to hard-code `<origin>/pricing` and `<origin>/about`. On a site
    with neither - which is most small sites - the file labelled paste-ready
    told the owner to publish two links that 404 on their own domain, guessed
    from a naming convention the crawl had already disproved.
    """
    lines = ["- [{}]({}): <one line saying what is on this page>".format(label, url)
             for label, url in llms_txt_key_pages(snapshot, signals)]
    if not lines:
        lines.append("- [<page name>](<url>): <one line saying what is on this page>")

    header = ("# {0}\n\n> {0} is a <category> that <does what> for <whom>.\n\n"
              "## Key pages\n\n")
    return header.format(brand) + "\n".join(lines) + "\n"


# Which findings entitle the report to say the site declares no identity
# markup, and which one says the opposite about the same site.
#
# A school's 60-page crawl carries `Organization`
# JSON-LD on 48 pages and not on the homepage. The verdict - the first prose
# anybody reads - said "no page carries Organization or LocalBusiness markup",
# 60 lines above the report's own finding saying "The homepage declares no
# Organization markup, though the rest of the site does" and above a
# paste-ready snippet reprinting the declared name.
#
# The cause was a denylist. `no-org-schema` is raised by four checks, the read
# below was "any of them except the branch-pages one", and two of the four say
# the site *has* the markup. An allowlist is the only shape that cannot go
# wrong the same way again: a check added later says nothing about the whole
# site until somebody adds it here, and `test_the_verdict_reads_the_whole_site`
# fails the build until they do.
IDENTITY_MARKUP_ABSENT_HINTS = frozenset({
    # "No Organization or LocalBusiness markup anywhere on the site" - the
    # only check that reads every crawled page before it says "anywhere".
    "no-organization-schema",
    # Folds the others. It replaces the six absent-markup findings only where no
    # crawled page carries JSON-LD, Microdata or RDFa of any kind, so it makes
    # the same claim about the same population.
    "no-structured-data-at-all",
})

# The site declares the block and the front door omits it. A real gap, a
# different sentence, and the reason this is a set of its own rather than an
# omission from the one above: the verdict may name it, and may not use it to
# say the site has no identity markup.
IDENTITY_MARKUP_HOMEPAGE_ONLY_HINTS = frozenset({
    "homepage-declares-no-organization-schema",
})


def identity_markup_gap(findings):
    """`(nowhere, homepage_only)` - what the published findings support.

    Read by `verdict_diagnosis` and by `_verdict_body`, so the paragraph and
    the action list it drives cannot disagree about what the site declares.

    `nowhere` is what licenses "no page carries Organization or LocalBusiness
    markup". `homepage_only` licenses "the homepage carries none, though other
    pages do" and nothing stronger. A finding raising `no-org-schema` under any
    other id hint - the branch-pages one, or a check written next month -
    licenses neither, because neither sentence would be a claim it measured.
    """
    hints = {f.get("id_hint") for f in findings
             if f.get("root_cause") == "no-org-schema"}
    return (bool(hints & IDENTITY_MARKUP_ABSENT_HINTS),
            bool(hints & IDENTITY_MARKUP_HOMEPAGE_ONLY_HINTS))


def verdict_diagnosis(findings, signals):
    """The id hints the verdict's identity paragraph rests on, or an empty set.

    One function, read by `_verdict_body` to decide what to say and by
    `compose` to decide what "Start here" leads with, so the two cannot
    disagree. One real report's verdict read "What it cannot do is
    establish who the brand is: no page states in one sentence what it is...
    Most of what follows is a consequence of that", and whose action list did
    not contain that finding at all. A report that names a root cause in its
    most-read sentence and leaves it off the list of what to do first has
    failed at prioritising in the one place everybody looks.

    Returns id hints rather than root causes because `no-org-schema` is raised
    by four checks and only two of them are what this paragraph is about - the
    same distinction `_verdict_body` draws for its own wording, and both draw
    it through `identity_markup_gap` so they cannot draw it differently.
    """
    signals = signals or {}
    causes = {f["root_cause"] for f in findings}
    breadth = signals.get("profile_breadth")
    no_org, homepage_only = identity_markup_gap(findings)
    no_definition = "no-entity-definition" in causes
    uncorroborated = ("weak-corroboration" in causes
                      or (breadth is not None and breadth <= THIN_PROFILE_BREADTH))
    if not ((no_org or no_definition) and uncorroborated):
        return set()
    named = set()
    for finding in findings:
        cause, hint = finding["root_cause"], finding.get("id_hint")
        if cause == "no-entity-definition" and no_definition:
            named.add(hint)
        elif cause == "no-org-schema" and (
                (no_org and hint in IDENTITY_MARKUP_ABSENT_HINTS)
                # Named only because the paragraph names it, and it never
                # makes the paragraph print: a site declaring the block on 48
                # of 60 pages has established who it is, whatever its homepage
                # template omits.
                or (homepage_only and hint in IDENTITY_MARKUP_HOMEPAGE_ONLY_HINTS)):
            named.add(hint)
        elif cause == "weak-corroboration":
            named.add(hint)
    return named


# The clause every identity paragraph of the verdict carries, whichever of its
# wordings prints. `verdict_leads` reads it to know that paragraph printed.
IDENTITY_VERDICT_CLAUSE = "establish who the brand is"


def verdict_leads(verdict, findings, signals):
    """The id hints of the findings the written verdict names as the cause.

    Read off the verdict that was actually written, not off the findings
    alone. `verdict_diagnosis` says what the identity paragraph would rest on,
    and was consulted whether or not that paragraph printed: on a storefront
    whose verdict opened on its JavaScript shell, the off-site-profile finding
    still held a place in "Start here" as the cause the verdict named. Now:
    the identity paragraph printed, so its findings lead; or the verdict
    opened on the most serious findings, so those lead; or nothing does.
    """
    if IDENTITY_VERDICT_CLAUSE in (verdict or ""):
        return verdict_diagnosis(findings, signals)
    named = {f.get("id_hint") for f in findings
             if f.get("severity") == "critical" and f.get("id") and f["id"] in (verdict or "")}
    return named - {None}


def _start_here_ids(ranked, limit=3, leads_the_verdict=(), all_findings=()):
    """The first three things to do, drawn from findings that are worth doing.

    "Start here" answers "what should I do first", and a `low` finding is by
    definition not that. Ranking on the priority score alone put missing Open
    Graph tags - which control what a link preview looks like when shared -
    third, above fixing structured data that does not parse, because a `low`
    finding still scores 1 and a cheap site-wide fix carries full reach and the
    lowest effort divisor.

    So the list is drawn from medium and above first, falling back to the low
    findings only when there are not enough. `ranked` arrives sorted by
    severity band first and by severity x reach / effort inside the band, and
    that order is kept, which is what the "Start here" prose describes.

    `ranked` also arrives filtered to the confident tier. The caller filters
    rather than this function, so the rule is visible where the list is built:
    a finding the audit is not sure of may not be the first instruction an
    owner reads, whatever it scores.

    `leads_the_verdict` is the one exception, and it is an exception to the
    tier filter and to nothing else. Those are the id hints the verdict's own
    diagnosis rests on - "what it cannot do is establish who the brand is: no
    page states in one sentence what it is... most of what follows is a
    consequence of that" - and a report whose prose names the cause of
    everything else and whose action list omits it has failed at prioritising
    in the most-read place there is. If the audit is not sure enough of a
    reading to put it on this list, it is not sure enough to open the report
    with it, and the verdict is the sentence more people read. `all_findings`
    is where those are looked up, because a verdict-naming finding may sit in
    the tier `ranked` has filtered out; the renderer marks such an entry as one
    to confirm first.

    It used to be an exception to the order as well, and that is the defect
    this function was rewritten for. Verdict-naming findings were moved to the
    front whatever their severity, so on 3 of 6 validation sites the sheet broke
    its own printed rule - "the ranking puts the most severe band first". A
    `critical` confident finding ranked third behind two `medium` ones; on
    another site a `high` confident finding was pushed off the list of three
    altogether by a `low` one from the tier this list is not supposed to draw
    from, under a printed line calling the omitted finding "of equal rank".
    The cost is high, because the sheet is the only page a non-expert
    reads.

    So severity is the outer key of the whole list. The verdict's causes join
    the candidates and are then sorted with everything else, and one of them
    is guaranteed a place - the last one, not the first - so that the verdict
    can never name a cause this list omits. The two rules the exception exists
    for both still hold, and neither of them was ever "put it first".
    """
    actionable = [f for f in ranked if f["severity"] != "info"]
    # `all_findings` is `ranked` before the tier filter: severity band outer,
    # priority score inside the band. It supplies the order *within* a band
    # here; the band itself is applied below rather than inherited, so this
    # function orders correctly whatever order it is handed - the caller
    # passing a list with the `low` findings at the front is a real case and
    # was what the deleted `substantive + remainder` split existed for.
    order = {f["id"]: index for index, f in enumerate(all_findings or ranked)}
    tail = len(order)

    def place(finding):
        return (SEVERITY_RANK[finding["severity"]], order.get(finding["id"], tail))

    candidates = list(actionable)
    if leads_the_verdict:
        listed = {f["id"] for f in candidates}
        candidates += [f for f in all_findings
                       if f.get("id_hint") in leads_the_verdict
                       and f["id"] not in listed and f["severity"] != "info"]
    candidates.sort(key=place)
    chosen = candidates[:limit]
    # The verdict's cause keeps a place on the list, and takes the last one
    # rather than the first. Without this a site with three findings more
    # severe than its named cause would print a verdict saying "most of what
    # follows is a consequence of that" above three fixes that are not it -
    # which is the same defect returning by the other door.
    if leads_the_verdict and not any(f.get("id_hint") in leads_the_verdict for f in chosen):
        cause = next((f for f in candidates if f.get("id_hint") in leads_the_verdict), None)
        if cause is not None and len(chosen) == limit:
            chosen = chosen[:limit - 1] + [cause]
            chosen.sort(key=place)
    return [f["id"] for f in chosen]


# How few off-site profiles counts as "what little is off the site". One is
# thin by any reading; the verdict's own `uncorroborated` test uses the same
# number, so the sentence and the test that produced it cannot disagree.
THIN_PROFILE_BREADTH = 1


def _front_door_finding(findings):
    """The published finding saying the homepage links to nothing, or None."""
    return next((f for f in findings
                 if f.get("root_cause") == "unreachable-from-homepage"), None)


def _named(finding):
    """A finding's title and id, as a clause a verdict sentence can carry."""
    title = (finding.get("title") or "").rstrip(".")
    title = title[:1].lower() + title[1:] if title[:2] != title[:2].upper() else title
    return "{} ({})".format(title, finding["id"]) if finding.get("id") else title


def _reach_lead(findings, name):
    """The verdict's opening sentence about reaching the site, from the findings.

    "A machine can reach no hello and read it" was printed directly above
    "Nothing on this site can be reached from its homepage". Both came out of
    one run; the first was a stock sentence and the second was a measurement.
    So the sentence is now chosen by what was published: an access finding
    says not every machine is let in, and the front-door finding says a
    machine that is let in reads one page and stops.
    """
    refused = [f for f in findings if f.get("mechanism") == "A"
               and f.get("severity") in ("critical", "high")]
    if refused:
        return "This audit could reach {} and read it, but not every machine can: {}.".format(
            name, "; ".join(_named(f) for f in refused[:2]))
    front_door = _front_door_finding(findings)
    if front_door is not None:
        return ("A machine can read the front page of {}, but cannot follow a link from it "
                "to any other page of the site{}.".format(
                    name, " ({})".format(front_door["id"]) if front_door.get("id") else ""))
    cover = next((f for f in findings
                  if (f.get("id_hint") or f.get("stable_id")) == "homepage-is-a-cover-page"),
                 None)
    if cover is not None:
        return ("A machine can reach {} and read the pages behind its front page, but the "
                "front page itself is a cover page with almost nothing on it to read{}.".format(
                    name, " ({})".format(cover["id"]) if cover.get("id") else ""))
    return "A machine can reach {} and read it.".format(name)


def _access_verdict(blocking, readable_pages=None):
    """The verdict where an access finding leads and the crawl read the site.

    Built from the findings that exist. Their titles already say who was
    refused and by what; the crawl says how much this audit was served. Never
    "shut out before they read anything": this is only reached once the crawl
    has read enough to grade, so that sentence is false of this audit at least.
    """
    read = ("This audit was served and read {}, so the site is not closed to machines. "
            .format(plural(readable_pages, "page", "pages")) if readable_pages else "")
    return ("{}What is wrong is narrower, and it comes first: {}. Until {} fixed, the "
            "machines {} cannot read or quote this site, whatever else below is done.".format(
                read, "; ".join(_named(f) for f in blocking[:2]),
                "that is" if len(blocking) == 1 else "those are",
                "it names" if len(blocking) == 1 else "they name"))


# The longest quotation the verdict carries. The citation table below it
# prints the whole sentence; the verdict needs enough to recognise it.
VERDICT_QUOTE_CHARS = 180


def what_an_assistant_can_repeat(signals, citations=(), brand_name=""):
    """`{"text", "url"}` - the sentence an assistant could lift about this site - or None.

    The one-sentence definition `fact-extractability-audit` found first, then
    the citation table's sentences: one naming the brand and saying what it
    is, then the homepage's, then any page's. Read by the verdict so it cannot
    say "no fact it can safely repeat" above a table quoting one.
    """
    signals = signals or {}
    definition = signals.get("entity_definition") if signals.get("entity_definition_found") else ""
    if definition and reads_as_a_description(definition):
        return {"text": truncate(definition.strip(), VERDICT_QUOTE_CHARS), "url": ""}
    quoted = [c for c in citations or () if c.get("likely_citation")
              and not _CUT_SHORT_RE.search(c["likely_citation"])]
    # A sentence that reads as the site describing itself, and failing that the
    # homepage's own sentence where it reads as one. Never any page's sentence
    # at all: the verdict frames what it quotes as "the fact it has" about the
    # brand, and the last two fallbacks put a national museum's child-privacy
    # notice and a university's event teaser in that place. Where nothing
    # qualifies the verdict says no such fact was found, which is what the
    # table beneath it then shows.
    # And the homepage's sentence only where it names the brand: the verdict
    # calls it the fact an assistant has about the brand. Without the name a
    # furniture shop's verdict quoted "New arrivals are here - plus $100 off
    # outdoor collections", then "enjoy an extra 5% off add-ons".
    names = [p for p in (brand_pattern(form) for form in brand_forms(brand_name)) if p] \
        if brand_name else []

    def names_the_brand(text):
        return any(re.search(r"\b(?:{})\b".format(p), text, re.I) for p in names)

    for test in (lambda c: c.get("why") == "names the brand and says what it is",
                 lambda c: c.get("page_type") == "home"
                 and not str(c.get("why") or "").startswith(("states a price",
                                                             "states a way to make contact"))
                 and names_the_brand(c["likely_citation"])
                 and reads_as_a_description(c["likely_citation"])):
        chosen = next((c for c in quoted if test(c)), None)
        if chosen is not None:
            return {"text": truncate(chosen["likely_citation"], VERDICT_QUOTE_CHARS),
                    "url": chosen.get("url") or ""}
    return None


def _verdict(counts, findings, crawl=None, signals=None, brand_label="",
             readable_share=None, has_recommendations=True,
             readable_pages=None, pages_reached=0, why_unread="",
             origin_redirect=None, site_not_serving=None, quotable=None):
    """The verdict, with the address it is about stated before it.

    A crawl whose seed URL leaves its own origin produces a report about a
    different hostname from the one somebody typed, and every sentence in it
    is true of that other host. The severity ladder below cannot know that, so
    the fact is stated in front of whatever it decides rather than left in a
    note at the bottom of the appendix.
    """
    redirect = origin_redirect or {}
    if redirect and redirect.get("followed"):
        requested = redirect.get("requested_origin") or "the address you asked about"
        landed = redirect.get("final_origin") or redirect.get("final_url") or "another address"
        prefix = ("You asked about {}. That address redirects to {}, which carries the same "
                  "registered name, so the crawl followed it and everything below describes "
                  "{} rather than what you typed. ".format(requested, landed, landed))
    else:
        prefix = ""
    return prefix + _verdict_body(
        counts, findings, crawl, signals, brand_label, readable_share,
        has_recommendations, readable_pages, pages_reached, why_unread, redirect,
        site_not_serving, quotable)


def _verdict_body(counts, findings, crawl=None, signals=None, brand_label="",
                  readable_share=None, has_recommendations=True,
                  readable_pages=None, pages_reached=0, why_unread="",
                  redirect=None, not_serving=None, quotable=None):
    """One paragraph naming what is actually wrong, not how many things are.

    This used to be a pure severity ladder, and on a site with no off-site
    presence, no identity markup, invalid markup where it existed and six of
    seven pages sharing a title, it opened with "The foundations are sound"
    because no finding happened to be `critical`. A summary that contradicts
    the report beneath it is the one sentence most likely to be the only
    sentence read.

    The counts still decide how loud to be. What the site is failing at now
    decides what to say.
    """
    # The site is answering, uniformly, with a status that speaks for the
    # service rather than for an address: it is switched off, gone, blocked in
    # this jurisdiction, or down. That outranks every branch below including
    # the redirect one, because it is the whole story of the site and each of
    # those branches would instead describe some detail of a site that is not
    # there. The report this exists to stop had one finding on such a site -
    # "remove `Disallow: /` from robots.txt" - which is a chore about a shop
    # nobody can reach.
    #
    # The crawler's own sentence, printed unchanged: it names the status, what
    # the status means and how many addresses it rests on, and it is the only
    # thing that knows all three. Composing a second sentence here would be a
    # second place for the two documents to disagree.
    if not_serving and not_serving.get("reason"):
        # Returned unchanged and with nothing appended. The recorded sentence
        # already says that nothing below measures what the site publishes,
        # and adding a second sentence saying it again is how the verdict and
        # the caveat under it came to print the same clause twice.
        return not_serving["reason"]
    # The seed URL left its own origin and was not followed, so not one page
    # of the site somebody asked about was fetched. That is a stronger
    # statement than "too little was read" and it outranks everything below,
    # including the robots branch: whatever the site's robots.txt says is a
    # fact about a site this crawl never reached. The crawler writes the
    # sentence, because it is the only thing that knows both hostnames and
    # what the redirect actually did; it is repeated here so that the one line
    # most likely to be the only line read is the one that says the site was
    # not read.
    redirect = redirect or {}
    if redirect and not redirect.get("followed"):
        landed = redirect.get("final_origin") or redirect.get("final_url") or ""
        return "{} {}".format(
            redirect.get("reason")
            or "The address this audit was asked about redirects off its own origin and was "
               "not followed, so no page of it was read.",
            "Nothing below is evidence about it either way. If {} is the site you meant, run "
            "the audit against that address.".format(landed) if landed else
            "Nothing below is evidence about it either way.")
    # A robots-blocked run reports almost nothing, and without saying why that
    # looks like a broken audit rather than a respected instruction.
    if (crawl or {}).get("audit_blocked_by_robots"):
        return ("This site's robots.txt disallows this auditor, so no pages were fetched and "
                "the findings below come from robots.txt alone. That is the file working as "
                "intended; it also means any crawler that respects it sees exactly as little.")
    # The edge refused this audit and served the named AI crawlers. Everything
    # below is then a report about one refused client, and the severity ladder
    # has nothing to rank - so the summary came out as "no blocking or
    # significant problems were found" for a site not one page of which was
    # read. True of the site, and the opposite of what the reader needs to know
    # about the report they are holding.
    refused = [f for f in findings
               if f.get("id_hint") == "audit-refused-while-crawlers-served"]
    if refused:
        return ("This audit could not read the site: its edge answered this crawler with an "
                "error and answered the named AI answer crawlers with the page. That is the "
                "site doing the right thing by them, and it means there is nothing below "
                "about your content - only what one refused client could see.")
    # Nothing below this line may claim the site is fine, because everything
    # below this line is computed from the severity counts, and severity counts
    # measure what the checks found - not whether the checks had anything to
    # look at. Twice now a crawl has read no text at all and the ladder below
    # has answered "No blocking or significant problems were found": once where
    # the only URL redirected off the origin and was skipped, once where the
    # response could not be decoded. Different causes, one invariant, so the
    # guard is on the invariant: without the coverage `enough_to_grade` asks
    # for, the verdict states what was and was not established instead of
    # grading a site nobody read.
    #
    # That predicate is also the one `withhold_absence_claims` obeys, and it is
    # shared on purpose. When the two were separate rules, a crawl of sixteen
    # URLs with seven readable held back eight findings for want of coverage
    # and printed "No blocking problems." two inches above the list of them.
    #
    # `None` means the caller did not measure it, which keeps the older callers
    # - and the tests that exercise one branch of the ladder in isolation -
    # working unchanged. `compose` always measures it.
    if not enough_to_grade(readable_pages, readable_share):
        # The access findings, where there are any, are the explanation. Say so
        # rather than dropping the diagnosis the rest of the report worked out.
        blocking = [f for f in findings
                    if f.get("mechanism") == "A"
                    and f.get("severity") in ("critical", "high")]
        opening = ("Machines are being shut out before they read anything, and this audit "
                   "was too. " if blocking else "")
        # Some pages did come back. Saying "every check had nothing to run
        # against" would then be false, and false in the direction that makes
        # the report look broken: the findings below were read off real pages.
        read_something = bool(readable_pages)
        established = (
            "What is established is why: {} below {} what happened to this crawler. ".format(
                "the access finding" if len(blocking) == 1 else "the access findings",
                "describes" if len(blocking) == 1 else "describe")
            if blocking else
            "What is established is what those pages show, and no more. " if read_something
            else "What is established is only what a request that returns no page can show. ")
        not_established = (
            "What is not established is anything about the URLs that did not come back: any "
            "claim that something is missing from this site would be a claim about them too, "
            "so those claims are listed under the questions this audit could not answer "
            "rather than reported as defects. "
            if read_something else
            "What is not established is anything about the content: every check that needed "
            "a page to read had nothing to run against, and those are listed under the "
            "questions this audit could not answer rather than counted as passes. ")
        return (
            "{}This audit read {} from the {} it reached, so it has not seen enough of this "
            "site to judge it{}. {}{}Read "
            "nothing below as a clean bill of health - it describes the little that came "
            "back, not the site.".format(
                opening,
                plural(readable_pages or 0, "page of readable text",
                       "pages of readable text"),
                plural(pages_reached, "URL", "URLs"),
                " ({})".format(why_unread) if why_unread else "",
                established, not_established))
    if counts["critical"]:
        # "Shut out before they read anything" is a claim about access, and only
        # a mechanism-A finding is evidence for it. `render-readability-audit`
        # raises `homepage-is-javascript-shell` at critical with mechanism C on
        # sites whose pages returned 200 and were read without trouble, and the
        # first sentence of the report told those owners the opposite of what
        # the crawl table two sections below showed.
        blocking = [f for f in findings
                    if f["severity"] == "critical" and f.get("mechanism") == "A"]
        if blocking:
            # Not "machines are being shut out before they read anything".
            # This line is only reached once the crawl has read enough to
            # grade, so this audit's own requests were answered - and on the
            # site that printed it, two of four named crawlers were served as
            # well and two were refused. The sentence is built from the
            # findings that exist: who was refused is in their titles.
            #
            # `None` means the caller did not measure what was read, as above,
            # and then nothing is known that contradicts the stock sentence.
            # `compose` always measures it.
            if readable_pages is None:
                return ("Machines are being shut out before they read anything. {} access "
                        "or delivery, and nothing else on the site can compensate until it "
                        "is fixed.".format(plural(len(blocking), "critical problem blocks",
                                                  "critical problems block")))
            # Where the site also declares no identity, the identity paragraph
            # below names both - its opening sentence says who was refused -
            # so the paragraph "Start here" credits as naming the cause does
            # name it. Otherwise the refusal is the whole story.
            if not verdict_diagnosis(findings, signals):
                return _access_verdict(blocking, readable_pages)
        # Named, and with what follows from it counted. On a storefront whose
        # homepage and content pages were shells, this sentence named nothing,
        # and "Start here" filled its second place with an off-site-profile
        # finding the verdict was then said to have named.
        critical = [f for f in findings if f["severity"] == "critical"]
        if blocking:
            pass
        elif critical and all(f.get("id") for f in critical):
            ids = {f["id"] for f in critical}
            following = sum(1 for f in findings
                            if (f.get("follows_from") or {}).get("id") in ids
                            or (f.get("partly_follows_from") or {}).get("id") in ids)
            return ("These pages were fetched and read, so access is not what is wrong. What "
                    "a machine cannot use is what it got back: {}. That is the first thing "
                    "to fix{}.".format(
                        "; ".join(_named(f) for f in critical[:2]),
                        ", and {} below {} the same defect seen from another angle".format(
                            plural(following, "finding", "findings"),
                            "is" if following == 1 else "are") if following else ""))
        if not blocking:
            return ("These pages were fetched and read, so access is not what is wrong. {} a "
                    "machine still cannot use what it got back, and that is the first thing "
                    "to fix.".format(
                        plural(counts["critical"], "problem of the most serious kind means",
                               "problems of the most serious kind mean")))
    # The Round-2 "invisible" diagnosis, and it outranks the severity ladder.
    # A brand that declares no identity a machine can read, and links to nothing
    # that would corroborate one, is unquotable however few findings are severe:
    # there is no fact to repeat and nothing to check it against.
    signals = signals or {}
    causes = {f["root_cause"] for f in findings}
    breadth = signals.get("profile_breadth")
    # On the findings themselves, not on the root cause, and through the one
    # function `verdict_diagnosis` reads. `no-org-schema` is raised by four
    # checks and two of them say the site *has* identity markup: "these branch
    # pages describe a place without saying so", and "the homepage declares no
    # Organization markup, though the rest of the site does". Reading the cause
    # put both of those sentences in the verdict as "no page carries
    # Organization or LocalBusiness markup" - on a site with three unmarked
    # branch pages and a complete block, and on a school carrying the block on
    # 48 of its 60 crawled pages.
    no_org, homepage_only = identity_markup_gap(findings)
    no_definition = "no-entity-definition" in causes
    undeclared = no_org or no_definition
    uncorroborated = ("weak-corroboration" in causes
                      or (breadth is not None and breadth <= THIN_PROFILE_BREADTH))
    # `verdict_diagnosis` above answers the same question and returns the
    # findings this paragraph is about, and "Start here" is built from it. The
    # two are not one call because this branch needs `no_org` and
    # `no_definition` apart to write its own sentence, so a test holds them
    # together instead: whenever this paragraph prints, the findings it names
    # lead the action list.
    if undeclared and uncorroborated:
        # Name the brand and the two specific gaps. The first version of this
        # paragraph was identical, word for word, for a two-person pottery and
        # for a widely-cited software project - which reads as a template
        # rather than a diagnosis, and discredits everything under it.
        missing = []
        if no_org:
            missing.append("no page carries Organization or LocalBusiness markup")
        elif homepage_only:
            # The accurate half of the same subject, and it may print only
            # where the sentence above did not: the two contradict each other,
            # and this one is what that site's own findings said 60 lines
            # below the verdict that denied it. No count here - the finding
            # carries it, and a second copy of a number is a second place for
            # it to be wrong.
            missing.append("the homepage carries no Organization markup, though other "
                           "crawled pages do")
        if no_definition:
            missing.append("no page states in one sentence what it is")
        # "only 12 off-site profiles are linked" was printed on a site with a
        # full set of claimed profiles, in a list of things that are missing,
        # one clause before being told what little is off the site is too thin.
        # Twelve profiles is not thin and is not missing, so above the thin bar
        # the count leaves the missing list and the closing clause changes.
        if breadth is not None and 0 < breadth <= THIN_PROFILE_BREADTH:
            missing.append("only " + plural(breadth, "off-site profile is linked",
                                            "off-site profiles are linked"))
        # The closing clause has to match the number two sentences above it. It
        # said "nothing off the site corroborates it" unconditionally, so a site
        # linking five profiles - counted, named and printed in the same
        # paragraph - was told in the next clause that it links none. A verdict
        # that contradicts its own evidence is the one sentence most likely to
        # be the only sentence read.
        #
        # And each clause may state only what was measured. "The 3 off-site
        # profiles linked from it do not say it either" was printed about a
        # coffee roaster whose profiles this audit counted and never fetched.
        # "Nothing off the site corroborates it" was printed about a holding
        # company whose report, further down, said "Your Wikidata item already
        # exists" and named it - the freshness skill had found it, and the
        # verdict read only the profile count. The Wikidata signals come first because
        # they are the one off-site reading this audit actually made.
        own_item = signals.get("wikidata_own_entity") or ""
        item_linked = not own_item and signals.get("wikidata_item_exists") is True
        if own_item:
            off_site = ("the Wikidata item registered under this name ({}) is one nothing on "
                        "the site links to".format(own_item))
        elif item_linked:
            off_site = "off the site it links a Wikidata item"
        elif not breadth:
            off_site = "it links no off-site profile that could corroborate it"
        elif breadth <= THIN_PROFILE_BREADTH:
            off_site = "the {} it links off the site {} too few to settle it".format(
                plural(breadth, "profile", "profiles"), "is" if breadth == 1 else "are")
        else:
            off_site = ("it links {} off-site profiles, which this audit counted and did "
                        "not read".format(breadth))
        # "A machine can reach this site and read it" was guarded only by there
        # being no critical finding, so it printed on a crawl where three of
        # four URLs came back 403. This branch used to carry its own low-share
        # variant of that sentence, which is a second coverage rule in a file
        # that now has one: `enough_to_grade` above has already returned for
        # every crawl that variant existed for, so reaching this line means the
        # crawl read most of what it reached and "can reach it and read it" is
        # a claim the run supports.
        # Three sentences about the same subject, and on a site whose deep
        # pages do carry the block all three were false as written: "cannot
        # establish who the brand is", "nothing on the site says it in a form a
        # machine reads", "no fact it can safely repeat". Where the markup
        # exists and the front door omits it, the claim narrows to the front
        # door - which is what the crawl measured and all it measured.
        front_door_only = homepage_only and not no_org
        # "No fact about this brand it can safely repeat" is a claim about three
        # measurements at once, and it was printed on the strength of one: no
        # Organization markup. One report carrying it quoted "<a central bank>
        # is a public institution with a mandate..." in its own table of what an
        # assistant would lift. So the sentence is now built from all three: it
        # stands only where no definition was found, no page offers a quotable
        # sentence and corroboration is weak. Otherwise the report quotes what
        # an assistant can repeat and says what is missing around it.
        repeatable = quotable or what_an_assistant_can_repeat(signals, ())
        if repeatable and not front_door_only and not own_item and not item_linked:
            where = (" ({})".format(repeatable["url"]) if repeatable.get("url")
                     else ", from its own one-sentence definition")
            lead = ("{} What it cannot do is establish who the brand is from anything a "
                    "machine can check: {}. ".format(
                        _reach_lead(findings, brand_label or "this site"),
                        "; ".join(missing) or "the identity is undeclared"))
            return lead + ('Nothing on the site says it in a form a machine reads, and {}. '
                           'What an assistant can repeat is the site\'s own sentence "{}"{} - '
                           "that is the fact it has - but nothing this audit read confirms "
                           "it, so an assistant repeating it has only the site's word for "
                           "it.".format(off_site, repeatable["text"], where))
        if front_door_only:
            cannot = ("What a machine landing on its front page cannot do is establish "
                      "who the brand is")
            scope = ("The page it lands on first says none of that in a form a machine "
                     "reads")
            tail = ("so an assistant that fetches the homepage and stops there has no fact "
                    "about this brand it can repeat")
        else:
            cannot = "What it cannot do is establish who the brand is"
            scope = "Nothing on the site says it in a form a machine reads"
            # What was measured, and no more. "No fact about this brand it can
            # safely repeat" was printed above a table quoting the shop's
            # prices and opening hours - facts, just not facts about who the
            # brand is. The claim is about identity, so it says identity.
            tail = ("so an assistant asked about this category has no sentence from this "
                    "site saying what the brand is, and nothing it can safely repeat about "
                    "who the brand is")
        # A Wikidata item is a fact a machine can repeat, so "no fact it can
        # safely repeat" is false wherever one was found. What stays true is
        # where that fact lives.
        if own_item:
            tail = ("so the one machine-readable statement of who this is sits off the "
                    "site, and a machine has no link telling it that entry is this site")
        elif item_linked:
            tail = ("so what a machine can repeat about this brand comes from that item "
                    "rather than from the site")
        lead = ("{} {}: {}. ".format(
                    _reach_lead(findings, brand_label or "this site"), cannot,
                    "; ".join(missing) or "the identity is undeclared"))
        return lead + ("{}, and {}, {}. Most of what follows is a "
                       "consequence of that.".format(scope, off_site, tail))

    # Both sentences below asserted that access and delivery are fine, with no
    # test for it, and were printed directly above high-severity mechanism-A
    # findings: a bot challenge, a site-wide noindex, a crawler refused on a
    # quarter of its requests. Whether a machine is let in is exactly what a
    # mechanism-A finding is about, so the claim is now gated on there being
    # none among the high-severity findings.
    high_access = [f for f in findings
                   if f["severity"] == "high" and f.get("mechanism") == "A"]
    # "Nothing else on the site can be relied on to be read" was printed over a
    # finding titled "refuses 2 named answer-engine crawlers and serves the
    # rest", on a crawl that read sixty pages. The access branches below now
    # say who was refused, from the finding, and what was read, from the crawl.
    front_door = _front_door_finding(findings)
    if counts["high"] >= 3:
        if high_access:
            return ("{} high-severity problems. {}".format(
                counts["high"], _access_verdict(high_access, readable_pages)))
        if front_door is not None:
            return ("{} {} high-severity problems then mean a machine fetching it struggles "
                    "to work out who this brand is or to find a fact worth quoting.".format(
                        _reach_lead(findings, brand_label or "this site"), counts["high"]))
        return ("The site is reachable, but {} high-severity problems mean a machine fetching "
                "it struggles to work out who this brand is or to find a fact worth "
                "quoting.".format(counts["high"]))
    if counts["high"]:
        if high_access:
            return _access_verdict(high_access, readable_pages)
        if front_door is not None:
            return ("{} {} back how confidently the brand can be described and "
                    "cited.".format(_reach_lead(findings, brand_label or "this site"),
                                    plural(counts["high"], "high-severity problem holds",
                                           "high-severity problems hold")))
        return ("Access and delivery are sound - a machine can fetch these pages and read "
                "them. {} back how confidently the brand can be described and "
                "cited.".format(plural(counts["high"], "high-severity problem then holds",
                                       "high-severity problems then hold")))
    if counts["medium"]:
        return ("No blocking problems. {} worth fixing to improve how often and how "
                "accurately the brand gets quoted.".format(
                    plural(counts["medium"], "medium-severity item is",
                           "medium-severity items are")))
    # The second sentence points at a section that is only rendered when
    # `build_recommendations` returned something. On a site where every
    # condition was already met it returned nothing, the section was omitted,
    # and the verdict sent the reader to a heading that is not in the document.
    if has_recommendations:
        return ("No blocking or significant problems were found. The proactive recommendations "
                "below are where the remaining upside is.")
    return ("No blocking or significant problems were found, and none of the proactive "
            "improvements this audit knows how to suggest applied to this site.")


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------

SEVERITY_HEADINGS = [
    ("critical", "Critical", "Machines cannot get in or cannot read the page. Fix these first."),
    ("high", "High", "Significant loss of visibility, accuracy or visitors."),
    ("medium", "Medium", "Real improvements, worth scheduling."),
    ("low", "Low", "Hygiene. Cheap to fix, small individual gain."),
    ("info", "For information", "Observations that are not defects, recorded so you know they were checked."),
]

WORTH_CHECKING_HEADING = "Worth checking first"

# Printed above the lower tier, in both documents. It has to do two things at
# once for a reader who is not an expert: stop them acting on a weak reading as
# if it were a firm one, and stop them concluding the audit is hiding
# something. So it says plainly that nothing has been dropped.
WORTH_CHECKING_BLURB = (
    "Weaker readings. The audit thinks each of these is probably true, but each rests on an "
    "inference rather than on something it read directly, so check it against the site before "
    "you spend money on it. Nothing has been removed from the report to get here: these are "
    "counted in the totals above and written out in full below, they are simply not mixed in "
    "with the findings the audit is sure of.")

# Printed above the severity table when the crawl fell below the readable-pages
# floor, so the first thing the reader meets is what was not established rather
# than a table of zeros that reads as a clean bill of health.
COVERAGE_CAVEAT_HEADING = "What this audit could not establish"

# Two headings for the two ways the address audited can fail to be the address
# asked about. They are separate because the reader's next action is different:
# one is "this report is about a different hostname, check that is what you
# meant", the other is "this report is about nothing, point it somewhere else".
ORIGIN_MOVED_HEADING = "This is not the address you asked about"
ORIGIN_NOT_READ_HEADING = "No page of the address you asked about was read"

# Printed above the whole report when every address the crawl reached answered
# with a status that speaks for the service rather than for the address. The
# case that produced it: a report whose only finding was "remove `Disallow: /`
# from robots.txt" on a site with no shop behind it. That is the whole story of
# the site, missed, and told as a chore.
SITE_NOT_SERVING_HEADING = "This site is not serving"

# The fifth appendix bucket. Deliberately not "checks that did not run": some
# of these - the ones that fetch robots.txt or probe a sitemap - do make their
# own request and do reach an answer. What is true of every one of them is that
# nothing they found is a statement about the site, which is the only thing
# worth saying about them and the reason they may not be counted as clean.
NOTHING_TO_RUN_AGAINST_HEADING = "Checks with nothing to report either way"
NOTHING_TO_RUN_AGAINST_BLURB = (
    "These checks were registered and never reached anything to judge - either the crawl read "
    "no page for them to look at, or the skill that owns them was stopped before it ran. A few "
    "of them did make a request of their own; none of them examined any content. They are "
    "listed here rather than among the checks that ran clean, because a check that never saw "
    "the site cannot be evidence that the site is fine. Where a reason was recorded it is "
    "under \"Questions this audit could not answer\" above:")

# Printed above `unverifiable[]`. Three different things now arrive in this
# channel - an absence claim held back for want of coverage, a site that
# answered every address with a refusal to serve, and a whole skill that was
# stopped before it ran - and the section used to open by asserting the first
# of them as the cause of all three. A reader meeting all three at once is
# already having a bad day; the shared sentence now says what the section is
# for, and each entry says which of the three it is.
UNANSWERED_HEADING = "Questions this audit could not answer"
UNANSWERED_BLURB = (
    "Every one of these is something this audit set out to establish and could not, with the "
    "reason it could not. They are listed here rather than as defects, because \"we could not "
    "see it\" and \"it is not there\" are different statements and only one of them is "
    "something to fix. Read them as the size of the gap in this report:")


def _not_serving_caveat(report):
    """(heading, lines) when the site itself is declining to serve.

    `crawl.site_not_serving` is set only when every address the crawl reached
    answered, all answered the same status, and that status speaks for the
    service rather than for the address - 402, 410, 451, 503. A 404 is about
    an address and a 403 is about who is asking, so neither qualifies.

    The failure this prints above everything else: a report whose only finding
    was "remove `Disallow: /` from robots.txt", on a site with no shop behind
    it. That is the whole story of the site, delivered as a chore. A robots.txt
    line is not why nobody can buy anything there, and no change to it, to a
    sitemap or to any page would alter the answer while the site answers this
    way.

    The crawler writes the sentence that states the fact, and the verdict
    prints it unchanged - the crawler is the only thing that knows the status,
    what it means and how many addresses it rests on. This block shows the
    working under it rather than restating it, because the verdict sits three
    lines above and a clause printed twice reads as two separate claims.
    """
    not_serving = (report.get("crawl") or {}).get("site_not_serving")
    if not not_serving:
        return None
    addresses = not_serving.get("addresses") or []
    lines = ["{} of the {} address(es) reached answered HTTP {}: {}.".format(
        len(addresses) or not_serving.get("addresses_answered", 0),
        not_serving.get("addresses_answered", 0),
        not_serving.get("status"), not_serving.get("meaning") or "")]
    if addresses:
        lines.append("They were: {}{}.".format(
            ", ".join(addresses[:5]),
            "" if len(addresses) <= 5 else
            " and {} more".format(len(addresses) - 5)))
    lines.append(
        "Every finding, count and recommendation below rests on content that was never "
        "served, so none of it is a measurement of the site. Bring the site back up and "
        "run the audit again.")
    return (SITE_NOT_SERVING_HEADING, lines)


def _origin_caveat(report):
    """(heading, lines) when the crawl did not stay on the requested origin.

    `crawl.notes` already carries the crawler's sentence, and a note in a list
    at the foot of the document is not where somebody looks to find out whose
    website they are reading about. A person who typed one hostname and is
    handed a report grading another has to be told before the grades,
    not after them.
    """
    crawl = report.get("crawl") or {}
    redirect = crawl.get("origin_redirect")
    if not redirect:
        return None
    requested = redirect.get("requested_origin") or crawl.get("requested_origin") or ""
    landed = redirect.get("final_origin") or redirect.get("final_url") or ""
    reason = redirect.get("reason") or ""
    if redirect.get("followed"):
        lines = [
            "You asked about {}. It redirects to {}, and the crawl followed it because the "
            "two carry the same registered name.".format(
                requested or "one address", landed or "another address"),
            "Every finding, count and quote below is about {}. If that is not the site you "
            "meant, run the audit against the address you did mean.".format(
                landed or "the address it landed on"),
        ]
        if reason:
            lines.append(reason)
        return (ORIGIN_MOVED_HEADING, lines)
    lines = [reason] if reason else []
    lines.append(
        "Nothing below describes {}. It describes one request, which was answered with a "
        "redirect somewhere else.".format(requested or "the address you asked about"))
    if landed:
        lines.append(
            "If {} is the site you meant, run the audit against that address.".format(landed))
    return (ORIGIN_NOT_READ_HEADING, lines)


def _requested_origin(report):
    """The address somebody typed, when it is not the one this report is about.

    Empty otherwise. Read in two places - the header line and the top sheet -
    and factored out so the two cannot disagree about whether a substitution
    happened.
    """
    crawl = report.get("crawl") or {}
    redirect = crawl.get("origin_redirect") or {}
    if not redirect or not redirect.get("followed"):
        return ""
    requested = redirect.get("requested_origin") or crawl.get("requested_origin")
    if not requested or requested == report.get("site"):
        return ""
    return requested


def _asked_about(report):
    """" · you asked about X", or "" when the audited host is the one typed.

    The report's title names the host the pages came from, which is the only
    honest thing for it to name and is silent about the substitution. This puts
    the address somebody typed in the same line as the title, so the two are
    never seen apart.
    """
    requested = _requested_origin(report)
    return " · you asked about {}".format(requested) if requested else ""


def _pages_line(report):
    """The "N pages crawled" fragment in the first line both documents print.

    A URL that answered 200 and was skipped as a redirect off the origin counts
    as crawled and carries no text, so "1 page crawled" was the whole of what
    the header said about a run that read nothing at all. Where the two numbers
    differ enough to matter, both are printed, in the first line a skimmer
    reads.
    """
    crawl = report.get("crawl") or {}
    crawled = crawl.get("pages_crawled", 0)
    if crawl.get("enough_to_judge", True):
        return _plural(crawled, "page crawled", "pages crawled")
    return "{}, {} readable".format(
        _plural(crawled, "page crawled", "pages crawled"),
        crawl.get("readable_pages", 0))


def _who_you_will_need(report):
    """One line saying how many actions need somebody who edits the template.

    Placed under the verdict rather than beside each fix, because it is a
    question about the whole report - can I do this at all - and the answer
    matters before the first instruction, not after the ninth. Empty where
    there are no such actions, because a report with none should not raise the
    question.

    "Somebody who can edit the files the site is built from" rather than "a
    developer", because on a hosted platform it is a theme, on a builder it is
    whoever set it up, and for a lot of small brands it is the person they paid
    once and have not spoken to since. Naming the job rather than the role is
    what makes the sentence answerable.
    """
    summary = report.get("summary") or {}
    needed = summary.get("actions_needing_a_template_editor") or 0
    total = summary.get("actions") or 0
    if not needed or not total:
        return ""
    return (
        "{} of the {} below {} somebody who can edit the files the site is built from - "
        "its theme or template, not a page in the admin screen. That is your developer, "
        "your agency, or whoever built the site. The rest you can do yourself.".format(
            needed, _plural(total, "action", "actions"),
            "needs" if needed == 1 else "need"))


DROPPED_SKILL_HEADING = "Part of this audit did not run"

# What each sub-skill would have examined, for a reader who is being told one
# of them is missing and has no idea what that costs them. Named here rather
# than taken from the skill's own description, because a description is written
# to sell a skill and this sentence has to say what a gap is.
_WHAT_A_SKILL_EXAMINES = {
    "crawl-access-audit": "whether crawlers are allowed in at all - robots.txt, the "
                          "sitemap, status codes and redirects",
    "render-readability-audit": "whether the pages deliver their text without a browser, "
                                "and whether that text can be read",
    "structured-data-audit": "the machine-readable markup: who the site says it is, what "
                             "it sells, and whether the markup agrees with the page",
    "fact-extractability-audit": "whether the facts an assistant would repeat - what this "
                                 "is, where it is, what it costs - are stated in plain text",
    "freshness-corroboration-audit": "whether the site looks current, and whether anywhere "
                                     "else on the web says the same things about it",
    "engagement-audit": "what a person who arrives from an assistant meets: headings, "
                        "navigation, page weight, forms and readability",
}


def _skills_that_did_not_run_lines(report):
    """Bullets naming any sub-skill that produced nothing, and what that costs.

    In the header rather than only in the appendix. One real run was against a
    site where one of the six sub-skills was stopped by the clock at 85s and a
    second never started; the report said so at line 531, and "The short
    version" - the part anybody reads - never mentioned that a sixth of the
    audit had not happened. A reader who is not told at the top has been given
    a count of findings and no way to know what the count is out of.
    """
    dropped = report.get("skills_that_did_not_run") or []
    if not dropped:
        return []
    lines = []
    for item in dropped:
        skill = item.get("skill") or "a sub-skill"
        examines = _WHAT_A_SKILL_EXAMINES.get(skill)
        # No markdown emphasis here: this list is rendered by both the
        # markdown and the HTML writer, and the HTML one escapes what it is
        # given, so a pair of asterisks would reach that reader as asterisks.
        lines.append("{} did not run{}. {}".format(
            skill,
            ", so nothing below covers {}".format(examines) if examines else "",
            item.get("reason") or "Everything it examines is unreported rather than clean."))
    lines.append(
        "{} of the {} sub-skills {} missing, so the counts below are out of less than the "
        "whole audit. Treat this as a gap, not as a pass: re-run with a larger --budget, "
        "with --no-network, or against a faster host to fill it in.".format(
            len(dropped), len(SUB_SKILLS), "is" if len(dropped) == 1 else "are"))
    return lines


def _coverage_caveat_lines(report):
    """Bullets naming what the crawl did not see. Empty when it saw enough.

    Shared by both renderers, because the run this exists to stop produced a
    report.md and a report.html that were each individually reassuring.
    """
    crawl = report.get("crawl") or {}
    if crawl.get("enough_to_judge", True):
        return []
    unanswered = len(report.get("unverifiable") or [])
    # Some pages did come back on the crawls that trip the share half of
    # `enough_to_grade` rather than the count half, so neither "with no page to
    # read" nor "fix whatever is stopping a crawler" can be asserted here. Both
    # were, and on a crawl where every URL answered 200 both were false.
    read = crawl.get("readable_pages", 0)
    lines = [
        "The crawl reached {} on {} and got {} back. Nothing in this report rests on more "
        "than that.".format(
            _plural(crawl.get("pages_crawled", 0), "URL", "URLs"), report["site"],
            _plural(read, "page of readable text", "pages of readable text")),
    ]
    if unanswered:
        lines.append(
            "{} stayed quiet, and that silence is not evidence the site is fine - {}. They "
            "are listed under \"Questions this audit could not answer\" near the end of this "
            "report.".format(
                _plural(unanswered, "check", "checks"),
                "the pages it would have answered from are among the ones that did not come "
                "back" if read else "there was no page to read"))
    lines.append(
        "A low count in the table below therefore means the audit found little, not that "
        "there is little to find. {}".format(
            "Look at why the rest of the URLs did not come back, then run this again."
            if read else
            "Fix whatever is stopping a crawler reading the site, then run this again."))
    return lines


def _markup_that_named_the_site(sources):
    """"an Organization name" / "og:site_name" / both, or "" for neither.

    `sources` is `brand.authoritative_sources` as the crawl recorded it -
    `jsonld:Organization`, `og:site_name`, and the `-off-home` variants of
    each, which mean the same declaration found away from the homepage. The
    suffix is dropped here: the sheet's reader is being told what the site's
    markup says, and "on a page that is not the homepage" is a fact about the
    ranking rather than about the declaration.
    """
    named = []
    if any(str(s).startswith("jsonld:Organization") for s in sources):
        named.append("an Organization name in its JSON-LD")
    if any(str(s).startswith("og:site_name") for s in sources):
        named.append("an og:site_name tag")
    return " and ".join(named)


TOP_SHEET_HEADING = "The one-page version"

TOP_SHEET_BLURB = (
    "Every line here is written out again further down with the evidence under it, and every "
    "figure here is one this report states somewhere else. Read this page and act on it; read "
    "the rest when you want the working.")


# A sentence that is a notice about the page rather than a statement of what
# the site is: a licence line, a copyright line, an address to go to. Three top
# sheets opened "What this site is" with one - a documentation site's
# "本中文文档采用 ... (CC BY-NC-SA 4.0) 进行许可。", a holding company's
# "FOR A FREE CAR INSURANCE RATE QUOTE ... WWW.<insurer>.COM OR CALL ...", and
# a library's event notice - while each site's own meta description said what
# it was.
NOT_A_DESCRIPTION_RE = re.compile(
    r"(licen[cs]|creative\s+commons|\bcc\s+by\b|copyright|all rights reserved|©|"
    r"许可|許諾|ライセンス|版权|著作権|www\.|https?://)", re.I)

# The share of cased letters above which a sentence is shouting - an advert or
# a banner - rather than describing anything.
SHOUTING_SHARE = 0.6


def reads_as_a_description(text):
    """Could this sentence be the site saying what it is?

    No: under 12 characters, a licence or copyright line, a sentence carrying a
    web address, or one whose cased letters are mostly capitals.
    """
    text = (text or "").strip()
    if len(text) < 12 or NOT_A_DESCRIPTION_RE.search(text):
        return False
    # Campaign copy addressed to the reader - "Your home should feel like..." -
    # says what the reader might want, not what the site is.
    if addresses_the_reader(text):
        return False
    cased = [c for c in text if c.isalpha() and (c.isupper() or c.islower())]
    if len(cased) >= 12 and sum(1 for c in cased if c.isupper()) > SHOUTING_SHARE * len(cased):
        return False
    return True


def what_the_site_says_it_is(snapshot, signals):
    """`{"text", "source"}` in the site's own words, or None.

    The one-sentence definition `fact-extractability-audit` found first, then
    the homepage's meta description, then its `og:description` - each only
    where it reads as a description. The sentence an assistant would most
    likely lift from the homepage is a different question, answered in the
    citation table, and it is the fallback on the top sheet rather than the
    first choice: it is chosen for being quotable, not for being about the
    site.
    """
    signals = signals or {}
    definition = signals.get("entity_definition") if signals.get("entity_definition_found") else ""
    if definition and reads_as_a_description(definition):
        return {"text": truncate(definition.strip(), 300), "source": "definition"}
    # The page every skill means by "the homepage", so this sentence and the
    # findings describe one page. A retailer's root is a country picker, and
    # the sheet quoted one country's storefront as what the homepage says.
    home = (site_homepage(snapshot) or {}).get("page") or next(
        (page for page in pages_of(snapshot) if page.get("page_type") == "home"), None)
    if home is None:
        return None
    for source, value in (("meta description", home.get("meta_description")),
                          ("og:description", (home.get("og") or {}).get("og:description"))):
        if value and reads_as_a_description(value):
            return {"text": truncate(value.strip(), 300), "source": source}
    return None


# How the top sheet names each kind of site.
SITE_KIND_PHRASES = {
    LOCAL_BUSINESS: "a local business with a place to visit",
    ONLINE_SELLER: "an online shop",
    ORGANISATION: "a company or non-profit that does not sell online",
    PROJECT: "a project or product",
    PUBLIC_BODY: "a public body",
    PERSONAL_OR_ACADEMIC: "a personal or academic site",
    PUBLICATION: "a publication",
}


def site_kind_record(snapshot):
    """`{"kind", "confidence", "why"}` - the classification the advice is shaped by.

    It decides which recommendations fire and how several findings are worded,
    and it was printed nowhere, so a wrong guess could only be seen in its
    effects. Recorded here so the top sheet can state it with its evidence.
    """
    kind = site_kind(snapshot)
    return {"kind": kind.kind, "confidence": kind.confidence, "why": kind.why()}


def top_sheet(report):
    """The five things a non-expert needs, before anything else in the report.

    A 66-70 KB report is more than a non-expert reads, so it gets a one-page
    top sheet. What is already here works - the severity x confidence table, the
    three-item "Start here" with owner and effort, the four-part layout under
    each finding, the table of what an assistant would quote, the appendix
    naming every check that stayed quiet - and the balance is off: the appendix
    is longer than the findings. So nothing is deleted. A page goes in front
    of it, and everything below it is unchanged for the reader who wants the
    evidence.

    Five entries, in the order somebody who has never opened this document
    needs them: what this site is, the worst thing about it, what to do first
    and who does each, how much of the site was actually read, and what part of
    the audit did not run.

    The argument is `report` and nothing else - not the snapshot, not the
    findings before they were ranked, not a count taken again. A summary that
    recomputes anything is a second source of truth and the two will disagree
    on the site where it matters. Where a figure would have had to be invented
    to fit the sentence, the sentence is written without it: "every sub-skill
    ran" rather than "6 of 6 sub-skills ran", because 6 is a constant in this
    file and not a number the report holds.

    Returns `[(label, sentence, [sub-items])]`. report.md and report.html both
    render this one list, so the two documents cannot say different things on
    the page a reader in a hurry is most likely to be the only one to read - on
    a different section the two were checked against each other and found
    disagreeing.
    """
    crawl = report.get("crawl") or {}
    by_id = {f["id"]: f for f in report.get("findings") or []}
    rows = []

    # 1. What this site is. The name and the host as the audit resolved them,
    #    then the site's own opening sentence rather than a description of it -
    #    the sentence in the citation table is what an assistant fetching the
    #    homepage would repeat, which is the closest thing this report has to
    #    an answer in the site's own words.
    site = report.get("site") or crawl.get("origin") or ""
    name = crawl.get("brand_name")
    # A page title is not a name, and this line is where saying so matters.
    # One sheet opened "What this site is. Best handcrafted Jute products
    # Online at ..." - the site's `<title>`, presented as the business - on a
    # site whose own appendix records that it declares no `og:site_name` and no
    # Organization `name`. The report knew and printed the SEO title anyway.
    #
    # The half of this that belongs here is the sentence: `brand_name_source`
    # is published on every report, so this page can say what it is looking at
    # instead of asserting it as the name. The other half is the ladder itself
    # - og:site_name, then Organization `name`, then the domain, and never the
    # title - which is `detect_brand`'s ranking in `crawl.py`, upstream of both
    # this file and every sub-skill's snippets. Fixing it here alone would put
    # a different name in this sentence from the one the paste-ready blocks
    # below are filled in with.
    #
    # The second sentence on the same site: "nothing in its markup - no
    # og:site_name, no Organization name - outranked the title", printed over
    # a school declaring an Organization name on 48 of its 60 crawled pages.
    # The ranking that produced it has since changed - a name the site declares
    # on two pages or more now outranks a title segment, so that site no longer
    # reaches this branch at all - and the sentence stayed false for the sites
    # that still do: a declaration on one deep page, or one this audit refused
    # for a reason of its own, is markup that exists and did not win.
    # `brand_name_declared_in` is what the crawl actually recorded, so the
    # sheet now says which of the two happened.
    source = crawl.get("brand_name_source") or ""
    if name and source.startswith("title"):
        declared = _markup_that_named_the_site(crawl.get("brand_name_declared_in") or [])
        what = ('{}. This report calls it "{}", which is a segment of the site\'s own '
                "homepage title and not a name it declares: {}. Read the name in every "
                "sentence below as this audit's guess at it.".format(
                    site, name,
                    "nothing in its markup - no og:site_name, no Organization name - names "
                    "the site at all" if not declared else
                    "its markup does name it ({}), on too few pages or in a form this audit "
                    "could not accept, so the title was used instead".format(declared)))
    elif name:
        what = "{} at {}.".format(name, site)
    else:
        what = "{}.".format(site)
    requested = _requested_origin(report)
    if requested:
        what += " You asked about {}, which redirects here; every page below came from {}.".format(
            requested, site)
    home = next((c for c in report.get("citation_simulation") or []
                 if c.get("page_type") == "home"), None)
    # The site's own definition first, and the quotable homepage sentence only
    # where the site gives none and that sentence reads as one. See
    # `what_the_site_says_it_is` and `NOT_A_DESCRIPTION_RE`.
    says = report.get("site_says") or {}
    if says.get("text"):
        what += (' It describes itself as: "{}"'.format(says["text"])
                 if says.get("source") == "definition" else
                 ' Its homepage describes it, in its {}, as: "{}"'.format(
                     says.get("source"), says["text"]))
    elif home and home.get("likely_citation") and reads_as_a_description(
            home["likely_citation"]):
        what += ' Its homepage says: "{}"'.format(home["likely_citation"])
    elif home and home.get("likely_citation"):
        what += (" Its homepage carries no sentence this audit could read as the site "
                 "describing itself: the one an assistant would most likely lift from it is a "
                 "notice, an address or an advertisement rather than a description.")
    elif home:
        what += (" Nothing on its homepage both stands alone and states a fact, so an "
                 "assistant that fetches the homepage has no sentence to repeat about what "
                 "this is.")
    # What kind of site the advice below was shaped for, with the evidence, so
    # a wrong reading is visible here rather than only in its effects.
    kind = report.get("site_kind") or {}
    if kind.get("kind"):
        what += (" This audit read it as {} ({} confidence{}), and the advice below is "
                 "tailored to that kind of site, so if that reading is wrong the tailoring is "
                 "too.".format(SITE_KIND_PHRASES.get(kind["kind"], kind["kind"]),
                               kind.get("confidence") or "low",
                               ", from: " + kind["why"] if kind.get("why") else ""))
    elif "site_kind" in report:
        what += (" This audit could not tell what kind of site this is, so the advice below "
                 "is not tailored to one.")
    rows.append(("What this site is", what, []))

    # 2. The single worst thing. The report's own answer, not a second ranking.
    #
    #    `start_here[0]` and not `leads_the_verdict[0]`, and the difference is
    #    not cosmetic. `_start_here_ids` already applies the verdict's
    #    exception - a finding the opening paragraph names as the cause of the
    #    rest leads that list whichever tier it is in - and it applies it after
    #    moving the `low` findings below the substantive ones, which
    #    `leads_the_verdict` does not. Read straight, `leads_the_verdict[0]` on
    #    one fixture named a `low`, demoted corroboration finding as the worst
    #    thing about a site whose "do this first" was missing identity markup.
    #    Taking both from one list is what stops the top sheet's own two
    #    entries disagreeing.
    leads = set(report.get("leads_the_verdict") or [])
    start = [i for i in (report.get("start_here") or []) if i in by_id]
    demoted = [i for i in (report.get("worth_checking") or []) if i in by_id]
    worst_ids = start or [i for i in (report.get("leads_the_verdict") or [])
                          if i in by_id] or demoted
    if worst_ids:
        worst = by_id[worst_ids[0]]
        worst_line = "{} — {}. {}".format(
            worst["id"], worst["title"].rstrip("."),
            worst["suggested_action"]["summary"])
        if worst["id"] in leads:
            worst_line += (" The paragraph under \"The short version\" names this as the "
                           "cause of most of the rest.")
        if worst["id"] in set(demoted):
            worst_line += (" It rests on a weaker reading than the others, so confirm it on "
                           "the site before you spend anything on it.")
    else:
        worst_line = ("No defect was raised at any severity. \"The short version\" below says "
                      "what that does and does not mean.")
    rows.append(("The single worst thing", worst_line, []))

    # 3. What to do first, with who does each and how long. The same three
    #    entries and the same effort wording as "Start here" below, read out of
    #    `start_here` rather than re-ranked here.
    if start:
        fixes = []
        for finding_id in start:
            finding = by_id[finding_id]
            action = finding["suggested_action"]
            fixes.append("{} — {}, {}.".format(
                finding["title"].rstrip("."), action["owner"],
                effort_time(action["effort"], action["owner"])))
        rows.append(("What to do first",
                     "{}, in this order.".format(
                         _plural(len(start), "fix", "fixes")), fixes))
    elif demoted:
        rows.append(("What to do first",
                     "Nothing reached the bar for a first instruction. Everything found rests "
                     "on a reading the audit is not certain of, so it is listed under "
                     "\"{}\" below to confirm rather than ranked as work to start "
                     "on.".format(WORTH_CHECKING_HEADING), []))
    else:
        rows.append(("What to do first",
                     "Nothing. No fix was ranked, because no defect was raised.", []))

    # 4. How much of the site was actually read. Both counts come out of
    #    `crawl{}`, and the second sentence is the one `_coverage_caveat_lines`
    #    prints below - a top sheet that read reassuringly over a crawl that
    #    saw nothing would be the exact failure that block exists to stop, one
    #    page higher up.
    #    "1 URL were crawled". The count was formatted on its own and
    #    glued to a clause written for the plural, which is the shape an
    #    earlier fix corrected about forty times in three other skills. The verb
    #    goes inside the call, so one argument decides both halves and they
    #    cannot come apart again.
    urls = crawl.get("pages_crawled", 0)
    read_line = "{} on this site and {} came back with readable text.".format(
        _plural(urls, "URL was crawled", "URLs were crawled"),
        _plural(crawl.get("readable_pages", 0), "page", "pages"))
    if crawl.get("non_page_responses"):
        read_line += (" {} more {} with a file rather than a page - an image, a document, a "
                      "script - and {} counted nowhere in this report.".format(
                          crawl["non_page_responses"],
                          "address answered" if crawl["non_page_responses"] == 1
                          else "addresses answered",
                          "is" if crawl["non_page_responses"] == 1 else "are"))
    if crawl.get("enough_to_judge", True):
        # "Every count below is out of that, and nothing below rests on more"
        # was false in the document that printed it. The sheet named 53
        # crawled and 49 readable; the body then said "41 of the 53 pages" and
        # "19 of 53 crawled page(s)", so the counts below were out of neither
        # of the two numbers the sentence pointed at with the word "that".
        #
        # Three different denominators are in play below and this sentence
        # cannot name one of them: `state_the_denominator` divides by the
        # pages that answered, or by one page type where every affected page is
        # of that type, and a check that sized itself keeps its own count. What
        # is true of all three is the bound - none of them is bigger than the
        # crawl - so the sentence states the bound and says where the one
        # bigger number in the report comes from.
        read_line += (
            " No page count below is out of more than {}: where a check looked at one "
            "kind of page it says which kind, and the only bigger number in this report is "
            "the site's own sitemap, which is named as the sitemap where it appears.".format(
                # "more than those 1" on a one-page crawl, which is one of the
                # shapes the awkward-site population is chosen for.
                "that one URL" if urls == 1 else "those {} URLs".format(urls)))
    else:
        read_line += (" That is too little to grade the site: a low count below means this "
                      "audit found little, not that there is little to find.")
    rows.append(("How much of the site was read", read_line, []))

    # 5. What did not run. A whole sub-skill missing is the largest gap an
    #    audit can have and it was disclosed at line 531 of one report, so it
    #    is on this page as well as in the header block below. No "N of 6": the
    #    6 is a constant in this file, and a figure this report does not hold
    #    has no business on a page whose whole rule is that it invents nothing.
    dropped = sorted({(item.get("skill") or "a sub-skill")
                      for item in report.get("skills_that_did_not_run") or []})
    unanswered = len(report.get("unverifiable") or [])
    silent = len(report.get("checks_with_nothing_to_run_against") or [])
    if dropped:
        did_not_run = ("{} did not run, so nothing below covers what {} examines. Treat that "
                       "as a gap in this report, not as a pass.".format(
                           ", ".join(dropped), "it" if len(dropped) == 1 else "they"))
    else:
        did_not_run = "Every sub-skill ran."
    if unanswered:
        did_not_run += (" {} that this audit set out to settle and could not {} listed under "
                        "\"{}\" near the end.".format(
                            _plural(unanswered, "question", "questions"),
                            "is" if unanswered == 1 else "are", UNANSWERED_HEADING))
    if silent:
        did_not_run += " {} never reached anything to judge.".format(
            _plural(silent, "check", "checks"))
    rows.append(("What part of the audit did not run", did_not_run, []))

    return rows


# How many times a value is unescaped before this file gives up on it.
#
# A homeware brand whose name carries an ampersand had it double-escaped in the
# site's own `og:site_name`, so a name written `Ash&Loom` reached this report as
# `Ash&amp;amp;Loom` and report.md printed, in prose, "A machine can reach
# Ash&amp;amp;Loom and read it." Markdown decodes no character reference, so
# that is the string the reader saw. One pass of `html.unescape` turns it into
# `Ash&amp;Loom`, which is the same defect one step smaller, so the decode
# repeats until the text stops changing.
#
# Four passes, and a bound rather than a `while True`, because the loop's own
# output is its next input: a value written as `&amp;amp;amp;amp;` is already
# further from anything a site means than this file should be guessing about,
# and a value engineered to keep producing new escapes must not spin here.
ENTITY_DECODE_PASSES = 4

# A run of text between backticks. Left alone by the decode: a fix step tells
# the reader to re-check a page in View Source and confirm the value carries
# "the punctuation itself, not `&#39;` or `&amp;`", and decoding those two
# spans turns that instruction into "not ' or &", which is the state it is
# telling them to fix.
_INLINE_CODE_RE = re.compile(r"(`[^`]*`)")


def decode_character_references(text):
    """Text with `&amp;`, `&#39;` and their double-escaped forms resolved."""
    out = str(text or "")
    for _ in range(ENTITY_DECODE_PASSES):
        once = html.unescape(out)
        if once == out:
            break
        out = once
    return out


# The letters that are written right to left: Hebrew and its presentation
# forms, Arabic and its supplements, Syriac, Thaana and N'Ko. A report quoting
# any of them inside an English sentence has a bidirectional problem whether or
# not anybody has looked at one yet.
_RTL_LETTER = (r"\u0590-\u05ff\u0600-\u06ff\u0700-\u074f\u0780-\u07bf"
               r"\u07c0-\u07ff\u0860-\u08ff\ufb1d-\ufdff\ufe70-\ufeff")

# What may sit inside one run without ending it: spacing, the punctuation both
# scripts share, the Arabic marks, and digits in any of the three sets. A Latin
# letter is deliberately not here - an isolate takes its direction from the
# first strong letter in it, so a run swallowing an English word would lay that
# word out backwards, which is the fault this is fixing rather than a fix.
_RTL_INNER = _RTL_LETTER + r" \t0-9\u0660-\u0669\u06f0-\u06f9.,:;!?/'\"()\[\]-"

# Longest first, so a run is isolated once rather than in fragments. The
# alternative is a single letter standing alone.
_RIGHT_TO_LEFT_RE = re.compile(
    "[" + _RTL_LETTER + "][" + _RTL_INNER + "]*[" + _RTL_LETTER + "]"
    "|[" + _RTL_LETTER + "]")

# U+2068 FIRST STRONG ISOLATE and U+2069 POP DIRECTIONAL ISOLATE. The pair
# Unicode defines for exactly this: text of unknown direction embedded in text
# of known direction. They take the run's own direction from its first strong
# letter and hand the direction back afterwards, so the punctuation around the
# quotation stays where it was written. Both are zero-width formatting
# characters - a renderer that does not implement them shows nothing.
_ISOLATE_OPEN, _ISOLATE_CLOSE = u"\u2068", u"\u2069"

# An address, as one whitespace-delimited token: anything carrying a scheme,
# anything starting `www.`, and a path starting `/` at the start of a token.
# An Arabic author's page lives at `/authors/view/home/17941/<Arabic name>/`,
# and isolating the name put two invisible characters inside the path, so the
# address a reader copied out of the report was not one the site serves.
_ADDRESS_TOKEN_RE = re.compile(
    r"(?:(?<=[\s(\[<\"'])|^)(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.|/)[^\s<>\"']*")


def isolate_right_to_left_runs(text):
    """`text` with every right-to-left run wrapped in a directional isolate.

    The report is written left to right and quotes the site's own words inside
    its own sentences. Where those words are Arabic, Hebrew, Persian or Urdu,
    the bidirectional algorithm gives every neutral character next to them -
    the closing quotation mark, the brackets, the counts, the full stop - to
    the right-to-left run, and they render at its other end. A reader of an
    Arabic site's report sees the punctuation of every evidence line displaced.

    Nothing is wrong with the string, so nothing upstream can fix it; it is a
    rendering fault, and this is the render boundary.

    Never inside an address. A URL is something a reader copies and pastes,
    and it has to arrive byte for byte as the site serves it; the isolates are
    for prose around it. So the text is cut at every address token and only
    the pieces between them are isolated.
    """
    text = str(text or "")
    out, at = [], 0
    for match in _ADDRESS_TOKEN_RE.finditer(text):
        out.append(_RIGHT_TO_LEFT_RE.sub(
            lambda run: _ISOLATE_OPEN + run.group(0) + _ISOLATE_CLOSE,
            text[at:match.start()]))
        out.append(match.group(0))
        at = match.end()
    out.append(_RIGHT_TO_LEFT_RE.sub(
        lambda run: _ISOLATE_OPEN + run.group(0) + _ISOLATE_CLOSE, text[at:]))
    return "".join(out)


def decode_rendered_markdown(markdown):
    """The rendered report with no character reference left in prose, and
    every right-to-left quotation isolated.

    The render boundary, deliberately, rather than each place a name or a
    value is read. Every string this report prints passes through here, so a
    check written next month inherits both; and the two places a reference is
    meant literally - a fenced snippet, and an inline code span - are the two
    this function can see and skip. A snippet is pasted into a page, so it has
    to leave here byte for byte as the check wrote it.
    """
    out, in_fence = [], False
    for line in markdown.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
        elif in_fence:
            out.append(line)
        else:
            out.append("".join(
                part if part.startswith("`")
                else isolate_right_to_left_runs(decode_character_references(part))
                for part in _INLINE_CODE_RE.split(line)))
    return "\n".join(out)


def render_markdown(report):
    lines = []
    add = lines.append

    add("# AI readiness audit: {}".format(report["site"]))
    add("")
    add("Audited {} · {} · {} · {}{}".format(
        report["audited_at"],
        _pages_line(report),
        _plural(report["summary"]["total_findings"], "finding", "findings"),
        _plural(len(report["recommendations"]), "recommendation", "recommendations"),
        _asked_about(report)))
    add("")
    # Above everything, including the verdict. The verdict is one paragraph of
    # prose, and what was missing was not a shorter paragraph - it was a
    # page that can be scanned. Scan first,
    # then read.
    add("## {}".format(TOP_SHEET_HEADING))
    add("")
    add("_{}_".format(TOP_SHEET_BLURB))
    add("")
    for label, sentence, items in top_sheet(report):
        add("- **{}.** {}".format(label, sentence))
        for position, item in enumerate(items, start=1):
            add("  {}. {}".format(position, item))
    add("")
    add("## The short version")
    add("")
    add(report["summary"]["verdict"])
    add("")
    # Directly under the verdict and above the counts: what the reader is up
    # against, before they read the first instruction rather than after the
    # ninth one they cannot carry out.
    who = _who_you_will_need(report)
    if who:
        add(who)
        add("")
    # Whether there is a site at all, then whose site it is, then how good it
    # is. All three blocks sit above the severity table, because a table of
    # counts under a reassuring paragraph is what shipped twice.
    not_serving = _not_serving_caveat(report)
    if not_serving:
        heading, not_serving_lines = not_serving
        add("### {}".format(heading))
        add("")
        for line in not_serving_lines:
            add("- {}".format(line))
        add("")
    origin = _origin_caveat(report)
    if origin:
        heading, origin_lines = origin
        add("### {}".format(heading))
        add("")
        for line in origin_lines:
            add("- {}".format(line))
        add("")
    caveat = _coverage_caveat_lines(report)
    if caveat:
        add("### {}".format(COVERAGE_CAVEAT_HEADING))
        add("")
        for line in caveat:
            add("- {}".format(line))
        add("")
    dropped = _skills_that_did_not_run_lines(report)
    if dropped:
        add("### {}".format(DROPPED_SKILL_HEADING))
        add("")
        for line in dropped:
            add("- {}".format(line))
        add("")

    by_id = {f["id"]: f for f in report["findings"]}
    demoted_ids = report.get("worth_checking") or []
    demoted = [by_id[i] for i in demoted_ids if i in by_id]
    graded = [f for f in report["findings"] if f["id"] not in set(demoted_ids)]
    table_rows, follows_rows = severity_table(report)
    if demoted:
        # Three columns, not one. A reader who saw "High: 2" and then found one
        # of the two under a heading saying it might be wrong would reasonably
        # conclude the table was lying, so the table shows the split itself.
        add("| Severity | Reported with confidence | Worth checking |")
        add("| --- | --- | --- |")
        for label, confident_count, worth_count in table_rows:
            add("| {} | {} | {} |".format(label, confident_count, worth_count))
        for cause, ids, confident_count, worth_count in follows_rows:
            add("| {} | {} | {} |".format(_follows_row_label(cause, ids),
                                          confident_count, worth_count))
        add("")
        add("{} of the {} below {} on a weaker reading, so {} listed under \"{}\" rather "
            "than beside the findings the audit read directly. They are still counted in "
            "this table.".format(
                len(demoted),
                _plural(report["summary"]["total_findings"], "finding", "findings"),
                "rests" if len(demoted) == 1 else "rest",
                "it is" if len(demoted) == 1 else "they are", WORTH_CHECKING_HEADING))
        add("")
    else:
        add("| Severity | Count |")
        add("| --- | --- |")
        for label, confident_count, worth_count in table_rows:
            add("| {} | {} |".format(label, confident_count + worth_count))
        for cause, ids, confident_count, worth_count in follows_rows:
            add("| {} | {} |".format(_follows_row_label(cause, ids),
                                     confident_count + worth_count))
        add("")

    if report["start_here"]:
        add("## Start here")
        add("")
        # The prose used to say the list was ranked "by how much they change
        # relative to the work involved, not by severity alone", and a comment
        # claimed a medium could outrank a high. `_start_here_ids` walks
        # `ranked`, which sorts on severity band first, so that never happened
        # and the paragraph described a rule the code does not use. It now
        # describes the rule the code does use.
        add("{}, in the order to do them. The ranking puts the most severe band first, and "
            "inside a band puts the fix that changes most for the least work "
            "first.".format(_plural(len(report["start_here"]), "fix", "fixes")))
        add("")
        # A reader who sees a `high` finding below, absent from this list,
        # reasonably concludes the report contradicts itself.
        #
        # Two different reasons a severe finding can be missing from this list,
        # and they need different sentences. It was outranked, or it is a
        # weaker reading and is not eligible for this list at all. Explaining
        # the second as though it were the first would tell the owner a finding
        # "ranks lower" when what actually happened is that the audit is not
        # sure it is true.
        severe = [by_id[i]["id"] for i in report["start_here"]]
        # A severe finding that is another finding read again is left off for
        # that reason, and says so. "F-004 (high) is more severe than the 3
        # fixes on this list" was printed about a finding marked, in its own
        # paragraph, as the homepage shell seen from another angle.
        repeats = [f for f in graded
                   if f["severity"] in ("critical", "high") and f["id"] not in severe
                   and f.get("follows_from")]
        if repeats:
            causes = sorted({f["follows_from"]["id"] for f in repeats})
            add("{} {} not on this list: {} the same defect as {}, read from another angle, "
                "so fixing {} comes first.".format(
                    "; ".join("{} ({})".format(f["id"], f["severity"]) for f in repeats[:3]),
                    "is" if len(repeats) == 1 else "are",
                    "it is" if len(repeats) == 1 else "each is",
                    " or ".join(causes), "that" if len(causes) == 1 else "those"))
            add("")
        omitted = [f for f in graded
                   if f["severity"] in ("critical", "high") and f["id"] not in severe
                   and not f.get("follows_from")]
        # A weaker reading the verdict named is on this list, so the sentence
        # explaining why weaker readings are not on this list may not name it.
        demoted_severe = [f for f in demoted
                          if f["severity"] in ("critical", "high")
                          and f["id"] not in set(severe)]
        if demoted_severe:
            add("{} {} not on this list: {} would matter if confirmed, but {} rests on a "
                "weaker reading, so {} under \"{}\" below. Confirm {} before spending "
                "anything on {}.".format(
                    "; ".join("{} ({})".format(f["id"], f["severity"]) for f in demoted_severe),
                    "is" if len(demoted_severe) == 1 else "are",
                    "it" if len(demoted_severe) == 1 else "they",
                    "it" if len(demoted_severe) == 1 else "each",
                    "it sits" if len(demoted_severe) == 1 else "they sit",
                    WORTH_CHECKING_HEADING,
                    "it" if len(demoted_severe) == 1 else "them",
                    "it" if len(demoted_severe) == 1 else "them"))
            add("")
        # Severity decides the list, so severity has to be the first thing
        # this paragraph reasons about. It used to reason only about the
        # priority score, which is the tiebreak inside one band, and the
        # sentence it produced was therefore capable of explaining a `high`
        # omission by "each is either narrower or more expensive" when the
        # real reason was the band above it - or, on one real run, by
        # calling it "of equal rank" with a `low` finding in its slot.
        listed_ranks = [SEVERITY_RANK[by_id[i]["severity"]] for i in report["start_here"]]
        weakest_listed = max(listed_ranks) if listed_ranks else SEVERITY_RANK["info"]
        # More severe than something on the list. Impossible on severity
        # alone, so the only route here is the verdict's exception holding the
        # last slot for the finding the opening paragraph names as the cause
        # of the rest. That is a deliberate demotion and the sheet names it.
        outranking = [f for f in omitted
                      if SEVERITY_RANK[f["severity"]] < weakest_listed]
        leads = set(report.get("leads_the_verdict") or [])
        held_for_the_verdict = [i for i in report["start_here"]
                                if i in leads
                                and SEVERITY_RANK[by_id[i]["severity"]] == weakest_listed]
        if outranking:
            if held_for_the_verdict:
                add("{} {} more severe than {}, which holds a place on this list because the "
                    "paragraph above names it as the cause of the rest. {} written out in "
                    "full below.".format(
                        "; ".join("{} ({})".format(f["id"], f["severity"])
                                  for f in outranking[:3]),
                        "is" if len(outranking) == 1 else "are",
                        held_for_the_verdict[0],
                        "It is" if len(outranking) == 1 else "They are"))
            else:
                add("{} {} more severe than the {} on this list and {} written out in full "
                    "below.".format(
                        "; ".join("{} ({})".format(f["id"], f["severity"])
                                  for f in outranking[:3]),
                        "is" if len(outranking) == 1 else "are",
                        _plural(len(report["start_here"]), "fix", "fixes"),
                        "is" if len(outranking) == 1 else "are"))
            add("")
        # Less severe than everything listed. The band is the whole reason and
        # the score has nothing to add.
        outbanded = [f for f in omitted
                     if SEVERITY_RANK[f["severity"]] > weakest_listed]
        if outbanded:
            add("{} {} below the {} above because {} of a less serious band, and {} listed "
                "in full below.".format(
                    "; ".join("{} ({})".format(f["id"], f["severity"])
                              for f in outbanded[:3]),
                    "ranks" if len(outbanded) == 1 else "rank",
                    _plural(len(report["start_here"]), "fix", "fixes"),
                    "it is" if len(outbanded) == 1 else "they are",
                    "is" if len(outbanded) == 1 else "are"))
            add("")
        # What is left is a tie on severity, decided by the priority score, and
        # only then does the score explain anything. Only findings that
        # genuinely score below something on the list get a reason: the old
        # code gave one to every omission, and on four highs scoring
        # identically it printed "F-004 (high, it affects 60 of the 60 pages
        # crawled) ranks lower for that reason" - naming full site reach, which
        # is the term that raises a score, as the cause of a low one. The real
        # cause there was three slots and a tie broken on the id.
        same_band = [f for f in omitted
                     if SEVERITY_RANK[f["severity"]] == weakest_listed]
        listed_scores = [by_id[i]["suggested_action"]["priority_score"]
                         for i in report["start_here"]
                         if SEVERITY_RANK[by_id[i]["severity"]] == weakest_listed]
        floor = min(listed_scores) if listed_scores else 0.0
        outscored = [f for f in same_band
                     if f["suggested_action"]["priority_score"] < floor]
        if outscored:
            explained = []
            for f in outscored[:3]:
                if f["suggested_action"]["effort"] == "high":
                    # Same rule as the effort line itself: the discipline
                    # comes from the owner, so this sentence cannot price a
                    # writer's work in developer-days either.
                    reason = "it needs {}".format(effort_time(
                        "high", f["suggested_action"].get("owner", "")))
                elif f.get("affected_page_count") and f.get("reach", 1.0) < 1.0:
                    reason = "it affects {} of the {} pages crawled".format(
                        f["affected_page_count"], report["crawl"]["pages_crawled"])
                else:
                    reason = "the fixes listed change more for the same work"
                explained.append("{} ({}, {})".format(f["id"], f["severity"], reason))
            add("{} rank{} below the {} above - each is either narrower or more expensive - "
                "and {} listed in full below.".format(
                    "; ".join(explained), "s" if len(explained) == 1 else "",
                    _plural(len(report["start_here"]), "fix", "fixes"),
                    "is" if len(explained) == 1 else "are"))
            add("")
        elif same_band:
            # A genuine tie: same severity band, and nothing separating them
            # on score either. "1 further serious finding of equal rank is
            # listed in full below rather than here" is true of this set and
            # was printed over a `high` finding displaced by a `low` one, so
            # the set it is printed over is now the only change it needed.
            add("This list holds {}, so {} of equal rank {} listed in full below rather "
                "than here.".format(
                    _plural(len(report["start_here"]), "fix", "fixes"),
                    _plural(len(same_band), "further serious finding",
                            "further serious findings"),
                    "is" if len(same_band) == 1 else "are"))
            add("")
        demoted_here = {f["id"] for f in demoted}
        for position, finding_id in enumerate(report["start_here"], start=1):
            finding = by_id[finding_id]
            action = finding["suggested_action"]
            add("{}. **{}** ({}) — {}".format(position, finding["title"], finding["id"],
                                              action["summary"]))
            # The verdict's exception, marked where it lands. "Start here" is
            # otherwise drawn only from the tier the audit read directly, and
            # an entry from the weaker tier arriving here unmarked would tell
            # the owner to spend a day on a reading the same report asks them
            # to check. It is listed because the opening paragraph names it as
            # the cause of the rest, and confirming it is the first move.
            if finding_id in demoted_here:
                add("   Confirm this one first: it is here because the verdict "
                    "above names it as the cause of the rest, and it rests on a "
                    "weaker reading than the others - see \"{}\" below for what "
                    "was read and what was not.".format(WORTH_CHECKING_HEADING))
            add("   Who does it: {}. Roughly: {}.".format(
                action["owner"], effort_time(action["effort"], action["owner"])))
        add("")
    elif demoted:
        # No "Start here" at all, with findings on the page, reads as the
        # section having gone missing. Say why it is empty: the list may only
        # draw from readings the audit is sure of, and none of these are.
        add("## Start here")
        add("")
        add("Nothing here reached the bar for a first instruction. There {} below, but {} on "
            "a reading the audit is not certain of, so {} listed under \"{}\" for you to "
            "confirm rather than ranked as work to start on.".format(
                "is {}".format(_plural(len(demoted), "finding", "findings"))
                if len(demoted) == 1 else
                "are {}".format(_plural(len(demoted), "finding", "findings")),
                "it rests" if len(demoted) == 1 else "each rests",
                "it is" if len(demoted) == 1 else "they are", WORTH_CHECKING_HEADING))
        add("")

    # Once, above the first finding of the report, whichever section that
    # turns out to be. Repeating it under every severity heading would put it
    # five times in a document already longer than it needs to
    # be; leaving it out entirely is what let a reader compare two runs on the
    # F-number.
    explained_the_ids = False
    # The print order, worked out first so a repeated step is written out
    # under the first finding that prints it and referred to after.
    plan = repeated_steps_plan(_findings_in_print_order(graded, demoted))
    # What has been written out once in this document already. See
    # `_render_finding`.
    printed = {}
    for key, label, blurb in SEVERITY_HEADINGS:
        group = [f for f in graded if f["severity"] == key]
        if not group:
            continue
        group.sort(key=lambda f: (-f["suggested_action"]["priority_score"], f["id"]))
        add("## {} findings".format(label))
        add("")
        add("_{}_".format(blurb))
        add("")
        if not explained_the_ids:
            add(ID_NOTE)
            add("")
            explained_the_ids = True
        for finding in group:
            add(_render_finding(finding, steps=display_steps(finding, plan), context=printed))
        add("")

    if demoted:
        add("## {}".format(WORTH_CHECKING_HEADING))
        add("")
        add("_{}_".format(WORTH_CHECKING_BLURB))
        add("")
        if not explained_the_ids:
            add(ID_NOTE)
            add("")
            explained_the_ids = True
        for finding in demoted:
            add(_render_finding(finding, demoted=True, steps=display_steps(finding, plan),
                                context=printed))
        add("")

    if report["recommendations"]:
        add("## Beyond the defects")
        add("")
        add("These are not problems with the site. They are things that would raise the chance "
            "of being found, quoted and trusted, and they are included only where the audit saw "
            "the condition that makes them relevant.")
        add("")
        for rec in report["recommendations"]:
            add("### {}".format(rec["title"]))
            add("")
            add(rec["summary"])
            add("")
            add("**Why this works.** {} — {}".format(
                MECHANISMS[rec["mechanism"]].rstrip("."), rec["why_this_works"]))
            add("")
            add("**How to do it** — {}, roughly {}:".format(
                rec["owner"], effort_time(rec["effort"], rec["owner"])))
            for step in rec["how_to_do_it"]:
                add("- {}".format(step))
            if rec.get("snippet"):
                add("")
                if rec.get("snippet_warning"):
                    add("> **{}**".format(rec["snippet_warning"]))
                    add("")
                if _non_public_host(rec["snippet"]):
                    add("> **{}**".format(NON_PUBLIC_HOST_WARNING))
                    add("")
                add("```")
                add(rec["snippet"])
                add("```")
            add("")

    citations = report.get("citation_simulation") or []
    if citations:
        add("## What an assistant would quote")
        add("")
        add("For each key page, the sentence an assistant fetching it would most likely lift. "
            '"Nothing quotable" means the page contains no self-contained sentence stating a fact.')
        add("")
        add("| Page | Type | Likely quote |")
        add("| --- | --- | --- |")
        for citation in citations:
            # Every cell is site-derived and every cell is escaped. A URL
            # holding a `|` (https://x.test/?q=a|b) produced four cells in a
            # three-column table and broke the table from that row down, and a
            # page sentence containing `<img src=x onerror=...>` reached
            # report.md as live HTML.
            quote = citation.get("likely_citation")
            body = (_escape_cell(quote) if quote
                    else "**Nothing quotable** — " + _escape_cell(citation.get("why")))
            add("| {} | {} | {} |".format(
                _escape_cell(citation.get("url")),
                _escape_cell(citation.get("page_type")), body))
        add("")

    add("## Appendix: what was checked")
    add("")
    add(_appendix_paragraph(report))
    add("")
    # Directly under the arithmetic, because it is the other thing a reader
    # checks about a report before they trust the rest of it: would running it
    # again say this again.
    add(repeatability_note(report))
    add("")
    if report["crawl"]["notes"]:
        for note in report["crawl"]["notes"]:
            add("- Note: {}".format(note))
        add("")

    # A skill that did not run is counted here, because the appendix's buckets
    # have to add up to the checks that ran, and printed elsewhere, because
    # "each of these ran" is the opposite of what its own reason says. The line
    # below tells a reader who is adding up where it went, so a bucket of 7
    # showing 6 bullets is not a missing check.
    did_not_run = {(c["skill"], c["check"])
                   for c in report.get("skills_that_did_not_run") or []}
    declines = [n for n in report["not_applicable"]
                if (n.get("skill") or "", n["check"]) not in did_not_run]
    if report["not_applicable"]:
        add("### Checks that did not apply, and why")
        add("")
        # Not "these were run and found nothing to report". That is word for
        # word the claim the paragraph three lines above counts under "N that
        # ran and found nothing wrong", and in one real report that
        # number was 0 with this list of declines printed under it - two
        # sentences describing the same set and disagreeing about how many were
        # in it. The count and this heading now name different sets, because
        # they are different sets: a decline found its subject absent from the
        # site and so had nothing to judge, which is not a clean result.
        add("A check that stays quiet on purpose is as important as one that fires. Each of "
            "these ran, found nothing on this site for it to judge, and stopped there - which "
            "is not the same as looking and finding the site clean:")
        add("")
        # One bullet per skill, each check or group of checks with the first
        # sentence of its reason. Sixty bullets of full reasons were a third
        # of one report; every word of every reason is in report.json.
        by_skill = collections.OrderedDict()
        for checks, skill, reason in grouped_declines(declines):
            by_skill.setdefault(skill, []).append("{}: {}".format(
                _names_in_prose(checks), first_sentence_of_a_reason(reason).rstrip(".")))
        for skill, entries in by_skill.items():
            add("- *{}* - {}".format(skill, "; ".join(entries)))
        if did_not_run:
            add("- {} of the {} counted above {} whole {} that did not run at all, which is "
                "not the same thing. {} listed under \"{}\" above.".format(
                    len(did_not_run), len(report["not_applicable"]),
                    "is a" if len(did_not_run) == 1 else "are",
                    "sub-skill" if len(did_not_run) == 1 else "sub-skills",
                    "It is" if len(did_not_run) == 1 else "They are",
                    UNANSWERED_HEADING))
        add("")

    if report.get("checks_passed"):
        add("### Checks that ran and found nothing wrong")
        add("")
        add("Listed so that silence is never ambiguous. These ran against this site and were "
            "clean - they are not omissions:")
        add("")
        for item in report["checks_passed"]:
            add("- **{}** ({})".format(item["check"], item.get("skill", "")))
        add("")

    if report.get("checks_with_nothing_to_run_against"):
        add("### {}".format(NOTHING_TO_RUN_AGAINST_HEADING))
        add("")
        add(NOTHING_TO_RUN_AGAINST_BLURB)
        add("")
        for item in report["checks_with_nothing_to_run_against"]:
            add("- **{}** ({})".format(item["check"], item.get("skill", "")))
        add("")

    if report.get("checks_that_found_something"):
        add("### Checks that contributed to a finding above")
        add("")
        add("Each of these ran in the same step as a finding this report publishes, from a "
            "skill every one of whose findings is above, and is part of why it was raised. "
            "They are listed for completeness: with them and the list below, every check "
            "that ran is accounted for in the count at the top of this appendix.")
        add("")
        for item in report["checks_that_found_something"]:
            add("- **{}** ({})".format(item["check"], item.get("skill", "")))
        add("")

    if report.get("checks_that_found_nothing_published"):
        add("### Checks that fired, with no finding above filed under them")
        add("")
        add("Each of these was marked as having found something by the skill that ran it, "
            "and no finding in this report is filed under its name. Either that skill "
            "published nothing at all, or something it raised did not survive into this "
            "report - held back as a question this audit could not answer, or merged into "
            "another finding - and which of its checks belonged to that one is not recorded, "
            "so none of them is claimed as contributing. They used to be listed under the "
            "heading above, which told the reader to go and look for a finding that is not "
            "there.")
        add("")
        for item in report["checks_that_found_nothing_published"]:
            add("- **{}** ({})".format(item["check"], item.get("skill", "")))
        add("")

    if report.get("unverifiable"):
        add("### {}".format(UNANSWERED_HEADING))
        add("")
        add(UNANSWERED_BLURB)
        add("")
        for titles, reason in grouped_unanswered(report["unverifiable"]):
            add("- {} - {}.".format(_names_in_prose(titles), reason))
        add("")

    if report["merged_duplicates"]:
        add("### Observations merged")
        add("")
        for item in report["merged_duplicates"]:
            # Two reasons a merge happens and they are not the same statement.
            # `reason` is set by the one-job fold, where the two findings have
            # different root causes and are still one edit; without this the
            # line claimed the two reported the same cause, which is the thing
            # `test_no_root_cause_has_two_owners` guarantees never happens.
            line = "- `{}` from {} was merged into `{}`, {}.".format(
                item["id_hint"], item["skill"], item["merged_into"],
                item.get("reason") or "which reports the same root cause")
            if item.get("severity_raised_to"):
                line += (" The merged finding was raised from {} to {}, because the two "
                         "checks disagreed about how serious it is.".format(
                             item["severity_raised_from"], item["severity_raised_to"]))
            if item.get("fix_taken_from"):
                line += " The fix printed above is the fuller one, from {}.".format(
                    item["fix_taken_from"])
            add(line)
        add("")

    add("---")
    add("")
    add("Generated by {} v{}. Read-only: this audit made no change to the site and submitted "
        "no forms.".format(report["auditor"]["name"], report["auditor"]["version"]))
    add("")
    return decode_rendered_markdown("\n".join(lines))


# A sentence in a crawl note claiming that running this audit again produces
# this audit again.
#
# The claim itself is legitimate and this file is where it belongs. It was
# being made in `crawl.py`, in the middle of a note about page cost - "How much
# of the site is read is decided by the budget and by the site's own speed, so
# a second audit of an unchanged site reads the same pages and reports the same
# findings" - and two runs of one site disproved it: 53 pages then
# 60, 16 findings then 19. The crawler that writes that sentence cannot see
# whether it turned out to be true, because it writes it while planning and the
# clock can still cut the crawl short afterwards. The snapshot's `page_plan`
# records what actually happened, so the claim moves to the one place that can
# read that field and is made from it.
#
# Matched on the assertion rather than on the wording, so the same promise in a
# note written next month is caught too. The measurement in front of it - what
# a page cost, how many pages the budget bought - is a fact and is kept.
_REPEATABILITY_PROMISE_RE = re.compile(
    r"(?:second|another|later|repeat(?:ed)?)\s+(?:audit|run|crawl)"
    r"|run\s+(?:it\s+)?again\s+(?:reads|gives|produces)"
    r"|same\s+pages\s+and\s+reports\s+the\s+same", re.I)

# Where one sentence of a note ends. Notes are single-line prose written by the
# crawler; nothing in them abbreviates with a full stop except a decimal, and
# the digit lookaround leaves "1.4s" alone.
_NOTE_SENTENCE_RE = re.compile(r"(?<!\d)(?<=[.!?])\s+(?=[A-Z(])")


def notes_without_a_repeatability_promise(notes):
    """Crawl notes with any claim about a second run of this site removed.

    Removed rather than rewritten, and only that sentence: the note it sits in
    also states what a page of this site cost to read and how many pages the
    budget bought, which are measurements and are the reason the note exists.
    `repeatability_note` then says what this run can actually support, from
    `page_plan`, in one place.
    """
    out = []
    for note in notes:
        kept = [sentence for sentence in _NOTE_SENTENCE_RE.split(str(note or ""))
                if sentence and not _REPEATABILITY_PROMISE_RE.search(sentence)]
        if kept:
            out.append(" ".join(kept))
    return out


def repeatability_note(report):
    """Whether a second audit of this unchanged site would say the same thing.

    Every report used to assert that it would, in the appendix, in a sentence
    the crawler wrote while planning the crawl. One site run twice gave
    53 pages then 60, and 16 findings then 19. The sentence was false in
    the document that printed it, which is the worst kind of wrong a report can
    be about itself: a reader who checks one self-description and finds it
    untrue has no way to grade the rest.

    So the claim is made from `crawl.page_plan`, which the crawler writes after
    the fact:

      `deterministic` false   the wall clock, a rate limiter or a sitemap fetch
                              cut the crawl short, so the page set is whatever
                              was reached before it stopped and a second run
                              may reach a different one.
      `deterministic` true    the count was fixed by dividing the budget by a
                              measured page cost rounded onto a ladder, before
                              the crawl was anywhere near its deadline, so two
                              runs over a slightly different network agree.
      missing                 an older snapshot, or a crawl that never got far
                              enough to plan. Nothing is claimed either way.

    The sentence names the mechanism rather than restating the promise,
    because the promise is what was disproved and a reader who is given the
    mechanism can check it against the numbers three lines above.
    """
    crawl = report.get("crawl") or {}
    plan = crawl.get("page_plan") or {}
    deterministic = plan.get("deterministic")
    if deterministic is None:
        return ("This report does not record what fixed its page count, so it cannot say "
                "whether a second audit of this site would read the same pages. Compare two "
                "audits by the short name printed beside each finding rather than by its "
                "F-number, which is a position and moves.")
    if not deterministic:
        truncated = plan.get("truncated_at")
        return ("The crawl stopped{} before it had read the pages its budget bought, so the "
                "page set is what was reached rather than what was planned and a second audit "
                "of this site may read a different set and report a different number of "
                "findings. Compare the two by the short name printed beside each finding "
                "rather than by its F-number, which is a position and moves.".format(
                    " at {}".format(truncated) if truncated else ""))
    planned, rung = plan.get("planned_pages"), plan.get("cost_rung_s")
    how = ""
    if planned and rung:
        how = (": a page of this site was measured at {}s, rounded up to {}s, and a budget "
               "this size buys {} of them".format(
                   plan.get("measured_cost_s"), rung, planned))
    return ("The page count was fixed by the plan rather than by where the clock fell{}, and "
            "the rounding is what lets two runs over a slightly different network agree. On "
            "that basis a second audit of this site, unchanged, reads the same pages and "
            "reports the same findings.".format(how))


def _string_arguments(node):
    """Every string a call's argument can evaluate to: a literal, or either arm
    of a conditional."""
    import ast
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _string_arguments(node.body) + _string_arguments(node.orelse)
    return []


_REGISTERED = {}


def checks_the_marketplace_registers():
    """`{(skill, check)}` for every check the six sub-skills can register, or None.

    Every report said "92 checks were run" while the README, which counts the
    code, said 96. Both were true: a check is registered only when a run
    reaches the branch that asks it, and four branches are for sites this one
    was not. A reader holding the two numbers could not tell that, so the
    appendix states both, and the bigger one is counted here the way the
    README's own test counts it - every string passed to `.check` or `.skip`
    in each skill's `check.py` - rather than written down as a constant that
    would drift the way the README's number did three times.

    None when a sub-skill's source is not where the marketplace puts it, so a
    partial install prints the older sentence rather than a wrong total.
    """
    if "checks" in _REGISTERED:
        return _REGISTERED["checks"]
    import ast
    skills_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    found = set()
    for skill in SUB_SKILLS:
        path = os.path.join(skills_dir, skill, "scripts", "check.py")
        if not os.path.isfile(path):
            _REGISTERED["checks"] = None
            return None
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("check", "skip") and node.args):
                found.update((skill, value) for value in _string_arguments(node.args[0]))
    _REGISTERED["checks"] = found
    return found


# How many of the checks this run never registered are named in the appendix
# sentence. The rest are in report.json.
NOT_REACHED_NAMED = 4


def checks_the_run_did_not_reach(checks_run):
    """`{"checks_registered": n, "checks_not_reached": [...]}` for report.json."""
    registered = checks_the_marketplace_registers()
    if registered is None:
        return {"checks_registered": None, "checks_not_reached": []}
    ran = {(c["skill"], c["check"]) for c in checks_run}
    return {"checks_registered": len(registered | ran),
            "checks_not_reached": [{"skill": skill, "check": check}
                                   for skill, check in sorted(registered - ran)]}


def _not_reached_sentence(report):
    """The checks the marketplace has and this run never asked, or ""."""
    missing = report.get("checks_not_reached") or []
    if not missing or not report.get("checks_registered"):
        return ""
    names = ", ".join("`{}`".format(item["check"]) for item in missing[:NOT_REACHED_NAMED])
    if len(missing) > NOT_REACHED_NAMED:
        names += " and {} more, listed under `checks_not_reached` in report.json".format(
            len(missing) - NOT_REACHED_NAMED)
    return (" The other {} - {} - {} in branches of {} skills that this site did not reach, "
            "so {} not asked here.".format(
                len(missing), names, "sits" if len(missing) == 1 else "sit",
                "its" if len(missing) == 1 else "their",
                "it was" if len(missing) == 1 else "they were"))


def _appendix_paragraph(report):
    """The arithmetic, rather than a promise that the arithmetic works.

    This paragraph used to end "every check that ran appears in exactly one
    place in this appendix", which is not true and cannot be: a check named by
    a finding appears in the finding, above the appendix. A reader who counted
    the three lists on a hospital site got 67 against a stated 68 and had no
    way to tell whether a check had gone missing or the sentence was loose.
    Printing where all of them went lets them add it up.

    Shared with report.html, which printed "0 checks run; 0 did not apply"
    where report.md printed all four buckets, so the two documents disagreed
    about the same run.
    """
    run_pairs = {(c["skill"], c["check"]) for c in report["checks_run"]}
    named_by_finding = len(run_pairs & named_check_pairs(report["findings"]))
    # The fifth bucket is printed only when it has something in it, which is
    # only on a crawl that read nothing. Printing "and 0 that had nothing to
    # run against" on every healthy report would raise a question about every
    # run in order to answer it about almost none.
    unexecuted = len(report.get("checks_with_nothing_to_run_against") or [])
    # The sixth bucket, printed only when it has something in it for the same
    # reason as the fifth. It is the half of "contributed to a finding above"
    # where no finding above can be shown to be the one - the skill published
    # none, or one of the skill's findings did not survive into this report and
    # nothing records which of its checks fired with that one. Counted
    # separately because a reader went looking for the finding the old sentence
    # promised and there was not one.
    unpublished = len(report.get("checks_that_found_nothing_published") or [])
    ran = len(report["checks_run"])
    skills = _plural(len({c["skill"] for c in report["checks_run"]}), "skill", "skills")
    registered = report.get("checks_registered")
    # "92 checks were run" beside a README saying 96, and nothing to say the
    # two were both right. See `checks_the_marketplace_registers`.
    opening = ("{} of the marketplace's {} checks ran across {}".format(ran, registered, skills)
               if registered and registered >= ran else
               "{} {} run across {}".format(_plural(ran, "check", "checks"),
                                            "was" if ran == 1 else "were", skills))
    return ("{}: {} named by a finding above, {} that did not apply, {} "
            "that ran and found nothing wrong, and {} that contributed to a finding without "
            "being named by one.{}{}{} The audit made {} extra read-only requests beyond the "
            "crawl, used the user agent `{}`, and rendered in `{}` mode.".format(
                opening,
                named_by_finding, len(report["not_applicable"]),
                len(report.get("checks_passed") or []),
                len(report.get("checks_that_found_something") or []),
                "" if not unpublished else
                " A further {} fired, and no finding in this report is filed under {} - the "
                "skill published none, or one of its findings was held back or merged away "
                "and which check belonged to it is not recorded - so {} not counted as "
                "contributing to one.".format(
                    _plural(unpublished, "check", "checks"),
                    "it" if unpublished == 1 else "them",
                    "it is" if unpublished == 1 else "they are"),
                # Not "listed below": this paragraph is also the whole of the
                # HTML appendix, which prints no per-bucket lists, and a
                # sentence pointing at a list that is not there is worse than
                # no sentence.
                # Not "on this crawl": one of the two ways to land in this
                # bucket is a skill stopped by the clock on a crawl that read
                # the site perfectly well.
                "" if not unexecuted else
                " A further {} registered and never reached anything to judge, so {} not "
                "counted as clean.".format(
                    _plural(unexecuted, "check", "checks"),
                    "it is" if unexecuted == 1 else "they are"),
                _not_reached_sentence(report),
                report["crawl"]["extra_requests_made"], report["crawl"]["user_agent"],
                report["crawl"]["render_mode"]))


# "Where we looked" in the body of a report: the first place looked, cut at its
# first clause, and how many more there were. Paragraphs of 150 words of
# method sat under every finding; report.json keeps every word of `checked`.
WHERE_WE_LOOKED_CHARS = 140


def where_we_looked_short(checked):
    """One short sentence for the body of a document, from `checked`."""
    checked = [c for c in checked or [] if c]
    if not checked:
        return ""
    first = re.split(r";\s|\s-\s|, which\b|: ", checked[0], maxsplit=1)[0].strip()
    first = shorten_long_code_lists(first).rstrip(" .")
    if len(first) > WHERE_WE_LOOKED_CHARS:
        # Cut at a word, and said to be cut, rather than mid-word.
        first = first[:WHERE_WE_LOOKED_CHARS].rsplit(" ", 1)[0].rstrip(",;:- ") + "..."
    if len(checked) == 1:
        return ("{}{} That is one source, so this is reported at reduced confidence.".format(
            first, "" if first.endswith("...") else "."))
    return "{}, and {}, all listed in report.json.".format(
        first, plural(len(checked) - 1, "other source", "other sources"))


def _render_finding(finding, demoted=False, steps=None, context=None):
    """One finding as markdown. `steps`: the fix steps as `display_steps` wrote
    them for this document, or None for the finding's own list. `context` is
    shared by every finding one document prints, so a line that would be the
    same under each of them - the mechanism, the language note - is written
    out once and referred to after."""
    action = finding["suggested_action"]
    context = context if context is not None else {}
    out = []
    out.append("### {} — {}".format(finding["id"], finding["title"]))
    # The letter alone is an unexplained code, and this document is written for
    # a marketing manager. The expansion lives in report.json and in a
    # reference file, neither of which the person reading report.md has.
    #
    # In the lower tier the severity has to travel with the finding. Everywhere
    # else the heading above it says "High findings"; under "Worth checking
    # first" there is no such heading, and a reader who could not see that a
    # demoted item is high severity cannot judge whether confirming it is worth
    # an afternoon.
    # The name that survives a re-run, on the same line as the priority and
    # the confidence. Beside the F-number rather than in place of it: the
    # number is what a reader says out loud and the name is what two audits
    # are compared on, and printing only one of them is how F-014 came to mean
    # two different problems to anyone running the same site twice.
    name = " · `{}`".format(finding["stable_id"]) if finding.get("stable_id") else ""
    also = (" and " + ", ".join(finding["also_detected_by"])
            if finding.get("also_detected_by") else "")
    # "Check this one against the site before acting on it" was printed under
    # every entry of a section whose heading and blurb already say exactly that.
    if demoted:
        meta = ("**{} severity if confirmed** · priority {} · confidence {} · found by "
                "{}{}{}".format(finding["severity"].capitalize(), action["priority"],
                                finding["confidence"], finding["detected_by"], also, name))
    else:
        meta = "**Priority: {}** · confidence {} · found by {}{}{}".format(
            action["priority"], finding["confidence"], finding["detected_by"], also, name)
    # A consequence of another finding, in four lines. Its cause carries the
    # fix; what is left here is what it measured, which finding it follows
    # from, and what to do if it outlives that fix - every step still printed,
    # in one paragraph. The reasoning and the sources are in report.json.
    # Five findings about one museum page pair each printed a full block.
    deferral = deferral_note(finding)
    if deferral:
        out.append("- {}".format(meta))
        out.append("- **What we found.** {}".format(
            shorten_long_code_lists(finding["evidence"])))
        out.append("- **This is the same defect as {}.** {}".format(
            finding["follows_from"]["id"], deferral))
        out.append("- **What to do if it is still here afterwards.** {} Who does it: {}. "
                   "Roughly: {}. {}".format(
                       action["summary"], action["owner"],
                       effort_time(action["effort"], action["owner"]),
                       " ".join(action["how_to_fix"])).rstrip())
        follows = finding["follows_from"]
        listed = _affected_pages_lines(finding)
        if listed and (follows.get("page_count") or 0) > len(follows.get("pages") or []):
            out.append("- {}".format(listed[-1]))
        out.append("")
        return "\n".join(out)
    out.append(meta)
    out.append("")
    # Once per mechanism per document. Seven mechanisms, one sentence each, and
    # a museum report printed them fifteen times.
    seen_mechanisms = context.setdefault("mechanisms", set())
    if finding["mechanism"] not in seen_mechanisms:
        seen_mechanisms.add(finding["mechanism"])
        out.append("*Why this class of problem matters: {}.*".format(
            MECHANISMS[finding["mechanism"]].rstrip(".")))
        out.append("")
    out.append("**What we found.** {}".format(shorten_long_code_lists(finding["evidence"])))
    out.append("")
    # Directly under the claim it qualifies, and not at the foot of the block:
    # a reader who has already read the evidence as a fact has taken it as one.
    dispute = dispute_note(finding)
    if dispute:
        out.append("> **The report disagrees with itself here.** {}".format(dispute))
        out.append("")
    # Under the evidence, above the fix, because it is the order to do the two
    # in: a reader who has read the fix has already started costing it.
    partial = partial_deferral_note(finding)
    if partial:
        out.append("> **This is partly the same defect as {}.** {}".format(
            finding["partly_follows_from"]["id"], partial))
        out.append("")
    # A finding that says something is missing states where it looked. The
    # reader can then judge the claim instead of taking it, and can tell us
    # when we looked in the wrong place - which is how every one of this
    # audit's own false positives was eventually caught. One sentence here and
    # the whole list in report.json: see `where_we_looked_short`.
    looked = []
    if finding.get("checked"):
        looked.append("**Where we looked.** {}".format(
            where_we_looked_short(finding["checked"])))
    # Directly under where we looked, because it is a limit on that looking:
    # the places were read in a language, and on a site this audit did not
    # establish as English the word patterns behind the claim were written for
    # a different one. Absent on every English site and on every finding that
    # read no prose. One report gives one answer about the language, so the
    # sentence is written out once and pointed at after.
    if finding.get("read_in"):
        first = context.setdefault("read_in", {})
        if finding["read_in"] in first:
            looked.append("**What language this was read in.** The same as under {} "
                          "above.".format(first[finding["read_in"]]))
        else:
            first[finding["read_in"]] = finding["id"]
            looked.append("**What language this was read in.** {}".format(
                finding["read_in"]))
    if looked:
        # Two lines of one paragraph: both are about how the claim was read.
        out.extend(looked)
        out.append("")
    out.append("**Why it matters.** {}".format(action["rationale"]))
    out.append("")
    # The steps follow the line that introduces them, as a list under it.
    out.append("**What to do.** {} Who does it: {}. Roughly: {}.".format(
        action["summary"], action["owner"], effort_time(action["effort"], action["owner"])))
    for step in (action["how_to_fix"] if steps is None else steps):
        out.append("- {}".format(step))
    if action.get("snippet"):
        out.append("")
        if action.get("snippet_warning"):
            out.append("> **{}**".format(action["snippet_warning"]))
            out.append("")
        # The snippet is pre-filled with values found on the site, and on a
        # staging or local origin that means a non-public URL inside code
        # labelled paste-ready. Somebody pastes it into a live template and
        # publishes a localhost logo path, so say so above the block.
        if _non_public_host(action["snippet"]):
            out.append("> **{}**".format(NON_PUBLIC_HOST_WARNING))
            out.append("")
        out.append("```")
        out.append(action["snippet"])
        out.append("```")
    out.extend(_affected_pages_lines(finding))
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Written once, referred to after
#
# A report for a marketing reader listed sixteen JavaScript framework variable
# names in one evidence line, and printed the same 450-character paragraph -
# "If you do not know who edits your pages..." - under most of its findings.
# Neither was wrong and both were in the way. report.json keeps every word, so
# the two documents a person reads shorten what repeats and point at where it
# is written out.
# --------------------------------------------------------------------------

# A comma-separated run of code spans this long or longer is a list of names,
# and three of them are enough to show what kind of list it is.
CODE_LIST_SHOWN = 3
CODE_LIST_MIN = 7
_CODE_LIST_RE = re.compile(r"`[^`\n]+`(?:, `[^`\n]+`){{{},}}".format(CODE_LIST_MIN - 1))


def shorten_long_code_lists(text):
    """`text` with every long list of code names cut to three and a count."""
    def cut(match):
        names = re.findall(r"`[^`\n]+`", match.group(0))
        return "{} and {} more, all named in report.json".format(
            ", ".join(names[:CODE_LIST_SHOWN]), len(names) - CODE_LIST_SHOWN)
    return _CODE_LIST_RE.sub(cut, str(text or ""))


# A fix step at least this long, printed word for word under two findings or
# more, is boilerplate: the full text is printed once and referred to after.
REPEATED_STEP_MIN = 160


def _findings_in_print_order(graded, demoted):
    """The findings in the order both documents print them: the confident tier
    by severity band, then priority score, then id; then the lower tier."""
    ordered = []
    for key, _, _ in SEVERITY_HEADINGS:
        ordered += sorted((f for f in graded if f["severity"] == key),
                          key=lambda f: (-f["suggested_action"]["priority_score"], f["id"]))
    return ordered + list(demoted)


# A sentence at least this long, printed word for word inside the steps of two
# findings or more, is boilerplate even where the step around it differs: the
# platform note "This audit could not tell what this site is built with..."
# sat inside four differently worded steps of one report, so the step-level
# plan above never saw it repeat.
REPEATED_SENTENCE_MIN = 80
_STEP_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`(])")


def repeated_steps_plan(ordered):
    """`{step text: id of the first finding printing it}`, for long repeated steps.

    `ordered` is the findings in the order the document prints them, so "above"
    in the reference is true. Long repeated sentences are in the same plan
    under `("sentence", text)`. A finding that is another one's defect read
    again prints no steps, so it is never where a step is first written out.
    """
    seen, first = collections.Counter(), {}
    sentence_seen, sentence_first = collections.Counter(), {}
    for finding in ordered:
        if finding.get("follows_from"):
            continue
        sentences_here = set()
        for step in dict.fromkeys(finding["suggested_action"]["how_to_fix"]):
            if len(step) >= REPEATED_STEP_MIN:
                seen[step] += 1
                first.setdefault(step, finding["id"])
            for sentence in _STEP_SENTENCE_RE.split(step):
                if len(sentence) >= REPEATED_SENTENCE_MIN:
                    sentences_here.add(sentence)
        for sentence in sentences_here:
            sentence_seen[sentence] += 1
            sentence_first.setdefault(sentence, finding["id"])
    plan = {step: first[step] for step, count in seen.items() if count > 1}
    plan.update({("sentence", sentence): sentence_first[sentence]
                 for sentence, count in sentence_seen.items() if count > 1})
    return plan


def _lead_of(text):
    """A step's or a sentence's own opening clause, so the reader knows which
    paragraph is meant without reading it again."""
    return re.split(r"[,:;.]\s|\s-\s", text, maxsplit=1)[0].strip()


def display_steps(finding, plan):
    """The fix steps as a document prints them: a repeat becomes a reference."""
    out = []
    for step in finding["suggested_action"]["how_to_fix"]:
        sentences = _STEP_SENTENCE_RE.split(step)
        where = plan.get(step)
        if where is not None and where != finding["id"]:
            # Pointed at where the words are actually printed: the first
            # finding carrying any of its long sentences, which is earlier than
            # the first carrying the whole step wherever the two differ.
            where = next((plan[("sentence", s)] for s in sentences
                          if ("sentence", s) in plan), where)
            out.append("{} - written out in full under {} above.".format(_lead_of(step), where))
            continue
        kept, pointing_at = [], None
        for sentence in sentences:
            origin = plan.get(("sentence", sentence))
            if origin is not None and origin != finding["id"]:
                if pointing_at != origin:
                    kept.append("{} - written out in full under {} above.".format(
                        _lead_of(sentence), origin))
                    pointing_at = origin
                continue
            pointing_at = None
            kept.append(sentence)
        out.append(" ".join(kept))
    return out


# The longest reason a decline prints in the appendix. Its first sentence says
# what the check found to decline on; the measurements after it are in
# report.json, and printed for sixty checks they were a third of one report.
DECLINE_REASON_CHARS = 120


def first_sentence_of_a_reason(reason):
    """A decline's reason as the appendix prints it: its first sentence."""
    text = (reason or "").strip()
    first = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text, maxsplit=1)[0]
    if len(first) > DECLINE_REASON_CHARS:
        first = truncate(first, DECLINE_REASON_CHARS)
    if first == text:
        return text
    return "{} (the rest is in report.json)".format(first.rstrip("."))


def grouped_declines(declines):
    """`[(checks, skill, reason)]`, one entry per distinct reason within a skill.

    A crawl that could not open one TLS connection declined twenty checks with
    one identical sentence, and the appendix printed it twenty times. The
    checks are all named, once each; the sentence is printed once.
    """
    groups = collections.OrderedDict()
    for item in declines:
        key = (item.get("skill") or "", item.get("reason") or "")
        groups.setdefault(key, []).append(item["check"])
    return [(checks, skill, reason) for (skill, reason), checks in groups.items()]


def grouped_unanswered(items):
    """`[(titles, reason)]`, one entry per distinct reason, in first-seen order.

    The same move as `grouped_declines`, for the unanswered questions: a crawl
    that read nothing gave thirty of them one identical reason, and a report
    with one finding ran to 270 lines.
    """
    groups = collections.OrderedDict()
    for item in items:
        groups.setdefault(item.get("reason") or "", []).append(item["title"])
    return [(titles, reason) for reason, titles in groups.items()]


def _names_in_prose(names, wrap="**{}**"):
    names = [wrap.format(n) for n in names]
    return names[0] if len(names) == 1 else "{} and {}".format(", ".join(names[:-1]), names[-1])


# How many affected pages a finding prints. The heading promised five and the
# loop printed the whole list, so a site-wide finding put sixty URLs into a
# report that had just told the reader it was showing five.
AFFECTED_PAGES_SHOWN = 5


def _affected_pages_lines(finding):
    """The per-page list, with a heading that matches what is printed below it."""
    pages = finding.get("affected_pages") or []
    if not pages:
        return []
    shown = pages[:AFFECTED_PAGES_SHOWN]
    total = finding.get("affected_page_count") or len(pages)
    if total > len(shown):
        # "first 5 shown" does not tell a reader which five, so a reader
        # who checks one of them cannot tell whether they checked a
        # representative page or the one this audit thought worst. They are
        # `sorted(set(...))[:5]` in `make_finding`, so say that.
        heading = ("Affected pages ({} in total; the {} listed are the first in alphabetical "
                   "order, not a ranking):".format(total, len(shown)))
    else:
        heading = "Affected pages:"
    # Not again where the evidence has just named every one of them: under
    # most findings of one report the list repeated its examples word for
    # word. Then only the count is added, where there are more than it shows.
    evidence = finding.get("evidence") or ""
    if all(url in evidence for url in shown):
        if total <= len(shown):
            return []
        # Which five still has to be said: the evidence names them as
        # examples, and a reader cannot tell from that whether they are the
        # worst five or the first five.
        return ["", "Affected pages: {} in total; the {} listed are the first in alphabetical "
                    "order, not a ranking, and each is named above.".format(total, len(shown))]
    # One line, the addresses separated, rather than a bullet each: five
    # bullets under every finding were a sixth of one report's length.
    return ["", "{} {}".format(heading, " · ".join(shown))]


def _escape_cell(text):
    """One table cell, safe to interpolate into a markdown row.

    A pipe splits the row into extra cells; a newline ends the row outright;
    and a markdown renderer passes raw HTML straight through, so a sentence
    lifted off a page that contains a tag renders as that tag. All three are
    site-derived text, so all three are neutralised here rather than at each
    of the three call sites.

    `&` is deliberately left alone: `&lt;` already renders as a literal `<`
    rather than as markup, and escaping the ampersand as well would turn every
    query string in the URL column into `?a=1&amp;b=2` for a reader who opens
    report.md as plain text.
    """
    cell = str(text or "")
    cell = cell.replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|")
    return re.sub(r"\s+", " ", cell).strip()


# --------------------------------------------------------------------------
# Optional HTML rendering
# --------------------------------------------------------------------------

SEVERITY_COLOURS = {"critical": "#b00020", "high": "#c2410c", "medium": "#a16207",
                    "low": "#3f6212", "info": "#334155"}


def render_html(report):
    def esc(value):
        # Decoded before it is escaped, or the two documents disagree about
        # the same brand: report.md printed "A machine can reach
        # Ash&amp;amp;Loom" and report.html escaped that string again into
        # `Ash&amp;amp;amp;Loom`, which a browser renders as `Ash&amp;Loom`.
        # One decode, at the one boundary every value crosses.
        #
        # Isolated last, and for the same reason: `report.md` isolates every
        # right-to-left quotation at its own render boundary, and a browser
        # displaces the punctuation around an Arabic or Hebrew quotation
        # exactly as a terminal does. Two documents, one behaviour.
        return isolate_right_to_left_runs(
            html.escape(decode_character_references(value)))

    def esc_code(value):
        # A snippet is not prose. `&lt;category&gt;` inside a paste-ready block
        # is the placeholder the reader is meant to replace, and decoding it
        # would hand them a block with a bare `<category>` element in it.
        return html.escape(str(value or ""))

    parts = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
             "<title>AI readiness audit: {}</title>".format(esc(report["site"])),
             "<style>",
             "body{font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif;max-width:52rem;",
             "margin:2rem auto;padding:0 1rem;color:#111;background:#fff}",
             "h1,h2,h3{line-height:1.25} code,pre{font-family:ui-monospace,Menlo,Consolas,monospace}",
             "pre{background:#f6f7f9;padding:.75rem;overflow-x:auto;border-radius:6px;font-size:.85em}",
             ".badge{display:inline-block;padding:.1rem .5rem;border-radius:999px;color:#fff;",
             "font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.03em}",
             ".finding{border:1px solid #e5e7eb;border-radius:8px;padding:1rem 1.25rem;margin:1rem 0}",
             # The lower tier has to look different, not only be labelled
             # differently: a reader skimming headings should be able to see
             # where the firm findings stop.
             ".unsure{border-style:dashed;background:#fbfaf7}",
             ".caveat{border-left:4px solid #b00020;padding:.25rem 0 .25rem 1rem;margin:1rem 0}",
             # The one-page version has to look like a cover sheet rather than
             # like the first of nineteen sections, or a reader scrolling past
             # it never learns it was the part they could have stopped at.
             ".topsheet{border:2px solid #111;border-radius:8px;padding:.25rem 1.25rem 1rem;",
             "margin:1rem 0}.topsheet li{margin:.5rem 0}",
             # A disagreement between two findings is the one thing in a
             # finding block a reader must not skim past.
             ".dispute{border-left:4px solid #a16207;padding:.25rem 0 .25rem 1rem}",
             ".meta{color:#555;font-size:.85rem} table{border-collapse:collapse;width:100%}",
             "td,th{border-bottom:1px solid #e5e7eb;padding:.4rem .5rem;text-align:left;font-size:.9rem}",
             "@media (prefers-color-scheme:dark){body{background:#0b0e14;color:#e6e6e6}",
             "pre{background:#161b22}.finding{border-color:#2a2f3a}td,th{border-color:#2a2f3a}",
             ".unsure{background:#14161d}.topsheet{border-color:#e6e6e6}",
             ".meta{color:#9aa4b2}}",
             "</style></head><body>"]
    parts.append("<h1>AI readiness audit: {}</h1>".format(esc(report["site"])))
    # report.md pluralises this line and report.html did not, so the same run
    # produced "1 page crawled · 1 finding" in one document and "1 pages
    # crawled · 1 findings" in the other.
    parts.append('<p class="meta">Audited {} · {} · {} · {}{}</p>'.format(
        esc(report["audited_at"]),
        esc(_pages_line(report)),
        esc(_plural(report["summary"]["total_findings"], "finding", "findings")),
        esc(_plural(len(report["recommendations"]), "recommendation", "recommendations")),
        esc(_asked_about(report))))
    # Same five entries, same order, same place as report.md, from the one
    # function that builds them.
    parts.append('<div class="topsheet"><h2>{}</h2><p class="meta">{}</p><ul>'.format(
        esc(TOP_SHEET_HEADING), esc(TOP_SHEET_BLURB)))
    for label, sentence, items in top_sheet(report):
        parts.append("<li><strong>{}.</strong> {}".format(esc(label), esc(sentence)))
        if items:
            parts.append("<ol>")
            for item in items:
                parts.append("<li>{}</li>".format(esc(item)))
            parts.append("</ol>")
        parts.append("</li>")
    parts.append("</ul></div>")
    parts.append("<p>{}</p>".format(esc(report["summary"]["verdict"])))
    # Same sentence, same place as report.md. The two documents disagreeing
    # about who the reader needs would be the same failure as them disagreeing
    # about how much was read.
    who = _who_you_will_need(report)
    if who:
        parts.append("<p>{}</p>".format(esc(who)))

    # Same bullets as report.md, in the same place and before anything that
    # counts findings. The two documents disagreeing about how much of the site
    # was read is how one of them ends up reassuring.
    not_serving = _not_serving_caveat(report)
    if not_serving:
        heading, not_serving_lines = not_serving
        parts.append('<div class="caveat"><h2>{}</h2><ul>'.format(esc(heading)))
        for line in not_serving_lines:
            parts.append("<li>{}</li>".format(esc(line)))
        parts.append("</ul></div>")
    origin = _origin_caveat(report)
    if origin:
        heading, origin_lines = origin
        parts.append('<div class="caveat"><h2>{}</h2><ul>'.format(esc(heading)))
        for line in origin_lines:
            parts.append("<li>{}</li>".format(esc(line)))
        parts.append("</ul></div>")
    caveat = _coverage_caveat_lines(report)
    if caveat:
        parts.append('<div class="caveat"><h2>{}</h2><ul>'.format(
            esc(COVERAGE_CAVEAT_HEADING)))
        for line in caveat:
            parts.append("<li>{}</li>".format(esc(line)))
        parts.append("</ul></div>")
    dropped = _skills_that_did_not_run_lines(report)
    if dropped:
        parts.append('<div class="caveat"><h2>{}</h2><ul>'.format(
            esc(DROPPED_SKILL_HEADING)))
        for line in dropped:
            parts.append("<li>{}</li>".format(esc(line)))
        parts.append("</ul></div>")

    by_id = {f["id"]: f for f in report["findings"]}
    demoted_ids = set(report.get("worth_checking") or [])
    demoted = [by_id[i] for i in (report.get("worth_checking") or []) if i in by_id]
    graded = [f for f in report["findings"] if f["id"] not in demoted_ids]
    if report["start_here"]:
        parts.append("<h2>Start here</h2><ol>")
        for finding_id in report["start_here"]:
            finding = by_id[finding_id]
            parts.append("<li><strong>{}</strong> — {}</li>".format(
                esc(finding["title"]), esc(finding["suggested_action"]["summary"])))
        parts.append("</ol>")
    elif demoted:
        # An absent "Start here" on a report full of findings reads as a
        # missing section rather than as a deliberate refusal to rank.
        parts.append("<h2>Start here</h2><p>Nothing here reached the bar for a first "
                     "instruction: every finding below rests on a reading the audit is not "
                     "certain of, so they are listed under &ldquo;{}&rdquo; for you to "
                     "confirm rather than ranked as work to start on.</p>".format(
                         esc(WORTH_CHECKING_HEADING)))

    if demoted:
        table_rows, follows_rows = severity_table(report)
        parts.append("<table><tr><th>Severity</th><th>Reported with confidence</th>"
                     "<th>Worth checking</th></tr>")
        for label, confident_count, worth_count in table_rows:
            parts.append("<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                esc(label), confident_count, worth_count))
        # The same row report.md prints, so the two tables add up alike.
        for cause, ids, confident_count, worth_count in follows_rows:
            parts.append("<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                esc(_follows_row_label(cause, ids)), confident_count, worth_count))
        parts.append("</table>")

    plan = repeated_steps_plan(_findings_in_print_order(graded, demoted))
    # The same record of what has been written out once as report.md keeps.
    printed = {"mechanisms": set(), "read_in": {}}

    def render_one(finding, unsure=False):
        action = finding["suggested_action"]
        key = finding["severity"]
        parts.append('<div class="finding{}">'.format(" unsure" if unsure else ""))
        parts.append('<h3><span class="badge" style="background:{}">{}</span> {} — {}</h3>'.format(
            SEVERITY_COLOURS[key], esc(key), esc(finding["id"]), esc(finding["title"])))
        # Same two labels as report.md, in the same order. An HTML reader
        # comparing two audits had only the F-number, which is the one that
        # moves.
        if finding.get("stable_id"):
            parts.append('<p class="meta"><code>{}</code></p>'.format(
                esc(finding["stable_id"])))
        if unsure:
            parts.append('<p class="meta">{} severity if confirmed · priority {} · confidence '
                         '{} · {} · {}</p>'.format(
                             esc(key.capitalize()), esc(action["priority"]),
                             esc(finding["confidence"]), esc(action["owner"]),
                             esc(effort_time(action["effort"], action["owner"]))))
        else:
            parts.append('<p class="meta">Priority: {} · confidence {} · {} · {}</p>'.format(
                esc(action["priority"]), esc(finding["confidence"]),
                esc(action["owner"]), esc(effort_time(action["effort"], action["owner"]))))
        if finding["mechanism"] not in printed["mechanisms"]:
            printed["mechanisms"].add(finding["mechanism"])
            parts.append('<p class="meta"><em>Why this class of problem matters: {}.</em></p>'
                         .format(esc(MECHANISMS[finding["mechanism"]].rstrip("."))))
        parts.append("<p><strong>What we found.</strong> {}</p>".format(
            esc(shorten_long_code_lists(finding["evidence"]))))
        # Same sentence, same place as report.md, from the one function that
        # writes it. Two documents disagreeing about which two findings
        # disagree would be the funniest failure this file could ship.
        dispute = dispute_note(finding)
        if dispute:
            parts.append('<p class="dispute"><strong>The report disagrees with itself '
                         'here.</strong> {}</p>'.format(esc(dispute)))
        # Same sentence, same place as report.md, from the one function that
        # writes it.
        deferral = deferral_note(finding)
        if deferral:
            parts.append('<p class="dispute"><strong>This is the same defect as {}.</strong> '
                         '{}</p>'.format(esc(finding["follows_from"]["id"]), esc(deferral)))
            # The same short block report.md prints for a consequence.
            parts.append("<p><strong>What to do if it is still here afterwards.</strong> {} "
                         "Who does it: {}. Roughly: {}. {}</p></div>".format(
                             esc(action["summary"]), esc(action["owner"]),
                             esc(effort_time(action["effort"], action["owner"])),
                             esc(" ".join(action["how_to_fix"]))))
            return
        partial = partial_deferral_note(finding)
        if partial:
            parts.append('<p class="dispute"><strong>This is partly the same defect as '
                         '{}.</strong> {}</p>'.format(
                             esc(finding["partly_follows_from"]["id"]), esc(partial)))
        if finding.get("checked"):
            parts.append("<p><strong>Where we looked.</strong> {}</p>".format(
                esc(where_we_looked_short(finding["checked"]))))
        # Same field, same place as report.md. Printed from the finding rather
        # than re-derived, so the two documents cannot disagree about what
        # language one claim was read in.
        if finding.get("read_in"):
            if finding["read_in"] in printed["read_in"]:
                parts.append("<p><strong>What language this was read in.</strong> The same "
                             "as under {} above.</p>".format(
                                 esc(printed["read_in"][finding["read_in"]])))
            else:
                printed["read_in"][finding["read_in"]] = finding["id"]
                parts.append("<p><strong>What language this was read in.</strong> {}</p>".format(
                    esc(finding["read_in"])))
        parts.append("<p><strong>Why it matters.</strong> {}</p>".format(esc(action["rationale"])))
        parts.append("<p><strong>What to do.</strong> {}</p><ul>".format(esc(action["summary"])))
        for step in display_steps(finding, plan):
            parts.append("<li>{}</li>".format(esc(step)))
        parts.append("</ul>")
        if action.get("snippet"):
            # Both warnings were printed by report.md and by neither by
            # report.html, so an HTML reader was handed a paste-ready block
            # containing `http://localhost:8000/` and invented values with
            # nothing above it saying either thing.
            if action.get("snippet_warning"):
                parts.append("<p><strong>{}</strong></p>".format(
                    esc(action["snippet_warning"])))
            if _non_public_host(action["snippet"]):
                parts.append("<p><strong>{}</strong></p>".format(
                    esc(NON_PUBLIC_HOST_WARNING)))
            parts.append("<pre><code>{}</code></pre>".format(esc_code(action["snippet"])))
        parts.append("</div>")

    explained_the_ids = False
    for key, label, _ in SEVERITY_HEADINGS:
        group = [f for f in graded if f["severity"] == key]
        if not group:
            continue
        parts.append("<h2>{} findings</h2>".format(esc(label)))
        if not explained_the_ids:
            parts.append('<p class="meta">{}</p>'.format(esc(ID_NOTE)))
            explained_the_ids = True
        # The same order as report.md, so "written out in full under F-003
        # above" names the same finding in both documents.
        for finding in sorted(group, key=lambda f: (-f["suggested_action"]["priority_score"],
                                                    f["id"])):
            render_one(finding)

    if demoted:
        parts.append("<h2>{}</h2><p>{}</p>".format(
            esc(WORTH_CHECKING_HEADING), esc(WORTH_CHECKING_BLURB)))
        if not explained_the_ids:
            parts.append('<p class="meta">{}</p>'.format(esc(ID_NOTE)))
            explained_the_ids = True
        for finding in demoted:
            render_one(finding, unsure=True)

    if report["recommendations"]:
        parts.append("<h2>Beyond the defects</h2>")
        for rec in report["recommendations"]:
            parts.append('<div class="finding"><h3>{}</h3><p>{}</p>'.format(
                esc(rec["title"]), esc(rec["summary"])))
            parts.append("<p><strong>Why this works.</strong> {} — {}</p><ul>".format(
                esc(MECHANISMS[rec["mechanism"]].rstrip(".")), esc(rec["why_this_works"])))
            for step in rec["how_to_do_it"]:
                parts.append("<li>{}</li>".format(esc(step)))
            parts.append("</ul>")
            if rec.get("snippet"):
                if rec.get("snippet_warning"):
                    parts.append("<p><strong>{}</strong></p>".format(esc(rec["snippet_warning"])))
                if _non_public_host(rec["snippet"]):
                    parts.append("<p><strong>{}</strong></p>".format(esc(NON_PUBLIC_HOST_WARNING)))
                parts.append("<pre><code>{}</code></pre>".format(esc_code(rec["snippet"])))
            parts.append("</div>")

    # report.md carries this section and report.html dropped it, so an HTML
    # reader was shown a short report with no way to tell that the crawler had
    # been refused most of the site and that whole checks therefore have
    # nothing to say either way.
    if report.get("unverifiable"):
        parts.append("<h2>{}</h2>".format(esc(UNANSWERED_HEADING)))
        # One wording, printed by both documents. They used to carry different
        # sentences here, and only one of them was still true once a whole
        # skill could be missing as well as a crawl being thin.
        parts.append("<p>{}</p><ul>".format(esc(UNANSWERED_BLURB)))
        for titles, reason in grouped_unanswered(report["unverifiable"]):
            parts.append("<li>{} - {}.</li>".format(
                _names_in_prose([esc(t) for t in titles], "<strong>{}</strong>"), esc(reason)))
        parts.append("</ul>")

    # "0 checks run; 0 did not apply" where report.md printed the real
    # four-bucket paragraph. Same sentence in both documents now.
    parts.append("<h2>Appendix: what was checked</h2>")
    parts.append('<p class="meta">{}</p>'.format(esc(_appendix_paragraph(report))))
    # Same sentence, same place as report.md. A report that says one thing
    # about its own repeatability in one document and another in the other has
    # made the problem worse rather than better.
    parts.append('<p class="meta">{}</p>'.format(esc(repeatability_note(report))))
    parts.append("</body></html>")
    return "".join(parts)


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--findings", nargs="+", required=True)
    parser.add_argument("--out-json", default="report.json")
    parser.add_argument("--out-md", default="report.md")
    parser.add_argument("--out-html", help="also write an HTML report to this path")
    parser.add_argument("--audited-at", help="override the report timestamp (used by tests)")
    args = parser.parse_args(argv)

    snapshot = load_snapshot(args.snapshot)
    results = load_skill_results(args.findings)
    report = compose(snapshot, results, audited_at=args.audited_at)

    write_json(args.out_json, report)
    with open(args.out_md, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    if args.out_html:
        with open(args.out_html, "w", encoding="utf-8") as handle:
            handle.write(render_html(report))

    summary = report["summary"]
    print("composed {} finding(s) [{} critical, {} high, {} medium, {} low, {} info] and {} "
          "recommendation(s) -> {}".format(
              summary["total_findings"], summary["critical"], summary["high"],
              summary["medium"], summary["low"], summary["info"],
              len(report["recommendations"]), args.out_json), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
