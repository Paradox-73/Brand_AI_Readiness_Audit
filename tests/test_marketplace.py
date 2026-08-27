"""Marketplace format and engineering hygiene.

These tests protect the rules the contest states outright: a valid manifest,
exactly one entrypoint, a spec-compliant SKILL.md in every listed folder, and
no dangling references. They also protect one rule this marketplace sets for
itself - a shared root-cause vocabulary - because a skill inventing a tag would
silently stop deduplicating rather than failing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

from conftest import ROOT, SCRIPTS, SUB_SKILLS

sys.path.insert(0, SCRIPTS)

from audit_common import MECHANISMS, ROOT_CAUSES, SEVERITY_RANK  # noqa: E402
from validate_marketplace import parse_frontmatter, validate  # noqa: E402

SKILLS_DIR = os.path.join(ROOT, "skills")
ALL_SKILLS = ["audit-orchestrator"] + SUB_SKILLS

# Sections the orchestrator's own convention requires, in this order.
REQUIRED_SECTIONS = ("When to use", "Inputs", "Procedure", "Output",
                     "Not applicable when", "References")


def skill_md(name):
    with open(os.path.join(SKILLS_DIR, name, "SKILL.md"), encoding="utf-8") as handle:
        return handle.read()


# --------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------

def test_marketplace_validates():
    problems = validate(ROOT)
    assert not problems, "marketplace validation failed:\n" + "\n".join(
        "  - " + p for p in problems)


def test_validator_cli_exits_zero():
    completed = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "validate_marketplace.py")],
        cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_manifest_shape():
    with open(os.path.join(ROOT, "marketplace.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)

    assert manifest["name"] == "brand-ai-readiness-audit"
    assert re.match(r"^\d+\.\d+\.\d+$", manifest["version"])

    entrypoints = [s for s in manifest["skills"] if s.get("entrypoint")]
    assert len(entrypoints) == 1, "exactly one skill must be the entrypoint"
    assert entrypoints[0]["id"] == "audit-orchestrator"

    ids = [s["id"] for s in manifest["skills"]]
    assert sorted(ids) == sorted(ALL_SKILLS)
    assert len(ids) == len(set(ids)), "duplicate skill ids"

    for skill in manifest["skills"]:
        assert skill["path"] == "skills/{}".format(skill["id"])
        assert not os.path.isabs(skill["path"])
        assert ".." not in skill["path"]


def test_every_skill_folder_is_listed():
    with open(os.path.join(ROOT, "marketplace.json"), encoding="utf-8") as handle:
        listed = {s["id"] for s in json.load(handle)["skills"]}
    on_disk = {d for d in os.listdir(SKILLS_DIR)
               if os.path.isdir(os.path.join(SKILLS_DIR, d)) and not d.startswith((".", "_"))}
    assert on_disk == listed, "manifest and skills/ disagree: {}".format(on_disk ^ listed)


# --------------------------------------------------------------------------
# SKILL.md format
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ALL_SKILLS)
def test_skill_frontmatter(name):
    frontmatter, error = parse_frontmatter(skill_md(name))
    assert error is None, error

    assert frontmatter["name"] == name, "frontmatter name must equal the folder name"
    assert re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", name)
    assert len(name) <= 64
    assert "--" not in name and not name.startswith("-") and not name.endswith("-")

    assert 1 <= len(frontmatter["description"]) <= 1024
    assert frontmatter["license"] == "MIT"

    # The live spec defines allowed-tools as a space-separated string and
    # metadata as a map of string keys to string values.
    assert isinstance(frontmatter["allowed-tools"], str)
    assert isinstance(frontmatter["metadata"], dict)
    for key, value in frontmatter["metadata"].items():
        assert isinstance(value, str), "metadata.{} must be a string".format(key)

    if "compatibility" in frontmatter:
        assert len(frontmatter["compatibility"]) <= 500


@pytest.mark.parametrize("name", ALL_SKILLS)
def test_skill_description_says_what_and_when(name):
    """The description is what makes an agent choose the skill; it must earn that."""
    description = parse_frontmatter(skill_md(name))[0]["description"]
    assert len(description) >= 120, "description is too thin to trigger reliably"
    assert re.search(r"\bUse (this )?(skill )?(when|for)\b", description, re.I), \
        "description should say when to use the skill, not only what it does"


@pytest.mark.parametrize("name", ALL_SKILLS)
def test_skill_body_sections_present_and_ordered(name):
    body = skill_md(name).split("\n---", 1)[-1]
    positions = []
    for section in REQUIRED_SECTIONS:
        match = re.search(r"^#{2,4}\s+" + re.escape(section), body, re.M | re.I)
        assert match, "{}: SKILL.md has no `{}` section".format(name, section)
        positions.append(match.start())
    assert positions == sorted(positions), \
        "{}: SKILL.md sections are out of order".format(name)


@pytest.mark.parametrize("name", ALL_SKILLS)
def test_skill_body_is_lean(name):
    """The spec recommends under 500 lines; detail belongs in references/."""
    lines = skill_md(name).split("\n---", 1)[-1].splitlines()
    assert len(lines) < 300, "{}: SKILL.md body is {} lines".format(name, len(lines))


@pytest.mark.parametrize("name", ALL_SKILLS)
def test_skill_references_resolve(name):
    """No SKILL.md may cite a reference file that does not exist."""
    body = skill_md(name)
    cited = set(re.findall(r"`(references/[A-Za-z0-9_.-]+)`", body))
    assert cited, "{}: SKILL.md cites no reference files".format(name)
    for relative in sorted(cited):
        path = os.path.join(SKILLS_DIR, name, relative)
        assert os.path.isfile(path), "{}: dangling reference {}".format(name, relative)
        assert os.path.getsize(path) > 500, "{}: {} is a stub".format(name, relative)


def test_no_real_domain_names_anywhere():
    """The brief forbids naming real sites in skills, references or tests."""
    # The rule forbids keying anything to a real *site*: no example sites, no
    # domains the checks recognise by name. It does not forbid naming the
    # standards bodies whose schemas we emit, the platforms `sameAs` should
    # point at, or the operators of the crawlers we document by user agent -
    # all of which are necessarily real and none of which is an example site.
    allowed = {
        # Standards and APIs the code actually talks to or emits.
        "schema.org", "agentskills.io", "wikidata.org", "wikipedia.org",
        "sitemaps.org", "json-schema.org",
        # Platforms recognised as authoritative `sameAs` targets.
        "crunchbase.com", "linkedin.com", "github.com", "google.com",
        "trustpilot.com", "yelp.com", "glassdoor.com", "medium.com",
        "pinterest.com", "threads.net", "bsky.app", "youtube.com", "youtu.be",
        "vimeo.com", "loom.com", "twitter.com", "x.com", "instagram.com",
        "facebook.com", "tiktok.com", "goo.gl", "g.page",
        # Crawler operators named in ai-crawler-user-agents.md.
        "webz.io", "you.com",
        # Reserved documentation domains.
        "example.com", "example.invalid",
    }
    pattern = re.compile(r"\b(?:https?://)?(?:www\.)?([a-z0-9-]+\.(?:com|net|org|io|co|ai))\b", re.I)
    offenders = []
    for base in (SKILLS_DIR, os.path.join(ROOT, "tests")):
        for directory, _, files in os.walk(base):
            if "__pycache__" in directory:
                continue
            for filename in files:
                if not filename.endswith((".md", ".py", ".html", ".json", ".txt", ".xml")):
                    continue
                path = os.path.join(directory, filename)
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    for domain in pattern.findall(handle.read()):
                        low = domain.lower()
                        if low in allowed or low.endswith(".example") or "example" in low:
                            continue
                        offenders.append("{}: {}".format(os.path.relpath(path, ROOT), domain))
    assert not offenders, "real domain names found:\n" + "\n".join(sorted(set(offenders))[:20])


# --------------------------------------------------------------------------
# The shared vocabulary
# --------------------------------------------------------------------------

def test_root_cause_vocabulary_is_shared_and_closed():
    """Every root cause a skill emits must be in the shared frozen set."""
    used = set()
    for name in SUB_SKILLS:
        path = os.path.join(SKILLS_DIR, name, "scripts", "check.py")
        with open(path, encoding="utf-8") as handle:
            used.update(re.findall(r'root_cause="([a-z-]+)"', handle.read()))
    assert used, "no root causes found in the sub-skills"
    unknown = used - ROOT_CAUSES
    assert not unknown, "root causes used but not declared in audit_common: {}".format(unknown)


def test_mechanism_letters_are_declared():
    used = set()
    for name in SUB_SKILLS:
        path = os.path.join(SKILLS_DIR, name, "scripts", "check.py")
        with open(path, encoding="utf-8") as handle:
            used.update(re.findall(r'mechanism="([A-G])"', handle.read()))
    assert used <= set(MECHANISMS)
    assert "G" in MECHANISMS, "mechanism G is this marketplace's engagement extension"


def test_make_finding_rejects_bad_input():
    """The finding constructor is strict on purpose: typos must fail loudly."""
    from audit_common import make_finding

    good = dict(
        id_hint="x", title="t", severity="high", confidence="high", evidence="e",
        mechanism="A", root_cause="robots-block", summary="s",
        how_to_fix=["do the thing"], effort="low",
        rationale="r", owner="developer")
    make_finding(**good)  # baseline must work

    for field, bad_value in [
        ("severity", "catastrophic"),
        ("confidence", "certain"),
        ("mechanism", "Z"),
        ("root_cause", "made-up-tag"),
        ("effort", "enormous"),
        ("how_to_fix", []),
    ]:
        with pytest.raises(ValueError):
            make_finding(**dict(good, **{field: bad_value}))


def test_severity_ranks_are_ordered():
    assert SEVERITY_RANK["critical"] < SEVERITY_RANK["high"] < SEVERITY_RANK["medium"] \
        < SEVERITY_RANK["low"] < SEVERITY_RANK["info"]


# --------------------------------------------------------------------------
# Packaging
# --------------------------------------------------------------------------

def test_root_contains_what_the_brief_requires():
    for filename in ("marketplace.json", "README.md", "LICENSE", "requirements.txt",
                     "run_audit.py"):
        assert os.path.isfile(os.path.join(ROOT, filename)), "missing {}".format(filename)


def test_marketplace_is_small_enough_to_submit():
    """The zip limit is 50 MB; this should be nowhere near it."""
    total = 0
    for directory, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs
                   if d not in ("__pycache__", ".git", ".pytest_cache", "out", ".venv")]
        for filename in files:
            total += os.path.getsize(os.path.join(directory, filename))
    megabytes = total / (1024 * 1024)
    assert megabytes < 5, "marketplace is {:.1f} MB before compression".format(megabytes)
