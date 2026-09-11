# -*- coding: utf-8 -*-
"""Two promises the tool makes about itself, held by arithmetic rather than luck.

The clock. The audit tells its reader it finishes inside five minutes and holds
itself to 285 seconds internally. The caps it ran under permitted 660: the crawl
was killed at `--budget + 120` (360 s) by a limit computed after the crawl had
already returned; each of six sub-skills was floored at 30 s of kill-timeout
even when the clock was already spent (180 s more); and composing the report got
a flat 120 s on top of all of it. Three of eight runs on ordinary sites finished
within thirty seconds of 300.

The three caps are now derived from the ceiling and add back up to it, so the
worst case is a property of the constants rather than of how the sites behaved.
The first test recomputes that sum; the second runs the real `main()` against a
fake clock in which every phase spends every second it is handed, which is the
only way to catch a formula that stops matching the constants.

The coverage rule. "Only N of the M URLs the crawl reached could be read - the
rest were refused or challenged" was printed on a crawl of sixteen URLs where
every one answered HTTP 200 and nothing was refused: nine of them were release
archives and a patch file, counted as pages the crawler had been shut out of.
That put the readable share under the bar and silenced four true findings about
the seven pages that had been read. Both halves - a denominator of URLs that
could have been pages, and a clause counting the reasons the crawl recorded -
already hold in the report composer. These tests hold them in the skill that
asks the same question about the same snapshot.

Every host in this file is invented and every address is fictional.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    RUN_WALL_CLOCK_LIMIT, SUB_SKILLS, WALL_CLOCK_BUDGET,
)

# The promise in the README and in the report's own appendix. The internal
# ceiling exists to keep it true, so the ceiling is the thing under test and
# this is what the ceiling is for.
FIVE_MINUTES = 300.0


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _runner():
    return _load("run_audit_for_clock_tests", os.path.join(ROOT, "run_audit.py"))


# --------------------------------------------------------------------------
# The arithmetic, read off the constants
# --------------------------------------------------------------------------

def test_the_three_caps_add_up_to_the_ceiling_they_are_taken_from():
    """The bound is the sum, so the sum is what a regression would break.

    Before: 360 for the crawl, 6 x 30 for the sub-skills, 120 for compose -
    660 seconds under a tool that advertises 285. Each of the three was chosen
    on its own and none of them knew about the other two.

    The sub-skills' share used to be 6 x MIN_STEP_SECONDS here, which is the
    two seconds each needs to read a snapshot and write a file - and nothing
    like the ninety the three that make requests need to make any. So the crawl
    was free to spend the probing, and on a slow site it did. The share is now
    `PROBE_PHASE_RESERVE_SECONDS`, which is what those six phases actually
    cost, and the crawl gets what is left.
    """
    module = _runner()
    worst = module.PROBE_PHASE_RESERVE_SECONDS + module.COMPOSE_RESERVE_SECONDS
    crawl_ceiling = RUN_WALL_CLOCK_LIMIT - worst
    assert crawl_ceiling + worst == RUN_WALL_CLOCK_LIMIT
    assert RUN_WALL_CLOCK_LIMIT < FIVE_MINUTES, \
        "the internal ceiling has to leave the five-minute claim true"
    # The reserve is a floor the six skills are guaranteed, not what they
    # usually spend - on a site that answers quickly they take about fifteen
    # seconds between them - so it is allowed to be the larger of the two
    # numbers. What it may not do is squeeze the crawl below a sample worth
    # analysing, and `test_the_run_degrades` states that bound in pages.
    assert crawl_ceiling > RUN_WALL_CLOCK_LIMIT / 3, \
        "the reserve has taken so much that the crawl cannot read a sample"
    assert WALL_CLOCK_BUDGET > crawl_ceiling, (
        "`--budget` is now always clamped by the ceiling, so it is a cap the caller "
        "may lower rather than a number the crawl is ever given in full")


def test_the_floor_a_spent_clock_leaves_is_smaller_than_the_one_it_replaces():
    """30 s per skill was a floor under a clock that was already gone.

    A sub-skill reaching that floor has been switched to `--no-network`: it
    reads the snapshot and writes a JSON file, which is under a second. The
    floor must stay well under the threshold that switches the network off, or
    the two numbers are saying different things about the same moment.
    """
    module = _runner()
    assert 0 < module.MIN_STEP_SECONDS < module.MIN_SUB_SKILL_SECONDS


# --------------------------------------------------------------------------
# The arithmetic, run
# --------------------------------------------------------------------------

class _Clock(object):
    """A monotonic clock that only moves when a phase spends time."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


def _worst_case(module, tmp_path, extra=()):
    """Run `main()` with every phase spending every second it is handed.

    The point of driving the real function rather than re-deriving the sum: a
    ceiling computed after the phase it is meant to bound looks correct in
    isolation and bounds nothing, which is exactly how the crawl came to be
    killed at 360 seconds under a 285-second limit.
    """
    clock = _Clock()
    phases = []

    def fake_run_step(command, label, timeout=None):
        assert timeout is not None, "{} was run with no ceiling".format(label)
        phases.append({"label": label, "timeout": timeout, "command": list(command)})
        clock.now += timeout
        return timeout

    module.time = clock
    module.run_step = fake_run_step
    argv = ["https://an-invented-host.test", "--out-dir", str(tmp_path),
            "--no-render"] + list(extra)
    assert module.main(argv) == 0
    return clock.now, phases


