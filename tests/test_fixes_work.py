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
#
# Four entries left this list when the fold landed. On "all
# JSON-LD is removed" the site publishes no markup in any form, and
# structured-data-audit now says that once instead of six times, so
# `no-website-schema`, `no-faq-schema` and `no-product-schema` no longer fire
# on that mutation and there is no separate finding to paste. Their guarantee
# did not go anywhere: `test_following_the_report_in_order_clears_every_type`
# below walks the same mutation the way the report tells a reader to walk it -
# identity block first, then one type per template - and asserts each of them
# is named, comes back with a block once the first one is live, and clears when
# that block is pasted. Same paste, same re-audit, one step further back.
CASES = [
    ("all JSON-LD is removed", "no-org-schema", "head"),
    ("Article markup is stripped from the posts", "no-article-schema", "head"),
    ("the site has a search box but no WebSite markup describing it",
     "no-website-schema", "head"),
    ("robots.txt disallows the answer-engine crawlers", "robots-block", "robots.txt"),
    ("the sitemap is gone", "sitemap-missing", "sitemap.xml"),
]

# The mutation that leaves the site with no structured data of any kind, which
# is the only state the fold applies to.
NO_MARKUP_AT_ALL = "all JSON-LD is removed"

# What the one folded finding must still name. Each entry is a fragment of the
# text a reader sees, and between them they carry every type and every count
# the six separate findings used to carry: a reader can still see that two
# article pages, one product page, one page of questions and four deep pages
# are involved.
FOLD_MUST_STILL_NAME = (
    "Organization",            # the site-wide identity block
    "Article", "2 of 2 article pages",
    "FAQPage", "Product", "WebSite", "BreadcrumbList",
    "4 pages",                 # the deep pages the breadcrumb finding counted
    "1 page of questions and answers",
)

# The types the fold defers to step 2. Each ships a paste-ready block again as
# soon as the site carries any markup at all, and the walk below proves each
# one clears.
DEFERRED_BY_THE_FOLD = ("no-article-schema", "no-faq-schema",
                        "no-product-schema", "no-website-schema")

# Named by the fold and by the walk, and shipping no block at either stage:
# BreadcrumbList markup mirrors a trail this audit cannot write for the site.
# Asserted as named rather than as cleared, so its absence from the paste list
# is a decision on the record rather than a gap.
NAMED_WITH_NO_BLOCK_TO_PASTE = "no-breadcrumb-markup"

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

# Snippets that are a shape to copy rather than a file to publish, and are
# therefore exempt from the "no placeholder text" assertion below.
#
# Empty, and `sitemap-missing` is the entry that left it. That snippet used to
# write both its URLs in - `{origin}/about` with a `lastmod` seven months
# before the audit - and it was wrong on three of eleven sites where `/about`
# returns 404, one of them a single-page site where it cannot exist. It now
# lists addresses the crawl fetched and got a 200 from, with a `lastmod` only
# where a date was read off the page, so it is a file a user can publish and it
# joins the paste-and-re-audit pass like every other fix.
TEMPLATE_SNIPPETS = set()


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


def _paste_every_snippet(report, site, causes):
    """Paste each named finding's snippet into the head of the pages it names.

    Returns the root causes that were actually pasted, so the caller can assert
    on what happened rather than on what was meant to happen.
    """
    pasted = []
    for finding in report.report["findings"]:
        if finding["root_cause"] not in causes:
            continue
        snippet = (finding.get("suggested_action") or {}).get("snippet")
        assert snippet, (
            "{} is named as a next step but hands the user no block to paste"
            .format(finding["root_cause"]))
        targets = [_local_path(site, report.base_url, url)
                   for url in finding["affected_pages"]]
        targets = [t for t in targets if t]
        assert targets, "{} names no page a user could edit".format(finding["root_cause"])
        for path in targets:
            _paste_into_head(path, snippet)
        pasted.append(finding["root_cause"])
    return pasted


def _every_json_ld_snippet_parses(report):
    """Every paste-ready JSON-LD block in this report is valid JSON.

    A snippet that does not parse is worse than no snippet. Returns how many
    were checked, so the caller can refuse a run that quietly emitted none.
    """
    import json

    checked = 0
    for finding in report.report["findings"]:
        snippet = (finding.get("suggested_action") or {}).get("snippet") or ""
        if "application/ld+json" not in snippet:
            continue
        body = re.sub(r"</?script[^>]*>", "", snippet).strip()
        json.loads(body)  # raises if we emit malformed JSON
        checked += 1
    return checked


