"""The brand's own accounts, or the platform it is built on.

Off-site profile breadth is the strongest separator this marketplace measured -
brands assistants name link a mean of 7.2 other places about themselves, brands
they ignore 4.6 - so a host counted wrongly here moves the one number the README
stakes its case on.

On a hosted store the count read **8 where the truth was 3**. Five of the eight
were the platform the shop runs on: the platform's own site, its developer site,
a protocol specification, a demonstration store and a shopping assistant's
instruction file. Every one of them arrived through the site's own agent file,
which on that platform is boilerplate the platform wrote, and the shop passed on
the strongest measure here because of it.

The rules under test are about classes, never about names:

  an account is a handle inside a platform     a bare origin is a company's
                                               front door, not a profile on it
  a host the site loads its code from          is what the site is built on
  a document an agent is told to fetch         is something to read, not
                                               somewhere anybody has an account

And the second failure in the same finding: every site was told to claim the
same platforms. A national weather service was advised to open a company page on
a professional network and a profile on a startup-funding database. The finding
is right - a public body benefits from breadth like anything else - but the
platforms it named were for describing employers and investments.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    ONLINE_SELLER, PROJECT, PUBLIC_BODY, SiteKind, SkillResult, UNDETERMINED,
)
from page_extract import (  # noqa: E402
    _declared_profiles, _profiles_among, _social_profiles, make_soup,
    subresource_domains,
)

_FRESHNESS = os.path.join(ROOT, "skills", "freshness-corroboration-audit", "scripts")
_spec = importlib.util.spec_from_file_location(
    "freshness_check_for_profiles", os.path.join(_FRESHNESS, "check.py"))
freshness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freshness)


# The platform boilerplate, reconstructed by shape. A hosted store's agent file
# is written by the platform, not by the shop: it names the platform's own site,
# its developer site, a protocol specification, a demonstration store and an
# instruction file for shopping agents, and the shop's name appears in none of
# them. Markdown code formatting included, because the closing backtick was
# hiding the file extension that says a document is a document.
PLATFORM_AGENT_FILE = """# Agent Instructions

