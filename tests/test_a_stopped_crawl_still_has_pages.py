# -*- coding: utf-8 -*-
"""What a crawl leaves behind when it is stopped part-way through.

The run now reserves wall clock for the six sub-skills before the crawl starts
rather than handing them whatever survived it, and the crawl is killed at that
reserve boundary. Under the old arrangement the ceiling held but coverage did
not - a budget that finishes in time by killing a skill has not met the
promise. Measured on a site answering in six
seconds a page, that run finished in 273.5 s with 29 pages, five of the six
sub-skills, and `engagement-audit` stopped at 94 s.

Stopping the crawl instead is only the cheaper cut if being stopped costs pages
rather than costing the run. It used to cost the run: `crawl.py` built its
snapshot as one dictionary at the end of a function a killed process never
reached, so the orchestrator's handling of a partial crawl - audit the pages it
read, say the crawl was cut short - could almost never fire, and the audit
exited with "nothing to report on" instead.

So the crawl writes what it has as it goes. These tests hold that the file it
leaves is one the six sub-skills can read, and that it says it is a partial
record rather than passing itself off as a finished crawl.

Every host in this file is invented; the servers listen on the loopback
address and every address they serve is fictional.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import load_snapshot  # noqa: E402
from crawl import CHECKPOINT_NOTE, write_json_atomically  # noqa: E402

# One implementation of a site that takes time to answer, not two. It lives in
# `fixture_server` beside the fixture server itself, because the crawl's own
# determinism tests need it too and a second copy here would be a second thing
# to keep in step with `crawl.py`.
from fixture_server import LatentSite, linked_pages  # noqa: E402

CRAWL = os.path.join(SCRIPTS, "crawl.py")

# One killed crawl, shared. Each one costs the seconds it is allowed to run
# before the kill, and three tests asking the same question of the same file do
# not need three of them.


@pytest.fixture(scope="module")
def stopped_snapshot(tmp_path_factory):
    out_path = str(tmp_path_factory.mktemp("stopped") / "snapshot.json")
    _crawl_until_killed(out_path, seconds=8)
    return out_path


def _crawl_until_killed(out_path, seconds, pace=0.4, pages=60):
    """Start a crawl against a slow site and kill it after `seconds`.

    Killed the way the orchestrator kills it - `subprocess` timeout expiry,
    which is a terminate with no chance to clean up - so this is the same
    ending, not a simulation of it.
    """
    with LatentSite(linked_pages(pages), lambda served: pace) as site:
        process = subprocess.Popen(
            [sys.executable, CRAWL, site.base_url, "--out", out_path,
             "--budget", "600", "--delay", "0", "--no-render"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.communicate(timeout=seconds)
            raise AssertionError(
                "the crawl finished inside {}s, so nothing was killed and this test "
                "is not measuring what it claims".format(seconds))
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


def test_a_crawl_killed_part_way_leaves_the_pages_it_had_read(stopped_snapshot):
    """The property the reserve depends on.

    Without it, holding wall clock back for the six sub-skills would be an
    empty promise: the crawl would still own the clock at the moment it was
    stopped, and stopping it would throw away the run rather than the pages.
    """
    out_path = stopped_snapshot

    assert os.path.exists(out_path), \
        "a killed crawl left no snapshot, so the whole run would be lost"
    with open(out_path, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    assert snapshot["pages"], "the snapshot holds no pages to audit"

    # Readable by the six, not merely by `json.load`. Every sub-skill comes
    # through `load_snapshot`, which resolves the brand name before anything
    # reads it, and a checkpoint that fell over in there would fail six times
    # rather than once.
    assert load_snapshot(out_path)["pages"]


def test_a_snapshot_written_mid_crawl_says_that_is_what_it_is(stopped_snapshot):
    """A partial record that reads as a finished one is worse than none.

    The pages in a checkpoint have not had their site-wide header and footer
    stripped, no rendered pass has run over them and whether the host answers
    HEAD was never measured. Those change word counts, the JavaScript findings
    and the three link checks, so the note goes in `crawl.notes` - the list the
    report already prints one bullet per line in its appendix.
    """
    with open(stopped_snapshot, encoding="utf-8") as handle:
        notes = json.load(handle)["crawl"]["notes"]
    assert CHECKPOINT_NOTE in notes, notes
    assert "still running" in CHECKPOINT_NOTE and "HEAD" in CHECKPOINT_NOTE


def test_a_crawl_that_finished_is_not_labelled_a_checkpoint(tmp_path):
    """The note may only appear on a snapshot that really is partial, or it
    stops meaning anything and every report carries the caveat."""
    out_path = str(tmp_path / "finished.json")
    with LatentSite(linked_pages(6)) as site:
        completed = subprocess.run(
            [sys.executable, CRAWL, site.base_url, "--out", out_path,
             "--budget", "60", "--delay", "0", "--no-render"],
            cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr[-1500:]
    with open(out_path, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    assert CHECKPOINT_NOTE not in snapshot["crawl"]["notes"]
    assert snapshot["crawl"]["pages_crawled"] >= 1
    assert not os.path.exists(out_path + ".partial"), \
        "the temporary the checkpoint writes through was left behind"


def test_a_write_that_is_interrupted_does_not_destroy_the_last_one(tmp_path):
    """Why the checkpoint renames rather than overwriting.

    The snapshot is written many times now instead of once, so the odds of a
    kill landing inside a write went up with the number of writes. A truncated
    JSON file where a readable snapshot had been is a worse outcome than the
    older one: the orchestrator refuses it, and the pages the crawl really did
    read are lost after having been on disk.
    """
    target = str(tmp_path / "snapshot.json")
    write_json_atomically(target, {"pages": [{"url": "https://an-invented-site.test/"}]})
    assert not os.path.exists(target + ".partial")

    # The failure this guards against, staged: something goes wrong after the
    # temporary is written and before the rename. The file readers see is
    # still the last complete one.
    with open(target + ".partial", "w", encoding="utf-8") as handle:
        handle.write('{"pages": [{"url": "https:/')
    with open(target, encoding="utf-8") as handle:
        assert json.load(handle)["pages"], "the interrupted write reached the target"


def test_the_pages_a_stopped_crawl_leaves_are_a_prefix_of_the_ones_it_planned(
        stopped_snapshot):
    """A checkpoint is a shorter crawl, not a different one.

    `crawl.py` works through a determined order - `KindSpreadQueue` serves each
    kind of page in turn, the sitemap sample is drawn under a fixed seed, and
    in-page links are expanded sorted - so the page set is always a prefix of
    one sequence. Writing it early may not reorder it, or two runs of one site
    would disagree about which pages were read for a reason that is not the
    site.
    """
    with open(stopped_snapshot, encoding="utf-8") as handle:
        pages = json.load(handle)["pages"]
    paths = [p["url"].split("/", 3)[-1] for p in pages]
    assert paths[0] == "", "the homepage is not first in a partial crawl"
    assert len(paths) == len(set(paths)), "a page was recorded twice"
