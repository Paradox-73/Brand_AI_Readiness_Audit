# -*- coding: utf-8 -*-
"""Two properties a validation pass measured and found missing.

**The confident tier has to separate accuracy.** Over eight unseen sites, an
independent review checking every finding against the live page marked 83.8%
of the report correct: 84.6% in the confident tier and 82.9% in the lower one.
The split was honestly built and it separated nothing, because it was keyed on `confidence` - a value each check writes about itself, so ordering by
it orders by the boldness of fourteen authors. All three of the worst errors
sat in the confident tier and two plainly-true findings sat below them, and
what told those five apart was the kind of evidence underneath. These tests
pin the map that records it, that it is total over `ROOT_CAUSES` in both
directions, and that each of the five moves the way the diagnosis says it must.

**Advice has to fit the site.** The same review rated suggested actions 6 out
of 10 and named the reason: "Corroborate the brand" read "Claim or update:
LinkedIn company page, Google Business Profile, Crunchbase" identically for a
shop, a municipal government and a program with no company and no premises. The
findings were gated on `site_kind` and the catalogue was not. These tests pin
both halves: the wording moves where the site's own structure says it should,
and a site the classifier cannot place gets exactly what it gets today.

Every host here is invented and no real site, brand or platform is named.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    ABSENCE_CAUSES, CORROBORATED_ABSENCE_SOURCES, EVIDENCE_BASIS,
    EVIDENCE_BASIS_BY_FINDING, INFERRED, OBSERVED, ROOT_CAUSES, SUB_SKILLS,
    evidence_basis,
    LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION, PROJECT, PUBLIC_BODY,
    PERSONAL_OR_ACADEMIC, PUBLICATION,
)

SKILLS_DIR = os.path.join(ROOT, "skills")


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_tier_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose()


# --------------------------------------------------------------------------
# The map is complete, in both directions
# --------------------------------------------------------------------------

def test_every_root_cause_is_classified():
    """A new cause without a basis fails the build here.

    Without this the map rots the way every other side table in this
    marketplace has rotted: a cause is added, nothing asks about it, and it
    reaches a reader in the confident tier or the lower one for no reason
    anybody wrote down. `evidence_basis` reads an unknown cause as inferred, so
    the failure would be silent and permanent rather than loud.
    """
    missing = sorted(ROOT_CAUSES - set(EVIDENCE_BASIS))
    assert not missing, (
        "these root causes can be published and nothing says how their "
        "evidence was obtained, so the tier they land in is an accident: {}. "
        "Add each to audit_common.EVIDENCE_BASIS with a comment saying which "
        "of the two it is and why.".format(missing))


def test_the_map_names_no_cause_that_does_not_exist():
    """The other direction. A renamed cause leaves a dead entry behind, and a
    dead entry reads as a decision somebody made about a check that is gone."""
    unknown = sorted(set(EVIDENCE_BASIS) - ROOT_CAUSES)
    assert not unknown, (
        "EVIDENCE_BASIS classifies causes the vocabulary does not "
        "define: {}".format(unknown))


def test_both_kinds_are_used_and_nothing_else_is():
    values = set(EVIDENCE_BASIS.values()) | set(EVIDENCE_BASIS_BY_FINDING.values())
    assert values == {OBSERVED, INFERRED}, (
        "the axis has exactly two values; found {}".format(sorted(values)))
    # A map that answered one way everywhere would be a rule saying nothing,
    # and it would pass the two completeness tests above unchanged.
    assert sum(1 for v in EVIDENCE_BASIS.values() if v == OBSERVED) >= 5
    assert sum(1 for v in EVIDENCE_BASIS.values() if v == INFERRED) >= 5


def test_every_override_names_a_finding_some_check_can_actually_raise():
    """An override keyed on an id_hint no check emits is a decision about
    nothing, and it silently stops applying the moment an id is renamed."""
    emitted = set()
    for name in SUB_SKILLS:
        path = os.path.join(SKILLS_DIR, name, "scripts", "check.py")
        with open(path, encoding="utf-8") as handle:
            emitted.update(re.findall(r'id_hint="([a-z0-9-]+)"', handle.read()))
    assert emitted, "no id_hint literals found in the sub-skills"
    unknown = sorted(set(EVIDENCE_BASIS_BY_FINDING) - emitted)
    assert not unknown, (
        "EVIDENCE_BASIS_BY_FINDING overrides findings no check emits: "
        "{}".format(unknown))


def test_no_override_repeats_what_its_cause_already_says():
    """An override that agrees with the cause-level answer does nothing and
    reads as though it does something. The table is meant to be short enough to
    check by eye, so an entry that changes no outcome has to go."""
    by_cause = {}
    for name in SUB_SKILLS:
        path = os.path.join(SKILLS_DIR, name, "scripts", "check.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for match in re.finditer(r'id_hint="([a-z0-9-]+)"', source):
            window = source[match.start():match.start() + 4000]
            cause = re.search(r'root_cause="([a-z-]+)"', window)
            if cause:
                by_cause.setdefault(match.group(1), cause.group(1))

    redundant = [hint for hint, basis in EVIDENCE_BASIS_BY_FINDING.items()
                 if hint in by_cause
                 and EVIDENCE_BASIS.get(by_cause[hint]) == basis]
    assert not redundant, (
        "these overrides say what their root cause already says, so removing "
        "them changes nothing: {}".format(sorted(redundant)))


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------

def _finding(root_cause, confidence="high", id_hint="x", checked=None):
    finding = {"id_hint": id_hint, "root_cause": root_cause,
               "confidence": confidence}
    if checked is not None:
        finding["checked"] = list(checked)
    return finding


def test_an_inferred_reading_never_reaches_the_confident_tier():
    """However sure the check that raised it says it is.

    This is the whole point. Each of the three findings an independent review
    marked wrong was stated at high confidence by its own check, and each of
    the three rests on this audit having decided what something is: whose
    account a URL is, which string is a street address, which pages are product
    pages.
    """
    for cause in ("weak-corroboration", "no-product-schema", "no-article-schema"):
        assert compose.published_tier(_finding(cause)) == compose.WORTH_CHECKING


def test_an_observed_reading_at_high_confidence_leads_the_report():
    for cause in ("non-200", "missing-lang", "invalid-jsonld", "broken-links"):
        assert compose.published_tier(_finding(cause)) == compose.CONFIDENT


def test_a_low_reading_stays_low_however_it_was_obtained():
    """A check calling its own reading `low` knows something about that
    reading no map of causes can know, so the map may not overrule it."""
    assert compose.published_tier(
        _finding("missing-lang", confidence="low")) == compose.WORTH_CHECKING


def test_a_cause_nobody_classified_is_treated_as_inferred():
    """The safe reading of "nobody decided" is the one that asks a person to
    look, not the one that prints an unexamined claim as a certainty."""
    assert evidence_basis({"root_cause": "a-cause-that-does-not-exist"}) == INFERRED
    assert evidence_basis({}) == INFERRED
    assert compose.published_tier({"confidence": "high"}) == compose.WORTH_CHECKING


def test_a_corroborated_medium_reading_taken_off_the_document_is_promoted():
    """The other half of the measured failure.

    Findings that were plainly true sat in the lower tier because the check
    that raises them hard-codes `medium`. `meta-hygiene` is the shape: a page's
    `<title>` and `<meta name="description">` are either in the delivered head
    or they are not, the check reads both, and nothing about the reading
    depends on this audit having decided what anything is.
    """
    promoted = _finding("meta-hygiene", confidence="medium",
                        id_hint="pages-missing-a-description",
                        checked=("the head of each page", "the Open Graph tags"))
    assert compose.published_tier(promoted) == compose.CONFIDENT


def test_one_source_is_not_corroboration_even_when_read_off_the_document():
    """The floor the rest of this marketplace already applies to a claim of
    absence. One detector with a finite list of shapes is a guess with a number
    on it, whatever it read."""
    assert CORROBORATED_ABSENCE_SOURCES == 2
    thin = _finding("meta-hygiene", confidence="medium",
                    id_hint="pages-missing-a-description",
                    checked=("only one place",))
    assert "meta-hygiene" in ABSENCE_CAUSES
    assert compose.published_tier(thin) == compose.WORTH_CHECKING


def test_a_fact_the_audit_had_to_read_out_of_prose_is_never_confident():
    """The measurement that moved `missing-core-fact` down.

    A later pass ran the frozen build over a dental clinic writing in Japanese, a
    town government, a documentation tree and a one-page design studio. The
    two tiers came back 78% and 79% accurate - the split had stopped sorting
    anything - and the findings wrong in the confident tier were the ones
    decided by running patterns over sentences. "This site never states when it
    was founded" is true only if every way that sentence can be written is in
    the pattern list, which is a claim about the pattern list.

    So a fact read out of prose is `inferred` however many places were checked
    and however sure the check is, and the promotion above applies only to
    readings taken off the markup.
    """
    for cause in ("missing-core-fact", "no-entity-definition"):
        assert evidence_basis({"root_cause": cause}) == INFERRED
        for confidence in ("high", "medium"):
            stated = _finding(cause, confidence=confidence,
                              checked=("the page text", "the markup", "an about page"))
            assert compose.published_tier(stated) == compose.WORTH_CHECKING, stated


def test_the_override_moves_one_check_and_leaves_its_cause_alone():
    """The failure this table exists for.

    One cause covers both "no Organization markup on any page", which is the
    absence of a JSON-LD block and true or false in View Source, and a check
    that counts only the pages a regular expression read a street out of. The
    second one told a single-site business it was a chain with four branches.
    Classifying the cause by its weaker half would demote the first as well,
    and that finding is one of the most-often-true things this audit says.
    """
    site_wide = _finding("no-org-schema", id_hint="no-organization-schema")
    from_prose = _finding("no-org-schema",
                          id_hint="branch-pages-carry-no-location-markup")
    assert compose.published_tier(site_wide) == compose.CONFIDENT
    assert compose.published_tier(from_prose) == compose.WORTH_CHECKING


def test_the_five_findings_a_real_run_got_wrong_move_the_right_way():
    """The measurement, as a test.

    Three findings in the confident tier were wrong and two below it were
    plainly true. A mechanism that does not move these five has not worked, and
    is worse than none, because it tells a reader a finding is trustworthy.
    """
    wrong_and_confident = [
        # "Distinct off-site profiles linked or listed in sameAs: none" - the
        # site's own snapshot held five official accounts.
        _finding("weak-corroboration", id_hint="thin-off-site-profile-footprint"),
        # "4 of 4 pages that print their own street address declare no
        # LocalBusiness" - the addresses were product codes beside prices.
        _finding("no-org-schema", id_hint="branch-pages-carry-no-location-markup"),
        # "1 of 2 product pages have no Product markup" - the second page was a
        # promotional landing page.
        _finding("no-product-schema", id_hint="no-product-schema"),
    ]
    true_and_demoted = [
        # Every page's head was read and the tag was in none of them. A later
        # validation pass forced the two `missing-core-fact` entries that used
        # to stand here into the lower tier, because both are decided by
        # patterns run over sentences - see
        # `test_a_fact_the_audit_had_to_read_out_of_prose_is_never_confident`.
        _finding("meta-hygiene", confidence="medium",
                 id_hint="pages-missing-a-description",
                 checked=("the head of each page", "the Open Graph tags")),
        _finding("open-graph-incomplete", confidence="medium",
                 id_hint="open-graph-incomplete",
                 checked=("the Open Graph tags", "the Twitter card tags")),
    ]
    for finding in wrong_and_confident:
        assert compose.published_tier(finding) == compose.WORTH_CHECKING, finding
    for finding in true_and_demoted:
        assert compose.published_tier(finding) == compose.CONFIDENT, finding


# --------------------------------------------------------------------------
# Nothing is deleted and the counts still add up
# --------------------------------------------------------------------------

def _page(url):
    return {"url": url, "final_url": url, "status": 200, "title": "A page",
            "page_type": "content",
            "body_text": "A sentence long enough to be worth quoting on its own.",
            "paragraphs": ["A sentence long enough to be worth quoting on its own."]}


def _snapshot(count=4):
    pages = [_page("https://example.test/p{}".format(i)) for i in range(count)]
    return {"site": "example.test", "origin": "https://example.test",
            "brand": {"name": "Example", "host": "example.test"},
            "pages": pages, "sitemaps": [],
            "crawl": {"pages_crawled": count, "pages_ok": count,
                      "render_mode": "static", "user_agent": "test-agent",
                      "elapsed_s": 1.0, "notes": []}}


def _raw(id_hint, root_cause, confidence, checked=("one place",)):
    finding = {
        "id_hint": id_hint, "title": "Something about {}".format(id_hint),
        "severity": "medium", "confidence": confidence,
        "evidence": "Observed on 2 of the 4 pages read.",
        "mechanism": "C", "root_cause": root_cause,
        "affected_pages": [], "affected_page_count": 0,
        "suggested_action": {"summary": "Do the thing.", "effort": "low",
                             "owner": "developer", "how_to_fix": ["Step one."],
                             "rationale": "Because it works."},
    }
    if checked:
        finding["checked"] = list(checked)
    return finding


def _report(*findings):
    return compose.compose(
        _snapshot(),
        [{"skill": "structured-data-audit", "findings": list(findings),
          "signals": {}, "checks_run": [], "not_applicable": [],
          "fired_checks": [], "extra_requests_made": 0}],
        audited_at="2026-01-01T00:00:00Z")


def test_a_demoted_inferred_finding_is_still_published_in_full():
    """Demotion is not deletion. A report needs few false positives *and*
    few misses, and moving a finding down is not a miss."""
    report = _report(_raw("no-product-schema", "no-product-schema", "high"),
                     _raw("missing-html-lang", "missing-lang", "high", checked=()))
    assert report["summary"]["total_findings"] == 2
    assert len(report["findings"]) == 2
    demoted = next(f for f in report["findings"]
                   if f["tier"] == compose.WORTH_CHECKING)
    assert demoted["evidence_basis"] == INFERRED
    for key in ("id", "title", "severity", "evidence", "suggested_action"):
        assert demoted[key], "a demoted finding lost {}".format(key)
    assert demoted["suggested_action"]["how_to_fix"]


def test_the_split_still_adds_up_and_start_here_draws_only_from_the_top():
    report = _report(_raw("no-product-schema", "no-product-schema", "high"),
                     _raw("no-faq-schema", "no-faq-schema", "high"),
                     _raw("missing-html-lang", "missing-lang", "high", checked=()))
    summary = report["summary"]
    assert summary["confident"] + summary["worth_checking"] == summary["total_findings"]
    by_id = {f["id"]: f for f in report["findings"]}
    assert report["start_here"], "an observed finding was available to lead"
    for finding_id in report["start_here"]:
        assert by_id[finding_id]["tier"] == compose.CONFIDENT
        assert by_id[finding_id]["evidence_basis"] == OBSERVED
    assert not set(report["start_here"]) & set(report["worth_checking"])


def test_every_published_finding_says_how_its_evidence_was_obtained():
    report = _report(_raw("no-product-schema", "no-product-schema", "high"),
                     _raw("missing-html-lang", "missing-lang", "high", checked=()))
    for finding in report["findings"]:
        assert finding["evidence_basis"] in (OBSERVED, INFERRED)


# --------------------------------------------------------------------------
# The catalogue fits the site
# --------------------------------------------------------------------------

def _kinded_snapshot(pages, **extra):
    snapshot = _snapshot(0)
    snapshot["pages"] = list(pages)
    snapshot["crawl"]["pages_crawled"] = len(pages)
    snapshot.update(extra)
    return snapshot


def _typed(url, page_type="other", external=(), **extra):
    page = _page(url)
    page["page_type"] = page_type
    page["jsonld"] = []
    page["jsonld_types"] = []
    page["links"] = {"internal": [], "external": [{"url": u} for u in external]}
    page.update(extra)
    return page


# A program: a repository, a licence, a manual, and none of the things a
# company has. Built from the structure `site_kind` actually reads rather than
# by asserting a kind, so the gate is exercised the way a real crawl exercises
# it. The repository host is a platform this audit recognises, not an example
# site, and the path under it is invented.
def _a_program():
    return _kinded_snapshot([
        _typed("https://example.test/", "home",
               body_text="A lightweight command-line stream processor. "
                         "Licensed under the Apache License.",
               external=["https://github.com/invented-owner/invented-tool"]),
        _typed("https://example.test/manual", "documentation",
               body_text="Filters read a stream and write a stream."),
        _typed("https://example.test/tutorial", "documentation",
               body_text="Start with the identity filter."),
    ])


def _recommendations(snapshot, **signals):
    return {r["id"]: r for r in compose.build_recommendations(snapshot, signals, [])}


def _steps(snapshot, rec_id, **signals):
    entry = _recommendations(snapshot, **signals).get(rec_id)
    return " ".join(entry["how_to_do_it"]) if entry else ""


def test_the_platforms_to_claim_are_named_for_the_kind_of_site():
    """The example that prompted this. One entry, seven kinds, seven lists, and none
    of them a list of brand names that stops being true when a platform is
    renamed."""
    seen = set()
    for kind in (LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION, PROJECT,
                 PUBLIC_BODY, PERSONAL_OR_ACADEMIC, PUBLICATION):
        row = next(r for r in compose._CLAIM_PLATFORMS if r[0] == kind)
        assert row[1] not in seen, (
            "{} is given the same platform list as another kind, which is the "
            "ungated advice this exists to replace".format(kind))
        seen.add(row[1])
    assert compose._CLAIM_PLATFORMS_WHATEVER_THE_SITE_IS not in seen


def test_a_program_is_not_told_to_claim_a_business_listing():
    """`might_be`, so this goes quiet only where the structure says there is no
    company behind the site. A program has no employer profile and no funding
    round to list."""
    steps = _steps(_a_program(), "R-SAMEAS-WIKIDATA", profile_breadth=1)
    assert steps, "the entry itself must still fire"
    assert "repository host" in steps
    assert "employer profile" not in steps


def test_a_site_the_classifier_cannot_place_keeps_todays_advice():
    """A wrong guess may cost tailoring, never an entry: an unplaceable site
    still gets the recommendation and the default platform list. It does not
    get the company-record step, which assumes a company - that step told a
    central bank, a scholarship network and a one-page essay to claim "a
    business database entry and an employer profile"."""
    blank = _kinded_snapshot([_typed("https://example.test/", "home")])
    assert not compose.site_kind(blank).determined
    steps = _steps(blank, "R-SAMEAS-WIKIDATA", profile_breadth=1)
    assert compose._CLAIM_PLATFORMS_WHATEVER_THE_SITE_IS in steps
    assert "employer profile" not in steps


