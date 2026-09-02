"""Advice that would have made the site worse.

Every case here is a real finding from a real audit whose recommended fix, if
followed, damages the site it was written for. These are the worst class of
defect this tool can produce: a false positive wastes a day, but a fix like
these leaves the site in a worse state than not running the audit at all.
"""

from __future__ import annotations

import json
import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    _is_section_root, detect_page_type, is_question_heading, looks_like_soft_404,
)
from page_extract import _looks_like_a_date, make_soup  # noqa: E402


# --------------------------------------------------------------------------
# Marking up legal text and policy statements as questions and answers
# --------------------------------------------------------------------------

def _faq_snippet_for(sections):
    import importlib.util
    import os
    path = os.path.abspath(os.path.join(SCRIPTS, "..", "..", "..", "skills",
                                        "structured-data-audit", "scripts", "check.py"))
    spec = importlib.util.spec_from_file_location("sd_check_for_faq_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = module._faq_snippet({"url": "https://example.test/help", "sections": sections})
    # The placeholder text contains angle brackets, so slice on the JSON
    # braces rather than on tag characters.
    return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])


def test_policy_statements_are_not_marked_up_as_questions():
    """A contributor style guide became twenty-four Question nodes asking nothing."""
    payload = _faq_snippet_for([
        {"heading": "Duplication is evil", "first_paragraph": "Do not copy code."},
        {"heading": "Code style", "first_paragraph": "Four spaces."},
        {"heading": "Asserts", "first_paragraph": "Use them."},
    ])
    names = [q["name"] for q in payload["mainEntity"]]
    assert names == ["<question as a visitor would ask it>"], (
        "with no real questions on the page the snippet must be a placeholder, "
        "not a fabrication built from whatever headings were there")


def test_legal_text_is_not_marked_up_as_questions():
    """A shop's help page carries a short FAQ, then its privacy policy and terms."""
    payload = _faq_snippet_for([
        {"heading": "How long does delivery take?", "first_paragraph": "Three days."},
        {"heading": "Can I return an item?", "first_paragraph": "Within 30 days."},
        {"heading": "Privacy Policy", "first_paragraph": "We collect the following data."},
        {"heading": "Terms of Service", "first_paragraph": "By using this site you agree."},
        {"heading": "Limitation of Liability", "first_paragraph": "To the fullest extent."},
    ])
    names = [q["name"] for q in payload["mainEntity"]]
    assert names == ["How long does delivery take?", "Can I return an item?"]


def test_the_real_questions_are_still_all_included():
    payload = _faq_snippet_for([
        {"heading": "What is it?", "first_paragraph": "A thing."},
        {"heading": "How much?", "first_paragraph": "Ten pounds."},
        {"heading": "Why?", "first_paragraph": "Because."},
    ])
    assert len(payload["mainEntity"]) == 3


def test_a_page_that_is_mostly_statements_is_not_an_faq_page():
    """Four questions among twenty-four headings is not an FAQ."""
    h2s = ["Duplication is evil", "Code style", "Asserts", "Naming"] * 5
    h2s += ["What is a good commit?", "How do I test?", "Why review?", "When to merge?"]
    assert detect_page_type("https://example.test/dev/code-review.html",
                            {"text": "", "jsonld_types": [],
                             "headings": {"h2": h2s}, "title": ""}) != "faq"


def test_a_page_that_is_mostly_questions_still_is():
    h2s = ["What is it?", "How much?", "Why bother?", "When does it ship?", "Contact"]
    assert detect_page_type("https://example.test/page/17",
                            {"text": "", "jsonld_types": [],
                             "headings": {"h2": h2s}, "title": ""}) == "faq"


@pytest.mark.parametrize("heading,expected", [
    ("What is it?", True), ("How do I return an item?", True),
    ("Duplication is evil", False), ("Privacy Policy", False), ("Code style", False),
])
def test_what_counts_as_a_question(heading, expected):
    assert is_question_heading(heading) is expected


# --------------------------------------------------------------------------
# Telling a shop to index its own error page
# --------------------------------------------------------------------------

def test_a_soft_404_carrying_noindex_is_doing_the_right_thing():
    """The fix said "remove noindex where the page should be public"."""
    assert looks_like_soft_404({
        "title": "Page Not Found - Example",
        "h1": "Uh-Oh, Nothing To See Here!"}) is True


def test_a_working_page_is_not_a_soft_404():
    assert looks_like_soft_404({"title": "Womens Flats | Example",
                                "h1": "Womens Flats"}) is False


def test_an_article_about_404s_is_not_a_soft_404():
    """Matched on title and heading only, never the body, for exactly this."""
    assert looks_like_soft_404({
        "title": "Designing a good error page",
        "h1": "Designing a good error page",
        "body_text": "A 404 page not found response tells the visitor nothing exists here."
    }) is False


# --------------------------------------------------------------------------
# Telling a blog index to publish Article markup about itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/blog", "/news", "/blogs/news", "/articles"])
def test_a_section_index_is_not_an_article(path):
    assert _is_section_root(path) is True


@pytest.mark.parametrize("path", ["/blog/a-post", "/blogs/news/a-post", "/about"])
def test_a_post_is_not_a_section_index(path):
    assert _is_section_root(path) is False


def test_a_declared_blog_index_is_a_listing():
    assert detect_page_type("https://example.test/x",
                            {"text": "", "jsonld_types": ["Blog"],
                             "headings": {}, "title": ""}) == "category"


# --------------------------------------------------------------------------
# A video player's countdown counted as a publication date
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["PT0S", "Remaining time unknown - --:--", "", "12:34"])
def test_a_duration_is_not_a_date(value):
    assert _looks_like_a_date(value) is False


@pytest.mark.parametrize("value", ["2026-01-31", "31/01/2026", "Published in 2024"])
def test_a_date_is_still_a_date(value):
    assert _looks_like_a_date(value) is True


def test_a_page_with_only_a_video_duration_carries_no_date():
    """It was excluded from the list of pages with no date, so it stayed undated."""
    from page_extract import _dates
    soup = make_soup('<html><body><time datetime="PT0S">--:--</time></body></html>')
    dates = _dates(soup, soup.get_text(" "), [])
    assert dates["has_any"] is False
