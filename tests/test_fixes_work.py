"""Does following our own advice actually clear the finding?

The brief asks whether the marketplace "produces fixes that ... actually resolve
the problems it finds". Everywhere else we assert that a check fires when a site
is broken. That is only half the claim. The other half is that the thing we tell
someone to do works - and until this file existed, nothing tested it.

Each case here breaks a property, reads the finding, takes the code the report
handed the user, pastes it exactly as instructed, and audits again. The finding
has to be gone. Not reduced, not softened: gone.

Only findings that ship paste-ready code are testable this way. "Write one
sentence saying what your company is" cannot be verified by a machine, and this
file does not pretend otherwise - see `UNTESTABLE_BY_MACHINE` at the bottom.
"""

from __future__ import annotations

import os
import re
import shutil

import pytest

from conftest import _audit
from mutations import MUTATIONS

pytestmark = pytest.mark.mutation

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# (mutation name, root cause the fix must clear, where the snippet goes)
# "head" means the <head> of every page the finding names; anything else is a
# filename at the site root, which is how the two non-HTML fixes are delivered.
CASES = [
    ("all JSON-LD is removed", "no-org-schema", "head"),
    ("all JSON-LD is removed", "no-website-schema", "head"),
    ("Article markup is stripped from the posts", "no-article-schema", "head"),
    ("the site has a search box but no WebSite markup describing it",
     "no-website-schema", "head"),
    ("all JSON-LD is removed", "no-faq-schema", "head"),
    ("all JSON-LD is removed", "no-product-schema", "head"),
    ("robots.txt disallows the answer-engine crawlers", "robots-block", "robots.txt"),
    ("the sitemap is gone", "sitemap-missing", "sitemap.xml"),
]

# Some fixes have a step besides pasting the code, and the report says so in
# `how_to_fix`. Following the advice means following all of it, so those steps
# are applied here too - each one quoting the instruction it carries out.
EXTRA_STEPS = {
    # "Add `Sitemap: <origin>/sitemap.xml` as the last line of robots.txt."
    "sitemap-missing": lambda site, base: _append(
        os.path.join(site, "robots.txt"),
        os.linesep + "Sitemap: " + base + "/sitemap.xml" + os.linesep),
}


def _append(path, text):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)

# The sitemap snippet is a shape to copy, not a file to publish: it lists two
# example URLs. Pasting it verbatim clears `sitemap-missing`, which is what this
# file tests, and correctly raises `sitemap-broken` because the example URLs do
# not exist. That second finding is the audit being right about a user who
# pasted a template without editing it.
TEMPLATE_SNIPPETS = {"sitemap-missing"}


def _local_path(site, base_url, page_url):
    """Map a crawled URL back to the file on disk that served it."""
    relative = page_url[len(base_url):].lstrip("/") or "index.html"
    relative = relative.split("?")[0].split("#")[0]
    if relative.endswith("/") or not relative:
        relative += "index.html"
    if not relative.endswith((".html", ".txt", ".xml")):
        relative = os.path.join(relative, "index.html")
    candidate = os.path.join(site, relative.replace("/", os.sep))
    return candidate if os.path.isfile(candidate) else None


def _paste_into_head(path, snippet):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    assert "</head>" in text, "cannot paste into {}: no </head>".format(path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text.replace("</head>", snippet + "\n</head>", 1))


def _audit_copy(tmp_path_factory, mutation_name, label):
    work = str(tmp_path_factory.mktemp(label))
    site = os.path.join(work, "site")
    shutil.copytree(os.path.join(FIXTURES, "good-site"), site)
    case = next(m for m in MUTATIONS if m["name"] == mutation_name)
    case["apply"](site)
    return work, site


def _run(site, work, suffix):
    out = os.path.join(work, "out-" + suffix)
    os.makedirs(out, exist_ok=True)
    return _audit("good-site", out, site_dir=site)


@pytest.mark.parametrize("mutation_name,root_cause,target", CASES,
                         ids=["{}::{}".format(m.split()[0], rc) for m, rc, _ in CASES])
