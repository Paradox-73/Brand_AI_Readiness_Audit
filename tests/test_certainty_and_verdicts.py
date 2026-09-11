"""Two properties of what gets published, both learned from real runs.

**Publish by certainty, not only by severity.** A validation pass over six
unseen sites produced 92 findings. An independent review, checking every finding
against the live page, marked 6 wrong and 11 overstated, so 22% of what was
published was defective - and the single wrongest finding on
one site was ranked first in "Start here". Every one of those findings already
carried a `confidence`: 41 of the 92 said `medium`, and the field gated
nothing, so a reading the audit itself calls uncertain was printed beside a
direct one, in the same list, at the same weight. These tests pin the split:
`high` confidence is the report's main body, everything below it goes to a
labelled lower tier that is still counted, still rendered in full, and never
eligible to lead "Start here".

**A verdict may not claim more than the crawl supports.** On one real site the
crawler recorded one page, that page was skipped as a redirect off the origin,
64 of 72 checks came back not-applicable, and the headline verdict read "No
blocking or significant problems were found". The same shape had shipped days
earlier from a different cause, a response the crawler could not decode. Both
times the audit read nothing and told the owner they were fine. These tests pin
the floor, the wording under it, and the fact that the checks which had nothing
to run against reach the reader as unanswered questions rather than as silence.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_certainty_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose()


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------

def _finding(id_hint, severity="medium", confidence="high", mechanism="B",
             root_cause="meta-hygiene", pages=(), checked=("one place",)):
    finding = {
        "id_hint": id_hint,
        "title": "Something about {}".format(id_hint),
        "severity": severity,
        "confidence": confidence,
        "evidence": "Observed on 2 of the 4 pages read.",
        "mechanism": mechanism,
        "root_cause": root_cause,
        "affected_pages": list(pages),
        "affected_page_count": len(pages),
        "suggested_action": {
            "summary": "Do the thing.",
            "effort": "low",
            "owner": "developer",
            "how_to_fix": ["Step one."],
            "rationale": "Because it works.",
        },
    }
    if checked:
        finding["checked"] = list(checked)
    return finding


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


def _page(url, body="A sentence long enough to be worth quoting on its own.", **kwargs):
    page = {
        "url": url,
        "final_url": url,
        "status": 200,
        "title": "A page",
        "page_type": "content",
        "body_text": body,
        "paragraphs": [body] if body else [],
    }
    page.update(kwargs)
    return page


def _snapshot(*pages, **kwargs):
    return {
        "site": "example.test",
        "origin": "https://example.test",
        "brand": {"name": "Example", "host": "example.test"},
        "pages": list(pages),
        "sitemaps": [],
        "crawl": dict({"pages_crawled": len(pages), "pages_ok": len(pages),
                       "render_mode": "static", "user_agent": "test-agent",
                       "elapsed_s": 1.0, "notes": []}, **kwargs.get("crawl", {})),
    }


def _readable_site(count=4):
    """A crawl that read enough pages for a confident verdict."""
    return _snapshot(*[_page("https://example.test/p{}".format(i)) for i in range(count)])


# --------------------------------------------------------------------------
# The tier rule
# --------------------------------------------------------------------------

def test_high_confidence_is_the_graded_body_and_the_rest_is_not():
    assert compose.certainty_tier({"confidence": "high"}) == compose.CONFIDENT
    assert compose.certainty_tier({"confidence": "medium"}) == compose.WORTH_CHECKING
    assert compose.certainty_tier({"confidence": "low"}) == compose.WORTH_CHECKING


def test_a_finding_with_no_confidence_at_all_is_not_treated_as_confident():
    """Absent is not the same as `high`, and the safe reading of a missing
    field is the one that does not put an unchecked claim in front of an owner
    as a certainty."""
    assert compose.certainty_tier({}) == compose.WORTH_CHECKING


def test_every_published_finding_carries_its_tier():
    report = compose.compose(
        _readable_site(),
        [_skill_result(_finding("a", confidence="high"),
                       _finding("b", confidence="medium"))],
        audited_at="2026-01-01T00:00:00Z")
    tiers = {f["id"]: f["tier"] for f in report["findings"]}
    assert set(tiers.values()) == {compose.CONFIDENT, compose.WORTH_CHECKING}
    assert all(f["tier"] in (compose.CONFIDENT, compose.WORTH_CHECKING)
               for f in report["findings"])


def test_a_demoted_finding_is_still_in_findings_and_still_counted():
    """Moving a finding down is not a miss. Deleting it is.

    The counts a schema consumer reads are over every tier, so splitting the
    report in two may not quietly shrink `total_findings` or the severity
    counts under it.
    """
    report = compose.compose(
        _readable_site(),
        [_skill_result(_finding("sure", severity="medium", confidence="high"),
                       _finding("unsure", severity="high", confidence="medium"))],
        audited_at="2026-01-01T00:00:00Z")

    assert report["summary"]["total_findings"] == 2
    assert report["summary"]["high"] == 1
    assert report["summary"]["medium"] == 1
    assert report["summary"]["confident"] == 1
    assert report["summary"]["worth_checking"] == 1
    assert len(report["findings"]) == 2
    assert len(report["worth_checking"]) == 1

    demoted = next(f for f in report["findings"] if f["tier"] == compose.WORTH_CHECKING)
    # The schema floor holds for a demoted finding exactly as for any other.
    for key in ("id", "title", "severity", "evidence", "suggested_action"):
        assert demoted[key], "a demoted finding lost the required key {}".format(key)
    assert demoted["suggested_action"]["how_to_fix"]


def test_the_summary_split_adds_up_to_the_total():
    report = compose.compose(
        _readable_site(),
        [_skill_result(_finding("a", confidence="high"),
                       _finding("b", confidence="medium"),
                       _finding("c", confidence="medium"))],
        audited_at="2026-01-01T00:00:00Z")
    summary = report["summary"]
    assert summary["confident"] + summary["worth_checking"] == summary["total_findings"]


def test_start_here_never_leads_with_a_finding_the_audit_is_unsure_of():
    """The failure this exists to stop, in the shape it happened.

    A high-severity, medium-confidence finding scored highest on one real
    site, led "Start here", and was the wrongest finding in the report. The
    lower-severity but firm finding is what an owner should be told to do
    first.
    """
    report = compose.compose(
        _readable_site(),
        [_skill_result(_finding("shaky", severity="high", confidence="medium"),
                       _finding("solid", severity="medium", confidence="high"))],
        audited_at="2026-01-01T00:00:00Z")
    by_id = {f["id"]: f for f in report["findings"]}
    assert report["start_here"], "a confident finding was available to lead"
    for finding_id in report["start_here"]:
        assert by_id[finding_id]["tier"] == compose.CONFIDENT
    assert by_id[report["start_here"][0]]["stable_id"] == "solid"


def test_start_here_is_empty_rather_than_led_by_a_weak_reading():
    """With nothing confident to lead on, the list is not filled from the
    lower tier. An empty "Start here" is honest; a confident-looking one drawn
    from readings the audit doubts is not."""
    report = compose.compose(
        _readable_site(),
        [_skill_result(_finding("a", severity="high", confidence="medium"),
                       _finding("b", severity="medium", confidence="medium"))],
        audited_at="2026-01-01T00:00:00Z")
    assert report["start_here"] == []
    assert len(report["worth_checking"]) == 2

    # And both documents say why it is empty, rather than dropping the section
    # and leaving the reader to think it went missing.
    for rendered in (compose.render_markdown(report), compose.render_html(report)):
        assert "Start here" in rendered
        assert "reached the bar for a first instruction" in rendered


def test_worth_checking_lists_ids_that_exist_and_match_the_tier():
    report = compose.compose(
        _readable_site(),
        [_skill_result(_finding("a", confidence="medium"),
                       _finding("b", confidence="high"))],
        audited_at="2026-01-01T00:00:00Z")
    by_id = {f["id"]: f for f in report["findings"]}
    for finding_id in report["worth_checking"]:
        assert finding_id in by_id
        assert by_id[finding_id]["tier"] == compose.WORTH_CHECKING
    assert not set(report["worth_checking"]) & set(report["start_here"])


# --------------------------------------------------------------------------
# Both documents show the split
# --------------------------------------------------------------------------

def _split_report():
    return compose.compose(
        _readable_site(),
        [_skill_result(_finding("solid", severity="medium", confidence="high"),
                       _finding("shaky", severity="high", confidence="medium"))],
        audited_at="2026-01-01T00:00:00Z")


def test_markdown_prints_the_lower_tier_under_its_own_heading():
    report = _split_report()
    markdown = compose.render_markdown(report)
    assert "## {}".format(compose.WORTH_CHECKING_HEADING) in markdown
    demoted = next(f for f in report["findings"] if f["tier"] == compose.WORTH_CHECKING)
    heading_index = markdown.index("## {}".format(compose.WORTH_CHECKING_HEADING))
    assert markdown.index(demoted["id"], heading_index) > heading_index, \
        "the demoted finding is not printed under the heading that qualifies it"


def test_markdown_says_the_demoted_finding_needs_checking_in_plain_words():
    markdown = compose.render_markdown(_split_report())
    assert "check it against the site" in markdown.lower()
    # And says nothing was dropped, so the reader does not conclude the audit
    # is hiding the rest.
    assert "counted in the totals" in markdown


def test_markdown_severity_table_shows_where_the_findings_went():
    """A reader who saw "High: 1" and then found that one under a heading
    saying it might be wrong would reasonably conclude the table was lying."""
    markdown = compose.render_markdown(_split_report())
    assert "| Severity | Reported with confidence | Worth checking |" in markdown
    assert "| High | 0 | 1 |" in markdown
    assert "| Medium | 1 | 0 |" in markdown


def test_markdown_explains_a_severe_finding_missing_from_start_here():
    """Two different reasons a severe finding can be off the list, and they
    need different sentences: outranked, or not sure enough to be eligible."""
    markdown = compose.render_markdown(_split_report())
    demoted = next(f for f in _split_report()["findings"]
                   if f["tier"] == compose.WORTH_CHECKING)
    assert "{} (high) is not on this list".format(demoted["id"]) in markdown


def test_html_prints_the_lower_tier_too():
    """report.md carrying the split and report.html not carrying it is how one
    of the two documents ends up reassuring."""
    report = _split_report()
    page = compose.render_html(report)
    assert compose.WORTH_CHECKING_HEADING in page
    for finding in report["findings"]:
        assert finding["id"] in page, "{} is missing from report.html".format(finding["id"])
    assert "Reported with confidence" in page
    assert "severity if confirmed" in page


def test_a_report_with_nothing_demoted_keeps_the_plain_table():
    report = compose.compose(
        _readable_site(),
        [_skill_result(_finding("a", confidence="high"))],
        audited_at="2026-01-01T00:00:00Z")
    markdown = compose.render_markdown(report)
    assert "| Severity | Count |" in markdown
    assert compose.WORTH_CHECKING_HEADING not in markdown


# --------------------------------------------------------------------------
# The readable-pages floor
# --------------------------------------------------------------------------

def test_the_floor_is_on_pages_that_came_back_with_text():
    """`pages_crawled` counted 1 on both damaging runs, so it cannot carry the
    floor. A URL that answers 200 and is skipped as a redirect off the origin,
    and one that answers 200 with a body nothing could be decoded from, both
    count as crawled and neither was read."""
    skipped = _snapshot(_page("https://example.test/", body="",
                              skipped="redirects off this origin"))
    assert compose.readable_text_pages(skipped) == []

    empty = _snapshot(_page("https://example.test/", body=""))
    assert compose.readable_text_pages(empty) == []

    challenged = _snapshot(_page("https://example.test/", challenge="bot manager"))
    assert compose.readable_text_pages(challenged) == []

    refused = _snapshot(_page("https://example.test/", status=403))
    assert compose.readable_text_pages(refused) == []

    real = _readable_site(3)
    assert len(compose.readable_text_pages(real)) == 3


def test_a_page_of_almost_no_text_does_not_count_as_read():
    """The bar is one quotable sentence. A page delivering less than that
    delivered nothing an assistant could use and nothing this audit can
    grade."""
    stub = _snapshot(_page("https://example.test/", body="Loading."))
    assert compose.readable_text_pages(stub) == []


@pytest.mark.parametrize("readable", [0, 1])
def test_no_clean_bill_of_health_below_the_floor(readable):
    """The exact sentence that shipped twice, and must not ship again."""
    verdict = compose._verdict(
        {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}, [],
        {}, {}, readable_pages=readable, pages_reached=1,
        why_unread="redirects off this origin")
    assert "No blocking or significant problems were found" not in verdict
    assert "has not seen enough of this site to judge it" in verdict


def test_the_verdict_below_the_floor_says_what_was_not_established():
    verdict = compose._verdict(
        {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}, [],
        {}, {}, readable_pages=0, pages_reached=1,
        why_unread="redirects off this origin")
    assert "What is established" in verdict
    assert "What is not established" in verdict
    # And names the cause the crawl itself recorded, rather than a guess.
    assert "redirects off this origin" in verdict


def test_the_floor_does_not_swallow_the_access_diagnosis():
    """A blocked site has a real diagnosis and it must survive the honesty
    clause: the access findings are the only thing the owner can act on."""
    findings = [{"root_cause": "bot-manager-block", "severity": "critical",
                 "mechanism": "A", "id_hint": "blocked"}]
    verdict = compose._verdict(
        {"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0}, findings,
        {}, {}, readable_pages=0, pages_reached=2, why_unread="2 answered HTTP 403")
    assert "shut out before they read anything" in verdict
    assert "has not seen enough of this site to judge it" in verdict


def test_a_crawl_that_read_enough_gets_the_ordinary_verdict():
    """The guard must not swallow every site. Two pages of readable text is
    the floor, and a site at or above it is graded as before."""
    verdict = compose._verdict(
        {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}, [],
        {}, {}, readable_pages=compose.VERDICT_MIN_READABLE_PAGES,
        pages_reached=2, has_recommendations=False)
    assert "has not seen enough" not in verdict
    assert "No blocking or significant problems were found" in verdict


def test_the_robots_block_still_explains_itself_first():
    """Two verdicts both say "we read nothing", and the one that names the
    cause is more useful than the one that only counts pages."""
    verdict = compose._verdict(
        {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}, [],
        {"audit_blocked_by_robots": True}, {}, readable_pages=0, pages_reached=0)
    assert "robots.txt disallows this auditor" in verdict


def test_compose_measures_the_floor_rather_than_leaving_it_unknown():
    """`readable_pages=None` turns the guard off, which is right for a caller
    testing one branch in isolation and wrong for the real report. `compose`
    always measures it."""
    report = compose.compose(
        _snapshot(_page("https://example.test/", body="",
                        skipped="redirects off this origin")),
        [_skill_result()], audited_at="2026-01-01T00:00:00Z")
    assert report["crawl"]["readable_pages"] == 0
    assert report["crawl"]["enough_to_judge"] is False
    assert "has not seen enough of this site to judge it" in report["summary"]["verdict"]


# --------------------------------------------------------------------------
# One meaning of "readable", and absence claims obey it
# --------------------------------------------------------------------------

def test_one_definition_of_readable_across_the_file():
    """A 200 that carried nothing is not a page the site was read on.

    `readable_counts` used to mean only "a 200 that was not skipped and not a
    challenge", which counts a page that answered perfectly and delivered no
    text. A two-URL crawl with one such page scored 0.5 readable, cleared the
    half-share bar, and published twelve claims about what a site does not say
    on a crawl that had read no text whatsoever.
    """
    snapshot = _snapshot(_page("https://example.test/a", body=""),
                         _page("https://example.test/b"))
    assert compose.readable_counts(snapshot) == (2, 1)
    assert compose.readable_share(snapshot) == 0.5
    # And the same numbers the verdict floor uses, not a second set.
    assert compose.readable_counts(snapshot)[1] == len(
        compose.readable_text_pages(snapshot))


def test_an_absence_claim_is_withheld_when_most_pages_carried_no_text():
    """The claim and the crawl in the shape they shipped in: a bot manager
    answers, the pages carry no text, and the report tells the owner their
    site never states its contact method."""
    snapshot = _snapshot(_page("https://example.test/", body=""),
                         _page("https://example.test/lander", body="",
                               skipped="redirects off this origin"))
    report = compose.compose(
        snapshot,
        [_skill_result(_finding("no-contact", root_cause="missing-core-fact",
                                checked=("the page text", "the markup")))],
        audited_at="2026-01-01T00:00:00Z")
    assert report["findings"] == []
    held = [i for i in report["unverifiable"] if i.get("root_cause") == "missing-core-fact"]
    assert held, "the claim vanished instead of being listed as unanswerable"
    assert "rather than about the site" in held[0]["reason"]


def test_an_absence_claim_survives_a_crawl_that_read_the_site():
    """The guard must not swallow every absence claim. A site that was read is
    a site the audit can say something about."""
    report = compose.compose(
        _readable_site(4),
        [_skill_result(_finding("no-contact", root_cause="missing-core-fact",
                                checked=("the page text", "the markup")))],
        audited_at="2026-01-01T00:00:00Z")
    assert [f["root_cause"] for f in report["findings"]] == ["missing-core-fact"]
    assert report["unverifiable"] == []


def test_a_single_page_site_that_was_read_still_gets_its_absence_claims():
    """The share is the right test here and a count floor is not. One page
    read out of one page reached is the whole site; telling that owner nothing
    could be established would be the opposite error."""
    report = compose.compose(
        _snapshot(_page("https://example.test/")),
        [_skill_result(_finding("no-contact", root_cause="missing-core-fact",
                                checked=("the page text", "the markup")))],
        audited_at="2026-01-01T00:00:00Z")
    assert [f["root_cause"] for f in report["findings"]] == ["missing-core-fact"]


# --------------------------------------------------------------------------
# The un-run checks reach the reader
# --------------------------------------------------------------------------

def _unread_report():
    declines = [{"skill": "structured-data-audit", "check": "product-schema",
                 "reason": "no product page was crawled"},
                {"skill": "structured-data-audit", "check": "faq-schema",
                 "reason": "no question-and-answer content was crawled"}]
    return compose.compose(
        _snapshot(_page("https://example.test/", body="",
                        skipped="redirects off this origin")),
        [_skill_result(not_applicable=declines,
                       checks_run=["product-schema", "faq-schema"])],
        audited_at="2026-01-01T00:00:00Z")


def test_checks_with_nothing_to_run_against_reach_the_unverifiable_channel():
    """`unverifiable` was an empty list on that real run while 64 checks had
    nothing to run against, so the appendix presented 64 declines as though
    each were a reasoned judgement about the site."""
    report = _unread_report()
    assert len(report["unverifiable"]) == 2
    titles = {item["title"] for item in report["unverifiable"]}
    assert titles == {"product-schema", "faq-schema"}


def test_the_declines_stay_in_the_appendix_as_well():
    """Echoed, not moved: the appendix's four buckets add up to the number of
    checks that ran, and taking a decline out of `not_applicable` would break
    that arithmetic."""
    report = _unread_report()
    assert len(report["not_applicable"]) == 2


def test_a_decline_is_not_called_a_pass_when_nothing_was_read():
    report = _unread_report()
    for item in report["unverifiable"]:
        assert "rather than about the site" in item["reason"]
        assert "counted as a pass" in item["reason"]


def test_a_crawl_that_read_enough_leaves_its_declines_alone():
    """A decline on a site that was read is a reasoned judgement and belongs in
    the appendix, not in the list of questions that went unanswered."""
    declines = [{"skill": "structured-data-audit", "check": "product-schema",
                 "reason": "the site sells nothing"}]
    report = compose.compose(
        _readable_site(),
        [_skill_result(not_applicable=declines, checks_run=["product-schema"])],
        audited_at="2026-01-01T00:00:00Z")
    assert report["unverifiable"] == []
    assert len(report["not_applicable"]) == 1


def test_both_documents_lead_with_what_was_not_established():
    """Ahead of the severity table in both, because a table of zeros under a
    reassuring paragraph is what cost marks twice."""
    report = _unread_report()

    markdown = compose.render_markdown(report)
    caveat = markdown.index(compose.COVERAGE_CAVEAT_HEADING)
    assert caveat < markdown.index("| Severity |"), \
        "report.md put the severity table above what the crawl could not establish"
    assert "Questions this audit could not answer" in markdown

    page = compose.render_html(report)
    assert compose.COVERAGE_CAVEAT_HEADING in page
    assert page.index(compose.COVERAGE_CAVEAT_HEADING) < page.index("Appendix")


def test_the_first_line_says_how_much_was_actually_read():
    """"1 page crawled" was the whole of what the header said about a run that
    read nothing: a skipped redirect counts as crawled and carries no text."""
    markdown = compose.render_markdown(_unread_report())
    assert "1 page crawled, 0 readable" in markdown


def test_a_normal_run_keeps_the_plain_first_line():
    report = compose.compose(_readable_site(3), [_skill_result()],
                             audited_at="2026-01-01T00:00:00Z")
    markdown = compose.render_markdown(report)
    assert "3 pages crawled ·" in markdown
    assert "readable" not in markdown.split("\n")[2]


def test_no_coverage_caveat_when_the_crawl_read_the_site():
    report = compose.compose(_readable_site(4), [_skill_result()],
                             audited_at="2026-01-01T00:00:00Z")
    assert compose._coverage_caveat_lines(report) == []
    assert compose.COVERAGE_CAVEAT_HEADING not in compose.render_markdown(report)


# --------------------------------------------------------------------------
# The address audited is not always the address asked about
# --------------------------------------------------------------------------

def _redirect(followed, requested="asked.test", landed="landed.test"):
    return {
        "requested_origin": "https://" + requested,
        "requested_url": "https://" + requested + "/",
        "final_url": "https://" + landed + "/",
        "final_origin": "https://" + landed,
        "status": 301,
        "same_registered_name": followed,
        "followed": followed,
        "reason": "The address this audit was asked about, https://{}/, answers with a "
                  "redirect to https://{}/, which is off its own origin.".format(
                      requested, landed),
    }


def test_a_seed_that_left_its_origin_and_was_not_followed_gets_no_verdict():
    """No page of the site was fetched, so the severity ladder has nothing to
    rank and must not be allowed to. This is the strongest form of the same
    failure the readable-pages floor exists for."""
    verdict = compose._verdict(
        {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}, [], {}, {},
        readable_pages=0, pages_reached=1,
        origin_redirect=_redirect(followed=False))
    assert "No blocking or significant problems were found" not in verdict
    assert "redirect" in verdict
    assert "Nothing below is evidence about it either way" in verdict
    assert "run the audit against that address" in verdict


def test_that_verdict_outranks_even_the_robots_explanation():
    """robots.txt is a fact about a site this crawl never reached."""
    verdict = compose._verdict(
        {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}, [],
        {"audit_blocked_by_robots": True}, {},
        readable_pages=0, pages_reached=1,
        origin_redirect=_redirect(followed=False))
    assert "robots.txt disallows this auditor" not in verdict
    assert "Nothing below is evidence about it either way" in verdict


def test_a_followed_redirect_says_whose_site_this_is_before_grading_it():
    """Pages were read, so the ladder still runs - but every sentence under it
    is about a different hostname from the one somebody typed."""
    verdict = compose._verdict(
        {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}, [], {}, {},
        readable_pages=9, pages_reached=9, has_recommendations=False,
        origin_redirect=_redirect(followed=True))
    assert verdict.startswith("You asked about ")
    assert "everything below describes" in verdict
    # And the ordinary verdict still follows it rather than being replaced.
    assert "No blocking or significant problems were found" in verdict


def _redirected_report(followed):
    snapshot = _readable_site(3)
    snapshot["requested_origin"] = "https://asked.test"
    snapshot["requested_seed_url"] = "https://asked.test/"
    snapshot["origin_redirect"] = _redirect(followed=followed)
    if not followed:
        snapshot["pages"] = [_page("https://asked.test/", body="",
                                   skipped="redirects off this origin")]
        snapshot["crawl"]["pages_crawled"] = 1
    return compose.compose(snapshot, [_skill_result()],
                           audited_at="2026-01-01T00:00:00Z")


def test_the_redirect_reaches_the_report_and_both_documents():
    """`crawl.notes` already carried the sentence, and a note at the foot of
    the appendix is not where somebody looks to find out whose website they
    are reading about."""
    for followed in (True, False):
        report = _redirected_report(followed)
        assert report["crawl"]["origin_redirect"]["followed"] is followed
        assert report["crawl"]["requested_origin"] == "https://asked.test"
        heading, lines = compose._origin_caveat(report)
        assert lines

        markdown = compose.render_markdown(report)
        assert heading in markdown
        assert markdown.index(heading) < markdown.index("| Severity |")

        page = compose.render_html(report)
        assert heading in page
        assert page.index(heading) < page.index("Appendix")


def test_a_followed_redirect_names_the_typed_address_in_the_first_line():
    report = _redirected_report(followed=True)
    first_lines = compose.render_markdown(report).split("\n")[:4]
    assert any("you asked about https://asked.test" in line for line in first_lines)


def test_no_origin_caveat_when_the_crawl_stayed_where_it_was_pointed():
    report = compose.compose(_readable_site(3), [_skill_result()],
                             audited_at="2026-01-01T00:00:00Z")
    assert report["crawl"]["origin_redirect"] is None
    assert compose._origin_caveat(report) is None
    assert compose.ORIGIN_MOVED_HEADING not in compose.render_markdown(report)
    assert "you asked about" not in compose.render_markdown(report)


# --------------------------------------------------------------------------
# Three counts of one crawl, narrowing
# --------------------------------------------------------------------------

def test_the_report_publishes_the_crawlers_count_and_its_own():
    """`pages_ok` counts a 200 whatever came back with it; `pages_read` is the
    crawler's count of 200s it did not skip; `readable_pages` is those minus
    challenges and minus anything carrying less than a quotable sentence. The
    floor gates on the strictest, and the three may not disagree silently."""
    snapshot = _snapshot(_page("https://example.test/a"),
                         _page("https://example.test/b", body="Loading."),
                         _page("https://example.test/c", body="",
                               skipped="redirects off this origin"))
    snapshot["crawl"]["pages_ok"] = 3
    snapshot["crawl"]["pages_read"] = 2
    report = compose.compose(snapshot, [_skill_result()],
                             audited_at="2026-01-01T00:00:00Z")
    crawl = report["crawl"]
    assert crawl["pages_ok"] == 3
    assert crawl["pages_read"] == 2
    assert crawl["readable_pages"] == 1
    assert crawl["readable_pages"] <= crawl["pages_read"] <= crawl["pages_ok"]
    assert crawl["enough_to_judge"] is False


def test_the_report_never_claims_more_coverage_than_the_crawler_recorded():
    for count in (0, 1, 3, 7):
        snapshot = _readable_site(count) if count else _snapshot()
        snapshot["crawl"]["pages_read"] = count
        report = compose.compose(snapshot, [_skill_result()],
                                 audited_at="2026-01-01T00:00:00Z")
        assert report["crawl"]["readable_pages"] <= report["crawl"]["pages_read"]


# --------------------------------------------------------------------------
# Recommendations fire on measurements, not on a page type being present
# --------------------------------------------------------------------------

def _recommendations(snapshot, **signals):
    return {r["id"] for r in compose.build_recommendations(snapshot, signals, [])}


def _shop(count=4):
    """A crawl of pages that publish a price, which is what these gate on."""
    snapshot = _readable_site(count)
    for index, page in enumerate(snapshot["pages"]):
        page["page_type"] = "product"
        page["prices"] = ["GBP 49"]
        page["body_text"] = "The Model {} costs GBP 49 and ships from our workshop.".format(
            index)
    return snapshot


def test_the_landing_page_entry_fires_on_measured_pages_not_on_a_page_type():
    """Gated on a page type, this would fire on every site with a product
    page - which is how three of the fourteen came to fire on six sites out of
    six. On a crawl that measured nothing it must stay quiet."""
    shop = _shop()
    assert "R-LANDING-PAGE-ANSWERS-FIRST" not in _recommendations(shop)

    urls = [p["url"] for p in shop["pages"]]
    fired = _recommendations(shop, landing_pages_stating_only_a_price=urls)
    assert "R-LANDING-PAGE-ANSWERS-FIRST" in fired

    fired = _recommendations(shop, landing_pages_that_bury_the_price=urls[:2])
    assert "R-LANDING-PAGE-ANSWERS-FIRST" in fired


def test_one_page_is_not_a_template():
    """The clean fixture has exactly one deep page whose only onward links are
    the site's own menu, and it must produce no advice at all."""
    shop = _shop()
    one = [shop["pages"][0]["url"]]
    assert "R-LANDING-PAGE-ANSWERS-FIRST" not in _recommendations(
        shop, landing_pages_stating_only_a_price=one)
    assert "R-WAYFINDING" not in _recommendations(
        shop, deep_pages_offering_only_site_wide_links=one)


