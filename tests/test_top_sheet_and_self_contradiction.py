"""The page a non-expert reads, and the promise that it does not lie to itself.

Two defects, both against `compose_report.py`.

**A one-page version.** "66-70 KB is more than a non-expert reads, so add a
one-page top sheet." The same review praised the severity x confidence table, the three-item
"Start here" with owner and effort, the four-part layout under every finding,
the table of what an assistant would quote and the appendix naming every quiet
check - and then named the balance: "the appendix is longer than the findings".
So the fix is not a deletion. It is a page at the front answering five
questions in order, with everything below it unchanged.

**No self-contradictions.** "Stop shipping self-contradictions (site 5 F-001
says no page defines the brand, F-009 says 'the opening text does explain what
this is')." The same review had found three more pairs earlier. Each was fixed
inside the check that raised it, one at a time, and the shape came back - it
has to, because no check can see what another check published. The guard is in
the orchestrator, asked of every pair.
"""

from __future__ import annotations

import html
import importlib.util
import json
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_top_sheet_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose()


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------

HOME = "https://shop.test/"


def _finding(id_hint, title, evidence, severity="medium", confidence="high",
             mechanism="B", root_cause="meta-hygiene", pages=(HOME,),
             checked=("one place",), how_to_fix=("Step one.",)):
    return {
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
            "how_to_fix": list(how_to_fix),
            "rationale": "Because it works.",
        },
    }


