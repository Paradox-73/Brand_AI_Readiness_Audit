"""The claims `evals/README.md` makes about the fixtures have to stay true.

That document tells a reader three prompts to type and exactly what should come
back — "the answer must lead with the WAF block and the four blocked answer
crawlers", "should name the three orphan pages". Those are specific, checkable
statements about specific fixtures, and nothing was checking them.

Prose drifts. A threshold moves, a check is renamed, and the document quietly
starts describing a version of the marketplace that no longer exists. A reader
following it would find the discrepancy before we did, and would be right to
read that as the docs being decoration.

So the claims are asserted here. If one becomes false, this fails and either the
behaviour or the document gets corrected — deliberately, rather than by
discovery.
"""

from __future__ import annotations

import os


from conftest import SCRIPTS


# --------------------------------------------------------------------------
# Eval 2 — "Why is my brand not showing up in ChatGPT?"
#
#   "On tests/fixtures/blocked-site the answer must lead with the WAF block and
#    the two blocked answer crawlers - and must not present the four blocked
#    training crawlers as a problem, since that is info severity and a rights
#    decision."
#
# The fixture's robots.txt names four crawlers. It used to produce "4 AI answer
# crawlers", because GPTBot and ClaudeBot were filed as answer crawlers. They
# are training crawlers by their operators' own documentation, so the split is
# now two and four, and the report tells the owner that allowing GPTBot back in
# would not buy a citation.
# --------------------------------------------------------------------------

def test_blocked_site_leads_with_the_bot_manager(audit):
    result = audit("blocked-site")
    findings = result.report["findings"]
    assert findings, "blocked-site should report something"
    assert findings[0]["root_cause"] == "bot-manager-block", (
        "the WAF block must come first; got {}".format(findings[0]["root_cause"]))
    # High, not critical: the probe sends a crawler's name from the auditor's
    # own address, and a CDN that verifies crawlers by IP refuses that request
    # while serving the real crawler, so a name probe cannot prove the rule is
    # on the name. It still leads the report.
    assert findings[0]["severity"] == "high"


def test_blocked_site_names_the_two_blocked_answer_crawlers(audit):
    result = audit("blocked-site")
    blocks = [f for f in result.report["findings"]
              if f["root_cause"] == "robots-block" and f["severity"] != "info"]
    assert blocks, "the answer-crawler block should be reported"
    assert "2 AI answer crawlers" in blocks[0]["title"], (
        "the eval document promises two; the report says {!r}".format(blocks[0]["title"]))
    assert "OAI-SearchBot" in blocks[0]["evidence"]
    assert "PerplexityBot" in blocks[0]["evidence"]


def test_the_two_training_crawlers_in_that_robots_txt_are_not_in_the_defect(audit):
    """The fixture disallows GPTBot and ClaudeBot alongside OAI-SearchBot and
    PerplexityBot. An independent review found the report telling owners to
    allow-list the training pair so their pages could be cited, at rank two of
    "Start here". Following it would have reopened the site to training
    collection it had deliberately opted out of, for a citation gain of zero.
    """
    result = audit("blocked-site")
    defect = [f for f in result.report["findings"]
              if f["root_cause"] == "robots-block" and f["severity"] != "info"]
    body = defect[0]["evidence"] + " " + " ".join(
        defect[0]["suggested_action"]["how_to_fix"])
    assert "GPTBot" not in body and "ClaudeBot" not in body, (
        "the citation defect must not ask the owner to allow a training crawler")


def test_the_report_names_the_agents_that_would_actually_buy_a_citation(audit):
    """Reporting the training block is not enough on its own. The owner needs
    told which agents from the same companies decide citation, so they do not
    undo the opt-out looking for one."""
    result = audit("blocked-site")
    training = [f for f in result.report["findings"]
                if f["root_cause"] == "robots-block" and f["severity"] == "info"]
    assert training, "the training-crawler block should still be surfaced"
    steps = " ".join(training[0]["suggested_action"]["how_to_fix"])
    assert "ChatGPT-User" in steps or "Claude-User" in steps, (
        "the info finding should name the answer-side agents of the same operators")


def test_every_agent_the_report_names_is_described_with_its_role(audit):
    """The old evidence line was a bare list of tokens under a sentence
    claiming all of them fetched pages to build cited answers."""
    result = audit("blocked-site")
    for finding in result.report["findings"]:
        if finding["root_cause"] != "robots-block":
            continue
        if "crawler" not in finding["title"]:
            continue
        evidence = finding["evidence"]
        assert ("search index" in evidence or "live fetch" in evidence
                or "model training" in evidence or "fetches nothing" in evidence), (
            "{!r} names agents without saying what any of them does".format(evidence[:120]))


# --------------------------------------------------------------------------
# Eval 3 — "Check if my site keeps visitors"
#
#   "On tests/fixtures/dead-end-site the answer should name the "Welcome" H1, the
#    single-item navigation, the 404 on the only onward link, and the three orphan
#    pages - and should recommend R-EMAIL-TEXT-FIRST, which fires on this fixture
#    alone because it is the only one with an email capture."
# --------------------------------------------------------------------------