def test_pasting_the_snippet_clears_the_finding(mutation_name, root_cause, target,
                                                tmp_path_factory):
    work, site = _audit_copy(tmp_path_factory, mutation_name, "fixwork")

    server, before = _run(site, work, "before")
    try:
        matches = [f for f in before.report["findings"] if f["root_cause"] == root_cause]
        assert matches, "{} did not produce {}".format(mutation_name, root_cause)
        finding = matches[0]
        snippet = (finding.get("suggested_action") or {}).get("snippet")
        assert snippet, (
            "{} ships no snippet, so a user has nothing to paste. Either add one "
            "or remove this case.".format(root_cause))
        if root_cause not in TEMPLATE_SNIPPETS:
            assert "&lt;" not in snippet and "<category>" not in snippet, (
                "the snippet contains placeholder text rather than the site's own "
                "values, so pasting it verbatim would publish nonsense")
        if target == "head":
            targets = [_local_path(site, before.base_url, url)
                       for url in finding["affected_pages"]]
            targets = [t for t in targets if t]
            assert targets, "the finding names no page a user could edit"
        else:
            targets = [os.path.join(site, target)]
        base_url = before.base_url
    finally:
        server.__exit__(None, None, None)

    if target == "head":
        for path in targets:
            _paste_into_head(path, snippet)
    else:
        with open(targets[0], "w", encoding="utf-8") as handle:
            handle.write(snippet.rstrip() + os.linesep)
    extra = EXTRA_STEPS.get(root_cause)
    if extra:
        extra(site, base_url)

    server, after = _run(site, work, "after")
    try:
        still_there = [f for f in after.report["findings"] if f["root_cause"] == root_cause]
        assert not still_there, (
            "the user pasted exactly what the report told them to, into exactly "
            "the pages it named, and {} is still reported: {}".format(
                root_cause, still_there[0]["evidence"][:200]))
    finally:
        server.__exit__(None, None, None)


def test_a_snippet_is_valid_json_ld(tmp_path_factory):
    """A snippet that does not parse is worse than no snippet."""
    import json

    work, site = _audit_copy(tmp_path_factory, "all JSON-LD is removed", "jsonwork")
    server, report = _run(site, work, "parse")
    try:
        checked = 0
        for finding in report.report["findings"]:
            snippet = (finding.get("suggested_action") or {}).get("snippet") or ""
            if "application/ld+json" not in snippet:
                continue
            body = re.sub(r"</?script[^>]*>", "", snippet).strip()
            json.loads(body)  # raises if we emit malformed JSON
            checked += 1
        assert checked >= 3, "expected several JSON-LD snippets to check, got {}".format(checked)
    finally:
        server.__exit__(None, None, None)


# Findings whose fix is prose. A machine cannot confirm that a sentence a human
# writes says what the company is, so these are verified by the blind check in
# the review plan instead of here. Listed so the gap is stated rather than
# implied by absence.
# `missing-schema-props` is covered indirectly: its snippet is the same complete
# Organization block that `no-org-schema` hands out, and that one is tested
# above. Listed here rather than duplicated as a case.
UNTESTABLE_BY_MACHINE = {
    "no-entity-definition",   # "write one sentence saying what the brand is"
    "fluff-first",            # "open each section with the answer"
    "nap-inconsistency",      # "agree one canonical version of each fact"
    "missing-core-fact",      # "state the price, the address, the contact route"
    "long-sentences",         # "shorten these sentences"
}


def test_every_code_shipping_finding_is_covered_or_declared():
    """A finding that hands out code must either be tested here or explained."""
    covered = {rc for _, rc, _ in CASES} | {"missing-schema-props"}
    ships_code = set()
    skills_dir = os.path.abspath(os.path.join(os.path.dirname(FIXTURES), "..", "skills"))
    for name in sorted(os.listdir(skills_dir)):
        path = os.path.join(skills_dir, name, "scripts", "check.py")
        if not os.path.isfile(path):
            continue
        # Split on the call itself rather than matching a closing bracket:
        # an earlier version missed half the blocks because their formatting
        # differed, so the coverage it reported was fiction.
        for block in open(path, encoding="utf-8").read().split("result.add(")[1:]:
            if "snippet=" not in block:
                continue
            match = re.search(r'root_cause="([a-z0-9-]+)"', block)
            if match:
                ships_code.add(match.group(1))
    unexplained = ships_code - covered - UNTESTABLE_BY_MACHINE
    assert not unexplained, (
        "these findings hand the user code but nothing proves the code works: "
        "{}. Add a case to CASES, or say why it cannot be machine-checked.".format(
            sorted(unexplained)))
