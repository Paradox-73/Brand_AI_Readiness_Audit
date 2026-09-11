# -*- coding: utf-8 -*-
"""Two meta-tag faults found by hand that the check could not see.

    a meta description that is literally `##`; product meta descriptions
    double-escaped while the same fault is reported for JSON-LD only

`_check_titles_and_descriptions` tested presence, duplication and title length.
`##` is present, unique and not a title, so it passed all three and the report
told the site its descriptions were in order.

The second is the same template filter run twice. An HTML parser resolves
`&amp;` in a `content` attribute to `&` on its own, so a value that arrives
still carrying `&amp;` was escaped after it was already escaped - and a search
result prints those five characters at a reader. `jsonld-values-escaped-as-html`
reports exactly this inside `<script>`; nothing read it in the `<head>`.

Its own finding rather than a line in the hygiene one, because the fix is not
the hygiene fix: "write a unique description that states the concrete fact" is
wrong advice for a sentence that is already written and already correct.

Every host below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)


def _module():
    path = os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("sd_for_meta_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module()


class _Result:
    def __init__(self):
        self.findings, self.skipped = [], []

    def check(self, name):
        pass

    def skip(self, name, reason):
        self.skipped.append((name, reason))

    def add(self, **fields):
        self.findings.append(fields)

    def signal(self, *args, **kwargs):
        pass


def _pages(count=4, **over):
    base = {"page_type": "product",
            "title": "Braided rope | Larkmoor",
            "meta_description": "Braided rope and twine, spliced by hand at the mill."}
    return [dict(base, url="https://larkmoor.test/p/{}".format(n), **over)
            for n in range(count)]


# --------------------------------------------------------------------------
# A tag with no word in it
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["##", "|", "...", "-", "—", "  ##  "])
def test_a_tag_that_is_only_punctuation_says_nothing(value):
    assert SD._says_nothing(value) is True, value


@pytest.mark.parametrize("value", [
    "Braided rope and twine",
    "2024 report",
    u"ロープ専門店",     # a Japanese shop name
    u"مركز الوادي",  # an Arabic one
    "",                                          # absent is a different finding
    None,
])
def test_a_tag_carrying_a_word_in_any_script_is_not_this_fault(value):
    """`##` is punctuation in every language; a name is a name in every
    script, and a check that reads only Latin letters would report every
    Japanese and Arabic title as empty."""
    assert SD._says_nothing(value) is False, value


def test_the_hygiene_finding_names_the_wordless_tag():
    result = _Result()
    SD._check_titles_and_descriptions(result, _pages(meta_description="##"))
    hygiene = [f for f in result.findings
               if f["id_hint"] == "title-and-description-hygiene"]
    assert hygiene, "a description of ## produced no finding at all"
    assert "no word in it" in hygiene[0]["evidence"]
    assert "##" in hygiene[0]["evidence"]
    assert len(hygiene[0]["affected_pages"]) == 4


# --------------------------------------------------------------------------
# A tag the template escaped twice
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "Braided rope &amp; twine",
    "spliced &#39;by hand&#39;",
    "rope &quot;spliced&quot; here",
])
def test_a_character_reference_that_survived_parsing_was_escaped_twice(value):
    assert SD._escaped_twice(value) is True, value


@pytest.mark.parametrize("value", [
    "Braided rope & twine",     # escaped once, parsed back, correct
    "R&D services",
    "100 & rising",
    "", None,
])
def test_an_ampersand_the_parser_already_resolved_is_not_this_fault(value):
    assert SD._escaped_twice(value) is False, value


def test_the_finding_names_the_template_and_not_the_copy():
    """The whole reason this is not a line in the hygiene finding."""
    result = _Result()
    SD._report_meta_escaped_twice(
        result, _pages(meta_description="Braided rope &amp; twine, spliced &#39;by hand&#39;."))
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding["owner"] == "developer"
    assert finding["effort"] == "low"
    assert "&amp;" in finding["evidence"]
    assert len(finding["affected_pages"]) == 4
    joined = " ".join(finding["how_to_fix"]).lower()
    assert "template" in joined
    assert "rather than editing the copy" in joined
    # The two halves of one template bug, named to each other.
    assert "json-ld" in joined


def test_a_site_whose_tags_parse_cleanly_gets_no_finding():
    result = _Result()
    SD._report_meta_escaped_twice(result, _pages())
    assert result.findings == []


def test_the_title_is_read_as_well_as_the_description():
    result = _Result()
    SD._report_meta_escaped_twice(
        result, _pages(title="Rope &amp; twine | Larkmoor"))
    assert len(result.findings) == 1
    assert "<title>" in result.findings[0]["evidence"]


def test_one_page_is_enough_because_the_fault_is_a_template():
    """Unlike the hygiene counts, which need a share before they mean
    anything: one escaped value is one template, and the template renders
    every page."""
    result = _Result()
    SD._report_meta_escaped_twice(
        result, _pages(1, meta_description="Rope &amp; twine"))
    assert len(result.findings) == 1
