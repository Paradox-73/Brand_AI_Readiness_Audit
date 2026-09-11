# -*- coding: utf-8 -*-
"""A container is not a page somebody wrote too little on.

Two checks inside `render-readability-audit` reached opposite conclusions about
one homepage. The document was 227,011 bytes, of which 224,637 were inline
script, and it carried twelve characters of visible text.

  `spa-shell-detection`  wanted two independent signals, found the
                         script-versus-prose one alone, and printed "every
                         crawled content page delivered readable text in the
                         initial HTML response"

  `thin-html`            fired at high severity, in the confident tier, ranked
                         "do first", saying "the rest of the delivered document
                         is accounted for by the site's own repeated header,
                         menu and footer, so there is no content this audit
                         failed to find", and told the owner to write two or
                         three sentences of body copy

Both were about the same page, they contradicted each other, and the fix that
led the report was the wrong one: a shell is fixed by rendering on the server,
thin copy by writing more, and neither fix touches the other defect.

The second half of this file is the coverage rule. Findings already hold it -
`withhold_absence_claims` sets an absence claim aside when too little was read,
and `enough_to_grade` gates the verdict - and the recommendations did not, so
one report withheld the findings about a one-page crawl and then recommended
publishing a press page and FAQ content the site had had for years.

Every host in this file is invented and every address is fictional.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(skill, name):
    scripts = os.path.join(ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _module("render-readability-audit", "render_for_shell_tests")

_COMPOSE_SPEC = importlib.util.spec_from_file_location(
    "compose_for_shell_tests", os.path.join(SCRIPTS, "compose_report.py"))
COMPOSE = importlib.util.module_from_spec(_COMPOSE_SPEC)
_COMPOSE_SPEC.loader.exec_module(COMPOSE)

SITE = "https://an-invented-college.test"
CHROME = ["/", "/about", "/courses", "/contact"]


def _reason(result, check):
    return next(n["reason"] for n in result.not_applicable if n["check"] == check)


def _causes(result):
    return {f["root_cause"] for f in result.findings}


def _shell_page(url=SITE + "/", visible=12, script_bytes=224637, **extra):
    """A response that is almost entirely script, with no mount node this
    audit's selector list recognises.

    The selectors match an id exactly, so a build that names its mount anything
    outside that list records no framework root at all - which is how the page
    that prompted this file reached `thin-html` with only one shell signal.
    """
    page = {
        "url": url, "final_url": url, "status": 200, "page_type": "home",
        "title": "A college", "body_text": "x" * visible, "body_text_len": visible,
        "text_len": visible,
        "spa_shell": {"root_selector": None, "root_text_len": None,
                      "state_blobs": [], "state_blob_bytes": 0,
                      "noscript_demands_js": False, "visible_text_len": visible},
        "scripts": {"inline_bytes": script_bytes, "external_count": 2},
        "chrome_signature": {"nav_paths": list(CHROME)},
    }
    page.update(extra)
    return page


def _written_page(index, body_chars=900, chrome_chars=700, script_bytes=4000):
    """An ordinary page: real copy, real chrome, an ordinary script payload."""
    url = "{}/course-{}".format(SITE, index)
    return {
        "url": url, "final_url": url, "status": 200, "page_type": "content",
        "title": "Course {}".format(index),
        "body_text": "y" * body_chars, "body_text_len": body_chars,
        "text_len": body_chars + chrome_chars,
        "spa_shell": {"root_selector": None, "root_text_len": None,
                      "state_blobs": [], "state_blob_bytes": 0,
                      "noscript_demands_js": False,
                      "visible_text_len": body_chars + chrome_chars},
        "scripts": {"inline_bytes": script_bytes, "external_count": 2},
        "chrome_signature": {"nav_paths": list(CHROME)},
    }


def _thin_page(url=SITE + "/opening-hours", body_chars=59, chrome_chars=700):
    """A page that really was written short: three lines of copy, and the
    site's own menu and footer around them exactly as on every other page."""
    return {
        "url": url, "final_url": url, "status": 200, "page_type": "content",
        "title": "Opening hours",
        "body_text": "z" * body_chars, "body_text_len": body_chars,
        "text_len": body_chars + chrome_chars,
        "spa_shell": {"root_selector": None, "root_text_len": None,
                      "state_blobs": [], "state_blob_bytes": 0,
                      "noscript_demands_js": False,
                      "visible_text_len": body_chars + chrome_chars},
        "scripts": {"inline_bytes": 4000, "external_count": 2},
        "chrome_signature": {"nav_paths": list(CHROME)},
    }


