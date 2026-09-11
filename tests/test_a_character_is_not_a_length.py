# -*- coding: utf-8 -*-
"""Three hundred characters of what?

Every text-length threshold in this marketplace was measured on English pages
and then applied to a raw character count. A character is not the same quantity
in every writing system: "People's Republic of China" is 26 characters and
seven in Han. A page holding 150 Han characters holds about what an English
page needs 500 for, and `thin-html` told it - at medium severity, in a finding
whose fix is "give each of these pages at least a paragraph of plain text" - it
carries too little text to be quoted.

Same family as the Hijri reader and the `lg.jp` label, and a failure to
generalize stated as a defect: a rule that is right about the shapes it was
written against and confidently wrong about an unfamiliar one.

The direction matters. Scaling the bar *down* for a dense script can only
remove findings from pages that carry a real paragraph; a page that is
genuinely empty has almost no characters in any script and stays below every
floor. The tests below hold both directions, and hold the Latin path unchanged
- Latin pages are most of every real run, and a generalization fix that moves
them is a regression wearing a fix's clothes.

Every host and organisation below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    CHARACTERS_PER_ENGLISH_CHARACTER, english_equivalent_length, length_floor_for,
)


def _render_module():
    path = os.path.join(ROOT, "skills", "render-readability-audit", "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("render_for_length_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _render_module()

# "The museum holds an exhibition introducing the culture of this region."
JAPANESE = u"当館は地域の文化を紹介する展示を行っております。"
CHINESE = u"本馆举办介绍本地区文化的展览。"
KOREAN = u"본관은 이 지역의 문화를 소개하는 전시를 열고 있습니다."
ENGLISH = ("The museum holds an exhibition introducing the culture of this "
           "region, open every day except Monday. ")
ARABIC = u"يقيم المركز معرضا يعرف بثقافة المنطقة ويفتح أبوابه يوميا. "


# --------------------------------------------------------------------------
# The ratio table itself
# --------------------------------------------------------------------------

def test_only_the_three_dense_scripts_carry_a_ratio():
    """Arabic, Hebrew, Devanagari, Thai, Cyrillic and Greek are alphabets and
    abugidas with word-length runs much like Latin's. Giving them a ratio would
    be inventing a difference to look thorough."""
    assert set(CHARACTERS_PER_ENGLISH_CHARACTER) == {"han", "kana", "hangul"}
    assert all(value > 1.0 for value in CHARACTERS_PER_ENGLISH_CHARACTER.values())


def test_a_latin_page_is_measured_in_the_characters_it_has():
    """The number was measured on these pages, so they must be held to it."""
    text = ENGLISH * 5
    assert english_equivalent_length(text) == len(text)
    assert length_floor_for(text, 300) == 300


@pytest.mark.parametrize("text", [ARABIC * 5, u"Это музей города. " * 8,
                                  u"Το μουσείο της πόλης. " * 8])
def test_the_other_non_latin_scripts_are_not_scaled_either(text):
    assert length_floor_for(text, 300) == 300


@pytest.mark.parametrize("text", [JAPANESE * 4, CHINESE * 5, KOREAN * 3])
def test_a_dense_script_lowers_the_bar_rather_than_the_page(text):
    floor = length_floor_for(text, 300)
    assert floor < 300, text[:20]
    assert floor >= 100, "the bar must not collapse to nothing"
    assert english_equivalent_length(text) > len(text)


def test_a_string_too_short_to_have_a_script_is_left_alone():
    """`dominant_script` returns None under twenty letters, and a threshold
    applied to a guess is worse than one applied to a plain count."""
    assert length_floor_for(u"当館", 300) == 300
    assert length_floor_for("", 300) == 300
    assert english_equivalent_length(None) == 0


# --------------------------------------------------------------------------
# What `thin-html` does with it
# --------------------------------------------------------------------------

def _page(text, url="https://example.test/exhibits"):
    return {"url": url, "body_text": text, "body_text_len": len(text),
            "text": text, "text_len": len(text)}


def test_one_paragraph_gets_one_verdict_in_either_script():
    """The claim underneath the ratio, put as a comparison rather than as a
    number: the same paragraph, written twice, is thin in both scripts or in
    neither. Six sentences of Japanese run to 144 characters and the same six
    in English to over 400, and before this both were compared against 300."""
    japanese = _page(JAPANESE * 6)
    english = _page(ENGLISH * 6)
    assert RENDER.quotable_floor(japanese) < RENDER.MIN_QUOTABLE_TEXT
    assert japanese["body_text_len"] >= RENDER.quotable_floor(japanese)
    assert english["body_text_len"] >= RENDER.quotable_floor(english)
    # And the Japanese page is well under the English bar, which is the whole
    # of the defect: it was held to a number measured on the other page.
    assert japanese["body_text_len"] < RENDER.MIN_QUOTABLE_TEXT


def test_the_thin_bar_on_an_english_page_is_still_three_hundred():
    page = _page(ENGLISH * 5)
    assert RENDER.quotable_floor(page) == RENDER.MIN_QUOTABLE_TEXT


def test_an_english_page_with_two_sentences_is_still_thin():
    """The check has to keep working. Scaling the bar for one script must not
    quietly raise it for the script it was measured on."""
    page = _page(ENGLISH)
    assert page["body_text_len"] < RENDER.quotable_floor(page)


def test_a_japanese_page_that_really_is_empty_is_still_thin():
    """The direction this fix must not lose. A page with nothing on it has
    almost no characters in any script."""
    page = _page(u"展示")
    assert page["body_text_len"] < RENDER.quotable_floor(page)


def test_a_page_carrying_no_text_at_all_is_thin_in_every_script():
    assert 0 < RENDER.quotable_floor(_page(""))
