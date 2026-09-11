# -*- coding: utf-8 -*-
"""Four paste-ready fixes that would have made the site worse.

`test_fixes_that_would_harm.py` holds earlier cases of the same class of
defect. These are the ones found next, and each is a real
finding from a real audit whose suggested action, if followed, damages the site
it was written for:

  a page of linked video titles      an `FAQPage` block whose three answers
                                     exist nowhere on the page
  a volunteer non-profit             `LocalBusiness` markup claiming a PO Box
                                     and two other people's addresses as its
                                     own branches
  a media site's robots.txt          `Allow: /` under nine crawler names,
                                     reopening the API, the GraphQL endpoint
                                     and two signed-in JSON endpoints that the
                                     same finding's own instructions say to
                                     leave alone
  a store answering 402 everywhere   two checks printed as "ran against this
                                     site and were clean" on a crawl that
                                     fetched nothing

The fourth is not a fix that harms so much as a report that certifies: a check
registered and then answered by neither a finding nor a decline is rendered as
a pass.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402
from robots_parser import (  # noqa: E402
    benign_disallow, is_disallowed, parse_robots,
)

SITE = "https://example.test"


def _skill(name):
    """Load a sub-skill's check module by path, the way the suite already does."""
    path = os.path.join(ROOT, "skills", name, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("soundness_" + name.replace("-", "_"),
                                                  path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _skill("structured-data-audit")
CA = _skill("crawl-access-audit")


# --------------------------------------------------------------------------
# 1. FAQPage markup for a page that answers nothing
# --------------------------------------------------------------------------

# Three headings that read as questions and are the titles of the videos the
# page links to. The page holds no answer to any of them; the answers are in
# the videos.
_PUZZLE_TITLES = [
    "Can you solve the bridge riddle?",
    "Can you solve the prisoner hat riddle?",
    "Can you solve the unstoppable blob riddle?",
]


def _card_page(path, page_type="other", **over):
    """A listing page whose cards are a thumbnail link and a title link.

    The thumbnail anchor comes first in the document, which is what the crawl
    records: `_links` keeps one entry per destination URL and the first anchor
    wins, so the entry's text is empty and the title anchor is dropped as a
    duplicate.
    """
    page = {
        "url": SITE + path,
        "page_type": page_type,
        "status": 200,
        "title": "Puzzles",
        "headings": {"h2": list(_PUZZLE_TITLES)},
        "sections": [{"heading": title, "first_paragraph": "", "link_ratio": 1.0,
                      "navigational": True} for title in _PUZZLE_TITLES],
        "body_text": "Puzzles " + " ".join(
            "{} 5:47".format(title) for title in _PUZZLE_TITLES),
        "links": {"internal": [
            {"url": "{}/watch/{}".format(SITE, index), "text": ""}
            for index, _ in enumerate(_PUZZLE_TITLES)]},
        "jsonld": [],
        "jsonld_types": [],
    }
    page.update(over)
    return page


def _answered_page(path, questions, page_type="other", **over):
    """A page that prints an answer under each of its question headings."""
    sections = [{"heading": q, "first_paragraph": a, "link_ratio": 0.0,
                 "navigational": False} for q, a in questions]
    page = {
        "url": SITE + path,
        "page_type": page_type,
        "status": 200,
        "title": "Help",
        "headings": {"h2": [q for q, _ in questions]},
        "sections": sections,
        "body_text": " ".join("{} {}".format(q, a) for q, a in questions),
        "links": {"internal": []},
        "jsonld": [],
        "jsonld_types": [],
    }
    page.update(over)
    return page


def _faq_result(pages):
    result = SkillResult("structured-data-audit")
    by_type = {}
    for page in pages:
        by_type.setdefault(page["page_type"], []).append(page)
    SD._check_faq(result, by_type, pages)
    return result


def _payload(snippet):
    return json.loads(snippet[snippet.index("{"):snippet.rindex("}") + 1])


def test_the_link_title_test_cannot_see_a_card_whose_image_link_came_first():
    """Why the two guards that already existed did not catch this page.

    `_reads_as_a_link_title` compares a heading against the anchor text the
    crawl recorded, and the crawl records one entry per destination URL with
    the first anchor's text. A card whose thumbnail image is a link to the same
    destination contributes an empty text and swallows the title anchor, so the
    question was never in the anchor set to be matched against.
    """
    page = _card_page("/puzzles")
    anchors = SD._headings_that_are_links_away(page)
    assert anchors == set(), (
        "every recorded anchor text is empty, which is the whole problem: there "
        "is nothing here for the heading to be matched against")
    assert not SD._reads_as_a_link_title(_PUZZLE_TITLES[0], anchors)


def test_a_page_of_linked_titles_is_not_a_page_of_questions_and_answers():
    """The finding named it "1 page of questions and answers"; it answers none."""
    pages = [_card_page("/puzzles"), _answered_page("/", [], page_type="home")]
    assert SD._unmarked_qa_pages({"other": [pages[0]]}) == []
    assert not _faq_result(pages).findings


def test_a_page_typed_faq_still_has_to_answer_something():
    """The crawl types a page `faq` from four question-shaped H2s or from its
    own address, and neither says an answer is printed on it."""
    page = _card_page("/puzzles", page_type="faq")
    assert not SD._answers_questions(page)
    assert not _faq_result([page]).findings


def test_the_snippet_never_writes_an_answer_the_page_does_not_have():
    """The block offered for that page had three `acceptedAnswer` values with
    no source anywhere on it."""
    payload = _payload(SD._faq_snippet(_card_page("/puzzles")))
    assert [q["name"] for q in payload["mainEntity"]] == [
        "<question as a visitor would ask it>"]


def test_a_real_question_and_answer_page_is_still_reported():
    """The same detector was right on a shop, and has to stay right."""
    questions = [
        ("Is the stoneware dishwasher safe?",
         "Yes. Every piece is fired to 1260C and is safe in the dishwasher."),
        ("How long does a commission take?",
         "Restaurant commissions take 8 to 12 weeks from deposit to delivery."),
        ("Do you ship outside the UK?",
         "Yes, to the EU. We do not currently ship outside Europe."),
    ]
    page = _answered_page("/faq", questions, page_type="faq")
    finding = _faq_result([page]).findings[0]
    published = _payload(finding["suggested_action"]["snippet"])["mainEntity"]
    assert [q["name"] for q in published] == [q for q, _ in questions]
    assert [q["acceptedAnswer"]["text"] for q in published] == [a for _, a in questions]


def test_a_question_answered_under_an_h3_is_kept():
    """`sections` covers H2s only, so the answer to a question one level down
    is read from the words between that heading and the next one."""
    page = {
        "url": SITE + "/about",
        "page_type": "about",
        "status": 200,
        "title": "About",
        "headings": {"h2": ["About"], "h3": ["May I reuse your data?",
                                             "How often is it updated?",
                                             "Who maintains it?"]},
        "sections": [],
        "body_text": (
            "About "
            "May I reuse your data? Yes, under the same licence the dataset carries, "
            "with attribution. "
            "How often is it updated? Every weekday morning, from the overnight batch. "
            "Who maintains it? A team of four, listed on the contributors page."),
        "links": {"internal": []},
        "jsonld": [],
        "jsonld_types": [],
    }
    assert SD._question_headings_on(page) == sorted([
        "How often is it updated?", "May I reuse your data?", "Who maintains it?"])
    assert _faq_result([page]).findings


def test_a_heading_with_only_a_running_time_under_it_is_not_answered():
    """The floor is the one the crawl already applies to a section's own first
    paragraph, so the two cannot disagree."""
    page = _card_page("/puzzles")
    assert SD._question_headings_on(page) == []
    assert SD._questions_asked_on(page) == sorted(_PUZZLE_TITLES), (
        "the questions are still read; only the claim that they are answered is gone")


# --------------------------------------------------------------------------
# 2. LocalBusiness markup claiming other people's addresses
# --------------------------------------------------------------------------

def _address_page(path, street, postcode, text, page_type="other"):
    return {
        "url": SITE + path,
        "page_type": page_type,
        "status": 200,
        "jsonld": [],
        "jsonld_types": [],
        "text": text,
        "contact_facts": {"street_hint": street, "postcode_hint": postcode,
                          "postcode_source": "beside the street", "declared_phones": []},
    }


def _charity_snapshot():
    """The three "branches" of a volunteer non-profit.

    A PO Box, an address inside a book review - which is the book author's -
    and a directory of freelance consultants listing other people's towns.
    """
    return {
        "origin": SITE, "site": "example.test", "brand": {"name": "Riverbank Trust"},
        "pages": [
            _address_page(
                "/contact", "1290 Kirkgate WA", "98104",
                "Write to us at PO Box 1290 Kirkgate WA 98104 and we will reply.",
                page_type="contact"),
            _address_page(
                "/reviews/the-long-summer", "41 Marine Parade", "BN2 1TL",
                "Reviewed this month. The author writes from a flat at "
                "41 Marine Parade, Brighton BN2 1TL, and the book carries that "
                "address on its title page."),
            _address_page(
                "/directory/consultants", "88 Cross Lane", "LS2 7EE",
                "Freelance consultants available for hire. Priya Raman works from "
                "88 Cross Lane, Leeds LS2 7EE and takes commissions directly."),
        ],
    }


def test_a_post_office_box_is_not_a_place_anyone_can_visit():
    page = _charity_snapshot()["pages"][0]
    assert SD._address_is_a_post_box(page)


def test_a_street_printed_near_a_post_box_is_still_a_street():
    """The mark has to introduce the address, not merely appear on the page."""
    page = _address_page(
        "/contact", "12 High Street", "YO1 9TT",
        "Post to PO Box 4, or call in at 12 High Street, York YO1 9TT.",
        page_type="contact")
    assert not SD._address_is_a_post_box(page)


def test_an_address_on_a_page_about_somebody_else_is_not_the_sites():
    review, directory = _charity_snapshot()["pages"][1:]
    assert not SD._address_belongs_to_the_site(review, "Riverbank Trust")
    assert not SD._address_belongs_to_the_site(directory, "Riverbank Trust")


def test_a_non_profit_is_not_told_it_looks_like_a_chain_of_branches():
    """Three addresses, none of them a place this site keeps."""
    snapshot = _charity_snapshot()
    assert SD._places_this_site_can_be_visited_at(snapshot) == []
    result = SkillResult("structured-data-audit")
    SD._check_per_location_markup(result, snapshot, snapshot["pages"])
    assert not result.findings
    reason = [d["reason"] for d in result.not_applicable
              if d["check"] == "per-location-markup"][0]
    assert "post-office box" in reason


@pytest.mark.parametrize("wording", [
    "Our Kirkgate shop is at {}, and is open Thursday to Sunday.",
    "Riverbank Trust, {}. Registered charity number 1090000.",
])
def test_a_branch_page_that_speaks_for_the_brand_still_counts(wording):
    """The chain this check exists for must survive both tests."""
    line = "51 Berwick Street, London W1F 8SJ"
    page = _address_page("/shops/soho", "51 Berwick Street", "W1F 8SJ",
                         wording.format(line))
    assert SD._address_belongs_to_the_site(page, "Riverbank Trust")


def test_a_branch_page_with_nothing_but_an_address_still_counts():
    """A location page printing a bare address in an `<address>` element is the
    commonest branch page there is, and no words around it say so."""
    page = _address_page("/shops/soho", "51 Berwick Street", "W1F 8SJ",
                         "51 Berwick Street, London W1F 8SJ", page_type="location")
    assert SD._address_belongs_to_the_site(page, "Riverbank Trust")


def test_the_chain_finding_still_fires():
    towns = [("51 Berwick Street", "W1F 8SJ", "Soho"),
             ("12 Park Row", "LS1 5HD", "Leeds"),
             ("9 Union Street", "BS1 5EF", "Bristol")]
    snapshot = {
        "origin": SITE, "site": "example.test", "brand": {"name": "Riverbank Trust"},
        "pages": [_address_page(
            "/shops/{}".format(town.lower()), street, code,
            "Our {} shop is at {}, {} {}.".format(town, street, town, code))
            for street, code, town in towns],
    }
    assert len(SD._places_this_site_can_be_visited_at(snapshot)) == 3
    result = SkillResult("structured-data-audit")
    SD._check_per_location_markup(result, snapshot, snapshot["pages"])
    assert result.findings, "a real chain with no place markup is still the defect"


# --------------------------------------------------------------------------
# 3a. robots.txt paths that are plumbing, not content
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rule", [
    "/dashboard", "/dashboard/", "/dashboards", "/settings", "/settings/",
    "/preferences",
])
def test_a_signed_in_account_screen_is_not_real_content(rule):
    assert benign_disallow(rule)


