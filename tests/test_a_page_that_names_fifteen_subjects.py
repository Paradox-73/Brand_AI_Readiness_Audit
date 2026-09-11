# -*- coding: utf-8 -*-
"""Fifteen `<h1>` elements on one homepage, and nothing said a word.

They were counted by hand. The only heading check that existed reports a
page with *zero* H1s; its own comment records why it stops there - reporting
more than one fired it on 27 of 29 real sites, because two or three H1s are
ordinary HTML5 sectioning and stop no machine reading the page.

Fifteen is not sectioning. Fifteen tells a machine that fifteen different
things are the subject of the page, which is the same defect as none at all
seen from the other side. This file pins the count, the floor that keeps
sectioning out of it, and the two ways a count of one heading used to look
like a count of many.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGAGEMENT = _module("engagement-audit", "engagement_for_h1_flood_tests")
FACTS = _module("fact-extractability-audit", "facts_for_h1_flood_tests")

SITE = "https://an-invented-host.test"
CHECK = "too-many-top-level-headings"


def _page(path, headings, in_markup=None, page_type="other"):
    return {"url": SITE + path, "status": 200, "page_type": page_type,
            "headings": {"h1": list(headings)},
            "headings_in_markup": {"h1": list(headings if in_markup is None
                                              else in_markup)}}


def _run(pages):
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_one_subject_per_page(result, pages)
    return result


def _reason(result):
    return " ".join(entry["reason"] for entry in result.not_applicable
                    if entry["check"] == CHECK)


def test_a_homepage_with_fifteen_h1s_is_reported():
    home = _page("/", ["Tile {}".format(n) for n in range(15)], page_type="home")
    result = _run([home, _page("/about", ["About us"])])

    finding = next(f for f in result.findings
                   if f["id_hint"] == "page-declares-many-subjects")
    assert "15 distinct top-level headings" in finding["evidence"]
    assert finding["affected_pages"] == [SITE + "/"]


def test_the_homepage_is_named_as_the_page_with_one_subject():
    """The site's front page has exactly one subject - the site - so fifteen
    answers there is the worst version of this, and the reader is told which
    page they are looking at."""
    home = _page("/", ["Tile {}".format(n) for n in range(15)], page_type="home")
    finding = _run([home])
    assert "This is the site's homepage" in finding.findings[0]["evidence"]


def test_sectioning_is_not_a_defect():
    """Two or three H1s are valid HTML5 sectioning. Reporting them is the
    behaviour that fired the zero-H1 check on 27 of 29 real sites, and this
    check exists only because that one was right to stop."""
    result = _run([_page("/", ["Overview", "Pricing", "Support"], page_type="home")])
    assert not result.findings
    assert "ordinary HTML5 sectioning" in _reason(result)


def test_a_heading_carried_in_both_readings_of_the_page_counts_once():
    """`_h1s_of` reads the headings twice on purpose - once from the visible
    document, once from the whole markup - because a theme's screen-reader-only
    H1 is still an H1. A count that did not fold them would double every
    page."""
    twelve = ["Section {}".format(n) for n in range(12)]
    result = _run([_page("/", twelve[:6], in_markup=twelve[:6], page_type="home")])
    assert not result.findings, "six headings read twice are six headings"


def test_a_carousel_repeating_one_heading_is_one_subject():
    """A slider ships its heading once per slide. Twelve copies of one string
    is a page with one subject, and counting elements rather than subjects
    would have reported it."""
    result = _run([_page("/", ["Autumn sale"] * 12,
                         in_markup=["Autumn sale"] * 13, page_type="home")])
    assert not result.findings
    assert "1 distinct heading" in _reason(result)


def test_the_page_with_no_h1_belongs_to_the_other_skill():
    """The two checks answer opposite halves of one question and must not both
    fire on one page. `heading-hierarchy` in `fact-extractability-audit` owns
    the page with none; this one owns the page with too many."""
    empty = _page("/", [], page_type="home")
    assert not _run([empty]).findings

    facts = SkillResult("fact-extractability-audit")
    FACTS._check_heading_hierarchy(facts, [empty])
    assert any(f["root_cause"] == "heading-structure" for f in facts.findings)
