# -*- coding: utf-8 -*-
"""The crawl budget the documents state is the one the run computes.

The same two numbers - what the crawl is told to spend and what is held back
for the six sub-skills - were written into the README, the orchestrator's
SKILL.md and the crawl-mechanics reference, and they said 115 s and 148 s while
the run printed 142 s and 118 s. Nobody had lied: the step floors changed and
prose does not recompute itself, so each document is now held to the
arithmetic `run_audit.py` does.
"""

from __future__ import annotations

import io
import os
import sys

from conftest import ROOT

sys.path.insert(0, ROOT)

import run_audit  # noqa: E402

CRAWL_TOLD = round(max(0.0, min(
    run_audit.WALL_CLOCK_BUDGET,
    run_audit.RUN_WALL_CLOCK_LIMIT - run_audit.COMPOSE_RESERVE_SECONDS
    - run_audit.PROBE_PHASE_RESERVE_SECONDS - run_audit.CRAWL_TAIL_SECONDS)))
HELD_FOR_SKILLS = round(run_audit.PROBE_PHASE_RESERVE_SECONDS)


def _read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_the_readme_states_the_budget_the_run_computes():
    readme = _read("README.md")
    assert "Clamped to {} s".format(CRAWL_TOLD) in readme
    assert "{} s is held back for the six sub-skills".format(HELD_FOR_SKILLS) in readme


def test_the_orchestrator_tells_an_agent_the_same_budget():
    skill = _read("skills", "audit-orchestrator", "SKILL.md")
    assert "--budget {}".format(CRAWL_TOLD) in skill


def test_the_crawl_mechanics_reference_states_the_same_budget():
    reference = _read("skills", "audit-orchestrator", "references", "crawl-mechanics.md")
    assert "--budget {}".format(CRAWL_TOLD) in reference
    assert "{} s reserve".format(HELD_FOR_SKILLS) in reference
