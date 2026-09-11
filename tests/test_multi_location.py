"""A chain is an organisation that has branches, not a confused one.

A restaurant group with hundreds of restaurants declares one `LocalBusiness`
node per restaurant, each with its own address, telephone and name. That is
correct schema.org, and it is what a customer searching for their nearest
branch depends on. Read as facts about a single organisation, the natural
differences between branches looked like the site contradicting itself, and
the report told marketing to spend a day removing them.
"""

from __future__ import annotations

import importlib.util
import os
import sys


from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import declared_locations, is_multi_location  # noqa: E402
from crawl import detect_brand  # noqa: E402


def _branch(city, postcode, phone="+1 555 0000", name=None):
    return {"url": "https://example.test/l/" + city.lower(), "status": 200,
            "page_type": "location", "title": "", "og": {}, "jsonld": [{
                "@type": "Restaurant",
                "name": name or "Example {}".format(city),
                "telephone": phone,
                "address": {"@type": "PostalAddress", "addressLocality": city,
                            "postalCode": postcode}}]}


HOME = {"url": "https://example.test/", "status": 200, "page_type": "home",
        "title": "Example Shack", "og": {}, "jsonld": []}


def test_several_branches_read_as_several_places():
    pages = [HOME, _branch("NYC", "11368"), _branch("Dubai", "00000"),
             _branch("York", "YO1 9TT")]
    assert len(declared_locations({"pages": pages})) == 3
    assert is_multi_location({"pages": pages}) is True


def test_fewer_than_two_places_is_not_a_chain():
    assert is_multi_location({"pages": [HOME, _branch("York", "YO1 9TT")]}) is False
    assert is_multi_location({"pages": [HOME]}) is False


def test_branch_names_are_not_spellings_of_the_brand():
    """"written 13 different ways" - they were thirteen restaurants."""
    pages = [HOME,
             _branch("NYC", "11368", name="Citi Field, NYC"),
             _branch("Dubai", "00000", name="Dubai, Mall of the Emirates"),
             _branch("York", "YO1 9TT", name="York, Coppergate")]
    brand = detect_brand(pages, "https://example.test")
    assert brand["authoritative_variants"] == [], (
        "branch names must not be collected as variants of the brand name")

    # A one-site business declaring LocalBusiness is declaring its own name,
    # so the fix must not throw that away too.
    one_place = [HOME, _branch("York", "YO1 9TT", name="Coppergate Ceramics")]
    assert "Coppergate Ceramics" in detect_brand(
        one_place, "https://example.test")["authoritative_variants"]


def _freshness():
    path = os.path.join(ROOT, "skills", "freshness-corroboration-audit", "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("freshness_for_branch_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_branches_with_different_phone_numbers_do_not_contradict_each_other():
    """The finding was high severity and second in what to fix first.

    Its advice - force every location onto one canonical address and phone -
    would delete the real addresses customers use to find their nearest branch.
    """
    from audit_common import SkillResult
    freshness = _freshness()
    pages = [HOME,
             _branch("NYC", "11368", phone="+1 718 555 0100"),
             _branch("Dubai", "00000", phone="+971 4 419 0510"),
             _branch("York", "YO1 9TT", phone="+44 1904 555812")]
    result = SkillResult("freshness-corroboration-audit")
    freshness._check_fact_consistency(result, {"pages": pages}, pages)
    causes = {f["root_cause"] for f in result.findings}
    assert "fact-inconsistency" not in causes and not result.findings, (
        "a chain's per-branch contact details are not a contradiction")


def test_one_organisation_declaring_two_phone_numbers_still_contradicts_itself():
    """The fix must not switch the check off for sites that are one place."""
    from audit_common import SkillResult
    freshness = _freshness()

    def org(url, phone):
        return {"url": url, "status": 200, "page_type": "home", "contact_facts": {},
                "jsonld": [{"@type": "Organization", "name": "Example",
                            "telephone": phone}]}

    pages = [org("https://example.test/", "+44 1904 555812"),
             org("https://example.test/contact", "+44 1904 999000")]
    result = SkillResult("freshness-corroboration-audit")
    freshness._check_fact_consistency(result, {"pages": pages}, pages)
    assert result.findings, "two primary numbers for one organisation is still a conflict"


# --------------------------------------------------------------------------
# A chain that declares no markup at all, recognised from its addresses
# --------------------------------------------------------------------------

from audit_common import pages_with_their_own_address  # noqa: E402


def _text_branch(i, street, postcode):
    return {"url": "https://x.test/restaurants/branch{}".format(i), "status": 200,
            "page_type": "other", "jsonld_types": [],
            "contact_facts": {"street_hint": street, "postcode_hint": postcode,
                              "declared_phones": ["+441000000{}".format(i)]}}


TEXT_CHAIN = {"origin": "https://x.test", "site": "x.test",
              "brand": {"name": "Example"},
              "pages": [_text_branch(1, "51 Berwick Street", "W1F 8SJ"),
                        _text_branch(2, "12 Park Row", "LS1 5HD"),
                        _text_branch(3, "9 Union Street", "BS1 5EF"),
                        _text_branch(4, "9 Union Street", "BS1 5EF")]}


def test_a_chain_is_recognised_from_its_addresses_not_its_url_slugs():
    """A pizza chain kept its branches at /restaurants/<slug>, printed each
    address in plain text, and declared no place markup at all - so the slug
    signal missed it and the markup signal could not fire, because the markup
    that would have identified the chain is the markup the chain is missing.
    The bug and the defect were the same fact.
    """
    assert is_multi_location(TEXT_CHAIN)
    assert len(pages_with_their_own_address(TEXT_CHAIN)) == 3   # the duplicate folds

    single = {"origin": "https://x.test", "site": "x.test",
              "pages": [_text_branch(1, "41 Walmgate", "YO1 9TT")]}
    assert not is_multi_location(single)
    assert len(pages_with_their_own_address(single)) == 1


def test_the_same_address_written_three_ways_is_one_place():
    """The street hint is a fuzzy substring match, so one head-office address
    came out three slightly different ways across three pages of a
    single-location fixture and was counted as three branches. A postal code
    identifies a place; the street only describes it."""
    pages = [{"url": "https://x.test/{}".format(i), "status": 200,
              "contact_facts": {"street_hint": street, "postcode_hint": "97204"}}
             for i, street in enumerate(("418 Harbour Street",
                                         "418 Harbour Street, Portland",
                                         "418 Harbour Street Portland OR"))]
    snapshot = {"origin": "https://x.test", "site": "x.test", "pages": pages}
    assert len(pages_with_their_own_address(snapshot)) == 1
    assert not is_multi_location(snapshot)
