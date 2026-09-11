"""One rendering fault, billed once.

A real report diagnosed a JavaScript-rendered storefront correctly, ranked
it critical and first, and then emitted its consequences as separate work
items:

    "F-007 ('never states its founding facts') and F-008 ('never states its
    location') both list `https://<site>/` and `https://<site>/contact-us` as
    affected - the same two pages F-001 and F-009 declare empty - and prescribe
    content fixes ('Put the full postal address in text on the contact page')
    for what is a rendering fault. F-010's no-H1 list opens with the homepage,
    the same fact a third time."

    "root cause found and ranked first - but three to four findings per site
    are that root cause wearing different hats, presented as independent work
    items."

Two properties are tested here, and they pull against each other, which is why
both are pinned:

  nothing is lost      every finding, every severity, every affected page and
                       every count survives. A deleted true finding is a miss,
                       as costly to the reader as a false positive, and "put
                       the address in the page text" is real work once the
                       page renders.
  nothing is billed    the consequences carry a sentence naming the finding
  twice                they follow from, and they do not take the "Start here"
                       slots the reader has three of.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)


def _compose_module():
    spec = importlib.util.spec_from_file_location(
        "compose_for_one_cause_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose_module()

HOME = "https://shop.test/"
CONTACT = "https://shop.test/contact-us"
DELIVERED = "https://shop.test/journal"


def _finding(id_hint, root_cause, pages, severity="medium", mechanism="B",
             confidence="high", skill="fact-extractability-audit", title=None,
             checked=("the page text", "the page markup")):
    return {
        "id_hint": id_hint,
        "title": title or id_hint.replace("-", " "),
        "severity": severity,
        "confidence": confidence,
        "evidence": "Measured on {} page(s).".format(len(pages)),
        "mechanism": mechanism,
        "root_cause": root_cause,
        "affected_pages": list(pages),
        "affected_page_count": len(pages),
        "checked": list(checked),
        "suggested_action": {
            "summary": "Put the full postal address in text on the contact page.",
            "effort": "low",
            "owner": "content owner",
            "how_to_fix": ["Write the address into the page."],
            "rationale": "An assistant quotes what the page says.",
        },
        "_skill": skill,
    }


def _results(*findings):
    """One skill result per skill named on the findings, in marketplace order."""
    by_skill = {}
    for finding in findings:
        finding = dict(finding)
        by_skill.setdefault(finding.pop("_skill"), []).append(finding)
    return [{"skill": skill, "findings": by_skill[skill], "signals": {},
             "checks_run": [], "not_applicable": [], "fired_checks": [],
             "extra_requests_made": 0}
            for skill in compose.SKILL_ORDER if skill in by_skill]


def _page(url, body):
    return {"url": url, "final_url": url, "status": 200, "title": "A page",
            "page_type": "content", "body_text": body, "paragraphs": [body]}


def _snapshot():
    pages = [
        _page(HOME, "Loom and Larder repairs cane chairs in a workshop by the river."),
        _page(CONTACT, "Write to the workshop or call during opening hours on weekdays."),
        _page(DELIVERED, "The journal records every chair the workshop has re-caned."),
    ]
    return {
        "site": "shop.test",
        "origin": "https://shop.test",
        "brand": {"name": "Loom and Larder", "host": "shop.test"},
        "pages": pages,
        "sitemaps": [],
        "crawl": {"pages_crawled": 3, "pages_ok": 3, "pages_read": 3,
                  "render_mode": "static", "user_agent": "test-agent",
                  "elapsed_s": 1.0, "notes": []},
    }


def _report(*findings):
    return compose.compose(_snapshot(), _results(*findings),
                           audited_at="2026-01-01T00:00:00Z")


def _shell(pages=(HOME, CONTACT)):
    """The finding that says those pages deliver no text of their own."""
    return _finding("content-pages-are-javascript-shells", "js-shell", pages,
                    severity="critical", mechanism="C",
                    skill="render-readability-audit",
                    title="Pages deliver no text without JavaScript",
                    checked=("the delivered HTML",))


def _chrome(pages=(HOME, CONTACT)):
    """The narrower reading: the copy arrived, the layout did not."""
    return _finding(LAYOUT, "js-shell", pages, severity="high", mechanism="C",
                    skill="render-readability-audit",
                    title="The header, navigation and footer are built in the browser",
                    checked=("the delivered HTML",))


LAYOUT = "chrome-is-client-rendered"


def _by_id(report, stable_id):
    for finding in report["findings"]:
        if finding.get("stable_id") == stable_id:
            return finding
    raise AssertionError("{} is not in this report: {}".format(
        stable_id, [f.get("stable_id") for f in report["findings"]]))


# --------------------------------------------------------------------------
# The case that prompted this
# --------------------------------------------------------------------------

def test_a_missing_fact_on_a_page_that_did_not_arrive_names_the_cause():
    """F-007, F-008 and F-010 on the two pages F-001 declares empty."""
    report = _report(
        _shell(),
        _finding("core-fact-missing-founding-year", "missing-core-fact", [HOME, CONTACT]),
        _finding("core-fact-missing-postal-address", "missing-core-fact", [HOME, CONTACT]),
        _finding("heading-structure-unclear", "heading-structure", [HOME]),
    )
    cause = _by_id(report, "content-pages-are-javascript-shells")
    for name in ("core-fact-missing-founding-year", "core-fact-missing-postal-address",
                 "heading-structure-unclear"):
        follows = _by_id(report, name).get("follows_from")
        assert follows, "{} is still billed as independent work".format(name)
        assert follows["id"] == cause["id"]
        assert "JavaScript" in follows["why"] or "text" in follows["why"]


def test_the_consequences_keep_their_severity_their_pages_and_their_place():
    """Not a fold and not a deletion. The four had to stop reading as four
    jobs, not three of them disappear - and "put the
    address in the page text" is real work the moment the page renders."""
    consequences = [
        _finding("core-fact-missing-postal-address", "missing-core-fact",
                 [HOME, CONTACT], severity="high"),
        _finding("heading-structure-unclear", "heading-structure", [HOME]),
    ]
    without = _report(*[dict(f) for f in consequences])
    with_cause = _report(_shell(), *[dict(f) for f in consequences])

    assert with_cause["summary"]["total_findings"] == without["summary"]["total_findings"] + 1
    for name in ("core-fact-missing-postal-address", "heading-structure-unclear"):
        before, after = _by_id(without, name), _by_id(with_cause, name)
        assert after["severity"] == before["severity"]
        assert after["affected_pages"] == before["affected_pages"]
        assert after["affected_page_count"] == before["affected_page_count"]
        assert after["suggested_action"]["how_to_fix"] == \
            before["suggested_action"]["how_to_fix"]
    assert with_cause["summary"]["high"] == without["summary"]["high"]
    assert not with_cause["merged_duplicates"]


