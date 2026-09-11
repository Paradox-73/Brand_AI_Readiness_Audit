# -*- coding: utf-8 -*-
"""Whose Wikidata item is it, and what a two-search budget owes the reader.

Two failures on one batch of real sites, both inside `entity-ambiguity`, and
both worse than a miscount because they were stated in the appendix, the part
of the report a reader is most likely to trust.

  the wrong entity claimed    an auto-generated documentation site for an
  as the site's own           unofficial third-party client library was told
                              that the large service the library talks to held
                              "this site's own item", and that there was
                              therefore "no name collision to resolve" - on the
                              one site in the batch whose name genuinely
                              collides with something much bigger. The rule was
                              written down and implemented; the host comparison
                              underneath it was too loose, matching a name-
                              bearing label across two different registered
                              domains.

  a string that is not a      a town's declared name carried its legal
  name, then a shrug          administrative class; the search returned zero
                              and the reason told the reader to "search the
                              name by hand, without any leading article or
                              legal suffix" while a second lookup sat unspent.

The inversion is what makes the first expensive. A site whose name belongs to a
far larger organisation is precisely the site that needs telling, because an
assistant asked about it will answer about the other one. Reported as
reassurance, the check found the collision and hid it.

Every host, name and Q-id below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FRESHNESS = _module("freshness-corroboration-audit", "freshness_for_entity_tests")

# The name is one word and spelt the same by both parties, which is what makes
# this a collision rather than two names that merely resemble each other.
NAME = "gravelmap"
SERVICE_SITE = "https://www.gravelmap.test"
SERVICE_ITEM = "Q7770001"


class _Json:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class _StubWikidata:
    """The search API and the item lookup behind it, with `P856` per item.

    `sites` maps an item id to the official website that item declares. It is
    the whole of what separates "this is your entry" from "this is somebody
    else's entry that happens to be spelt like you".
    """

    def __init__(self, items, sites=None, entities_answer=True):
        self.items = items
        self.sites = sites or {}
        self.entities_answer = entities_answer
        self.urls = []

    def try_get(self, url, **kwargs):
        self.urls.append(url)
        if "wbgetentities" in url:
            if not self.entities_answer:
                return None
            return _Json({"entities": {
                item_id: {"claims": {"P856": [
                    {"mainsnak": {"datavalue": {"value": site}}}]}}
                for item_id, site in self.sites.items()}})
        return _Json({"search": self.items})

    @property
    def searches(self):
        return [u for u in self.urls if "wbsearchentities" in u]


def _item(item_id, label, description="an invented thing", aliases=()):
    return {"id": item_id, "label": label, "description": description,
            "aliases": list(aliases)}


def _run(fetcher, origin, brand=None, profiles=None, pages=None):
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_entity_ambiguity(
        result, {"origin": origin}, pages or [], brand or {"name": NAME},
        profiles or {}, fetcher, True)
    return result


def _reason(result):
    return next(entry["reason"] for entry in result.not_applicable
                if entry["check"] == "entity-ambiguity")


def _finding(result):
    return next(f for f in result.findings if f["root_cause"] == "entity-ambiguity")


# --------------------------------------------------------------------------
# Whose website is whose
# --------------------------------------------------------------------------

@pytest.mark.parametrize("audited,declared,same", [
    # The failure. A project hosted under a documentation platform's registered
    # domain shares nothing with the service whose name it borrowed.
    ("gravelmap.docs-platform.test", "www.gravelmap.test", False),
    ("gravelmap.pages-host.test", "gravelmap.test", False),
    # A subdomain of the same registered domain is the same organisation, and
    # so is a national edition on another registry. Both of these were the
    # reason the loose comparison existed; neither needs it.
    ("docs.gravelmap.test", "www.gravelmap.test", True),
    ("uk.gravelmap.test", "www.gravelmap.test", True),
    ("shop.gravelmap.test", "gravelmap.test", True),
    ("gravelmap.example", "gravelmap.test", True),
    # A two-part registry suffix, `co.uk`-shaped. Spelled with an invented
    # two-letter country and the `ne` second level rather than `co`, because
    # `.co` is a real address and no test here may write one.
    ("gravelmap.ne.zq", "gravelmap.test", True),
    # Two strangers under one platform, and two strangers on one country
    # registry, are not one organisation either.
    ("gravelmap.pages-host.test", "otherbrand.pages-host.test", False),
    ("gravelmap.ne.zq", "otherbrand.ne.zq", False),
    # A tenant label beside an ordinary prefix is still the same organisation:
    # the platform hands out `shop.`, and the brand hands out nothing.
    ("shop.gravelmap.pages-host.test", "gravelmap.pages-host.test", True),
])
def test_one_organisations_addresses_differ_by_subdomain_or_registry_only(
        audited, declared, same):
    assert FRESHNESS._same_organisation_host(declared, audited) is same


def test_the_item_for_a_different_site_is_not_claimed_as_this_sites_own():
    """The sentence a real report printed, and it may not be produced again.

    The check has the right rule - decline unless an item names this site as
    its own website - and applied it to a comparison that answered yes across
    two unrelated registered domains.
    """
    stub = _StubWikidata([_item(SERVICE_ITEM, NAME, "an invented search service")],
                         sites={SERVICE_ITEM: SERVICE_SITE})
    result = _run(stub, "https://{}.docs-platform.test".format(NAME))

    assert result.signals["wikidata_own_entity"] is None
    assert not any("this site's own item" in entry["reason"]
                   for entry in result.not_applicable)
    assert not any("no name collision" in entry["reason"]
                   for entry in result.not_applicable)
    # And nothing downstream may offer that Q-id back as the reader's own.
    for finding in result.findings:
        for step in finding["suggested_action"]["how_to_fix"]:
            assert "your Wikidata item" not in step
            assert SERVICE_ITEM not in step


def test_a_national_edition_still_recognises_its_own_item():
    """The generosity the old comparison was written for, kept.

    A manufacturer's item gives the `.com` address while the site audited is
    the national one. Tightening the comparison must not put that item back
    among the entities colliding with its own name.
    """
    stub = _StubWikidata([_item(SERVICE_ITEM, NAME)], sites={SERVICE_ITEM: SERVICE_SITE})
    result = _run(stub, "https://{}.example".format(NAME))
    assert result.signals["wikidata_own_entity"] == SERVICE_ITEM
    assert "no name collision to resolve" in _reason(result)


# --------------------------------------------------------------------------
# One item that publishes its own address is a collision, not a shrug
# --------------------------------------------------------------------------

def test_a_single_item_naming_another_website_is_reported_rather_than_skipped():
    stub = _StubWikidata([_item(SERVICE_ITEM, NAME, "an invented search service")],
                         sites={SERVICE_ITEM: SERVICE_SITE})
    result = _run(stub, "https://py-client-lib.test")

    finding = _finding(result)
    assert finding["confidence"] == "high"
    # The decisive fact, in the evidence, in terms a reader can check.
    assert "gravelmap.test" in finding["evidence"]
    assert "not the site audited here" in finding["evidence"]
    # And the title counts one thing as one thing.
    assert "entities" not in finding["title"]
    assert result.signals["wikidata_other_entity_sites"] == [
        {"id": SERVICE_ITEM, "label": NAME, "official_website_host": "gravelmap.test"}]


def test_where_this_site_could_be_the_same_brand_hosted_elsewhere_it_says_so():
    """The one shape the addresses cannot decide.

    A brand whose own site sits on a platform subdomain spelling its name,
    while its item gives its own registered domain, looks exactly like a third
    party that named a project after somebody else. The finding is still made -
    silence is the worse failure - and it states the doubt rather than burying
    it in a confidence level nobody reads.
    """
    stub = _StubWikidata([_item(SERVICE_ITEM, NAME, "an invented search service")],
                         sites={SERVICE_ITEM: SERVICE_SITE})
    result = _run(stub, "https://{}.docs-platform.test".format(NAME))

    finding = _finding(result)
    assert finding["confidence"] == "medium"
    assert "cannot decide" in finding["evidence"]


def test_an_item_with_no_declared_website_is_still_only_a_count():
    """`P856` is what turns a shared label into evidence. Without it the
    two-entity threshold is all there is, and one label stays below it."""
    stub = _StubWikidata([_item(SERVICE_ITEM, NAME)], sites={})
    result = _run(stub, "https://py-client-lib.test")

    assert not result.findings
    reason = _reason(result)
    assert "fewer than the 2 entities" in reason


def test_the_fix_step_does_not_offer_a_stranger_s_item_to_link():
    """"Link your existing item" is wrong advice when the item is not theirs,
    and it is the advice a reader is most likely to act on."""
    stub = _StubWikidata([_item(SERVICE_ITEM, NAME, "an invented search service")],
                         sites={SERVICE_ITEM: SERVICE_SITE})
    first_step = _finding(_run(stub, "https://py-client-lib.test"))[
        "suggested_action"]["how_to_fix"][0]
    assert "already exists" not in first_step
    assert "about a different organisation" in first_step


# --------------------------------------------------------------------------
# The second lookup is spent, or the reason says why not
# --------------------------------------------------------------------------

def test_a_leading_administrative_class_comes_off_and_the_second_lookup_runs():
    """The town. The check knew the rule and printed it at the reader."""
    brand = {"name": "Town of Ashgrove, Vershire", "domain_token": "ashgrove"}
    stub = _StubWikidata([])
    result = _run(stub, "https://an-invented-host.test", brand=brand)

    assert result.signals["wikidata_searched_names"] == [
        "Town of Ashgrove, Vershire", "Ashgrove, Vershire"]
    assert len(stub.searches) == 2
    reason = _reason(result)
    assert "Ashgrove, Vershire" in reason
    # The instruction the check can carry out is not printed as homework.
    assert "by hand" not in reason


def test_an_institution_keeps_the_class_that_is_part_of_its_name():
    """"University of <place>" is the name; stripping it searches for the place
    and counts the place's item as a collision with the university on it."""
    brand = {"name": "University of Ashgrove", "domain_token": "ashgrove"}
    assert FRESHNESS._shorter_search_name(brand, brand["name"]) is None


