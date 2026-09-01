#!/usr/bin/env python3
"""Build the submission zip.

The zip contains the marketplace root directory, as the brief requires. It
excludes caches, virtual environments and audit output.

    python package.py
    python package.py --out dist/brand-ai-readiness-audit.zip
    python package.py --check          # Validate and test first, then build

The script prints the file count and the size. It fails if the zip is larger
than the 50 MB submission limit.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGE_NAME = "brand-ai-readiness-audit"
SIZE_LIMIT_MB = 50

# Directories that must never enter the zip.
EXCLUDE_DIRS = {
    "__pycache__", ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "out", ".venv", "venv", "env", "dist", ".idea", ".vscode",
}

# File suffixes and names that must never enter the zip.
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".zip", ".log")
EXCLUDE_NAMES = {
    "Thumbs.db", "desktop.ini",
    "snapshot.json", "report.json", "report.md", "report.html",
    # Our own working notes. The brief asks for the manifest, the skills and a
    # README; internal planning documents are not part of the deliverable and
    # would only pad it. They live outside this directory so they cannot drift
    # back in, and these names are listed as a second line of defence.
    "DECISIONS.md", "PROGRESS.md", "VERIFICATION.md", "NOTES.md", "TODO.md",
}


ORCHESTRATOR_SCRIPTS = os.path.join("skills", "audit-orchestrator", "scripts")

VENDOR_HEADER = """# ---------------------------------------------------------------------------
# GENERATED COPY - do not edit.
#
# The single source of this file is {source}.
# package.py writes a copy into every skill that imports it, so that each skill
# folder satisfies the brief's portability rule on its own: lift one out of the
# marketplace and it still runs.
#
# The checkout keeps one copy so the definitions cannot drift; the submission
# carries one per skill that needs it.
# ---------------------------------------------------------------------------
"""

# Which modules to copy is derived from what each skill imports, never from a
# list kept here by hand. A hand-kept list said `audit_common.py` and nothing
# else, so the built zip shipped a `crawl-access-audit` that could not start:
# it also imports `robots_parser`, and vendoring made things worse rather than
# better, because a vendored `audit_common.py` sitting beside `check.py` wins
# the import search and takes the orchestrator's scripts directory - the only
# place `robots_parser` existed - out of the path entirely.
_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([a-z_][a-z0-9_]*)", re.M)


def _local_modules(root):
    """Module names that live in the orchestrator's scripts directory."""
    scripts = os.path.join(root, ORCHESTRATOR_SCRIPTS)
    return {name[:-3] for name in os.listdir(scripts)
            if name.endswith(".py") and not name.startswith("_")}


def _imports_of(path, available):
    with open(path, encoding="utf-8") as handle:
        return {name for name in _IMPORT_RE.findall(handle.read()) if name in available}


def vendored_shared_library(root):
    """One `(archive path, bytes)` pair per module each skill needs.

    Returned rather than written to disk: the checkout stays single-source, and
    only the zip carries the copies. Resolved transitively, so a vendored module
    that imports another local module brings it along.
    """
    available = _local_modules(root)
    scripts_dir = os.path.join(root, ORCHESTRATOR_SCRIPTS)
    sources = {}
    for name in available:
        with open(os.path.join(scripts_dir, name + ".py"), encoding="utf-8") as handle:
            sources[name] = handle.read()

    copies = []
    skills_dir = os.path.join(root, "skills")
    for skill in sorted(os.listdir(skills_dir)):
        entry = os.path.join(skills_dir, skill, "scripts", "check.py")
        if skill == "audit-orchestrator" or not os.path.isfile(entry):
            continue

        needed, queue = set(), list(_imports_of(entry, available))
        while queue:
            name = queue.pop()
            if name in needed:
                continue
            needed.add(name)
            queue.extend(_imports_of(os.path.join(scripts_dir, name + ".py"), available))

        for name in sorted(needed):
            header = VENDOR_HEADER.format(
                source="skills/audit-orchestrator/scripts/{}.py".format(name))
            copies.append(("skills/{}/scripts/{}.py".format(skill, name),
                           (header + sources[name]).encode("utf-8")))
    return copies


# What the submission is allowed to contain, as an allowlist.
#
# This used to be a denylist: walk everything, drop the names we had thought
# of. Its own comment recorded a 283 KB `.coverage` file reaching a built zip,
# and the fix at the time was to add another name. The failure then repeated
# with a file called `-`, created by a mistyped command, which was neither
# hidden nor generated nor on any list - and shipped.
#
# A denylist has to anticipate every mistake anybody will ever make. An
# allowlist has to describe the deliverable, which is a much shorter and much
# more stable thing.
ALLOWED_ROOT_FILES = {
    "marketplace.json", "README.md", "LICENSE", "requirements.txt",
    "run_audit.py", "package.py", "pytest.ini",
}
ALLOWED_TREES = ("skills", "tests", "evals")
# Every extension present in the deliverable trees, checked against the tree
# rather than guessed: a first pass at this list omitted `.pdf` and silently
# dropped the fixture asset that proves the "facts locked in a PDF" check.
ALLOWED_SUFFIXES = (".py", ".md", ".json", ".txt", ".xml", ".html", ".ini",
                    ".cfg", ".pdf")


