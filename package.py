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


SHARED_LIBRARY = os.path.join("skills", "audit-orchestrator", "scripts", "audit_common.py")

VENDOR_HEADER = """# ---------------------------------------------------------------------------
# GENERATED COPY - do not edit.
#
# The single source of this file is skills/audit-orchestrator/scripts/audit_common.py.
# package.py writes a copy into every skill that reads it, so that each skill
# folder satisfies the brief's portability rule on its own: lift one out of the
# marketplace and it still runs.
#
# The checkout keeps one copy so the definitions cannot drift; the submission
# carries seven so no skill depends on its neighbours being present.
# ---------------------------------------------------------------------------
"""


def vendored_shared_library(root):
    """One `(archive path, bytes)` pair per skill that reads the shared library.

    Returned rather than written to disk: the checkout stays single-source, and
    only the zip carries the copies.
    """
    with open(os.path.join(root, SHARED_LIBRARY), encoding="utf-8") as handle:
        source = handle.read()
    payload = (VENDOR_HEADER + source).encode("utf-8")

    copies = []
    skills_dir = os.path.join(root, "skills")
    for name in sorted(os.listdir(skills_dir)):
        scripts = os.path.join(skills_dir, name, "scripts")
        if name == "audit-orchestrator" or not os.path.isfile(
                os.path.join(scripts, "check.py")):
            continue
        copies.append(("skills/{}/scripts/audit_common.py".format(name), payload))
    return copies


def collect_files():
    """List every file to package, in a stable order."""
    selected = []
    for directory, subdirectories, files in os.walk(ROOT):
        subdirectories[:] = sorted(d for d in subdirectories if d not in EXCLUDE_DIRS)
        for filename in sorted(files):
            # Anything hidden is tooling, not deliverable. Naming artefacts
            # one at a time was how a 283 KB `.coverage` file reached a built
            # zip: it was in `.gitignore`, which this script does not read.
            if filename.startswith("."):
                continue
            if filename in EXCLUDE_NAMES or filename.endswith(EXCLUDE_SUFFIXES):
                continue
            if filename.endswith(".findings.json"):
                continue
            selected.append(os.path.join(directory, filename))
    return selected


def build(out_path):
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
