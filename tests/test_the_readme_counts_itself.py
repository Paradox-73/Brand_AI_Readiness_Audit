# -*- coding: utf-8 -*-
"""The three numbers the README states about the marketplace, derived.

`README.md` opens by saying how many checks the six skills run, how many
distinct findings they draw on and how many root causes those findings name. A
reader meets that paragraph first and can check it, and it has been wrong three
times: 75 checks stated when there were 91, then 91 when there were 93, then 93
when there were 96. Each time the number was counted by hand, and each time the
tree moved underneath it before it shipped.

So the numbers are derived from the code here and the README is held to them.
`counted_from_the_code()` is the whole definition:

* a **check** is a string passed to `SkillResult.check` or `.skip` - the exact
  strings that appear in `checks_run[]` and `not_applicable[]` in a report.
* a **finding** is a distinct `id_hint` a `result.add(...)` can emit. Three
  shapes reach that argument and all three count: a plain literal, a
  conditional (two findings, not none), and a name assigned literals in the
  branches above it. The one family built from a list rather than written out
  is `core-fact-missing-<fact>`, one per entry in `_CORE_FACT_SLOTS`.
* a **root cause** is a member of `ROOT_CAUSES`, and an **absence** cause a
  member of `ABSENCE_CAUSES`, both in `audit_common.py`.

Counting the same way the report does is the point: a number a reader cannot
reproduce from a run is a number they cannot check.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import os
import re
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import ABSENCE_CAUSES, ROOT_CAUSES  # noqa: E402

SKILLS = os.path.join(ROOT, "skills")


def _literals(node):
    """Every string this value expression can evaluate to."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _literals(node.body) + _literals(node.orelse)
    return []


def _core_fact_findings():
    """`core-fact-missing-<fact>`, one per fact the skill looks for.

    Built from `_CORE_FACT_SLOTS` by the same expression the check uses, so
    adding a core fact adds a finding here without anybody remembering to.
    """
    path = os.path.join(SKILLS, "fact-extractability-audit", "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("fx_check_for_counting", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {"core-fact-missing-" + re.sub(r"[^a-z]+", "-", name.lower()).strip("-")
            for name in module._CORE_FACT_SLOTS}


def counted_from_the_code():
    """`(checks, findings, unresolved)` as sets of `(skill, id)` pairs.

    `unresolved` is every `id_hint` argument this function could not read. It
    has to stay empty: an id built in a shape not handled here is a finding
    missing from the count, which is the defect this file exists for.
    """
    checks, findings, unresolved = set(), set(), []
    for skill in sorted(os.listdir(SKILLS)):
        path = os.path.join(SKILLS, skill, "scripts", "check.py")
        if not os.path.exists(path):
            continue
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        assigned = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    for value in _literals(node.value):
                        assigned.setdefault(target.id, set()).add(value)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr in ("check", "skip") and node.args:
                for value in _literals(node.args[0]):
                    checks.add((skill, value))
            if node.func.attr != "add":
                continue
            for keyword in node.keywords:
                if keyword.arg != "id_hint":
                    continue
                direct = _literals(keyword.value)
                if direct:
                    findings.update((skill, value) for value in direct)
                elif isinstance(keyword.value, ast.Name):
                    named = assigned.get(keyword.value.id)
                    if named:
                        findings.update((skill, value) for value in named)
                    else:
                        unresolved.append((skill, keyword.value.lineno))
                elif _is_the_core_fact_family(keyword.value):
                    findings.update(("fact-extractability-audit", value)
                                    for value in _core_fact_findings())
                else:
                    unresolved.append((skill, keyword.value.lineno))
    return checks, findings, unresolved


def _is_the_core_fact_family(node):
    """`"core-fact-missing-{}".format(...)` - the one templated id."""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and isinstance(node.func.value, ast.Constant)
            and str(node.func.value.value).startswith("core-fact-missing-"))


CHECKS, FINDINGS, UNRESOLVED = counted_from_the_code()
README = io.open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()


def test_every_finding_id_was_readable():
    """A shape this counter cannot read is a finding missing from the count,
    and the count is what the README publishes."""
    assert not UNRESOLVED, (
        "id_hint arguments this file could not resolve, so the catalogue count "
        "below is short by at least one: {}".format(UNRESOLVED))


