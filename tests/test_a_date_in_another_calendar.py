# -*- coding: utf-8 -*-
"""A date the page prints in a calendar that is not the Gregorian one.

The same defect showed up on two different sites:

    "no date on the page" fired on a page carrying 28 สิงหาคม 2569 and three
    more Buddhist-era dates in 54,990 characters

    14 Thai pages printing พ.ศ. 2569 recorded as dateless, and the report
    quotes one of those dates in its own citation table two paragraphs above
    the finding saying there are none

The readers were not missing. `audit_common.buddhist_era_dates`,
`gregorian_year` and `reads_in_a_buddhist_era_calendar` had been written and
tested, and **nothing called them** - while `page_extract`'s own
`DATE_TEXT_RE` anchored every year on `(?:19|20)\\d{2}`, so the date was never
captured as a candidate and there was nothing for a reader to convert.

So this file tests the wiring rather than the arithmetic: that the extractor
captures these shapes, that it converts them through the shared readers, that
`has_any` says the page has a date, and - the direction that matters more -
that a four-digit number in the same range on a page with no Thai anywhere is
left alone. A wrongly converted date is worse than an unread one.

Every host and organisation below is invented.
"""

from __future__ import annotations

import sys

import pytest
from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    hijri_dates, japanese_era_dates, non_gregorian_dates, thai_era_dates,
)
from page_extract import DATE_TEXT_RE, extract_page  # noqa: E402

SITE = "https://sarawadee-school.test"

# "Announcement, dated 28 August 2569 BE, the school will ..." - the shape the
# real page printed, in the script it was printed in.
THAI_DATED = (
    u"ประกาศ เมื่อวั"
    u"นที่ 28 สิงหาคม 2569 "
    u"ทางโรงเรียนได"
    u"้จัดกิจกรรมสำ"
    u"หรับนักเรียน"
)
THAI_ERA_ABBREVIATION = u"พ.ศ. 2569"       # พ.ศ. 2569


def _page(html, path="/news/1", lang="th"):
    html = ("<html lang=\"{lang}\"><head><title>News</title></head>"
            "<body><main>{body}</main></body></html>").format(lang=lang, body=html)
    return extract_page(url=SITE + path, final_url=SITE + path, status=200,
                        headers={"content-type": "text/html"}, html=html,
                        redirect_chain=[], elapsed_ms=11, depth=1,
                        source="link", origin=SITE)


# --------------------------------------------------------------------------
# The shape has to be captured before any reader can convert it
# --------------------------------------------------------------------------

@pytest.mark.parametrize("printed", [
    u"28 สิงหาคม 2569",   # 28 August 2569 BE
    u"พ.ศ. 2569",                                   # พ.ศ. 2569
    u"31/03/2563",                                            # d/m/BE
    u"令和8年4月6日",                     # 令和8年4月6日
    u"平成30年",                                  # 平成30年
])
def test_the_pattern_captures_a_year_that_is_not_nineteen_or_twenty_hundred(printed):
    """`DATE_TEXT_RE` anchored every alternative on `(?:19|20)\\d{2}`, so none
    of these was a candidate at all and no reader downstream could see one."""
    assert DATE_TEXT_RE.search(printed), printed


# --------------------------------------------------------------------------
# The readers that existed are now called
# --------------------------------------------------------------------------

def test_a_thai_page_is_a_page_with_a_date_on_it():
    """The published finding, in the extractor's own record: `has_any: false` on
    a page printing a date, which is what "48 of 48 pages where one is
    expected show no date on the page" was built out of."""
    page = _page("<h1>Announcement</h1><p>" + THAI_DATED + "</p>")
    assert page["dates"]["has_any"] is True
    assert any(u"2569" in value for value in page["dates"]["visible"])


def test_the_year_it_states_in_the_common_era_is_recorded_beside_it():
    """Kept beside the printed string rather than replacing it: `visible` is
    quoted in reports as what the page prints, and a converted date is a
    string no page carries."""
    page = _page("<h1>Announcement</h1><p>" + THAI_DATED + "</p>")
    read = page["dates"]["non_gregorian"]
    assert read and read[0]["gregorian_year"] == 2026, read
    assert u"2569" in read[0]["printed"]


def test_the_era_abbreviation_on_its_own_is_a_date():
    page = _page("<h1>Announcement</h1><p>" + THAI_DATED + " " +
                 THAI_ERA_ABBREVIATION + "</p>")
    years = {entry["gregorian_year"] for entry in page["dates"]["non_gregorian"]}
    assert years == {2026}


def test_a_japanese_era_year_is_read_from_the_era_name_in_the_string():
    """No language evidence is needed or consulted: the era is in the match.
    令和1年 is 2019, so 令和8年 is 2026."""
    page = _page(u"<h1>お知らせ</h1>"
                 u"<p>更新日 令和8年4月6日</p>",
                 lang="ja")
    assert page["dates"]["has_any"] is True
    assert page["dates"]["non_gregorian"][0]["gregorian_year"] == 2026


# --------------------------------------------------------------------------
# The direction that costs more to get wrong
# --------------------------------------------------------------------------

