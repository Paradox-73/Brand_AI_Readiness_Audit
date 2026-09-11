# -*- coding: utf-8 -*-
"""Every check that ran ends up in exactly one place in the report.

The README promises that a check which stays quiet says why. A check has four
possible fates - it produced a finding, it passed, it declined with a reason,
or it was one of several registered beside the finding that fired - and a
reader counting the four buckets should reach the number of checks that ran.

They did not add up. `_group` was a flat list that only ever emptied on
`skip()`, so a check registered in one function and answered by neither a
finding nor a decline sat in it until some unrelated `add()` later in the same
skill swept it up as "fired". That excludes it from the passed list without
putting it anywhere else.

Seven of seventy-one checks on one real site appeared nowhere at all: not as
findings, not as declines, not as passes. Among them, on another site, was
whether robots.txt blocks AI crawlers - the single check that skill exists for.

The first four tests hold the bookkeeping in `SkillResult`. The rest hold the
report: two compose a run whose shape is chosen, and three run real audits and
count the buckets in the finished report, because the bookkeeping being right
is not the same claim as the report adding up.

The bucket has to be true as well as full. `sitemap-present`,
`sitemap-parses` and `unknown-paths-return-200` were filed under "Checks that
contributed to a finding above" in a report holding no such finding. A reader
who checks one claim and finds it wrong stops trusting the ones they cannot
check.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import ABSENCE_CAUSES, SkillResult, make_finding  # noqa: E402


def _compose_module():
    spec = importlib.util.spec_from_file_location(
        "compose_for_accounting_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose_module()


def _raised(check, **over):
    """One finding as a skill emits it: `make_finding` plus the `check` field
    `SkillResult.add` stamps on it."""
    finding = make_finding(**_finding(**over))
    finding["check"] = check
    return finding


def _synthetic_snapshot():
    """Three ordinary pages: enough coverage that no absence claim is held
    back, so the only thing deciding the buckets is what the test set up."""
    def page(url, body):
        return {"url": url, "final_url": url, "status": 200, "title": "A page",
                "page_type": "content", "body_text": body, "paragraphs": [body]}
    pages = [page("https://shop.test/", "The workshop re-canes chairs by the river."),
             page("https://shop.test/a", "Every chair is listed with the date it came in."),
             page("https://shop.test/b", "The bench is open on weekdays until five.")]
    return {"site": "shop.test", "origin": "https://shop.test",
            "brand": {"name": "Loom and Larder", "host": "shop.test"},
            "pages": pages, "sitemaps": [],
            "crawl": {"pages_crawled": 3, "pages_ok": 3, "pages_read": 3,
                      "render_mode": "static", "user_agent": "test-agent",
                      "elapsed_s": 1.0, "notes": []}}


def _finding(**over):
    base = dict(id_hint="x", title="t", severity="low", confidence="low",
                evidence="e", mechanism="A", root_cause="robots-block",
                summary="s", how_to_fix=["f"], effort="low", owner="developer",
                rationale="r")
    base.update(over)
    # A root cause that asserts a negative has to say where it looked, so a
    # synthetic finding using one has to say so too. `robots-block`, the
    # default here, reports something observed and must not carry it.
    if base["root_cause"] in ABSENCE_CAUSES:
        base.setdefault("checked", ("a stand-in source, for a test that is about "
                                    "check accounting rather than about evidence",))
    return base


# --------------------------------------------------------------------------
# A finding may only claim the checks its own function registered
# --------------------------------------------------------------------------

def test_a_check_left_unanswered_elsewhere_is_not_swept_up_as_fired():
    """`_group` was a flat list that only ever emptied on `skip()`.

    A check registered in one function and answered by neither a finding nor a
    decline sat in it until some unrelated `add()` later in the same skill
    swept it up as "fired" - which excludes it from the passed list without
    putting it anywhere else. Seven of seventy-one checks that ran on one site
    appeared nowhere in the report: not as findings, not as declines, not as
    passes. Among them was whether robots.txt blocks AI crawlers.
    """
    result = SkillResult("t")

    def registers_and_finds_nothing():
        result.check("robots-blocks-ai-answer-crawlers")
        result.check("robots-txt-reachable")

    def finds_something_else():
        result.check("sitemap-present")
        result.add(**_finding())

    registers_and_finds_nothing()
    finds_something_else()

    assert result.fired_checks == {"sitemap-present"}
    assert "robots-blocks-ai-answer-crawlers" not in result.fired_checks
    assert "robots-txt-reachable" not in result.fired_checks


def test_a_finding_still_claims_every_check_its_own_function_registered():
    """The behaviour this preserves: several checks registered together at the
    top of one function, any of which the finding might be about."""
    result = SkillResult("t")

    def one_function():
        result.check("sitemap-present")
        result.check("sitemap-parses")
        result.add(**_finding(root_cause="sitemap-missing"))

    one_function()
    assert result.fired_checks == {"sitemap-present", "sitemap-parses"}


def test_a_finding_raised_by_a_helper_still_claims_its_callers_checks():
    """A `_check_*` function often registers and then delegates."""
    result = SkillResult("t")

    def helper():
        result.add(**_finding())

    def outer():
        result.check("robots-blocks-all-crawlers")
        helper()

    outer()
    assert result.fired_checks == {"robots-blocks-all-crawlers"}


def test_an_explicit_decline_still_wins_over_a_sibling_that_fired():
    result = SkillResult("t")

    def one_function():
        result.check("sitemap-present")
        result.skip("sitemap-parses", "no sitemap to parse")
        result.add(**_finding(root_cause="sitemap-missing"))

    one_function()
    assert result.fired_checks == {"sitemap-present"}
    assert [n["check"] for n in result.not_applicable] == ["sitemap-parses"]


# --------------------------------------------------------------------------
# Every check that ran is in exactly one place in the appendix
# --------------------------------------------------------------------------

def test_no_check_that_ran_is_accounted_for_nowhere(audit):
    """The README promises that every check which stays quiet says why.

    A check registered alongside the one a finding names was marked as having
    fired - correctly keeping it out of the passed list - and then appeared
    nowhere at all: not a finding, not a decline, not a pass. Four of
    seventy-three on one real site, and among them, on another, was whether
    robots.txt blocks AI crawlers.
    """
    for name in ("good-site", "blocked-site", "dead-end-site", "no-schema-site",
                 "stale-site", "js-shell-site"):
        report = audit(name).report
        ran = {(c["skill"], c["check"]) for c in report.get("checks_run") or []}
        passed = {(c["skill"], c["check"]) for c in report.get("checks_passed") or []}
        declined = {(c.get("skill"), c["check"])
                    for c in report.get("not_applicable") or []}
        # Two lists where there used to be one. A check that fired and whose
        # finding this report does not publish - held back for want of
        # coverage, or from a skill none of whose findings survived the merge
        # - is not a contributor to a finding above, and a reader who went
        # looking for the finding the old heading promised found none. Both lists
        # count towards the accounting; only the wording splits.
        contributed = {(c["skill"], c["check"])
                       for c in report.get("checks_that_found_something") or []}
        unpublished = {(c["skill"], c["check"])
                       for c in report.get("checks_that_found_nothing_published") or []}
        named = {f.get("check") for f in report["findings"] if f.get("check")}
        stranded = [c for c in ran
                    if c not in passed and c not in declined
                    and c not in contributed and c not in unpublished
                    and c[1] not in named]
        assert not stranded, "{}: {} check(s) appear nowhere: {}".format(
            name, len(stranded), sorted(stranded))


def test_a_check_appears_in_only_one_place(audit):
    """Four buckets, no overlaps: a reader counting them should reach the
    number of checks that ran."""
    report = audit("blocked-site").report
    passed = {(c["skill"], c["check"]) for c in report.get("checks_passed") or []}
    declined = {(c.get("skill"), c["check"])
                for c in report.get("not_applicable") or []}
    contributed = {(c["skill"], c["check"])
                   for c in report.get("checks_that_found_something") or []}
    unpublished = {(c["skill"], c["check"])
                   for c in report.get("checks_that_found_nothing_published") or []}
    assert not passed & declined
    assert not passed & contributed
    assert not declined & contributed
    assert not contributed & unpublished
    assert not passed & unpublished
    assert not declined & unpublished


def test_a_check_whose_skill_lost_a_finding_claims_no_finding_above():
    """The same defect, and the half the skill-level rule left in place.

    A check gets into this bucket by having been registered by the function
    that raised some finding - `SkillResult.add` marks the whole group and
    records nowhere which of them the finding was. So "this skill published
    something" does not establish that *this* check's finding is above: on the
    catch-all origin that prompted this, `crawl-access-audit` published two
    findings, which is why `sitemap-present` and `sitemap-parses` were still
    called contributors after the first attempt at a fix.

    Here the site's breadcrumb markup finding is folded into engagement's
    visible-trail finding - a real path, `ONE_JOB_PAIRS` - so
    `structured-data-audit` has a finding this report does not hold. Its two
    unnamed checks may not claim one.
    """
    trail = dict(id_hint="deep-pages-have-no-breadcrumb", mechanism="D",
                     root_cause="no-breadcrumbs", severity="medium",
                     affected_pages=["https://shop.test/a"])
    markup = dict(id_hint="deep-pages-have-no-breadcrumb-markup", mechanism="D",
                      root_cause="no-breadcrumb-markup", severity="low",
                      affected_pages=["https://shop.test/a"])
    kept = dict(id_hint="no-organization-markup", mechanism="D",
                    root_cause="no-org-schema", severity="medium",
                    affected_pages=["https://shop.test/a"])
    results = [
        {"skill": "structured-data-audit",
         "findings": [_raised("breadcrumb-markup", **markup),
                      _raised("organization-markup", **kept)],
         "signals": {}, "extra_requests_made": 0, "not_applicable": [],
         "checks_run": ["breadcrumb-markup", "organization-markup", "logo-markup"],
         "fired_checks": ["breadcrumb-markup", "organization-markup", "logo-markup"]},
        {"skill": "engagement-audit", "findings": [_raised("breadcrumb-navigation", **trail)],
         "signals": {}, "extra_requests_made": 0, "not_applicable": [],
         "checks_run": ["breadcrumb-navigation"],
         "fired_checks": ["breadcrumb-navigation"]},
    ]
    report = compose.compose(_synthetic_snapshot(), results,
                             audited_at="2026-01-01T00:00:00Z")

    assert any(item["id_hint"] == "deep-pages-have-no-breadcrumb-markup"
               for item in report["merged_duplicates"]), \
        "this test needs the fold to have happened"
    contributed = {(c["skill"], c["check"])
                   for c in report["checks_that_found_something"]}
    assert not [c for c in contributed if c[0] == "structured-data-audit"], (
        "a skill with a finding this report does not publish cannot say which of "
        "its checks contributed to the ones it does")
    unpublished = {(c["skill"], c["check"])
                   for c in report["checks_that_found_nothing_published"]}
    assert ("structured-data-audit", "logo-markup") in unpublished
    # And the wording the reader checks it against.
    markdown = compose.render_markdown(report)
    assert "Checks that fired, with no finding above filed under them" in markdown
    assert "no finding in this report is filed under" in markdown


def test_a_skill_whose_every_finding_is_published_still_names_its_contributors():
    """The other direction: on a report holding everything a skill raised, the
    heading is true and the bucket must not empty itself into the other one.
    Without this, "no finding above is filed under them" becomes the answer to
    every check and says nothing."""
    kept = dict(id_hint="no-organization-markup", mechanism="D",
                    root_cause="no-org-schema", severity="medium",
                    affected_pages=["https://shop.test/a"])
    results = [{"skill": "structured-data-audit",
                "findings": [_raised("organization-markup", **kept)],
                "signals": {}, "extra_requests_made": 0, "not_applicable": [],
                "checks_run": ["organization-markup", "logo-markup"],
                "fired_checks": ["organization-markup", "logo-markup"]}]
    report = compose.compose(_synthetic_snapshot(), results,
                             audited_at="2026-01-01T00:00:00Z")
    assert {(c["skill"], c["check"]) for c in report["checks_that_found_something"]} == \
        {("structured-data-audit", "logo-markup")}
    assert not report["checks_that_found_nothing_published"]


def test_a_check_never_claims_a_finding_the_report_does_not_hold(audit):
    """The defect seen in a real report: `sitemap-present`, `sitemap-parses` and
    `unknown-paths-return-200` filed under "contributed to a finding above" in
    a report containing no such finding.

    A check may only be filed as contributing where the report actually holds
    a published finding from the same skill. Where it does not, it goes to the
    list whose heading says what happened instead.
    """
    for name in ("good-site", "blocked-site", "dead-end-site", "no-schema-site",
                 "stale-site", "js-shell-site"):
        report = audit(name).report
        published = {f.get("detected_by") for f in report["findings"]}
        for item in report.get("checks_that_found_something") or []:
            assert item["skill"] in published, (
                "{}: {} ({}) is filed as contributing to a finding above, and this report "
                "holds no finding from that skill".format(name, item["check"], item["skill"]))