def test_wayfinding_fires_on_pages_whose_only_links_are_site_furniture():
    shop = _shop()
    urls = [p["url"] for p in shop["pages"]][:2]
    assert "R-WAYFINDING" in _recommendations(
        shop, deep_pages_offering_only_site_wide_links=urls)


def test_the_landing_page_entry_is_written_for_a_non_expert():
    shop = _shop()
    urls = [p["url"] for p in shop["pages"]]
    entry = next(r for r in compose.build_recommendations(
        shop, {"landing_pages_stating_only_a_price": urls}, [])
        if r["id"] == "R-LANDING-PAGE-ANSWERS-FIRST")
    assert entry["mechanism"] == "G"
    assert entry["how_to_do_it"]
    # The mechanism, said in words rather than by letter.
    assert "assistant" in entry["why_this_works"]
    assert "leave" in entry["why_this_works"]


def test_comparison_pages_are_not_suggested_to_a_site_that_sells_nothing():
    """A national weather service and a computing history archive were both
    told to publish "X vs Y" pages, because the whole condition was that no
    page of the site is typed as a comparison."""
    reference = _readable_site(4)
    for page in reference["pages"]:
        page["page_type"] = "article"
    assert "R-COMPARISON-PAGES" not in _recommendations(reference)
    assert "R-COMPARISON-PAGES" in _recommendations(_shop())


