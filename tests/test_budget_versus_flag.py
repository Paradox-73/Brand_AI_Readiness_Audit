# -*- coding: utf-8 -*-
"""One definition of the threshold, and one of the sentence it decides.

A sub-skill that makes no extra requests is told only that its network is off.
It is told that in exactly the same way whether the caller passed
`--no-network` or the run simply had no wall clock left, so the flag cannot
tell the two apart and a report that names the flag in the second case tells
the reader they chose something they did not choose.

What separates them is the time budget the orchestrator hands down: a caller
who asks for no network still leaves the run its whole budget, while a run that
has spent its budget is handed a slice below the threshold at which the
orchestrator switches the network off for it. The inference is only sound while
both ends read the same threshold, so these tests assert that there is one
number and one implementation rather than copies that agree today.

Every host in this file is invented and every address is fictional.
"""

from __future__ import annotations

import importlib.util
import io
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

import audit_common  # noqa: E402
from audit_common import (  # noqa: E402
    NO_TIME_LEFT_SECONDS, no_network_reason, SkillResult,
)


def _module(skill, name):
    scripts = os.path.join(ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGAGEMENT = _module("engagement-audit", "engagement_for_budget_tests")
ACCESS = _module("crawl-access-audit", "access_for_budget_tests")
FRESHNESS = _module("freshness-corroboration-audit", "freshness_for_budget_tests")

SITE = "https://an-invented-shop.test"

# A site that asked to be read slowly, and a crawl that obeyed until the clock
# ran out. This is the shape of snapshot the misleading sentence was printed on.
SLOW_ROBOTS = {"status": 200,
               "groups": [{"agents": ["*"], "allow": [], "disallow": [],
                           "crawl_delay": 10}]}


def _spent_snapshot():
    return {"origin": SITE, "site": "an invented shop", "pages": [],
            "robots": SLOW_ROBOTS,
            "crawl": {"stopped_by": "wall clock", "elapsed_s": 241.0,
                      "budget_exhausted": True}}


def _reason(result, check):
    return next(n["reason"] for n in result.not_applicable if n["check"] == check)


def _read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


# --------------------------------------------------------------------------
# One implementation, not three that agree
# --------------------------------------------------------------------------

def test_every_skill_calls_the_same_function_object():
    """Three byte-similar copies of this helper existed and a fourth skill went
    on naming the flag, which is what a copy is for. The skills now hold a
    reference to the shared library's function rather than one of their own."""
    for module in (ENGAGEMENT, ACCESS, FRESHNESS):
        assert module.no_network_reason is no_network_reason, module.__name__


def test_no_skill_keeps_a_private_copy_of_the_helper_or_the_threshold():
    """A local `def no_network_reason` or `NO_TIME_LEFT_SECONDS = ...` in a
    skill is a copy that can drift out of step with the orchestrator's
    decision, which is the defect, not the sentence it prints."""
    offenders = []
    skills = os.path.join(ROOT, "skills")
    for skill in sorted(os.listdir(skills)):
        path = os.path.join(skills, skill, "scripts", "check.py")
        if not os.path.isfile(path):
            continue
        source = _read(path)
        for pattern in (r"^def no_network_reason\b",
                        r"^def _where_the_clock_went\b",
                        r"^NO_TIME_LEFT_SECONDS\s*="):
            if re.search(pattern, source, re.M):
                offenders.append("{}: {}".format(skill, pattern))
    assert not offenders, "private copies of a shared helper:\n" + "\n".join(offenders)


def test_the_threshold_is_written_once():
    """`run_audit.py` decides with it and the sub-skills read it back. Two
    literals agreeing today is not one definition."""
    run_audit = _read(os.path.join(ROOT, "run_audit.py"))
    assert "MIN_SUB_SKILL_SECONDS = NO_TIME_LEFT_SECONDS" in run_audit
    assert not re.search(r"^MIN_SUB_SKILL_SECONDS\s*=\s*[0-9]", run_audit, re.M)

    common = _read(os.path.join(SCRIPTS, "audit_common.py"))
    assert len(re.findall(r"^NO_TIME_LEFT_SECONDS\s*=\s*[0-9]", common, re.M)) == 1


def test_the_orchestrator_decides_with_the_number_the_skills_read_back():
    sys.path.insert(0, ROOT)
    spec = importlib.util.spec_from_file_location(
        "run_audit_for_budget_tests", os.path.join(ROOT, "run_audit.py"))
    run_audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_audit)
    assert run_audit.MIN_SUB_SKILL_SECONDS == NO_TIME_LEFT_SECONDS


