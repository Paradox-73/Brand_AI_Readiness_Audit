"""The submission is the zip, so the zip is what has to work.

Every other test in this suite runs from the checkout. That is not what a judge
receives, and the difference is not cosmetic: `package.py` writes a copy of the
shared library into each skill so that a folder lifted out on its own can still
run, and that copy changes which directory wins the import search.

The consequence went unnoticed until someone ran the built zip by hand.

  - In the checkout, `crawl-access-audit/scripts/check.py` finds no
    `audit_common.py` beside it, walks up to `skills/audit-orchestrator/scripts/`
    and puts *that* directory on the path. `robots_parser` lives there too, so
    the import succeeds.
  - In the zip, a vendored `audit_common.py` sits beside `check.py`. That wins,
    the orchestrator's scripts directory never enters the path, and
    `robots_parser` is nowhere. The skill cannot start.

So `python run_audit.py <url>` - the first command in the README - exited 1 on
the shipped artefact, and the documented six-step procedure in the entrypoint's
SKILL.md failed at step 3. A hundred per cent of the suite passed throughout,
because none of it ran the thing being submitted.

These tests run the zip. They are slow and they are worth it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

import pytest

from conftest import FIXTURES, ROOT

ARCHIVE = os.path.join(ROOT, "brand-ai-readiness-audit.zip")
PACKAGE = "brand-ai-readiness-audit"

SUB_SKILLS = [
    "crawl-access-audit",
    "render-readability-audit",
    "structured-data-audit",
    "fact-extractability-audit",
    "freshness-corroboration-audit",
    "engagement-audit",
]


def _require_zip():
    if not os.path.exists(ARCHIVE):
        pytest.skip("no zip built; run `python package.py` first")


@pytest.fixture(scope="module")
def extracted():
    """The zip, unpacked, exactly as a judge would have it."""
    _require_zip()
    work = tempfile.mkdtemp(prefix="submission-")
    try:
        with zipfile.ZipFile(ARCHIVE) as bundle:
            bundle.extractall(work)
        yield os.path.join(work, PACKAGE)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _serve(root, fixture):
    """Serve a fixture from inside the extracted copy."""
    sys.path.insert(0, os.path.join(root, "tests"))
    from fixture_server import FixtureServer
    return FixtureServer(os.path.join(root, "tests", "fixtures", fixture))


def _run(root, args, timeout=420):
    return subprocess.run([sys.executable] + args, cwd=root,
                          capture_output=True, text=True, timeout=timeout)


# --------------------------------------------------------------------------
# Every skill can start
# --------------------------------------------------------------------------

@pytest.mark.parametrize("skill", SUB_SKILLS)
def test_every_skill_in_the_zip_can_be_imported(extracted, skill):
    """Cheap, and it is the exact failure that shipped.

    Parametrised over all six rather than a representative one: the test that
    existed picked `structured-data-audit`, which is the only sub-skill whose
    sole local import is `audit_common`. It proved the case that worked.
    """
    script = os.path.join(extracted, "skills", skill, "scripts", "check.py")
    result = _run(extracted, [script, "--help"], timeout=60)
    assert result.returncode == 0, (
        "{} cannot start from the built zip:\n{}".format(skill, result.stderr[-800:]))


@pytest.mark.parametrize("skill", SUB_SKILLS)
def test_every_skill_lifted_out_of_the_zip_still_runs(extracted, skill):
    """The brief's portability rule, applied to each skill rather than one."""
    work = tempfile.mkdtemp(prefix="lifted-")
    try:
        shutil.copytree(os.path.join(extracted, "skills", skill),
                        os.path.join(work, skill))
        script = os.path.join(work, skill, "scripts", "check.py")
        result = _run(work, [script, "--help"], timeout=60)
        assert result.returncode == 0, (
            "{} does not run outside the marketplace:\n{}".format(
                skill, result.stderr[-800:]))
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------
# The two documented ways to run an audit
# --------------------------------------------------------------------------