@pytest.mark.parametrize("rule", [
    "/me.json", "/follows.json", "/translations.json", "/strings.xml",
    "/config.yaml", "/bundle.js.map",
])
def test_a_file_named_by_its_extension_is_not_a_page(rule):
    assert benign_disallow(rule)


@pytest.mark.parametrize("rule", ["/RCS/", "/CVS/", "/.hg/", "/.bzr/", "/_darcs/"])
def test_a_version_control_metadata_directory_is_not_a_page(rule):
    assert benign_disallow(rule)


@pytest.mark.parametrize("rule", [
    # The same words continuing into a real section name. Every one of these is
    # a page some site publishes, and excusing it would hide a real block.
    "/dashboard-cameras", "/settings-guide", "/preferences-explained",
    # Lower case, which is what separates a page of curricula vitae from the
    # directory a version-control system leaves behind.
    "/cvs", "/cvs/",
])
def test_the_words_these_are_made_of_are_still_real_content(rule):
    assert not benign_disallow(rule)


# --------------------------------------------------------------------------
# 3b. a snippet that reopens what the site closed
# --------------------------------------------------------------------------

_CLOSED = ["/api/", "/graphql/", "/_next/data/", "/me.json", "/translations.json"]

# Real sections, because the account screens the original report named are now
# recognised as plumbing and produce no finding at all - which is the other
# half of this fix and is covered above.
_REPORTED = ["/store/", "/research"]


