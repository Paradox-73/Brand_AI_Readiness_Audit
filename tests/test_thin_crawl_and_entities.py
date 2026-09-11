# -*- coding: utf-8 -*-
"""Three defects an independent review found in one frozen build's reports.

**A count of what was read is not a claim about the site.** The review ran the
build against a grocery site where 45 of its 59 URLs answered HTTP 429, so 13
pages were read. The report then withheld five findings, every one of them
true of those 13 pages - "47 images have no description at all", "No
Organization or LocalBusiness markup anywhere on the site", "13 pages do not
declare a language", "3 of 3 article pages have no Article markup", "Heading
structure does not describe the page reliably" - while publishing
`title-and-description` from the same 13 pages. The diagnosis: the gate
"keys on crawl coverage instead of on whether a finding is a presence
observation (safe to report) or an absence inference (not safe)". These tests
hold both ends: the counts come back, and the claim a thin crawl really does
invalidate - a fact that has to appear once anywhere, so the page nobody read
may be the page that states it - is still held back.

**Two sentences about one set have to agree.** From the same report: "reconcile
`0 that ran and found nothing wrong` with the section header two lines later
that says `These were run and found nothing to report`".

**A character reference is not a brand name.** Report prose read "A machine can
reach Ash&amp;amp;Loom and read it" - a homeware brand whose name carries an
ampersand, double-escaped in the site's own `og:site_name`. Markdown decodes no
character reference, so that is the string the reader saw.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_thin_crawl_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose()

HOST = "https://an-invented-grocer.test"
READ_TEXT = "A sentence long enough that this page counts as one the crawl read."


def _page(url, **fields):
    page = {"url": url, "final_url": url, "status": 200, "title": "A page",
            "page_type": "content", "body_text": READ_TEXT, "paragraphs": [READ_TEXT]}
    page.update(fields)
    return page


def _refused(url):
    """One of the 45 URLs that answered 429."""
    return {"url": url, "final_url": url, "status": 429, "title": "",
            "page_type": "content", "body_text": "", "paragraphs": []}


def _thin_crawl(read=13, refused=46):
    """The grocery site's crawl: a few pages read, most of the URLs refused."""
    pages = [_page("{}/read{}".format(HOST, i)) for i in range(read)]
    pages += [_refused("{}/refused{}".format(HOST, i)) for i in range(refused)]
    return {
        "site": "an-invented-grocer.test",
        "origin": HOST,
        "brand": {"name": "Kelvin Grocers", "host": "an-invented-grocer.test"},
        "pages": pages,
        "sitemaps": [],
        "crawl": {"pages_crawled": len(pages), "pages_ok": read, "render_mode": "static",
                  "user_agent": "test-agent", "elapsed_s": 1.0, "notes": []},
    }


def _read_urls(snapshot, count):
    return ["{}/read{}".format(HOST, i) for i in range(count)]


def _finding(title, root_cause="alt-missing", pages=(), evidence="Read off those pages."):
    return {
        "id_hint": re.sub(r"[^a-z]+", "-", title.lower()).strip("-")[:40],
        "title": title,
        "severity": "medium", "confidence": "high",
        "evidence": evidence,
        "mechanism": "C", "root_cause": root_cause,
        "checked": ["one place", "a second place"],
        "affected_pages": list(pages),
        "affected_page_count": len(pages),
        "suggested_action": {"summary": "Do the thing.", "effort": "low",
                             "owner": "developer", "how_to_fix": ["Step one."],
                             "rationale": "Because a machine reads it."},
    }


def _skill_result(*findings, **kwargs):
    return {
        "skill": kwargs.get("skill", "structured-data-audit"),
        "findings": list(findings),
        "signals": kwargs.get("signals", {}),
        "checks_run": kwargs.get("checks_run", []),
        "not_applicable": kwargs.get("not_applicable", []),
        "fired_checks": [],
        "extra_requests_made": 0,
    }


# --------------------------------------------------------------------------
# 1. What a thin crawl does and does not invalidate
# --------------------------------------------------------------------------

THE_FIVE_NAMED = (
    ("47 images have no description at all", "alt-missing"),
    ("No Organization or LocalBusiness markup anywhere on the site", "no-org-schema"),
    ("13 pages do not declare a language", "missing-lang"),
    ("3 of 3 article pages have no Article markup", "no-article-schema"),
    ("Heading structure does not describe the page reliably", "heading-structure"),
)


def test_the_five_named_counts_are_published():
    """Each was true of the 13 pages that were read, and each was withheld."""
    snapshot = _thin_crawl()
    findings = [_finding(title, cause, pages=_read_urls(snapshot, 3))
                for title, cause in THE_FIVE_NAMED]
    kept, held = compose.withhold_absence_claims(findings, snapshot)
    assert held == [], [item["title"] for item in held]
    assert len(kept) == len(THE_FIVE_NAMED)