def test_the_readme_command_produces_a_report_from_the_zip(extracted):
    """`python run_audit.py <url>`, the first command the README gives."""
    with _serve(extracted, "good-site") as server:
        result = _run(extracted, [os.path.join(extracted, "run_audit.py"),
                                  server.base_url, "--out-dir", "out",
                                  "--no-render", "--no-network"])
    assert result.returncode == 0, (
        "the README's own command failed on the built zip:\n" + result.stderr[-1500:])

    report = os.path.join(extracted, "out", "report.json")
    assert os.path.exists(report), "no report.json was written"
    with open(report, encoding="utf-8") as handle:
        payload = json.load(handle)
    for key in ("site", "audited_at", "summary", "findings"):
        assert key in payload


def test_the_documented_procedure_produces_a_report_from_the_zip(extracted):
    """The six steps in audit-orchestrator/SKILL.md, run literally.

    This is the path a judge's agent follows: it reads the entrypoint's
    SKILL.md and runs the commands there. `run_audit.py` is a convenience, not
    the graded route, so passing one and failing the other is not good enough.
    """
    scripts = os.path.join(extracted, "skills", "audit-orchestrator", "scripts")

    with _serve(extracted, "good-site") as server:
        step2 = _run(extracted, [os.path.join(scripts, "crawl.py"), server.base_url,
                                 "--out", "snapshot.json", "--no-render"])
        assert step2.returncode == 0, "step 2 (crawl) failed:\n" + step2.stderr[-800:]

        produced = []
        for skill in SUB_SKILLS:
            out = os.path.join(extracted, "{}.findings.json".format(skill))
            produced.append(out)
            args = [os.path.join(extracted, "skills", skill, "scripts", "check.py"),
                    "--snapshot", "snapshot.json", "--out", out, "--no-network"]
            step3 = _run(extracted, args)
            assert step3.returncode == 0, (
                "step 3 ({}) failed:\n{}".format(skill, step3.stderr[-800:]))

    step4 = _run(extracted, [os.path.join(scripts, "compose_report.py"),
                             "--snapshot", "snapshot.json", "--findings"] + produced +
                            ["--out-json", "report.json", "--out-md", "report.md"])
    assert step4.returncode == 0, "step 4 (compose) failed:\n" + step4.stderr[-800:]
    assert os.path.getsize(os.path.join(extracted, "report.md")) > 1000


# --------------------------------------------------------------------------
# What the zip contains
# --------------------------------------------------------------------------

def test_the_zip_carries_every_module_each_skill_imports(extracted):
    """Derived from the imports, so a new dependency cannot be forgotten.

    The vendored set used to be a hand-kept list naming one file. It stayed
    correct until a skill imported a second one.
    """
    sys.path.insert(0, ROOT)
    import package

    available = package._local_modules(ROOT)
    for skill in SUB_SKILLS:
        scripts = os.path.join(extracted, "skills", skill, "scripts")
        needed = package._imports_of(os.path.join(scripts, "check.py"), available)
        for module in needed:
            assert os.path.exists(os.path.join(scripts, module + ".py")), (
                "{} imports {} and the zip does not ship it".format(skill, module))


def test_the_zip_contains_nothing_the_allowlist_does_not_describe():
    """A file named `-`, created by a mistyped command, shipped in a build.

    It was not hidden, not generated and not on any exclusion list. The
    packaging is an allowlist now; this checks the outcome.
    """
    _require_zip()
    sys.path.insert(0, ROOT)
    import package

    with zipfile.ZipFile(ARCHIVE) as bundle:
        names = bundle.namelist()

    for name in names:
        parts = name.split("/")[1:]
        if not parts or not parts[-1]:
            continue
        assert not any(part.startswith(".") for part in parts), name
        if len(parts) == 1:
            # Root files are allowed by name, not by extension: LICENSE has none.
            assert parts[0] in package.ALLOWED_ROOT_FILES, (
                "{} is at the root and not on the allowlist".format(name))
        else:
            assert parts[0] in package.ALLOWED_TREES, (
                "{} is in a directory the submission does not ship".format(name))
            assert parts[-1].endswith(package.ALLOWED_SUFFIXES), (
                "{} is not a file type the submission ships".format(name))