def _media_robots():
    """A group naming the answer crawlers, closing account screens and
    endpoints the owner meant to close."""
    agents = list(CA.ANSWER_CRAWLERS[:3])
    lines = ["User-agent: {}".format(name) for name in agents]
    lines += ["Disallow: {}".format(rule) for rule in _REPORTED + _CLOSED]
    lines += ["", "User-agent: *", "Disallow: /admin"]
    robots = parse_robots("\n".join(lines))
    robots.update({"status": 200, "url": SITE + "/robots.txt"})
    return robots, agents


def _section_finding():
    robots, _ = _media_robots()
    result = SkillResult("crawl-access-audit")
    CA._check_robots_blocks(result, robots, SITE, [])
    return next(f for f in result.findings
                if f["id_hint"] == "robots-blocks-answer-crawler-sections")


def test_the_snippet_allows_only_the_paths_the_finding_names():
    snippet = _section_finding()["suggested_action"]["snippet"]
    allows = [line.split(":", 1)[1].strip() for line in snippet.splitlines()
              if line.lower().startswith("allow:")]
    assert sorted(allows) == sorted(_REPORTED)
    assert "Allow: /" not in snippet.splitlines(), (
        "a bare Allow: / reopens every path the group closes")


def test_pasting_the_snippet_leaves_the_closed_endpoints_closed():
    """The test the old snippet fails: apply it and read the file back.

    Records with the same product token combine, so the snippet's group joins
    the one already there; the reported paths open on the Allow/Disallow tie
    and every other rule goes on working.
    """
    robots, agents = _media_robots()
    original = "\n".join(
        ["User-agent: {}".format(name) for name in agents]
        + ["Disallow: {}".format(rule) for rule in _REPORTED + _CLOSED])
    after = parse_robots(original + "\n\n" + _section_finding()["suggested_action"]["snippet"])

    for rule in _REPORTED:
        assert not is_disallowed(after, agents[0], rule), (
            "the finding asks for {} to be opened".format(rule))
    for rule in _CLOSED:
        assert is_disallowed(after, agents[0], rule), (
            "{} is closed on purpose and the fix must not reopen it".format(rule))


