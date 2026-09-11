"""What the run does when one phase does not finish.

A measured run: 273 seconds, exit code 1, no `report.json` and no `report.md`,
on the message that one sub-skill had passed its ceiling and been stopped. The
crawl had finished. Five of the six sub-skills had finished. All of it was
thrown away because the sixth ran long, and the identical command half an hour
later produced fifteen findings in 219 seconds.

Aborting is a worse failure than a late report, and it is a much worse failure
than a report that names what is missing. So the rule these tests hold is:

  * a sub-skill that overruns or crashes costs the report that skill's checks
    and nothing else, and the report says which ones and why;
  * the crawl is fatal only when it leaves no snapshot, because there is then
    no site to describe;
  * composing the report is fatal, because it is the output;
  * and none of that may become a way to run past the ceiling the audit
    advertises - a phase that fails still only ever spends its own cap.

The second half holds the split between the crawl and the probes that follow
it. The crawl used to be allowed the whole run and the probes got what was
left, which on a slow site is nothing: one run spent 245 of the 261 seconds the
crawl was permitted and every sub-skill was switched to `--no-network`, so the
user-agent probe, the profile-link resolution, the entity lookup and the
agent-file fetch were all skipped on the site that most needed them.

Holding a reserve back from the crawl was the first half of that split and it
was not enough, because the reserve had no slack in it. Reproduced against a
site answering in six seconds a page: the crawl was told 171 s and took 174,
and those three seconds cost `crawl-access-audit` its network budget and
`engagement-audit` its life - stopped at 94 s. So the reserve now also carries
one wedged response, the margin between what a skill is told and the point at
which it is stopped covers one, and where a phase must still give way it is the
crawl. Fewer pages read by all six is a gap every finding already states; a
skill that did not run is one the report can only apologise for.

Every host in this file is invented and every address is fictional.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import ROOT, SCRIPTS, SUB_SKILLS, REFERENCE_TIMESTAMP

sys.path.insert(0, SCRIPTS)

from audit_common import RUN_WALL_CLOCK_LIMIT  # noqa: E402

INVENTED_TARGET = "https://an-invented-host.test"

# 60 pages of a large real site, measured at 99 s while the page ceiling was
# being chosen - 1.65 s a page, which is what the crawl's budget is read
# against. The budget has to stay above it. The page count is declared before
# the crawl starts, so a budget that cannot cover sixty pages of a site this
# speed does not quietly read fewer of them: the clock cuts the crawl, and the
# page set is then whatever the network delivered before it fell.
MEASURED_FULL_CRAWL_SECONDS = 99.0


def _runner():
    spec = importlib.util.spec_from_file_location(
        "run_audit_for_degradation_tests", os.path.join(ROOT, "run_audit.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_degradation_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMPOSE = _compose()


class _Clock(object):
    """A monotonic clock that only moves when a phase spends time."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


def _drive(module, out_dir, failing=(), spend=None, extra=(), before_failing=None):
    """Run the real `main()` with every phase faked, and report what happened.

    `failing` names the phases that do not finish; each spends its whole cap
    first, which is the shape of an overrun. `before_failing` is called with
    the failing phase's label, so a test can make the crawl write a snapshot
    and then fail.

    `spend` says how long each phase takes: a dict keyed by label, or a
    callable `(label, timeout, told) -> seconds`. The callable form exists
    because what a sub-skill is *told* it may spend is computed inside
    `main()` from the clock this harness drives, so it is not knowable before
    the phase is reached - and "spends exactly what it was told" is the
    behaviour of a skill that honours its deadline, which is the case most of
    these tests are about. The default, spending the whole kill-timeout, is a
    skill that ignores its deadline entirely.
    """
    clock = _Clock()
    phases = []

    def fake_run_step(command, label, timeout=None):
        assert timeout is not None, "{} was run with no ceiling".format(label)
        told = None
        if "--time-budget" in command:
            told = float(command[command.index("--time-budget") + 1])
        if spend is None:
            seconds = timeout
        elif callable(spend):
            seconds = min(timeout, spend(label, timeout, told))
        else:
            seconds = min(timeout, spend.get(label, timeout))
        phases.append({"label": label, "timeout": timeout, "seconds": seconds,
                       "told": told, "no_network": "--no-network" in command,
                       "command": list(command)})
        clock.now += seconds
        if label in failing:
            if before_failing is not None:
                before_failing(label)
            raise module.PhaseFailed(label, "was stopped after {:.0f}s".format(timeout),
                                     seconds)
        return seconds

    module.time = clock
    module.run_step = fake_run_step
    argv = [INVENTED_TARGET, "--out-dir", str(out_dir), "--no-render",
            "--keep-intermediates"] + list(extra)
    return module.main(argv), clock.now, phases


