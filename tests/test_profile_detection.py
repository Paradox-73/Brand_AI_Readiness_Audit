# -*- coding: utf-8 -*-
"""What makes a link an off-site profile, when no list of platforms is allowed.

Off-site profile breadth is the strongest separator this marketplace measured,
so what counts here moves the one number the README stakes its case on. For
most of this project's life what counted was a hardcoded table of about thirty
platform hostnames, each with a regex for that platform's profile URLs. A
closed list is wrong in one direction only, and it produced two false findings
in one validation pass:

  "No off-site profile is linked      the site's home page links three
  from the crawled pages or           brand-named storefronts on the three
  declared in sameAs" - high          largest shopping platforms in its own
  severity, and the whole report's    country, and all three were sitting in
  headline                            this crawl's own `links.external`. The
                                      table held thirty Western hosts and not
                                      one Asian one.

  "Only 2 off-site profiles are       the site links a brand-named group on a
  linked: Bluesky, X" - in the        social platform and a brand-named channel
  confident tier, and third in        on a video platform, both from its home
  what to fix first                   page. The social regex allowed one path
                                      segment, so a group address could not
                                      match; the video regex was anchored at
                                      the end of the handle, so a channel
                                      address with a view segment after it
                                      could not match. The true count was 4.

The table had been extended twice before, each time after a real site turned
up a platform it could not see. This file tests the rule that replaced it: an
address is a profile when its *path* says it names an account, on any host at
all, and the shape of that path is a role word, a handle and a view - none of
which needs a platform to be recognised first.

Every host below is invented except the handful this audit names on purpose,
and those are only ever platforms, never example sites.
"""

from __future__ import annotations

import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import host_name_forms, make_soup  # noqa: E402
from page_extract import (  # noqa: E402
    SOCIAL_PLATFORMS, _account_shape, _platform_name, _profiles_among,
    _social_profiles,
)


# The audited site, and the romanised labels of its own address. That is the
# only name this stage has: the brand name is decided after the crawl finishes,
# which is why attribution belongs to `freshness-corroboration-audit` and shape
# belongs here.
SITE = "https://invented-footwear.example/"
SITE_NAMES = host_name_forms("invented-footwear.example")


def _found(urls, site_names=SITE_NAMES):
    """The platform keys `_profiles_among` records for these addresses."""
    return sorted(key for key, _url in _profiles_among(list(urls), site_names))


# --------------------------------------------------------------------------
# The two measured failures
# --------------------------------------------------------------------------

# A footwear maker's home page. Three storefronts on three marketplaces, each
# addressed the way marketplaces address a seller: a role word and the seller's
# name, or the seller's name alone at the root.
STOREFRONTS = [
    "https://a-fashion-marketplace.example/shop/invented-footwear/",
    "https://a-general-marketplace.example/invented-footwear/",
    "https://a-shopping-mall.example/store/invented-footwear/",
]


def test_a_storefront_on_a_marketplace_nobody_listed_is_still_a_profile():
    """The headline finding. "No off-site profile is linked from the crawled
    pages or declared in sameAs", at high severity, about a site whose own
    snapshot held three brand-named storefronts - because none of the three
    hosts was in a thirty-entry table of Western platforms."""
    assert len(_found(STOREFRONTS)) == 3


def test_the_headline_absence_claim_is_no_longer_made():
    """The finding this feeds reads the page record, so the assertion that
    matters is the one on the record itself."""
    links = [{"url": url} for url in STOREFRONTS]
    profiles = _social_profiles(links, [], make_soup("<p>x</p>"), SITE)
    assert len(profiles) == 3, "an empty social_profiles is what printed 'none'"


# A volunteer non-profit's home page. A group and a channel, both brand-named,
# both rejected by a regex written for a different shape of the same platform's
# URLs.
GROUP = "https://a-social-platform.example/groups/invented-charity"
CHANNEL = "https://a-video-platform.example/channel/UCinvented1234/featured"
ALREADY_COUNTED = [
    "https://a-microblog.example/profile/inventedcharity",
    "https://a-short-post-platform.example/@invented-charity",
]
CHARITY_NAMES = host_name_forms("invented-charity.example")