def test_a_consequence_does_not_take_a_start_here_slot():
    """"Start here" holds three items and the real report spent three of them
    on one defect. The cause keeps its place; the consequences leave."""
    consequences = [
        _finding("core-fact-missing-postal-address", "missing-core-fact",
                 [HOME, CONTACT], severity="high", mechanism="B"),
        _finding("core-fact-missing-founding-year", "missing-core-fact",
                 [HOME, CONTACT], severity="high", mechanism="B"),
        _finding("heading-structure-unclear", "heading-structure", [HOME],
                 severity="high", mechanism="B"),
    ]
    independent = _finding("title-and-description-hygiene", "meta-hygiene",
                           [DELIVERED], severity="high", mechanism="B",
                           skill="structured-data-audit")

    with_cause = _report(_shell(), independent, *[dict(f) for f in consequences])
    deferred = {_by_id(with_cause, f["id_hint"])["id"] for f in consequences}
    assert not deferred & set(with_cause["start_here"]), (
        "a finding that cannot be fixed until another one is is still first in "
        "the list of what to do first")
    assert _by_id(with_cause, "content-pages-are-javascript-shells")["id"] \
        in with_cause["start_here"]

    # The same report with the cause absent: the consequences are then
    # independent work and belong on the list. This is the before half of the
    # real case, and it is what stops the assertion above passing because
    # they were never candidates.
    without = _report(independent, *[dict(f) for f in consequences])
    assert set(without["start_here"]) & {
        _by_id(without, f["id_hint"])["id"] for f in consequences}


def test_the_reader_is_told_which_finding_to_do_first():
    report = _report(
        _shell(),
        _finding("core-fact-missing-postal-address", "missing-core-fact", [HOME, CONTACT]),
    )
    consequence = _by_id(report, "core-fact-missing-postal-address")
    markdown = compose.render_markdown(report)
    html_text = compose.render_html(report)
    for document in (markdown, html_text):
        assert "This is the same defect as {}".format(consequence["follows_from"]["id"]) \
            in document
    note = compose.deferral_note(consequence)
    assert CONTACT in note and "re-run this audit" in note


