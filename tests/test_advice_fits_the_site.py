# -*- coding: utf-8 -*-
"""Advice written for a shop, given to something that is not one.

Two skills asked every site the same four questions and handed every site the
same fixes, and the fixes were written for a company with premises, customers
and a marketing department. Measured on unseen sites:

  a command-line JSON       "The site never states its founding facts in plain
  processor                 text", fixed by "Add a short paragraph on the about
                            page: `<Brand>` was founded in `<year>` in
                            `<place>`." It is a program. It has a repository, a
                            licence and a manual, and it was not founded
                            anywhere.

  the same program          "The site never states its location or service
                            area", fixed by "`<Brand>` serves customers across
                            `<regions>`." It has no premises and no customers.

  a national weather        told to claim a company page on a professional
  service                   network and a profile on a startup-funding database

The cause: the fix text was not conditioned on what kind of thing the site is.

`site_kind` in `audit_common.py` answers that question from the site's own
structure, and `might_be` is how these two skills read it - True for every site
it cannot place and every weak determination, so nothing is suppressed on a
guess. These tests hold both halves: the advice goes quiet where the structure
says it does not apply, and it fires exactly as it did before everywhere else.

Every host here is invented and every address is fictional.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import site_kind, SkillResult  # noqa: E402


def _module(skill):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(
        "advice_" + skill.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACTS = _module("fact-extractability-audit")
SD = _module("structured-data-audit")


def _page(url, page_type="other", text="", external=(), **extra):
    page = {
        "url": url,
        "page_type": page_type,
        "body_text": text,
        "jsonld_types": [],
        "jsonld": [],
        "links": {"internal": [], "external": [{"url": u} for u in external]},
    }
    page.update(extra)
    return page


def _snapshot(origin, pages):
    return {"origin": origin, "site": origin.split("//")[-1], "pages": list(pages)}


# A program: a repository, a licence, a manual, and none of the things a
# company has. This is the site that was told to publish a founding year in a
# place and a service area.
def _a_program():
    return _snapshot("https://jqlike.example", [
        _page("https://jqlike.example/", "home",
              text="A lightweight command-line JSON processor. "
                   "Licensed under the Apache License.",
              external=["https://github.com/invented-owner/jqlike"]),
        _page("https://jqlike.example/manual", "documentation",
              text="Filters read a stream and write a stream."),
        _page("https://jqlike.example/tutorial", "documentation",
              text="Start with the identity filter."),
    ])


# A site with one plain page and nothing decisive about it. The classifier
# says so, and every gate below has to leave it alone.
def _unplaceable():
    return _snapshot("https://quiet.example", [
        _page("https://quiet.example/", "home",
              text="Quiet Ltd helps teams plan their week. "
                   "Read about what we do and how it works."),
    ])


def _core_facts(snapshot):
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, snapshot, snapshot["pages"], "Quiet")
    return result


def _titles(result):
    return " | ".join(f["title"] for f in result.findings)


def _declined(result, check):
    return next((n["reason"] for n in result.not_applicable if n["check"] == check), "")


# --------------------------------------------------------------------------
# The expectation of a fact, gated on what the site is
# --------------------------------------------------------------------------

def test_a_program_is_not_told_to_publish_a_founding_year_in_a_place():
    result = _core_facts(_a_program())
    assert "founding facts" not in _titles(result)
    reason = _declined(result, "core-fact-founding-facts")
    assert reason, "the check went quiet without saying why"
    # What was concluded, and the evidence it was concluded from - both, in the
    # sentence the report's appendix prints.
    assert "software project" in reason
    assert "repository" in reason and "licen" in reason


def test_a_program_is_not_asked_where_it_is_or_who_it_serves():
    result = _core_facts(_a_program())
    assert "location or service area" not in _titles(result)
    reason = _declined(result, "core-fact-location-or-service-area")
    assert "premises" in reason and "service area" in reason


def test_a_site_the_classifier_cannot_place_is_asked_for_both_as_before():
    """The safety property. A wrong guess is what produced the failures above,
    so no determination means no gating at all."""
    result = _core_facts(_unplaceable())
    titles = _titles(result)
    assert "founding facts" in titles
    assert "location or service area" in titles
    assert not _declined(result, "core-fact-founding-facts")
    assert not _declined(result, "core-fact-location-or-service-area")


def test_an_address_a_program_does_print_is_still_read_and_reported():
    """The gate is on the expectation, never on the observation. A project that
    publishes an address has published one, and the report says so."""
    snapshot = _a_program()
    snapshot["pages"].append(_page(
        "https://jqlike.example/contact", "contact",
        text="Post reaches us at 27 Invented Lane, Ashcombe SE16 4RA.",
        contact_facts={"street_hint": "27 Invented Lane", "postcode_hint": "SE16 4RA",
                       "has_address": True, "emails": ["post@jqlike.example"],
                       "phones": []}))
    result = _core_facts(snapshot)
    printed = _declined(result, "core-facts-present")
    assert "27 Invented Lane" in printed
    assert "Location: https://jqlike.example/contact" in printed
    assert not _declined(result, "core-fact-location-or-service-area")


# --------------------------------------------------------------------------
# Wording, for the facts that stay
# --------------------------------------------------------------------------

def _steps(name, snapshot):
    return FACTS._core_fact_steps(name, "Quiet", True, site_kind(snapshot))


def _a_public_body():
    return _snapshot("https://coastal-warnings.gov.zz", [
        _page("https://coastal-warnings.gov.zz/", "home",
              text="Warnings for the coast, published every hour."),
    ])


def test_a_public_body_is_asked_when_it_was_established_not_who_founded_it():
    """It still has to state the year - the finding is untouched. A body
    created by a law is not founded in a place by anybody, and the wording is
    the difference between a sentence its owner can write and one they cannot."""
    steps = " ".join(_steps("founding facts", _a_public_body()))
    assert "was established in" in steps
    assert "was founded in" not in steps
    # And the report shows its working, so a reader can see it was tailored and
    # on what evidence.
    assert "public body" in steps and "suffix reserved for government" in steps


def test_a_public_body_covers_an_area_and_does_not_serve_customers():
    steps = " ".join(_steps("location or service area", _a_public_body()))
    assert "serves customers across" not in steps
    assert "covers" in steps


def test_an_organisation_is_not_told_it_has_customers():
    organisation = _snapshot("https://society.example", [
        _page("https://society.example/", "home", text="A learned society."),
        _page("https://society.example/jobs", "careers", text="Work with us."),
    ])
    steps = " ".join(_steps("location or service area", organisation))
    assert "serves customers across" not in steps
    assert "works across" in steps


def test_an_unplaceable_site_keeps_the_wording_it_has_today():
    """The safety property: an undetermined site gets the untailored branch.

    The marker used to be the phrase "serves customers across", which is what
    the untailored branch said. It no longer says it - the shop vocabulary was
    reaching sites that are not shops, and the noun came out of this sentence
    too - so the property is asserted against the branch rather than
    against one of its words: neither tailored sentence is the one produced.
    """
    steps = " ".join(_steps("location or service area", _unplaceable()))
    assert "serves `<regions>`" in steps
    assert "covers" not in steps and "works across" not in steps
    assert "This wording is for" not in steps
    founding = " ".join(_steps("founding facts", _unplaceable()))
    assert "was founded in" in founding


def test_a_program_is_not_sent_to_a_press_kit_it_does_not_have():
    """The definition sentence is worth repeating wherever something describes
    the site. Which places those are is a claim about what the site is."""
    tailored = FACTS._repeat_the_definition_step(site_kind(_a_program()))
    assert "LinkedIn" not in tailored and "press kit" not in tailored
    assert "repository summary" in tailored

    unchanged = FACTS._repeat_the_definition_step(site_kind(_unplaceable()))
    assert "LinkedIn" in unchanged and "press kit" in unchanged


# --------------------------------------------------------------------------
# structured-data-audit: one answer to "is this a place you can walk into"
# --------------------------------------------------------------------------

def _campus(host, city, street, postcode):
    return _page("https://{}/campus/{}".format(host, city), "about",
                 text="{}, {} {}".format(street, city, postcode),
                 contact_facts={"street_hint": street, "postcode_hint": postcode,
                                "has_address": True, "declared_phones": []})


def test_an_institution_is_not_handed_a_storefront_as_its_identity():
    """`_identity_type` had its own three-signal rule for "somewhere you can
    go", and a library with opening hours met it. The site-wide block a
    university pastes then declares the whole institution to be one storefront.
    One question, one answer, read from the whole structure of the site."""
    pages = [dict(_page("https://physics.ac.zz/", "home"),
                  jsonld=[{"@type": "Library", "name": "Reading Room"}])]
    institution = _snapshot("https://physics.ac.zz", pages)
    assert SD._identity_type(institution, pages, {"opening_hours": True}) == "Organization"

    # The same markup on a site the classifier does not place as an institution
    # still earns the narrower type, or this would be a blanket change rather
    # than a gate.
    ordinary = _snapshot("https://reading-room.example", pages)
    assert SD._identity_type(ordinary, pages, {"opening_hours": True}) == "LocalBusiness"


def _per_location(snapshot):
    result = SkillResult("structured-data-audit")
    SD._check_per_location_markup(result, snapshot, snapshot["pages"])
    return result


def test_places_that_do_not_trade_are_still_asked_for_markup():
    """The observation holds for every kind of site: a page printing a street
    address and declaring no place markup cannot answer "where" for a machine.
    Only the fix moves."""
    snapshot = _snapshot("https://physics.ac.zz", [
        _campus("physics.ac.zz", "ashcombe", "1 Invented Lane", "AB1 2CD"),
        _campus("physics.ac.zz", "kelvin", "2 Fictional Row", "EF3 4GH"),
        _campus("physics.ac.zz", "harbour", "3 Imagined Way", "IJ5 6KL"),
    ])
    result = _per_location(snapshot)
    assert len(result.findings) == 1, "the finding was suppressed rather than reworded"
    finding = result.findings[0]
    steps = " ".join(finding["suggested_action"]["how_to_fix"])
    assert "Restaurant, Dentist" not in steps
    assert "openingHoursSpecification` only where" in steps
    assert '"@type": "Place"' in finding["suggested_action"]["snippet"]
    assert "phone" not in finding["suggested_action"]["snippet"]


def test_a_site_the_classifier_cannot_place_still_gets_the_branch_wording():
    snapshot = _snapshot("https://quiet.example", [
        _campus("quiet.example", "ashcombe", "1 Invented Lane", "AB1 2CD"),
        _campus("quiet.example", "kelvin", "2 Fictional Row", "EF3 4GH"),
        _campus("quiet.example", "harbour", "3 Imagined Way", "IJ5 6KL"),
    ])
    finding = _per_location(snapshot).findings[0]
    steps = " ".join(finding["suggested_action"]["how_to_fix"])
    assert "Restaurant, Dentist" in steps
    assert "openingHoursSpecification`. Do not reuse" in steps
    assert '"@type": "LocalBusiness"' in finding["suggested_action"]["snippet"]
