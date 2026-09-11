#!/usr/bin/env python3
"""Convenience runner: crawl a site, run all six sub-skills, emit the report.

This is the executable form of the procedure written out in
skills/audit-orchestrator/SKILL.md. An agent following that file by hand
performs the same steps in the same order and produces the same report.

    python run_audit.py https://example.com
    python run_audit.py example.com --out-dir ./out --format html

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

from audit_common import (  # noqa: E402
    DEADLINE_OVERSHOOT_SECONDS,
    MAX_PAGES, NO_TIME_LEFT_SECONDS, REQUEST_TIMEOUT, REQUEST_TOTAL_TIMEOUT,
    RUN_WALL_CLOCK_LIMIT, SUB_SKILLS, WALL_CLOCK_BUDGET, eprint, read_json,
    write_json,
)

# Order matters: earlier skills own overlapping observations at the dedup step,
# so this is the same tuple `compose_report` deduplicates by. Imported rather
# than repeated: four copies of it were in the tree, and reordering any one
# would have silently changed which skill owns a shared root cause.

# What composing the report costs, held back from the deadline handed to the
# probe phase.
#
# Without it the ceiling was not a ceiling. Measured on a real retailer whose
# crawl uses its whole 240 s budget: the probes were given every remaining
# second up to 285, spent them, and compose then ran on top - 289.2 s and
# 289.7 s on two runs, past a limit whose own comment says it exists so the
# five-minute promise holds. Compose took about five seconds there; twelve
# leaves room for a report with far more findings in it.
COMPOSE_RESERVE_SECONDS = 12.0

# The least wall clock any one phase is given, and the reason it is not thirty.
#
# The sub-skill loop used to floor its kill-timeout at MIN_SUB_SKILL_SECONDS,
# which is a floor of 30 s per skill *even when the clock was already spent*:
# six skills, 180 s, all of it past a ceiling the run had already reached. A
# skill in that state has been switched to `--no-network`, so all it does is
# read the snapshot and write a JSON file. Every floor is subtracted from the
# crawl's own budget below, so the floors cannot push the run past the limit.
#
# Two seconds, on a measurement of 0.85 s for the slowest of the six over a
# sixty-page snapshot - taken on this suite's fixtures, whose pages carry a
# paragraph or two each. Re-measured on a snapshot the size a real sixty-page
# crawl produces (2.2 MB; the page records hold extracted text and every link
# on the page, and no raw HTML): the three skills that only read the snapshot
# take 0.27, 0.58 and 1.37 s. Only those three can ever be floored here - the
# other three make requests, and the arithmetic below never hands one of them
# less than MIN_SUB_SKILL_SECONDS - so 1.37 s is the number this has to cover,
# and 2.0 covered it by 46%. Three seconds is twice the measurement and holds
# on a snapshot half again as large; the crawl pays 6 s for it.
#
# It is also the gap between what a sub-skill is told it may spend and the
# point at which it is killed, for the same measured reason: a skill tests its
# deadline between requests and then still has to write that file.
MIN_STEP_SECONDS = 3.0

# Below this a sub-skill is run without network access rather than given a
# slice of a budget that is already gone. It still reads the snapshot and still
# reports; what it stops doing is spending requests the clock cannot afford.
#
# The same number decides, at the other end, whether a sub-skill blames the
# `--no-network` flag or a spent clock for the requests it did not make. A
# sub-skill is handed only the flag, so it reads the threshold back to tell the
# two apart, and the inference is wrong the moment the two numbers differ.
# Imported under the name the decision reads better under here; the one
# definition lives in `audit_common` as `NO_TIME_LEFT_SECONDS`.
MIN_SUB_SKILL_SECONDS = NO_TIME_LEFT_SECONDS

# The three that make requests of their own after the crawl. They are the only
# ones that can overrun the clock, so they are the only ones handed a deadline.
NETWORK_SUB_SKILLS = frozenset({
    "crawl-access-audit",
    "freshness-corroboration-audit",
    "engagement-audit",
})

# What one wedged response can cost a phase beyond its own deadline.
#
# Every deadline below this file is advisory to a socket that is already open,
# and the size of that gap is `Fetcher`'s to state rather than this file's to
# guess. `DEADLINE_OVERSHOOT_SECONDS` is the bound it publishes and pins.
#
# This number was 2.0 for a sub-skill - the same MIN_STEP_SECONDS that pays for
# writing a JSON file - and 2.0 is not a margin against a stall. Measured on a
# real run, that costs this much: `engagement-audit` was told 83 s, killed at
# 85 s, and a sixth of the audit was thrown away because one response outlived
# the advisory deadline the skill was honouring.
#
# It was then 40 s - `REQUEST_TOTAL_TIMEOUT + REQUEST_TIMEOUT`, one whole
# dribbling response plus one silent one - because `Fetcher` could begin a
# request with a second left and run 30 s past it. It no longer can: the socket
# timeout and the elapsed cap are both clamped to the clock the phase has left,
# measured at +0.03 s over a 12-second budget and +3.02 s over a two-second one,
# against +18.11 s and +28.08 s before. So the margin is the bound `Fetcher`
# promises, and the 30 s this file used to hold back for a limit that no longer
# exists goes to the crawl - about ten more pages on a mid-speed site.
SKILL_WEDGE_MARGIN_SECONDS = DEADLINE_OVERSHOOT_SECONDS

# The crawl's is twice a sub-skill's. The second of its two requests - the
# meta-refresh it may follow - is now refused outright once the deadline has
# passed, so one bound would do; but this number only sets a kill timeout the
# crawl is not expected to reach, and paying for a second one costs nothing.
#
# It used to be a flat 120 s picked next to nothing, which is why the crawl
# could be killed at 360 seconds under a 285-second run.
CRAWL_WEDGE_MARGIN_SECONDS = 2 * SKILL_WEDGE_MARGIN_SECONDS

# What `crawl.py` still has to do after its own deadline has passed: strip
# site-wide boilerplate off every page, build the snapshot and write it. Its
# remaining network work - the one homepage retry and the HEAD probe - is
# gated by the same deadline inside `Fetcher`, so none of it opens a socket.
#
# Measured, on a site answering in six seconds a page: the crawl was told 171 s
# and the process took 174.0 - three seconds for the request that was in flight
# when the deadline passed, plus the stripping, the write and the interpreter.
# So the tail is that work plus the one request that can still be in flight
# when the deadline passes. It was ten seconds while the wedge above was 40,
# because the crawl ceiling clipped the wedge long before the kill time could
# be reached. At a 10 s wedge it no longer does - a crawl told 142 s can end a
# wedged fetch at 152 - so the tail carries the bound explicitly and adds the
# three seconds measured for the stripping, the write and the interpreter.
#
# Beyond that there is nothing left to guess at: `crawl.py` writes a readable
# snapshot every few pages, so a crawl that does overrun is stopped at the
# boundary and the six skills audit the pages it had reached.
CRAWL_TAIL_SECONDS = DEADLINE_OVERSHOOT_SECONDS + 3.0


def step_floor(skill):
    """The least wall clock this one sub-skill is worth starting with.

    Two different numbers, because the six are not the same kind of phase. The
    three that only read the snapshot need `MIN_STEP_SECONDS` and no more. The
    three that make requests of their own need enough to make a request at all,
    which is what `MIN_SUB_SKILL_SECONDS` already means everywhere else in this
    file: below it, the orchestrator stops handing out a network budget.

    The extra `MIN_STEP_SECONDS` on top is what a skill needs to write its JSON
    file after its last request.

    What this floor is and is not. It guarantees every skill *starts*, which is
    the promise that matters: a skill with no network budget still reads the
    snapshot, declines its network checks with a reason and reports. It buys
    each of the three that make requests their thirty seconds of probing only
    while nothing ahead of them has overrun - `SKILL_WEDGE_MARGIN_SECONDS` is
    charged to whichever phase is inside a wedged response, and comes out of
    the probing of the skills behind it. Losing the probing is a check that
    says it could not reach its targets, which this report already knows how to
    print; losing the skill is a hole it can only apologise for.
    """
    if skill in NETWORK_SUB_SKILLS:
        return MIN_SUB_SKILL_SECONDS + MIN_STEP_SECONDS
    return MIN_STEP_SECONDS


# What the whole probe phase is owed, held back from the crawl's own budget.
#
# The crawl was allowed to spend the entire run and the probes got the
# remainder, which on a slow site is nothing: one measured run spent 245 of the
# 261 s the crawl was permitted, so all six sub-skills were switched to
# `--no-network` and the AI-user-agent probe, the profile-link resolution, the
# entity lookup and the agent-file fetch were all skipped. The crawl is not
# more important than the checks it exists to feed.
#
# Every sub-skill's floor, added up, is the split: the crawl gets what is left
# after the probe phase is paid, so the first sub-skill starts with its own
# floor intact even when the crawl uses every second it was given.
#
# Plus one wedged response, once. The floors summed to exactly what six skills
# need if nothing at all overruns, so the run had not one second of slack in
# it. Measured, on a site answering in six seconds a page: the crawl was told
# 171 s and the process took 174.0. Those three seconds dropped the first
# sub-skill from the 30 s at which this run still hands out a network budget to
# 27, so `crawl-access-audit` - the skill that reads robots.txt, the sitemaps
# and the status codes - ran with no requests at all, and the last skill was
# killed. Three seconds of slop cost two of the six their work.
#
# One wedge rather than six, because only the phase currently running can be
# inside a wedged response. A later skill that loses its network budget to an
# earlier overrun still runs and still reports: it is the *probing* that
# degrades under pressure, never the coverage.
PROBE_PHASE_RESERVE_SECONDS = (sum(step_floor(name) for name in SUB_SKILLS)
                               + SKILL_WEDGE_MARGIN_SECONDS)

# The check name a skill that never ran is recorded under. It is registered as
# a check *and* declined, so the appendix's four-bucket arithmetic still adds
# up and the reader sees the gap in the same place they read every other reason
# a check stayed quiet.
UNRUN_CHECK = "skill-did-not-run"


class PhaseFailed(Exception):
    """One phase did not finish, and what to tell the reader about it.

    Raised rather than exiting, because whether a phase is fatal is not the
    phase's decision. A sub-skill that overran is a gap in the report; the
    crawl producing no snapshot is the end of the run. Both used to exit 1.
    """

    def __init__(self, label, reason, elapsed):
        Exception.__init__(self, "{} {}".format(label, reason))
        self.label = label
        self.reason = reason
        self.elapsed = elapsed


def run_step(command, label, timeout=None):
    """Run one phase, bounded.

    Every deadline inside the crawl and the sub-skills is advisory: they are
    tested between requests, so a phase that wedges on one socket is not bound
    by any of them. Nothing here enforced the five-minute promise the report
    makes in its own appendix. This does, and it says which phase ran over
    rather than dying silently.
    """
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PhaseFailed(label, "was stopped after {:.0f}s, the share of this audit's "
                                 "wall-clock ceiling that was left for it".format(timeout),
                          time.monotonic() - started)
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        raise PhaseFailed(label, "exited with code {}".format(result.returncode), elapsed)
    return elapsed


def unrun_result(skill, reason):
    """A sub-skill's result file for a sub-skill that produced none.

    Same shape as `SkillResult.to_dict`, so `compose_report` reads it without
    knowing anything went wrong: one check registered, that same check declined
    with a reason. It lands in the report's "Checks that did not apply, and
    why" list, which is where every other unanswered question already goes.

    The wording says unexamined rather than clean on purpose. A missing skill
    that reads as silence is worse than a late report, because silence in this
    report means "checked, nothing wrong".
    """
    reason = ("this skill {}, so none of its checks ran against this site. What it "
              "examines is therefore unreported rather than clean - treat it as a gap in "
              "this audit, not as a pass. Re-run with a larger --budget, with "
              "--no-network, or against a faster host to fill it in".format(reason))
    # `skill_did_not_run` says so outright, rather than leaving the reader of
    # this file to recognise a check name. The composer routes a dropped skill
    # into the report's "Questions this audit could not answer" section, and
    # keying that on a magic string couples two files through a literal that
    # nothing enforces - rename the check and the routing silently stops. The
    # check name stays as a fallback so an older result file still works.
    return {
        "skill": skill,
        "findings": [],
        "checks_run": [UNRUN_CHECK],
        "fired_checks": [],
        "not_applicable": [{"check": UNRUN_CHECK, "skill": skill, "reason": reason,
                            "did_not_run": True}],
        "extra_requests_made": 0,
        "signals": {},
        "skill_did_not_run": True,
    }


def snapshot_if_usable(path):
    """The snapshot this run wrote, or None if there is nothing to audit.

    Called only after the crawl failed. `crawl.py` now writes a readable
    snapshot every few pages, through a temporary file and one rename, so a
    crawl stopped at the reserve boundary usually leaves the pages it had
    reached rather than nothing - which is what makes stopping the crawl the
    cheap way to hold the ceiling rather than a way to lose the run.

    The check stays because a crawl can still leave no file at all: one killed
    before its first checkpoint, or one that never reached a page. Reading a
    file that is not a snapshot would fail somewhere deep in a sub-skill
    instead of here.
    """
    try:
        snapshot = read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(snapshot, dict) or not snapshot.get("pages"):
        return None
    return snapshot


def note_on_snapshot(path, snapshot, note):
    """Record in the snapshot that the crawl behind it was cut short.

    `crawl.notes` is already rendered in the report's appendix, one bullet per
    note, so a reader learns the pages below are a partial crawl from the same
    document that describes them rather than from a console message they never
    saw.
    """
    crawl = snapshot.setdefault("crawl", {})
    notes = crawl.setdefault("notes", [])
    if isinstance(notes, list):
        notes.append(note)
        write_json(path, snapshot)


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
                        help="require the Playwright pass; the run says so if it cannot happen")
    parser.add_argument("--no-render", action="store_true",
                        help="skip the Playwright pass even if Playwright is installed")
    parser.add_argument("--no-network", action="store_true",
                        help="skip every extra request the sub-skills would make")
    parser.add_argument("--budget", type=float, default=WALL_CLOCK_BUDGET,
                        help="wall-clock seconds for the crawl (default %(default)s; lowered "
                             "if it would leave the sub-skills nothing to probe with)")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES,
                        help="page ceiling for the crawl (default %(default)s)")
    parser.add_argument("--now", help="reference date YYYY-MM-DD for freshness checks")
    parser.add_argument("--keep-intermediates", action="store_true",
                        help="keep each skill's raw findings file")
    args = parser.parse_args(argv)

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    snapshot = os.path.join(out_dir, "snapshot.json")
    python = sys.executable
    started = time.monotonic()

    # A caller who raises --budget past the five-minute ceiling has chosen a
    # longer run, so the ceiling moves with them rather than silently capping
    # the number they asked for.
    # `max(285, budget * 1.2)` gave the *default* run 288 seconds, because the
    # default budget is 240 and 240 x 1.2 is 288 - so the branch written for a
    # caller who deliberately raises the budget was taking effect when nobody
    # had. The proportional allowance applies only above the default.
    hard_limit = (RUN_WALL_CLOCK_LIMIT if args.budget <= WALL_CLOCK_BUDGET
                  else args.budget * 1.2)
    # This used to be computed *after* the crawl returned, so the one phase it
    # could not bound was the longest one: the crawl was killed at
    # `budget + 120`, 360 s against a 285 s ceiling, and the worst case for the
    # whole run - 360 crawl, 6 x 30 s of sub-skill floor, 120 s of compose -
    # came to 660 s under a tool that advertises 285.
    #
    # The three caps below are now derived from `hard_limit` rather than picked
    # independently, and they add back up to it exactly:
    #
    #     crawl_ceiling + PROBE_PHASE_RESERVE_SECONDS
    #                   + COMPOSE_RESERVE_SECONDS == hard_limit
    #
    # so no combination of slow phases can put the run past the number the
    # report prints in its own appendix. `tests/test_run_clock_and_coverage.py`
    # recomputes that sum from these constants, so the arithmetic cannot drift
    # back apart without a red test.
    #
    # `crawl_ceiling` is the point at which the crawl is killed. It used to be
    # `probe_deadline - 6 x MIN_STEP_SECONDS` - 261 s at the default ceiling -
    # which reserved for the six sub-skills only the two seconds each needs to
    # read the snapshot and write a file, and left the crawl free to eat the
    # ninety seconds the three that make requests need to make any. So a crawl
    # that overran took the probing with it, and that is the shape of a real
    # run: 274.5 s inside a 300 s promise, with `crawl-access-audit`
    # run with no network work at all and `engagement-audit` killed outright.
    #
    # It is now the reserve boundary itself. Below it, cutting a phase cuts the
    # crawl - fewer pages, read by all six skills - rather than a skill, and
    # every finding already names the sample it was read from, so a shorter
    # crawl is a gap the report states and a missing skill is one it cannot.
    probe_deadline = hard_limit - COMPOSE_RESERVE_SECONDS
    crawl_ceiling = probe_deadline - PROBE_PHASE_RESERVE_SECONDS

    # What the crawl is *told* to spend, as opposed to the point at which it is
    # killed. The two were the same number, so the crawl was entitled to the
    # whole run and the probes inherited the remainder - nothing, on any site
    # slow enough to use its budget.
    #
    # At the default ceiling: 285 - 12 for compose leaves 273 for crawl and
    # probes together; the probe phase is owed `PROBE_PHASE_RESERVE_SECONDS`,
    # 118 s as the step floors stand; so the crawl is killed at 155 and told
    # 142 rather than 240. The run prints both numbers on its first lines, and
    # README and the orchestrator's SKILL.md state the same two.
    #
    # A smaller budget buys fewer pages than 171 did, and that is the trade being made
    # deliberately. `crawl.py` plans its page count from the budget and the
    # measured cost of a page, so the cost falls only where a page is slow: a
    # site answering in under a second still reaches the sixty-page ceiling and
    # is not shortened at all, one answering in 1.65 s a page - sixty pages in
    # the 99 s measured when the ceiling was chosen - plans 43 rather than 60,
    # and one answering in six and a half seconds a page plans 10 rather than 16.
    #
    # Against that, measured on one site answering in six seconds a page, the
    # same command before and after: 29 pages, five of the six sub-skills, one
    # of the three that make requests reaching the network, 11 findings,
    # 273.5 s - against 21 pages, all six skills, all three reaching the
    # network, 13 findings, 231.7 s. Eight pages bought a third of the audit
    # back, and the run finished 42 seconds sooner.
    #
    # Fewer pages read by all six is a gap every finding already states - each
    # one names the sample it was read from - and a missing skill is one the
    # report can only apologise for.
    crawl_budget = max(0.0, min(args.budget, crawl_ceiling - CRAWL_TAIL_SECONDS))

    # Say up front whether the browser pass will happen. It changes the
    # confidence of the JavaScript findings, and previously the only way to
    # learn which mode you got was to read `render_mode` in the finished JSON.
    if args.no_render:
        rendering = "rendering off (--no-render)"
    else:
        try:
            import playwright  # noqa: F401
            rendering = "Playwright found, rendering on"
        except ImportError:
            rendering = ("Playwright not installed, static analysis only - a property of this "
                         "machine, not a defect in the site")
    eprint("[1/3] crawling {} (budget {:.0f}s, max {} pages; {})".format(
        args.target, crawl_budget, args.max_pages, rendering))
    # Why the crawl is not getting the number the caller asked for. Without
    # this the only visible sign was a smaller page count, and the obvious
    # reading of that is that the tool is slower than it says - rather than
    # that it is spending the difference on the analysis.
    if crawl_budget < args.budget:
        eprint("      ({:.0f}s of the {:.0f}s ceiling is held for the {} sub-skills and "
               "the report, so a site too slow for both is read less deeply rather "
               "than analysed less widely)".format(
                   PROBE_PHASE_RESERVE_SECONDS + COMPOSE_RESERVE_SECONDS,
                   hard_limit, len(SUB_SKILLS)))
    crawl_command = [python, os.path.join(SCRIPTS, "crawl.py"), args.target,
                     "--out", snapshot, "--budget", "{:.1f}".format(crawl_budget),
                     "--max-pages", str(args.max_pages)]
    # The rendered pass is on by default and degrades to a note when Playwright
    # is absent, so any machine that has it gets the measured JavaScript gap
    # rather than the inferred one without anyone having to know a flag exists.
    if args.no_render:
        crawl_command.append("--no-render")
    elif args.render:
        crawl_command.append("--render")
    # A snapshot left in this --out-dir by an earlier run is indistinguishable
    # from one this crawl wrote. Removed first, so that "a snapshot exists"
    # below cannot silently mean "one from last week": a stopped crawl would
    # otherwise be composed into a confident report about pages fetched at some
    # other time, with nothing anywhere saying so.
    #
    # The half-written temporary too. `crawl.py` checkpoints through
    # `snapshot.json.partial` and renames it into place; a crawl killed between
    # the write and the rename leaves that file behind, and one left by an
    # earlier run would sit in the output directory of this one.
    for stale in (snapshot, snapshot + ".partial"):
        if os.path.exists(stale):
            os.remove(stale)
    # The crawl gets its own budget plus the margin a single wedged request can
    # cost, or whatever the run ceiling leaves, whichever is smaller - and at
    # the default ceiling the second one is smaller, so the kill lands at 131 s
    # against a crawl told to stop itself at 121. Ten seconds is what stripping
    # boilerplate, building the snapshot and writing it were measured to cost
    # after the deadline passes, so a crawl that stops itself is never touched.
    #
    # Being killed here is no longer the end of the run: `crawl.py` writes a
    # readable snapshot as it goes, so what the crawl had reached is on disk
    # and the six skills audit that. Reserving the probe phase would be an
    # empty promise otherwise - the crawl would still be holding the clock at
    # the moment it was stopped, and stopping it would throw the run away.
    try:
        crawl_seconds = run_step(
            crawl_command, "crawl",
            timeout=min(crawl_budget + CRAWL_WEDGE_MARGIN_SECONDS, crawl_ceiling))
    except PhaseFailed as failure:
        # The one phase whose loss is genuinely fatal, and only in one case:
        # there is no snapshot. Everything after this reads the snapshot and
        # nothing after this can reach the site, so with no snapshot there is
        # no site to describe and a report would be a page of blank sections.
        #
        # A crawl that failed but left a snapshot is survivable, and is treated
        # as one: the pages it did read are audited and the report says the
        # crawl was cut short. That is a partial crawl, which this audit
        # already knows how to describe - every finding states the sample it
        # was read from.
        crawl_seconds = failure.elapsed
        partial = snapshot_if_usable(snapshot)
        if partial is None:
            raise SystemExit(
                "the crawl {} and left no snapshot, so there is nothing to report on. "
                "Every later phase reads the snapshot and none of them can reach the "
                "site. Lower --max-pages or --budget, or re-run against a faster "
                "host.".format(failure.reason))
        eprint("  (the crawl {}; auditing the {} page(s) it did read)".format(
            failure.reason, len(partial.get("pages") or [])))
        note_on_snapshot(snapshot, partial,
                         "the crawl {}, so these pages are what it had reached by then "
                         "rather than everything this audit would normally read".format(
                             failure.reason))
    # A crawl killed between a checkpoint's write and its rename leaves the
    # temporary behind. Removed here rather than at the end, so it is gone
    # whether or not the phases after this one finish.
    if os.path.exists(snapshot + ".partial"):
        try:
            os.remove(snapshot + ".partial")
        except OSError:
            pass

    eprint("[2/3] running {} sub-skills".format(len(SUB_SKILLS)))
    findings_files = []
    skill_seconds = {}
    dropped = []
    for index, skill in enumerate(SUB_SKILLS):
        out_path = os.path.join(out_dir, "{}.findings.json".format(skill))
        command = [python, os.path.join(ROOT, "skills", skill, "scripts", "check.py"),
                   "--snapshot", snapshot, "--out", out_path]
        # Whatever is left of the run, minus the floor every skill after this
        # one is still owed. A crawl that finished in 20 seconds leaves the
        # probes plenty; a crawl that used its whole budget leaves them little,
        # and they report the targets they could not reach as unchecked rather
        # than guessing.
        #
        # Subtracting the floors ahead is what makes the phase bounded rather
        # than merely shrinking: without it the first skill could spend every
        # remaining second and the five after it would still each get their
        # floor on top. With it, `elapsed + MIN_STEP_SECONDS * skills_left`
        # never rises, so the phase ends at `probe_deadline` whichever branch
        # each skill takes.
        #
        # `left` is one number with two uses: it is what this skill is killed
        # at, and it is what the budget handed down is derived from. They used
        # to be computed separately and the advisory one was the larger, so a
        # skill could plan requests into seconds the parent was never going to
        # let it have.
        #
        # The floors ahead are per skill, not one number for all six: a
        # snapshot-only skill is owed two seconds and one that makes requests is
        # owed enough to make them. Owing every skill the same two seconds is
        # what let the crawl, and then each earlier sub-skill in turn, spend the
        # network allowance of every skill behind it.
        left = (probe_deadline - (time.monotonic() - started)
                - sum(step_floor(name) for name in SUB_SKILLS[index + 1:]))
        # Told less than it is killed at, by what one wedged response costs
        # plus the second it takes to write the result file afterwards.
        #
        # The gap used to be MIN_STEP_SECONDS alone, two seconds, against a
        # response that can hold a request open for forty. So a skill that
        # honoured its advisory deadline exactly was still killed for the one
        # request that outlived it, and one real run lost `engagement-audit`
        # that way at 85 s. A skill is now killed only if it
        # ignores the deadline it was handed, which is a defect in the skill
        # rather than a property of the site it was pointed at.
        #
        # Only the three that make requests can wedge; the other three are
        # given no budget to read and cannot outlive a deadline they never see.
        wedge = SKILL_WEDGE_MARGIN_SECONDS if skill in NETWORK_SUB_SKILLS else 0.0
        budget = max(0.0, left - wedge - MIN_STEP_SECONDS)
        if skill in NETWORK_SUB_SKILLS:
            command += ["--time-budget", "{:.1f}".format(budget)]
        if args.no_network:
            command.append("--no-network")
        if skill == "freshness-corroboration-audit" and args.now:
            command += ["--now", args.now]
        # A skill with no time left is not given thirty seconds to spend: it is
        # run with `--no-network`, which makes it read the snapshot and decline
        # its network checks with a reason, and the report says the run was cut
        # short rather than going quietly over the five minutes it promises.
        #
        # The test is on what the skill was told, not on what is left: the
        # skill reads the same threshold back to decide whether to blame the
        # flag or a spent clock, and it can only read the number it was handed.
        #
        # Only the three that make requests. The other three accept the flag
        # for interface parity and never read it, so handing it to them printed
        # a console line saying a skill would make no extra requests when it
        # was never going to make any.
        if (skill in NETWORK_SUB_SKILLS and budget < MIN_SUB_SKILL_SECONDS
                and "--no-network" not in command):
            command.append("--no-network")
            eprint("  (out of time: {} runs without extra requests)".format(skill))
        # `max(30.0, left)` was the kill-timeout too, so six skills could add
        # three minutes after the clock was already spent - and add them on a
        # run that had switched them all to snapshot-only reads, which take
        # under a second each.
        try:
            skill_seconds[skill] = run_step(
                command, skill, timeout=max(MIN_STEP_SECONDS, left))
        except PhaseFailed as failure:
            # Survivable, all six of them. A sub-skill that overran or crashed
            # costs the report the checks that skill owns and nothing else;
            # exiting here threw away a finished crawl and five finished skills
            # over the sixth, which is a worse outcome than a report that names
            # what is missing. The stand-in result puts the gap in the report's
            # own appendix, where a reader who never sees this console will
            # find it.
            skill_seconds[skill] = failure.elapsed
            dropped.append(skill)
            write_json(out_path, unrun_result(skill, failure.reason))
            eprint("  ({} {}; the report will say so)".format(skill, failure.reason))
        findings_files.append(out_path)

    eprint("[3/3] composing the report")
    compose_command = [python, os.path.join(SCRIPTS, "compose_report.py"),
                       "--snapshot", snapshot, "--findings"] + findings_files + [
                          "--out-json", os.path.join(out_dir, "report.json"),
                          "--out-md", os.path.join(out_dir, "report.md")]
    if args.format in ("html", "both"):
        compose_command += ["--out-html", os.path.join(out_dir, "report.html")]
    # What is left of the ceiling, floored at the reserve held back for this
    # phase. A flat 120 s was two minutes nobody had counted: it ran after the
    # ceiling had already been reached and pushed the worst case past the
    # five-minute promise on its own. The floor is what the reserve is for, and
    # the loop above cannot eat into it.
    #
    # Fatal, and the only other phase that is. Composing the report *is* the
    # output: there is no degraded form of it to fall back to, and a run that
    # gets here has already written every input it needs, so --keep-intermediates
    # plus a direct call to compose_report.py recovers the run without crawling
    # the site again.
    try:
        compose_seconds = run_step(
            compose_command, "compose_report",
            timeout=max(COMPOSE_RESERVE_SECONDS, hard_limit - (time.monotonic() - started)))
    except PhaseFailed as failure:
        raise SystemExit(
            "composing the report {}. The snapshot and every sub-skill's findings file "
            "are still in {} - they are only deleted once a report exists - so "
            "compose_report.py can be run against them directly rather than crawling "
            "the site again.".format(failure.reason, out_dir))

    if not args.keep_intermediates:
        for path in findings_files:
            try:
                os.remove(path)
            except OSError:
                pass

    elapsed = time.monotonic() - started
    # Where the five minutes went. Without it the only way to find out which
    # phase is slow on a given site was to time the phases by hand, and the
    # answer decides whether to lower `--budget` or `--max-pages`.
    eprint("")
    eprint("time: crawl {:.0f}s, sub-skills {:.0f}s ({}), compose {:.0f}s".format(
        crawl_seconds, sum(skill_seconds.values()),
        ", ".join("{} {:.0f}s".format(name.split("-")[0], seconds)
                  for name, seconds in sorted(skill_seconds.items(),
                                              key=lambda kv: -kv[1])[:3]),
        compose_seconds))
    # Named on the way out as well as in the report, because the number of
    # findings is the first thing anyone compares between two runs and a run
    # missing a skill produces fewer of them for a reason that is not the site.
    if dropped:
        eprint("")
        eprint("{} of {} sub-skills did not run: {}. The report lists each one under "
               "\"Checks that did not apply, and why\"; what they examine is unreported, "
               "not clean.".format(len(dropped), len(SUB_SKILLS), ", ".join(dropped)))
    eprint("done in {:.1f}s. Reports written to {}:".format(elapsed, out_dir))
    eprint("  report.json  machine-readable findings")
    eprint("  report.md    the one to read")
    if args.format in ("html", "both"):
        eprint("  report.html  the one to send to someone else")
    if elapsed > RUN_WALL_CLOCK_LIMIT:
        eprint("WARNING: the run took {:.0f}s, over the {:.0f}s this audit promises. "
               "Lower --budget or --max-pages.".format(elapsed, RUN_WALL_CLOCK_LIMIT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