def test_a_group_and_a_channel_are_two_more_profiles_not_zero():
    """"Only 2 off-site profiles are linked" - the two whose URL happened to
    match the shape the regex for that platform was written around. A group
    address carries a role word before the name, and a channel address may name
    which of its views to open after it. Both are the same account."""
    two = _found(ALREADY_COUNTED, CHARITY_NAMES)
    four = _found(ALREADY_COUNTED + [GROUP, CHANNEL], CHARITY_NAMES)
    assert len(two) == 2
    assert len(four) == 4


@pytest.mark.parametrize("url", [
    "https://a-video-platform.example/@invented-charity/videos",
    "https://a-video-platform.example/c/InventedCharity/about",
    "https://a-professional-network.example/company/invented-charity/people",
    "https://a-social-platform.example/groups/invented-charity/posts",
])
def test_a_view_of_an_account_is_still_that_account(url):
    """The regex that failed was anchored at the end of the handle. Which tab
    of an account a link opens says nothing about whose account it is."""
    assert _found([url], CHARITY_NAMES)


# --------------------------------------------------------------------------
# The rule, stated as what it accepts and what it refuses
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    # A role word says an account follows, and anybody can read it without
    # being told which platform this is.
    ("https://a-platform.example/company/invented-footwear", "role"),
    ("https://a-platform.example/user/invented-footwear", "role"),
    ("https://a-platform.example/seller/invented-footwear", "role"),
    ("https://a-platform.example/@invented-footwear", "role"),
    # A bare name at the root of a host is a handle, and whether that is an
    # account depends on whether the host hands its root out to accounts.
    ("https://a-platform.example/invented-footwear", "handle"),
    # Content inside an account, not the account.
    ("https://a-platform.example/invented-footwear/posts/9184", ""),
    ("https://a-platform.example/blog/how-we-make-shoes", ""),
    ("https://a-platform.example/2024/03/01/a-story", ""),
    # The platform's own pages sit in the same namespace its accounts do.
    ("https://a-platform.example/pricing", ""),
    ("https://a-platform.example/search", ""),
    # A number at the root of a site is an article or an order. Behind a role
    # word it is the account identifier platforms without handles hand out.
    ("https://a-platform.example/48213", ""),
    ("https://a-platform.example/u/48213", "role"),
])
def test_what_a_path_says_about_whether_it_names_an_account(url, expected):
    kind, _handle = _account_shape(url)
    assert kind == expected


def test_a_platforms_own_front_page_is_not_a_profile_on_it():
    """A "powered by" credit, a payment badge, a footer logo. The address has
    nothing after the slash, so it is the operator's front door - and every one
    of the four wrong entries that took one shop's count from 3 to 8 was
    exactly that."""
    assert _found(["https://a-platform.example",
                   "https://a-platform.example/"]) == []


def test_a_share_widget_is_not_a_profile():
    """A share button points at a social platform and is not an account on it.
    Counting them inflated corroboration on every site carrying one."""
    assert _found(["https://a-social-platform.example/sharer/sharer.php?u=x",
                   "https://a-microblog.example/intent/tweet?url=x"]) == []


def test_a_named_individuals_page_is_not_the_organisations_account():
    """A customer story linking an employee's page put that person's account
    into the company's own corroboration count. The role word says who it
    belongs to, and it is not the organisation."""
    assert _found(["https://a-professional-network.example/in/some-person",
                   "https://a-magazine.example/author/some-person"]) == []


def test_a_bare_name_on_an_unlisted_host_needs_to_name_this_site():
    """The loosest case, and the one that would count everybody's pages. On a
    host this audit cannot name, the first path segment is a section of that
    site - a price list, a catalogue - not somebody's handle. It counts when it
    spells the audited site's own name, which is how a storefront on a
    marketplace nobody has listed is found without listing the marketplace."""
    assert _found(["https://a-marketplace.example/invented-footwear/"])
    assert _found(["https://a-marketplace.example/catalogue/"]) == []
    assert _found(["https://a-marketplace.example/some-other-brand/"]) == []


