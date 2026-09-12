# -*- coding: utf-8 -*-
"""The editions finding names what is missing, and does not deny the links.

A single-page site in 29 languages was told "This site publishes 29 language
editions and none links to the others". Every edition links all 28 others
through its language picker; only the `hreflang` tags are missing.

Every host here is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(name, alias):
    path = os.path.join(ROOT, "skills", name, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module("crawl-access-audit", "ca_for_edition_title_tests")

SITE = "https://undo-guide.test"
CODES = ("de", "es", "fr", "ja")


def _edition(code, linked=True):
    url = SITE + "/" + code
    others = [{"url": SITE + "/" + c, "text": c} for c in CODES if c != code] if linked else []
    return {"url": url, "final_url": url, "status": 200, "lang": code, "depth": 1,
            "text_len": 4000, "hreflang": [], "links": {"internal": others, "external": []}}


class _Html:
    status_code = 200
    history = ()
    encoding = "utf-8"

    def __init__(self, url):
        self.url = url
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.content = b"<html><head><title>Undo</title></head><body>Undo</body></html>"

    @property
    def text(self):
        return self.content.decode("utf-8")


class _Reader:
    budget_left = True
    time_left = True

    def try_get(self, url, **kwargs):
        return _Html(url)


def _finding(pages):
    result = SkillResult("crawl-access-audit")
    CA._check_hreflang(result, {"origin": SITE, "pages": pages, "sitemaps": []}, pages,
                       _Reader(), robots={})
    return next(f for f in result.findings
                if f["id_hint"] == "no-hreflang-between-language-editions")


def test_editions_linked_by_a_picker_are_not_said_to_be_unlinked():
    finding = _finding([_edition(code) for code in CODES])
    assert "none links to the others" not in finding["title"]
    assert "hreflang" in finding["title"]
    assert "only through ordinary links" in finding["title"]
    assert "4 of the 4 crawled edition pages link to at least one other edition" in (
        finding["evidence"])


def test_editions_with_no_link_between_them_say_hreflang_is_missing():
    finding = _finding([_edition(code, linked=False) for code in CODES])
    assert finding["title"] == ("This site publishes 4 language editions and none declares "
                                "the others with `hreflang`")
    assert "ordinary link" not in finding["evidence"]
