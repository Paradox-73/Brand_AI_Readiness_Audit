"""Four defects in the orchestrator's own report, all in one file.

**Ids.** One site audited twice gave 16 findings then 19. Ids were positional,
so `F-014` was `sitemap-excludes-private-paths` in run A and `article-markup`
in run B - every id after the first insertion referred to a different
problem. Sequential display ids stay, because they are readable and
because `start_here`, `worth_checking` and `leads_the_verdict` are lists of
them. The identity moves to `stable_id`, which the report already published,
which nothing made unique, and which no rendered document printed.

**Rank order.** The top sheet states its own rule - "The ranking puts the most
severe band first" - and broke it on 3 of 6 sites: a `critical` confident
finding ranked third, and on another site a `high` confident finding dropped
from "Start here" in favour of a `low` worth-checking one under a printed line
calling the omission "of equal rank". The sheet is the only page a non-expert
reads.

**The sheet's figures.** The sheet said "Every count below is out of that, and nothing below
rests on more" over a body dividing by a third number it had not named, and
opened "What this site is" with the site's `<title>`.

**Claims about itself.** Every appendix asserted "a second audit of an unchanged site reads
the same pages and reports the same findings", which two runs disproved; and
promised "every check that ran is accounted for" while filing three checks
under "contributed to a finding above" in a report holding no such finding.
"""

from __future__ import annotations

import html
import importlib.util
import os
import re
import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_id_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose()

HOME = "https://shop.test/"


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------

def _finding(id_hint, title="A problem", evidence="Something is wrong here.",
             severity="medium", confidence="high", mechanism="B",
             root_cause="meta-hygiene", pages=(HOME,), checked=("one place",),
             check=None):
    finding = {
        "id_hint": id_hint,
        "title": title,
        "severity": severity,
        "confidence": confidence,
        "evidence": evidence,
        "mechanism": mechanism,
        "root_cause": root_cause,
        "affected_pages": list(pages),
        "affected_page_count": len(pages),
        "checked": list(checked),
        "suggested_action": {
            "summary": "Do the thing.",
            "effort": "low",
            "owner": "content owner",
            "how_to_fix": ["Step one."],
            "rationale": "Because it works.",
        },
    }
    if check:
        finding["check"] = check
    return finding


def _skill_result(*findings, **kwargs):
    return {
        "skill": kwargs.get("skill", "structured-data-audit"),
        "findings": list(findings),
        "signals": kwargs.get("signals", {}),
        "checks_run": kwargs.get("checks_run", []),
        "not_applicable": kwargs.get("not_applicable", []),
        "fired_checks": kwargs.get("fired_checks", []),
        "extra_requests_made": 0,
    }


def _page(url, body="Loom and Larder is a small workshop that repairs cane chairs.",
          **kwargs):
    page = {
        "url": url,
        "final_url": url,
        "status": 200,
        "title": "A page",
        "page_type": "content",
        "body_text": body,
        "paragraphs": [body] if body else [],
    }
    page.update(kwargs)
    return page


def _snapshot(*pages, **kwargs):
    snapshot = {
        "site": "shop.test",
        "origin": "https://shop.test",
        "brand": dict({"name": "Loom and Larder", "host": "shop.test"},
                      **kwargs.get("brand", {})),
        "pages": list(pages),
        "sitemaps": [],
        "crawl": dict({"pages_crawled": len(pages), "pages_ok": len(pages),
                       "pages_read": len(pages), "render_mode": "static",
                       "user_agent": "test-agent", "elapsed_s": 1.0, "notes": []},
                      **kwargs.get("crawl", {})),
    }
    return snapshot


def _site(count=4, **kwargs):
    pages = [_page(HOME, page_type="home")]
    pages += [_page("https://shop.test/p{}".format(i),
                    body="Page {} lists {} chairs waiting for new cane seats.".format(i, i))
              for i in range(1, count)]
    return _snapshot(*pages, **kwargs)


def _report(*results, **kwargs):
    return compose.compose(kwargs.get("snapshot") or _site(), list(results),
                           audited_at="2026-01-01T00:00:00Z")


def _by_stable_id(report):
    return {f["stable_id"]: f for f in report["findings"]}


# --------------------------------------------------------------------------
# An id should name the finding, not its position in a list
# --------------------------------------------------------------------------

def test_the_display_id_still_runs_from_one_with_no_gaps():
    """The half that may not break. `tests/test_runtime.py` asserts it over
    real audits; asserted here as well because the change that would break it
    is in this file."""
    report = _report(_skill_result(
        _finding("a", severity="critical"), _finding("b", severity="high"),
        _finding("c"), _finding("d", severity="low")))
    ids = [f["id"] for f in report["findings"]]
    assert ids == ["F-{:03d}".format(i) for i in range(1, len(ids) + 1)]


