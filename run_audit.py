#!/usr/bin/env python3
"""Convenience runner: crawl a site, run all six sub-skills, emit the report.

This is the executable form of the procedure written out in
skills/audit-orchestrator/SKILL.md. An agent following that file by hand
performs the same steps in the same order and produces the same report.

    python run_audit.py https://example.com
    python run_audit.py example.com --out-dir ./out --format html --render

Read-only throughout: GET and HEAD only, no forms, no cookies the site did not
set, nothing under /cart, /checkout, /login or /admin, and robots.txt respected.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(ROOT, "skills", "audit-orchestrator", "scripts")
sys.path.insert(0, SCRIPTS)

from audit_common import WALL_CLOCK_BUDGET, eprint  # noqa: E402

# Order matters: earlier skills own overlapping observations at the dedup step.
SUB_SKILLS = [
    "crawl-access-audit",
    "render-readability-audit",
    "structured-data-audit",
    "fact-extractability-audit",
    "freshness-corroboration-audit",
    "engagement-audit",
]


def run_step(command, label):
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT)
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        raise SystemExit("{} failed with exit code {}".format(label, result.returncode))
    return elapsed


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Recommend-only. This tool never writes to the site it audits.")
    parser.add_argument("target", help="site URL or bare hostname, e.g. https://example.com")
    parser.add_argument("--out-dir", default="out", help="where to write the snapshot and report")
    parser.add_argument("--format", choices=("md", "html", "both"), default="md",
                        help="human-readable report format (report.json is always written)")
    parser.add_argument("--render", action="store_true",
                        help="add a Playwright pass if Playwright is installed")
    parser.add_argument("--no-network", action="store_true",
                        help="skip every extra request the sub-skills would make")
    parser.add_argument("--budget", type=float, default=WALL_CLOCK_BUDGET,
                        help="wall-clock seconds for the crawl (default %(default)s)")
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--now", help="reference date YYYY-MM-DD for freshness checks")
    parser.add_argument("--keep-intermediates", action="store_true",
                        help="keep each skill's raw findings file")
    args = parser.parse_args(argv)

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    snapshot = os.path.join(out_dir, "snapshot.json")
    python = sys.executable
    started = time.monotonic()

    eprint("[1/3] crawling {} (budget {:.0f}s, max {} pages)".format(
        args.target, args.budget, args.max_pages))
    crawl_command = [python, os.path.join(SCRIPTS, "crawl.py"), args.target,
                     "--out", snapshot, "--budget", str(args.budget),
                     "--max-pages", str(args.max_pages)]
    if args.render:
        crawl_command.append("--render")
    run_step(crawl_command, "crawl")

    eprint("[2/3] running {} sub-skills".format(len(SUB_SKILLS)))
    findings_files = []
    for skill in SUB_SKILLS:
        out_path = os.path.join(out_dir, "{}.findings.json".format(skill))
        command = [python, os.path.join(ROOT, "skills", skill, "scripts", "check.py"),
                   "--snapshot", snapshot, "--out", out_path]
        if args.no_network:
            command.append("--no-network")
        if skill == "freshness-corroboration-audit" and args.now:
            command += ["--now", args.now]
        run_step(command, skill)
        findings_files.append(out_path)

    eprint("[3/3] composing the report")
    compose_command = [python, os.path.join(SCRIPTS, "compose_report.py"),
                       "--snapshot", snapshot, "--findings"] + findings_files + [
                          "--out-json", os.path.join(out_dir, "report.json"),
                          "--out-md", os.path.join(out_dir, "report.md")]
    if args.format in ("html", "both"):
        compose_command += ["--out-html", os.path.join(out_dir, "report.html")]
    run_step(compose_command, "compose_report")

    if not args.keep_intermediates:
        for path in findings_files:
            try:
                os.remove(path)
            except OSError:
                pass

    elapsed = time.monotonic() - started
    eprint("")
    eprint("done in {:.1f}s. Reports written to {}:".format(elapsed, out_dir))
    eprint("  report.json  machine-readable findings")
    eprint("  report.md    the one to read")
    if args.format in ("html", "both"):
        eprint("  report.html  the one to send to someone else")
    if elapsed > 300:
        eprint("WARNING: the run took over 5 minutes; lower --budget or --max-pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
