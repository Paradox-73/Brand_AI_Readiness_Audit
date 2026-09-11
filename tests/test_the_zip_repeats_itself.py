"""Two things counted by hand in the shipped archive, made into assertions.

An independent review of the archive raised two problems. The first was
determinism. The second was the shape of the archive: "it ships
`audit_common.py` (431 KB) in seven copies across **two different versions**,
`robots_parser.py` in seven, `page_extract.py` in three" - and separately, that
"7 x 431 KB of generated copies ... is 3 MB of an 8.4 MB archive buying nothing
inside the marketplace".

The copies themselves are deliberate: each skill
folder carries the modules it imports so that a folder lifted out of the
marketplace still runs, which is the brief's portability rule. What was not
deliberate:

  * **Two versions.** `package.py` read the copies in text mode, which turns
    CRLF into LF, and wrote the source through a separate byte copy. On a
    Windows checkout `audit_common.py` is CRLF, so one CRLF source shipped
    beside six LF copies of itself. Both halves now go through
    `package.archive_bytes`, so there is one reader and there can be one
    result.
  * **Two archives.** Two `python package.py` runs over an untouched tree
    produced different files - sha256 50619860... and 2474af8e... - identical
    in all 230 entries and differing in the timestamps of exactly the fifteen
    generated ones, because `writestr` given a plain name stamps the entry with
    the clock. The other 215 carried file modification times, which a fresh
    clone does not reproduce either.
  * **A rule that read prose.** What to vendor came from a regular expression
    over the raw text of each file. Over the eleven files it runs on it matches
    four English sentences inside docstrings - "from a", "from one", "from
    the", "from being" - and shipped the right set only because no shared
    module is named after an English word. One of them is called `crawl`.

These tests build into a temporary directory. They never touch `dist/`: the
artefact there is the one that ships, and rebuilding it to test the packaging
would mix two builds into one submission.
"""

from __future__ import annotations

import hashlib
import os
import sys
import zipfile

import pytest

from conftest import ROOT, SUB_SKILLS

sys.path.insert(0, ROOT)

import package  # noqa: E402

PACKAGE = package.PACKAGE_NAME
VENDOR_MARKER = b"GENERATED COPY - do not edit"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A zip built the way `python package.py` builds one, somewhere harmless."""
    out = str(tmp_path_factory.mktemp("repeatable") / "submission.zip")
    package.build(out)
    return out


# --------------------------------------------------------------------------
# The same tree has to produce the same archive
# --------------------------------------------------------------------------

def test_no_entry_carries_the_moment_the_build_ran(built):
    """The mechanism, checked directly, because the outcome test below can pass
    by luck.

    A zip stores timestamps to two-second granularity, so two builds a second
    apart could land in the same bucket and match even with the clock still in
    the archive. This cannot: every entry carries one constant, so the archive
    depends on the packaged bytes and the file modes and nothing else. That is
    also the only form of the property that survives a fresh clone, where every
    modification time is the moment of cloning.
    """
    with zipfile.ZipFile(built) as archive:
        stamped = {item.filename: item.date_time for item in archive.infolist()}
    assert stamped, "the build produced an empty archive"
    wrong = {name: when for name, when in stamped.items()
             if when != package.ARCHIVE_TIMESTAMP}
    assert not wrong, (
        "{} entries carry a time that is not ARCHIVE_TIMESTAMP, so the archive "
        "records when it was built rather than what it contains: {}".format(
            len(wrong), sorted(wrong.items())[:5]))


def test_two_builds_of_the_same_tree_are_the_same_archive(tmp_path):
    """The outcome. Byte-for-byte, not entry-by-entry.

    If this fails while the test above passes, the difference is in the
    contents rather than the metadata - which on a shared checkout can simply
    mean a file changed between the two builds. The failure message names the
    entries so the two cases can be told apart.
    """
    first = str(tmp_path / "one.zip")
    second = str(tmp_path / "two.zip")
    package.build(first)
    package.build(second)

    digests = [hashlib.sha256(open(path, "rb").read()).hexdigest()
               for path in (first, second)]
    if digests[0] == digests[1]:
        return

    with zipfile.ZipFile(first) as a, zipfile.ZipFile(second) as b:
        names_a = [item.filename for item in a.infolist()]
        names_b = [item.filename for item in b.infolist()]
        assert names_a == names_b, "the two builds selected different files"
        differing = [name for name in names_a if a.read(name) != b.read(name)]
    raise AssertionError(
        "two builds of one tree produced different archives ({} vs {}); "
        "entries whose bytes differ: {}".format(
            digests[0][:12], digests[1][:12], differing[:10] or "none, so the "
            "difference is in entry metadata"))


# --------------------------------------------------------------------------
# What decides which modules a skill folder carries
# --------------------------------------------------------------------------

def test_the_vendoring_rule_reads_code_and_not_prose(tmp_path):
    """A docstring that talks about a module must not ship that module.

    The rule this replaced matched `from`/`import` at the start of any line in
    the raw text. The file below is what that costs: no import statement in it,
    and a regex reads two. `crawl.py` is 188 KB.
    """
    prose = tmp_path / "prose.py"
    prose.write_text(
        '"""How the check reads a sitemap.\n'
        "\n"
        "    from crawl records alone the audit can tell a sitemap from a\n"
        "    homepage, so no extra request is needed.\n"
        "    import robots_parser is what an import of it would look like.\n"
        '"""\n'
        "import os\n",
        encoding="utf-8")
    available = {"crawl", "robots_parser", "audit_common"}
    assert package._imports_of(str(prose), available) == set(), (
        "a docstring naming a module made the packaging vendor it")