def test_a_program_is_not_told_when_it_was_founded_or_who_it_serves():
    """"What you are, who you serve, where you are, when you started" is a
    sentence about a business. A program has no customers and was not founded
    anywhere."""
    steps = _steps(_a_program(), "R-BOILERPLATE")
    assert steps, "the boilerplate entry is unconditional and must still fire"
    assert "who you serve" not in steps
    assert "README" in steps


def test_the_unconditional_entry_is_still_unconditional():
    """It is the one entry with no condition, and the reason is in its comment:
    identical wording across independent sources is the strongest corroboration
    signal there is, and the only measurement available to gate it reads the
    same on every real site. Wording moved; firing did not."""
    for snapshot in (_a_program(),
                     _kinded_snapshot([_typed("https://example.test/", "home")])):
        assert "R-BOILERPLATE" in _recommendations(snapshot)


def test_a_program_is_not_sent_to_write_a_press_page():
    assert "R-PRESS-PAGE" not in _recommendations(
        _a_program(), core_facts_missing=["founding facts"])


def test_a_press_page_is_not_recommended_where_the_facts_are_present_and_agree():
    """The measured half. This entry fired on 12 of 13 real sites on a
    condition that was "no page of a type almost no crawl ever assigns" - a
    presence test wearing a condition's clothes. A site stating all four core
    facts, with nothing contradicting itself, has the reusable facts already.
    """
    shop = _kinded_snapshot([
        _typed("https://example.test/", "home"),
        _typed("https://example.test/shop/a", "product"),
        _typed("https://example.test/shop/b", "product"),
    ])
    assert "R-PRESS-PAGE" not in _recommendations(shop, core_facts_missing=[])
    assert "R-PRESS-PAGE" in _recommendations(
        shop, core_facts_missing=["founding facts"])


