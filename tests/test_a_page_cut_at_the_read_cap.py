# -*- coding: utf-8 -*-
"""A page the crawl stopped reading at the size cap is marked, and not judged.

On a Vietnamese shop a guide page ran past the 5,000,000-byte read cap. The
crawl's own note said so, and checks still reported what that page lacked -
about a document read only as far as the cap. The crawl now writes
`truncated_at_read_cap: True` on the page itself, and checks skip it.

Every host here is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402
from crawl import fetch_page  # noqa: E402


def _module(name, alias):
    path = os.path.join(ROOT, "skills", name, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module("crawl-access-audit", "ca_for_read_cap_tests")

SITE = "https://guide-shop.test"


class _Response:
    history = ()
    content = (b"<html lang=\"vi\"><head><title>Buying guide</title>"
               b"<link rel=\"canonical\" href=\"https://elsewhere.test/guide\"></head>"
               b"<body><h1>Buying guide</h1><p>How to order from the shop.</p></body></html>")
    status_code = 200
    elapsed_ms = 1
    encoding = "utf-8"

    def __init__(self, url, truncated):
        self.url = url
        self.truncated = truncated
        self.headers = {"content-type": "text/html; charset=utf-8"}

    @property
    def text(self):
        return self.content.decode("utf-8")


class _Fetcher:
    def __init__(self, truncated):
        self.truncated = truncated

    def get(self, url, **kwargs):
        return _Response(url, self.truncated)


def test_the_crawl_marks_a_page_cut_at_the_cap():
    record = fetch_page(_Fetcher(True), SITE + "/guide", 1, "bfs", SITE)
    assert record["truncated_at_read_cap"] is True
    assert record["truncated"] is True


def test_a_page_read_whole_carries_no_such_mark():
    record = fetch_page(_Fetcher(False), SITE + "/guide", 1, "bfs", SITE)
    assert "truncated_at_read_cap" not in record


def test_the_canonical_check_does_not_judge_a_cut_page():
    record = fetch_page(_Fetcher(True), SITE + "/guide", 1, "bfs", SITE)
    result = SkillResult("crawl-access-audit")
    CA._check_canonicals(result, {"origin": SITE, "pages": [record]}, [record])
    assert not [f for f in result.findings if f["id_hint"] == "canonical-points-off-domain"]

    whole = fetch_page(_Fetcher(False), SITE + "/guide", 1, "bfs", SITE)
    result = SkillResult("crawl-access-audit")
    CA._check_canonicals(result, {"origin": SITE, "pages": [whole]}, [whole])
    assert [f for f in result.findings if f["id_hint"] == "canonical-points-off-domain"]