def collect_files():
    """List every file to package, in a stable order.

    Anything not described here is left out and named by `unexpected_files`, so
    a stray file is a build failure rather than a silent passenger.
    """
    selected = []
    for filename in sorted(ALLOWED_ROOT_FILES):
        path = os.path.join(ROOT, filename)
        if os.path.isfile(path):
            selected.append(path)

    for tree in ALLOWED_TREES:
        base = os.path.join(ROOT, tree)
        if not os.path.isdir(base):
            continue
        for directory, subdirectories, files in os.walk(base):
            subdirectories[:] = sorted(d for d in subdirectories if d not in EXCLUDE_DIRS)
            for filename in sorted(files):
                if filename.startswith(".") or filename in EXCLUDE_NAMES:
                    continue
                if filename.endswith(EXCLUDE_SUFFIXES) or filename.endswith(".findings.json"):
                    continue
                if not filename.endswith(ALLOWED_SUFFIXES):
                    continue
                selected.append(os.path.join(directory, filename))
    return selected


def unexpected_files():
    """Files in the checkout root that the allowlist does not describe.

    Reported rather than silently dropped: a file nobody meant to create is
    worth seeing, and a file somebody did mean to ship needs adding above.
    """
    strays = []
    for entry in sorted(os.listdir(ROOT)):
        path = os.path.join(ROOT, entry)
        if os.path.isdir(path):
            if entry not in ALLOWED_TREES and entry not in EXCLUDE_DIRS:
                strays.append(entry + "/")
            continue
        if entry.startswith(".") or entry in ALLOWED_ROOT_FILES:
            continue
        if entry.endswith(EXCLUDE_SUFFIXES) or entry in EXCLUDE_NAMES:
            continue
        strays.append(entry)
    return strays


def build(out_path):
    strays = unexpected_files()
    if strays:
        print("Refusing to build: the checkout root holds files the allowlist "
              "does not describe.", file=sys.stderr)
        for stray in strays:
            print("  " + stray, file=sys.stderr)
        print("Delete them, or add them to ALLOWED_ROOT_FILES / ALLOWED_TREES "
              "if they belong in the submission.", file=sys.stderr)
        raise SystemExit(2)

    files = collect_files()
    directory = os.path.dirname(os.path.abspath(out_path))
    if directory:
        os.makedirs(directory, exist_ok=True)

    copies = vendored_shared_library(ROOT)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = os.path.relpath(path, ROOT).replace(os.sep, "/")
            archive.write(path, "{}/{}".format(PACKAGE_NAME, relative))
        for relative, payload in copies:
            archive.writestr("{}/{}".format(PACKAGE_NAME, relative), payload)
    return files + [name for name, _ in copies], os.path.getsize(out_path)


def run_checks():
    """Validate the marketplace and run the tests. Stop on the first failure."""
    steps = [
        (["python", os.path.join("skills", "audit-orchestrator", "scripts",
                                 "validate_marketplace.py")], "marketplace validation"),
        (["python", "-m", "pytest", "tests", "-q"], "test suite"),
    ]
    for command, label in steps:
        command[0] = sys.executable
        print("Running the {}...".format(label))
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            print("The {} failed. The script did not build the zip.".format(label))
            return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="{}.zip".format(PACKAGE_NAME),
                        help="Path of the zip file to write")
    parser.add_argument("--check", action="store_true",
                        help="Validate the marketplace and run the tests before the build")
    args = parser.parse_args(argv)

    if args.check and not run_checks():
        return 1

    files, size = build(args.out)
    megabytes = size / (1024 * 1024)

    print("")
    print("Wrote {}".format(os.path.abspath(args.out)))
    print("Files: {}".format(len(files)))
    print("Size: {:.2f} MB (the limit is {} MB)".format(megabytes, SIZE_LIMIT_MB))

    if megabytes > SIZE_LIMIT_MB:
        print("The zip is too large to submit.")
        return 1

    # A zip without these files is not a valid submission.
    with zipfile.ZipFile(args.out) as archive:
        names = set(archive.namelist())
    for required in ("marketplace.json", "README.md"):
        entry = "{}/{}".format(PACKAGE_NAME, required)
        if entry not in names:
            print("The zip does not contain {}.".format(required))
            return 1
    skill_files = [n for n in names if n.endswith("/SKILL.md")]
    if len(skill_files) != 7:
        print("The zip contains {} SKILL.md files. It must contain 7.".format(len(skill_files)))
        return 1

    print("The zip contains marketplace.json, README.md and 7 SKILL.md files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