def test_a_page_title_is_named_as_a_title_rather_than_searched_twice():
    """Two separators in one string is not a name anybody registered.

    Guessing which segment is the organisation is the aggression that once
    counted fifteen unrelated encyclopedia articles as one brand's profiles, so
    the second lookup is not spent on a guess - and the reason says that, in
    terms of the string, rather than assigning the reader homework.
    """
    title = u"予防歯科｜アイリス歯科｜KEEP28"
    stub = _StubWikidata([])
    result = _run(stub, "https://an-invented-host.test", brand={"name": title})

    assert len(stub.searches) == 1
    reason = _reason(result)
    assert "page title rather than a name" in reason
    assert "3 separated segments" in reason
    assert "by hand" not in reason


def test_a_name_the_site_publishes_is_searched_even_where_words_are_unspaced():
    """The word-count rule that rejected every candidate in an unspaced script.

    Japanese, Chinese and Thai write no spaces, so a segment of a declared name
    and the whole declared name both have exactly one "word" - and the guard
    meant to stop a domain suffix being searched threw away a name the site
    publishes in its own markup.
    """
    title = u"予防歯科｜アイリス歯科｜KEEP28"
    segment = u"アイリス歯科"
    brand = {"name": title, "alternate_names": [segment]}
    assert FRESHNESS._shorter_search_name(brand, title) == segment

    stub = _StubWikidata([_item("Q7770009", segment, "an invented clinic")])
    result = _run(stub, "https://an-invented-host.test", brand=brand)
    assert result.signals["wikidata_searched_names"] == [title, segment]
    # And the reason names the form the match is actually on, not the title.
    assert segment in _reason(result)


