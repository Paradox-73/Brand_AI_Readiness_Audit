"""Runtime contract: schema, determinism, budgets, safety and the end-to-end runner.

These are the properties a user is entitled to assume without reading the
code: the report has the shape the contest specified, the same input produces
the same output, the crawl stays inside its budget, and nothing writes to the
site being audited.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

import pytest

from conftest import (
    REFERENCE_DATE, REFERENCE_TIMESTAMP, ROOT, SCRIPTS, SUB_SKILLS,
    all_fixture_names, run_script,
)
from fixture_server import FixtureServer, LatentSite, linked_pages

FIXTURE_NAMES = all_fixture_names()

SEVERITY_RANK_FOR_TEST = {
    "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# The contest's minimum report shape. Nothing below is optional.
REQUIRED_TOP_LEVEL = ("site", "audited_at", "summary", "findings")
REQUIRED_SUMMARY = ("total_findings", "critical", "high", "medium")
REQUIRED_FINDING = ("id", "title", "severity", "evidence", "suggested_action")
REQUIRED_ACTION = ("summary", "priority")


# --------------------------------------------------------------------------
# Required schema
# --------------------------------------------------------------------------

def test_report_has_every_required_key(audit):
    for name in FIXTURE_NAMES:
        _assert_report_shape(audit(name).report)


def _assert_report_shape(report):
    for key in REQUIRED_TOP_LEVEL:
        assert key in report, "report is missing required key `{}`".format(key)
    for key in REQUIRED_SUMMARY:
        assert key in report["summary"], "summary is missing required key `{}`".format(key)
        assert isinstance(report["summary"][key], int)

    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", report["audited_at"])
    assert report["site"]

    for finding in report["findings"]:
        for key in REQUIRED_FINDING:
            assert key in finding, "finding {} is missing `{}`".format(
                finding.get("id"), key)
        for key in REQUIRED_ACTION:
            assert key in finding["suggested_action"], \
                "finding {} suggested_action is missing `{}`".format(finding["id"], key)
        assert re.match(r"^F-\d{3}$", finding["id"])
        assert finding["severity"] in ("critical", "high", "medium", "low", "info")
        # Deliberately not the severity words. Priority answers "what should you
        # do first", severity answers "how bad is this", and sharing a
        # vocabulary made a medium finding read as "Priority critical".
        assert finding["suggested_action"]["priority"] in (
            "do first", "do soon", "schedule", "when convenient")
        assert finding["suggested_action"]["priority"] != finding["severity"]


def test_summary_counts_match_the_findings(audit):
    for name in FIXTURE_NAMES:
        _assert_summary_counts(audit(name).report)


def _assert_summary_counts(report):
    findings = report["findings"]
    assert report["summary"]["total_findings"] == len(findings)
    for level in ("critical", "high", "medium", "low", "info"):
        counted = sum(1 for f in findings if f["severity"] == level)
        assert report["summary"][level] == counted, \
            "summary.{} says {} but {} findings have that severity".format(
                level, report["summary"][level], counted)


def test_ids_are_unique_and_sequential(audit):
    for name in FIXTURE_NAMES:
        ids = [f["id"] for f in audit(name).report["findings"]]
        assert len(ids) == len(set(ids)), "{}: duplicate finding IDs".format(name)
        assert ids == ["F-{:03d}".format(i) for i in range(1, len(ids) + 1)], \
            "{}: finding IDs must run F-001 upward with no gaps".format(name)


def test_start_here_points_at_real_findings(audit):
    for name in FIXTURE_NAMES:
        _assert_start_here(audit(name))


def _assert_start_here(result):
    report = result.report
    by_id = {f["id"]: f for f in report["findings"]}
    assert len(report["start_here"]) <= 3
    for finding_id in report["start_here"]:
        assert finding_id in by_id, "start_here references unknown finding {}".format(finding_id)
        assert by_id[finding_id]["severity"] != "info", \
            "start_here must not surface info-severity observations"

    # Only the confident tier is eligible, with one exception the report names
    # for itself. A real run put a high-severity, medium-confidence finding
    # first on one site and it was the wrongest finding in that report, so the
    # first instruction the owner read was to spend a day fixing something that
    # was not wrong. The expected list below is recomputed from the same pool
    # the report draws from.
    #
    # The exception is a finding the verdict's own opening paragraph names as
    # the cause of the rest. A report whose prose says "most of what follows is
    # a consequence of that" and whose action list omits that finding has
    # failed at prioritising in the most-read place there is, so it keeps a
    # place on the list whichever tier it is in - and `leads_the_verdict`
    # records which, so a reader is told to confirm a lower-tier one before
    # spending anything.
    #
    # A place, not the first place. Moving those entries to the front was how
    # the sheet came to break its own printed rule that "the ranking puts the
    # most severe band first" on 3 of 6 sites reviewed: a `critical` confident
    # finding ranked third behind two `medium` ones, and on another site a
    # `high` confident finding was pushed off the list of three by a `low` one
    # from the tier the list is not drawn from. Severity is the outer key of
    # the whole list, and the exception now buys the last slot rather than the
    # first.
    leads = [i for i in (report.get("leads_the_verdict") or [])
             if by_id[i]["severity"] != "info"]
    everything = sorted([f for f in report["findings"] if f["severity"] != "info"],
                        key=lambda f: (SEVERITY_RANK_FOR_TEST[f["severity"]],
                                       -f["suggested_action"]["priority_score"], f["id"]))

    # `follows_from` is the second rule the list is built on and this
    # reimplementation did not carry it: a finding whose own fix cannot be
    # carried out until another finding in the same report is fixed is not a
    # thing to do first. `compose_report` has applied it since the deferral
    # machinery landed; nothing had exercised it here until a `confident`
    # finding started carrying the field, and the first one to do so - a
    # homepage that reaches no other page, deferred to the shell finding that
    # explains it - made this helper and the code disagree about one slot.
    actionable = [f for f in report["findings"]
                  if f["severity"] != "info" and f["tier"] == "confident"
                  and not f.get("follows_from")]
    if actionable:
        # Top three by priority score, drawn from medium-and-above first.
        # Ranking on the score alone put a `low` finding - missing Open Graph
        # tags, which control what a link preview looks like when shared -
        # above structured data that does not parse, because a cheap site-wide
        # fix carries full reach and the lowest effort divisor.
        # Severity leads; the priority score orders within each band. Ranking
        # on the score alone let a `low` site-wide fix outrank a `high` one,
        # because reach floors at 0.25 for a finding that names its pages and
        # is 1.0 for one that names none.
        ranked = sorted(actionable,
                        key=lambda f: (SEVERITY_RANK_FOR_TEST[f["severity"]],
                                       -f["suggested_action"]["priority_score"], f["id"]))
        substantive = [f for f in ranked if f["severity"] in ("critical", "high", "medium")]
        # The verdict's exception, applied the way `_start_here_ids` applies
        # it: its findings join the candidates whichever tier they are in, the
        # whole set is put back into the report's own severity-first order,
        # and one of them takes the last slot if none of them made the top
        # three on merit.
        position = {f["id"]: index for index, f in enumerate(everything)}
        candidates = list(ranked)
        listed = {f["id"] for f in candidates}
        candidates += [f for f in everything
                       if f["id"] in leads and f["id"] not in listed]
        candidates.sort(key=lambda f: position[f["id"]])
        chosen = candidates[:3]
        if leads and not any(f["id"] in leads for f in chosen):
            cause = next((f for f in candidates if f["id"] in leads), None)
            if cause is not None and len(chosen) == 3:
                chosen = chosen[:2] + [cause]
                chosen.sort(key=lambda f: position[f["id"]])
        expected = [f["id"] for f in chosen]
        assert report["start_here"] == expected

        # The rule the sheet prints about itself, asserted rather than
        # described: no entry on this list is less severe than one below it.
        bands = [SEVERITY_RANK_FOR_TEST[by_id[i]["severity"]] for i in report["start_here"]]
        assert bands == sorted(bands), \
            "{}: Start here is not in severity order".format(result.name)

        # An entry the verdict pulled up out of the lower tier is marked as one
        # to confirm, because every other entry on this list was read straight
        # off the document.
        for finding_id in report["start_here"]:
            if by_id[finding_id]["tier"] != "confident":
                assert finding_id in leads, \
                    "{} is on Start here from the lower tier without the " \
                    "verdict naming it".format(finding_id)
                assert "Confirm this one first" in result.report_md, \
                    "a lower-tier entry on Start here is not marked"

        chosen = [by_id[i] for i in report["start_here"]]
        if len(substantive) >= 3:
            assert all(f["severity"] != "low" for f in chosen),                 "a low-severity finding displaced a substantive one in Start here"


def test_priority_score_matches_the_documented_formula(audit):
    """severity_weight x reach / effort, as severity-and-priority.md states."""
    weights = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    divisors = {"low": 1.0, "medium": 1.5, "high": 2.0}
    for finding in [f for n in FIXTURE_NAMES for f in audit(n).report["findings"]]:
        action = finding["suggested_action"]
        expected = round(
            weights[finding["severity"]] * finding["reach"] / divisors[action["effort"]], 3)
        assert abs(action["priority_score"] - expected) < 0.002, \
            "{}: priority_score {} does not match the formula ({})".format(
                finding["id"], action["priority_score"], expected)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["good-site", "blocked-site", "dead-end-site"])
def test_same_snapshot_produces_identical_findings(audit, tmp_path, name):
    """Run every check twice over one snapshot; the findings must be byte-identical.

    This is why no finding carries a timestamp and why `audited_at` lives only
    at the report root. It is also why sampling is seeded and every list is
    sorted before it is written.
    """
    result = audit(name)
    runs = []
    for attempt in (1, 2):
        outputs = []
        for skill in SUB_SKILLS:
            out_path = str(tmp_path / "{}-{}-{}.json".format(name, skill, attempt))
            args = [os.path.join(ROOT, "skills", skill, "scripts", "check.py"),
                    "--snapshot", result.snapshot_path, "--out", out_path,
                    "--no-network"]
            if skill == "freshness-corroboration-audit":
                args += ["--now", REFERENCE_DATE]
            run_script(args, skill)
            outputs.append(out_path)

        report_path = str(tmp_path / "{}-report-{}.json".format(name, attempt))
        run_script([os.path.join(SCRIPTS, "compose_report.py"),
                    "--snapshot", result.snapshot_path, "--findings"] + outputs +
                   ["--out-json", report_path,
                    "--out-md", str(tmp_path / "{}-report-{}.md".format(name, attempt)),
                    "--audited-at", REFERENCE_TIMESTAMP], "compose_report.py")
        with open(report_path, encoding="utf-8") as handle:
            runs.append(json.load(handle))

    first, second = runs
    assert json.dumps(first["findings"], sort_keys=True) == \
        json.dumps(second["findings"], sort_keys=True), \
        "findings differ between two runs over the same snapshot"
    assert first["recommendations"] == second["recommendations"]
    assert first["citation_simulation"] == second["citation_simulation"]
    assert first["summary"] == second["summary"]


def test_no_finding_contains_a_timestamp(audit):
    """`audited_at` is the only timestamp; anything else would break determinism."""
    iso_like = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
    for name in ("good-site", "stale-site", "blocked-site"):
        blob = json.dumps(audit(name).report["findings"])
        assert not iso_like.search(blob), \
            "{}: a finding contains a timestamp".format(name)


# --------------------------------------------------------------------------
# Determinism when the site, rather than the crawl, is the larger of the two
# --------------------------------------------------------------------------
#
# The test above runs the checks twice over one snapshot, so it can only prove
# that a fixed page set produces fixed findings. It cannot see the defect two
# independent reviews found. The first ran the whole tool twice against one
# unchanged site and got 11 findings and then 13. The second got 53 pages and
# 16 findings, then 60 pages and 19, on one machine with nothing else running,
# and put it this way: "Two people running the same audit get two different reports and
# cannot tell which findings moved."
#
# Every fixture in this suite answers off local disk in microseconds and is
# smaller than the page ceiling, so nothing here ever read a page set that
# anything but the site decided. `LatentSite` is the missing half: a generated
# site larger than the crawl is allowed to read, answering at a stated speed,
# and answering at a *varying* speed when a test asks it to. Variation is the
# case that matters, because the count used to be computed from the median
# cost of the first five pages rounded onto a doubling ladder, and ordinary
# jitter is enough to move that median across a rung.


def _crawl_latent_site(pace, budget, out_path, pages=40, max_pages=None,
                       extra_args=()):
    """Crawl a generated site of `pages` pages answering at `pace`."""
    site_pages = linked_pages(pages)
    for missing in _ADDRESSES_THIS_SITE_DOES_NOT_SERVE:
        site_pages.pop(missing, None)
    with LatentSite(site_pages, pace) as site:
        command = [os.path.join(SCRIPTS, "crawl.py"), site.base_url,
                   "--out", out_path, "--budget", str(budget),
                   "--delay", "0", "--no-render"]
        if max_pages is not None:
            command += ["--max-pages", str(max_pages)]
        run_script(command + list(extra_args), "crawl.py [latent site]")
    with open(out_path, encoding="utf-8") as handle:
        return json.load(handle)


# The 19th and 20th addresses in the crawl's fixed order, deleted from the site
# the homepage links to. They are how this file tells a difference in the page
# set from a difference in the report: a crawl that reaches them fetches two
# 404s and raises a finding about them, and a crawl that stops at eighteen
# pages does not. Every other page here is the same shape as every other, so
# nothing else in the report can move.
_ADDRESSES_THIS_SITE_DOES_NOT_SERVE = ("/note-17.html", "/note-18.html")


def _paths(snapshot):
    """The crawled addresses without their host.

    Each run gets its own ephemeral port, so the host is the one thing about
    two runs of this site that is genuinely different.
    """
    return [page["url"].split("/", 3)[-1] for page in snapshot["pages"]]


def test_three_networks_one_page_set(tmp_path):
    """The experiment that exposed it: one unchanged site, three speeds, one
    answer.

    Reproduced against a site of 40 pages with the crawl allowed 20 of them,
    which is the shape seen in the wild - a site larger than the budget can
    read. The
    three runs differ only in how long the server takes to answer: a tenth of
    a second, three tenths, and a draw from a seeded generator that varies
    every response between the two.

    Before the fix this failed on the numbers first reported. The count was
    `budget x 0.75 / rung(median cost of the first five pages)` over rungs
    doubling from 0.25s, so 0.10s a page bought `int(9 / 0.25) = 36` pages -
    capped at 20 - and 0.30s a page bought `int(9 / 0.5) = 18`. Measured here:
    20, 18, 20, and 12, 11, 12 findings. The two counts are not two readings of
    a site, they are the two sides of one rung boundary.
    """
    import random

    def steady(seconds):
        return lambda served: seconds

    def jitter(seed):
        generator = random.Random(seed)
        return lambda served: generator.uniform(0.05, 0.35)

    runs = {}
    for name, pace in (("fast", steady(0.10)),
                       ("slow", steady(0.30)),
                       ("varying", jitter(11))):
        runs[name] = _crawl_latent_site(
            pace, 12, str(tmp_path / "{}.json".format(name)), max_pages=20)

    for name, snapshot in runs.items():
        crawl = snapshot["crawl"]
        plan = crawl["page_plan"]
        assert plan["deterministic"] is True, (
            "the {} run says its own page set does not repeat: {}".format(
                name, plan["not_reproducible_because"]))
        assert plan["not_reproducible_because"] == []
        assert crawl["stopped_by"] == "page ceiling", (
            "the {} run was stopped by {}, so this test is not measuring what it "
            "claims".format(name, crawl["stopped_by"])
        )
        assert crawl["pages_crawled"] == 20, name
        # What ended the crawl is the count it was allowed, and that count was
        # settled before the first request. Nothing this run timed is in it.
        assert plan["page_cap"] == 20 and "max-pages" in plan["cap_source"]

    first = _paths(runs["fast"])
    for name in ("slow", "varying"):
        assert _paths(runs[name]) == first, (
            "two runs against one unchanged site read different pages, which is how "
            "one site produced 53 pages and 16 findings and then 60 and 19")


def test_a_crawl_that_read_all_it_was_allowed_did_not_exhaust_its_budget(tmp_path):
    """The second observation, which is the first one seen sideways.

    "Both runs report `budget_exhausted: true` against a 142 s budget they
    never spent." They had not spent it: `budget_exhausted` was written as
    `budget_exhausted or queue`, so it was true of every crawl that reached its
    page ceiling with addresses still waiting - which is every crawl of a site
    larger than the ceiling. The fact those two readers downstream actually
    want is that pages were left unread, and that is `stopped_early`.
    """
    snapshot = _crawl_latent_site(
        lambda served: 0.02, 12, str(tmp_path / "capped.json"), max_pages=20)
    crawl = snapshot["crawl"]

    assert crawl["elapsed_s"] < 12, "this run did spend its budget"
    assert crawl["budget_exhausted"] is False, (
        "a crawl that used {:.1f}s of a 12s budget reports it as exhausted".format(
            crawl["elapsed_s"]))
    assert crawl["stopped_early"] is True, (
        "the crawl left 20 pages of this site unread and does not say so")
    assert crawl["stopped_by"] == "page ceiling"


def test_the_page_count_is_settled_before_the_first_request(tmp_path):
    """Nothing this crawl times may decide how much of the site it reads.

    The arithmetic still exists, and has one caller: a `Crawl-delay` the site
    publishes in robots.txt. That number is declared rather than measured, so
    dividing it into the budget reads the same on every run - which is the
    property the whole of this section is about, and the reason the measured
    version of the same arithmetic was removed.
    """
    sys.path.insert(0, SCRIPTS)
    import crawl as crawl_module

    # Rounded up, never down, so a count cannot promise more pages than the
    # budget pays for.
    assert crawl_module.cost_rung(0.3) == 0.5
    assert crawl_module.cost_rung(0.5) == 0.5
    assert crawl_module.cost_rung(4000) == crawl_module.PAGE_COST_RUNGS[-1]
    # A site asking for 4s between requests, against a 240s budget: 45 pages.
    assert crawl_module.pages_a_budget_buys(240, 4.0, 60) == 45
    # A site that asks for nothing is bounded by the page ceiling.
    assert crawl_module.pages_a_budget_buys(240, 0.4, 60) == 60
    # Never zero: a crawl that reads one page still says something.
    assert crawl_module.pages_a_budget_buys(1, 300, 60) == 1

    # And the crawl module no longer holds a way of turning an elapsed time
    # into a page count. `middle_value` was the median of the first five pages'
    # measured cost; it existed for that one purpose and its return value
    # decided how much of a site a report was about.
    assert not hasattr(crawl_module, "middle_value"), (
        "the median of a set of measured page costs is back in crawl.py, which "
        "is how a page count starts depending on the network again")
    assert not hasattr(crawl_module, "PLAN_AFTER_PAGES")


def test_the_queue_can_be_read_without_being_served():
    """`peek` is how a truncated crawl names the address it stopped before."""
    sys.path.insert(0, SCRIPTS)
    from crawl import KindSpreadQueue

    queue = KindSpreadQueue()
    assert queue.peek() is None
    queue.append(("http://kestrel.test/notes/one", 1, "bfs"))
    queue.append(("http://kestrel.test/notes/two", 1, "bfs"))
    assert queue.peek() == queue.popleft()[0], "peek served a different URL"
    assert len(queue) == 1, "peek must not consume anything"


def test_a_crawl_the_clock_still_cuts_says_so_and_names_where_it_stopped(tmp_path):
    """The honest half. A site too slow to read inside its budget really does
    hand back a page set this minute's network chose, and no arithmetic can
    change that - the count that used to hide it was itself measured. What the
    snapshot may not do is stay quiet about it."""
    snapshot = _crawl_latent_site(
        lambda served: 0.02 if served < 8 else 1.2, 6,
        str(tmp_path / "cut.json"), max_pages=40)
    crawl = snapshot["crawl"]
    plan = crawl["page_plan"]

    assert crawl["stopped_by"] == "wall clock"
    # The one state in which this may be true, and the whole of it.
    assert crawl["budget_exhausted"] is True
    assert plan["deterministic"] is False, (
        "a crawl the clock cut short must not claim its page set repeats")
    assert plan["not_reproducible_because"], (
        "the snapshot says the page set does not repeat and does not say why")
    assert any("wall clock" in reason for reason in plan["not_reproducible_because"])
    assert plan["truncated_at"], "the snapshot does not say where the crawl stopped"
    assert plan["truncated_at"] not in [p["url"] for p in snapshot["pages"]], (
        "truncated_at must name the address the crawl stopped *before*")
    stopped = [n for n in crawl["notes"] if "wall-clock budget" in n]
    assert stopped, "no note tells the reader the crawl was cut short"
    assert plan["truncated_at"] in stopped[0], (
        "the note must name the address, so two truncated runs can be compared")
    assert "--budget" in stopped[0] and "--max-pages" in stopped[0], (
        "the note must tell the reader what to do about it, with a number they "
        "can pass")


# --------------------------------------------------------------------------
# Crawl budget and safety
# --------------------------------------------------------------------------

def test_crawl_stays_within_budget(audit):
    for name in FIXTURE_NAMES:
        crawl = audit(name).snapshot["crawl"]
        assert crawl["pages_crawled"] <= crawl["max_pages"], name
        assert crawl["seed"] == 42, "the sampling seed must be fixed for reproducibility"
        assert crawl["user_agent"].startswith("BrandAIReadinessAudit/")
        assert crawl["respect_robots"] is True


def test_crawl_never_touches_a_forbidden_path(audit):
    """No cart, checkout, login, admin or account URL may be fetched."""
    forbidden = ("/cart", "/checkout", "/login", "/signin", "/account", "/admin",
                 "/wp-admin", "/logout", "/register", "/basket")
    for page in [p for n in FIXTURE_NAMES for p in audit(n).snapshot["pages"]]:
        lowered = page["url"].lower()
        assert not any(part in lowered for part in forbidden), \
            "crawler fetched a forbidden path: {}".format(page["url"])


def test_robots_disallowed_paths_are_not_crawled(audit):
    """blocked-site disallows /guides and /products/trade-only for all agents."""
    result = audit("blocked-site")
    for page in result.snapshot["pages"]:
        assert "/guides" not in page["url"]
        assert "/trade-only" not in page["url"]


def test_only_get_and_head_are_used(audit):
    """The fixture server logs every request; none may be a write method.

    The read-only promise is the one a site owner is asked to trust before
    pointing this at production. It used to be asserted by looking at the
    fixture server class for `do_POST`, which was true of the class whatever
    the crawler did; this reads the server's own log of what arrived.
    """
    result = audit("good-site")
    methods = {entry["method"] for entry in result.server.request_log}
    assert methods, "the fixture server logged no requests at all"
    assert methods <= {"GET", "HEAD"},         "the audit sent a write method: {}".format(sorted(methods - {"GET", "HEAD"}))


def test_audit_completes_well_inside_the_five_minute_limit(audit):
    """The contest allows 5 minutes for a typical site."""
    for name in FIXTURE_NAMES:
        elapsed = audit(name).snapshot["crawl"]["elapsed_s"]
        assert elapsed < 60, "{} crawl took {}s".format(name, elapsed)


# --------------------------------------------------------------------------
# Reports a person reads
# --------------------------------------------------------------------------

def test_markdown_report_is_complete(audit):
    for name in FIXTURE_NAMES:
        _assert_markdown_complete(audit(name))


def _assert_markdown_complete(result):
    markdown = result.report_md
    assert markdown.startswith("# AI readiness audit:")
    assert "## The short version" in markdown
    assert "## Appendix: what was checked" in markdown
    assert result.report["summary"]["verdict"] in markdown

    for finding in result.report["findings"]:
        assert finding["id"] in markdown, "{} missing from report.md".format(finding["id"])
    for recommendation in result.report["recommendations"]:
        assert recommendation["title"] in markdown

    if result.report["not_applicable"]:
        assert "Checks that did not apply" in markdown


def test_html_report_renders(audit, tmp_path):
    result = audit("blocked-site")
    findings = [os.path.join(result.out_dir, "{}.findings.json".format(s))
                for s in SUB_SKILLS]
    html_path = str(tmp_path / "report.html")
    run_script([os.path.join(SCRIPTS, "compose_report.py"),
                "--snapshot", result.snapshot_path, "--findings"] + findings +
               ["--out-json", str(tmp_path / "r.json"),
                "--out-md", str(tmp_path / "r.md"),
                "--out-html", html_path,
                "--audited-at", REFERENCE_TIMESTAMP], "compose_report.py --out-html")
    with open(html_path, encoding="utf-8") as handle:
        html = handle.read()
    assert html.startswith("<!doctype html>")
    assert "prefers-color-scheme" in html, "the HTML report should work in dark mode"
    for finding in result.report["findings"]:
        assert finding["id"] in html


# --------------------------------------------------------------------------
# The end-to-end runner
# --------------------------------------------------------------------------

def test_run_audit_end_to_end(tmp_path):
    """Drive run_audit.py exactly as a user would, as a subprocess."""
    with FixtureServer(os.path.join(ROOT, "tests", "fixtures", "no-schema-site")) as server:
        out_dir = str(tmp_path / "out")
        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, os.path.join(ROOT, "run_audit.py"), server.base_url,
             "--out-dir", out_dir, "--format", "both", "--budget", "60",
             "--now", REFERENCE_DATE],
            cwd=ROOT, capture_output=True, text=True)
        elapsed = time.monotonic() - started

    assert completed.returncode == 0, \
        "run_audit.py failed:\nstdout:\n{}\nstderr:\n{}".format(
            completed.stdout, completed.stderr)
    assert elapsed < 300, "run took {}s, over the 5 minute limit".format(elapsed)

    for filename in ("snapshot.json", "report.json", "report.md", "report.html"):
        path = os.path.join(out_dir, filename)
        assert os.path.isfile(path), "run_audit.py did not write {}".format(filename)
        assert os.path.getsize(path) > 0

    # Intermediates are cleaned up unless --keep-intermediates was passed.
    leftovers = [f for f in os.listdir(out_dir) if f.endswith(".findings.json")]
    assert not leftovers, "intermediate findings files were left behind: {}".format(leftovers)

    with open(os.path.join(out_dir, "report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    for key in REQUIRED_TOP_LEVEL:
        assert key in report
    assert report["findings"], "the no-schema fixture should produce findings"


def test_run_audit_keeps_intermediates_when_asked(tmp_path):
    with FixtureServer(os.path.join(ROOT, "tests", "fixtures", "good-site")) as server:
        out_dir = str(tmp_path / "kept")
        completed = subprocess.run(
            [sys.executable, os.path.join(ROOT, "run_audit.py"), server.base_url,
             "--out-dir", out_dir, "--budget", "60", "--no-network",
             "--keep-intermediates", "--now", REFERENCE_DATE],
            cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    kept = sorted(f for f in os.listdir(out_dir) if f.endswith(".findings.json"))
    assert len(kept) == len(SUB_SKILLS), "expected one findings file per sub-skill, got {}".format(kept)


def test_unreachable_site_fails_cleanly(tmp_path):
    """A dead host must produce a report, not a traceback."""
    out_dir = str(tmp_path / "dead")
    completed = subprocess.run(
        [sys.executable, os.path.join(ROOT, "run_audit.py"),
         "http://127.0.0.1:9", "--out-dir", out_dir, "--budget", "20",
         "--no-network", "--now", REFERENCE_DATE],
        cwd=ROOT, capture_output=True, text=True)

    assert completed.returncode == 0, \
        "an unreachable site should be reported, not crash:\n{}".format(completed.stderr)
    with open(os.path.join(out_dir, "report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["summary"]["critical"] >= 1, \
        "an unreachable homepage should be a critical finding"
