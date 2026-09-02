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

import pytest

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


def test_one_branch_is_not_a_chain():
    pages = [HOME, _branch("York", "YO1 9TT")]
    assert is_multi_location({"pages": pages}) is False


def test_a_site_with_no_places_is_not_a_chain():
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


def test_a_single_locations_declared_name_is_still_the_brand():
    """A one-site business declaring LocalBusiness is declaring its own name."""
    pages = [HOME, _branch("York", "YO1 9TT", name="Coppergate Ceramics")]
    brand = detect_brand(pages, "https://example.test")
    assert "Coppergate Ceramics" in brand["authoritative_variants"]


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
