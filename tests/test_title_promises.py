# -*- coding: utf-8 -*-
"""When "this page does not deliver what its title promises" is itself untrue.

The check reads a page's `<title>`, drops the site name and the stopwords, and
looks for the remaining words in the page's body text and headings. Under a
third of them present is reported as drift. Two batches of real reports show the
two ways that instrument breaks, and each produced a finding stated as a fact
about words a reader could see on the page.

  a new-arrivals grid titled for the two     "missing" both of them: the grid's
  kinds of goods it lists                    own words are the item names, so
                                             the category words are in the
                                             page's job rather than its prose

  a homepage whose title is the company      three of four title words called
  tagline, in a language that marks          missing while their stems were all
  grammar by adding a syllable to the        in the first paragraph
  end of a word

The second is a measurement failure rather than a threshold wanting adjustment.
The match keeps a title word's first six characters so an inflected form still
counts - "reports" found inside "reporting". Where one character spells a whole
syllable, six characters is longer than most whole words, the window trims
nothing, and the test quietly becomes an exact match on the surface form. The
title writes a noun with one particle and the sentence writes it with another,
so an exact match fails on words the page does deliver. No shorter window
recovers it either: two syllables of a three-syllable stem matches unrelated
words.

The check still has to fire where it is right. A page titled for one thing that
delivers another is the defect this check exists to find, and the last section
here is that one.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    dominant_script, is_listing_page, SkillResult, words_are_separated,
)


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGAGEMENT = _module("engagement-audit", "engagement_for_title_promise_tests")

HOST = "https://an-invented-host.test"


def _page(path, title, body, headings=None, links=None, page_type=None):
    return {
        "url": HOST + path,
        "title": title,
        "body_text": body,
        "body_text_len": max(len(body), 400),
        "headings": headings or {},
        "links": links or {"internal": [], "nav": [], "footer": []},
        "page_type": page_type,
        "jsonld_types": [],
        "paragraphs": [],
    }


def _run(pages):
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_title_body_drift(result, pages)
    reasons = [entry["reason"] for entry in result.not_applicable
               if entry["check"] == "title-body-alignment"]
    return result.findings, (reasons[0] if reasons else "")


def _delivering_pages():
    """Three pages that deliver their titles, so the check has the three it
    needs before anything else in a test becomes the thing under test."""
    return [
        _page("/about", "About our workshop",
              "Our workshop in the valley has made shoes and leather goods since 1974. " * 8),
        _page("/care", "Leather care guide",
              "This leather care guide explains how to clean and condition leather. " * 8),
        _page("/returns", "Returns and exchanges",
              "Returns and exchanges are accepted for thirty days after delivery. " * 8),
    ]


def _drifting_page():
    """A title promising a thing the page never mentions. The real defect."""
    return _page("/warranty", "Lifetime warranty registration",
                 "Our workshop in the valley has made shoes and leather goods since 1974. "
                 "Visit us on weekdays between nine and five. " * 8)


_ITEMS = ["Aster Runner", "Bay Loafer", "Cove Tote", "Dune Sandal",
          "Elm Satchel", "Fern Slipper", "Glen Clutch", "Holt Boot"]


def _listing_page():
    """A grid whose title names two categories and whose words are item names."""
    return _page(
        "/new-arrivals", "New Arrivals Footwear & Bags",
        " ".join(_ITEMS) + " Sort by price or by newest first. " * 6,
        headings={"h2": list(_ITEMS)},
        links={"internal": [{"url": "{}/item/{}".format(HOST, index), "text": name}
                            for index, name in enumerate(_ITEMS)],
               "nav": [], "footer": []},
    )


# --------------------------------------------------------------------------
# A title naming a category, on the page that lists that category
# --------------------------------------------------------------------------

def test_a_listing_page_titled_for_what_it_lists_is_not_drift():
    """The candidate list carried no page-type test at all, so the category
    words were called missing from the one page whose job is to list them."""
    listing = _listing_page()
    assert is_listing_page(listing), "the fixture is not being read as a listing page"
    findings, _ = _run([listing] + _delivering_pages())
    assert not findings


def test_the_decline_says_the_listing_pages_were_left_out():
    """A pass stated over pages that were never compared is a claim about text
    the check did not read."""
    _, reason = _run([_listing_page()] + _delivering_pages())
    assert "list of other pages" in reason
    # "1 page(s) ... were left out" is a singular count spliced into a clause
    # written for several, and an independent review marked it as an error in
    # the published report. The count and the clause are one `plural` phrase now.
    assert "1 page whose content is a list of other pages was left out" in reason


def test_a_page_set_aside_does_not_pad_the_count_the_evidence_prints():
    findings, _ = _run([_drifting_page(), _listing_page()] + _delivering_pages())
    assert len(findings) == 1
    assert "Of 4 page(s) compared" in findings[0]["evidence"]
    assert "list of other pages" in findings[0]["evidence"]


def test_the_exemption_is_keyed_on_being_a_list_not_on_the_title():
    """A page with prose of its own and no list of linked headings goes through
    the comparison unchanged, whatever its title says."""
    page = _page("/new-arrivals-story", "New Arrivals Footwear & Bags",
                 "Our workshop in the valley has made shoes since 1974. "
                 "Visit us on weekdays between nine and five. " * 8)
    assert not is_listing_page(page)
    findings, _ = _run([page] + _delivering_pages())
    assert len(findings) == 1
    assert findings[0]["affected_pages"] == [HOST + "/new-arrivals-story"]


# --------------------------------------------------------------------------
# One character, one syllable
# --------------------------------------------------------------------------

# A tagline whose four content words all clear the four-letter floor, beside a
# body that delivers three of them with different grammatical endings. The
# stems are all in the body's first sentence; the whole surface forms are not.
_TAGLINE = "함께하는 새로움을 만들어가는 라이프스타일"
_TAGLINE_BODY = (
    "저희는 고객과 항상 함께합니다. "
    "매일의 새로움이 일상을 바꿉니다. "
    "좀 더 나은 물건을 만들어 갑니다. "
    "우리의 라이프스타일은 단순함에서 시작합니다. "
) * 8


def _syllabic_pages():
    return [
        _page("/", _TAGLINE, _TAGLINE_BODY),
        _page("/story", "브랜드 이야기",
              "오랫동안 같은 생각을 지켜 "
              "왔습니다. 재료를 고르는 "
              "기준은 달라지지 않았습니다. " * 8),
        _page("/guide", "제품 안내서",
              "제품을 고르는 방법을 "
              "안내합니다. 크기와 색상은 "
              "매장에서 확인해 주세요. " * 8),
        _page("/stores", "매장 찾기",
              "가까운 매장을 찾아보세요. "
              "영업시간은 지점마다 다릅니다. " * 8),
    ]


def test_the_gate_has_to_read_the_body_because_a_title_is_too_short_to_type():
    """`dominant_script` names no script below 20 letters and a title does not
    have them, so a gate asked of the title answers "ordinary spaced text" for
    every short non-Latin title there is. That is why the check reads the body.
    """
    assert dominant_script(_TAGLINE) is None
    assert words_are_separated(_TAGLINE) is True
    assert dominant_script(_TAGLINE_BODY) == "hangul"
    # And the shared list of scripts that write no space between one word and
    # the next does not cover this one, because this script does write spaces.
    # The defect is not spacing, so that predicate cannot be the gate.
    assert words_are_separated(_TAGLINE_BODY) is True


def test_a_body_that_spells_a_syllable_per_character_is_not_compared():
    findings, reason = _run(_syllabic_pages())
    assert not findings
    assert "whole syllable in one character" in reason
    assert "grammatical ending" in reason


def test_the_words_it_used_to_call_missing_are_on_the_page():
    """Three of the four title words have their stems in the body. Reporting
    them absent was not a weak claim, it was a false one."""
    for stem in ("함께", "새로움", "만들어"):
        assert stem in _TAGLINE_BODY


def test_one_unmeasurable_page_among_ordinary_ones_is_set_aside_not_reported():
    """Sites mix languages. The page that cannot be measured drops out of the
    population; the pages that can are judged as before."""
    findings, _ = _run([_page("/", _TAGLINE, _TAGLINE_BODY), _drifting_page()]
                       + _delivering_pages())
    assert len(findings) == 1
    assert findings[0]["affected_pages"] == [HOST + "/warranty"]
    assert "whole syllable in one character" in findings[0]["evidence"]


# --------------------------------------------------------------------------
# And it still fires where it is right
# --------------------------------------------------------------------------

def test_a_page_that_does_not_deliver_its_title_is_still_reported():
    drifting = _drifting_page()
    findings, _ = _run([drifting] + _delivering_pages())
    assert len(findings) == 1
    assert findings[0]["root_cause"] == "title-body-drift"
    assert drifting["url"] in findings[0]["evidence"]


def test_every_word_the_finding_calls_missing_really_is_missing():
    """The evidence prints the words that are absent, not the first few of the
    title alphabetically. A reader who checks has to find them absent."""
    drifting = _drifting_page()
    findings, _ = _run([drifting] + _delivering_pages())
    named = findings[0]["evidence"].split("missing ", 1)[1].rstrip(").").split(", ")
    assert named
    for word in named:
        assert word not in drifting["body_text"].lower()