def test_the_faq_entry_asks_the_people_who_actually_answer_the_questions():
    """There is no sales team behind a program. The entry still fires; only the
    first step moves."""
    steps = _steps(_a_program(), "R-FAQ-SCHEMA")
    assert steps
    assert "sales or support team" not in steps


def test_naming_your_components_is_gated_on_a_measurement_not_a_page_type():
    """One of the three entries found firing on six sites out of six,
    where having a product page at all was the whole condition.

    Two pages that sell, and nothing in the markup naming a property of its
    own. A site whose products already carry a named property has taken this
    advice and stops being told to take it.
    """
    def selling(jsonld):
        return _kinded_snapshot([
            _typed("https://example.test/", "home"),
            _typed("https://example.test/shop/a", "product", jsonld=jsonld,
                   jsonld_types=["product"]),
            _typed("https://example.test/shop/b", "product", jsonld=jsonld,
                   jsonld_types=["product"]),
        ])

    bare = [{"@type": "Product", "name": "A thing",
             "offers": {"@type": "Offer", "price": "10"}}]
    named = [{"@type": "Product", "name": "A thing",
              "additionalProperty": [{"@type": "PropertyValue",
                                      "name": "A coined component",
                                      "value": "described here"}]}]
    assert "R-BRAND-TERMINOLOGY" in _recommendations(selling(bare))
    assert "R-BRAND-TERMINOLOGY" not in _recommendations(selling(named))