def test_the_same_four_digits_on_a_page_with_no_thai_are_left_alone():
    """`31/03/2563` in a Latin-script language is not a date, and subtracting
    543 from it would publish an invented year as the site's own. The page's
    own script or its declared language has to say which calendar it is in."""
    page = _page("<h1>Order 2563</h1><p>Batch 31/03/2563 shipped from the "
                 "warehouse this week and the rest follow next Monday.</p>",
                 path="/orders", lang="en")
    assert page["dates"]["non_gregorian"] == []
    assert page["dates"]["visible"] == []
    assert page["dates"]["has_any"] is False


def test_a_bare_number_in_the_window_is_never_a_year():
    """Nothing here reads a four-digit number on its own. It takes a Thai word
    or the era abbreviation in front of it, or an era name around it."""
    assert thai_era_dates(u"2569") == []
    assert thai_era_dates(u"Model 2569 mixer") == []
    assert japanese_era_dates(u"2569") == []
    assert non_gregorian_dates(u"2569", script="thai") == []


def test_a_gregorian_date_is_not_touched_by_any_of_this():
    page = _page("<h1>Notice</h1><p>Published 6 April 2015 by the office.</p>",
                 path="/notice", lang="en")
    assert page["dates"]["visible"] == ["6 April 2015"]
    assert page["dates"]["non_gregorian"] == []
    assert page["dates"]["has_any"] is True


# --------------------------------------------------------------------------
# The failure this is an instance of: a reader nothing calls
# --------------------------------------------------------------------------

def test_every_reader_in_the_library_has_a_caller():
    """`publishing_platform` was written, tested and called by nothing, the
    worst defect in the tree at the time; these three readers then repeated
    it. The test is the wiring, so it fails if the call site is deleted while
    the readers stay.
    """
    import io
    import os
    extractor = io.open(os.path.join(SCRIPTS, "page_extract.py"),
                        encoding="utf-8").read()
    for reader in ("non_gregorian_dates", "reads_in_a_buddhist_era_calendar",
                   "BUDDHIST_ERA_DATE_RE", "THAI_ERA_YEAR_RE",
                   "JAPANESE_ERA_DATE_RE"):
        assert extractor.count(reader) >= 2, (
            reader + " is imported and never used, which is the defect this "
            "file exists for")


# --------------------------------------------------------------------------
# The Hijri calendar, which returned nothing for every shape
# --------------------------------------------------------------------------
#
# Before this, Hijri was not implemented at all: `non_gregorian_dates` on an
# Arabic news page returned [], and a Hijri-dated page was reported as carrying
# no date. Three of the four shapes that page used are here, plus the numeric
# one that the first attempt at the reader still missed, because it read the
# mark only where it sits straight on the year.

HIJRI_FILLER = (
    u"\u0627\u0641\u062a\u062a\u062d \u0627\u0644\u0645\u0639\u0631\u0636 "
    u"\u0623\u0628\u0648\u0627\u0628\u0647 \u0644\u0644\u0632\u0648\u0627\u0631 "
    u"\u0645\u0646 \u0627\u0644\u0633\u0627\u0639\u0629 \u0627\u0644\u062a\u0627"
    u"\u0633\u0639\u0629 \u0635\u0628\u0627\u062d\u0627 \u062d\u062a\u0649 "
    u"\u0627\u0644\u0645\u0633\u0627\u0621 \u0648\u064a\u0633\u062a\u0645\u0631 "
    u"\u0623\u0633\u0628\u0648\u0639\u0627 \u0643\u0627\u0645\u0644\u0627."
)


@pytest.mark.parametrize("printed", [
    u"\u0627\u0644\u062e\u0645\u064a\u0633 15 \u0631\u0628\u064a\u0639 "
    u"\u0627\u0644\u0623\u0648\u0644 1447\u0647\u0640",   # the mark on the year
    u"1447/03/15 \u0647\u0640",                            # the mark after a full date
    u"1447.03.15 \u0647\u0640",                            # the same with dots
    u"\u0661\u0664\u0664\u0667\u0647\u0640",               # Arabic-Indic digits
    u"1447 \u0647\u062c\u0631\u064a",                      # the word, spelled out
])
def test_a_marked_hijri_year_is_read_as_the_gregorian_one_it_starts_in(printed):
    """1447 AH begins in 2025 CE. The reader returns the year and nothing
    finer: a Hijri year is 354 days, so it straddles two Gregorian ones and
    the month a conversion formula would produce is not a fact this audit
    has."""
    read = hijri_dates(printed)
    assert read, printed
    assert read[0][1] == 2025, read


@pytest.mark.parametrize("printed", [
    u"1447",                                    # a bare number is never a year
    u"1447 Mill Road",                          # a street address
    u"1200\u0647\u0640",                        # 1882 CE, outside the window
    u"1447\u0647\u0630\u0627",                  # the letter inside a word
])
def test_a_number_the_page_did_not_mark_as_hijri_is_left_alone(printed):
    """The direction that costs more to get wrong. Four digits in this range
    are a product code, a price and a house number more often than a year, so
    the page has to have printed the mark itself."""
    assert hijri_dates(printed) == [], printed


