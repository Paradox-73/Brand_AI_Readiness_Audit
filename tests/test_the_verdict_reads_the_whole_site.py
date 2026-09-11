# -*- coding: utf-8 -*-
"""The verdict said the site has no identity markup while the report reprinted it.

The site was a school's. The verdict paragraph - the first prose anybody reads
- said:

    no page carries Organization or LocalBusiness markup

The site declares `Organization` JSON-LD on 48 of its 60 crawled pages. The
same report reprinted the declared name in its own paste-ready snippet, and two
findings sixty lines below said so outright: "The homepage declares no
Organization markup, though the rest of the site does", and "declares
Organization and omits description, sameAs. The same markup is on 48 pages of
the 60 crawled."

The cause was one boolean. `no-org-schema` is raised by four checks and the
verdict read the *cause*, minus one hint on a denylist - so the two checks that
report a site which **has** the markup both set the flag that prints "no page
carries" it.

The rule this file pins:

    the report may say the homepage lacks the block, and may say the site
    declares it elsewhere, and may not say the site has none when it has 48.

The second half is the same site's top sheet: "nothing in its markup - no
og:site_name, no Organization name - outranked the title", printed over markup
carrying an Organization name on 48 pages. That sentence conflates "nothing
declared" with "nothing outranked the title", and the two are different facts.

Every school, brand and host below is invented.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_verdict_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose()

HOME = "https://sarawadee.test/"
SCHOOL = "Sarawadee Wittaya School"

# The four checks in the marketplace that raise `no-org-schema`, and what each
# one licenses the verdict to say. Read off `skills/structured-data-audit`:
# `_check_organization` raises the first two, `_fold_absent_markup` the third,
# `_check_branch_pages` the fourth.
SAYS_THE_SITE_HAS_NONE = ("no-organization-schema", "no-structured-data-at-all")
SAYS_THE_SITE_HAS_SOME = ("homepage-declares-no-organization-schema",
                          "branch-pages-carry-no-location-markup")


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------

def _finding(id_hint, title="A problem", evidence="Something is wrong here.",
             severity="medium", confidence="high", mechanism="C",
             root_cause="no-org-schema", pages=(HOME,), checked=("one place",)):
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
            "owner": "developer",
            "how_to_fix": ["Step one."],
            "rationale": "Because it works.",
        },
    }


def _skill_result(*findings, **kwargs):
    return {
        "skill": kwargs.get("skill", "structured-data-audit"),
        "findings": list(findings),
        "signals": kwargs.get("signals", {}),
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
        "title": "Sarawadee Wittaya School",
        "page_type": "content",
        "body_text": "Sarawadee Wittaya School teaches six hundred pupils in two buildings.",
        "paragraphs": ["Sarawadee Wittaya School teaches six hundred pupils."],
    }
    page.update(kwargs)
    return page


def _snapshot(brand=None, pages=6):
    urls = [_page(HOME, page_type="home")]
    urls += [_page("{}p{}".format(HOME, i)) for i in range(1, pages)]
    return {
        "site": "sarawadee.test",
        "origin": "https://sarawadee.test",
        "brand": dict({"name": SCHOOL, "host": "sarawadee.test",
                       "source": "jsonld:Organization"}, **(brand or {})),
        "pages": urls,
        "sitemaps": [],
        "crawl": {"pages_crawled": len(urls), "pages_ok": len(urls),
                  "pages_read": len(urls), "render_mode": "static",
                  "user_agent": "test-agent", "elapsed_s": 1.0, "notes": []},
    }


def _thin_profiles():
    """The other half of the identity paragraph's condition: nothing off-site."""
    return _skill_result(
        _finding("weak-corroboration", title="Corroborated nowhere off its own site",
                 root_cause="weak-corroboration", severity="low", pages=()),
        skill="freshness-corroboration-audit", signals={"profile_breadth": 0})


def _report(*results, **kwargs):
    return compose.compose(kwargs.get("snapshot") or _snapshot(), list(results),
                           audited_at="2026-01-01T00:00:00Z")