def _snapshot_of(pages=1):
    return {"site": "an invented host", "origin": INVENTED_TARGET,
            "crawl": {"pages_crawled": pages, "pages_ok": pages, "notes": []},
            "pages": [{"url": INVENTED_TARGET + "/", "status": 200}] * pages}


# --------------------------------------------------------------------------
# Which phases are survivable
# --------------------------------------------------------------------------

@pytest.mark.parametrize("skill", SUB_SKILLS)
def test_one_sub_skill_that_does_not_finish_does_not_cost_the_report(skill, tmp_path):
    """The defect, in one assertion: exit 0 and a composed report."""
    module = _runner()
    code, _, phases = _drive(module, tmp_path, failing=(skill,))

    assert code == 0, "the run exited on a sub-skill that did not finish"
    assert phases[-1]["label"] == "compose_report", \
        "the report was never composed after a sub-skill was stopped"
    assert [p["label"] for p in phases] == ["crawl"] + list(SUB_SKILLS) + ["compose_report"], \
        "a stopped sub-skill must not stop the ones after it either"

    # The stopped skill still hands compose_report a file, or the report would
    # be composed from five results and say nothing about the sixth.
    composed = phases[-1]["command"]
    for name in SUB_SKILLS:
        path = os.path.join(str(tmp_path), "{}.findings.json".format(name))
        assert path in composed, "{} was left out of the report entirely".format(name)


def test_every_sub_skill_failing_still_produces_a_report(tmp_path):
    """The floor of the degradation, not just one step down it."""
    module = _runner()
    code, _, phases = _drive(module, tmp_path, failing=tuple(SUB_SKILLS))
    assert code == 0
    assert phases[-1]["label"] == "compose_report"


def test_a_crawl_that_leaves_no_snapshot_is_fatal(tmp_path):
    """The one genuinely fatal failure.

    Every phase after the crawl reads the snapshot and none of them can reach
    the site, so without one there is nothing to describe and a report would be
    a document of empty sections.
    """
    module = _runner()
    with pytest.raises(SystemExit) as stopped:
        _drive(module, tmp_path, failing=("crawl",))
    assert "nothing to report on" in str(stopped.value)


def test_a_crawl_that_failed_after_writing_a_snapshot_is_audited_anyway(tmp_path):
    """Pages already read are pages worth auditing.

    The fatal case is an absent snapshot, not a non-zero exit code: a crawl
    that recorded pages and then failed has produced exactly the partial sample
    every finding in this report already states the size of.
    """
    module = _runner()
    snapshot_path = os.path.join(str(tmp_path), "snapshot.json")

    def write_snapshot(label):
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            json.dump(_snapshot_of(3), handle)

    code, _, phases = _drive(module, tmp_path, failing=("crawl",),
                             before_failing=write_snapshot)
    assert code == 0
    assert phases[-1]["label"] == "compose_report"

    with open(snapshot_path, encoding="utf-8") as handle:
        notes = json.load(handle)["crawl"]["notes"]
    assert any("the crawl" in note for note in notes), \
        "nothing in the snapshot records that the crawl was cut short"


