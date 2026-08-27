#!/usr/bin/env python3
"""Validate marketplace.json and every SKILL.md against the contest rules.

Checks, in the order a judge would apply them:
  1. marketplace.json parses and has name, version and a non-empty skills list
  2. every declared path exists, is inside the marketplace root, and holds a SKILL.md
  3. exactly one skill is marked entrypoint
  4. each SKILL.md has valid YAML frontmatter with name, description and license
  5. the frontmatter `name` equals both the folder name and the manifest `id`
  6. no skill folder on disk is missing from the manifest
  7. field-level rules: name charset and length, description length, body length

Exits non-zero with a numbered list of problems, so it is usable in CI.

Usage:
    python validate_marketplace.py [--root .]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# Constraints taken from the Agent Skills specification at
# https://agentskills.io/specification (checked before this file was written):
# `name` 1-64 chars, lowercase alphanumeric and hyphens, no leading, trailing or
# consecutive hyphens, and must match the parent directory name; `description`
# 1-1024 chars; `compatibility` 1-500 chars; `metadata` a map of string keys to
# string values; `allowed-tools` a space-separated string; body kept under 500
# lines. This pattern already excludes leading, trailing and doubled hyphens.
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MIN, DESCRIPTION_MAX = 1, 1024
COMPATIBILITY_MAX = 500
BODY_MAX_LINES = 500

KNOWN_FRONTMATTER = ("name", "description", "license", "compatibility",
                     "metadata", "allowed-tools")
# `license` is optional in the published spec but required here: the contest
# asks for it on every skill.
REQUIRED_FRONTMATTER = ("name", "description", "license")
RECOMMENDED_SECTIONS = ("When to use", "Inputs", "Procedure", "Output",
                        "Not applicable when", "References")


def parse_frontmatter(text):
    """Parse the small YAML subset a SKILL.md frontmatter block actually uses.

    Deliberately dependency-free: scalars, inline lists and one nesting level
    of mappings. Anything more elaborate than that does not belong in
    frontmatter anyway.
    """
    if not text.startswith("---"):
        return None, "file does not start with a `---` frontmatter fence"
    end = text.find("\n---", 3)
    if end == -1:
        return None, "frontmatter block is not closed with `---`"
    block = text[3:end].strip("\n")
    data = {}
    current_key = None
    for lineno, raw in enumerate(block.splitlines(), start=2):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indented = raw[:1] in (" ", "\t")
        line = raw.strip()
        if indented and current_key:
            if ":" in line:
                key, _, value = line.partition(":")
                if not isinstance(data.get(current_key), dict):
                    data[current_key] = {}
                data[current_key][key.strip()] = _scalar(value.strip())
            elif line.startswith("-"):
                data.setdefault(current_key, [])
                if isinstance(data[current_key], list):
                    data[current_key].append(_scalar(line[1:].strip()))
            else:
                # Continuation of a folded multi-line scalar.
                if isinstance(data.get(current_key), str):
                    data[current_key] = (data[current_key] + " " + line).strip()
            continue
        if ":" not in line:
            return None, "line {}: not a key/value pair".format(lineno)
        key, _, value = line.partition(":")
        current_key = key.strip()
        value = value.strip()
        data[current_key] = _scalar(value) if value else ""
    return data, None


def _scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_scalar(v) for v in inner.split(",") if v.strip()] if inner else []
    if value in ("|", ">", "|-", ">-"):
        return ""
    return value


def validate(root):
    problems = []
    root = os.path.abspath(root)
    manifest_path = os.path.join(root, "marketplace.json")

    if not os.path.isfile(manifest_path):
        return ["marketplace.json is missing from the marketplace root"]
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except json.JSONDecodeError as exc:
        return ["marketplace.json does not parse: {}".format(exc)]

    for key in ("name", "version", "skills"):
        if key not in manifest:
            problems.append("marketplace.json is missing the required `{}` key".format(key))
    if problems:
        return problems

    if not NAME_RE.match(str(manifest["name"])):
        problems.append("marketplace name {!r} must be lowercase letters, digits and "
                        "hyphens".format(manifest["name"]))
    if not isinstance(manifest["skills"], list) or not manifest["skills"]:
        return problems + ["marketplace.json `skills` must be a non-empty list"]

    entrypoints = [s for s in manifest["skills"] if s.get("entrypoint") is True]
    if len(entrypoints) != 1:
        problems.append("exactly one skill must set `entrypoint: true`; found {}".format(
            len(entrypoints)))

    declared_paths = set()
    for index, skill in enumerate(manifest["skills"]):
        label = skill.get("id") or "skills[{}]".format(index)
        for key in ("id", "path"):
            if not skill.get(key):
                problems.append("{}: missing `{}`".format(label, key))
        if not skill.get("id") or not skill.get("path"):
            continue

        skill_id = skill["id"]
        rel_path = skill["path"]
        if os.path.isabs(rel_path) or ".." in rel_path.split("/"):
            problems.append("{}: path {!r} must be relative and inside the marketplace "
                            "root".format(label, rel_path))
            continue
        full = os.path.abspath(os.path.join(root, rel_path))
        if not full.startswith(root + os.sep):
            problems.append("{}: path {!r} resolves outside the marketplace root".format(
                label, rel_path))
            continue
        if not os.path.isdir(full):
            problems.append("{}: path {!r} does not exist".format(label, rel_path))
            continue
        declared_paths.add(full)

        folder = os.path.basename(full)
        if folder != skill_id:
            problems.append("{}: manifest id {!r} does not match folder name {!r}".format(
                label, skill_id, folder))

        skill_md = os.path.join(full, "SKILL.md")
        if not os.path.isfile(skill_md):
            problems.append("{}: no SKILL.md in {}".format(label, rel_path))
            continue
        problems.extend(_validate_skill_md(skill_md, skill_id, folder, label))

    skills_dir = os.path.join(root, "skills")
    if os.path.isdir(skills_dir):
        for entry in sorted(os.listdir(skills_dir)):
            full = os.path.join(skills_dir, entry)
            if not os.path.isdir(full) or entry.startswith((".", "_")):
                continue
            if os.path.abspath(full) not in declared_paths:
                problems.append("skills/{} exists on disk but is not listed in "
                                "marketplace.json".format(entry))

    return problems


def _validate_skill_md(path, skill_id, folder, label):
    problems = []
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    frontmatter, error = parse_frontmatter(text)
    if error:
        return ["{}: SKILL.md frontmatter is invalid - {}".format(label, error)]

    for key in REQUIRED_FRONTMATTER:
        if not frontmatter.get(key):
            problems.append("{}: SKILL.md frontmatter is missing `{}`".format(label, key))

    name = str(frontmatter.get("name", ""))
    if name:
        if not NAME_RE.match(name):
            problems.append("{}: SKILL.md name {!r} must be lowercase letters, digits and "
                            "hyphens".format(label, name))
        if len(name) > NAME_MAX:
            problems.append("{}: SKILL.md name is {} chars, over the {} limit".format(
                label, len(name), NAME_MAX))
        if name != folder:
            problems.append("{}: SKILL.md name {!r} does not match folder {!r}".format(
                label, name, folder))
        if name != skill_id:
            problems.append("{}: SKILL.md name {!r} does not match manifest id {!r}".format(
                label, name, skill_id))

    description = str(frontmatter.get("description", ""))
    if description and not DESCRIPTION_MIN <= len(description) <= DESCRIPTION_MAX:
        problems.append("{}: SKILL.md description is {} chars, outside {}-{}".format(
            label, len(description), DESCRIPTION_MIN, DESCRIPTION_MAX))

    compatibility = frontmatter.get("compatibility")
    if compatibility and len(str(compatibility)) > COMPATIBILITY_MAX:
        problems.append("{}: SKILL.md compatibility is {} chars, over the {} limit".format(
            label, len(str(compatibility)), COMPATIBILITY_MAX))

    # The spec defines `metadata` as a map of string keys to string values, so a
    # bare number here (version: 1.0) is invalid even though YAML accepts it.
    metadata = frontmatter.get("metadata")
    if metadata and not isinstance(metadata, dict):
        problems.append("{}: SKILL.md metadata must be a key/value mapping".format(label))
    elif isinstance(metadata, dict):
        for key, value in metadata.items():
            if not isinstance(value, str):
                problems.append("{}: SKILL.md metadata.{} must be a string".format(label, key))

    allowed_tools = frontmatter.get("allowed-tools")
    if allowed_tools is not None and not isinstance(allowed_tools, str):
        problems.append("{}: SKILL.md allowed-tools must be a space-separated string, "
                        "not a list".format(label))

    unknown = [k for k in frontmatter if k not in KNOWN_FRONTMATTER]
    if unknown:
        problems.append("{}: SKILL.md frontmatter has field(s) not in the spec: {}".format(
            label, ", ".join(sorted(unknown))))

    body = text.split("\n---", 1)[-1]
    body_lines = [line for line in body.splitlines() if line.strip()]
    if len(body.splitlines()) > BODY_MAX_LINES:
        problems.append("{}: SKILL.md body is {} lines, over the {}-line guideline".format(
            label, len(body.splitlines()), BODY_MAX_LINES))
    if not body_lines:
        problems.append("{}: SKILL.md has no body after the frontmatter".format(label))

    missing_sections = [s for s in RECOMMENDED_SECTIONS
                        if not re.search(r"^#{1,4}\s+" + re.escape(s), body, re.M | re.I)]
    if missing_sections:
        problems.append("{}: SKILL.md is missing section heading(s): {}".format(
            label, ", ".join(missing_sections)))

    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                       "..", "..", ".."),
                        help="marketplace root (defaults to the repository root)")
    args = parser.parse_args(argv)

    problems = validate(args.root)
    if problems:
        print("marketplace validation FAILED with {} problem(s):".format(len(problems)))
        for index, problem in enumerate(problems, start=1):
            print("  {}. {}".format(index, problem))
        return 1
    print("marketplace validation passed: manifest, all skill folders and all SKILL.md "
          "frontmatter are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