DENIAL = "no page carries Organization or LocalBusiness markup"


# --------------------------------------------------------------------------
# The case that prompted this
# --------------------------------------------------------------------------

def test_the_verdict_does_not_deny_markup_the_same_report_reprints():
    """The defect, in the shape it was found.

    One finding: the homepage omits the block the rest of the site publishes.
    The verdict used to answer that with "no page carries Organization or
    LocalBusiness markup".
    """
    report = _report(
        _skill_result(_finding(
            "homepage-declares-no-organization-schema",
            title="The homepage declares no Organization markup, though the rest of the "
                  "site does",
            evidence="{} declares no Organization-level JSON-LD type, while 48 of the 60 "
                     "crawled do.".format(HOME))),
        _thin_profiles())
    verdict = report["summary"]["verdict"]
    assert DENIAL not in verdict, verdict
    assert "Nothing on the site says it in a form a machine reads" not in verdict, verdict


def test_the_homepage_gap_can_still_be_named_beside_a_second_identity_gap():
    """The report may say the homepage lacks the block. It may not upgrade that
    into a claim about every page - so where the paragraph prints for another
    reason, the clause it prints is the true one."""
    report = _report(
        _skill_result(
            _finding("homepage-declares-no-organization-schema",
                     title="The homepage declares no Organization markup"),
            _finding("no-entity-definition", title="No page says what this is",
                     root_cause="no-entity-definition")),
        _thin_profiles())
    verdict = report["summary"]["verdict"]
    assert DENIAL not in verdict, verdict
    assert "the homepage carries no Organization markup, though other crawled pages do" \
        in verdict, verdict
    # And the three sentences around it narrow with it: a site declaring the
    # block on its deep pages has established who it is somewhere.
    assert "front page" in verdict, verdict
    assert "nothing it can safely repeat about who the brand is" not in verdict, verdict


def test_a_site_that_really_has_none_still_gets_the_sentence():
    """The other direction, and the reason this is not fixed by deleting the
    sentence. On a site where the site-wide check fired, the denial is true and
    is the most useful thing the verdict can say."""
    report = _report(
        _skill_result(_finding(
            "no-organization-schema",
            title="No Organization or LocalBusiness markup anywhere on the site",
            evidence="Checked 6 content pages. None declares an Organization-level type.")),
        _thin_profiles())
    assert DENIAL in report["summary"]["verdict"]


def test_the_fold_for_a_site_with_no_markup_at_all_counts_as_none():
    """`_fold_absent_markup` replaces six absence findings with one, and it
    fires only where no crawled page carries JSON-LD, Microdata or RDFa. It
    makes the same claim about the same pages, so it licenses the same
    sentence - and an allowlist that forgot it would have silently dropped the
    identity paragraph from every report about a site with no markup at all."""
    report = _report(
        _skill_result(_finding(
            "no-structured-data-at-all",
            title="This site publishes no structured data at all",
            evidence="Checked 6 crawled pages. Not one carries a JSON-LD block.")),
        _thin_profiles())
    assert DENIAL in report["summary"]["verdict"]


def test_a_branch_page_finding_still_denies_nothing():
    """MB's own case, kept: a site with a complete Organization block and three
    unmarked branch pages was told no page carries the markup."""
    report = _report(
        _skill_result(_finding(
            "branch-pages-carry-no-location-markup",
            title="3 pages describe a place to visit without saying so in markup")),
        _thin_profiles())
    assert DENIAL not in report["summary"]["verdict"]


# --------------------------------------------------------------------------
# The classification is closed
# --------------------------------------------------------------------------

