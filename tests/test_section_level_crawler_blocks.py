# -*- coding: utf-8 -*-
"""A robots.txt group that names AI crawlers and closes one section of a site.

Two checks read this file and neither could see that shape. One asks whether an
agent is disallowed from `/`; the other reads only the `User-agent: *` group. A
robots.txt whose single group named eleven answer, search and training crawlers
and carried `Disallow: /store/` fell between them, and on a shop that path is
every product, every price and every stock state the site publishes:

  the answer-crawler check   reported the site clean, and the check appeared in
                             the report under "checks that ran and found
                             nothing wrong"
  the content-path check     declined - "robots.txt names specific crawlers and
                             has no `User-agent: *` group"

So the seam printed the opposite of the truth twice, in the one audit whose
whole purpose is to say whether AI answer engines can read a brand at all.

What this file pins:

  the finding        a section closed by a group naming an answer or search
                     crawler, priced by how much of the crawl sits behind it
  the distinction    training crawlers and opt-out tokens stay at `info` with
                     "this is often deliberate", in a finding of their own
  the boundary       a whole-site block reports once, and a path the `*` group
                     closes too belongs to the check that reads that group
  the silence        a site that blocks nothing says what was tested, in
                     words, rather than passing without any

Every host here is invented and no rule is keyed to a real site.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from robots_parser import (  # noqa: E402
    names_agent, parse_robots, rule_matches_path, section_disallows,
)


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
             "ca_for_section_blocks")

SITE = "https://an-invented-shop.test"

# The shape the audit missed: one group, eleven named crawlers, one section.
# Three of the eleven fetch pages to build answers, six collect training data or
# carry a training opt-out, and two are neither and are not in the table at all.
MIXED_GROUP = (
    "User-agent: GPTBot\n"
    "User-agent: ChatGPT-User\n"
    "User-agent: ClaudeBot\n"
    "User-agent: OAI-SearchBot\n"
    "User-agent: PerplexityBot\n"
    "User-agent: Meta-ExternalAgent\n"
    "User-agent: CCBot\n"
    "User-agent: Google-Extended\n"
    "User-agent: Applebot-Extended\n"
    "User-agent: PetalBot\n"
    "User-agent: baiduspider\n"
    "Disallow: /store/\n"
)


def _robots(text):
    return dict(parse_robots(text), status=200, url=SITE + "/robots.txt")


def _pages(inside=12, outside=2, prefix="/store/"):
    pages = [{"url": "{}/page-{}".format(SITE, i)} for i in range(outside)]
    pages += [{"url": "{}{}item-{}".format(SITE, prefix, i)} for i in range(inside)]
    return pages


def _run(text, pages=None):
    result = CA.SkillResult("crawl-access-audit")
    CA._check_robots_blocks(result, _robots(text), SITE, pages or [])
    return result


def _findings(result, id_hint):
    return [f for f in result.findings if f["id_hint"] == id_hint]


def _reason(result, check):
    return next((n["reason"] for n in result.not_applicable if n["check"] == check), "")


SECTION = "robots-blocks-answer-crawler-sections"
TRAINING_SECTION = "robots-blocks-training-crawler-sections"


# --------------------------------------------------------------------------
# The finding the seam swallowed
# --------------------------------------------------------------------------

def test_a_section_closed_to_named_answer_crawlers_is_a_finding():
    result = _run(MIXED_GROUP, _pages())
    found = _findings(result, SECTION)
    assert found, "a group naming three answer crawlers closes /store/ and nothing fired"
    assert found[0]["root_cause"] == "robots-block"
    assert found[0]["mechanism"] == "A"


def test_the_finding_names_the_crawlers_and_the_paths():
    """A reader has to be able to hold the finding against their own file."""
    evidence = _findings(_run(MIXED_GROUP, _pages()), SECTION)[0]["evidence"]
    for token in ("OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "/store/"):
        assert token in evidence


def test_the_finding_counts_the_crawled_pages_behind_the_block():
    """"a path is disallowed" is a fact about a file. "12 of the 14 pages this
    crawl read sit under it" is a fact about the site, and only the second one
    tells an owner what the rule costs."""
    finding = _findings(_run(MIXED_GROUP, _pages(inside=12, outside=2)), SECTION)[0]
    assert "12 of the 14 pages this crawl read" in finding["evidence"]
    assert finding["affected_page_count"] == 12
    assert all("/store/" in url for url in finding["affected_pages"])


@pytest.mark.parametrize("inside,outside,severity,confidence", [
    # A twelfth-of-nothing rule would report the same severity for a shop's
    # whole catalogue and for a two-page corner, so the crawl's own pages set
    # it: enough of them behind the rule, and the section is a section.
    (12, 2, "high", "high"),
    # One page behind the rule clears a tenth of a nine-page crawl on its own,
    # and one page is not a section.
    (1, 8, "medium", "high"),
    # Nothing was fetched behind the rule. It still closes the path, and how
    # much is behind it was not measured - so the severity does not assume.
    (0, 9, "medium", "medium"),
])
def test_severity_follows_how_much_of_the_crawl_sits_behind_the_path(
        inside, outside, severity, confidence):
    finding = _findings(_run(MIXED_GROUP, _pages(inside, outside)), SECTION)[0]
    assert finding["severity"] == severity
    assert finding["confidence"] == confidence


def test_an_unfetched_section_says_it_was_not_measured():
    evidence = _findings(_run(MIXED_GROUP, _pages(0, 9)), SECTION)[0]["evidence"]
    assert "unverified" in evidence


# --------------------------------------------------------------------------
# The training-versus-answer distinction, which predates this check
# --------------------------------------------------------------------------

def test_training_crawlers_in_the_same_group_stay_at_info():
    """The worst thing this audit has been caught saying was "allow-list GPTBot
    so your pages can be cited". A section block must not become a second
    route to it: the same line closes /store/ to six training crawlers and
    opt-out tokens, and that half is a rights decision."""
    result = _run(MIXED_GROUP, _pages())
    training = _findings(result, TRAINING_SECTION)
    assert training and training[0]["severity"] == "info"
    assert "often deliberate" in training[0]["title"]
    for token in ("GPTBot", "ClaudeBot", "CCBot", "Google-Extended"):
        assert token in training[0]["evidence"]


def test_no_training_crawler_appears_in_the_answer_side_finding():
    finding = _findings(_run(MIXED_GROUP, _pages()), SECTION)[0]
    for token in ("GPTBot", "ClaudeBot", "CCBot", "Meta-ExternalAgent"):
        assert token not in finding["evidence"]
    assert finding["severity"] in ("high", "medium")


def test_the_fix_keeps_the_training_opt_out_and_the_snippet_splits_the_group():
    """Following the advice must not reopen a training opt-out the owner made
    on purpose. The remedy is two groups, not one group with the block gone."""
    finding = _findings(_run(MIXED_GROUP, _pages()), SECTION)[0]
    steps = " ".join(finding["suggested_action"]["how_to_fix"])
    assert "rights decision" in steps
    snippet = finding["suggested_action"]["snippet"]
    assert "User-agent: OAI-SearchBot" in snippet and "Allow: /" in snippet
    assert "User-agent: GPTBot" in snippet and "Disallow: /store/" in snippet
    # And the snippet is a robots.txt the parser reads back the way it reads:
    # the answer crawlers allowed, the training crawlers still blocked.
    reparsed = parse_robots(snippet)
    assert not section_disallows(reparsed, "OAI-SearchBot")
    assert section_disallows(reparsed, "GPTBot") == ["/store/"]


def test_a_group_naming_only_training_crawlers_produces_no_defect():
    result = _run("User-agent: GPTBot\nUser-agent: CCBot\nDisallow: /store/\n", _pages())
    assert not _findings(result, SECTION)
    assert _findings(result, TRAINING_SECTION)[0]["severity"] == "info"
    # And the answer-crawler check answers its own question rather than being
    # left to pass silently beside a finding about somebody else's agents.
    assert "both shapes of block were tested" in _reason(
        result, "robots-blocks-ai-answer-crawlers")


# --------------------------------------------------------------------------
# Reported once
# --------------------------------------------------------------------------

def test_a_section_inside_a_whole_site_block_is_not_reported_twice():
    """`Disallow: /` with a narrower rule beside it is one defect with one fix,
    and the whole-site finding is the one that states it."""
    text = ("User-agent: OAI-SearchBot\nUser-agent: ChatGPT-User\n"
            "Disallow: /\nDisallow: /store/\n")
    result = _run(text, _pages())
    assert not _findings(result, SECTION)
    assert _findings(result, "robots-blocks-answer-crawlers")
    assert section_disallows(_robots(text), "OAI-SearchBot") == []


def test_an_agent_shut_out_of_the_site_and_another_closed_out_of_a_section():
    """Two agents, two different rules, two findings - because the fix for one
    is not the fix for the other."""
    result = _run("User-agent: OAI-SearchBot\nDisallow: /\n\n"
                  "User-agent: ChatGPT-User\nDisallow: /store/\n", _pages())
    section = _findings(result, SECTION)[0]
    assert "ChatGPT-User" in section["evidence"]
    assert "OAI-SearchBot" not in section["evidence"]
    assert _findings(result, "robots-blocks-answer-crawlers")


def test_a_path_the_wildcard_group_closes_too_belongs_to_the_other_check():
    """A section shut to every crawler is site-wide policy, not a decision
    about AI - and `robots-blocks-content-paths` is the check that reads the
    `User-agent: *` group. Reporting it here as well would price one rule
    twice and describe the second copy wrongly."""
    text = ("User-agent: *\nDisallow: /members-area/\n\n"
            "User-agent: GPTBot\nUser-agent: OAI-SearchBot\n"
            "Disallow: /members-area/\n")
    result = _run(text, _pages(prefix="/members-area/"))
    assert not _findings(result, SECTION)
    assert not _findings(result, TRAINING_SECTION)
    assert section_disallows(_robots(text), "OAI-SearchBot") == []


def test_a_named_group_that_closes_only_plumbing_is_not_a_finding():
    """The cart, the search results and a query-string filter. Firing on these
    would put this check on most of the web, and the vocabulary that tells a
    section from its plumbing already exists."""
    result = _run("User-agent: OAI-SearchBot\nUser-agent: GPTBot\n"
                  "Disallow: /cart\nDisallow: /checkouts/\nDisallow: /search\n"
                  "Disallow: /*?sort=\nDisallow: /assets/\n", _pages())
    assert not _findings(result, SECTION)
    assert not _findings(result, TRAINING_SECTION)


# --------------------------------------------------------------------------
# The false clean
# --------------------------------------------------------------------------

def test_the_answer_crawler_check_no_longer_passes_silently():
    """A bare name under "checks that ran and found nothing wrong" carries no
    wording, so a check testing one shape of block had nowhere to say so - and
    certified a site whose shop was closed to every answer crawler it names."""
    reason = _reason(_run("User-agent: *\nAllow: /\n"),
                     "robots-blocks-ai-answer-crawlers")
    assert "disallowed from `/`" in reason
    assert "closes a section of the site" in reason
    assert "`User-agent: *`" in reason


def test_the_check_that_found_the_section_is_the_one_the_report_names():
    """Not a pass, and not somebody else's check: the question "is an AI answer
    crawler blocked" is the one this answers, and the appendix has to agree."""
    result = _run(MIXED_GROUP, _pages())
    assert "robots-blocks-ai-answer-crawlers" in result.fired_checks
    assert not [n for n in result.not_applicable
                if n["check"] == "robots-blocks-ai-answer-crawlers"]


