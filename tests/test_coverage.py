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

# Two causes cannot be staged against a local fixture server, with the reason
# each one is exempt. Keep this list short and keep the reasons specific: it is
# the only way a check escapes proof, so anything added here needs to be
# genuinely unreachable rather than merely inconvenient.
UNSTAGEABLE = {
    # Needs a TLS certificate and an https origin. The fixture server is plain
    # HTTP on a loopback port, so there is no insecure-transport to detect and
    # no secure one to contrast it against.
    "insecure-transport",
    # Needs a real Wikidata name collision. The fixture brand is invented
    # precisely so it collides with nothing, and hard-coding a real entity's
    # identifier would put a real organisation's name in the test corpus.
    "entity-ambiguity",
}

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