def test_a_single_selling_page_is_not_a_template():
    """The floor the rest of this catalogue already applies. One page is a
    page; two independent pages showing the same gap is the template, and this
    entry asks for a change to a template."""
    one = _kinded_snapshot([
        _typed("https://example.test/", "home"),
        _typed("https://example.test/shop/a", "product"),
    ])
    assert "R-BRAND-TERMINOLOGY" not in _recommendations(one)


def test_prose_that_mentions_a_material_is_not_a_named_property():
    """The measurement has to read the markup rather than the words in it.
    Matching the string anywhere would make a product described as "made of
    recycled material" look like one carrying a declared property, and the
    condition would go quiet on exactly the sites that need it."""
    described = [{"@type": "Product", "name": "A thing",
                  "description": "Made of recycled material throughout."}]
    snapshot = _kinded_snapshot([
        _typed("https://example.test/", "home"),
        _typed("https://example.test/shop/a", "product", jsonld=described,
               jsonld_types=["product"]),
        _typed("https://example.test/shop/b", "product", jsonld=described,
               jsonld_types=["product"]),
    ])
    assert compose.products_name_nothing_distinctive(snapshot)


# --------------------------------------------------------------------------
# A check that never executed is not a clean check
# --------------------------------------------------------------------------

def _skill(name="crawl-access-audit", checks=(), fired=(), declined=(),
           findings=(), unresolved=None):
    declined_names = {d["check"] for d in declined}
    if unresolved is None:
        unresolved = [c for c in checks
                      if c not in fired and c not in declined_names]
    return {"skill": name, "findings": list(findings), "signals": {},
            "checks_run": list(checks), "not_applicable": list(declined),
            "fired_checks": list(fired), "unresolved_checks": list(unresolved),
            "extra_requests_made": 0}