def test_every_no_org_emitter_is_classified_one_way_or_the_other():
    """An allowlist is only safe while somebody has to answer for each check.

    A fifth emitter of `no-org-schema` added next month says nothing about the
    whole site until it is named in one of the two sets here, and this test is
    what makes somebody notice. The names are read out of the skill's own
    source, so a rename in the check breaks the build rather than the verdict.
    """
    source = os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py")
    text = io.open(source, encoding="utf-8").read()
    for hint in SAYS_THE_SITE_HAS_NONE + SAYS_THE_SITE_HAS_SOME:
        assert 'id_hint="{}"'.format(hint) in text, (
            "{} is classified here but no check raises it any more".format(hint))
    classified = (compose.IDENTITY_MARKUP_ABSENT_HINTS
                  | compose.IDENTITY_MARKUP_HOMEPAGE_ONLY_HINTS)
    assert set(SAYS_THE_SITE_HAS_NONE) == set(compose.IDENTITY_MARKUP_ABSENT_HINTS)
    assert not classified & {"branch-pages-carry-no-location-markup"}


def test_only_the_two_site_wide_checks_license_the_denial():
    """The rule as a table, over every emitter, with no report built."""
    for hint in SAYS_THE_SITE_HAS_NONE:
        nowhere, homepage_only = compose.identity_markup_gap([_finding(hint)])
        assert nowhere and not homepage_only, hint
    for hint in SAYS_THE_SITE_HAS_SOME:
        nowhere, _ = compose.identity_markup_gap([_finding(hint)])
        assert not nowhere, hint
    # A check nobody has classified licenses neither sentence, which is the
    # direction that cannot print a falsehood.
    assert compose.identity_markup_gap([_finding("a-check-added-next-month")]) \
        == (False, False)


def test_the_verdict_and_the_action_list_agree_about_which_it_is():
    """`verdict_diagnosis` decides what leads "Start here" and `_verdict_body`
    decides what the paragraph says. They read one function, so a finding the
    paragraph names is a finding the list leads with - and one it does not name
    cannot lead it for identity reasons."""
    report = _report(
        _skill_result(_finding(
            "homepage-declares-no-organization-schema",
            title="The homepage declares no Organization markup")),
        _thin_profiles())
    # No identity paragraph, so nothing leads the verdict on identity grounds.
    assert report["leads_the_verdict"] == [], report["summary"]["verdict"]


# --------------------------------------------------------------------------
# The top sheet's own sentence about the name
# --------------------------------------------------------------------------

def _sheet_says_what_this_site_is(report):
    label, sentence, _ = compose.top_sheet(report)[0]
    assert label == "What this site is"
    return sentence


def test_the_sheet_does_not_say_the_markup_names_nothing_when_it_names_something():
    """The sentence as shipped. The brand was read off a title segment; the markup
    carried an Organization name on 48 pages; the sheet said there was none."""
    report = _report(
        _skill_result(_finding("meta-hygiene", root_cause="meta-hygiene")),
        snapshot=_snapshot(brand={
            "name": "Home page", "source": "title-part-0",
            "authoritative_sources": ["jsonld:Organization-off-home"],
        }))
    sentence = _sheet_says_what_this_site_is(report)
    assert "no og:site_name, no Organization name" not in sentence, sentence
    assert "an Organization name in its JSON-LD" in sentence, sentence
    assert "guess at it" in sentence, sentence
    assert report["crawl"]["brand_name_declared_in"] == ["jsonld:Organization-off-home"]


def test_the_sheet_still_says_so_where_the_markup_really_names_nothing():
    """The sentence is right on the site it was written for - a homepage title
    used as the name because there is nothing else - and that site keeps it."""
    report = _report(
        _skill_result(_finding("meta-hygiene", root_cause="meta-hygiene")),
        snapshot=_snapshot(brand={"name": "Best jute products online",
                                  "source": "title", "authoritative_sources": []}))
    sentence = _sheet_says_what_this_site_is(report)
    assert "no og:site_name, no Organization name" in sentence, sentence


def test_a_declared_name_gets_no_warning_sentence_at_all():
    """The common case: the name came from markup, so there is nothing to warn
    about and the sheet says the name and the host."""
    sentence = _sheet_says_what_this_site_is(_report(
        _skill_result(_finding("meta-hygiene", root_cause="meta-hygiene"))))
    assert sentence.startswith("{} at ".format(SCHOOL)), sentence
    assert "guess at it" not in sentence