def test_a_snapshot_from_an_earlier_run_is_not_mistaken_for_this_one(tmp_path):
    """Otherwise a stopped crawl is composed from pages read at some other
    time, and the report describes them as though they were fetched today."""
    module = _runner()
    stale = os.path.join(str(tmp_path), "snapshot.json")
    with open(stale, "w", encoding="utf-8") as handle:
        json.dump(_snapshot_of(9), handle)

    with pytest.raises(SystemExit):
        _drive(module, tmp_path, failing=("crawl",))
    assert not os.path.exists(stale), "a stale snapshot survived a failed crawl"


def test_the_half_written_checkpoint_of_an_earlier_run_is_cleared_too(tmp_path):
    """`crawl.py` writes its checkpoints through `snapshot.json.partial` and
    renames the file into place, so a crawl killed between the write and the
    rename leaves that temporary behind. One left by an earlier run would sit
    in this run's output directory, and the next kill would rename a week-old
    file over today's snapshot."""
    module = _runner()
    stale = os.path.join(str(tmp_path), "snapshot.json.partial")
    with open(stale, "w", encoding="utf-8") as handle:
        json.dump(_snapshot_of(9), handle)

    code, _, _ = _drive(module, tmp_path)
    assert code == 0
    assert not os.path.exists(stale), \
        "a half-written checkpoint from an earlier run survived this one"


def test_a_truncated_snapshot_is_not_treated_as_a_crawl(tmp_path):
    """A crawl killed before its first checkpoint leaves no file at all, and
    one killed inside a write leaves half of one - the rename closes that
    second door, and this holds the guard that catches both anyway."""
    module = _runner()
    path = os.path.join(str(tmp_path), "snapshot.json")
    assert module.snapshot_if_usable(path) is None, "a missing file read as a crawl"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"site": "an invented host", "pages": [{"url": "https:/')
    assert module.snapshot_if_usable(path) is None, "half a JSON file read as a crawl"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"site": "an invented host", "pages": []}, handle)
    assert module.snapshot_if_usable(path) is None, "a crawl of no pages read as a crawl"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(_snapshot_of(2), handle)
    assert module.snapshot_if_usable(path) is not None


def test_composing_the_report_is_the_other_fatal_phase(tmp_path):
    """There is no degraded form of the output itself, and the message has to
    point at the files that survived rather than only at the failure."""
    module = _runner()
    with pytest.raises(SystemExit) as stopped:
        _drive(module, tmp_path, failing=("compose_report",))
    message = str(stopped.value)
    assert "compose_report.py" in message
    assert str(tmp_path) in message


# --------------------------------------------------------------------------
# Degrading must not become a way to run long
# --------------------------------------------------------------------------

def test_a_run_where_every_phase_overruns_still_ends_inside_the_ceiling(tmp_path):
    """Continuing past a failure may not buy a phase a second more clock.

    The success path already has this test. The failure path is new and could
    have carried its own arithmetic: a phase that is stopped has spent its cap,
    and the phases after it are handed what is left over, exactly as they are
    when it succeeds.
    """
    module = _runner()
    _, total, phases = _drive(module, tmp_path, failing=tuple(SUB_SKILLS))
    assert total <= RUN_WALL_CLOCK_LIMIT, \
        "worst case is {:.0f}s against a ceiling of {:.0f}s".format(
            total, RUN_WALL_CLOCK_LIMIT)
    for phase in phases:
        assert phase["seconds"] <= phase["timeout"] + 0.05, phase["label"]


# --------------------------------------------------------------------------
# What the report says about a skill that did not run
# --------------------------------------------------------------------------