def _skill_result(*findings, **kwargs):
    return {
        "skill": kwargs.get("skill", "structured-data-audit"),
        "findings": list(findings),
        "signals": kwargs.get("signals", {}),
        "checks_run": kwargs.get("checks_run", []),
        "not_applicable": kwargs.get("not_applicable", []),
        "fired_checks": [],
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
    return {
        "site": "shop.test",
        "origin": "https://shop.test",
        "brand": {"name": "Loom and Larder", "host": "shop.test"},
        "pages": list(pages),
        "sitemaps": [],
        "crawl": dict({"pages_crawled": len(pages), "pages_ok": len(pages),
                       "pages_read": len(pages), "render_mode": "static",
                       "user_agent": "test-agent", "elapsed_s": 1.0, "notes": []},
                      **kwargs.get("crawl", {})),
    }


def _site(count=4):
    # Every page carries a different sentence, because a sentence repeated
    # across three pages is that site's furniture and the citation table
    # deliberately refuses to offer it as any one page's content.
    pages = [_page(HOME, page_type="home")]
    pages += [_page("https://shop.test/p{}".format(i),
                    body="Page {} lists {} chairs waiting for new cane seats.".format(i, i))
              for i in range(1, count)]
    return _snapshot(*pages)


def _report(*results, **kwargs):
    return compose.compose(kwargs.get("snapshot") or _site(), list(results),
                           audited_at="2026-01-01T00:00:00Z")


def _top_sheet_text(report):
    """The rendered top sheet on its own, cut out of report.md."""
    markdown = compose.render_markdown(report)
    start = markdown.index("## " + compose.TOP_SHEET_HEADING)
    return markdown[start:markdown.index("\n## ", start + 1)]


# --------------------------------------------------------------------------
# The one-page version
# --------------------------------------------------------------------------

def test_the_top_sheet_is_the_first_section_of_the_report():
    """"A one-page top sheet" means the first page, not a second summary
    somewhere in the middle. It sits above the verdict, because the verdict is
    a paragraph of prose and the complaint was about scanning, not length."""
    markdown = compose.render_markdown(_report(_skill_result()))
    assert markdown.startswith("# AI readiness audit:")
    assert compose.TOP_SHEET_HEADING in markdown
    assert markdown.index(compose.TOP_SHEET_HEADING) < markdown.index("## The short version")


def test_the_top_sheet_answers_the_five_questions_in_order():
    """The five asked for, in the order somebody who has never opened the report
    needs them. Order is part of the ask: what this is, then the worst of it,
    then what to do, then how much was read, then what did not run."""
    labels = [label for label, _, _ in compose.top_sheet(_report(_skill_result()))]
    assert labels == [
        "What this site is",
        "The single worst thing",
        "What to do first",
        "How much of the site was read",
        "What part of the audit did not run",
    ]


def test_the_top_sheet_names_the_site_and_quotes_its_own_homepage():
    text = _top_sheet_text(_report(_skill_result()))
    assert "Loom and Larder at shop.test" in text
    # The site's own words rather than a description of them: this is the
    # sentence the citation table below says an assistant would lift.
    assert "repairs cane chairs" in text


def test_the_top_sheet_says_so_when_the_homepage_has_nothing_to_quote():
    """The absence is the answer to "what is this site", not a missing bullet."""
    snapshot = _snapshot(_page(HOME, body="Home", page_type="home"),
                         _page("https://shop.test/p1"))
    text = _top_sheet_text(_report(_skill_result(), snapshot=snapshot))
    assert "no sentence to repeat about what this is" in text


def test_the_three_fixes_carry_an_owner_and_a_duration():
    """"With who does each and how long" - the two facts an owner needs before
    they can tell whether a fix is theirs to make."""
    report = _report(_skill_result(
        _finding("a", "Titles repeat", "Six pages share one title.", severity="high"),
        _finding("b", "No summary", "Four pages carry no meta description."),
        _finding("c", "No image tags", "Two pages carry no image tag.", severity="low")))
    _, sentence, fixes = compose.top_sheet(report)[2]
    assert sentence == "3 fixes, in this order."
    assert len(fixes) == len(report["start_here"])
    for entry in fixes:
        assert "content owner" in entry
        assert "under an hour" in entry


def test_the_worst_thing_is_the_report_s_own_first_instruction():
    report = _report(_skill_result(
        _finding("a", "Titles repeat", "Six pages share one title.", severity="high"),
        _finding("b", "No summary", "Four pages carry no meta description.")))
    _, worst, _ = compose.top_sheet(report)[1]
    assert worst.startswith(report["start_here"][0] + " — ")


def test_the_worst_thing_and_the_first_fix_are_never_two_different_findings():
    """Both read out of `start_here`, which is the one list that has already
    applied the verdict's exception. Read from `leads_the_verdict` instead, one
    fixture named a low-severity corroboration finding as the worst thing about
    a site whose own "do this first" was missing identity markup - the top
    sheet contradicting itself four lines apart."""
    report = _report(
        _skill_result(_finding(
            "no-organization-schema", "No Organization markup anywhere on the site",
            "No page carries it.", root_cause="no-org-schema", severity="high")),
        _skill_result(_finding(
            "thin-profiles", "The brand is corroborated nowhere off its own site",
            "One profile was found.", root_cause="weak-corroboration",
            severity="low", pages=()), skill="freshness-corroboration-audit",
            signals={"profile_breadth": 0}))
    assert report["leads_the_verdict"], "this fixture is meant to trip the diagnosis"
    _, worst, _ = compose.top_sheet(report)[1]
    _, _, fixes = compose.top_sheet(report)[2]
    first = report["start_here"][0]
    title = next(f["title"] for f in report["findings"] if f["id"] == first).rstrip(".")
    assert worst.startswith(first + " — ")
    assert fixes[0].startswith(title)


def test_the_verdict_s_cause_is_marked_when_it_is_the_worst_thing():
    report = _report(
        _skill_result(_finding(
            "no-organization-schema", "No Organization markup anywhere on the site",
            "No page carries it.", root_cause="no-org-schema", severity="high")),
        _skill_result(_finding(
            "thin-profiles", "The brand is corroborated nowhere off its own site",
            "One profile was found.", root_cause="weak-corroboration",
            severity="low", pages=()), skill="freshness-corroboration-audit",
            signals={"profile_breadth": 0}))
    _, worst, _ = compose.top_sheet(report)[1]
    assert report["start_here"][0] in report["leads_the_verdict"]
    assert "cause of most of the rest" in worst


def test_the_top_sheet_reports_what_was_read_from_the_crawl_block():
    report = _report(_skill_result(), snapshot=_site(4))
    _, read, _ = compose.top_sheet(report)[3]
    assert "4 URLs were crawled" in read
    assert "{} came back with readable text".format(
        compose._plural(report["crawl"]["readable_pages"], "page", "pages")) in read


def test_a_crawl_that_read_almost_nothing_does_not_read_reassuringly_at_the_top():
    """The failure `_coverage_caveat_lines` exists to stop, one page higher up.
    A top sheet is the part a non-expert reads; a reassuring one over a crawl
    that saw nothing is worse than no top sheet at all."""
    snapshot = _snapshot(_page(HOME, body="", page_type="home"))
    report = _report(_skill_result(), snapshot=snapshot)
    assert report["crawl"]["enough_to_judge"] is False
    _, read, _ = compose.top_sheet(report)[3]
    assert "too little to grade the site" in read
    assert "found little, not that there is little to find" in read


def test_a_dropped_sub_skill_is_named_on_the_top_sheet():
    """An earlier fix put this in the header. It belongs on the page in front of the
    header too: a count of findings means nothing without what it is out of."""
    result = _skill_result(skill="engagement-audit", not_applicable=[{
        "skill": "engagement-audit",
        "check": compose.SKILL_DID_NOT_RUN_CHECK,
        "reason": "It had 12s left of a 300s budget.",
        "did_not_run": True}])
    report = _report(_skill_result(), result)
    _, did_not_run, _ = compose.top_sheet(report)[4]
    assert "engagement-audit did not run" in did_not_run
    assert "not as a pass" in did_not_run


def test_a_complete_run_says_so_without_inventing_a_denominator():
    _, did_not_run, _ = compose.top_sheet(_report(_skill_result()))[4]
    assert did_not_run.startswith("Every sub-skill ran.")
    assert not re.search(r"\d", did_not_run.split(".")[0])


def _integers_the_top_sheet_wrote_itself(report):
    """Every number on the top sheet that is not copied text.

    A title, an action summary and the homepage's own sentence are strings
    lifted whole out of the report, so a figure inside one of them is already
    the report's. What is left is what this page composed, and that is what may
    not be a second count.
    """
    text = " ".join("{} {}".format(sentence, " ".join(items))
                    for _, sentence, items in compose.top_sheet(report))
    text = re.sub(r'"[^"]*"', " ", text)
    for finding in report["findings"]:
        text = text.replace(finding["title"].rstrip("."), " ")
        text = text.replace(finding["suggested_action"]["summary"], " ")
    return {int(n) for n in re.findall(r"\d+", text)}


def test_the_top_sheet_invents_no_number():
    """"Every number on it must already exist elsewhere in the report." A top
    sheet that computes anything of its own is a second source of truth and
    will eventually disagree with the body, on the site where it matters."""
    report = _report(_skill_result(
        _finding("a", "Titles repeat", "Six pages share one title.", severity="high"),
        _finding("b", "No summary", "Four pages carry no meta description.")))
    held = _values_held_by(report)
    for number in _integers_the_top_sheet_wrote_itself(report):
        assert number in held, (
            "{} appears on the top sheet and nowhere in the report it summarises"
            .format(number))


def _values_held_by(report):
    """Every integer in the report, plus the length of every list in it."""
    out = set()

    def walk(value):
        if isinstance(value, bool):
            return
        if isinstance(value, int):
            out.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            out.add(len(value))
            for item in value:
                walk(item)

    walk(report)
    return out


def test_the_top_sheet_moves_when_the_report_moves():
    """Read out of the report rather than held beside it. Change the report and
    the top sheet changes with it; hold a copy and it does not."""
    report = _report(_skill_result(
        _finding("a", "Titles repeat", "Six pages share one title.", severity="high")))
    before = compose.top_sheet(report)
    report["crawl"]["readable_pages"] = 41
    report["crawl"]["pages_crawled"] = 44
    after = compose.top_sheet(report)
    assert before[3] != after[3]
    assert "44 URLs were crawled" in after[3][1]
    assert "41 pages came back with readable text" in after[3][1]


def test_both_renderers_print_the_same_top_sheet():
    """The same comparison on a different section once found report.md and
    report.html disagreeing. The page most likely to be the only page
    somebody reads may not be the next one."""
    report = _report(_skill_result(
        _finding("a", "Titles repeat", "Six pages share one title.", severity="high"),
        _finding("b", "No summary", "Four pages carry no meta description.")))
    markdown = compose.render_markdown(report)
    page = compose.render_html(report)
    assert compose.TOP_SHEET_HEADING in markdown and compose.TOP_SHEET_HEADING in page
    for label, sentence, items in compose.top_sheet(report):
        for fragment in [label, sentence] + list(items):
            assert fragment in markdown, "{} missing from report.md".format(fragment)
            # The HTML document escapes what it prints, so the quotation marks
            # round the homepage's own sentence arrive as `&quot;`. Same text,
            # one encoding apart.
            assert html.escape(fragment) in page, \
                "{} missing from report.html".format(fragment)
    # And in front of the grades in both, not tucked under them.
    assert page.index(compose.TOP_SHEET_HEADING) < page.index("Appendix")


def test_the_top_sheet_adds_nothing_a_reader_cannot_reach_below_it():
    """Nothing was removed to make room for it. The review said the body is
    not padding, and every section it named is still there."""
    report = _report(_skill_result(
        _finding("a", "Titles repeat", "Six pages share one title.", severity="high")))
    markdown = compose.render_markdown(report)
    for heading in ("## The short version", "## Start here",
                    "## Appendix: what was checked"):
        assert heading in markdown


# --------------------------------------------------------------------------
# One page, one answer
# --------------------------------------------------------------------------

def _the_real_pair():
    """The exact pair from the real report, from the two skills that raise it.

    `fact-extractability-audit` says no page states what the brand is;
    `engagement-audit`, reading the same homepage, says its opening text does.
    Both name the homepage in `affected_pages`.
    """
    return (
        _skill_result(
            _finding("no-page-defines-the-brand",
                     "No page states in one sentence what the brand is",
                     'No sentence of the form "Loom and Larder is a ...", "We are a ..." '
                     'or "I am a ..." appears in the first 120 words or the opening '
                     'headings of {}.'.format(HOME),
                     root_cause="no-entity-definition", severity="high"),
            skill="fact-extractability-audit"),
        _skill_result(
            _finding("homepage-does-not-orient-visitors",
                     "The homepage explains itself in prose but not in its headings",
                     "The homepage has no h1; the opening text does explain what this is, "
                     "so this is a headings and markup problem rather than a visitor who "
                     "cannot tell where they are.",
                     root_cause="no-orientation", severity="low"),
            skill="engagement-audit"),
    )


def test_the_real_pair_is_caught():
    report = _report(*_the_real_pair())
    disputed = [f for f in report["findings"] if f.get("disputed_by")]
    assert len(disputed) == 1, "the two opposite claims about one homepage were published flat"
    assert disputed[0]["stable_id"] == "no-page-defines-the-brand"
    assert disputed[0]["disputed_by"]["subject"] == (
        "whether this page says, in one sentence, what the site is")


def test_neither_half_of_a_disagreement_is_deleted():
    """A miss costs as much as a false positive, and one of two
    disagreeing findings is usually right. Both stay, at their own severity."""
    report = _report(*_the_real_pair())
    stable = {f["stable_id"] for f in report["findings"]}
    assert stable == {"no-page-defines-the-brand", "homepage-does-not-orient-visitors"}
    assert report["summary"]["total_findings"] == 2
    assert report["summary"]["high"] == 1, "the demoted half kept its severity"


def test_the_weaker_half_is_demoted_and_never_leads_start_here():
    """"High severity, weakly evidenced" is the dangerous combination: it leads
    the list an owner acts on first while being the reading most likely to be
    wrong."""
    report = _report(*_the_real_pair())
    disputed = next(f for f in report["findings"] if f.get("disputed_by"))
    assert disputed["tier"] == compose.WORTH_CHECKING
    assert disputed["id"] in report["worth_checking"]
    assert disputed["id"] not in report["start_here"]


def test_the_disagreement_is_printed_in_both_documents():
    report = _report(*_the_real_pair())
    disputed = next(f for f in report["findings"] if f.get("disputed_by"))
    note = compose.dispute_note(disputed)
    assert disputed["disputed_by"]["id"] in note
    assert HOME in note
    for rendered in (compose.render_markdown(report), compose.render_html(report)):
        assert "The report disagrees with itself here." in rendered
        assert note in rendered


def test_a_page_called_thin_by_one_finding_and_full_by_another_is_caught():
    """The second pair from the same review: one page called "1 page of questions and
    answers" by one finding and "too little text to be quoted" by another.
    "Too little" carries none of the words a plain absence test looks for."""
    report = _report(
        _skill_result(
            _finding("faq-not-marked-up", "A page of questions and answers carries no FAQ markup",
                     "1 page of questions and answers was found and none declares FAQPage.",
                     root_cause="no-faq-schema"),
            skill="structured-data-audit"),
        _skill_result(
            _finding("nothing-to-quote", "A key page carries nothing an assistant could quote",
                     "This page carries too little text to be quoted.",
                     root_cause="thin-html"),
            skill="render-readability-audit"))
    disputed = [f for f in report["findings"] if f.get("disputed_by")]
    assert len(disputed) == 1
    # The basis test settles this pair before the presence test is reached, and
    # it settles it the right way round. Counting the words on a delivered
    # document is a reading of the document; deciding the page is a page of
    # questions and answers is a reading of its prose, and it is the half that
    # was wrong on the real site.
    assert disputed[0]["stable_id"] == "faq-not-marked-up"
    assert "read its answer off the document" in disputed[0]["disputed_by"]["why"]


def test_a_direct_reading_beats_an_inferred_one_whichever_way_it_points():
    """Step one of the rule. `evidence_basis` is derived from the root cause
    rather than written by whoever wrote the check, so it is the one comparison
    here that is not somebody's opinion of their own work.

    `thin-html` is OBSERVED and `no-faq-schema` is INFERRED, so the absence
    claim wins this pair even though it is an absence claim - which is the
    whole point of asking the basis first.
    """
    from audit_common import evidence_basis, OBSERVED
    assert evidence_basis({"root_cause": "thin-html"}) == OBSERVED
    assert evidence_basis({"root_cause": "no-faq-schema"}) != OBSERVED
    absent = {"root_cause": "thin-html", "id_hint": "thin"}
    present = {"root_cause": "no-faq-schema", "id_hint": "faq"}
    loser, winner, why = compose._which_claim_stands(absent, present)
    assert loser is present and winner is absent
    assert "read its answer off the document" in why


def test_two_findings_about_different_pages_are_not_a_contradiction():
    """A site can carry a defining sentence on one page and not on another.
    That is two facts, not a disagreement, and treating it as one would demote
    a true finding for no reason."""
    absent, present = _the_real_pair()
    present["findings"][0]["affected_pages"] = ["https://shop.test/p1"]
    report = _report(absent, present)
    assert not [f for f in report["findings"] if f.get("disputed_by")]


def test_a_site_wide_presence_claim_does_not_silence_one_page_s_absence():
    """The asymmetry, stated as a test. A claim that something is nowhere is
    disproved by one page carrying it. A claim that something is present is not
    a claim about the one page another finding names, so it settles nothing."""
    absent, present = _the_real_pair()
    absent["findings"][0]["affected_pages"] = [HOME]
    present["findings"][0]["affected_pages"] = []
    present["findings"][0]["affected_page_count"] = 0
    report = _report(absent, present)
    assert not [f for f in report["findings"] if f.get("disputed_by")]


def test_a_fix_step_is_not_a_claim_about_the_page_as_it_is():
    """"Replace the H1 with a line naming what this is" is an instruction about
    a page that does not exist yet. Reading it as an assertion makes every
    well-written finding contradict its own remedy, and each of those is a
    false positive."""
    finding = _finding(
        "no-definition", "No page states in one sentence what the brand is",
        "Nothing on the homepage says what the brand is.",
        root_cause="no-entity-definition",
        how_to_fix=["Replace the H1 with a line naming what this is.",
                    "Put the sentence that explains what this is in the first paragraph."])
    report = _report(_skill_result(finding))
    assert not [f for f in report["findings"] if f.get("disputed_by")]


def test_a_brand_name_carrying_a_denial_word_is_not_read_as_one():
    """A real medical charity carries the word "without" in its registered
    name. Quoted spans come out before the sentence is read, or every finding
    naming such a brand reads as a denial of whatever else is in the clause."""
    assert compose._stance_on(
        'The homepage of "Chairs Without Borders" does explain what this is.',
        compose.CONTRADICTION_SUBJECTS[0]["matches"]) == compose.CLAIM_PRESENT


def test_a_subject_phrase_that_contains_its_own_denial_word_reads_correctly():
    """"Delivers its text without JavaScript" is an assertion that it does. The
    `without` is part of the subject, not a denial of it, and counting it as
    one inverted the answer."""
    pattern = compose.CONTRADICTION_SUBJECTS[2]["matches"]
    assert compose._stance_on(
        "This page delivers its text without JavaScript.", pattern) == compose.CLAIM_PRESENT
    assert compose._stance_on(
        "This page delivers no readable text until JavaScript runs.",
        pattern) == compose.CLAIM_ABSENT


def test_a_finding_that_says_both_things_is_left_alone():
    """A finding that contradicts itself is not a claim this file can weigh
    against another one, and guessing which half it meant is how a guard like
    this starts producing what it exists to remove."""
    both = ("The homepage never says what the brand is. The about page does say what "
            "the brand is.")
    assert compose._stance_on(both, compose.CONTRADICTION_SUBJECTS[0]["matches"]) is None


def test_an_ordinary_report_carries_no_dispute_key_at_all():
    """The guard is silent on a healthy build, and `disputed_by` being absent
    from every finding is the evidence that it is."""
    report = _report(_skill_result(
        _finding("a", "Titles repeat", "Six pages share one title.", severity="high"),
        _finding("b", "No summary", "Four pages carry no meta description.")))
    assert not any("disputed_by" in f for f in report["findings"])
    rendered = compose.render_markdown(report)
    assert "The report disagrees with itself here." not in rendered


def test_the_report_json_stays_readable_after_a_dispute():
    """`disputed_by` is published, so a consumer reading `findings[]` can see
    that two entries in the array disagree and which one this audit is entitled
    to assert."""
    report = _report(*_the_real_pair())
    round_tripped = json.loads(json.dumps(report))
    disputed = next(f for f in round_tripped["findings"] if f.get("disputed_by"))
    assert set(disputed["disputed_by"]) == {
        "id", "stable_id", "subject", "pages", "page_count", "why"}
    assert disputed["disputed_by"]["id"] in {f["id"] for f in round_tripped["findings"]}


def test_no_two_published_findings_are_left_asserting_opposite_things():
    """The promise, stated once as one assertion over the whole document: after
    composition, no pair of published findings takes opposite views of one
    subject about one page without the report saying so."""
    report = _report(*_the_real_pair())
    findings = report["findings"]
    for index, one in enumerate(findings):
        for other in findings[index + 1:]:
            for subject in compose.CONTRADICTION_SUBJECTS:
                ours = compose._stance_on(compose._claim_text(one), subject["matches"])
                theirs = compose._stance_on(compose._claim_text(other), subject["matches"])
                if not ours or not theirs or ours == theirs:
                    continue
                absent, present = (one, other) if ours == compose.CLAIM_ABSENT else (other, one)
                if not compose._pages_both_speak_about(absent, present):
                    continue
                assert one.get("disputed_by") or other.get("disputed_by"), (
                    "{} and {} disagree about {} and the report says nothing".format(
                        one["id"], other["id"], subject["subject"]))