def test_an_arabic_page_carrying_a_hijri_date_is_not_reported_as_dateless():
    """The whole point of the fix: the extractor has to reach the reader. It
    does not go through `DATE_TEXT_RE`, which anchors on Latin digits, so this
    fails if the call in `non_gregorian_dates` is removed."""
    dated = (u"\u0646\u0634\u0631 \u0628\u062a\u0627\u0631\u064a\u062e "
             u"1447/03/15 \u0647\u0640 ")
    page = _page(u"<h1>\u062e\u0628\u0631</h1><p>" + dated + HIJRI_FILLER +
                 u"</p>", path="/news/2", lang="ar")
    assert page["dates"]["has_any"] is True
    assert page["dates"]["non_gregorian"][0]["gregorian_year"] == 2025


def test_the_other_two_calendars_still_read_after_hijri_was_added():
    """Hijri joined `thai_era_dates` and `japanese_era_dates` in one
    expression, and a mistake there would silence both."""
    assert non_gregorian_dates(u"28 \u0e2a\u0e34\u0e07\u0e2b\u0e32\u0e04\u0e21 "
                               u"2569", script="thai")[0][1] == 2026
    assert non_gregorian_dates(u"\u4ee4\u548c8\u5e744\u67086\u65e5",
                               script="japanese")[0][1] == 2026


# --------------------------------------------------------------------------
# The other Hijri calendar, and the other set of digits
# --------------------------------------------------------------------------
#
# Two faults found while checking the reader above, and both are failures to
# generalize: the marketplace read the shapes it was built against and got an
# unfamiliar one confidently wrong.
#
#   * `1404 \u0647\u0640.\u0634` is *shamsi*, the solar Hijri calendar Iran and Afghanistan
#     date everything in. It shares a year number with the lunar calendar and
#     stands 42 years away from it, so reading the first two characters of the
#     mark and stopping converted March 2025 to 1983 and published it.
#   * `\u06f0-\u06f9` is the Extended Arabic-Indic digit set - the one Persian, Dari,
#     Pashto and Urdu pages actually print. The reader mapped only
#     `\u0660-\u0669`, so `\u06f1\u06f4\u06f0\u06f4` was not a number at all while `\u0661\u0664\u0660\u0664` was.


@pytest.mark.parametrize("printed", [
    u"1404 \u0647\u0640.\u0634",                                  # he-tatweel, stop, shin
    u"\u06f1\u06f4\u06f0\u06f4 \u0647\u0640.\u0634",              # the same in Persian digits
    u"\u06f1\u06f4\u06f0\u06f4/\u06f0\u06f6/\u06f1\u06f8 \u0647\u0640.\u0634",   # a full solar date
    u"1404 \u0647\u062c\u0631\u06cc \u0634\u0645\u0633\u06cc",    # spelled out, Persian yeh
    u"1404 \u0634\u0645\u0633\u06cc",                             # shamsi on its own
])
def test_a_solar_hijri_year_is_its_gregorian_year_plus_621(printed):
    """Jalali 1404 begins at the March 2025 equinox. `+ 621` is the Gregorian
    year it starts in, which is the same promise the lunar reader makes and
    the same reason neither returns a month."""
    read = hijri_dates(printed)
    assert read, printed
    assert read[0][1] == 2025, read


def test_the_solar_mark_is_never_converted_with_the_lunar_formula():
    """The defect this pair of readers exists for. Both marks open with the
    same two characters, and stopping there is a 42-year error published as
    the page's own date - which the module's rule calls worse than an unread
    one."""
    solar = hijri_dates(u"1404 \u0647\u0640.\u0634")
    lunar = hijri_dates(u"1404 \u0647\u0640.\u0642")
    assert solar[0][1] == 2025
    assert lunar[0][1] == 1983
    assert solar[0][1] != lunar[0][1], (
        "the two calendars were read with one formula")


@pytest.mark.parametrize("printed,expected", [
    (u"\u0661\u0664\u0664\u0667\u0647\u0640", 2025),   # Arabic-Indic
    (u"\u06f1\u06f4\u06f4\u06f7\u0647\u0640", 2025),   # Extended Arabic-Indic
    (u"1447\u0647\u0640", 2025),                       # ASCII
])
def test_all_three_digit_sets_read_the_same_year(printed, expected):
    """One letter, three codepoints, and the same for the digits. A reader
    covering one set is a reader that works on Arabic sites and silently
    returns nothing on Iranian ones."""
    read = hijri_dates(printed)
    assert read and read[0][1] == expected, (printed, read)


def test_a_persian_year_with_no_mark_is_still_left_alone():
    """The guard has to hold in the new digit set too, or every four-digit
    Persian product code becomes a date."""
    assert hijri_dates(u"\u06f1\u06f4\u06f0\u06f4") == []
    assert hijri_dates(u"\u06f1\u06f4\u06f0\u06f4 \u062e\u06cc\u0627\u0628\u0627\u0646") == []
