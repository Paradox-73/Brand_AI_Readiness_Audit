"""Every defect the marketplace can report must have a test that makes it report it.

`thin-html` was declared in the root-cause vocabulary, documented in a SKILL.md
and registered in every report's check list for the whole build - and no code
path could ever emit it. Nothing failed, because nothing asked.

This file asks. It is static and instant: it compares the vocabulary against
what the fixtures and the mutation suite between them claim to produce. Adding
a root cause without adding a case that produces it fails here.
"""

from __future__ import annotations

import json
import os

from audit_common import ROOT_CAUSES
from conftest import GOLDEN, all_fixture_names
from mutations import MUTATIONS

# Empty, and it should stay empty. This was the escape hatch: two causes sat
# on it as "cannot be staged against a local fixture server". Re-reading the
# reasons found neither survived contact with the code.
#
#   insecure-transport   was recorded as needing a TLS certificate. It needs no
#                        such thing - the check reads whether the origin string
#                        starts with `http://`. What the fixture server cannot
#                        be is a non-loopback host, which is a different fact
#                        and takes one dict to supply.
#   entity-ambiguity     was recorded as needing a real Wikidata collision. It
#                        needs a response *shaped like* one, which a stub gives
#                        it with invented labels.
#
# An exemption is a claim that something is unprovable. Both claims were wrong,
# and each had been sitting in this file being read as settled. Anything added
# here needs to be genuinely unreachable rather than merely inconvenient - and
# on this evidence, check twice.
UNSTAGEABLE = set()

# Causes proved by a unit test instead of a fixture, and the file that proves
# them. This is not an exemption: the guarantee still holds, it is just met
# somewhere the fixture server cannot reach. The test below opens each file and
# checks the cause is actually named in it, so an entry cannot rot into a lie.
PROVED_BY_UNIT_TEST = {
    # Proving this needs a real platform to answer 404 for a profile that does
    # not exist. The fixture server can only serve itself, and pointing the
    # suite at LinkedIn on every run would make it slow, rude and dependent on
    # somebody else's uptime.
    "dead-profile-link": "test_profile_links.py",
    # Needs an origin on a real hostname served over plain HTTP. The fixture
    # server is loopback, and the check correctly treats loopback as a
    # deployment detail rather than a defect, so a snapshot supplies the origin
    # directly.
    "insecure-transport": "test_unstageable_causes.py",
    # Needs a Wikidata response listing several entities with one name. A stub
    # supplies it with invented labels, so no real organisation enters the
    # test corpus.
    "entity-ambiguity": "test_unstageable_causes.py",
    # Needs an origin that answers correctly and takes seconds to do it. The
    # fixture server answers in milliseconds, and making it sleep would add
    # that time to every run of the suite for one root cause. A snapshot with
    # real timings supplies it instead.
    "slow-origin": "test_later_rounds.py",
}


def _covered():
    covered = set()
    for case in MUTATIONS:
        covered |= case["expect"] | case["also"]
    for name in all_fixture_names():
        path = os.path.join(GOLDEN, "{}.json".format(name))
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                covered |= set(json.load(handle).get("must_find") or [])
    return covered


def test_every_root_cause_has_a_case_that_produces_it():
    missing = ROOT_CAUSES - _covered() - UNSTAGEABLE - set(PROVED_BY_UNIT_TEST)
    assert not missing, (
        "these root causes can be reported to a user but nothing in the suite "
        "makes them fire, so nobody knows whether they work: {}. Add a mutation "
        "to tests/mutations.py, or a fixture expectation, or - if the cause is "
        "genuinely unreachable locally - add it to UNSTAGEABLE with the "
        "reason.".format(sorted(missing)))


def test_unstageable_list_has_not_grown_silently():
    assert UNSTAGEABLE <= ROOT_CAUSES, (
        "UNSTAGEABLE names something that is not a root cause: {}".format(
            sorted(UNSTAGEABLE - ROOT_CAUSES)))
    assert len(UNSTAGEABLE) <= 3, (
        "the exemption list is meant to stay tiny; it now holds {}".format(
            sorted(UNSTAGEABLE)))


def test_no_case_claims_a_root_cause_that_does_not_exist():
    claimed = set()
    for case in MUTATIONS:
        claimed |= case["expect"] | case["also"]
    unknown = claimed - ROOT_CAUSES
    assert not unknown, (
        "mutations expect root causes the vocabulary does not define: {}".format(
            sorted(unknown)))


def test_the_unit_test_registry_points_at_files_that_prove_what_it_claims():
    """An entry here has to name a file that exists and mentions the cause.

    Without this the registry is a way to silence the coverage check by typing
    a root cause into a dictionary, which is the opposite of what it is for.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for cause, filename in sorted(PROVED_BY_UNIT_TEST.items()):
        assert cause in ROOT_CAUSES, "{} is not a real root cause".format(cause)
        assert cause not in UNSTAGEABLE, (
            "{} is listed as both proved and unstageable".format(cause))
        path = os.path.join(here, filename)
        assert os.path.isfile(path), "{} claims proof in {}, which does not exist".format(
            cause, filename)
        with open(path, encoding="utf-8") as handle:
            body = handle.read()
        assert cause in body, (
            "{} claims to be proved in {}, but that file never mentions it".format(
                cause, filename))