@pytest.mark.parametrize("dropped", SUB_SKILLS)
def test_a_dropped_skill_is_reported_as_a_gap_rather_than_as_silence(dropped, audit,
                                                                     tmp_path):
    """Silence in this report means "checked, nothing wrong".

    So a skill that never ran may not read as silence. It arrives as one check
    registered and that same check declined with a reason, which is the channel
    the report already uses for every question it could not answer.
    """
    source = audit("good-site")
    findings_paths = []
    for skill in SUB_SKILLS:
        path = os.path.join(str(tmp_path), "{}.findings.json".format(skill))
        if skill == dropped:
            module = _runner()
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(module.unrun_result(skill, "was stopped after 31s"), handle)
        else:
            shutil.copy(os.path.join(source.out_dir,
                                     "{}.findings.json".format(skill)), path)
        findings_paths.append(path)

    report_json = os.path.join(str(tmp_path), "report.json")
    report_md = os.path.join(str(tmp_path), "report.md")
    completed = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "compose_report.py"),
         "--snapshot", source.snapshot_path, "--findings"] + findings_paths +
        ["--out-json", report_json, "--out-md", report_md,
         "--audited-at", REFERENCE_TIMESTAMP],
        cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, \
        "a skill that did not run broke the report:\n" + completed.stderr[-1500:]

    with open(report_json, encoding="utf-8") as handle:
        report = json.load(handle)
    with open(report_md, encoding="utf-8") as handle:
        markdown = handle.read()

    declined = [n for n in report["not_applicable"] if n.get("skill") == dropped]
    assert declined, "{} vanished from the report".format(dropped)
    reason = declined[0]["reason"]
    assert "none of its checks ran" in reason
    assert "not as a pass" in reason, "the gap must not read as a clean bill of health"
    assert dropped in markdown and reason[:60] in markdown, \
        "report.md does not say the skill did not run"

    # Registered as a check as well as declined, so the appendix's four buckets
    # still add up to the number of checks it says ran.
    ran = {(c["skill"], c["check"]) for c in report["checks_run"]}
    assert (dropped, "skill-did-not-run") in ran
    passed = {(c["skill"], c["check"]) for c in report.get("checks_passed") or []}
    assert (dropped, "skill-did-not-run") not in passed, \
        "a skill that never ran was counted as a check that ran clean"


def test_a_dropped_skill_contributes_no_findings(tmp_path):
    """The stand-in is an admission, not an invention. It may not assert
    anything about the site - the whole point is that nothing was looked at."""
    module = _runner()
    result = module.unrun_result(SUB_SKILLS[0], "exited with code 1")
    assert result["findings"] == []
    assert result["fired_checks"] == []
    assert result["extra_requests_made"] == 0
    assert result["signals"] == {}
    assert [n["check"] for n in result["not_applicable"]] == result["checks_run"]


# --------------------------------------------------------------------------
# The split between the crawl and the probes that follow it
# --------------------------------------------------------------------------

def test_the_crawl_is_told_to_stop_before_the_probe_phase_s_share(tmp_path):
    """The measured defect: 245 s of a 261 s ceiling spent in the crawl, and
    every sub-skill after it run with no network at all.

    The budget the crawl is handed and the point at which it is killed are two
    different numbers now. The budget leaves the probe phase its reserve; the
    kill-timeout sits above the budget because a crawl only tests its deadline
    between requests and one wedged socket must not be fatal.
    """
    module = _runner()
    _, _, phases = _drive(module, tmp_path, spend={"crawl": 10 ** 6})
    crawl = phases[0]
    budget = float(crawl["command"][crawl["command"].index("--budget") + 1])
    assert budget < crawl["timeout"], \
        "the crawl is told to spend every second it would be killed at"
    assert budget + module.PROBE_PHASE_RESERVE_SECONDS <= RUN_WALL_CLOCK_LIMIT, \
        "the crawl's budget and the probes' reserve do not both fit in the ceiling"


def _crawl_budget(module, tmp_path):
    """The seconds the crawl is told it may spend, on a run of this build."""
    _, _, phases = _drive(module, tmp_path, spend={"crawl": 0.0})
    command = phases[0]["command"]
    return float(command[command.index("--budget") + 1])


def _honours_its_deadline(crawl_seconds, write_seconds=1.0):
    """A `spend` function in which every phase uses what it was allowed.

    The crawl spends `crawl_seconds`; a sub-skill handed a network budget
    spends it and then writes its result file; one that was handed none reads
    the snapshot and writes. Measured on this tree, the slowest of the six
    takes 0.85 s over a sixty-page snapshot, so a second is the cost of the
    reading and the writing together.

    `_drive`'s default - every phase spending its whole kill-timeout - models a
    skill that ignores its deadline, and that is a different question from the
    one these tests ask.
    """
    def spend(label, timeout, told):
        if label == "crawl":
            return crawl_seconds
        if not told:
            return write_seconds
        return told + write_seconds
    return spend


