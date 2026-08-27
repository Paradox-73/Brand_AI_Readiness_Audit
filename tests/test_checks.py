"""Behavioural tests: does each check fire on the right site and stay quiet elsewhere?

Golden files record expected *root causes* rather than byte-exact findings.
Findings contain URLs, and the fixture server binds an ephemeral port, so a
byte-exact golden would encode the port number and fail on every run. Root
causes are the stable, meaningful thing: they say which problem was found, not
how the evidence sentence happened to be worded.

The `must_not_find` lists carry as much weight as `must_find`. A check that
fires everywhere detects nothing.
"""

from __future__ import annotations

import pytest

from conftest import SUB_SKILLS, all_fixture_names

FIXTURE_NAMES = all_fixture_names()


# --------------------------------------------------------------------------
# The false-positive guard
# --------------------------------------------------------------------------

def test_good_site_produces_no_findings(audit, golden):
    """A site that does everything right must produce nothing at all.

    This is the most important test in the suite. Reaching zero here is what
    surfaced seven real bugs, including a price comparison that read "480.00"
    as different from "$480", and "founded in 2019" scored as a staleness claim.
    """
    result = audit("good-site")
    assert golden("good-site")["expect_zero_findings"] is True

    if result.report["findings"]:
        detail = "\n".join(
            "  [{}] {:<8} {:<22} {}\n      {}".format(
                f["id"], f["severity"], f["root_cause"], f["title"], f["evidence"])
            for f in result.report["findings"])
        pytest.fail(
            "good-site is the false-positive guard and must produce zero findings, "
            "but produced {}:\n{}".format(len(result.report["findings"]), detail))


def test_good_site_still_gets_the_unconditional_recommendation(audit):
    """Zero findings is not zero advice: a clean site still has upside."""
    result = audit("good-site")
    assert "R-BOILERPLATE" in result.recommendation_ids
    assert result.report["summary"]["verdict"]


def test_good_site_every_key_page_has_a_quotable_sentence(audit):
    result = audit("good-site")
    unquotable = [c for c in result.report["citation_simulation"]
                  if c["likely_citation"] is None]
    assert not unquotable, "pages with nothing quotable: {}".format(
        [c["url"] for c in unquotable])


# --------------------------------------------------------------------------
# Per-fixture expectations
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_finds_what_it_should(audit, golden, name):
    expected = golden(name)
    found = audit(name).root_causes
    missing = [rc for rc in expected["must_find"] if rc not in found]
    assert not missing, "{}: expected root causes not detected: {}\nfound: {}".format(
        name, missing, sorted(found))


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_stays_quiet_where_it_should(audit, golden, name):
    """The false-positive half. A check firing here is a bug in the check."""
    expected = golden(name)
    result = audit(name)
    found = result.root_causes
    spurious = [rc for rc in expected["must_not_find"] if rc in found]
    if spurious:
        detail = "\n".join(
            "  {:<22} {} :: {}".format(f["root_cause"], f["title"], f["evidence"])
            for f in result.report["findings"] if f["root_cause"] in spurious)
        pytest.fail("{}: these root causes fired but should not have:\n{}".format(name, detail))


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_recommendations(audit, golden, name):
    expected = golden(name)
    recommended = audit(name).recommendation_ids
    missing = [r for r in expected.get("must_recommend", []) if r not in recommended]
    spurious = [r for r in expected.get("must_not_recommend", []) if r in recommended]
    assert not missing, "{}: missing recommendations {}".format(name, missing)
    assert not spurious, "{}: recommendations offered without their condition: {}".format(
        name, spurious)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_severity_expectations(audit, golden, name):
    expected = golden(name)
    result = audit(name)

    minimum_critical = expected.get("expect_critical_at_least")
    if minimum_critical:
        criticals = result.severities("critical")
        assert len(criticals) >= minimum_critical, \
            "{}: expected at least {} critical finding(s), got {}".format(
                name, minimum_critical, [f["title"] for f in criticals])

    for root_cause in expected.get("critical_root_causes", []):
        matching = [f for f in result.findings_with(root_cause) if f["severity"] == "critical"]
        assert matching, "{}: `{}` should be critical, got {}".format(
            name, root_cause,
            [(f["root_cause"], f["severity"]) for f in result.report["findings"]])

    minimum_info = expected.get("expect_info_at_least")
    if minimum_info:
        assert len(result.severities("info")) >= minimum_info


# --------------------------------------------------------------------------
# Specific behaviours worth pinning down
# --------------------------------------------------------------------------

def test_training_crawler_block_is_info_not_a_defect(audit, golden):
    """Opting out of training corpora is a rights decision, not a discoverability bug."""
    result = audit("blocked-site")
    training = [f for f in result.report["findings"]
                if "training" in f["title"].lower()]
    assert training, "the training-crawler block was not reported at all"
    assert all(f["severity"] == "info" for f in training), \
        "training-crawler block must be info, got {}".format(
            [(f["title"], f["severity"]) for f in training])

    answer = [f for f in result.report["findings"]
              if "answer crawler" in f["title"].lower()]
    assert answer and answer[0]["severity"] == "high", \
        "an answer-crawler block must be high severity"


def test_answer_and_training_crawlers_are_separated(audit, golden):
    result = audit("blocked-site")
    signals = result.findings["crawl-access-audit"]["signals"]
    expected = golden("blocked-site")
    assert set(expected["blocked_answer_crawlers"]) <= set(signals["blocked_answer_crawlers"])
    assert set(expected["blocked_training_crawlers"]) <= set(signals["blocked_training_crawlers"])
    # No crawler may appear in both groups.
    assert not set(signals["blocked_answer_crawlers"]) & set(signals["blocked_training_crawlers"])