# --------------------------------------------------------------------------
# The precedence rule
# --------------------------------------------------------------------------

def test_script_dwarfing_the_whole_document_is_a_shell_on_its_own():
    """The signal `thin-html` claimed to have consulted, made sufficient.

    It does not depend on recognising the mount node by name, which is what
    let this page through: two signals were required and only one shape of
    evidence was available.
    """
    verdict, reasons = RENDER.shell_verdict(_shell_page())
    assert verdict == RENDER.SHELL, reasons
    assert reasons and "224,637" in reasons[0]


def test_a_short_page_with_an_ordinary_script_payload_is_not_a_shell():
    """The guard the two-signal rule existed for, kept.

    The ratio is measured against the whole delivered document's visible text,
    navigation and footer included, so a page that is short because somebody
    wrote three lines still ships its menu and clears the floor.
    """
    assert RENDER.shell_verdict(_thin_page())[0] == ""
    # And a genuinely short page that also loads a large consent bundle: the
    # chrome is what settles it, not the script size.
    loaded = _thin_page()
    loaded["scripts"] = {"inline_bytes": 120000, "external_count": 2}
    assert RENDER.shell_verdict(loaded)[0] == ""


def test_both_checks_read_one_answer_and_cannot_disagree():
    """The composition failure, asserted from the outside.

    `spa-shell-detection` reporting the page and `thin-html` reporting the same
    page are the two halves of the contradiction, so both are asserted here.
    """
    home = _shell_page()
    pages = [home] + [_written_page(i) for i in range(4)]
    result = SkillResult("render-readability-audit")
    RENDER._check_shells(result, {"pages": pages}, pages, "static")

    assert "js-shell" in _causes(result)
    assert "thin-html" not in _causes(result), (
        "a document the crawler could not read was reported as a page somebody "
        "wrote too little on")
    reason = _reason(result, "thin-html")
    assert home["url"] in reason
    assert "JavaScript shell" in reason


def test_thin_html_never_says_it_found_everything_there_was_to_find():
    """The confident-tier falsehood, in the exact words it was printed in.

    The chrome accounting compares visible text against visible text; it never
    looked at the script bytes, so it could not support a claim about the whole
    delivered document.
    """
    pages = [_written_page(i) for i in range(6)] + [_thin_page()]
    result = SkillResult("render-readability-audit")
    RENDER._check_thin_pages(result, pages, [])
    finding = next(f for f in result.findings if f["root_cause"] == "thin-html")
    assert "no content this audit failed to find" not in finding["evidence"]


def test_thin_html_still_fires_on_copy_that_really_was_never_written():
    """The precedence must not silence the check it takes precedence over.

    A page carrying its template's furniture and 59 characters of its own is
    the case this check exists for, and the fix - write a paragraph - is the
    right one there.
    """
    pages = [_written_page(i) for i in range(6)] + [_thin_page()]
    result = SkillResult("render-readability-audit")
    RENDER._check_thin_pages(result, pages, [])
    finding = next(f for f in result.findings if f["root_cause"] == "thin-html")
    assert finding["affected_pages"] == [SITE + "/opening-hours"]
    assert "two or three sentences" in " ".join(
        finding["suggested_action"]["how_to_fix"])


def test_one_shell_signal_is_not_a_finding_and_is_not_thin_either():
    """The middle case, which had nowhere to go.

    One signal is too little to accuse a template of shipping empty and far too
    much to tell the owner the copy was never written, so the page is named in
    both checks' reasons and counted by neither.
    """
    # A short page whose copy is sitting in a state blob: the site telling us
    # the text arrives later, and one signal on its own.
    page = _thin_page(url=SITE + "/apply", body_chars=250, chrome_chars=0)
    page["spa_shell"]["state_blobs"] = ["__NEXT_DATA__"]
    pages = [page] + [_written_page(i) for i in range(4)]
    assert RENDER.shell_verdict(page)[0] == RENDER.SHELL_UNSETTLED

    result = SkillResult("render-readability-audit")
    RENDER._check_shells(result, {"pages": pages}, pages, "static")
    assert _causes(result) == set()
    assert page["url"] in _reason(result, "spa-shell-detection")
    assert page["url"] in _reason(result, "thin-html")


def test_the_passing_sentence_does_not_count_pages_it_never_assessed():
    """"each of the 0 pages this check could assess carries at least 300
    characters" is what a one-page crawl of a shell printed: a pass, phrased as
    a measurement, over nothing."""
    home = _shell_page()
    result = SkillResult("render-readability-audit")
    RENDER._check_shells(result, {"pages": [home]}, [home], "static")
    reason = _reason(result, "thin-html")
    assert "each of the 0 pages" not in reason
    assert "no crawled content page reached this check" in reason