def test_following_the_report_in_order_clears_every_type(tmp_path_factory):
    """The fold's own walk, followed exactly, resolves everything it reported.

    A site publishing no structured data at all used to get
    six findings from this one skill - six chores for one condition - and each
    of the six was pasted and re-audited by its own entry in `CASES`. The skill
    now says it once, so those entries have nothing to paste. The guarantee they
    carried is not weaker for being here: it is stated over the whole walk the
    report prints, in the order the report prints it.

    Three stages, three audits:

      1. the folded finding is the only markup-absence finding, and it still
         names every type and every count the six used to;
      2. pasting its block - the identity block, the one the report says goes
         in the template every page shares - clears it, and each type it
         deferred comes back as a finding of its own with a block filled in
         from this site's values;
      3. pasting each of those clears each of them.

    Stage 2 is also where the "re-run this audit" step in the fold's own fix is
    either true or a lie. This is what makes it true.

    The JSON-LD validity check that used to live in its own test runs at every
    stage here, over a larger set of snippets than it saw before.
    """
    work, site = _audit_copy(tmp_path_factory, NO_MARKUP_AT_ALL, "foldwork")

    server, folded = _run(site, work, "folded")
    try:
        absence = [f for f in folded.report["findings"]
                   if f["root_cause"] in set(DEFERRED_BY_THE_FOLD)
                   | {"no-org-schema", NAMED_WITH_NO_BLOCK_TO_PASTE}]
        assert len(absence) == 1, (
            "a site that publishes no structured data has one condition, and this "
            "report states it {} times: {}".format(
                len(absence), [f["title"] for f in absence]))
        one = absence[0]
        assert one["root_cause"] == "no-org-schema"
        text = one["evidence"] + " " + " ".join(
            one["suggested_action"]["how_to_fix"])
        for fragment in FOLD_MUST_STILL_NAME:
            assert fragment in text, (
                "the fold dropped {!r}. Nothing may be lost: every type that would "
                "have been reported has to still be named, with its count."
                .format(fragment))
        assert _every_json_ld_snippet_parses(folded) >= 1
        _paste_every_snippet(folded, site, {"no-org-schema"})
    finally:
        server.__exit__(None, None, None)

    server, opened = _run(site, work, "opened")
    try:
        causes = {f["root_cause"] for f in opened.report["findings"]}
        assert "no-org-schema" not in causes, (
            "the user pasted the identity block the report handed them and the "
            "site is still reported as having no identity markup")
        missing = set(DEFERRED_BY_THE_FOLD) - causes
        assert not missing, (
            "the fold told the reader these come back as findings of their own "
            "once the site carries markup, and they did not: {}".format(sorted(missing)))
        assert NAMED_WITH_NO_BLOCK_TO_PASTE in causes
        assert _every_json_ld_snippet_parses(opened) >= 3, (
            "expected several JSON-LD snippets to check")
        pasted = _paste_every_snippet(opened, site, set(DEFERRED_BY_THE_FOLD))
        assert sorted(pasted) == sorted(DEFERRED_BY_THE_FOLD)
    finally:
        server.__exit__(None, None, None)

    server, done = _run(site, work, "done")
    try:
        left = {f["root_cause"] for f in done.report["findings"]} & set(DEFERRED_BY_THE_FOLD)
        assert not left, (
            "the user followed the report's own order to the end and these are "
            "still reported: {}".format(sorted(left)))
    finally:
        server.__exit__(None, None, None)


def test_the_fold_is_only_for_a_site_with_no_markup_at_all(tmp_path_factory):
    """A site with some markup keeps a finding per missing type.

    The fold is for the site that publishes nothing. On "Article markup is
    stripped from the posts" the Organization, FAQPage, Product and WebSite
    blocks are all still on the site, so the reader must get the Article
    finding and nothing else - exactly as before the fold existed.

    Without this, the cheapest way to make the six-findings complaint go away
    would be to fold whenever several types are missing, which would hide a
    real, specific, single-type defect behind a claim that is false of the site.
    """
    work, site = _audit_copy(tmp_path_factory, "Article markup is stripped from the posts",
                             "partialwork")
    server, report = _run(site, work, "partial")
    try:
        causes = [f["root_cause"] for f in report.report["findings"]]
        assert causes.count("no-article-schema") == 1
        assert "no-structured-data-at-all" not in {
            f.get("stable_id") for f in report.report["findings"]}
        titles = " ".join(f["title"] for f in report.report["findings"])
        assert "publishes no structured data" not in titles
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

# Code whose fix is to *replace* a block the page already carries, so the
# paste-into-the-head machinery above cannot clear the finding: after pasting,
# the wrong block is still on the page and the check is still right to fire.
# Each is proved somewhere else instead, and the file that proves it is named
# so the claim is checkable rather than asserted.
COVERED_ELSEWHERE = {
    # The snippet is the same complete Organization block `no-org-schema`
    # hands out, and that one is pasted and re-audited above.
    "missing-schema-props": "test_fixes_work.py",
    # The block whose `name` is really a page title, or whose `url` points at
    # one section of the site. The snippet is the corrected block: every value
    # in it is one the site published about itself, and the test file named
    # here checks each value's provenance one at a time.
    "schema-text-mismatch": "test_snippet_identity_values.py",
}


def test_every_code_shipping_finding_is_covered_or_declared():
    """A finding that hands out code must either be tested here or explained."""
    here = os.path.dirname(os.path.abspath(__file__))
    for root_cause, filename in COVERED_ELSEWHERE.items():
        assert os.path.isfile(os.path.join(here, filename)), \
            "{} is said to be covered by {}, which does not exist".format(
                root_cause, filename)
    # The walk in `test_following_the_report_in_order_clears_every_type` pastes
    # these and re-audits, exactly as a `CASES` entry does. They are listed
    # here rather than there because on the mutation that used to carry them
    # the skill now emits one folded finding, so there is no separate finding
    # to paste until the fold's own first step has been followed.
    covered = ({rc for _, rc, _ in CASES} | set(COVERED_ELSEWHERE)
               | set(DEFERRED_BY_THE_FOLD))
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