def _dead_pages(status=402, count=3):
    """Addresses that answered, with a status that speaks for the service."""
    pages = []
    for i in range(count):
        url = "https://example.test/{}".format(i)
        pages.append({"url": url, "final_url": url, "status": status,
                      "title": "", "page_type": "home" if not i else "other",
                      "body_text": "", "paragraphs": []})
    return pages


def _not_serving_snapshot(status=402):
    reason = ("Every one of the 3 address(es) this audit reached on example.test answered "
              "HTTP {}: payment is required before the service will answer. That is the site "
              "declining to serve, not a page that is missing, so nothing below is a "
              "measurement of what example.test publishes.".format(status))
    return {"site": "example.test", "origin": "https://example.test",
            "brand": {"name": "Example", "host": "example.test"},
            "pages": _dead_pages(status), "sitemaps": [],
            "crawl": {"pages_crawled": 3, "pages_ok": 0, "render_mode": "static",
                      "user_agent": "test-agent", "elapsed_s": 1.0,
                      "notes": [reason]},
            "site_not_serving": {
                "status": status,
                "meaning": "payment is required before the service will answer",
                "addresses": ["https://example.test/",
                              "https://example.test/robots.txt",
                              "https://example.test/sitemap.xml"],
                "addresses_answered": 3, "reason": reason}}