@pytest.mark.parametrize("number,noun", [
    (len(CHECKS), "checks"),
    (len(FINDINGS), "distinct findings"),
    (len(ROOT_CAUSES), "named root causes"),
])
def test_the_readme_states_the_number_the_code_has(number, noun):
    """The sentence a reader meets first, held to the tree it describes."""
    assert "**{} {}**".format(number, noun) in README, (
        "README.md does not say \"**{} {}**\"; the code has {}. Every count "
        "in that paragraph: {} checks, {} findings, {} root causes, {} of them "
        "absences.".format(number, noun, number, len(CHECKS), len(FINDINGS),
                           len(ROOT_CAUSES), len(ABSENCE_CAUSES)))


def test_the_readme_states_how_many_causes_say_where_they_looked():
    """The absence gate is the marketplace's own differentiator, and the
    fraction naming it is a claim a reader can check against `ABSENCE_CAUSES`.
    """
    assert "{} of the {}".format(len(ABSENCE_CAUSES), len(ROOT_CAUSES)) in README, (
        "README.md should say \"{} of the {} root causes\"".format(
            len(ABSENCE_CAUSES), len(ROOT_CAUSES)))


@pytest.mark.parametrize("skill", sorted({skill for skill, _ in CHECKS}))
def test_every_check_id_appears_in_its_skills_reference(skill):
    """Each skill's `references/*-checks.md` carries a register: one entry per
    registered check id. A check added without an entry is a check a reader
    of the reference cannot find, and a reader who checks one claim and finds
    it missing stops trusting the rest.
    """
    folder = os.path.join(SKILLS, skill, "references")
    text = "".join(io.open(os.path.join(folder, name), encoding="utf-8").read()
                   for name in sorted(os.listdir(folder))
                   if name.endswith(".md"))
    missing = sorted(check for owner, check in CHECKS
                     if owner == skill and "`{}`".format(check) not in text)
    assert not missing, (
        "{} registers these check ids and its references never name them: "
        "{}".format(skill, ", ".join(missing)))


def _root_causes_emitted_by(skill):
    path = os.path.join(SKILLS, skill, "scripts", "check.py")
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    causes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add":
            for keyword in node.keywords:
                if keyword.arg == "root_cause":
                    causes.update(_literals(keyword.value))
    return causes


@pytest.mark.parametrize("skill", sorted({skill for skill, _ in CHECKS}))
def test_every_root_cause_a_skill_emits_is_named_in_its_skill_md(skill):
    """Each SKILL.md lists the root causes its findings carry. Two causes were
    added to the code and never to that list, so an agent following the
    instructions by hand could not produce them."""
    text = io.open(os.path.join(SKILLS, skill, "SKILL.md"), encoding="utf-8").read()
    missing = sorted(cause for cause in _root_causes_emitted_by(skill)
                     if "`{}`".format(cause) not in text)
    assert not missing, "{}/SKILL.md never names: {}".format(skill, ", ".join(missing))


FAILURE_MODES = io.open(os.path.join(
    SKILLS, "audit-orchestrator", "references", "round2-failure-modes.md"),
    encoding="utf-8").read()


def test_every_check_id_is_mapped_to_a_failure_mode():
    """The map promises that every registered check falls under one of the
    brief's seven failure signals. Three checks were added after it was
    written and it went on saying 93 while the code had 96."""
    unmapped = sorted(check for _, check in CHECKS
                      if "`{}`".format(check) not in FAILURE_MODES)
    assert not unmapped, (
        "round2-failure-modes.md maps no failure signal for: {}".format(
            ", ".join(unmapped)))


def test_the_failure_mode_headings_add_up_to_the_checks_registered():
    """Each of the seven headings states how many checks sit under it, and
    those seven numbers are a claim a reader can add up."""
    stated = [int(n) for n in re.findall(
        r"^## [1-7]\. .*? — (\d+) checks?$", FAILURE_MODES, re.M)]
    assert len(stated) == 7, stated
    assert sum(stated) == len(CHECKS), (
        "the seven failure-mode headings sum to {}; the code registers {}".format(
            sum(stated), len(CHECKS)))
    assert "every one of the {}\nchecks".format(len(CHECKS)) in FAILURE_MODES or \
        "every one of the {} checks".format(len(CHECKS)) in FAILURE_MODES
