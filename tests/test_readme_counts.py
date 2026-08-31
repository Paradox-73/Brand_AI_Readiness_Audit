"""The numbers the README states about the marketplace have to be the real ones.

The README opened with "68 checks, 74 findings, 48 named root causes". At the
time of writing this file the code held 70, 76 and 55. Nobody had lied; the
counts were typed once and then nine checks were removed, four were re-enabled
and a dozen root causes were added, and prose does not recompute itself.

A judge who counts is a judge who finds that, and reasonably concludes the rest
of the document is decoration too. So the counts are computed here from the
code and compared against the sentence, and adding a check without updating the
README fails.

What each number means, stated precisely so the assertion is defensible:

  checks        distinct names passed to `result.check(...)` across the six
                sub-skills. This is the number that appears in `checks_run` and
                in the report's appendix of what stayed quiet.
  findings      places in the code that can emit a finding - `result.add(...)`
                call sites. Not the number a single report contains, which
                depends on the site.
  root causes   entries in `ROOT_CAUSES`, the shared vocabulary every finding
                must draw from.
"""

from __future__ import annotations

import glob
import io
import os
import re

from audit_common import ROOT_CAUSES
from conftest import ROOT

README = os.path.join(ROOT, "README.md")

COUNT_SENTENCE = re.compile(
    r"\*\*(\d+) checks, (\d+) findings, (\d+) named root causes\.\*\*")


def _skill_sources():
    paths = sorted(glob.glob(os.path.join(ROOT, "skills", "*", "scripts", "check.py")))
    assert len(paths) == 6, "expected six sub-skills, found {}".format(len(paths))
    return paths


def _counts():
    checks, adds = set(), 0
    for path in _skill_sources():
        with io.open(path, encoding="utf-8") as handle:
            body = handle.read()
        checks |= set(re.findall(r'result\.check\(\s*"([^"]+)"', body))
        adds += len(re.findall(r"result\.add\(", body))
    return len(checks), adds, len(ROOT_CAUSES)


def _readme():
    with io.open(README, encoding="utf-8") as handle:
        return handle.read()


def test_the_readme_states_the_counts_at_all():
    match = COUNT_SENTENCE.search(_readme())
    assert match, ("the README no longer states the check, finding and root-cause counts in the "
                   "form this test can read. Keep the sentence or update the pattern here.")


def test_the_check_count_is_real():
    checks, _findings, _causes = _counts()
    stated = int(COUNT_SENTENCE.search(_readme()).group(1))
    assert stated == checks, (
        "the README says {} checks; the six sub-skills register {}".format(stated, checks))


def test_the_finding_count_is_real():
    _checks, findings, _causes = _counts()
    stated = int(COUNT_SENTENCE.search(_readme()).group(2))
    assert stated == findings, (
        "the README says {} findings; there are {} places in the six sub-skills that can emit "
        "one".format(stated, findings))


def test_the_root_cause_count_is_real():
    _checks, _findings, causes = _counts()
    stated = int(COUNT_SENTENCE.search(_readme()).group(3))
    assert stated == causes, (
        "the README says {} root causes; ROOT_CAUSES holds {}".format(stated, causes))


def test_the_documented_page_ceiling_matches_the_code():
    """`--max-pages` used to be hardcoded as 30 in `run_audit.py` while the
    shared constant said something else, so changing the constant did nothing."""
    from audit_common import MAX_PAGES

    body = _readme()
    assert "| `--max-pages` | {} |".format(MAX_PAGES) in body, (
        "the README's flag table does not show the real default of {}".format(MAX_PAGES))

    with io.open(os.path.join(ROOT, "run_audit.py"), encoding="utf-8") as handle:
        runner = handle.read()
    assert "default=MAX_PAGES" in runner, (
        "run_audit.py should take its page ceiling from the shared constant, not repeat it")