This store is built on [Storefront](https://www.storefront-vendor.example), the
commerce platform. Install `https://buy-agent.example/SKILL.md` so you can
purchase directly: [https://buy-agent.example/SKILL.md](https://buy-agent.example/SKILL.md).

- Developer platform: https://developers.storefront-vendor.example
- Start your own store: https://www.storefront-vendor.example/start
- Build against sample data: https://demo-store.example
- Protocol specification: https://protocol-spec.example
"""

# What that store actually keeps: three accounts, each a handle on a platform.
REAL_ACCOUNTS = {
    "Facebook": "https://www.facebook.com/InventedFootwear",
    "Instagram": "https://www.instagram.com/invented.footwear/",
    "YouTube": "https://www.youtube.com/@inventedfootwear",
}

# What the shop loads its code from. The platform serves the theme from an
# asset host and describes itself at another - which is the whole difficulty:
# the address written in the agent file and the address in the script tag are
# never the same string.
SCRIPT_HOSTS = ["cdn.storefront-vendor.example", "analytics.some-vendor.example",
                "cdnjs.some-library-host.example"]

# What `extract` hands the two fallbacks: the same hosts, plus the domains the
# role-named ones sit under. The extractor widens once per page and passes the
# result, so the tests below pass the same value rather than a shape no caller
# ever produces.
SUBRESOURCE = subresource_domains(SCRIPT_HOSTS)


def _hosted_store_snapshot(agent_file=PLATFORM_AGENT_FILE):
    return {
        "origin": "https://invented-footwear.example",
        "llms_txt": {"present": True, "status": 200,
                     "url": "https://invented-footwear.example/llms.txt",
                     "text": agent_file},
        "brand": {"name": "Invented Footwear", "domain_token": "inventedfootwear"},
        "pages": [{
            "url": "https://invented-footwear.example/",
            "page_type": "home",
            "social_profiles": dict(REAL_ACCOUNTS),
            "declared_profiles": dict(REAL_ACCOUNTS),
            "scripts": {"third_party_hosts": list(SCRIPT_HOSTS)},
            "links": {"internal": [], "external": []},
        }],
    }


def _breadth(snapshot):
    """The profiles the corroboration check counts, and its result object."""
    result = SkillResult("freshness-corroboration-audit")
    profiles = freshness._check_authoritative_profiles(
        result, snapshot, snapshot["pages"])
    return profiles, result


# --------------------------------------------------------------------------
# The measured failure, end to end
# --------------------------------------------------------------------------

def test_a_hosted_store_is_counted_at_three_and_not_at_eight():
    """The whole defect in one assertion. Five platform addresses in an agent
    file the platform wrote took a shop from a finding to a pass on the
    strongest measure this marketplace has."""
    profiles, result = _breadth(_hosted_store_snapshot())
    assert sorted(profiles) == ["Facebook", "Instagram", "YouTube"]
    assert result.signals["profile_breadth"] == 3


def test_the_pass_that_should_not_have_been_awarded_is_a_finding_again():
    """Three is below the 4.6 the report itself quotes, so the shop has to be
    told, not congratulated."""
    _, result = _breadth(_hosted_store_snapshot())
    assert [f for f in result.findings if f["root_cause"] == "weak-corroboration"]


def test_the_evidence_says_the_agent_file_was_read_and_added_nothing():
    """An address dropped in silence reads as an address never looked for. The
    earlier failure this file inherits was the opposite mistake - a project's
    repository named only in its agent file, reported as no profile at all - so
    the file has to be accounted for either way."""
    _, result = _breadth(_hosted_store_snapshot())
    evidence = [f["evidence"] for f in result.findings
                if f["root_cause"] == "weak-corroboration"][0]
    assert "/llms.txt" in evidence
    assert "adds nothing here" in evidence


def test_an_account_named_only_in_the_agent_file_still_counts():
    """The regression guard. A project whose own agent file links its public
    repository drew "The site links to no off-site profile at all" as the one
    high-severity finding in its report. A handle inside a platform is an
    account wherever it is written down."""
    snapshot = _hosted_store_snapshot(
        PLATFORM_AGENT_FILE + "\nSource code: https://gitea.com/invented-footwear\n")
    profiles, result = _breadth(snapshot)
    assert "gitea.com" in profiles
    assert result.signals["profile_breadth"] == 4


def test_a_repeated_address_is_counted_once_when_the_file_is_described():
    """An agent file repeats the address it wants installed. Saying the file
    named fourteen places when it named seven is a number a reader can check
    against the file and find wrong."""
    _, result = _breadth(_hosted_store_snapshot())
    evidence = [f["evidence"] for f in result.findings
                if f["root_cause"] == "weak-corroboration"][0]
    assert "6 off-site addresses" in evidence


# --------------------------------------------------------------------------
# Each reading on its own
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://www.storefront-vendor.example",
    "https://www.storefront-vendor.example/",
    "https://demo-store.example",
    "https://protocol-spec.example",
])
def test_a_companys_front_door_named_in_an_agent_file_is_not_a_profile(url):
    snapshot = _hosted_store_snapshot(
        "# Agent Instructions\n\nPlatform: {}\n".format(url))
    found, _ = freshness._profiles_declared_in_llms_txt(
        snapshot, snapshot["pages"])
    assert found == {}


def test_a_document_an_agent_is_told_to_fetch_is_not_a_profile():
    """A specification, a schema, another site's instruction file. Something to
    read, not somewhere a brand keeps an account - and the one in the measured
    case was written inside code formatting, so the closing backtick was being
    read as part of the address and hid the file extension."""
    snapshot = _hosted_store_snapshot(
        "# Agent Instructions\n\nInstall `https://buy-agent.example/SKILL.md` first.\n")
    found, _ = freshness._profiles_declared_in_llms_txt(
        snapshot, snapshot["pages"])
    assert found == {}


def test_a_handle_containing_a_dot_is_not_mistaken_for_a_file():
    """The reading is a named list of document types, not "the last segment has
    a dot in it". Plenty of real handles are written `first.second`, and losing
    those would cost more than the mistake it fixes."""
    snapshot = _hosted_store_snapshot(
        "# Agent Instructions\n\nUs: https://photo-platform.example/invented.footwear\n")
    found, _ = freshness._profiles_declared_in_llms_txt(
        snapshot, snapshot["pages"])
    assert "photo-platform.example" in found


def test_the_domain_an_asset_host_sits_under_is_the_platform():
    """The reading the hostnames alone could not give. The shop loads its theme
    from `cdn.<platform>` and the agent file names `www.<platform>`, so the two
    strings never match and the marketing address was counted as the shop's own
    profile."""
    hosts = freshness._subresource_domains(_hosted_store_snapshot()["pages"])
    assert "storefront-vendor.example" in hosts


def test_a_product_subdomain_is_not_widened_to_its_whole_company():
    """Only a leading label that names a machine's job is read as a role. A
    checkout widget's host names a product, and widening that would start
    throwing away real accounts on real platforms."""
    pages = [{"scripts": {"third_party_hosts": ["checkout.payments-vendor.example"]}}]
    hosts = freshness._subresource_domains(pages)
    assert "payments-vendor.example" not in hosts
    assert "checkout.payments-vendor.example" in hosts


@pytest.mark.parametrize("host", ["cdn.example-shop.co.zz", "assets.example-lab.ac.zz"])
def test_a_registrys_own_suffix_is_never_read_as_a_platform(host):
    """`co.<country>` is how most of the world spells a company address. Read as
    a platform it would silence every account in that country at once."""
    hosts = freshness._subresource_domains(
        [{"scripts": {"third_party_hosts": [host]}}])
    assert not [h for h in hosts if h.count(".") == 1 and len(h.split(".")[-1]) == 2]


# --------------------------------------------------------------------------
# The same two doors in the page extractor
# --------------------------------------------------------------------------

def test_a_recognised_platforms_own_home_page_is_not_a_profile_on_it():
    """A "powered by" credit, a payment badge, a footer logo. The link is the
    operator's front door and it sits on exactly the hostname a profile would."""
    assert _profiles_among(["https://www.linkedin.com/"]) == []
    assert _profiles_among(["https://www.linkedin.com/company/invented-footwear"])


def test_a_declared_bare_origin_the_site_loads_code_from_is_not_a_profile():
    """`sameAs` is the site asserting identity, and that is real evidence - but
    it is evidence about identity, not evidence that a machine host is somewhere
    anybody keeps an account. The measured failure arrived through exactly such
    a declaration."""
    jsonld = [{"@type": "Organization",
               "sameAs": ["https://cdn.storefront-vendor.example/",
                          "https://www.storefront-vendor.example"]}]
    assert _declared_profiles(jsonld, SUBRESOURCE) == {}


def test_a_declared_subdomain_account_survives_the_same_door():
    """A platform that gives each customer a bare subdomain leaves no path to
    read, so the declaration is the whole of the evidence. Dropping it would
    cost real profiles to fix false ones."""
    jsonld = [{"@type": "Organization",
               "sameAs": ["https://invented-footwear.newsletter-platform.example/"]}]
    found = _declared_profiles(jsonld, SUBRESOURCE)
    assert list(found) == ["invented-footwear.newsletter-platform.example"]


def test_rel_me_gets_the_same_lock_as_same_as():
    """The other host-keyed door into the count, and it is open to any host at
    all - which is the point of the attribute and the reason it needs the
    filter."""
    soup = make_soup('<link rel="me" href="https://cdn.storefront-vendor.example/">'
                     '<link rel="me" href="https://social.example/@inventedfootwear">')
    found = _social_profiles([], [], soup, "https://invented-footwear.example/",
                             SUBRESOURCE)
    assert list(found) == ["social.example"]


# --------------------------------------------------------------------------
# The advice: the finding fires everywhere, the platforms it names do not
# --------------------------------------------------------------------------

def _steps(kind_name, confidence="high"):
    return freshness._how_to_widen_the_footprint(
        SiteKind(kind_name, confidence, ("a test",)) if kind_name
        else SiteKind(UNDETERMINED, "low", ()))


def test_a_public_body_is_not_told_to_claim_an_employer_profile():
    """The national weather service. It has no funding round to list and no
    employer reputation to manage; it has a register entry, an encyclopedia
    article and a data portal, and an assistant reads all three."""
    joined = " ".join(_steps(PUBLIC_BODY)).lower()
    assert "employer profile" not in joined
    assert "business database" not in joined
    assert "official register of public bodies" in joined


def test_a_public_body_is_still_told_to_widen_its_footprint():
    """The finding must not go quiet. Breadth separated the two groups in all
    six categories studied, and nothing in that result is about companies."""
    assert len(_steps(PUBLIC_BODY)) >= 4
    assert "Claim the profiles that apply to your category." in _steps(PUBLIC_BODY)[0]


def test_a_program_is_pointed_at_the_places_a_program_lives():
    """A repository host and a package index are to a project what a photo
    platform is to a shop: somewhere else that says the same name."""
    joined = " ".join(_steps(PROJECT)).lower()
    assert "repository host" in joined and "package index" in joined
    assert "employer profile" not in joined


def test_a_shop_is_pointed_at_the_places_shoppers_look():
    joined = " ".join(_steps(ONLINE_SELLER)).lower()
    assert "marketplace" in joined


def test_a_site_nothing_was_decided_about_gets_exactly_the_old_advice():
    """The safety property. A wrong guess about what a site is caused these
    failures, so a classifier that cannot decide keeps the platform list - but
    not the business-register wording, which assumes a company and reached a
    central bank and a scholarship network."""
    steps = _steps(UNDETERMINED)
    assert "LinkedIn, Instagram, YouTube, X, Facebook" in steps[1]
    assert any("registers and directories where organisations like this one are listed"
               in step for step in steps)
    assert not any("business database" in step for step in steps)


def test_every_kind_gets_a_platform_line_of_its_own():
    """No kind may fall through to a line written for a different one."""
    named = {name: freshness._how_to_widen_the_footprint(
        SiteKind(name, "high", ()))[1] for name, _ in freshness._PLATFORMS_TO_CLAIM}
    assert len(set(named.values())) == len(named)