# --------------------------------------------------------------------------
# What may not be deferred
# --------------------------------------------------------------------------

def test_a_page_that_did_arrive_keeps_its_own_finding():
    """One page outside the undelivered set and the claim stands on its own:
    it is then also about a page whose text this audit read."""
    report = _report(
        _shell(),
        _finding("core-fact-missing-postal-address", "missing-core-fact",
                 [HOME, CONTACT, DELIVERED]),
    )
    assert not _by_id(report, "core-fact-missing-postal-address").get("follows_from")


def test_a_site_wide_claim_naming_no_page_is_not_deferred():
    """No page list is no subset test. Deferring it would be this file
    asserting that a claim it cannot place is somebody else's."""
    finding = _finding("no-quotable-entity-definition", "no-entity-definition", [])
    finding["affected_page_count"] = 0
    report = _report(_shell(), finding)
    assert not _by_id(report, "no-quotable-entity-definition").get("follows_from")


def test_a_sampled_page_list_is_not_deferred():
    """`affected_pages` is truncated by `make_finding`, so a finding claiming
    more pages than it lists holds addresses this file cannot see and the
    subset test cannot be made on it."""
    finding = _finding("core-fact-missing-postal-address", "missing-core-fact",
                       [HOME, CONTACT])
    finding["affected_page_count"] = 40
    report = _report(_shell(), finding)
    assert not _by_id(report, "core-fact-missing-postal-address").get("follows_from")


def test_a_thin_page_is_not_a_page_that_did_not_arrive():
    """"This page carries almost nothing" is true of a page written thin, and
    "write the address on it" is then the right instruction rather than one
    that cannot work."""
    thin = _finding("pages-too-thin-to-quote", "thin-html", [HOME, CONTACT],
                    severity="high", mechanism="C", skill="render-readability-audit",
                    checked=("the delivered HTML",))
    report = _report(thin,
                     _finding("core-fact-missing-postal-address", "missing-core-fact",
                              [HOME, CONTACT]))
    assert not _by_id(report, "core-fact-missing-postal-address").get("follows_from")


def test_an_access_finding_is_never_deferred():
    """Mechanism A findings are the ones explaining the block, and they are
    what the reader can act on. Same carve-out as `withhold_absence_claims`."""
    report = _report(
        _shell(),
        _finding("no-xml-sitemap", "sitemap-missing", [HOME, CONTACT],
                 mechanism="A", skill="crawl-access-audit",
                 checked=("the conventional locations",)),
    )
    assert not _by_id(report, "no-xml-sitemap").get("follows_from")


# --------------------------------------------------------------------------
# The layout reading is narrower than the document reading
# --------------------------------------------------------------------------

def test_a_client_rendered_layout_defers_only_what_it_explains():
    """`client-rendered-chrome` says the body copy did arrive and the header,
    menu, footer and H1 did not. A founding year missing from text that was
    delivered is a real gap; a missing H1 on the same page is not."""
    report = _report(
        _chrome(),
        _finding("core-fact-missing-founding-year", "missing-core-fact", [HOME, CONTACT]),
        _finding("heading-structure-unclear", "heading-structure", [HOME, CONTACT]),
        _finding("pages-with-no-next-step", "dead-end", [HOME, CONTACT],
                 skill="engagement-audit"),
    )
    assert not _by_id(report, "core-fact-missing-founding-year").get("follows_from")
    for name in ("heading-structure-unclear", "pages-with-no-next-step"):
        assert _by_id(report, name).get("follows_from"), \
            "{} is one of the four symptoms that finding's own evidence names".format(name)


def test_the_cause_never_defers_to_itself():
    report = _report(_shell(), _chrome())
    for name in ("content-pages-are-javascript-shells", LAYOUT):
        assert not _by_id(report, name).get("follows_from")


def test_a_report_with_no_rendering_finding_is_unchanged():
    """Silent on every site that delivers its pages, which is almost all of
    them: no new key, no sentence, no change to the order."""
    report = _report(
        _finding("core-fact-missing-postal-address", "missing-core-fact", [HOME, CONTACT]),
        _finding("heading-structure-unclear", "heading-structure", [HOME]),
    )
    assert all(not f.get("follows_from") for f in report["findings"])
    assert "This is the same defect as" not in compose.render_markdown(report)
