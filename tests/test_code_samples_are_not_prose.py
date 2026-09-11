# -*- coding: utf-8 -*-
"""A code sample is text the page shows, not text the page says.

Two false positives on one documentation site, both traced to `<pre>` and
`<code>` never being taken out before the prose was measured:

  "8 pages are written in        the "sentences" quoted underneath were the
  sentences too long to quote"   ASCII output tables of a dataframe library,
                                 sampled out of the tool's own `body_text`
  "references 2024 in            the year sat inside `# As of 14th October
  "DataFrame ({# As of 14th      2024, ~3pm UTC`, a comment in a demo snippet,
  October 2024 ..."              and the fix offered was half a day to a day
                                 of rewriting copy nobody had written

The separation is the one `<select>` menus already get: the samples are kept,
in `code_text`, and are out of every field that reads the page as writing. The
boundary is the thing to hold - a `<code>` span inside a sentence is part of
that sentence, and removing it would corrupt the prose rather than clean it.
"""

from __future__ import annotations

import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import make_soup  # noqa: E402
from page_extract import (  # noqa: E402
    _code_samples, _code_text, _without_code_samples, extract_page,
)

SITE = "https://invented-docs.test"

# The shape a documentation generator ships: the sample inside `<pre><code>`,
# wrapped in a highlighting div.
SAMPLE_PAGE = """<html><body><main>
<h1>Window functions</h1>
<p>A window function computes an aggregate over a group of rows and returns one
value for each row of that group.</p>
<p>Set <code>--jobs</code> to 4 when you want the work spread over four cores.</p>
<div class="highlight"><pre><code>frame = library.DataFrame({# As of 14th October 2024, ~3pm UTC
    "ticker": ["AAA", "BBB", "CCC"],
})
print("{result}"); shape: (163, 3)
Def | Speed | Generation | Legendary | --- | --- | --- | --- | one two three
four five six seven eight nine ten eleven twelve thirteen fourteen fifteen
sixteen seventeen eighteen nineteen twenty twentyone twentytwo twentythree
</code></pre></div>
<p>The rows come back in the order you sorted them, which is what makes the
result reproducible between runs.</p>
</main></body></html>"""


def _record(html, url=SITE + "/window-functions/"):
    return extract_page(url, url, 200, {"content-type": "text/html"},
                        html, [], 12, 1, "seed", SITE)


def test_an_output_table_is_not_a_sentence_the_page_wrote():
    """The quoted "sentences" in the long-sentence finding were an ASCII table
    printed by a demo, sampled out of this file's own `body_text`."""
    record = _record(SAMPLE_PAGE)
    assert "shape: (163, 3)" not in record["body_text"]
    assert "Legendary" not in record["body_text"]
    assert record["readability"]["long_sentence_count"] == 0
    # The prose the page really wrote is all still there.
    assert "window function computes an aggregate" in record["body_text"]


def test_a_comment_inside_a_demo_is_not_evergreen_copy():
    """"references 2024 in "DataFrame ({# As of 14th October 2024 ..."" - the
    staleness check reads `body_text`, and the year was in a code comment."""
    record = _record(SAMPLE_PAGE)
    assert "2024" not in record["body_text"]
    assert "2024" in record["code_text"], "the sample itself must still be readable"


def test_a_code_span_inside_a_sentence_stays_in_the_sentence():
    """"set `--jobs` to 4" is prose with a flag name in it. Removing the span
    would corrupt the sentence rather than clean the page."""
    record = _record(SAMPLE_PAGE)
    assert "Set --jobs to 4 when you want" in record["body_text"]


def test_the_page_still_carries_its_code_where_a_check_can_see_it():
    """Kept, not deleted: a check asking whether this page carries code reads
    `code_text`, the way a check asking what a menu offers reads
    `control_text`."""
    record = _record(SAMPLE_PAGE)
    assert record["code_sample_count"] == 1
    assert record["code_text_len"] == len(record["code_text"])
    assert "library.DataFrame" in record["code_text"]
    # And the whole visible document, which is what a machine would read off
    # the page, is unchanged.
    assert "shape: (163, 3)" in record["text"]


def test_a_standalone_code_element_outside_pre_is_a_sample_too():
    """Documentation themes ship one-line samples as a bare `<code>` in a
    `<div>`. Nothing else in that block is words, so it is not a sentence."""
    soup = make_soup(
        '<div><p>Install it first.</p><div class="cmd">'
        '<code>installer add invented-library</code></div>'
        '<p>Then import it into your program and call it.</p></div>')
    samples = _code_samples(soup)
    assert [s.get_text() for s in samples] == ["installer add invented-library"]
    assert "installer add" not in _without_code_samples(soup).get_text(" ")


def test_a_page_that_is_only_code_keeps_its_text():
    """The same floor `_without_form_choices` uses. A removal that leaves
    almost nothing behind has stopped being about samples and become a claim
    that the page is empty."""
    soup = make_soup(
        "<body><h1>API</h1><pre>" + ("call(argument) " * 200) + "</pre></body>")
    assert _without_code_samples(soup) is soup
    # And the samples are still recorded, because the field is about what the
    # page carries rather than about what was removed.
    assert "call(argument)" in _code_text(soup)


def test_a_page_with_no_code_is_not_reparsed():
    """Reparsing costs about as much as the first parse, and most pages carry
    no sample at all - the guard `without_other_peoples_words` uses."""
    soup = make_soup("<body><p>Plain prose, no samples anywhere.</p></body>")
    assert _without_code_samples(soup) is soup
    assert _code_text(soup) == ""