def test_the_steps_name_the_rules_the_snippet_does_not_touch():
    steps = " ".join(_section_finding()["suggested_action"]["how_to_fix"])
    assert "/api/" in steps and "not a replacement" in steps


# --------------------------------------------------------------------------
# 4. two checks reported as clean on a crawl that fetched nothing
# --------------------------------------------------------------------------

_NOT_SERVING_REASON = (
    "Every one of the 4 address(es) this audit reached on example.test answered "
    "HTTP 402: the client must pay before the resource is served.")


def _switched_off_snapshot():
    """A store answering the same status at every address it was asked for."""
    return {
        "origin": SITE, "site": "example.test", "seed_url": SITE + "/",
        "pages": [],
        "robots": {"url": SITE + "/robots.txt", "status": 402,
                   "groups": [], "sitemaps": [], "errors": []},
        "sitemaps": [], "brand": {"name": "Example"},
        "crawl": {"pages_crawled": 0, "pages_ok": 0, "pages_read": 0},
        "audit_blocked_by_robots": True,
        "site_not_serving": {
            "status": 402, "meaning": "the client must pay before the resource is served",
            "addresses": [SITE + "/"], "addresses_answered": 4,
            "reason": _NOT_SERVING_REASON},
    }


def _crawl_access_outcomes():
    result = CA.run(_switched_off_snapshot(), allow_network=False)
    declined = {d["check"]: d["reason"] for d in result.not_applicable}
    passed = set(result.checks_run) - result.fired_checks - set(declined)
    return declined, passed