def test_every_probe_keeps_its_allowance_when_the_crawl_uses_its_whole_budget(tmp_path):
    """The guarantee the split exists to make.

    A crawl that spends its entire advisory budget is the case the sub-skills
    used to be starved by. Each of the three that make requests must still be
    handed enough to be worth running, which is the same threshold the
    orchestrator uses to decide to switch the network off.
    """
    module = _runner()
    crawl_budget = _crawl_budget(module, tmp_path)

    module = _runner()
    _, total, phases = _drive(module, tmp_path,
                              spend=_honours_its_deadline(crawl_budget))
    told = {p["label"]: p["told"] for p in phases if p["told"] is not None}
    assert set(told) == set(module.NETWORK_SUB_SKILLS)
    for label, seconds in told.items():
        assert seconds >= module.MIN_SUB_SKILL_SECONDS, (
            "{} was handed {:.0f}s, below the {:.0f}s at which this run stops making "
            "requests at all".format(label, seconds, module.MIN_SUB_SKILL_SECONDS))
    for phase in phases[1:-1]:
        assert not phase["no_network"], \
            "{} was switched off despite the crawl staying inside its budget".format(
                phase["label"])
    assert total <= RUN_WALL_CLOCK_LIMIT


def test_a_probe_may_spend_every_second_it_was_told_and_still_finish(tmp_path):
    """The defect, in one assertion.

    `engagement-audit` was told it had 83 s and killed at 85; reproduced here
    against a site answering in six seconds a page, it was killed at 94. A
    sub-skill tests
    its deadline before it opens a socket and not again, so the one response
    that outlives the deadline can hold it open for a further
    REQUEST_TOTAL_TIMEOUT - thirty seconds against a two-second margin. The
    skill honoured its budget exactly and was killed anyway; a sixth of that
    audit was thrown away and the report had to publish the gap.

    The margin between what a skill is told and the point at which it is
    stopped now covers one wedged response and the file it writes afterwards,
    so spending the whole allowance can no longer end a skill.
    """
    module = _runner()
    for crawl_seconds in (0.0, 30.0, _crawl_budget(module, tmp_path), 10 ** 6):
        module = _runner()
        _, _, phases = _drive(module, tmp_path,
                              spend=_honours_its_deadline(crawl_seconds))
        network = [p for p in phases if p["told"]]
        for phase in network:
            assert (phase["told"] + module.SKILL_WEDGE_MARGIN_SECONDS
                    + module.MIN_STEP_SECONDS) <= phase["timeout"] + 0.05, (
                "{} is told {:.0f}s and killed at {:.0f}s, which leaves {:.0f}s for a "
                "response that can hold a request open for {:.0f}s".format(
                    phase["label"], phase["told"], phase["timeout"],
                    phase["timeout"] - phase["told"],
                    module.SKILL_WEDGE_MARGIN_SECONDS))


def test_every_sub_skill_still_runs_when_every_probe_wedges(tmp_path):
    """Coverage is the thing that may not degrade; probing is the thing that may.

    Every skill that makes requests spends its whole allowance and then wedges
    on one response. What must come out of that is six result files and a
    composed report: a later skill losing its network budget is a check that
    says it could not reach its targets, which the report already states, and a
    skill that never ran is a hole the report can only apologise for.
    """
    module = _runner()
    crawl_budget = _crawl_budget(module, tmp_path)

    def wedges(label, timeout, told):
        if label == "crawl":
            return crawl_budget
        if not told:
            # No network budget means no request to wedge on: this skill reads
            # the snapshot, declines its network checks with a reason and
            # writes its file.
            return 1.0
        return told + module.SKILL_WEDGE_MARGIN_SECONDS + 1.0

    module = _runner()
    code, total, phases = _drive(module, tmp_path, spend=wedges)
    assert code == 0
    assert [p["label"] for p in phases] == \
        ["crawl"] + list(SUB_SKILLS) + ["compose_report"]
    for phase in phases:
        assert phase["seconds"] <= phase["timeout"] + 0.05, (
            "{} was stopped: it spent {:.0f}s of {:.0f}s".format(
                phase["label"], phase["seconds"], phase["timeout"]))
    composed = phases[-1]["command"]
    for name in SUB_SKILLS:
        assert os.path.join(str(tmp_path), "{}.findings.json".format(name)) in composed
    assert total <= RUN_WALL_CLOCK_LIMIT