def test_a_skill_reports_the_checks_it_registered_and_never_answered():
    from audit_common import SkillResult

    result = SkillResult("crawl-access-audit")
    result.check("homepage-reachable")
    result.skip("sitemap-present", "no sitemap was declared")
    assert result.to_dict()["unresolved_checks"] == ["homepage-reachable"]
    assert "sitemap-present" not in result.to_dict()["unresolved_checks"]


def test_a_check_that_never_reached_the_site_is_not_printed_as_clean():
    """The failure, in the shape it happened.

    A store answering HTTP 402 on every URL, zero pages fetched, and its report
    listed `homepage-reachable` and `non-200-rate` under "These ran against
    this site and were clean - they are not omissions". Neither check reached
    anything. Registered and executed are different facts.
    """
    report = compose.compose(
        _not_serving_snapshot(),
        [_skill(checks=["homepage-reachable", "non-200-rate", "robots-reachable"])],
        audited_at="2026-01-01T00:00:00Z")

    assert report["checks_passed"] == []
    unexecuted = [c["check"] for c in report["checks_with_nothing_to_run_against"]]
    assert unexecuted == ["homepage-reachable", "non-200-rate", "robots-reachable"]

    markdown = compose.render_markdown(report)
    assert compose.NOTHING_TO_RUN_AGAINST_HEADING in markdown
    assert "ran against this site and were clean" not in markdown
    # Both documents say the same thing about the same run. The HTML appendix
    # prints only the paragraph, so the count has to be in the sentence.
    for rendered in (markdown, compose.render_html(report)):
        assert "never reached anything to judge" in rendered
        assert "not counted as clean" in rendered


def test_a_crawl_that_read_pages_still_reports_its_clean_checks():
    """The safety half. This guard may not empty the bucket an independent
    review singled out as better than expected; it fires only where nothing
    was read."""
    report = compose.compose(
        _snapshot(),
        [_skill(checks=["homepage-reachable", "non-200-rate"])],
        audited_at="2026-01-01T00:00:00Z")
    assert [c["check"] for c in report["checks_passed"]] == [
        "homepage-reachable", "non-200-rate"]
    assert report["checks_with_nothing_to_run_against"] == []


def test_a_result_written_before_the_key_existed_still_composes():
    """An older findings file carries no `unresolved_checks`, and an absent key
    claims nothing either way rather than crashing or emptying the bucket."""
    stale = _skill(checks=["homepage-reachable"])
    del stale["unresolved_checks"]
    report = compose.compose(_snapshot(), [stale],
                             audited_at="2026-01-01T00:00:00Z")
    assert [c["check"] for c in report["checks_passed"]] == ["homepage-reachable"]


# --------------------------------------------------------------------------
# A skill that did not run is a gap, not a decline
# --------------------------------------------------------------------------

# The stand-in `run_audit.py` writes when a sub-skill overruns its share of the
# wall-clock ceiling. Its own sentence, quoted rather than paraphrased, because
# what this routing has to preserve is the sentence.
_STOPPED_REASON = (
    "this skill was stopped after 30s, the share of this audit's wall-clock ceiling that was "
    "left for it, so none of its checks ran against this site. What it examines is therefore "
    "unreported rather than clean - treat it as a gap in this audit, not as a pass. Re-run "
    "with a larger --budget, with --no-network, or against a faster host to fill it in")