def test_no_check_is_rendered_as_a_pass_on_a_crawl_that_fetched_nothing():
    """A check that neither fires nor declines is printed under "these ran
    against this site and were clean - they are not omissions"."""
    declined, passed = _crawl_access_outcomes()
    assert not passed, "certified as clean without reading anything: {}".format(
        sorted(passed))


@pytest.mark.parametrize("name", [
    "homepage-reachable", "non-200-rate", "redirect-chain-length",
    "noindex-on-content-pages", "robots-txt-reachable",
])
def test_the_decline_says_the_site_is_not_serving(name):
    declined, _ = _crawl_access_outcomes()
    assert _NOT_SERVING_REASON in declined[name], (
        "the honest reason is the site's own status, not a missing page")


def test_a_homepage_recorded_under_its_redirect_destination_is_found():
    """`p["url"] == home_url` matched nothing where the front page is stored
    under the address that answered, and the check then said nothing at all."""
    landed = {"url": SITE + "/en/", "final_url": SITE + "/en/",
              "page_type": "home", "status": 200}
    assert CA._homepage_record([landed], SITE + "/") is landed

    answered = {"url": SITE + "/index.php", "final_url": SITE + "/",
                "page_type": "other", "status": 200}
    assert CA._homepage_record([answered], SITE + "/") is answered


def test_a_crawl_with_pages_and_no_front_page_still_declines():
    """And the reason names what was missing rather than borrowing the
    not-serving sentence from a site that is serving."""
    result = SkillResult("crawl-access-audit")
    pages = [{"url": SITE + "/about", "page_type": "about", "status": 200,
              "redirect_chain": [], "headings": {}, "links": {}}]
    CA._check_status_and_indexability(
        result, {"origin": SITE, "pages": pages}, pages, pages)
    reason = [d["reason"] for d in result.not_applicable
              if d["check"] == "homepage-reachable"][0]
    assert "no record of the front page" in reason