PROMISED_ON_DEAD_END_SITE = [
    ("no-orientation", "the generic homepage heading and the thin navigation"),
    ("broken-links", "the 404 on the only onward link"),
    ("orphan-pages", "pages in the sitemap that nothing links to"),
    ("dead-end", "pages with no next step"),
]


def test_dead_end_site_reports_what_the_evals_promise(audit):
    found = audit("dead-end-site").root_causes
    for root_cause, promise in PROMISED_ON_DEAD_END_SITE:
        assert root_cause in found, (
            "evals/README.md promises {}, and {} did not fire".format(promise, root_cause))


def test_dead_end_site_quotes_the_things_the_evals_name(audit):
    """The document promises the "Welcome" H1 and three orphan pages by name,
    so the report has to quote them back rather than merely fire the check."""
    result = audit("dead-end-site")
    evidence = " ".join(f["evidence"] for f in result.findings_with("no-orientation"))
    assert "welcome" in evidence.lower(), evidence

    orphans = result.findings_with("orphan-pages")
    assert orphans, "orphan pages should be reported"
    assert "3 pages" in orphans[0]["title"], orphans[0]["title"]


def test_the_email_recommendation_fires_only_where_there_is_an_email_capture(audit):
    """`R-EMAIL-TEXT-FIRST` is claimed to fire on this fixture alone."""
    assert "R-EMAIL-TEXT-FIRST" in audit("dead-end-site").recommendation_ids
    for other in ("good-site", "blocked-site", "no-schema-site"):
        assert "R-EMAIL-TEXT-FIRST" not in audit(other).recommendation_ids, (
            "{} has no email capture, so the recommendation should not appear".format(other))


# --------------------------------------------------------------------------
# "What to look for across all three"
# --------------------------------------------------------------------------

def test_the_clean_fixture_reports_nothing_but_still_recommends(audit):
    """The table promises zero findings on `good-site` and `R-BOILERPLATE` anyway.

    Proactive advice where nothing is broken is part of the brief, so the
    combination matters: silence about defects, not silence altogether.
    """
    result = audit("good-site")
    assert result.report["findings"] == [], sorted(result.root_causes)
    assert "R-BOILERPLATE" in result.recommendation_ids


def test_every_finding_carries_evidence_rather_than_an_adjective(audit):
    """"0/12 product pages contain schema.org markup", never "could be improved"."""
    vague = ("could be improved", "should be better", "is suboptimal", "needs work",
             "is not ideal", "consider improving")
    for name in ("dead-end-site", "blocked-site", "no-schema-site", "stale-site"):
        for finding in audit(name).report["findings"]:
            evidence = finding["evidence"]
            lowered = evidence.lower()
            assert not any(phrase in lowered for phrase in vague), (
                "{}: {!r} reads as an adjective, not evidence".format(name, evidence[:90]))
            # A count, a URL, or a named specific thing. "Disallowed at `/`:
            # GPTBot, OAI-SearchBot, ClaudeBot, PerplexityBot" carries no digit
            # and is exactly the kind of evidence this is meant to require.
            concrete = (any(char.isdigit() for char in evidence)
                        or "://" in evidence
                        or "`" in evidence)
            assert concrete, (
                "{}: evidence names nothing specific: {!r}".format(name, evidence[:90]))


def test_every_finding_names_an_owner_and_a_duration(audit):
    for name in ("dead-end-site", "blocked-site"):
        for finding in audit(name).report["findings"]:
            action = finding["suggested_action"]
            assert action.get("owner"), finding["id"]
            assert action.get("effort") in ("low", "medium", "high"), finding["id"]
            assert action.get("how_to_fix"), finding["id"]


# --------------------------------------------------------------------------
# A site we could not read gets no advice about its content
#
# Every proactive recommendation's condition is the absence of a signal. On a
# site the crawl never reached, every signal is absent, so almost all of them
# fired: auditing a domain that does not resolve produced six improvements for
# it, including publishing a file at its root. The entrypoint's own procedure
# already says an unreachable homepage makes every other check meaningless -
# the findings honoured that and the recommendations did not.
# --------------------------------------------------------------------------

def _compose_for(snapshot):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "compose_for_test", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_site_that_was_never_read_gets_no_proactive_recommendations():
    """The guard must not silence advice on a site that simply has few
    signals, so the read and unread cases are asserted together."""
    compose = _compose_for(None)
    unread = {"origin": "https://an-invented-host.test", "site": "audited site",
              "pages": [{"url": "https://an-invented-host.test/", "status": None,
                         "page_type": "home", "error": "no response"}],
              "crawl": {"pages_crawled": 1}}
    assert compose.build_recommendations(unread, {}, []) == []

    read = {"origin": "https://an-invented-host.test", "site": "audited site",
            "pages": [{"url": "https://an-invented-host.test/", "status": 200,
                       "page_type": "home", "text": "hello", "text_len": 5,
                       "links": {"internal": [], "external": []}, "jsonld": []}],
            "crawl": {"pages_crawled": 1}}
    assert compose.build_recommendations(read, {}, []) != []


