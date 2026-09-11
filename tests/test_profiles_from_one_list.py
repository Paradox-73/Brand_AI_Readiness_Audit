# -*- coding: utf-8 -*-
"""One page, one answer about which off-site accounts it points at.

A municipal site's report stated two numbers for one fact. Its finding read
"Distinct off-site profiles linked from the crawled pages or listed in
`sameAs`: <two of them>" and told the town to go and claim more; the paste-ready
`sameAs` block two lines further down listed seven, and the snapshot from the
same run held seven. The two fields the report was reading - `social_profiles`
and `declared_profiles` - were built by two functions that read overlapping
candidates through different tests, so a declaration the site had made could be
in one and absent from the other.

`_off_site_profiles` is now the one list and both are views of it. These tests
pin that, and the three accounts extraction was losing before they ever reached
a count:

  a one-page coaching site   "Connect": Instagram and LinkedIn side by side in
                             one `<ul>`. The crawl stored both, the matcher
                             accepts both, and the report said one profile,
                             because the LinkedIn address names a person
  a documentation tree       its package registry entry and its archive deposit
                             were missed while the tool's own recommendation
                             named "a package or registry entry" as a profile
                             to go and get
  a hosted documentation     the site's own name in front of somebody else's
  address                    documentation host, with no account path to read
"""

from __future__ import annotations

import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import make_soup  # noqa: E402
from page_extract import (  # noqa: E402
    _account_shape, _declared_profiles, _off_site_profiles,
    _profiles_by_platform, _profiles_the_page_publishes, _social_profiles,
    extract_page,
)

COACH = "https://invented-coach.test"
DOCS = "https://invented-library.test"

COACH_PAGE = """<html><body>
<h1>Coaching for people who lead teams</h1>
<p>I run one-to-one sessions for new managers, in person and online.</p>
<h2>Connect</h2>
<ul class="connect">
  <li><a href="https://www.instagram.com/inventedcoach/">Instagram Instagram</a></li>
  <li><a href="https://www.linkedin.com/in/an-invented-person">LinkedIn LinkedIn</a></li>
</ul>
</body></html>"""


def _links(*urls):
    return [{"url": url, "text": ""} for url in urls]


# --------------------------------------------------------------------------
# The accounts extraction was losing
# --------------------------------------------------------------------------

def test_a_row_of_account_links_is_the_page_claiming_them():
    """Two links, two platforms, nothing else in the list. That is the page
    saying "these are my accounts" - the same assertion `rel="me"` makes,
    written in ordinary markup."""
    soup = make_soup(COACH_PAGE)
    published = _profiles_the_page_publishes(soup, COACH + "/", COACH)
    assert published == {"https://www.instagram.com/inventedcoach/",
                         "https://www.linkedin.com/in/an-invented-person"}


def test_a_one_person_business_keeps_the_account_that_names_the_person():
    """The report said one profile, Instagram. The page has two, side by side
    in one `<ul>`, and `counts_as_off_site_profile` accepts both - so the loss
    was upstream of the matcher, in this file."""
    soup = make_soup(COACH_PAGE)
    found = _social_profiles(
        _links("https://www.instagram.com/inventedcoach/",
               "https://www.linkedin.com/in/an-invented-person"),
        [], soup, COACH + "/")
    assert sorted(found) == ["Instagram", "LinkedIn"]


def test_a_named_employee_in_a_customer_story_is_still_somebody_elses():
    """The case the personal-role rule exists for. A link to a person inside
    prose has nothing publishing it as this site's, so it stays out."""
    soup = make_soup(
        "<article><p>We worked with "
        '<a href="https://www.linkedin.com/in/a-named-buyer">a buyer at a '
        "client</a> for eighteen months on this programme.</p></article>")
    assert _social_profiles(
        _links("https://www.linkedin.com/in/a-named-buyer"), [], soup,
        "https://invented-agency.test/") == {}