def _stand_in(skill="engagement-audit", check=None, flag=None):
    """A skill that did not run, identified whichever of the three ways."""
    check = check or compose.SKILL_DID_NOT_RUN_CHECK
    decline = {"check": check, "skill": skill, "reason": _STOPPED_REASON}
    result = _skill(skill, checks=[check], declined=[decline], unresolved=[])
    if flag == "on the result":
        result["skill_did_not_run"] = True
    elif flag == "on the decline":
        decline["did_not_run"] = True
    return result


def test_a_skill_that_did_not_run_is_not_printed_as_a_check_that_did_not_apply():
    """The failure: the stand-in lands in `not_applicable`, which prints under
    "Checks that did not apply, and why - these were run and found nothing to
    report". Its own reason says the opposite, in the same paragraph.

    Echoed rather than moved, exactly as `checks_left_unanswered` echoes a
    decline the crawl gave nothing to decline on: the entry stays in the JSON
    key so the appendix's buckets still add up to the checks that ran, and the
    renderers print it elsewhere. A reader adding the bullets up is told where
    the difference went, so a bucket of 2 showing 1 bullet is not a check that
    vanished.
    """
    report = compose.compose(
        _snapshot(),
        [_stand_in(),
         _skill(checks=["homepage-reachable", "non-200-rate"],
                declined=[{"check": "non-200-rate", "skill": "crawl-access-audit",
                           "reason": "nothing to measure"}])],
        audited_at="2026-01-01T00:00:00Z")

    assert len(report["not_applicable"]) == 2
    assert [c["skill"] for c in report["skills_that_did_not_run"]] == ["engagement-audit"]

    markdown = compose.render_markdown(report)
    declines = markdown.split("### Checks that did not apply, and why")[1].split("###")[0]
    assert "nothing to measure" in declines
    assert "stopped after 30s" not in declines, \
        "the gap is printed under a heading saying the checks below were run"
    assert compose.UNANSWERED_HEADING in declines, \
        "a reader adding the bullets up is not told where the difference went"


def test_the_stand_in_reason_reaches_the_unanswered_questions_intact():
    """A skill that did not run is the largest gap an audit can have, so it
    gets the most visible channel and its own sentence unedited - it names how
    long the skill had and what to change to give it more."""
    report = compose.compose(_snapshot(), [_stand_in()],
                             audited_at="2026-01-01T00:00:00Z")
    entry = report["unverifiable"][0]
    assert entry["reason"] == _STOPPED_REASON
    assert "engagement-audit" in entry["title"]
    for rendered in (compose.render_markdown(report), compose.render_html(report)):
        assert compose.UNANSWERED_HEADING in rendered
        assert "stopped after 30s" in rendered


def test_a_dropped_skill_leads_the_unanswered_questions():
    """An absence claim held back names one thing the audit could not settle.
    A skill that did not run names everything that skill would have looked at,
    so it goes first."""
    snapshot = _snapshot()
    held = {"title": "Something absent", "reason": "the crawl read too little"}
    report = compose.compose(snapshot, [_stand_in()],
                             audited_at="2026-01-01T00:00:00Z")
    report["unverifiable"].append(held)
    assert "did not run" in report["unverifiable"][0]["title"]


def test_a_dropped_skill_is_recognised_by_a_flag_as_well_as_by_the_name():
    """Three ways in, in order of how much they should exist. The check name is
    a string agreed between two files; a flag is the same fact said once. Both
    work, so `run_audit.py` can move to the flag without a flag day."""
    for flag in (None, "on the result", "on the decline"):
        report = compose.compose(
            _snapshot(), [_stand_in(check="stopped-early", flag=flag)],
            audited_at="2026-01-01T00:00:00Z")
        recognised = bool(report["skills_that_did_not_run"])
        # The name alone is the fallback and it is the check `run_audit.py`
        # writes today; a differently-named stand-in is recognised only by a
        # flag, which is the argument for the flag.
        assert recognised == (flag is not None), flag
        if recognised:
            assert report["unverifiable"], flag


def test_a_dropped_skills_check_is_never_counted_as_clean():
    """"None of its checks ran against this site" and "ran and found nothing
    wrong" are the two sentences this whole routing exists to keep apart."""
    report = compose.compose(
        _snapshot(), [_stand_in(), _skill(checks=["homepage-reachable"])],
        audited_at="2026-01-01T00:00:00Z")
    passed = [c["check"] for c in report["checks_passed"]]
    assert compose.SKILL_DID_NOT_RUN_CHECK not in passed
    assert passed == ["homepage-reachable"]


