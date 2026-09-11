"""Does each check detect the thing it says it detects, and nothing else?

`good-site` audits clean. Every test here takes a copy of it, breaks exactly
one property, and asserts the audit reports that property - and, where the edit
is surgical enough to make the claim fair, reports nothing else.

The second half is what fixture tests and real-site samples cannot give us. A
check that correlates with a defect on 36 sampled sites passes any test built
from those 36 sites. It fails here, because here we control the cause.

Slow by design: each case is a full crawl and audit. Run just this file with

    pytest tests/test_mutations.py -q

and skip it during quick iteration with `-m "not mutation"`.
"""

from __future__ import annotations

import os
import shutil

import pytest

from conftest import _audit
from mutations import MUTATIONS

pytestmark = pytest.mark.mutation

FIXTURE = "good-site"


def _ids(mutations):
    return [m["name"] for m in mutations]


@pytest.fixture(scope="module")
def clean_baseline(tmp_path_factory):
    """The unmutated site must report nothing, or every case below is void."""
    out_dir = str(tmp_path_factory.mktemp("baseline"))
    server, result = _audit(FIXTURE, out_dir, render=False)
    try:
        yield result.root_causes
    finally:
        server.__exit__(None, None, None)


def test_baseline_is_clean(clean_baseline):
    assert clean_baseline == set(), (
        "good-site must audit clean for the mutation suite to mean anything; "
        "it currently reports {}".format(sorted(clean_baseline)))


@pytest.mark.parametrize("case", MUTATIONS, ids=_ids(MUTATIONS))
def test_mutation_is_detected_and_specific(case, clean_baseline, tmp_path_factory):
    work = str(tmp_path_factory.mktemp("mut"))
    site = os.path.join(work, "site")
    shutil.copytree(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fixtures", FIXTURE), site)
    case["apply"](site)

    out_dir = os.path.join(work, "out")
    os.makedirs(out_dir)
    server, result = _audit(FIXTURE, out_dir, site_dir=site, render=False)
    try:
        found = result.root_causes
    finally:
        server.__exit__(None, None, None)

    missed = case["expect"] - found
    assert not missed, (
        "broke '{}' and the audit did not notice: expected {}, got {}".format(
            case["name"], sorted(case["expect"]), sorted(found) or "nothing"))

    if case["exclusive"]:
        spurious = found - case["expect"] - case["also"] - clean_baseline
        assert not spurious, (
            "broke '{}' and the audit reported {} as well. Either the edit has "
            "a real knock-on that belongs in `also`, or those checks are not "
            "measuring what they claim.".format(case["name"], sorted(spurious)))
