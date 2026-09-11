#!/usr/bin/env python3
"""Build the submission zip.

The zip contains the marketplace root directory, as the brief requires. It
excludes caches, virtual environments and audit output.

    python package.py                  # writes dist/brand-ai-readiness-audit.zip
    python package.py --out ../submission.zip
    python package.py --check          # Validate and test first, then build

The script prints the file count and the size. It fails if the zip is larger
than the 50 MB submission limit.

The artefact is written to `dist/`, never beside the files it packages. A 1.8 MB
zip sitting in the checkout root is a file every tool that walks the checkout
then has to know about, and one of them did not: the test that measures whether
the submission is small enough summed the tree it was standing in, counted the
previous build, and failed at 5.1 MB against its own 5 MB bar. Anyone running
the README's test command saw a red suite on an untouched tree. `dist/` is
already in `.gitignore` and in `EXCLUDE_DIRS` below, so the build output is
invisible to the build, to git and to the tests alike.
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGE_NAME = "brand-ai-readiness-audit"
SIZE_LIMIT_MB = 50

# The modification time every entry in the archive carries, in place of the
# moment the build happened to run.
#
# Measured before this was here: two `python package.py` runs over an untouched
# tree produced two different files - sha256 50619860... and 2474af8e... - with
# identical contents in all 230 entries. The difference was in exactly the
# fifteen generated copies, whose timestamps were two seconds apart, because
# `writestr` given a plain name (rather than a `ZipInfo`) stamps the entry with
# `time.localtime()`. The other 215 entries carried file modification times,
# which a fresh clone of the same commit does not reproduce either, so the same
# source produced a different artefact on a different machine.
#
# A build artefact that differs from itself is the plainest failure of
# determinism there is. With this
# constant the archive is a function of the packaged bytes and nothing else:
# same tree, same zip, on any machine, at any time. 1980-01-01 is the earliest
# moment the zip format can store and the value reproducible builds conventionally
# use; the format cannot store a null.
ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

# Where a build lands unless the caller says otherwise. Inside the checkout so
# it is easy to find, inside a directory the packaging ignores so it can never
# become part of what it packages, and inside a directory `.gitignore` already
# covers so it is never committed.
BUILD_DIR = "dist"
DEFAULT_OUT = "{}/{}.zip".format(BUILD_DIR, PACKAGE_NAME)

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
    # Internal working notes, kept out of the deliverable for the same reason
    # as the names above.
    "ROUNDS.md",
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


def _local_modules(root):
    """Module names that live in the orchestrator's scripts directory."""
    scripts = os.path.join(root, ORCHESTRATOR_SCRIPTS)
    return {name[:-3] for name in os.listdir(scripts)
            if name.endswith(".py") and not name.startswith("_")}


def _imports_of(path, available):
    """The shared modules this file imports, read off a parse of the code.

    Parsed rather than pattern-matched. This was a regular expression over the
    raw text - `^\\s*(?:from|import)\\s+([a-z_][a-z0-9_]*)` with `re.M` - which
    cannot tell a line of code from a line of English. Measured over the eleven
    Python files it is run on, it matches four sentences inside docstrings:
    "from a", "from one", "from the", "from being". It picked the right set
    anyway, but only because no shared module is named after an English word,
    and one of them is called `crawl`: an indented docstring line reading "from
    crawl records alone" would have vendored 188 KB into a skill that never
    imports it. That is the over-generous half of the rule, the half that reads
    as padding, and a parse cannot make the mistake.

    Every skill ships `robots_parser.py` even though only two name it.
    `ast.walk` is the reason and is deliberate: `audit_common.py`
    imports the parser from inside two function bodies, so any skill that calls
    `third_party_allows` or `_where_the_clock_went` needs it beside them or
    dies at the call. Import-time-only detection would ship six folders that
    start and then fail mid-run, which is worse than 31 KB.
    """
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found & available