def test_a_published_count_names_the_read_population_and_the_unread_number():
    """"Restated" means the reader can see what the figure covers without
    going to the crawl table: the denominator is the pages that were read and
    the pages that were not are counted beside it."""
    snapshot = _thin_crawl()
    kept, _ = compose.withhold_absence_claims(
        [_finding("13 pages do not declare a language", "missing-lang",
                  pages=_read_urls(snapshot, 3))], snapshot)
    evidence = kept[0]["evidence"]
    assert "13 pages this crawl read" in evidence
    assert "46 of the 59 URLs" in evidence


def test_a_whole_site_scope_is_cut_back_to_the_pages_that_were_read():
    """The title is the claim, and a heading saying "anywhere on the site" over
    a crawl that read 13 of 59 URLs is the sentence the gate exists to stop."""
    snapshot = _thin_crawl()
    kept, _ = compose.withhold_absence_claims(
        [_finding("No Organization or LocalBusiness markup anywhere on the site",
                  "no-org-schema", pages=_read_urls(snapshot, 3))], snapshot)
    assert kept[0]["title"] == (
        "No Organization or LocalBusiness markup on any of the 13 pages read")


def test_a_fact_that_only_has_to_appear_once_is_still_held_back():
    """The opposite failure, measured on an earlier build: absence claims
    published from four pages of sixty. A founding year has to be stated once,
    anywhere, so the about page that states it may be among the 46 URLs that
    came back 429 - and no restatement over the read pages is that defect."""
    snapshot = _thin_crawl()
    _, held = compose.withhold_absence_claims(
        [_finding("The site never states its founding facts in plain text",
                  "missing-core-fact", pages=_read_urls(snapshot, 3))], snapshot)
    assert len(held) == 1
    assert "rather than about the site" in held[0]["reason"]


def test_a_claim_naming_no_page_that_was_read_has_nothing_to_restate():
    """A finding attached to pages that all came back 429 counted nothing over
    the sample, whatever its wording."""
    snapshot = _thin_crawl()
    _, held = compose.withhold_absence_claims(
        [_finding("No FAQ markup anywhere on the site", "no-faq-schema",
                  pages=["{}/refused0".format(HOST)])], snapshot)
    assert len(held) == 1


def test_a_count_taken_over_pages_that_were_not_read_is_not_a_count_of_what_was_read():
    """Every page the finding names, not any of them. A site delivering its
    text with JavaScript answers 200 everywhere and hands this crawler a few
    characters per page: the identity and wayfinding checks then count over
    pages nobody read, and restating that count over the ones who were read
    would print a number never taken over them."""
    snapshot = _thin_crawl()
    _, held = compose.withhold_absence_claims(
        [_finding("No Organization or LocalBusiness markup anywhere on the site",
                  "no-org-schema",
                  pages=_read_urls(snapshot, 3) + ["{}/refused0".format(HOST)])],
        snapshot)
    assert len(held) == 1


def test_a_crawl_that_read_the_site_is_not_restated_at_all():
    """The restatement is what a thin crawl costs a finding. A crawl that read
    what it reached pays nothing, and its evidence is left as the check wrote
    it."""
    snapshot = _thin_crawl(read=13, refused=1)
    findings = [_finding("13 pages do not declare a language", "missing-lang",
                         pages=_read_urls(snapshot, 3))]
    kept, held = compose.withhold_absence_claims(findings, snapshot)
    assert held == []
    assert kept[0]["evidence"] == "Read off those pages."


def test_the_report_publishes_the_counts_and_still_refuses_to_grade():
    """Both halves in one report. The counts are facts about 13 documents; the
    verdict is a claim about a site this crawl did not see, and the two
    statements have to be able to sit in one report without contradicting."""
    snapshot = _thin_crawl()
    report = compose.compose(
        snapshot,
        [_skill_result(*[_finding(title, cause, pages=_read_urls(snapshot, 3))
                         for title, cause in THE_FIVE_NAMED])],
        audited_at="2026-01-01T00:00:00Z")
    assert len(report["findings"]) == len(THE_FIVE_NAMED)
    assert report["crawl"]["enough_to_judge"] is False
    assert "No blocking" not in report["summary"]["verdict"]


# --------------------------------------------------------------------------
# 2. Two sentences, one set
# --------------------------------------------------------------------------