def test_no_run_can_reach_the_ceiling_however_slow_every_phase_is(tmp_path):
    """Every phase wedges and spends its whole cap. 660 s before; 285 now."""
    module = _runner()
    total, phases = _worst_case(module, tmp_path)
    assert [p["label"] for p in phases] == \
        ["crawl"] + list(SUB_SKILLS) + ["compose_report"]
    assert total <= RUN_WALL_CLOCK_LIMIT, \
        "worst case is {:.0f}s against a ceiling of {:.0f}s".format(
            total, RUN_WALL_CLOCK_LIMIT)
    assert total < FIVE_MINUTES


def test_a_budget_the_caller_raised_moves_the_ceiling_with_it(tmp_path):
    """A caller who asks for a longer crawl has chosen a longer run. The bound
    still has to be a bound - proportional to what they asked for, not absent."""
    module = _runner()
    total, _ = _worst_case(module, tmp_path, extra=["--budget", "400"])
    assert total <= 400 * 1.2


def test_a_slow_crawl_still_leaves_every_later_phase_something(tmp_path):
    """The goal is a bound, not a shorter audit.

    A crawl that spends its whole ceiling is the case where the old floors were
    invented, and cutting them to nothing would trade going over the clock for
    producing no report at all. Every sub-skill still gets a floor and the
    report still gets its reserve.

    This used to also assert that the crawl's kill-timeout sat above
    `WALL_CLOCK_BUDGET`, on the reasoning that the crawl must keep its whole
    advertised budget. That is the arrangement a real run exposed: the crawl
    kept 240 s of headroom, spent it, and two of the six sub-skills were left
    with nothing - one switched off before it started and one stopped at 85 s.
    The kill-timeout now sits at the boundary of the reserve held for those
    six, below `WALL_CLOCK_BUDGET`, and what it must stay above is the budget
    the crawl is actually told to spend.
    """
    module = _runner()
    _, phases = _worst_case(module, tmp_path)
    crawl = phases[0]
    told = float(crawl["command"][crawl["command"].index("--budget") + 1])
    assert crawl["timeout"] > told, \
        "the crawl's kill-timeout must sit above the budget it is given"
    for phase in phases[1:-1]:
        assert phase["timeout"] >= module.MIN_STEP_SECONDS, phase["label"]
    assert phases[-1]["timeout"] >= module.COMPOSE_RESERVE_SECONDS


def test_a_skill_is_never_told_it_has_time_it_will_be_killed_for(tmp_path):
    """One number, two uses. The advisory budget handed down and the ceiling
    the parent enforces were computed separately, and the advisory one was the
    larger - so a skill could plan requests into seconds it was never going to
    be allowed to spend, and be stopped mid-probe for using them."""
    module = _runner()
    _, phases = _worst_case(module, tmp_path)
    seen = 0
    for phase in phases:
        if "--time-budget" not in phase["command"]:
            continue
        seen += 1
        told = float(phase["command"][phase["command"].index("--time-budget") + 1])
        assert told <= phase["timeout"] + 0.05, phase["label"]
    assert seen == len(module.NETWORK_SUB_SKILLS)


# --------------------------------------------------------------------------
# What the coverage rule divides by, in the skill that asks it second
# --------------------------------------------------------------------------

FACTS = _load("fact_extractability_for_coverage_tests",
              os.path.join(ROOT, "skills", "fact-extractability-audit",
                           "scripts", "check.py"))

SITE = "https://an-invented-toolkit.test"

# Enough text, a heading and a recognised type, so these are pages every filter
# in the skill agrees are pages. Nothing here states a price, a location, a
# founding fact or a contact route: the core-facts check is meant to fire.
_BODY = ("This project publishes its releases and documents how to use them. "
         "Each release note explains what changed and why it changed. ") * 6


def _page(path, page_type="about"):
    url = SITE + path
    return {"url": url, "final_url": url, "status": 200, "page_type": page_type,
            "body_text": _BODY, "body_text_len": len(_BODY), "text": _BODY,
            "text_len": len(_BODY), "word_count": len(_BODY.split()),
            "headings": {"h1": ["The toolkit"], "h2": []},
            "heading_sequence": ["h1"], "title": "The toolkit",
            "lang": "en", "html_len": len(_BODY) + 200}


def _archive(path):
    """A 2xx whose own content type proved it was never a page."""
    url = SITE + path
    return {"url": url, "final_url": url, "status": 200, "page_type": "other",
            "not_a_page": True, "skipped": "non-HTML content type",
            "content_type": "application/x-bzip2", "body_text": "",
            "body_text_len": 0, "headings": {"h1": []}}


def _refused(path, status=503):
    url = SITE + path
    return {"url": url, "final_url": url, "status": status, "page_type": "other",
            "body_text": "", "body_text_len": 0, "headings": {"h1": []}}