def test_a_finding_keeps_its_name_when_a_more_severe_one_is_inserted():
    """The defect, in the shape it happened.

    Run A has four findings, run B has five, and the extra one sorts above the
    one being compared. Every F-number after the insertion moves; the name does
    not, which is the whole point of having two.
    """
    common = [_finding("sitemap-lists-private-paths", severity="high"),
              _finding("article-markup"),
              _finding("no-meta-description", severity="low")]
    run_a = _report(_skill_result(*common))
    run_b = _report(_skill_result(
        _finding("robots-blocks-all-crawlers", severity="critical"), *common))

    moved = {f["stable_id"]: f["id"] for f in run_a["findings"]}
    after = {f["stable_id"]: f["id"] for f in run_b["findings"]}
    assert set(moved) < set(after), "run B should hold run A's findings and one more"
    assert any(moved[name] != after[name] for name in moved), (
        "this fixture is meant to renumber: if nothing moved it cannot show that "
        "the name is what survives")
    for name in moved:
        assert name in after, "{} lost its name between two runs".format(name)


def test_the_name_is_unique_within_one_report():
    """A label that points at two findings is the same defect one level down.

    A check that fires per page raises two findings under one `id_hint`, and
    `stable_id` was `id_hint` with nothing guaranteeing anything.
    """
    report = _report(_skill_result(
        _finding("article-markup", pages=("https://shop.test/blog/one",)),
        _finding("article-markup", pages=("https://shop.test/blog/two",)),
        _finding("no-meta-description", severity="low")))
    names = [f["stable_id"] for f in report["findings"]]
    assert len(names) == len(set(names)), "two findings share one name: {}".format(names)


def test_two_findings_of_one_check_are_told_apart_by_their_first_page():
    """The obvious fix - key the id to `check + first affected URL`
    - applied where it is needed and nowhere else. `article-markup` stays
    readable when one finding carries it; the URL joins only the ones that
    would otherwise collide, and a digest joins neither."""
    report = _report(_skill_result(
        _finding("article-markup", pages=("https://shop.test/blog/one",)),
        _finding("article-markup", pages=("https://shop.test/blog/two",))))
    names = sorted(f["stable_id"] for f in report["findings"])
    assert names == ["article-markup@/blog/one", "article-markup@/blog/two"]
    for name in names:
        assert not re.search(r"[0-9a-f]{8}", name), \
            "{} reads like a hash; a person has to be able to say this out loud".format(name)


def test_the_name_does_not_move_when_the_pages_arrive_in_another_order():
    """Two crawls can attach one finding's pages in a different order. A name
    that moved with page order would be the defect it exists to remove."""
    forward = _report(_skill_result(
        _finding("article-markup", pages=("https://shop.test/a", "https://shop.test/b")),
        _finding("article-markup", pages=("https://shop.test/c",))))
    backward = _report(_skill_result(
        _finding("article-markup", pages=("https://shop.test/b", "https://shop.test/a")),
        _finding("article-markup", pages=("https://shop.test/c",))))
    assert (sorted(f["stable_id"] for f in forward["findings"])
            == sorted(f["stable_id"] for f in backward["findings"]))


def test_both_documents_print_the_name_and_say_which_id_moves():
    """It existed in report.json and in neither rendered document, so a reader
    comparing two audits by eye had only the number that moves."""
    report = _report(_skill_result(
        _finding("sitemap-lists-private-paths", severity="high"),
        _finding("article-markup")))
    markdown = compose.render_markdown(report)
    page = compose.render_html(report)
    assert compose.ID_NOTE in markdown
    assert html.escape(compose.ID_NOTE) in page
    for finding in report["findings"]:
        assert finding["stable_id"] in markdown, \
            "{} is not printed in report.md".format(finding["stable_id"])
        assert finding["stable_id"] in page, \
            "{} is not printed in report.html".format(finding["stable_id"])


def test_the_name_reaches_the_lower_tier_too():
    """A worth-checking finding is a finding somebody re-runs the audit
    against, so it needs the handle as much as a confident one does."""
    report = _report(_skill_result(_finding("shaky", confidence="medium")))
    assert report["worth_checking"], "this fixture is meant to demote its finding"
    markdown = compose.render_markdown(report)
    assert compose.ID_NOTE in markdown
    assert "`shaky`" in markdown


# --------------------------------------------------------------------------
# Severity is the outer sort key of the sheet
# --------------------------------------------------------------------------

