# -*- coding: utf-8 -*-
"""A finding that asserts a negative has to say where it looked.

Every false positive this marketplace has produced on a real site reduces to
one move: a detector did not find something, and the report said the site does
not have it.

  a breadcrumb named only in a `data-` attribute      28 pages, all wrong
  a call to action that is a `<button>`               "no call-to-action link
                                                      was found anywhere"
  search offered as a link rather than a form         "the site has no on-site
                                                      search"
  an FAQ with no markup                               "no FAQ pages were
                                                      detected", beside a
                                                      recommendation to write
                                                      one
  a maintainer named in the third person              "no named team detail
                                                      appears in the page text"
  a definition written with a verb other than "to be" "no page states in one
                                                      sentence what the brand is"

In each case the page had the thing and the detector had a finite list of
shapes. No detector can be proved complete, so the rule is not "be sure": it is
that a claim of absence names what it looked at, and may not be stated at high
confidence on the word of one detector.

These tests hold that line for every check in the marketplace, including ones
written after this file.
"""

from __future__ import annotations

import ast
import os

import pytest

from conftest import ROOT, SUB_SKILLS
from audit_common import (
    ABSENCE_CAUSES, CORROBORATED_ABSENCE_SOURCES, make_finding, name_appears_in,
    names_match, ROOT_CAUSES,
)

SKILLS_DIR = os.path.join(ROOT, "skills")

_FINDING = dict(
    id_hint="x", title="t", severity="high", confidence="high", evidence="e",
    mechanism="C", summary="s", how_to_fix=["step"], effort="low",
    rationale="r", owner="developer",
)


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------

def test_an_absence_claim_without_a_basis_is_refused():
    """And the reverse: `non-200` carries its own evidence, the status code, so
    a `checked` list there would be padding - and padding in an evidence field
    is worse than nothing."""
    with pytest.raises(ValueError) as raised:
        make_finding(root_cause="no-breadcrumbs", **_FINDING)
    assert "checked" in str(raised.value)

    with pytest.raises(ValueError):
        make_finding(root_cause="non-200", checked=("something",), **_FINDING)


def test_how_many_sources_an_absence_claim_names_caps_its_confidence():
    """A single detector reported as high confidence is the shape of every
    false positive above; two independent ones keep it."""
    one = make_finding(root_cause="no-breadcrumbs",
                       checked=("the visible trail on each page",), **_FINDING)
    assert one["confidence"] == "medium"

    two = make_finding(
        root_cause="no-breadcrumbs",
        checked=("the visible trail on each page", "BreadcrumbList markup"), **_FINDING)
    assert two["confidence"] == "high"
    assert len(two["checked"]) >= CORROBORATED_ABSENCE_SOURCES


def test_the_absence_causes_partition_the_vocabulary():
    """If every cause were an absence claim the gate would be a tax rather
    than a test, and if none were it would be dead."""
    assert not ABSENCE_CAUSES - ROOT_CAUSES
    assert ABSENCE_CAUSES
    assert ROOT_CAUSES - ABSENCE_CAUSES


# --------------------------------------------------------------------------
# No check may route around it
# --------------------------------------------------------------------------

def _absence_calls_without_a_basis(path):
    """`result.add(root_cause="no-...")` calls with no `checked=` argument."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "add":
            continue
        names = {k.arg for k in node.keywords if k.arg}
        cause = next((k.value.value for k in node.keywords
                      if k.arg == "root_cause" and isinstance(k.value, ast.Constant)), None)
        if cause in ABSENCE_CAUSES and "checked" not in names:
            offenders.append((cause, node.lineno))
    return offenders


@pytest.mark.parametrize("skill", SUB_SKILLS)
def test_no_skill_asserts_an_absence_without_saying_where_it_looked(skill):
    path = os.path.join(SKILLS_DIR, skill, "scripts", "check.py")
    offenders = _absence_calls_without_a_basis(path)
    assert not offenders, "{}: {}".format(skill, "; ".join(
        "{} at line {}".format(cause, line) for cause, line in offenders))


# --------------------------------------------------------------------------
# One name comparison, not one per check
# --------------------------------------------------------------------------

@pytest.mark.parametrize("declared,candidate,same", [
    # Each row is a real site whose report carried a false positive because
    # one check did not know about this particular variation.
    ("Harrowgate Law LLP", "Harrowgate Law", True),          # legal suffix
    ("Walmgate Indian Restaurants", "Walmgate Indian Restaurant", True),  # plural
    ("Le Havre.fr", "Le Havre", True),                        # domain suffix
    ("The Museum of Modern Craft", "Museum of Modern Craft", True),  # article
    ("charity: shelter", "CHARITY SHELTER", True),            # punctuation and case
    # And the ones that must stay different, or the comparison is useless.
    ("Acme Bakeries", "Acme Breweries", False),
    ("Acme", "", False),
])
def test_the_shared_name_comparison(declared, candidate, same):
    assert names_match(declared, candidate) is same


def test_a_name_split_across_a_title_is_still_on_the_page():
    """A substring test cannot see a name the title breaks up with pipes, and
    that produced 44 conflicts on one site and its only high finding - while a
    name that really is absent still has to come back absent."""
    assert name_appears_in(
        "Walmgate Indian Restaurants",
        "Walmgate | Indian Restaurant Fossgate Mill | South York")
    assert not name_appears_in("Northwind Traders", "Walmgate | Indian Restaurant")