def vendored_shared_library(root):
    """One `(archive path, source path, bytes)` triple per module a skill needs.

    Returned rather than written to disk: the checkout stays single-source, and
    only the zip carries the copies. Resolved transitively, so a vendored module
    that imports another local module brings it along.

    The payload is `archive_bytes` of the source, which is the same function
    that produces the source entry's own bytes a few lines later in `build`.
    That is not a tidiness point. It used to be `open(...).read()` here and a
    byte copy there, and the two disagreed: text mode turns CRLF into LF, so a
    CRLF `audit_common.py` on a Windows checkout shipped as one CRLF source
    beside six LF copies: seven copies across two different versions.
    Line-ending normalisation
    settled the symptom; reading the copies through the one function that
    decides what a packaged file looks like settles the cause, because there is
    now no second reader to disagree with.

    The source path travels with the payload so `build` can build the copy's
    archive entry from the source file itself, giving it the source's own
    permission bits. A generated copy that differs from its source in metadata
    is still a second version of it to anybody listing the archive - and the
    entry the copy used to get instead was stamped with the clock, which is
    what made two builds of one tree produce two different files.
    """
    available = _local_modules(root)
    scripts_dir = os.path.join(root, ORCHESTRATOR_SCRIPTS)
    source_paths = {name: os.path.join(scripts_dir, name + ".py")
                    for name in available}
    sources = {name: archive_bytes(path) for name, path in source_paths.items()}

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
            queue.extend(_imports_of(source_paths[name], available))

        for name in sorted(needed):
            header = VENDOR_HEADER.format(
                source="skills/audit-orchestrator/scripts/{}.py".format(name))
            copies.append(("skills/{}/scripts/{}.py".format(skill, name),
                           source_paths[name],
                           header.encode("utf-8") + sources[name]))
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

# What enters the archive with LF line endings, whatever it has on disk.
#
# An unzipped build turned out to hold mixed CRLF and LF. That is
# what a checkout on Windows produces: 33 of the 94 Python files here are CRLF
# and 61 are LF, and `.md`, `.json`, `.xml` and `.html` are split the same way,
# so the shipped zip carried both conventions with nothing having chosen
# either. Worse, `package.py` copies source files byte for byte and writes the
# vendored copies from text it has already read - which converts CRLF to LF -
# so a CRLF `audit_common.py` shipped alongside six LF copies of itself, and
# the "byte-identical copy" the packaging promises was true only of the files
# that happened to be LF already. Normalising on the way in settles both.
#
# This is the deliverable's text, listed by extension rather than by "not
# binary", so a file type added later is normalised only when someone says it
# may be. `.pdf` is the one allowed suffix deliberately absent: the fixture
# rate card is the evidence for the "facts locked in a PDF" check, its bytes
# are the point, and a CR inside a compressed stream is data, not a line
# ending. Files with no extension are not normalised either - `LICENSE` is
# already LF, and matching by name would be a second list to keep in step.
NORMALISED_SUFFIXES = (".py", ".md", ".json", ".txt", ".xml", ".html", ".ini",
                       ".cfg")


def archive_bytes(path):
    """One file's contents as they should enter the zip."""
    with open(path, "rb") as handle:
        raw = handle.read()
    if not path.endswith(NORMALISED_SUFFIXES):
        return raw
    # CRLF first, then any lone CR, so a file carrying both ends up with one
    # convention rather than a third. Inside these formats a bare CR byte is
    # only ever whitespace: JSON escapes a carriage return inside a string as
    # `\r`, and Python source cannot hold a raw one outside a string literal.
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _entry_for(source_path, relative):
    """The archive entry for one packaged file, with the clock taken out of it.

    `ZipInfo.from_file` reads the permission bits, which the deliverable does
    care about - `run_audit.py` and the six `check.py` files carry an exec bit
    on a POSIX checkout and anyone may run them directly. It also reads the
    modification time, which the deliverable does not care about and which
    nothing reproduces: a fresh clone stamps every file with the moment it was
    cloned. `ARCHIVE_TIMESTAMP` replaces it, so the archive is decided by the
    packaged bytes and the file modes alone.
    """
    entry = zipfile.ZipInfo.from_file(
        source_path, "{}/{}".format(PACKAGE_NAME, relative))
    entry.date_time = ARCHIVE_TIMESTAMP
    return entry


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