def test_a_domain_suffix_is_still_not_a_shorter_way_of_writing_the_name():
    """The failure in the other direction, kept closed.

    A host whose declared name ends in a dot and a short suffix is not a brand
    with a shorter trading name; searching the stripped token is how one was
    told eight entities shared its name, the eight being an insect order, a
    housefly, a country album and a pop single.
    """
    brand = {"name": "Norhaven.dev", "authoritative_variants": ["Norhaven"]}
    assert FRESHNESS._shorter_search_name(brand, "Norhaven.dev") is None


def test_no_derivable_form_is_a_stated_limit_not_an_instruction():
    stub = _StubWikidata([])
    result = _run(stub, "https://an-invented-host.test",
                  brand={"name": "Norhaven Tessellate"})
    assert len(stub.searches) == 1
    reason = _reason(result)
    assert "the second lookup was not spent" in reason
    assert "no entity at all" in reason
    assert "no name collision" not in reason


# --------------------------------------------------------------------------
# The budget, unchanged
# --------------------------------------------------------------------------

def test_the_wikidata_half_of_this_skill_never_exceeds_three_requests():
    """Two searches and one batch item lookup, whatever the site looks like."""
    brand = {"name": "Town of Ashgrove, Vershire", "domain_token": "ashgrove"}
    stub = _StubWikidata(
        [_item("Q777001{}".format(i), "Ashgrove, Vershire") for i in range(6)],
        sites={"Q7770010": "https://ashgrove-vt.test"})
    _run(stub, "https://an-invented-host.test", brand=brand)
    assert len(stub.urls) == 3
    assert len(stub.searches) == 2