def test_the_site_name_test_can_only_keep_and_never_drop():
    """A handle is a name somebody chose, so a mismatch proves nothing. A
    version of this that applied a name test to every platform turned "links to
    only 4 off-site profiles" into "links to no off-site profile at all" at
    high severity, on a language's own site whose four accounts are all real
    and none of whose handles spells its name."""
    unlike_the_site = "https://a-platform.example/company/a-quite-different-name"
    assert _found([unlike_the_site]) == _found([unlike_the_site], [])
    assert _found([unlike_the_site])


# --------------------------------------------------------------------------
# The platform table: a supplement, and never the rule
# --------------------------------------------------------------------------

def test_no_host_needs_to_be_in_the_table_to_be_counted():
    """The defect the table was. Every address in this file that counts does so
    on a host the table has never heard of."""
    for url in STOREFRONTS + [GROUP, CHANNEL]:
        assert _platform_name(url) == "", "the fixtures must not lean on the table"
    assert len(_found(STOREFRONTS + [GROUP, CHANNEL], SITE_NAMES + CHARITY_NAMES)) == 5


def test_an_unnamed_host_is_keyed_by_its_own_name():
    """A platform with no entry in the table still has to be countable and
    still has to be printable, so the host stands in for the name."""
    keys = _found([GROUP], CHARITY_NAMES)
    assert keys == ["a-social-platform.example"]


# The exact strings three readers downstream key off: the corroboration
# check's list of platforms whose entry is authoritative, its list of platforms
# whose 404 means the profile is dead, and the one platform whose URL states
# its own subject rather than a handle somebody chose. A renamed value here is
# a silently dropped reader there.
KEYED_ON_DOWNSTREAM = (
    "LinkedIn", "Wikipedia", "Wikidata", "Crunchbase", "GitHub",
    "Google Business", "Trustpilot", "Yelp", "Glassdoor", "X", "YouTube",
)


@pytest.mark.parametrize("name", KEYED_ON_DOWNSTREAM)
def test_every_name_another_skill_keys_off_is_still_produced(name):
    """Two entries in this table named neither a host nor a host suffix, so
    once the substring test behind them was replaced by a host test they could
    never match again - a dead line reading as a live one."""
    assert name in set(SOCIAL_PLATFORMS.values())


def test_a_platform_section_is_matched_by_its_path_and_not_by_a_substring():
    """`?to=<a review site>` inside a share link contains that review site's
    name, and a blog on a `.page` address contains a mapping service's host.
    Both were recorded as brands' own profiles while the test was a substring
    one."""
    for domain in SOCIAL_PLATFORMS:
        host, _, prefix = domain.partition("/")
        assert _platform_name("https://" + host + "/" + prefix + "/a-handle")
        if prefix:
            assert _platform_name("https://" + host + "/something-else/a-handle") == ""


# --------------------------------------------------------------------------
# Repository hosts, where the account is the first segment
# --------------------------------------------------------------------------

def test_a_repository_owner_counts_only_when_the_site_links_it_twice():
    """A brand's own organisation is linked from the docs, the SDK and the
    command-line tool; a dependency or a credit is linked once. A single link
    to `<host>/<somebody-else>/<library>` is a credit, and counting it put a
    stranger's account into a brand's corroboration total."""
    one = ["https://gitlab.com/somebody-else/a-library"]
    two = ["https://gitlab.com/invented-footwear/first-project",
           "https://gitlab.com/invented-footwear/second-project"]
    assert _profiles_among(one, SITE_NAMES) == []
    owners = {url for _key, url in _profiles_among(two, SITE_NAMES)}
    assert owners == {"https://gitlab.com/invented-footwear"}


# --------------------------------------------------------------------------
# The honest limit
# --------------------------------------------------------------------------

def test_somebody_elses_account_is_recorded_here_and_rejected_later():
    """The cost of a rule general enough to see every platform: a role word and
    a handle on a host nobody listed is an account, and this stage cannot know
    whose. It must not guess - the brand name does not exist until the crawl
    finishes - so it records the candidate and `freshness-corroboration-audit`
    attributes it against every name form the crawl found, the site's own
    `sameAs`, and where the link sits on the page.

    This test exists so that the separation is a decision on the record rather
    than an oversight, and so that anybody moving attribution into this file
    has to delete a test that says why it is not here."""
    stranger = "https://a-directory.example/company/an-entirely-different-firm"
    assert _found([stranger]), "shape is all this stage can read"
