# -*- coding: utf-8 -*-
"""The defect this project keeps making: code written, tested, called by nothing.

Three times, each found in a real report rather than by the suite that covered
it:

* `publishing_platform` was written and unit-tested and no skill ever called
  it - the worst defect in the tree at the time.
* `buddhist_era_dates`, `gregorian_year` and `reads_in_a_buddhist_era_calendar`
  repeated it, and the consequence showed up on two sites - Thai and Japanese
  pages recorded as dateless with their dates in the report's own citation
  table.
* `prose_blocks` and `reads_as_source_not_prose` were added to the extractor
  *for* the definition check and the identity sentence, and neither had a
  caller outside the extractor and its own tests.

A unit test cannot see this. It passes on the function, and the function works;
what is missing is the call. So this file tests the wiring, by name, in both
directions: every shared reader has a caller in the skill that needed it, and
every consumer still reads the field the extractor built for it.

Adding a reader here is the point of the file. If one is deleted on purpose,
delete its row and the test goes with it.
"""

from __future__ import annotations

import io
import os

import pytest

from conftest import ROOT

# (the name, the file that must call it, why it exists)
WIRED = [
    ("publishing_platform",
     "skills/structured-data-audit/scripts/check.py",
     "names the platform whose template the fix has to be made in"),
    ("publishing_platform",
     "skills/engagement-audit/scripts/check.py",
     "the same, for the engagement fixes"),
    ("non_gregorian_dates",
     "skills/audit-orchestrator/scripts/page_extract.py",
     "reads a date in a calendar that is not the Gregorian one"),
    ("hijri_dates",
     "skills/audit-orchestrator/scripts/audit_common.py",
     "both Hijri calendars, called by non_gregorian_dates"),
    ("prose_blocks",
     "skills/fact-extractability-audit/scripts/check.py",
     "the definition in a line the site repeats, which the boilerplate strip "
     "removes from body_text"),
    ("reads_as_source_not_prose",
     "skills/audit-orchestrator/scripts/compose_report.py",
     "stops a fragment of leaked script becoming \"Its homepage says\""),
    ("reads_as_source_not_prose",
     "skills/fact-extractability-audit/scripts/check.py",
     "stops a fragment of leaked script becoming the definition sentence"),
    ("isolate_right_to_left_runs",
     "skills/audit-orchestrator/scripts/compose_report.py",
     "keeps the punctuation around an Arabic or Hebrew quotation where it was "
     "written"),
    ("length_floor_for",
     "skills/render-readability-audit/scripts/check.py",
     "states the thin-text bar in the characters the page is written in"),
    ("english_equivalent_length",
     "skills/audit-orchestrator/scripts/audit_common.py",
     "the unit every text-length threshold here was measured in"),
]


def _source(relative):
    return io.open(os.path.join(ROOT, relative), encoding="utf-8").read()


@pytest.mark.parametrize("name,consumer,why", WIRED,
                         ids=["{}->{}".format(n, os.path.basename(os.path.dirname(
                             os.path.dirname(c)))) for n, c, _ in WIRED])
def test_the_reader_has_a_caller(name, consumer, why):
    """Twice in the file: once where it arrives, once where it is used."""
    source = _source(consumer)
    assert source.count(name) >= 2, (
        "{} is imported into {} and never called. It exists to {}, and a "
        "reader nothing calls is this project's most repeated defect."
        .format(name, consumer, why))


def test_the_field_the_extractor_builds_is_the_field_the_check_reads():
    """`prose_blocks` survives the crawl's site-wide boilerplate strip only
    because it is not one of the names that strip rewrites. If the strip ever
    learns the name, the field goes back to being `body_text` with more code
    around it and the tagline disappears again.
    """
    crawl = _source("skills/audit-orchestrator/scripts/crawl.py")
    stripped = crawl.split("_strip_sitewide_boilerplate", 1)[1][:4000]
    assert "prose_blocks" not in stripped, (
        "the boilerplate strip now rewrites prose_blocks, which is the one "
        "thing that field exists to avoid")
    assert '"prose_blocks"' in _source(
        "skills/audit-orchestrator/scripts/page_extract.py")


def test_every_calendar_reader_is_reached_from_the_one_entry_point():
    """`non_gregorian_dates` is the single door, so a caller cannot wire in
    half the calendars - which is how the Buddhist-era readers sat unused
    while `thai_era_dates` beside them was called."""
    common = _source("skills/audit-orchestrator/scripts/audit_common.py")
    body = common.split("def non_gregorian_dates", 1)[1].split("\ndef ", 1)[0]
    for reader in ("buddhist_era_dates", "thai_era_dates", "japanese_era_dates",
                   "hijri_dates"):
        assert reader in body, (
            "{} is not reached from non_gregorian_dates, so a page in that "
            "calendar is reported as carrying no date".format(reader))