# --------------------------------------------------------------------------
# One set behind every number a finding prints
# --------------------------------------------------------------------------

def test_the_shell_finding_counts_the_pages_it_lists():
    """The title counted every shell, homepage included; the list underneath
    held every shell except the homepage, which has its own finding. A reader
    who counts the URLs finds the finding disagreeing with itself."""
    home = _shell_page()
    others = [_shell_page(url="{}/dept-{}".format(SITE, i)) for i in range(3)]
    for page in others:
        page["page_type"] = "content"
    pages = [home] + others + [_written_page(i) for i in range(4)]
    result = SkillResult("render-readability-audit")
    RENDER._check_shells(result, {"pages": pages}, pages, "static")

    finding = next(f for f in result.findings
                   if f["id_hint"] == "content-pages-are-javascript-shells")
    assert finding["title"].startswith("3 of 8 ")
    assert len(finding["affected_pages"]) == 3
    assert "homepage reported separately" in finding["evidence"]


# --------------------------------------------------------------------------
# The coverage rule the findings already obey
# --------------------------------------------------------------------------

def _snapshot(page_count, body_chars=400):
    pages = []
    for index in range(page_count):
        url = "{}/p{}".format(SITE, index)
        pages.append({
            "url": url, "final_url": url, "status": 200, "page_type": "content",
            "title": "Page {}".format(index),
            "body_text": "w" * body_chars, "body_text_len": body_chars,
            "text_len": body_chars, "links": {"internal": [], "external": []},
            "jsonld": [], "prices": [],
        })
    return {"site": "an-invented-college.test", "origin": SITE,
            "brand": {"name": "An Invented College", "host": "an-invented-college.test"},
            "pages": pages, "sitemaps": [],
            "crawl": {"pages_crawled": page_count, "pages_ok": page_count}}


def _unreadable_snapshot():
    """One page reached, and it delivered nothing anybody could read."""
    snapshot = _snapshot(1, body_chars=12)
    return snapshot


def _by_id(snapshot, **signals):
    return {r["id"]: r for r in COMPOSE.build_recommendations(snapshot, signals, [])}


def test_an_absence_shaped_recommendation_is_a_question_on_a_thin_crawl():
    """Both entries seen on a real run: the crawl reached one page and the report
    told the site to publish a press page and FAQ content. Both existed and
    both answered 200."""
    entries = _by_id(_unreadable_snapshot())
    for rec_id in ("R-PRESS-PAGE", "R-FAQ-SCHEMA"):
        entry = entries[rec_id]
        assert entry["conditioned_on"] == "absence"
        assert entry["title"].endswith("?"), entry["title"]
        assert "could not establish that this is missing" in entry["summary"]


def test_the_unconditional_entry_is_still_an_instruction():
    """`R-BOILERPLATE` has no absence in it to be wrong about, so the coverage
    rule may not touch it - the entry is documented as deliberately
    unconditional and this is what that means in the output."""
    entry = _by_id(_unreadable_snapshot())["R-BOILERPLATE"]
    assert "conditioned_on" not in entry
    assert not entry["title"].endswith("?")


def test_a_crawl_that_read_the_site_still_gets_plain_instructions():
    """The rule may not soften advice on a site that was actually read, or the
    whole catalogue turns into a list of questions."""
    entries = _by_id(_snapshot(6))
    assert entries["R-PRESS-PAGE"]["title"].startswith("Publish")
    assert "conditioned_on" not in entries["R-PRESS-PAGE"]


def test_the_recommendations_and_the_findings_use_one_coverage_rule():
    """Two rules that disagree are how a report holds a finding back on the
    grounds that too little was read and then instructs the reader on the same
    subject two sections later."""
    thin = _unreadable_snapshot()
    assert not COMPOSE.enough_to_grade(
        len(COMPOSE.readable_text_pages(thin)), COMPOSE.readable_share(thin))
    held = COMPOSE.withhold_absence_claims(
        [{"title": "No FAQ markup anywhere on the site", "mechanism": "C",
          "root_cause": "no-faq-schema", "detected_by": "structured-data-audit"}],
        thin)[1]
    assert held, "the finding half of this subject was published on a thin crawl"
    assert _by_id(thin)["R-FAQ-SCHEMA"].get("conditioned_on") == "absence"