def test_the_crawl_is_the_phase_that_gives_way_and_by_how_much(tmp_path):
    """Which phase pays for the reserve, and what it costs in pages.

    The old split gave the crawl 171 s and the six skills whatever survived it,
    which on a slow site is nothing. Reproduced against a site answering in six
    seconds a page: the crawl was told 171 s and the process took 174.0, those
    three seconds put `crawl-access-audit` under the threshold at which the run
    still hands out a network budget so it made no requests at all, and
    `engagement-audit` was killed at 94 s. The new split gives the crawl 115 s
    and the six skills a reserve the crawl cannot reach; the same site then
    read 21 pages instead of 29, ran all six skills, and gave all three that
    make requests their network - 231.7 s against 273.5.

    121 s is fewer pages, and this is the size of that: what the crawl has
    left has to cover the 99 s that site's sixty pages were measured at, or
    the clock cuts the crawl and the page set stops repeating. It does, with
    43 s to spare.

    This test used to assert something else, and the something else was the
    defect itself: `crawl.py` planned its page count as `budget / rung(measured cost of a page)`, so this site
    "plans 43 pages rather than 60". 43 was not a reading of the site. It was
    where a measured duration fell on a ladder of doubling rungs, and a
    second run whose first five pages measured a tenth of a second faster
    bought 60. The page count is declared before the crawl starts now, and
    the budget's job is to cover it.
    """
    module = _runner()
    budget = _crawl_budget(module, tmp_path)
    a_page = MEASURED_FULL_CRAWL_SECONDS / 60.0

    assert budget >= MEASURED_FULL_CRAWL_SECONDS, (
        "a {:.0f}s crawl cannot read sixty pages of a site answering in {:.2f}s a "
        "page - measured at {:.0f}s - so the clock would cut it and the page set "
        "would be whatever this run's network delivered".format(
            budget, a_page, MEASURED_FULL_CRAWL_SECONDS))


def test_the_reserve_is_every_sub_skill_s_own_floor_plus_one_wedged_response(tmp_path):
    """One number derived from the six, not a seventh number chosen next to
    them. A skill that makes requests is owed enough to make one; a skill that
    only reads the snapshot is owed the time it takes to write a file.

    Plus one wedged response, once rather than six times. The floors alone
    summed to exactly what six skills need if nothing overruns, so the crawl
    running 17 s past its budget came straight out of the first skill's
    allowance. Once, because only the phase currently running can be inside a
    wedged response, and a skill that loses its network budget to an earlier
    overrun still runs.
    """
    module = _runner()
    assert module.PROBE_PHASE_RESERVE_SECONDS == sum(
        module.step_floor(name) for name in SUB_SKILLS
    ) + module.SKILL_WEDGE_MARGIN_SECONDS
    for name in SUB_SKILLS:
        floor = module.step_floor(name)
        if name in module.NETWORK_SUB_SKILLS:
            assert floor > module.MIN_SUB_SKILL_SECONDS, name
        else:
            assert floor == module.MIN_STEP_SECONDS, name