def test_the_path_still_reports_that_the_account_names_a_person():
    """Nothing was thrown away to make the case above work: the shape reader
    still says which accounts name an individual, so the decision can be taken
    where the evidence is."""
    assert _account_shape(
        "https://www.linkedin.com/in/an-invented-person")[0] == "personal"


def test_a_registry_entry_and_an_archive_deposit_count():
    """A published package and a deposited dataset are the same evidence a
    directory listing is: a third party holding a record under this name. The
    tool was recommending the owner go and get what the site already had."""
    found = _social_profiles(
        _links("https://an-invented-registry.test/project/invented-library",
               "https://an-invented-archive.test/records/1234567",
               "https://an-invented-doi.test/10.5281/deposit.1234567"),
        [], make_soup("<p>x</p>"), DOCS + "/")
    assert len(found) == 3, found


def test_a_subdomain_that_spells_the_site_name_is_an_account():
    """No path to read, so `looks_like_an_account` refuses it and says in its
    own docstring that the caller decides. Nothing decided, and a hosted
    documentation address was missed on every run."""
    found = _social_profiles(
        _links("https://invented-library.a-docs-host.test/en/stable/"),
        [], make_soup("<p>x</p>"), DOCS + "/")
    assert found == {"a-docs-host.test":
                     "https://invented-library.a-docs-host.test/en/stable/"}


def test_a_subdomain_that_spells_somebody_elses_name_is_not():
    """Equality after dropping punctuation, not containment. A stranger's
    subdomain is not this site's account."""
    assert _social_profiles(
        _links("https://a-different-project.a-docs-host.test/en/stable/"),
        [], make_soup("<p>x</p>"), DOCS + "/") == {}


# --------------------------------------------------------------------------
# One list, and the views of it
# --------------------------------------------------------------------------

DECLARED = [{"@type": "Organization",
             "sameAs": ["https://www.instagram.com/inventedcoach/",
                        # A host with no account path to read. The declaration
                        # is the whole of the evidence, and it was in
                        # `declared_profiles` and absent from
                        # `social_profiles` - one page, two answers.
                        "https://a-donation-portal.test/"]}]


def test_a_declaration_the_site_made_is_in_both_views():
    """The defect in one line. The count that says "linked from the crawled
    pages or listed in `sameAs`" could not see half of what the site had
    listed there, while the report's own paste-ready block printed all of it."""
    profiles = _off_site_profiles([], DECLARED, make_soup("<p>x</p>"),
                                  COACH + "/")
    linked = _profiles_by_platform(profiles)
    declared = _profiles_by_platform(profiles, declared_only=True)
    assert set(declared) <= set(linked), (declared, linked)
    assert "a-donation-portal.test" in linked


def test_the_record_publishes_the_list_the_two_views_are_built_from():
    """Both fields in the snapshot trace to one reading of the page, and the
    evidence each account carries travels with it."""
    record = extract_page(COACH + "/", COACH + "/", 200,
                          {"content-type": "text/html"}, COACH_PAGE, [], 10, 0,
                          "seed", COACH)
    listed = record["off_site_profiles"]
    assert _profiles_by_platform(listed) == record["social_profiles"]
    assert _profiles_by_platform(listed, declared_only=True) \
        == record["declared_profiles"]
    by_platform = {entry["platform"]: entry for entry in listed}
    assert by_platform["LinkedIn"]["personal"] is True
    assert by_platform["LinkedIn"]["published"] is True
    assert by_platform["Instagram"]["declared"] is False


def test_the_declared_view_still_answers_on_json_ld_alone():
    """`_declared_profiles` keeps its old contract - `sameAs` and nothing else
    - so a caller with no document to hand still gets the same answer."""
    assert _declared_profiles(DECLARED) == {
        "Instagram": "https://www.instagram.com/inventedcoach/",
        "a-donation-portal.test": "https://a-donation-portal.test/"}