def _verdict_led_report(*extra):
    """A report whose verdict names its cause, plus whatever else is passed.

    `verdict_diagnosis` fires on a `no-org-schema` or `no-entity-definition`
    finding beside thin off-site corroboration, which is the shape three of the
    six sites had.
    """
    return _report(
        _skill_result(
            _finding("no-organization-schema", title="No Organization markup anywhere on the site",
                     evidence="No page carries it.", root_cause="no-org-schema",
                     severity="medium"),
            *extra),
        _skill_result(
            _finding("thin-profiles",
                     title="The brand is corroborated nowhere off its own site",
                     evidence="One profile was found.", root_cause="weak-corroboration",
                     severity="low", pages=()),
            skill="freshness-corroboration-audit", signals={"profile_breadth": 0}))


def test_a_critical_finding_leads_the_sheet_over_the_verdict_s_own_cause():
    """The first case: F-001 critical and confident, "the single worst
    thing" naming a medium, and the critical ranked third."""
    report = _verdict_led_report(
        _finding("robots-blocks-all-crawlers", title="Crawlers are blocked outright",
                 evidence="robots.txt disallows everything.", severity="critical",
                 mechanism="A", root_cause="robots-block", checked=()))
    assert report["leads_the_verdict"], "this fixture is meant to trip the diagnosis"
    by_id = {f["id"]: f for f in report["findings"]}
    first = by_id[report["start_here"][0]]
    assert first["severity"] == "critical", (
        "the most severe finding is not first: {}".format(
            [(i, by_id[i]["severity"]) for i in report["start_here"]]))
    _, worst, _ = compose.top_sheet(report)[1]
    assert worst.startswith(first["id"] + " — ")


def test_start_here_is_in_severity_order_throughout():
    report = _verdict_led_report(
        _finding("robots-blocks-all-crawlers", severity="critical", mechanism="A",
                 root_cause="robots-block", checked=()),
        _finding("no-h1", severity="high", root_cause="heading-structure"))
    by_id = {f["id"]: f for f in report["findings"]}
    bands = [compose.SEVERITY_RANK[by_id[i]["severity"]] for i in report["start_here"]]
    assert bands == sorted(bands), [by_id[i]["severity"] for i in report["start_here"]]


def test_a_low_worth_checking_finding_never_displaces_a_high_confident_one():
    """The third and worst case: a `high` confident finding dropped from
    "Start here" in favour of a `low` finding from the tier this list is not
    drawn from, under the line "1 further serious finding of equal rank is
    listed in full below rather than here"."""
    report = _verdict_led_report(
        _finding("no-h1", title="No page carries an H1",
                 evidence="None of the pages read has one.", severity="high",
                 root_cause="heading-structure"))
    by_id = {f["id"]: f for f in report["findings"]}
    high = [f for f in report["findings"] if f["severity"] == "high"]
    assert len(high) == 1
    assert high[0]["id"] in report["start_here"], (
        "a high-severity confident finding is not on Start here while {} are".format(
            [by_id[i]["severity"] for i in report["start_here"]]))
    assert report["start_here"][0] == high[0]["id"]


def test_the_verdict_s_cause_keeps_a_place_even_when_outranked():
    """The rule the exception exists for, which is not "put it first".

    Three findings more severe than the one the verdict names still leaves the
    named one on the list, because a report whose prose calls something the
    cause of the rest and whose action list omits it has failed in the most
    visible place there is.
    """
    report = _verdict_led_report(
        _finding("a", severity="critical", mechanism="A", root_cause="robots-block",
                 checked=()),
        _finding("b", severity="critical", mechanism="A", root_cause="noindex",
                 checked=()),
        _finding("c", severity="high", root_cause="heading-structure"))
    assert report["leads_the_verdict"]
    assert len(report["start_here"]) == 3
    assert report["leads_the_verdict"][0] in report["start_here"], (
        "the verdict names a cause that Start here omits")
    by_id = {f["id"]: f for f in report["findings"]}
    bands = [compose.SEVERITY_RANK[by_id[i]["severity"]] for i in report["start_here"]]
    assert bands == sorted(bands)


def test_the_sheet_names_the_demotion_rather_than_calling_it_a_tie():
    """Where the verdict's exception holds the last slot over a more severe
    finding, the reader is told which finding and why - the one case this
    report already handled well. What it may not do is print
    "of equal rank" over an omission that is not of equal rank."""
    report = _verdict_led_report(
        _finding("a", severity="critical", mechanism="A", root_cause="robots-block",
                 checked=()),
        _finding("b", severity="critical", mechanism="A", root_cause="noindex",
                 checked=()),
        _finding("c", severity="high", root_cause="heading-structure"))
    markdown = compose.render_markdown(report)
    section = markdown[markdown.index("## Start here"):]
    section = section[:section.index("\n## ")]
    assert "of equal rank" not in section
    assert "names it as the cause of the rest" in section


