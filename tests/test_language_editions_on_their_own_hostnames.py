# -*- coding: utf-8 -*-
"""Language editions on hostnames of their own are editions too.

A Chinese documentation site links every page to its twins on the English,
Japanese, French and Korean hostnames - `ja.<domain>`, `fr.<domain>` and so on,
one language picker on every page - with no `hreflang` and no canonical. The
edition check read editions only as path prefixes and declined with "no
crawled address begins with a path segment".

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


CA = _module("crawl-access-audit", "ca_for_hostname_edition_tests")

SITE = "https://zh.docs-site.test"
PICKER = ["https://docs-site.test/", "https://ja.docs-site.test/",
          "https://fr.docs-site.test/", "https://ko.docs-site.test/"]
# Sibling hosts that are not editions: a blog, an old version, a playground.
OTHERS = ["https://blog.docs-site.test/", "https://v2.docs-site.test/",
          "https://play.docs-site.test/"]


def _page(path, depth=1, hreflang=()):
    url = SITE + path
    return {"url": url, "final_url": url, "status": 200, "lang": "zh-CN", "depth": depth,
            "text_len": 4000, "hreflang": list(hreflang),
            "links": {"internal": [{"url": SITE + "/guide/"}],
                      "external": [{"url": u, "text": ""} for u in PICKER + OTHERS]}}


def _pages():
    return [_page("/", 0)] + [_page("/guide/{}".format(n)) for n in range(5)]


class _Html:
    status_code = 200
    history = ()
    encoding = "utf-8"

    def __init__(self, url, body):
        self.url = url
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.content = body

    @property
    def text(self):
        return self.content.decode("utf-8")


class _Reader:
    budget_left = True
    time_left = True

    def __init__(self, body=b"<html><head><title>Docs</title></head><body>Docs</body></html>"):
        self.body = body
        self.asked = []

    def try_get(self, url, **kwargs):
        self.asked.append(url)
        return _Html(url, self.body)


def _run(pages, fetcher=None):
    result = SkillResult("crawl-access-audit")
    CA._check_hreflang(result, {"origin": SITE, "pages": pages, "sitemaps": []}, pages,
                       fetcher, robots={})
    return result


def test_editions_on_language_hostnames_with_no_hreflang_are_reported():
    reader = _Reader()
    result = _run(_pages(), reader)
    finding = next(f for f in result.findings
                   if f["id_hint"] == "no-hreflang-between-language-editions")
    assert "3 language editions on other hostnames" in finding["title"]
    assert "hreflang" in finding["title"]
    for host in ("ja.docs-site.test", "fr.docs-site.test", "ko.docs-site.test"):
        assert host in finding["evidence"]
    for host in ("blog.docs-site.test", "v2.docs-site.test", "play.docs-site.test"):
        assert host not in finding["evidence"]
    # One request: the front page, for the anchors and header the record lacks.
    assert reader.asked == [SITE + "/"]


def test_without_a_request_the_decline_names_the_hostname_editions():
    reason = next(e["reason"] for e in _run(_pages()).not_applicable
                  if e["check"] == "hreflang-between-language-editions")
    assert "ja.docs-site.test" in reason
    assert "begins with a path segment" not in reason


def test_editions_the_pages_do_declare_are_left_alone():
    declared = [{"hreflang": "ja", "url": "https://ja.docs-site.test/"}]
    pages = [_page("/", 0, declared)] + [_page("/guide/{}".format(n), 1, declared)
                                         for n in range(5)]
    result = _run(pages, _Reader())
    assert result.findings == []


def test_one_language_subdomain_linked_once_is_not_an_edition_set():
    """A regional office at `fr.` linked from a contact page is not a picker."""
    pages = _pages()
    for page in pages:
        page["links"]["external"] = [{"url": u} for u in OTHERS]
    pages[0]["links"]["external"].append({"url": "https://fr.docs-site.test/"})
    reason = next(e["reason"] for e in _run(pages, _Reader()).not_applicable
                  if e["check"] == "hreflang-between-language-editions")
    assert "no language edition on this site" in reason