def test_the_vendoring_rule_sees_an_import_inside_a_function(tmp_path):
    """And the opposite mistake, which is the expensive one.

    `audit_common.py` imports `robots_parser` from inside two function bodies.
    A rule that only read module-level imports would ship six skill folders
    that start and then fail at the call, so this is why every skill carries
    the parser and not an oversight.
    """
    lazy = tmp_path / "lazy.py"
    lazy.write_text(
        "def third_party_allows(url):\n"
        "    from robots_parser import is_disallowed\n"
        "    return not is_disallowed(url)\n",
        encoding="utf-8")
    assert package._imports_of(str(lazy), {"robots_parser"}) == {"robots_parser"}

    source = os.path.join(ROOT, package.ORCHESTRATOR_SCRIPTS, "audit_common.py")
    assert "robots_parser" in package._imports_of(
        source, package._local_modules(ROOT)), (
        "audit_common.py no longer imports robots_parser; if that is "
        "deliberate, every skill folder can stop carrying 31 KB of it")


# --------------------------------------------------------------------------
# Exactly what each folder needs, and nothing else
# --------------------------------------------------------------------------

def _vendored_by_skill(archive_path):
    """Skill name -> the generated copies in its scripts directory."""
    found = {skill: set() for skill in SUB_SKILLS}
    with zipfile.ZipFile(archive_path) as archive:
        for item in archive.infolist():
            parts = item.filename.split("/")
            if len(parts) != 5 or parts[1] != "skills" or parts[3] != "scripts":
                continue
            if not parts[4].endswith(".py"):
                continue
            if VENDOR_MARKER not in archive.read(item.filename)[:800]:
                continue
            found.setdefault(parts[2], set()).add(parts[4][:-3])
    return found


def _needed_by(skill):
    """The transitive closure of shared modules that skill's check.py imports."""
    available = package._local_modules(ROOT)
    scripts = os.path.join(ROOT, package.ORCHESTRATOR_SCRIPTS)
    needed = set()
    queue = list(package._imports_of(
        os.path.join(ROOT, "skills", skill, "scripts", "check.py"), available))
    while queue:
        name = queue.pop()
        if name in needed:
            continue
        needed.add(name)
        queue.extend(package._imports_of(
            os.path.join(scripts, name + ".py"), available))
    return needed


@pytest.mark.parametrize("skill", SUB_SKILLS)
def test_a_skill_folder_carries_no_module_it_does_not_need(built, skill):
    """The half the existing tests do not cover.

    `test_the_built_zip_works.py` asserts every module a skill imports is
    present. Nothing asserted the converse, and the converse is what was
    measured: a 315 KB `compose_report.py` "inside `fact-extractability-audit`
    where nothing composes a report". That one is a real import - the skill
    reads `page_shaped_urls` and `why_little_was_read` out of it - so it stays
    until those two move, but the next one might not be, and this fails on it.
    """
    carried = _vendored_by_skill(built)[skill]
    needed = _needed_by(skill)
    assert carried == needed, (
        "{} carries {} and needs {}; {} is dead weight in the archive".format(
            skill, sorted(carried), sorted(needed), sorted(carried - needed)))


def test_the_only_duplicated_files_in_the_zip_are_generated_copies(built):
    """The archive held seven `audit_common.py` and seven `robots_parser.py`.

    Every one of them has to be a copy the packaging made on purpose and
    labelled as such. A second file arriving under a name that already exists,
    by a hand-copy or a stray, is the thing this refuses.
    """
    with zipfile.ZipFile(built) as archive:
        by_name = {}
        for item in archive.infolist():
            if not item.filename.endswith(".py"):
                continue
            by_name.setdefault(os.path.basename(item.filename), []).append(
                item.filename)
        unexplained = []
        for name, paths in sorted(by_name.items()):
            if len(paths) == 1:
                continue
            for path in paths:
                head = archive.read(path)[:800]
                if VENDOR_MARKER in head:
                    continue
                # The one source of each shared module, and the six check.py
                # files, which share a name and share nothing else.
                if path.startswith("{}/skills/audit-orchestrator/".format(PACKAGE)):
                    continue
                if name == "check.py":
                    continue
                unexplained.append(path)
    assert not unexplained, (
        "these files duplicate a name in the archive and are not generated "
        "copies: {}".format(unexplained))


def test_every_generated_copy_is_a_module_some_skill_imports(built):
    """No copy exists that no skill asked for.

    Stated over the whole archive rather than per skill, so a copy landing in a
    directory that is not a sub-skill at all - the orchestrator, a references
    folder - is caught too.
    """
    wanted = set()
    for skill in SUB_SKILLS:
        for module in _needed_by(skill):
            wanted.add("{}/skills/{}/scripts/{}.py".format(PACKAGE, skill, module))

    with zipfile.ZipFile(built) as archive:
        generated = {item.filename for item in archive.infolist()
                     if item.filename.endswith(".py")
                     and VENDOR_MARKER in archive.read(item.filename)[:800]}
    assert generated == wanted, (
        "generated copies the imports do not ask for: {}; asked for and "
        "missing: {}".format(sorted(generated - wanted), sorted(wanted - generated)))