def _snapshot(pages):
    return {"origin": SITE, "site": "an invented toolkit", "pages": pages,
            "brand": {"name": "Toolkit"},
            "crawl": {"pages_crawled": len(pages), "max_pages": 60},
            "sitemaps": [], "robots": {"status": 404}}


def _skip_reason(result, check):
    return next((note["reason"] for note in result.not_applicable
                 if note["check"] == check), None)


def test_a_download_is_not_a_page_the_crawler_was_refused():
    """Seven pages read and nine archives reached is a crawl that read every
    page it found. Counting the archives put it at 7 of 16, under the coverage
    bar, and the four core-fact findings were replaced by a skip."""
    snapshot = _snapshot([_page("/p%d" % i) for i in range(7)]
                         + [_archive("/release-%d.tar.bz2" % i) for i in range(9)])
    result = FACTS.run(snapshot)
    assert _skip_reason(result, "core-facts-present") is None, \
        "the gate fired on a crawl that read every page it found"
    assert [f for f in result.findings if f["root_cause"] == "missing-core-fact"], \
        "the findings the gate was suppressing did not come back"


def test_a_url_that_was_refused_stays_in_the_denominator():
    """The bar exists to catch exactly this. Dropping refusals to fix the
    downloads would disarm the guard that stops the audit telling a site it
    never states a fact, on pages it was never allowed to open."""
    snapshot = _snapshot([_page("/p")] + [_refused("/q%d" % i, 403) for i in range(9)])
    reason = _skip_reason(FACTS.run(snapshot), "core-facts-present")
    assert reason and "1 of the 10" in reason


def test_the_skip_sentence_counts_what_the_crawl_recorded():
    """It asserted a refusal and a challenge with no test for either. On the
    crawl that produced the complaint, all sixteen URLs answered 200."""
    snapshot = _snapshot([_page("/p")] + [_refused("/q%d" % i, 503) for i in range(9)])
    reason = _skip_reason(FACTS.run(snapshot), "core-facts-present")
    assert "9 answered HTTP 503" in reason
    assert "refused" not in reason and "challenge" not in reason


def test_a_run_that_read_nothing_names_no_status_nobody_sent():
    """The whole-skill skip said "no content pages returned HTTP 200" about a
    crawl where every URL had returned exactly that and every body was a
    download. A reason that names a cause has to have counted one.

    Two shapes reach it and they are not the same answer: a crawl that found no
    page-shaped URL at all, and a crawl that found them and could read none.
    """
    only_downloads = _snapshot([_archive("/release-%d.tar.bz2" % i) for i in range(4)])
    reason = _skip_reason(FACTS.run(only_downloads), "core-facts-present")
    assert reason and "HTTP 200" not in reason
    assert reason == "the crawl reached no page of this site"

    challenged = _snapshot([dict(_page("/p%d" % i), challenge=True) for i in range(4)])
    reason = _skip_reason(FACTS.run(challenged), "core-facts-present")
    assert "4 answered with a bot-manager verification page" in reason
    assert "HTTP 200" not in reason


def test_a_homepage_that_was_reached_is_not_reported_as_never_crawled():
    """"neither a home nor an about page was crawled" was tested against a list
    already filtered by status, by challenge and by language - so a homepage
    served as a bot-manager screen came back as a page the crawl never asked
    for, which points the reader at their sitemap instead of at the block."""
    snapshot = _snapshot([dict(_page("/", page_type="home"), challenge=True),
                          _page("/notes", page_type="other")])
    reason = _skip_reason(FACTS.run(snapshot), "entity-definition")
    assert reason and "was crawled" not in reason
    assert "bot-manager verification page" in reason

    absent = _snapshot([_page("/notes", page_type="other")])
    assert _skip_reason(FACTS.run(absent), "entity-definition") == \
        "neither a home nor an about page was crawled"


def test_the_scale_a_finding_states_counts_only_pages():
    """"Examined 7 of the 16 pages this crawl read" described nine downloads as
    pages. The number a finding gives for how much it examined is the number a
    reader weighs it by."""
    snapshot = _snapshot([_page("/p%d" % i) for i in range(7)]
                         + [_archive("/release-%d.tar.bz2" % i) for i in range(9)])
    result = FACTS.run(snapshot)
    scales = [f["evidence"] for f in result.findings
              if "page(s) this audit read" in f["evidence"]
              or "pages this crawl read" in f["evidence"]]
    assert scales, "no finding stated the scale it was read from"
    for evidence in scales:
        assert "of 16" not in evidence and "of the 16" not in evidence


def test_the_sample_share_is_not_diluted_by_downloads():
    """`_sample_share` decides whether a title may say "never" and whether the
    severity stands. It sized the site from every record the crawl wrote, so
    nine downloads made a fully-read site look 44% read and every finding here
    came out one step milder and scoped to the sample."""
    pages = [_page("/p%d" % i) for i in range(7)]
    snapshot = _snapshot(pages + [_archive("/release-%d.tar.bz2" % i) for i in range(9)])
    assert FACTS._sample_share(snapshot, len(pages)) == 1.0