def test_the_wildcard_decline_points_at_the_check_that_read_the_named_groups():
    """"robots.txt names specific crawlers and has no `User-agent: *` group" is
    true and reads as "no path is closed", which was the opposite of the truth
    about the file that prompted all of this."""
    reason = _reason(_run(MIXED_GROUP, _pages()), "robots-blocks-content-paths")
    assert "robots-blocks-ai-answer-crawlers" in reason


def test_a_decline_never_denies_a_disallow_line_that_exists():
    """The first wording read "and disallows no real content to any" about a
    file whose named group carries `Disallow: /cart`. A sentence saying nothing
    was found may only describe what was looked at."""
    reason = _reason(_run("User-agent: OAI-SearchBot\nDisallow: /cart\n"),
                     "robots-blocks-ai-answer-crawlers")
    assert "ordinary hygiene" in reason
    assert "disallows no real content" not in reason


# --------------------------------------------------------------------------
# The rule machinery underneath
# --------------------------------------------------------------------------

@pytest.mark.parametrize("agent,named", [
    ("OAI-SearchBot", True),
    # Prefix matching, the same rule `group_for` follows: a token is matched
    # from the start of the name or not at all.
    ("OAI-SearchBot/1.0", True),
    ("ChatGPT-User", False),
])
def test_a_group_names_an_agent_by_prefix_and_never_by_wildcard(agent, named):
    parsed = parse_robots("User-agent: *\nDisallow: /a\n\n"
                          "User-agent: OAI-SearchBot\nDisallow: /b\n")
    assert names_agent(parsed, agent) is named


def test_the_wildcard_group_alone_names_nobody():
    """`group_for` falls back to `*`, so it cannot tell a rule written about
    one crawler from the site's policy for all of them."""
    assert names_agent(parse_robots("User-agent: *\nDisallow: /a\n"), "GPTBot") is False


@pytest.mark.parametrize("rule,path,covered", [
    ("/store/", "/store/shoe-1", True),
    ("/store/", "/storefront", False),
    ("/store", "/storefront", True),
    ("/*/archive", "/2019/archive", True),
    ("/store/$", "/store/shoe-1", False),
])
def test_a_page_is_counted_only_where_the_rule_really_covers_it(rule, path, covered):
    assert rule_matches_path(rule, path) is covered