def test_prose_only_site_is_not_told_its_facts_are_missing(audit):
    """no-schema-site has no markup but excellent prose.

    The extractability skill must not inherit the structured-data skill's
    complaint. Conflating "not marked up" with "not stated" is the single
    easiest way for this marketplace to produce nonsense.
    """
    result = audit("no-schema-site")
    assert "no-org-schema" in result.root_causes
    assert "missing-core-fact" not in result.root_causes


def test_shell_page_is_not_double_reported(audit):
    """A JS shell is reported once, not also as image-locked or iframed."""
    result = audit("js-shell-site")
    shells = result.findings_with("js-shell")
    assert shells
    shell_pages = {url for f in shells for url in f["affected_pages"]}
    for root_cause in ("image-locked-facts", "iframe-content"):
        for finding in result.findings_with(root_cause):
            overlap = shell_pages & set(finding["affected_pages"])
            assert not overlap, "{} re-reports shell page(s) {}".format(root_cause, overlap)


def test_newsletter_is_a_recommendation_never_a_finding(audit):
    result = audit("dead-end-site")
    assert "R-EMAIL-TEXT-FIRST" in result.recommendation_ids
    assert not [f for f in result.report["findings"]
                if "newsletter" in f["title"].lower()]


def test_citation_simulation_reports_nothing_quotable(audit, golden):
    """The shell site's key pages must have nothing an assistant could quote."""
    expected = golden("js-shell-site")["citation_simulation_none_for_types"]
    result = audit("js-shell-site")
    by_type = {c["page_type"]: c for c in result.report["citation_simulation"]}
    for page_type in expected:
        assert page_type in by_type, "no citation row for page type {}".format(page_type)
        assert by_type[page_type]["likely_citation"] is None, \
            "{} unexpectedly had a quotable sentence: {}".format(
                page_type, by_type[page_type]["likely_citation"])


def test_legal_pages_are_never_assessed(audit):
    """A privacy policy with no CTA and no schema is a normal privacy policy."""
    result = audit("good-site")
    legal_urls = {p["url"] for p in result.snapshot["pages"]
                  if p.get("page_type") == "legal"}
    assert legal_urls, "the good-site fixture should contain a legal page"
    for finding in result.report["findings"]:
        assert not (set(finding["affected_pages"]) & legal_urls)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_page_type_detection_is_sane(audit, golden, name):
    """Every crawled page gets a type, and product type is never guessed loosely."""
    result = audit(name)
    for page in result.snapshot["pages"]:
        assert page.get("page_type"), "page {} has no detected type".format(page["url"])

    expected_types = golden(name).get("expect_page_types")
    if expected_types:
        detected = result.page_types()
        missing = [t for t in expected_types if t not in detected]
        assert not missing, "{}: page types not detected: {} (got {})".format(
            name, missing, sorted(detected))


# --------------------------------------------------------------------------
# The sub-skill contract
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_skill_reports_what_it_skipped(audit, name):
    """A check that stays quiet must say why. Silence is not an output."""
    result = audit(name)
    for skill in SUB_SKILLS:
        payload = result.findings[skill]
        assert payload["skill"] == skill
        assert payload["checks_run"], "{} ran no checks on {}".format(skill, name)
        for entry in payload["not_applicable"]:
            assert entry["reason"].strip(), \
                "{} skipped `{}` on {} without a reason".format(skill, entry["check"], name)
            assert entry["check"] in payload["checks_run"], \
                "{} recorded `{}` as not applicable but not as run".format(skill, entry["check"])


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_extra_requests_stay_within_declared_budgets(audit, name):
    """Each skill's SKILL.md declares a request ceiling. Hold it to that."""
    budgets = {
        "crawl-access-audit": 10,
        "render-readability-audit": 0,
        "structured-data-audit": 0,
        "fact-extractability-audit": 0,
        "freshness-corroboration-audit": 4,
        "engagement-audit": 20,
    }
    result = audit(name)
    for skill, ceiling in budgets.items():
        made = result.findings[skill]["extra_requests_made"]
        assert made <= ceiling, "{} made {} extra requests on {}, ceiling is {}".format(
            skill, made, name, ceiling)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_findings_are_well_formed(audit, name):
    """Every finding carries the evidence and the fix that make it actionable."""
    for finding in audit(name).report["findings"]:
        action = finding["suggested_action"]
        assert finding["evidence"].strip()
        assert finding["evidence"] != finding["title"], \
            "{}: evidence merely restates the title".format(finding["id"])
        assert action["how_to_fix"], "{}: no fix steps".format(finding["id"])
        assert action["rationale"].strip()
        assert finding["mechanism"] in "ABCDEFG"
        assert finding["confidence"] in ("high", "medium", "low")
        assert action["owner"] in ("developer", "content owner", "marketing")
        assert len(finding["affected_pages"]) <= 5, \
            "{}: affected_pages must be a sample of at most 5".format(finding["id"])


def test_offline_mode_makes_no_extra_requests(audit):
    """--no-network must be honoured by every skill that can make requests."""
    result = audit("blocked-site", no_network=True)
    for skill in SUB_SKILLS:
        assert result.findings[skill]["extra_requests_made"] == 0, \
            "{} made requests despite --no-network".format(skill)
    # And the checks that needed the network must say they were skipped.
    access = result.findings["crawl-access-audit"]
    skipped = {n["check"] for n in access["not_applicable"]}
    assert "bot-manager-user-agent-comparison" in skipped
