# -*- coding: utf-8 -*-
"""A report written left to right, quoting a site that writes right to left.

Every evidence line in this marketplace is an English sentence with the site's
own words inside it:

    Example: https://site.test/news declares its title as "<the title>"
    (3 of 12 pages).

Where the title is Arabic, Hebrew, Persian or Urdu, the Unicode bidirectional
algorithm assigns every neutral character next to it - the closing quotation
mark, the bracket, the digits, the full stop - to the right-to-left run, and a
reader sees them at the run's other end. The count reads as part of the title.
The URL keeps its own direction and the punctuation between the two does not.

Nothing is wrong with the string, so no check upstream can fix it. It is a
rendering fault, and the fix is the pair Unicode defines for exactly this case:
U+2068 FIRST STRONG ISOLATE and U+2069 POP DIRECTIONAL ISOLATE around each run.
Both are zero-width; a consumer that does not implement them shows nothing.

Applied at the two render boundaries and nowhere else, so `report.json` carries
the site's string byte for byte and a paste-ready snippet leaves as it was
written. Found by reading a rendered line for an Arabic site, and the first
version of the pattern was non-greedy and cut a five-letter word into three
isolated fragments - which is why the run shape is tested here
rather than only the presence of an isolate.

Every host and organisation below is invented.
"""

from __future__ import annotations

import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from compose_report import (  # noqa: E402
    decode_rendered_markdown, isolate_right_to_left_runs,
)

OPEN, CLOSE = u"⁨", u"⁩"

ARABIC = u"افتتاح المعرض"
HEBREW = u"מוזיאון העיר"
PERSIAN = u"۱۴۰۴ هـ.ش"
URDU = u"ادارہ خبر"


# --------------------------------------------------------------------------
# One isolate per run
# --------------------------------------------------------------------------

@pytest.mark.parametrize("quoted", [ARABIC, HEBREW, PERSIAN, URDU])
def test_a_quotation_is_wrapped_once_rather_than_letter_by_letter(quoted):
    """The first pattern was non-greedy, so it closed at the first pair it
    could and produced three isolates inside one word. A run is the longest
    stretch that opens and closes with a right-to-left letter."""
    isolated = isolate_right_to_left_runs(u'title as "{}" (3 pages).'.format(quoted))
    assert isolated.count(OPEN) == 1, isolated
    assert isolated.count(CLOSE) == 1, isolated
    assert OPEN + quoted + CLOSE in isolated, isolated


def test_the_punctuation_around_the_quotation_stays_outside_it():
    """What the reader is actually being given back: the closing quote, the
    bracket and the count belong to the English sentence, not to the title."""
    isolated = isolate_right_to_left_runs(
        u'Example: https://site.test/news declares its title as "{}" '
        u'(3 of 12 pages).'.format(ARABIC))
    assert isolated.startswith(u'Example: https://site.test/news declares its '
                               u'title as "' + OPEN)
    assert isolated.endswith(CLOSE + u'" (3 of 12 pages).')


def test_two_runs_with_english_between_them_are_two_isolates():
    """A run may not swallow a Latin word. An isolate takes its direction from
    the first strong letter in it, so one covering both runs would lay the
    English between them out backwards - the fault, not the fix."""
    line = u"{} then in English then {}".format(ARABIC, HEBREW)
    isolated = isolate_right_to_left_runs(line)
    assert isolated.count(OPEN) == 2
    assert u"then in English then" in isolated
    assert OPEN + u"then" not in isolated


def test_a_line_with_no_right_to_left_letter_is_returned_unchanged():
    line = u"Example: 3 of 12 pages carry no meta description."
    assert isolate_right_to_left_runs(line) == line


def test_digits_and_dots_inside_a_run_do_not_end_it():
    """`۱۴۰۴ هـ.ش` is one date, and splitting it at the full stop would
    isolate the calendar mark away from its year."""
    isolated = isolate_right_to_left_runs(u"published {} here".format(PERSIAN))
    assert isolated == u"published {}{}{} here".format(OPEN, PERSIAN, CLOSE)


# --------------------------------------------------------------------------
# The two boundaries, and the two places this must never reach
# --------------------------------------------------------------------------

def test_the_rendered_report_isolates_a_quotation_in_its_prose():
    rendered = decode_rendered_markdown(
        u'- **Evidence.** The homepage title reads "{}".'.format(ARABIC))
    assert OPEN + ARABIC + CLOSE in rendered


def test_a_paste_ready_snippet_leaves_byte_for_byte():
    """A fenced block is copied into a page. An invisible formatting character
    inside a JSON-LD value is a value the site then publishes."""
    fence = u'```json\n{{"name": "{}"}}\n```'.format(ARABIC)
    assert decode_rendered_markdown(fence) == fence


def test_an_inline_code_span_is_left_alone_too():
    """`افتتاح` in backticks is a value being named, not prose being
    read, and the reader may be about to copy it."""
    line = u"The declared name is `{}` on every page.".format(ARABIC)
    rendered = decode_rendered_markdown(line)
    assert u"`{}`".format(ARABIC) in rendered
    assert OPEN not in rendered.split(u"`")[1]


def test_both_rendered_documents_do_the_same_thing():
    """`report.md` and `report.html` are produced from one report and a reader
    reads whichever they open. The two documents disagreeing about one string
    is a defect this repository has recorded twice already.
    """
    import compose_report

    markdown = decode_rendered_markdown(u'reads "{}" here'.format(ARABIC))
    assert OPEN in markdown
    # `esc` is report.html's own boundary; every prose value crosses it.
    source = open(compose_report.__file__, encoding="utf-8").read()
    assert "isolate_right_to_left_runs(\n            html.escape(" in source, (
        "report.html no longer isolates, so the two documents disagree")