def test_corroboration_is_suggested_on_measured_breadth_alone():
    """The study this catalogue cites says the gap between named and ignored
    brands is spread across ordinary platforms rather than concentrated in
    Wikipedia, so the absence of a Wikidata item is not the trigger. Two sites
    in a validation pass link 8 and 10 profiles and were told to go and claim
    more."""
    shop = _shop()
    assert "R-SAMEAS-WIKIDATA" not in _recommendations(
        shop, profile_breadth=8, has_wikidata_or_wikipedia=False)
    assert "R-SAMEAS-WIKIDATA" in _recommendations(
        shop, profile_breadth=1, has_wikidata_or_wikipedia=True)


# --------------------------------------------------------------------------
# The rules are general
# --------------------------------------------------------------------------

def test_the_unread_reason_is_read_off_the_crawl_and_not_hardcoded():
    """A refusal shape nobody has seen yet has to be described as accurately as
    the two that have already cost marks, so the clause counts the reasons the
    crawl recorded rather than naming causes this file knows about."""
    snapshot = _snapshot(
        _page("https://example.test/a", body="", skipped="not HTML"),
        _page("https://example.test/b", body="", skipped="not HTML"),
        _page("https://example.test/c", status=503))
    clause = compose.why_little_was_read(snapshot)
    assert "2 not HTML" in clause
    assert "answered HTTP 503" in clause