# --------------------------------------------------------------------------
# The top sheet's own figures
# --------------------------------------------------------------------------

def test_the_sheet_states_a_bound_it_can_keep():
    """"53 URLs were crawled and 49 pages came back with readable text. Every
    count below is out of that, and nothing below rests on more" - printed over
    a body saying "41 of the 53 pages" and "19 of 53 crawled page(s)". The
    denominator is neither of the two numbers the sentence pointed at."""
    snapshot = _site(6, crawl={"pages_crawled": 9, "pages_ok": 6})
    report = _report(_skill_result(_finding("a")), snapshot=snapshot)
    _, read, _ = compose.top_sheet(report)[3]
    assert "nothing below rests on more" not in read
    assert "No page count below is out of more than those 9" in read


def test_no_finding_divides_by_more_than_the_sheet_allows():
    """The bound the sheet states, asserted against the sentences below it.

    Three denominators are in play - the pages that answered, one page type,
    and whatever a check that sized itself used - and the sheet can only name
    what is true of all three.
    """
    snapshot = _site(6, crawl={"pages_crawled": 9, "pages_ok": 6})
    report = _report(_skill_result(
        _finding("a", pages=("https://shop.test/p1", "https://shop.test/p2"),
                 root_cause="no-org-schema", evidence="No page carries it.")),
        snapshot=snapshot)
    named = report["crawl"]["pages_crawled"]
    printed = [f["evidence"] for f in report["findings"]]
    # Both shapes `state_the_denominator` writes: "Seen on 2 of the 6 pages
    # this crawl read" for a per-page count, and "Checked all 5 content pages
    # this crawl read" for a site-wide absence claim, whose population is the
    # one page type every affected page turned out to be.
    fractions = {int(m) for text in printed
                 for m in re.findall(r"\b(?:of(?: the)?|all)\s+(\d+)\b", text)}
    assert fractions, "this fixture is meant to size its finding: {}".format(printed)
    for denominator in fractions:
        assert denominator <= named, (
            "a finding divides by {} and the sheet says no count below is out of more "
            "than {}".format(denominator, named))


def test_a_page_title_is_not_presented_as_the_site_s_name():
    """One sheet opened "What this site is. Best handcrafted Jute products
    Online at ..." - the site's `<title>` - on a site whose own appendix
    records that it declares no `og:site_name` and no Organization `name`."""
    snapshot = _site(4, brand={"name": "Best handcrafted Jute products Online",
                               "source": "title-part-0"})
    report = _report(_skill_result(_finding("a")), snapshot=snapshot)
    _, what, _ = compose.top_sheet(report)[0]
    assert "homepage title" in what
    assert "og:site_name" in what
    assert not what.startswith("Best handcrafted Jute products Online at "), (
        "the title is still being presented as the name: {}".format(what))


def test_a_declared_name_is_still_printed_as_the_name():
    """The guard fires on a title source and on nothing else. A site that
    declares who it is gets the sentence it always got."""
    snapshot = _site(4, brand={"name": "Loom and Larder", "source": "og:site_name"})
    report = _report(_skill_result(_finding("a")), snapshot=snapshot)
    _, what, _ = compose.top_sheet(report)[0]
    assert what.startswith("Loom and Larder at shop.test.")


# --------------------------------------------------------------------------
# The report's claims about itself
# --------------------------------------------------------------------------

_CRAWLERS_OWN_PROMISE = (
    "a page of this site takes about 1.4s to read, so a 142s crawl plans for 20 page(s) "
    "and stops at that count rather than wherever the clock happens to fall. How much of "
    "the site is read is decided by the budget and by the site's own speed, so a second "
    "audit of an unchanged site reads the same pages and reports the same findings.")


def _with_a_plan(deterministic, truncated_at=None, notes=(_CRAWLERS_OWN_PROMISE,)):
    return _site(4, crawl={
        "notes": list(notes),
        "page_plan": {"planned_pages": 20, "measured_cost_s": 1.4, "cost_rung_s": 2.0,
                      "deterministic": deterministic, "truncated_at": truncated_at},
    })