def test_the_reserve_is_held_back_from_the_crawl_rather_than_taken_after_it(tmp_path):
    """The crawl is killed at the reserve boundary, not past it.

    It used to be killed at `probe_deadline - 6 x MIN_STEP_SECONDS`, which
    reserved for the six skills only the two seconds each needs to write a
    file and left the crawl free to eat the ninety the three that make requests
    need to make any. A crawl that wedged took the probing with it.
    """
    module = _runner()
    _, _, phases = _drive(module, tmp_path, spend={"crawl": 10 ** 6})
    kill_at = phases[0]["timeout"]
    assert kill_at + module.PROBE_PHASE_RESERVE_SECONDS \
        + module.COMPOSE_RESERVE_SECONDS <= RUN_WALL_CLOCK_LIMIT + 0.05, (
        "a crawl killed at {:.0f}s leaves less than the {:.0f}s reserved for the six "
        "sub-skills".format(kill_at, module.PROBE_PHASE_RESERVE_SECONDS))

    # And a crawl that had to be killed at that boundary still leaves every
    # skill that makes requests a budget worth having, which is what holding
    # the reserve is for.
    module = _runner()
    _, _, phases = _drive(module, tmp_path,
                          spend=_honours_its_deadline(kill_at))
    told = [p["told"] for p in phases if p["told"] is not None]
    assert len(told) == len(module.NETWORK_SUB_SKILLS)
    assert all(t >= module.MIN_SUB_SKILL_SECONDS for t in told), told


def test_a_skill_is_killed_no_sooner_than_the_deadline_it_was_given(tmp_path):
    """A skill tests its deadline between requests and then still has to write
    its result file. Told and killed at the same instant, a skill that spent
    its whole allowance was killed for spending it - which is the failure that
    threw away a finished crawl and five finished skills."""
    module = _runner()
    for crawl_seconds in (0.0, 30.0, 120.0, 10 ** 6):
        _, _, phases = _drive(module, tmp_path, spend={"crawl": crawl_seconds})
        for phase in phases:
            if phase["told"] is None or phase["told"] <= 0:
                continue
            assert phase["timeout"] > phase["told"], (
                "{} is told {:.1f}s and killed at {:.1f}s".format(
                    phase["label"], phase["told"], phase["timeout"]))


# --------------------------------------------------------------------------
# A sub-skill that did not run is named at the top, not at line 531
# --------------------------------------------------------------------------

def test_a_dropped_sub_skill_is_named_in_the_short_version(audit):
    """A real run against a temple's site crawled slowly enough that one
    sub-skill was stopped by the clock at 85 seconds and a second never
    started. The report said so - in the appendix, at line 531. "The short
    version", which is the part anybody reads, never mentioned that a sixth of
    the audit had not happened, so the reader was given a count of findings
    with no way to know what the count was out of.

    Built from a real composed report rather than a hand-written dict, because
    the last hand-written one had to be repaired twice for keys the renderer
    reads and the test does not care about.
    """
    report = json.loads(json.dumps(audit("good-site").report))
    report["skills_that_did_not_run"] = [
        {"skill": "engagement-audit", "check": "skill_did_not_run",
         "reason": "this skill was stopped after 85s, the share of this audit's "
                   "wall-clock ceiling that was left for it."}]

    markdown = COMPOSE.render_markdown(report)
    head = markdown.split("| Severity")[0]
    assert COMPOSE.DROPPED_SKILL_HEADING in head, \
        "a dropped sub-skill is not named above the severity table"
    assert "engagement-audit" in head
    # And what the gap costs, in words a reader who has never opened this
    # repository can act on.
    assert "headings, navigation" in head, head[-800:]
    assert "1 of the 6 sub-skills is missing" in head

    # The HTML report cannot say something different from the markdown one.
    html = COMPOSE.render_html(report)
    assert COMPOSE.DROPPED_SKILL_HEADING in html
    assert "engagement-audit" in html
    # No markdown emphasis leaking into escaped HTML.
    assert "**engagement-audit**" not in html

    # A run where every skill ran says nothing at all.
    report["skills_that_did_not_run"] = []
    assert COMPOSE.DROPPED_SKILL_HEADING not in COMPOSE.render_markdown(report)


def test_every_sub_skill_says_what_it_examines():
    """The sentence exists so a reader knows what a gap costs them. A skill
    missing from the map gets the gap named and not its consequence, which is
    the half that matters."""
    from audit_common import SUB_SKILLS

    for skill in SUB_SKILLS:
        assert skill in COMPOSE._WHAT_A_SKILL_EXAMINES, skill
