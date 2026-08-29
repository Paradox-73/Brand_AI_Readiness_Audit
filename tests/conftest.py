"""Shared pytest fixtures: serve a fixture site, audit it, cache the result.

Auditing a fixture is the expensive part of the suite, so each site is audited
at most once per session and the result is reused. The servers stay up for the
whole session because several checks make live requests (the bot user-agent
probe, sitemap HEADs, internal link checks) and must be able to reach them.

Sub-skills are invoked as subprocesses rather than imported. That is slower,
but it exercises the same command-line contract the orchestrator uses, so a
broken argument or a non-zero exit shows up here rather than in front of a
judge.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
SCRIPTS = os.path.join(ROOT, "skills", "audit-orchestrator", "scripts")
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
GOLDEN = os.path.join(TESTS_DIR, "golden")

sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, SCRIPTS)

from fixture_server import FixtureServer  # noqa: E402

# Sub-skill order matters: it is the dedup precedence the orchestrator relies on.
SUB_SKILLS = [
    "crawl-access-audit",
    "render-readability-audit",
    "structured-data-audit",
    "fact-extractability-audit",
    "freshness-corroboration-audit",
    "engagement-audit",
]

# Fixed reference date. Without it, "older than 18 months" would quietly change
# meaning as the calendar moves and the suite would start failing on its own.
REFERENCE_DATE = "2026-09-01"
REFERENCE_TIMESTAMP = "2026-09-01T00:00:00Z"


def run_script(args, label):
    """Run a marketplace script, failing the test with its stderr on error."""
    completed = subprocess.run(
        [sys.executable] + args, cwd=ROOT, capture_output=True, text=True)
    if completed.returncode != 0:
        raise AssertionError("{} exited {}\nstdout:\n{}\nstderr:\n{}".format(
            label, completed.returncode, completed.stdout, completed.stderr))
    return completed


class AuditResult:
    """Everything one audited fixture produced, for tests to assert against."""

    def __init__(self, name, base_url, out_dir, snapshot, findings, report, report_md):
        self.name = name
        self.base_url = base_url
        self.out_dir = out_dir
        self.snapshot = snapshot
        self.snapshot_path = os.path.join(out_dir, "snapshot.json")
        self.findings = findings          # {skill: parsed findings json}
        self.report = report
        self.report_md = report_md

    @property
    def root_causes(self):
        return {f["root_cause"] for f in self.report["findings"]}

    @property
    def recommendation_ids(self):
        return {r["id"] for r in self.report["recommendations"]}

    def severities(self, *levels):
        return [f for f in self.report["findings"] if f["severity"] in levels]

    def page_types(self):
        return {p.get("page_type") for p in self.snapshot["pages"]}

    def findings_with(self, root_cause):
        return [f for f in self.report["findings"] if f["root_cause"] == root_cause]


def _audit(name, out_dir, no_network=False, site_dir=None):
    """Serve, crawl and audit one site directory. Returns (server, AuditResult).

    `site_dir` defaults to the named fixture. The mutation suite passes a
    throwaway copy instead, so it can break one property at a time without
    touching the fixtures the rest of the suite depends on.
    """
    server = FixtureServer(site_dir or os.path.join(FIXTURES, name)).__enter__()
    try:
        snapshot_path = os.path.join(out_dir, "snapshot.json")
        run_script([os.path.join(SCRIPTS, "crawl.py"), server.base_url,
                    "--out", snapshot_path, "--budget", "60", "--delay", "0"],
                   "crawl.py [{}]".format(name))

        findings_paths, findings = [], {}
        for skill in SUB_SKILLS:
            out_path = os.path.join(out_dir, "{}.findings.json".format(skill))
            args = [os.path.join(ROOT, "skills", skill, "scripts", "check.py"),
                    "--snapshot", snapshot_path, "--out", out_path]
            if skill == "freshness-corroboration-audit":
                args += ["--now", REFERENCE_DATE]
            if no_network:
                args.append("--no-network")
            run_script(args, "{} [{}]".format(skill, name))
            findings_paths.append(out_path)
            with open(out_path, encoding="utf-8") as handle:
                findings[skill] = json.load(handle)

        report_json = os.path.join(out_dir, "report.json")
        report_md = os.path.join(out_dir, "report.md")
        run_script([os.path.join(SCRIPTS, "compose_report.py"),
                    "--snapshot", snapshot_path, "--findings"] + findings_paths +
                   ["--out-json", report_json, "--out-md", report_md,
                    "--audited-at", REFERENCE_TIMESTAMP],
                   "compose_report.py [{}]".format(name))

        with open(snapshot_path, encoding="utf-8") as handle:
            snapshot = json.load(handle)
        with open(report_json, encoding="utf-8") as handle:
            report = json.load(handle)
        with open(report_md, encoding="utf-8") as handle:
            markdown = handle.read()

        return server, AuditResult(name, server.base_url, out_dir, snapshot,
                                   findings, report, markdown)
    except Exception:
        server.__exit__(None, None, None)
        raise


@pytest.fixture(scope="session")
def audit(tmp_path_factory):
    """Factory: `audit("good-site")` -> AuditResult, cached for the session."""
    cache, servers = {}, []

    def _audit_site(name, no_network=False):
        key = (name, no_network)
        if key not in cache:
            out_dir = str(tmp_path_factory.mktemp(name.replace("-", "_")))
            server, result = _audit(name, out_dir, no_network=no_network)
            servers.append(server)
            cache[key] = result
        return cache[key]

    yield _audit_site

    for server in servers:
        server.__exit__(None, None, None)


@pytest.fixture(scope="session")
def golden():
    """Factory: `golden("good-site")` -> the expectations file for that fixture."""
    def _load(name):
        with open(os.path.join(GOLDEN, "{}.json".format(name)), encoding="utf-8") as handle:
            return json.load(handle)
    return _load


def all_fixture_names():
    return sorted(
        entry for entry in os.listdir(FIXTURES)
        if os.path.isdir(os.path.join(FIXTURES, entry)) and not entry.startswith((".", "_"))
    )