def test_a_cut_short_crawl_does_not_promise_the_same_pages_next_time():
    """Two runs disproved the promise: 53 pages then 60, 16
    findings then 19. Where `page_plan.deterministic` is false the sentence
    says so instead of restating the promise."""
    report = _report(_skill_result(_finding("a")),
                     snapshot=_with_a_plan(False, "https://shop.test/p9"))
    note = compose.repeatability_note(report)
    assert "may read a different set" in note
    assert "reports the same findings" not in note
    for rendered in (compose.render_markdown(report), compose.render_html(report)):
        assert "may read a different set" in rendered


def test_the_crawler_s_own_promise_never_reaches_the_document():
    """The claim was being made in `crawl.py`, while planning, by the one
    component that cannot see whether it came true. The measurement in front of
    it is a fact and stays."""
    report = _report(_skill_result(_finding("a")), snapshot=_with_a_plan(False))
    notes = report["crawl"]["notes"]
    assert notes, "the measurement half of the note was thrown away with the promise"
    assert any("takes about 1.4s to read" in note for note in notes)
    for note in notes:
        assert "second audit" not in note
        assert "reports the same findings" not in note
    markdown = compose.render_markdown(report)
    assert "a second audit of an unchanged site reads the same pages" not in markdown


def test_the_promise_is_made_where_the_plan_fixed_the_page_count():
    """Not a rule against saying it - a rule about saying it from the field
    that settles it, and saying what makes it true rather than restating it."""
    report = _report(_skill_result(_finding("a")), snapshot=_with_a_plan(True))
    note = compose.repeatability_note(report)
    assert "reads the same pages and reports the same findings" in note
    assert "fixed by the plan rather than by where the clock fell" in note
    assert "2.0s" in note, "the sentence should say what makes it true"


def test_a_snapshot_with_no_plan_claims_nothing_either_way():
    report = _report(_skill_result(_finding("a")), snapshot=_site(4))
    note = compose.repeatability_note(report)
    assert "cannot say" in note
    assert "reads the same pages and reports the same findings" not in note


def test_a_check_is_not_called_a_contributor_with_no_finding_to_contribute_to():
    """`sitemap-present`, `sitemap-parses` and `unknown-paths-return-200` filed
    under "contributed to a finding above" in a report containing no such
    finding. The skill's own findings never reached the report, so there was
    nothing above for any of its checks to have contributed to."""
    report = _report(
        _skill_result(_finding("a")),
        _skill_result(skill="crawl-access-audit",
                      checks_run=["sitemap-present", "sitemap-parses",
                                  "unknown-paths-return-200"],
                      fired_checks=["sitemap-present", "sitemap-parses",
                                    "unknown-paths-return-200"]))
    contributed = {c["check"] for c in report["checks_that_found_something"]}
    unpublished = {c["check"] for c in report["checks_that_found_nothing_published"]}
    assert contributed == set()
    assert unpublished == {"sitemap-present", "sitemap-parses", "unknown-paths-return-200"}

    markdown = compose.render_markdown(report)
    heading = "### Checks that contributed to a finding above"
    assert heading not in markdown
    # The heading says what is true of every entry: the check fired and no
    # finding in this report is filed under it. "Found something this report
    # does not publish" was true of these three and not of the case that
    # followed - a skill that published some of what it raised, where which
    # check belonged to the lost finding is not recorded anywhere.
    assert "### Checks that fired, with no finding above filed under them" in markdown


def test_every_check_that_ran_is_still_in_exactly_one_place():
    """Splitting the bucket may not lose one. The appendix's own arithmetic is
    the thing a reader adds up."""
    report = _report(
        _skill_result(_finding("a", check="org-markup"),
                      checks_run=["org-markup", "org-markup-sibling"],
                      fired_checks=["org-markup", "org-markup-sibling"]),
        _skill_result(skill="crawl-access-audit", checks_run=["sitemap-present"],
                      fired_checks=["sitemap-present"]))
    ran = {(c["skill"], c["check"]) for c in report["checks_run"]}
    buckets = set()
    for key in ("checks_passed", "checks_that_found_something",
                "checks_that_found_nothing_published",
                "checks_with_nothing_to_run_against"):
        buckets |= {(c["skill"], c["check"]) for c in report.get(key) or []}
    buckets |= {(n.get("skill") or "", n["check"]) for n in report["not_applicable"]}
    buckets |= {(f.get("detected_by") or "", f.get("check"))
                for f in report["findings"] if f.get("check")}
    assert not (ran - buckets), "check(s) accounted for nowhere: {}".format(
        sorted(ran - buckets))
    assert ("structured-data-audit", "org-markup-sibling") in {
        (c["skill"], c["check"]) for c in report["checks_that_found_something"]}, (
            "a sibling of a published finding's check is a genuine contributor")
