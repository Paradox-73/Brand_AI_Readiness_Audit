# -*- coding: utf-8 -*-
"""A naming finding may not quote a string its own named sources do not hold.

On an Indian leather-goods shop the naming-consistency finding listed four
sources it had read and then quoted two spellings of the brand. An independent
review, checking by hand, could not find that pair in any of the four:

    "That is the actual name inconsistency; F-005 instead quotes a pair ...
     that I could not locate in any of the four sources it names."

A finding that names its sources and then quotes a string from outside them is
unverifiable, which is worse than silent. The one thing this report sells is
that every claim in it can be checked, and a reader who follows a citation to
nothing stops trusting the citations that are good.

This is the fact-extractability half of the row. The structured-data half is
pinned by `test_front_door_title_and_declared_brand.py::
test_every_string_quoted_is_one_the_named_sources_hold`, and these tests are
that assertion applied to the other finding of the same shape.

Both ends of the pipe are exercised: the `brand` record is built by
`crawl.detect_brand` from pages rather than written out by hand, so a name that
can enter `authoritative_variants` from somewhere the finding does not name as
a source fails here rather than reaching a report.

Every host and every name below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402
from crawl import detect_brand  # noqa: E402


def _facts_module():
    path = os.path.join(ROOT, "skills", "fact-extractability-audit", "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("facts_for_naming_evidence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACTS = _facts_module()

SITE = "https://sarai-leathers.test"


def _page(path, page_type="other", title="", org_name=None, site_name=None, lang="en"):
    page = {
        "url": SITE + path,
        "page_type": page_type,
        "title": title,
        "lang": lang,
        "status": 200,
        "body_text": "",
        "jsonld": [],
        "jsonld_types": [],
        "og": {},
        "links": {},
    }
    if org_name is not None:
        page["jsonld"] = [{"@type": "Organization", "name": org_name,
                           "url": SITE + "/"}]
        page["jsonld_types"] = ["Organization"]
    if site_name is not None:
        page["og"] = {"og:site_name": site_name}
    return page


def _run(pages):
    brand = detect_brand(pages, SITE)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_naming_consistency(result, {"pages": pages}, pages, brand)
    return brand, result


def _quoted(text):
    """Every string the evidence puts in double quotes."""
    return {chunk for chunk in re.split(r'"', text)[1::2] if chunk.strip()}


# --------------------------------------------------------------------------
# The measured shape: two spellings of one name, declared in markup
# --------------------------------------------------------------------------

def _two_spellings():
    return [
        _page("/", "home", title="Sarai Leathers", org_name="Sarai Leathers",
              site_name="Sarai Leathers"),
        _page("/about", "about", title="About", org_name="SaraiLeathers"),
        _page("/contact", "contact", title="Contact", org_name="SaraiLeathers"),
    ]


def test_the_disagreement_is_reported_at_all():
    """The detection half, so the verifiability half is not passing on silence."""
    _, result = _run(_two_spellings())
    assert result.findings, "two declared spellings of one name is the finding"
    assert result.findings[0]["id_hint"] == "brand-name-written-inconsistently"


def test_every_name_the_evidence_quotes_is_one_the_snapshot_declares():
    """The row itself. A quoted string is either a declared variant or the
    primary form, and the primary form prints the source it came from beside
    it, so both are strings a reader can go and find."""
    brand, result = _run(_two_spellings())
    finding = result.findings[0]
    holdable = set(brand["authoritative_variants"]) | {brand["name"]}
    holdable |= set(brand.get("alternate_names") or [])
    unlocatable = _quoted(finding["evidence"]) - holdable
    assert not unlocatable, (
        "the evidence quotes a name no source holds: {}".format(sorted(unlocatable)))


def test_every_quoted_variant_is_shown_with_a_page_that_declares_it():
    """Naming the sources is not enough on its own: the problem was that the
    pair could not be *located*. Each variant is printed with a URL,
    so following the claim is one fetch rather than a search."""
    brand, result = _run(_two_spellings())
    evidence = result.findings[0]["evidence"]
    declared_on = brand["declared_on"]
    for variant in _quoted(evidence) & set(brand["authoritative_variants"]):
        urls = declared_on.get(variant) or []
        assert urls, "{} is declared on no page this crawl recorded".format(variant)
        assert urls[0] in evidence, (
            "{} is quoted without a page a reader could check it on".format(variant))


def test_the_sources_it_names_are_the_sources_the_snapshot_recorded():
    """The list at the head of the evidence is read from the snapshot rather
    than written as a fixed sentence. A finding whose source list is a constant
    is the shape that produced the unverifiable pair."""
    brand, result = _run(_two_spellings())
    evidence = result.findings[0]["evidence"]
    assert brand["authoritative_sources"], "nothing was declared authoritatively"
    for source in brand["authoritative_sources"]:
        assert source in evidence, source


def test_the_primary_form_never_appears_without_the_source_it_came_from():
    """`brand["name"]` may be chosen from a page title or the domain, neither
    of which is an authoritative source. Printing it bare among names read from
    markup is exactly how a reader is handed a string the listed sources do not
    hold."""
    brand, result = _run(_two_spellings())
    evidence = result.findings[0]["evidence"]
    assert '"{}" (from {})'.format(brand["name"], brand["source"]) in evidence


# --------------------------------------------------------------------------
# The same guarantee where the primary name is not declared anywhere
#
# The site above declares its own name. This one declares two names in markup
# and takes its primary form from the homepage title, so `brand["name"]` is a
# string no authoritative source holds - the exact condition under which the
# old wording became unverifiable.
# --------------------------------------------------------------------------

def _primary_from_the_title():
    return [
        _page("/", "home", title="Kestrel Optics | Lenses since 1974"),
        _page("/about", "about", org_name="Kestrel Optics Ltd"),
        _page("/stockists", org_name="KestrelOptics Ltd"),
        _page("/repairs", org_name="KestrelOptics Ltd"),
    ]


def test_a_primary_form_taken_from_a_title_is_labelled_as_such():
    brand, result = _run(_primary_from_the_title())
    if not result.findings:
        return
    evidence = result.findings[0]["evidence"]
    holdable = set(brand["authoritative_variants"]) | {brand["name"]}
    holdable |= set(brand.get("alternate_names") or [])
    assert not (_quoted(evidence) - holdable), sorted(_quoted(evidence) - holdable)
    assert brand["source"] in evidence


# --------------------------------------------------------------------------
# The direction that pays for it
# --------------------------------------------------------------------------

def test_one_spelling_declares_a_pass_and_quotes_nothing_it_cannot_hold():
    pages = [_page("/", "home", title="Sarai Leathers", org_name="Sarai Leathers"),
             _page("/about", "about", org_name="Sarai Leathers")]
    brand, result = _run(pages)
    assert not result.findings
    reason = next(n["reason"] for n in result.not_applicable
                  if n["check"] == "brand-naming-consistency")
    assert reason.strip()
    holdable = set(brand["authoritative_variants"]) | {brand["name"]}
    assert not (_quoted(reason) - holdable), sorted(_quoted(reason) - holdable)