# --------------------------------------------------------------------------
# The two reasons the shared helper keeps apart
# --------------------------------------------------------------------------

def test_a_snapshot_that_is_missing_entirely_does_not_raise():
    """The two copies had drifted here: one read `snapshot["crawl"]` through a
    bare `.get`, so a caller with no snapshot to hand crashed the check it was
    trying to explain. The tolerant version is the one that was kept."""
    reason = no_network_reason(
        None, time_budget=4.0,
        flag_reason="extra network requests were disabled for this run",
        exhausted_reason="nothing was probed")
    assert "spent clock" in reason
    assert "Where the time went" not in reason


def test_a_spent_clock_names_the_slow_crawl_the_site_asked_for():
    reason = no_network_reason(
        _spent_snapshot(), time_budget=NO_TIME_LEFT_SECONDS - 1,
        flag_reason="extra network requests were disabled for this run",
        exhausted_reason="nothing was probed")
    assert "10s between requests" in reason
    assert "stopped by the clock after 241s" in reason
    assert "--no-network" in reason and "may not have been passed" in reason


def test_the_threshold_itself_still_counts_as_a_budget_the_caller_had():
    """Exactly at the threshold the orchestrator would still have handed out a
    network budget, so the flag came from the caller."""
    reason = no_network_reason(
        _spent_snapshot(), time_budget=NO_TIME_LEFT_SECONDS,
        flag_reason="extra network requests were disabled for this run",
        exhausted_reason="nothing was probed")
    assert reason.endswith("(--no-network)")


# --------------------------------------------------------------------------
# The third skill, which went on naming the flag
# --------------------------------------------------------------------------

def _freshness_skip(check, time_budget):
    result = SkillResult("freshness-corroboration-audit")
    snapshot = _spent_snapshot()
    if check == "entity-ambiguity":
        FRESHNESS._check_entity_ambiguity(
            result, snapshot, [], {"name": "An Invented Shop"}, {}, None, False,
            time_budget=time_budget)
    else:
        FRESHNESS._check_profile_links_resolve(
            result, {"LinkedIn": "https://linkedin.com/company/an-invented-shop"},
            None, False, snapshot=snapshot, time_budget=time_budget)
    return _reason(result, check)


def test_freshness_skips_blame_the_clock_when_the_clock_is_what_ran_out():
    """Both sentences named a flag nobody passed: "this run was told not to
    make extra requests" and "external lookups were disabled for this run"."""
    for check in ("entity-ambiguity", "profile-links-resolve"):
        reason = _freshness_skip(check, time_budget=4.0)
        assert "spent clock" in reason, check
        assert "10s between requests" in reason, check
        assert "disabled for this run" not in reason, check
        assert "told not to make extra requests" not in reason, check


def test_freshness_still_names_the_flag_when_the_caller_passed_it():
    """The correction must not swing the other way. A run with its whole budget
    left that makes no requests makes none because it was asked not to."""
    for check in ("entity-ambiguity", "profile-links-resolve"):
        reason = _freshness_skip(check, time_budget=180.0)
        assert reason.endswith("(--no-network)"), check
        assert "spent clock" not in reason, check


def test_freshness_run_on_its_own_from_the_command_line_names_the_flag():
    """No ceiling was imposed, which is what a direct run of one skill gets:
    the flag is the only explanation left."""
    for check in ("entity-ambiguity", "profile-links-resolve"):
        assert _freshness_skip(check, time_budget=None).endswith("(--no-network)"), check


def test_no_check_script_still_hard_codes_the_misleading_sentence():
    """The sentence is only ever correct as a `flag_reason` the helper may
    discard; written straight into a `result.skip` it is the original defect."""
    offenders = []
    skills = os.path.join(ROOT, "skills")
    for skill in sorted(os.listdir(skills)):
        path = os.path.join(skills, skill, "scripts", "check.py")
        if not os.path.isfile(path):
            continue
        source = _read(path)
        for match in re.finditer(
                r"result\.skip\(\s*\"[a-z0-9-]+\",\s*\n?\s*\"[^\"]*"
                r"(?:disabled for this run|told not to make extra requests)", source):
            offenders.append("{}: offset {}".format(skill, match.start()))
    assert not offenders, ("a skip reason names the flag unconditionally:\n"
                           + "\n".join(offenders))