def test_the_declines_are_not_described_as_the_checks_that_found_nothing_wrong():
    """The count and the header two lines under it described the same set in
    the same words and disagreed about its size: "0 that ran and found nothing
    wrong", then a list of declines introduced as "These were run and found
    nothing to report"."""
    declines = [{"skill": "structured-data-audit", "check": "product-schema",
                 "reason": "the site sells nothing"}]
    report = compose.compose(
        _thin_crawl(read=13, refused=1),
        [_skill_result(not_applicable=declines, checks_run=["product-schema"])],
        audited_at="2026-01-01T00:00:00Z")
    markdown = compose.render_markdown(report)
    assert "0 that ran and found nothing wrong" in markdown
    declines_section = markdown.split("### Checks that did not apply, and why")[1]
    assert "found nothing to report" not in declines_section.split("###")[0]


def test_the_bucket_the_count_names_is_the_bucket_the_heading_names():
    """Said as an assertion rather than as one absent string: whatever number
    the appendix prints for checks that ran and found nothing wrong is the
    number of bullets under the heading that says so."""
    passed = ["homepage-reachable", "sitemap-present"]
    report = compose.compose(
        _thin_crawl(read=13, refused=1),
        [_skill_result(checks_run=passed)],
        audited_at="2026-01-01T00:00:00Z")
    markdown = compose.render_markdown(report)
    stated = int(re.search(r"(\d+) that ran and found nothing wrong", markdown).group(1))
    section = markdown.split("### Checks that ran and found nothing wrong")[1].split("###")[0]
    assert stated == len(report["checks_passed"])
    assert stated == len([line for line in section.splitlines() if line.startswith("- ")])


# --------------------------------------------------------------------------
# 3. No character reference in prose
# --------------------------------------------------------------------------

# `&amp;`, `&#39;`, `&quot;`, and the double-escaped forms of each.
_CHARACTER_REFERENCE_RE = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,30}|#\d{1,7}|#x[0-9A-Fa-f]{1,6});")


def _references_outside_code(markdown):
    """Every rendered line carrying a character reference in prose."""
    offenders, in_fence = [], False
    for line in markdown.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        prose = re.sub(r"`[^`]*`", "", line)
        if _CHARACTER_REFERENCE_RE.search(prose):
            offenders.append(line)
    return offenders


def _report_for_a_double_escaped_brand():
    snapshot = _thin_crawl(read=13, refused=1)
    # What `og:site_name` carried on the real site: the ampersand escaped
    # twice, so one decode still leaves `Ash&amp;Loom` behind.
    snapshot["brand"]["name"] = "Ash&amp;amp;Loom"
    finding = _finding('Ash&amp;amp;Loom is written more than one way',
                       "name-inconsistency", pages=_read_urls(snapshot, 2),
                       evidence='The markup declares "Ash&amp;amp;Loom" and the '
                                "heading says Ash &amp;amp; Loom.")
    finding["suggested_action"]["how_to_fix"] = [
        "Write the name as Ash&amp;amp;Loom everywhere.",
        "Re-check one page in View Source: the value should carry the punctuation "
        "itself, not `&#39;` or `&amp;`.",
    ]
    return compose.compose(snapshot, [_skill_result(finding)],
                           audited_at="2026-01-01T00:00:00Z")


def test_no_rendered_line_prints_a_character_reference_in_prose():
    """Report prose read "A machine can reach Ash&amp;amp;Loom and read it."
    The name is `Ash&Loom`; the site's own `og:site_name` carries it
    double-escaped, and markdown decodes nothing."""
    markdown = compose.render_markdown(_report_for_a_double_escaped_brand())
    assert _references_outside_code(markdown) == []
    assert "Ash&Loom" in markdown


def test_a_reference_inside_code_is_left_exactly_as_written():
    """The fix step that tells a reader to confirm a value carries "the
    punctuation itself, not `&#39;` or `&amp;`" is the instruction, and
    decoding those two spans turns it into "not ' or &"."""
    markdown = compose.render_markdown(_report_for_a_double_escaped_brand())
    assert "not `&#39;` or `&amp;`" in markdown


def test_a_fenced_snippet_keeps_its_escapes():
    """A paste-ready block is not prose. `&lt;category&gt;` is the placeholder
    the reader replaces, and decoding it hands them a bare element instead."""
    fenced = "prose &amp;amp; more\n```\n<p>&lt;category&gt;</p>\n```\ntail &quot;x&quot;"
    decoded = compose.decode_rendered_markdown(fenced)
    assert "<p>&lt;category&gt;</p>" in decoded
    assert decoded.startswith("prose & more")
    assert decoded.endswith('tail "x"')


def test_a_double_escaped_value_is_decoded_all_the_way_down():
    """One pass of `html.unescape` turns `Ash&amp;amp;Loom` into
    `Ash&amp;Loom`, which is the same defect one step smaller."""
    assert compose.decode_character_references("Ash&amp;amp;Loom") == "Ash&Loom"
    assert compose.decode_character_references("O&#39;Neill &quot;q&quot;") == 'O\'Neill "q"'