def test_the_tier_rule_reads_one_field_and_names_no_instance():
    """A rule that names an instance is a rule that fails on the next site.

    The tier decision must rest on `confidence` alone. Branching it on
    severity, on a root cause, on which skill raised the finding or on how many
    sources it named would turn one general rule into a list of special cases,
    and the list would be one short on the next site audited.
    """
    import inspect
    body = inspect.getsource(compose.certainty_tier)
    code = body.split('"""')[-1]
    assert "confidence" in code
    for forbidden in ("severity", "root_cause", "detected_by", "checked", "id_hint"):
        assert forbidden not in code, \
            "certainty_tier branches on {}, which makes it a rule about " \
            "particular checks rather than about certainty".format(forbidden)


def test_the_report_schema_documents_the_new_keys():
    """The schema file is the contract a reader of report.json is handed."""
    import json
    path = os.path.join(ROOT, "skills", "audit-orchestrator", "references",
                        "report-schema.json")
    with open(path, encoding="utf-8") as handle:
        schema = json.load(handle)
    properties = schema["properties"]
    assert "worth_checking" in properties
    assert "tier" in properties["findings"]["items"]["properties"]
    assert "confident" in properties["summary"]["properties"]
    assert "readable_pages" in properties["crawl"]["properties"]
    assert "enough_to_judge" in properties["crawl"]["properties"]