def why_the_artefact_cannot_go_there(out_path):
    """Why this output path would put the build inside the thing it builds.

    Empty string when the path is fine. The zip is not itself packaged - `.zip`
    is not an allowed suffix - but a build artefact sitting among the source is
    still a file every tool that walks the checkout has to know to ignore, and
    the test that measures the submission's size did not: it counted the
    previous build and failed on an otherwise untouched tree.

    So the rule is about where the file lands, not about what goes in the zip.
    Anywhere outside the checkout is fine, as is any directory the packaging
    already ignores; the root and the three packaged trees are not.
    """
    try:
        relative = os.path.relpath(os.path.abspath(out_path), ROOT)
    except ValueError:                     # a different drive, on Windows
        return ""
    parts = relative.replace(os.sep, "/").split("/")
    if parts[0] == "..":
        return ""
    if len(parts) == 1:
        return "the checkout root, which is the directory being packaged"
    if parts[0] in ALLOWED_TREES:
        return "{}/, which is packaged in full".format(parts[0])
    return ""


def build(out_path):
    where = why_the_artefact_cannot_go_there(out_path)
    if where:
        print("Refusing to build: {} would put the artefact in {}.".format(
            out_path, where), file=sys.stderr)
        print("Write it to {} (the default) or to a path outside the checkout.".format(
            DEFAULT_OUT), file=sys.stderr)
        raise SystemExit(2)

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
    # Sorted by the name each entry gets in the archive, so the order is a
    # property of the tree and not of how the filesystem happened to list it.
    #
    # Building the zip from a clean unzip of the shipped one gave the same
    # 239 entries with **zero differing CRCs** and a different entry order.
    # Two builds on one machine already matched, because `os.walk` is stable
    # there; two machines did not. Reproducible on one machine is not
    # reproducible - anyone checking the artefact against a
    # rebuild has to be able to compare bytes, and the whole point of the fixed
    # timestamp above is defeated by an order nobody chose.
    entries = [(os.path.relpath(path, ROOT).replace(os.sep, "/"), path, None)
               for path in files]
    entries += [(relative, source_path, payload)
                for relative, source_path, payload in copies]
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, source_path, payload in sorted(entries):
            # A vendored copy carries its payload; a source entry is read off
            # disk. Both go through one `ZipInfo`, because `writestr` given a
            # plain name stamps the entry with the clock, which is what made
            # two builds of one tree differ before.
            archive.writestr(
                _entry_for(source_path, relative),
                archive_bytes(source_path) if payload is None else payload,
                zipfile.ZIP_DEFLATED)
    return files + [name for name, _, _ in copies], os.path.getsize(out_path)


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
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="Path of the zip file to write (default %(default)s)")
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

    # Added up by hand, the generated copies read as padding: "3 MB of an
    # 8.4 MB archive buying nothing". The number is right and the build
    # should be the one saying it, so it is visible on every
    # build rather than discovered by someone unzipping the submission.
    with zipfile.ZipFile(args.out) as archive:
        unpacked = sum(item.file_size for item in archive.infolist())
    copies = vendored_shared_library(ROOT)
    vendored = sum(len(payload) for _, _, payload in copies)
    print("Of which generated copies: {} files, {:.2f} MB of {:.2f} MB unpacked "
          "({:.0f}%).".format(len(copies), vendored / (1024 * 1024),
                              unpacked / (1024 * 1024), 100.0 * vendored / unpacked))
    print("Each is a module the skill beside it imports, so that a folder "
          "lifted out of the marketplace still runs.")

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