def test_the_appendix_arithmetic_still_adds_up_with_a_skill_missing():
    """Every check that ran is in exactly one bucket, and the paragraph a
    reader can add up has to keep saying so."""
    report = compose.compose(
        _snapshot(),
        [_stand_in(),
         _skill(checks=["homepage-reachable", "non-200-rate"],
                declined=[{"check": "non-200-rate", "skill": "crawl-access-audit",
                           "reason": "nothing to measure"}])],
        audited_at="2026-01-01T00:00:00Z")
    buckets = (len(report["not_applicable"])
               + len(report["checks_passed"])
               + len(report["checks_that_found_something"])
               # The half of that bucket with no finding above it. Split out
               # after three checks were found filed under "contributed to a
               # finding above" in a report holding no such finding; both
               # halves still count towards the total the paragraph prints.
               + len(report["checks_that_found_nothing_published"])
               + len(report["checks_with_nothing_to_run_against"])
               + len({(c["skill"], c["check"]) for c in report["checks_run"]}
                     & compose.named_check_pairs(report["findings"])))
    assert buckets == len(report["checks_run"])


def test_one_wording_for_the_unanswered_questions_in_both_documents():
    """Three different things arrive in this channel now - an absence claim
    held back, a site refusing to serve, and a skill that never ran - and the
    section used to open by asserting the first as the cause of all three."""
    report = compose.compose(_snapshot(), [_stand_in()],
                             audited_at="2026-01-01T00:00:00Z")
    for rendered in (compose.render_markdown(report), compose.render_html(report)):
        assert compose.UNANSWERED_BLURB.split(".")[0] in rendered
        assert "shown very little of the site" not in rendered


# --------------------------------------------------------------------------
# A site that is not serving cannot be graded
# --------------------------------------------------------------------------

def test_the_not_serving_sentence_leads_the_report():
    """The case: a report whose only finding was "remove `Disallow: /` from
    robots.txt", on a site with no shop behind it. That is the whole story of
    the site, delivered as a chore."""
    snapshot = _not_serving_snapshot()
    report = compose.compose(snapshot, [_skill()],
                             audited_at="2026-01-01T00:00:00Z")
    verdict = report["summary"]["verdict"]
    assert verdict == snapshot["site_not_serving"]["reason"], (
        "the crawler's own sentence names the status, its meaning and how many "
        "addresses it rests on; composing a second one here is a second place "
        "for the two to disagree")
    assert report["crawl"]["site_not_serving"]["status"] == 402


def test_a_site_that_is_not_serving_cannot_be_graded():
    """The same standing `origin_redirect` with `followed: false` already has:
    a snapshot in this state carries no evidence about content, so it cannot
    support a verdict of any kind, a clean one included."""
    report = compose.compose(_not_serving_snapshot(), [_skill()],
                             audited_at="2026-01-01T00:00:00Z")
    assert report["crawl"]["enough_to_judge"] is False
    verdict = report["summary"]["verdict"]
    for reassurance in ("No blocking", "foundations are sound",
                        "nothing significant"):
        assert reassurance.lower() not in verdict.lower()


def test_both_documents_lead_with_it():
    report = compose.compose(_not_serving_snapshot(), [_skill()],
                             audited_at="2026-01-01T00:00:00Z")
    for rendered in (compose.render_markdown(report), compose.render_html(report)):
        assert compose.SITE_NOT_SERVING_HEADING in rendered
        # Above the severity table, not in a note at the foot of the appendix.
        assert rendered.index(compose.SITE_NOT_SERVING_HEADING) < \
            rendered.index("Appendix")


def test_an_ordinary_site_says_nothing_about_serving():
    report = compose.compose(_snapshot(), [_skill()],
                             audited_at="2026-01-01T00:00:00Z")
    assert report["crawl"]["site_not_serving"] is None
    assert compose._not_serving_caveat(report) is None
    assert compose.SITE_NOT_SERVING_HEADING not in compose.render_markdown(report)


def test_no_recommendation_names_a_real_platform_where_a_role_would_do():
    """Written as things to go and claim, so a step stays followable by
    somebody who knows their own country's equivalent and stays true when a
    platform is renamed. The two exceptions are the ones with no generic name:
    an open data vocabulary and an encyclopedia this audit queries by API."""
    allowed = {"wikidata", "wikipedia"}
    catalogue = " ".join(row[1] for row in compose._CLAIM_PLATFORMS)
    catalogue += " " + compose._CLAIM_PLATFORMS_WHATEVER_THE_SITE_IS
    words = set(re.findall(r"\b[A-Z][A-Za-z]+\b", catalogue))
    stray = sorted(w for w in words if w.lower() not in allowed)
    assert not stray, (
        "these read as brand names rather than as roles: {}".format(stray))
