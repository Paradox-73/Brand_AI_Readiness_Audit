"""The five-minute ceiling, and the phase that was not counted against it.

`--budget` bounded the crawl. It bounded nothing after the crawl, and the
phase after the crawl also makes requests: up to 40 link probes, 16 sitemap
and redirect probes, 10 profile checks and 2 Wikidata lookups. Each can sit
on a `REQUEST_TIMEOUT` before failing, so a slow or half-dead host turns 68
requests into ten minutes of waiting that nothing was measuring. One real run
crawled inside its budget and still finished at 496 seconds against an audit
that tells the reader it finishes inside 300.

These tests hold two separate claims:

  * the orchestrator hands each network sub-skill what is *left* of the run,
    not a fixed slice, so a slow crawl shrinks the probe phase rather than
    adding to it;
  * a sub-skill given no time reports its targets as unchecked instead of
    calling them broken, which is the difference between an honest gap and a
    false accusation.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time


from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import RUN_WALL_CLOCK_LIMIT, WALL_CLOCK_BUDGET, Fetcher  # noqa: E402

ROOT = os.path.dirname(SCRIPTS.rstrip(os.sep).rstrip("scripts").rstrip(os.sep))
ROOT = os.path.abspath(os.path.join(SCRIPTS, "..", "..", ".."))
NETWORK_SKILLS = ("crawl-access-audit", "freshness-corroboration-audit",
                  "engagement-audit")


def _load(skill):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("check_" + skill.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_whole_run_has_a_ceiling_not_just_the_crawl():
    """The number the audit promises has to bound the thing it describes."""
    assert RUN_WALL_CLOCK_LIMIT < 300, (
        "the run ceiling must leave the five-minute claim true")
    assert RUN_WALL_CLOCK_LIMIT > WALL_CLOCK_BUDGET, (
        "the run ceiling must leave the crawl its full budget")


def test_a_time_budget_becomes_a_deadline():
    """Every skill that makes requests has to turn the budget the orchestrator
    hands it into a wall-clock deadline, or the ceiling bounds nothing."""
    # One helper, shared. Each of these skills carried its own byte-identical
    # `_deadline`; they now import `deadline_from_budget` from the shared
    # library, and this test checks both that the property holds and that the
    # skill really is reading the shared one rather than a fourth copy.
    for skill in NETWORK_SKILLS:
        module = _load(skill)
        assert not hasattr(module, "_deadline"), (
            "{}: a local copy of the deadline helper is back".format(skill))
        assert module.deadline_from_budget(None) is None, "no budget means no ceiling"
        soon = module.deadline_from_budget(5.0)
        assert 0 < soon - time.monotonic() <= 5.0, skill
        assert module.deadline_from_budget(-10.0) <= time.monotonic(), (
            "{}: a budget already spent must not read as time remaining".format(skill))


def test_composing_the_report_is_reserved_for_before_the_ceiling():
    """The ceiling was not a ceiling: compose ran on top of it.

    The probe phase was handed every second up to the limit and used them,
    then the report was composed afterwards - two real runs finished at 289.2
    and 289.7 seconds against a 285-second design target that exists so the
    five-minute promise holds.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_audit_reserve", os.path.join(ROOT, "run_audit.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.COMPOSE_RESERVE_SECONDS > 0
    assert RUN_WALL_CLOCK_LIMIT - module.COMPOSE_RESERVE_SECONDS > WALL_CLOCK_BUDGET, (
        "the reserve must not eat into the crawl's own budget")


def test_only_the_three_that_make_requests_are_given_a_deadline():
    """The other three read the snapshot and cannot overrun anything."""
    spec = importlib.util.spec_from_file_location(
        "run_audit", os.path.join(ROOT, "run_audit.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.NETWORK_SUB_SKILLS == set(NETWORK_SKILLS)
    assert module.NETWORK_SUB_SKILLS <= set(module.SUB_SKILLS)


def test_an_exhausted_clock_stops_a_fetcher_before_it_opens_a_socket():
    """Not merely slower - it must refuse, so a dead host costs nothing."""
    fetcher = Fetcher(max_requests=10, deadline=time.monotonic() - 1)
    assert fetcher.time_left is False
    assert fetcher.try_get("https://example.com/") is None, (
        "an optional probe past the deadline must return None, not raise")
    assert fetcher.count == 0, "no request may be counted, or attempted"
