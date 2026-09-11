# -*- coding: utf-8 -*-
"""Two figures a reader cannot check, and count-agreement grammar.

One of this tool's claims was disproved by hand, and the problem this file
exists for is that a reader could not have checked it from the report.
The finding's title said 48 pages; `affected_pages` in `report.json` held five;
nothing in the file said the five were a sample, how they were chosen, or where
the 48 came from.

The same reports printed two sentences: *"1 URL were crawled"* and *"1 of the
profile link the brand publishes lead nowhere"*. Both are a count formatted on
its own and glued to a clause written for the plural - the shape an earlier
pass fixed about forty times in three other skills. The first is this file's;
the second is a title built in `skills/freshness-corroboration-audit`, which
this file cannot reach and names in the report instead.

Every brand and host below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_figure_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose()

HOME = "https://loomandlarder.test/"


def _finding(id_hint="no-h1", pages=(HOME,), count=None, root_cause="heading-structure",
             severity="medium"):
    pages = list(pages)
    return {
        "id_hint": id_hint,
        # Through `plural`, because this fixture's own title is inside the
        # sweep below and a fixture that fails the rule proves nothing about
        # the file under test.
        "title": compose.plural(count if count is not None else len(pages),
                                "page has no top-level heading",
                                "pages have no top-level heading"),
        "severity": severity,
        "confidence": "high",
        "evidence": "Pages with no H1: {}.".format(", ".join(pages) or "none"),
        "mechanism": "B",
        "root_cause": root_cause,
        "affected_pages": pages,
        "affected_page_count": count if count is not None else len(pages),
        "checked": ("the h1 elements of every crawled page",),
        "suggested_action": {
            "summary": "Give every page an H1.",
            "effort": "low",
            "owner": "developer",
            "how_to_fix": ["Step one."],
            "rationale": "Because it works.",
        },
    }


def _skill_result(*findings):
    return {
        "skill": "render-readability-audit",
        "findings": list(findings),
        "signals": {},
        "checks_run": [],
        "not_applicable": [],
        "fired_checks": [],
        "extra_requests_made": 0,
    }


def _page(url, **kwargs):
    page = {
        "url": url,
        "final_url": url,
        "status": 200,
        "title": "Loom and Larder",
        "page_type": "content",
        "body_text": "Loom and Larder is a workshop that repairs cane chairs by hand.",
        "paragraphs": ["Loom and Larder is a workshop that repairs cane chairs."],
    }
    page.update(kwargs)
    return page


def _snapshot(pages=4):
    urls = [_page(HOME, page_type="home")]
    urls += [_page("{}p{}".format(HOME, i)) for i in range(1, pages)]
    return {
        "site": "loomandlarder.test",
        "origin": "https://loomandlarder.test",
        "brand": {"name": "Loom and Larder", "host": "loomandlarder.test",
                  "source": "og:site_name"},
        "pages": urls,
        "sitemaps": [],
        "crawl": {"pages_crawled": len(urls), "pages_ok": len(urls),
                  "pages_read": len(urls), "render_mode": "static",
                  "user_agent": "test-agent", "elapsed_s": 1.0, "notes": []},
    }


def _report(*results, **kwargs):
    return compose.compose(kwargs.get("snapshot") or _snapshot(), list(results),
                           audited_at="2026-01-01T00:00:00Z")


# --------------------------------------------------------------------------
# The list of five under a title claiming 48
# --------------------------------------------------------------------------

def _real_finding():
    """The shape seen in the wild: five addresses listed, forty-eight claimed.

    `make_finding` in `audit_common.py` writes `sorted(set(pages))[:5]`, so a
    finding arrives at this file already trimmed and the other forty-three
    addresses exist nowhere in the run. This fixture is what composition
    actually receives.
    """
    return _finding("no-h1", pages=["{}p{}".format(HOME, i) for i in range(1, 6)],
                    count=48)


def test_report_json_says_the_page_list_is_a_sample_and_of_what():
    published = _report(_skill_result(_real_finding()))["findings"][0]
    note = published.get("affected_pages_are_a_sample")
    assert note, "a five-page list under a 48-page claim shipped with nothing saying so"
    assert "5 of 48 pages" in note, note
    assert "affected_page_count" in note, note
    assert published["affected_page_count"] == 48
    assert len(published["affected_pages"]) == 5


def test_the_note_says_which_five_so_the_sample_is_reproducible():
    """"First five" does not tell a reader whether they are looking at the five
    worst pages or five arbitrary ones, and that decides what checking one of
    them proves."""
    note = _report(_skill_result(_real_finding()))["findings"][0][
        "affected_pages_are_a_sample"]
    assert "alphabetical order" in note, note
    assert "not a ranking" in note, note


def test_a_complete_list_carries_no_note_at_all():
    """The key exists to qualify a sample. A finding whose list is the whole
    population has nothing to qualify, and a report with no sampled finding in
    it gains no new key."""
    published = _report(_skill_result(_finding("no-h1", pages=[HOME])))["findings"][0]
    assert "affected_pages_are_a_sample" not in published
    assert published["affected_page_count"] == 1


def test_the_document_a_person_reads_says_the_same_thing():
    """report.json is not what a person reads. The markdown heading used to say
    "first 5 shown", which is the count again and not which five."""
    markdown = compose.render_markdown(_report(_skill_result(_real_finding())))
    assert "48 in total" in markdown
    assert "the 5 listed are the first in alphabetical order, not a ranking" in markdown


# --------------------------------------------------------------------------
# Count agreement
# --------------------------------------------------------------------------

# The nouns this report pluralises, and the verbs a clause written for the
# plural puts after them. A count of 1 in front of either is the defect.
_COUNTED_NOUNS = ("URL", "page", "finding", "recommendation", "fix", "check",
                  "skill", "question", "action", "problem", "profile", "sub-skill")
_PLURAL_VERBS = ("were", "are", "have", "lead", "rest", "say", "carry", "do", "mean")

_A_PLURAL_NOUN_AFTER_ONE = re.compile(
    r"\b1 (?:{})s\b".format("|".join(_COUNTED_NOUNS)))
_A_PLURAL_VERB_AFTER_ONE = re.compile(
    r"\b1 (?:{})s? (?:{})\b".format("|".join(_COUNTED_NOUNS), "|".join(_PLURAL_VERBS)))


def _one_of_everything():
    """One page, one finding, one recommendation - every count in the report 1."""
    return _report(_skill_result(_finding("no-h1", pages=[HOME])),
                   snapshot=_snapshot(pages=1))


def test_the_sheet_does_not_say_one_url_were_crawled():
    """The sentence a real report printed, and the fix is in the call rather than in the
    template: the verb goes inside `_plural` so one argument decides the noun
    and the verb together."""
    _, read, _ = compose.top_sheet(_one_of_everything())[3]
    assert "1 URL were crawled" not in read, read
    assert "1 URL was crawled" in read, read


def test_the_same_sentence_still_agrees_in_the_plural():
    _, read, _ = compose.top_sheet(_report(_skill_result(_finding())))[3]
    assert "4 URLs were crawled" in read, read


def test_nothing_in_either_rendered_document_counts_one_of_a_plural():
    """A sweep rather than a sentence, because the defect is a shape. One page,
    one finding, one of everything - and neither document may put a plural noun
    or a plural verb behind the number 1."""
    report = _one_of_everything()
    for name, document in (("report.md", compose.render_markdown(report)),
                           ("report.html", compose.render_html(report))):
        nouns = _A_PLURAL_NOUN_AFTER_ONE.findall(document)
        verbs = _A_PLURAL_VERB_AFTER_ONE.findall(document)
        assert not nouns and not verbs, "{}: {}".format(name, nouns + verbs)


def test_a_one_page_crawl_does_not_point_at_those_one_urls():
    """The same sentence's tail. "No page count below is out of more than those
    1" is the same defect one clause later."""
    _, read, _ = compose.top_sheet(_one_of_everything())[3]
    assert "those 1" not in read, read
