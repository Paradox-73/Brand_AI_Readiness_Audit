# -*- coding: utf-8 -*-
"""Which off-site accounts count as the brand's, and what a lookup may conclude.

Off-site profile breadth is the strongest signal this marketplace measures, so
a wrong count here moves the headline. `test_profile_links.py` holds the other
question - whether a profile the site publishes still leads anywhere. This file
holds whose account it is, and what a Wikidata search is entitled to say.

The count was wrong in both directions on real sites:

  "Distinct off-site profiles linked:   two of the four were a contributor's
  Bluesky, GitHub, Wikipedia, X"        personal account and an encyclopedia
                                        article about a computer-science term,
                                        while the project's own organisation
                                        page - linked dozens of times - was
                                        never counted at all
  a name test applied to every          "links to only 4 off-site profiles"
  platform                              became "links to no off-site profile at
                                        all", at high severity, on a language's
                                        own site, because none of its LinkedIn,
                                        X or YouTube handles spells its name
  a `rel="me"` Mastodon link            invisible to every source the finding
                                        named, and Mastodon has no fixed domain
                                        for a platform list to hold
  `/llms.txt`, fetched and recorded     "The site links to no off-site profile
                                        at all" as the only high-severity
                                        finding, over a file this audit had
                                        fetched that links the project's public
                                        repository

And the Wikidata verdicts contradicted their own clauses: a reason naming one
other entity while concluding none exists, and zero search results reported as
a fact about the world rather than about the name string the audit was handed.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    SkillResult, make_soup, profile_is_opaque, profile_names_brand,
    profiles_naming_brand,
)
from page_extract import _social_profiles  # noqa: E402


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FRESHNESS = _module("freshness-corroboration-audit", "freshness_for_profile_tests")

ORIGIN = "https://an-invented-host.test"


def _reason(result, check):
    return next(entry["reason"] for entry in result.not_applicable
                if entry["check"] == check)


# --------------------------------------------------------------------------
# A profile is somebody's, and shape does not say whose
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url,brand,expected", [
    # A contributor thanked in an open-source project's footer.
    ("https://github.com/Some-Person", "Preact", False),
    ("https://github.com/preactjs", "Preact", True),
    # A blog post about signals cited these; neither is about the project.
    ("https://en.wikipedia.org/wiki/Pure_function", "Preact", False),
    # An article that really is about the company.
    ("https://en.wikipedia.org/wiki/Example_Manufacturing_GmbH",
     "Example Manufacturing", True),
    # A handle brands really use.
    ("https://x.com/getexample", "Example", True),
])
def test_a_profile_counts_only_when_it_names_the_brand(url, brand, expected):
    """A quantified, false headline claim: "Distinct off-site profiles linked:
    Bluesky, GitHub, Wikipedia, X". Two of the four were a contributor's
    personal account and an article about a computer-science term, while the
    project's own organisation page - linked dozens of times, always as a
    repository path - was never counted at all."""
    assert profile_names_brand(url, brand) is expected


def test_a_profile_identified_by_number_cannot_be_named_and_still_counts():
    """A Wikidata item is a Q-number and a YouTube channel is a UC-string.
    Requiring a handle to name the brand dropped a fixture's own correct
    Wikidata link."""
    assert profile_is_opaque("https://www.wikidata.org/wiki/Q42")
    assert profile_is_opaque("https://www.youtube.com/channel/UCabcdef")
    assert not profile_is_opaque("https://en.wikipedia.org/wiki/Pure_function")


def test_a_profile_the_site_claims_in_same_as_needs_no_further_proof():
    """`sameAs` says "these accounts are me", and that settles it even for the
    platforms whose URL does state its subject."""
    candidates = {"Wikipedia": "https://en.wikipedia.org/wiki/Pure_function"}
    assert profiles_naming_brand(candidates, "Example") == {}
    assert profiles_naming_brand(
        candidates, "Example",
        declared={"Wikipedia": "https://en.wikipedia.org/wiki/Pure_function"}) == candidates


def test_a_handle_nobody_can_check_is_taken_at_face_value():
    """A handle is a name somebody chose, so a mismatch proves nothing.

    Applying the name test to every platform made a worse mistake than the one
    it fixed: on a language's own site it dropped the foundation's LinkedIn
    page, its X account and its conference YouTube channel - all real, all the
    project's own - because none of the handles spells the site's name, and
    turned "links to only 4 off-site profiles" into "links to no off-site
    profile at all" at high severity.
    """
    candidates = {"LinkedIn": "https://www.linkedin.com/company/example-foundation/",
                  "X": "https://twitter.com/TheEF",
                  "YouTube": "https://www.youtube.com/user/exconf08"}
    assert profiles_naming_brand(candidates, "Example.org", None,
                                 ["example"]) == candidates


# --------------------------------------------------------------------------
# Where a site declares an account, other than in `sameAs` and the footer
# --------------------------------------------------------------------------

def test_a_rel_me_account_counts_as_a_profile():
    """A cloud host declares its Mastodon account in the head. It is not a body
    link, not a footer link and not in `sameAs`, so no source the finding named
    could see it - and Mastodon has no fixed domain, so no platform list can
    hold it either. A `rel="me"` pointing back at the site is not a profile."""
    soup = make_soup('<link rel="me" href="https://hachyderm.example/@brand">')
    found = _social_profiles([], [], soup, "https://brand.test/")
    assert list(found.values()) == ["https://hachyderm.example/@brand"]

    own = make_soup('<link rel="me" href="https://brand.test/about">')
    assert _social_profiles([], [], own, "https://brand.test/") == {}


@pytest.mark.parametrize("url,platform", [
    ("https://www.patreon.com/join/brand", "Patreon"),
    ("https://opencollective.com/brand", "Open Collective"),
])
def test_platforms_found_in_real_footers(url, platform):
    """A browser-compatibility database's whole off-site presence besides
    GitHub was a Patreon and a Mastodon account, and the report said it links
    one profile."""
    found = _social_profiles([{"url": url}], [], make_soup("<p>x</p>"), "https://brand.test/")
    assert platform in found


# --------------------------------------------------------------------------
# An absence title may not claim more than the evidence under it
# --------------------------------------------------------------------------

def _profile_snapshot(llms_txt=None):
    return {"origin": ORIGIN,
            "brand": {"name": "Invented Name"},
            "llms_txt": llms_txt or {},
            "pages": []}


def test_the_absence_title_names_what_was_looked_at():
    """"The site links to no off-site profile at all" was the only
    high-severity finding in one project's report, and that project's own
    `/llms.txt` - fetched by this audit and recorded as present - links its
    public repository. The evidence line was scoped; the title was not, and a
    reader reads titles first."""
    result = SkillResult("freshness-corroboration-audit")
    snapshot = _profile_snapshot({"present": True, "status": 200,
                                  "url": ORIGIN + "/llms.txt"})
    FRESHNESS._check_authoritative_profiles(result, snapshot, [])
    finding = next(f for f in result.findings if f["root_cause"] == "weak-corroboration")
    assert "crawled pages" in finding["title"] and "sameAs" in finding["title"]
    assert "llms.txt" in finding["evidence"], (
        "a file this audit fetched and did not read is a hole in an absence claim")


def test_an_account_the_site_names_in_its_own_agent_file_counts_as_declared():
    """`/llms.txt` is a file the site writes about itself, so an off-site link
    in it is the site saying that account is connected to it - the strongest
    evidence a corroboration check can have."""
    result = SkillResult("freshness-corroboration-audit")
    snapshot = _profile_snapshot({
        "present": True, "status": 200,
        "url": ORIGIN + "/llms.txt",
        "text": "# Invented Name\n\n- [Source](https://repo-host.test/invented/project)\n",
    })
    FRESHNESS._check_authoritative_profiles(result, snapshot, [])
    finding = next(f for f in result.findings if f["root_cause"] == "weak-corroboration")
    assert "repo-host.test" in finding["evidence"]
    assert result.signals["profiles_declared_in_llms_txt"] == ["repo-host.test"]


# --------------------------------------------------------------------------
# A Wikidata verdict may not contradict its own clause
# --------------------------------------------------------------------------

BRAND = "Norhaven Tessellate"


class _StubWikidata:
    """The search API, and optionally the item lookup behind it.

    `owner` is the item id whose `official website` claim names the audited
    host, so the check can tell this site's own entry from the entities
    colliding with it. `entities_answer` False makes that second request fail,
    which is a different thing from it answering and naming nobody.
    """

    def __init__(self, labels, owner=None, entities_answer=True):
        self.items = [{"id": "Q{}".format(900000 + i), "label": label,
                       "description": "an invented thing", "aliases": []}
                      for i, label in enumerate(labels)]
        self.owner = owner
        self.entities_answer = entities_answer
        self.count = 0

    def try_get(self, url, **kwargs):
        self.count += 1
        if "wbgetentities" in url:
            if not self.entities_answer:
                return None
            claims = {}
            if self.owner:
                claims[self.owner] = {"claims": {"P856": [
                    {"mainsnak": {"datavalue": {"value": ORIGIN}}}]}}
            return _Json({"entities": claims})
        return _Json({"search": self.items})


class _Json:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


def _ambiguity(fetcher, name=BRAND, profiles=None):
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_entity_ambiguity(
        result, {"origin": ORIGIN}, [], {"name": name}, profiles or {}, fetcher, True)
    return result


def test_a_count_below_the_threshold_is_not_announced_as_no_collision():
    """The clause named another entity and the conclusion denied it existed."""
    result = _ambiguity(_StubWikidata([BRAND, BRAND], owner="Q900000"))
    reason = _reason(result, "entity-ambiguity")
    assert "1 other entity" in reason
    assert "no name collision" not in reason
    assert "below the 2 entities" in reason


def test_no_other_entity_still_reads_as_no_collision():
    result = _ambiguity(_StubWikidata([BRAND], owner="Q900000"))
    reason = _reason(result, "entity-ambiguity")
    assert "no other entity" in reason
    assert "no name collision to resolve" in reason


def test_a_name_that_matches_nothing_is_reported_as_being_about_the_name():
    """Zero results for a name a site plainly uses is evidence about the string
    the audit was handed, which is read from the site's own markup, and not
    about what Wikidata holds."""
    result = _ambiguity(_StubWikidata([]))
    reason = _reason(result, "entity-ambiguity")
    assert "no entity at all" in reason
    assert "name string this audit was given" in reason
    assert "no name collision" not in reason
    # And nothing downstream may read it as "this brand has no Wikidata item".
    assert result.signals["wikidata_item_exists"] is None


def test_a_failed_own_item_lookup_leaves_the_item_question_unanswered():
    """"We looked and found nothing" is not "we could not look"."""
    result = _ambiguity(_StubWikidata([BRAND] * 3, entities_answer=False))
    assert result.signals["wikidata_item_exists"] is None
    evidence = next(f["evidence"] for f in result.findings
                    if f["root_cause"] == "entity-ambiguity")
    assert "did not answer" in evidence
