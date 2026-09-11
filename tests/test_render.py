"""The optional rendered pass, exercised for real.

`--render` is the one code path in this marketplace that a plain `pytest` run
never touched: without Playwright installed it falls back to static analysis,
the fallback is well covered, and the branch behind it had never executed. Two
things were wrong the first time it ran, and neither was visible from reading
it.

These tests skip when Playwright is absent, which is the normal case on a
user's machine. That is the point of the skip - the marketplace must work
without a browser, and must be correct with one.

    pip install playwright && playwright install chromium
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

import pytest

from conftest import ROOT, SCRIPTS, REFERENCE_DATE, run_script
from fixture_server import FixtureServer

playwright = pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is not installed; the rendered pass is optional by design")

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# The whole point of the pass is that it stays inside the audit's time promise.
# The first working version used Playwright's `networkidle`, which took 133
# seconds over five local pages because a page with a pending request never
# reaches idle. Anything near that number here is the same bug returning.
RENDER_CEILING_SECONDS = 90


def _rendered_audit(fixture, tmp_path):
    """Crawl one fixture with `--render`, then run the readability check on it."""
    with FixtureServer(os.path.join(FIXTURES, fixture)) as server:
        snapshot_path = os.path.join(str(tmp_path), "snapshot.json")
        started = time.monotonic()
        run_script([os.path.join(SCRIPTS, "crawl.py"), server.base_url,
                    "--out", snapshot_path, "--budget", "150", "--delay", "0", "--render"],
                   "crawl.py --render [{}]".format(fixture))
        elapsed = time.monotonic() - started

        findings_path = os.path.join(str(tmp_path), "render.findings.json")
        run_script([os.path.join(ROOT, "skills", "render-readability-audit",
                                 "scripts", "check.py"),
                    "--snapshot", snapshot_path, "--out", findings_path],
                   "render-readability-audit [{}]".format(fixture))

    with open(snapshot_path, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    with open(findings_path, encoding="utf-8") as handle:
        findings = json.load(handle)
    return snapshot, findings, elapsed


@pytest.fixture(scope="module")
def rendered_shell(tmp_path_factory):
    return _rendered_audit("js-shell-site", tmp_path_factory.mktemp("render_shell"))


@pytest.fixture(scope="module")
def rendered_clean(tmp_path_factory):
    return _rendered_audit("good-site", tmp_path_factory.mktemp("render_clean"))


# --------------------------------------------------------------------------
# The pass runs at all
# --------------------------------------------------------------------------

def test_render_mode_is_recorded_as_rendered(rendered_shell):
    snapshot, _, _ = rendered_shell
    assert snapshot["crawl"]["render_mode"] == "rendered", (
        "Playwright is installed but the crawl still reported a static pass; "
        "notes: {}".format(snapshot["crawl"].get("notes")))


def test_render_pass_stays_inside_its_budget(rendered_shell):
    _, _, elapsed = rendered_shell
    assert elapsed < RENDER_CEILING_SECONDS, (
        "the rendered crawl took {:.0f}s over five local pages. The audit "
        "promises a five-minute run; a browser waiting on an idle state that "
        "never arrives will spend all of it.".format(elapsed))


# --------------------------------------------------------------------------
# The pass changes the answer, in the right direction
# --------------------------------------------------------------------------

def test_javascript_recovers_the_shell_content(rendered_shell):
    """The fixture hydrates, so the text exists - for consumers that run JS.

    Read through `static_view`. The crawl now re-reads a shell from the
    rendered DOM so the other five skills judge the page a visitor sees, which
    means `body_text_len` on the record describes the rendered document. The
    delivered lengths are kept under `static_view`, and they are what this test
    is about.
    """
    snapshot, _, _ = rendered_shell
    home = next(p for p in snapshot["pages"] if p.get("page_type") == "home")
    delivered = home.get("static_view") or home
    assert delivered["body_text_len"] < 300, "the fixture should ship an empty shell"
    assert home["rendered_text_len"] > delivered["body_text_len"] * 5, (
        "hydration should add most of the text: static {} vs rendered {}".format(
            delivered["body_text_len"], home["rendered_text_len"]))


def test_recovered_content_softens_the_verdict(rendered_shell):
    """`critical` claims the content is unreachable. Here it is reachable.

    Getting this backwards would tell a brand its homepage does not exist when
    in fact it exists for anything running JavaScript - a different problem
    with a different fix and a different urgency.
    """
    _, findings, _ = rendered_shell
    shells = [f for f in findings["findings"] if f["root_cause"] == "js-shell"]
    assert shells, "the shell fixture should still report a shell"
    homepage = [f for f in shells if "homepage" in f["title"].lower()]
    assert homepage, "expected a homepage shell finding"
    assert homepage[0]["severity"] == "high", (
        "with the text recovered the finding should be `high`, not `critical`; "
        "got {}".format(homepage[0]["severity"]))
    # The invariant is that the evidence states the measurement, not that it
    # uses one particular verb. The wording that said "a Playwright pass
    # recovered only N chars" belonged to the branch where the text is NOT
    # reachable; asserting that word here asked the `high` branch to speak in
    # the `critical` branch's voice.
    evidence = homepage[0]["evidence"].lower()
    assert re.search(r"\d+ chars after javascript ran", evidence), (
        "the evidence should say how much text JavaScript supplied: {}".format(evidence))


def test_gap_check_runs_instead_of_skipping(rendered_shell):
    """Without a browser this check reports why it could not run. With one it runs."""
    _, findings, _ = rendered_shell
    skipped = {s["check"] for s in findings.get("not_applicable", [])}
    assert "static-vs-rendered-text-gap" not in skipped, (
        "the gap check skipped despite a rendered pass being available")


def test_a_server_rendered_site_shows_no_gap(rendered_clean):
    """The check must not treat every JavaScript-using site as a stub."""
    snapshot, findings, _ = rendered_clean
    assert snapshot["crawl"]["render_mode"] == "rendered"

    # The invariant is the outcome, not the wording. Under load the pass can
    # stop at its budget with fewer pages measured, which is a different and
    # equally correct reason to report nothing - asserting the exact sentence
    # made this test fail in a full run and pass on its own.
    assert not [f for f in findings["findings"] if f["root_cause"] == "js-shell"], (
        "a server-rendered site was reported as a JavaScript shell")
    skipped = {s["check"]: s["reason"] for s in findings.get("not_applicable", [])}
    assert "static-vs-rendered-text-gap" in skipped, (
        "good-site delivers its text in the HTML, so the gap check should have "
        "found nothing to report")

    measured = [p for p in snapshot["pages"] if "rendered_text_len" in p]
    if measured:
        assert "less than" in skipped["static-vs-rendered-text-gap"], (
            "pages were measured, so the reason should be that the gap was "
            "small: {}".format(skipped["static-vs-rendered-text-gap"]))


def test_rendered_run_reports_no_findings_on_the_clean_site(rendered_clean):
    """A browser must not manufacture findings the static pass does not make."""
    _, findings, _ = rendered_clean
    assert findings["findings"] == [], (
        "the rendered pass introduced findings on the clean fixture: {}".format(
            [f["root_cause"] for f in findings["findings"]]))

# --------------------------------------------------------------------------
# Which pages get rendered
#
# This was `pages[:RENDER_PAGES]`. The crawl is breadth-first from the
# homepage, so those five were the homepage plus the first four top-nav pages
# - on a hybrid site, precisely the pages rendered on the server. The
# JavaScript shells this pass exists to measure live on product and article
# pages one level down, and could never be in the sample.
# --------------------------------------------------------------------------

def _page(url, page_type, status=200):
    return {"url": url, "status": status, "page_type": page_type}


def test_the_render_sample_reaches_deep_page_types():
    from crawl import _render_targets
    pages = [_page("https://h.test/", "home")]
    pages += [_page("https://h.test/list{}".format(i), "listing") for i in range(6)]
    pages += [_page("https://h.test/p1", "product"), _page("https://h.test/a1", "article")]
    chosen = {p["page_type"] for p in _render_targets(pages)}
    assert "product" in chosen and "article" in chosen
    # And the homepage still leads the sample.
    assert _render_targets(pages)[0]["page_type"] == "home"


def test_the_render_sample_is_the_same_two_runs_running():
    from crawl import _render_targets
    pages = [_page("https://h.test/", "home")]
    pages += [_page("https://h.test/x{}".format(i), "listing") for i in range(10)]
    first = [p["url"] for p in _render_targets(pages)]
    second = [p["url"] for p in _render_targets(list(reversed(pages)))]
    assert first == second


def test_only_pages_that_answered_200_are_rendered_and_never_more_than_the_budget():
    from crawl import RENDER_PAGES, _render_targets
    forty = [_page("https://h.test/p{}".format(i), "product") for i in range(40)]
    assert len(_render_targets(forty)) == RENDER_PAGES

    pages = [_page("https://h.test/", "home"),
             _page("https://h.test/gone", "article", status=404)]
    assert all(p["status"] == 200 for p in _render_targets(pages))
    assert _render_targets([_page("https://h.test/x", "other", status=500)]) == []


# --------------------------------------------------------------------------
# The rendered document is kept, not just measured
#
# The pass recorded `rendered_text_len` and threw the DOM away. Measured on a
# software vendor's homepage: 60 characters delivered, 6,606 rendered, and the
# snapshot kept the 60. Five skills then read a page with no H1, no navigation,
# no call to action and nothing quotable, and the report carried four findings
# that were all false of the page a visitor sees - two entries below a finding
# that quoted the 6,606 figure it had just measured.
# --------------------------------------------------------------------------

def test_a_shell_is_re_read_from_the_rendered_document(rendered_shell):
    """The record downstream skills read describes the hydrated page."""
    snapshot, _, _ = rendered_shell
    home = next(p for p in snapshot["pages"] if p.get("page_type") == "home")
    assert home.get("content_from") == "rendered", (
        "the homepage delivered 13 characters and rendered several hundred; it should "
        "have been re-read from the browser's document")
    assert home["text_len"] > (home.get("static_view") or {})["text_len"], (
        "the record still holds the delivered text: {} vs {}".format(
            home["text_len"], (home.get("static_view") or {})["text_len"]))


def test_the_delivered_document_is_kept_beside_the_rendered_one(rendered_shell):
    """Both halves of the JavaScript gap survive, or the gap cannot be stated."""
    snapshot, _, _ = rendered_shell
    home = next(p for p in snapshot["pages"] if p.get("page_type") == "home")
    view = home.get("static_view") or {}
    assert view.get("text_len") == home.get("static_text_len")
    assert view.get("spa_shell"), "the shell markers of the delivered page are the evidence"
    assert home["rendered_text_len"] > view["text_len"] * 3


def test_a_server_rendered_page_is_left_alone(rendered_clean):
    """Nothing is re-read on a site that delivers its own text."""
    snapshot, _, _ = rendered_clean
    adopted = [p["url"] for p in snapshot["pages"] if p.get("content_from") == "rendered"]
    assert not adopted, (
        "the clean fixture delivers its text in the HTML; re-reading it from a browser "
        "would replace a measurement with a different measurement for no reason: {}".format(
            adopted))
