# -*- coding: utf-8 -*-
"""A canonical written as plain http:// on an https page is named as such.

A national library's homepage is served at https://www.<host>/ and declares
its canonical as http://<host>/. The report said only that canonicals are
split across two hostnames, and left out that the address the homepage names
as the real one is an unencrypted address.

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


CA = _module("crawl-access-audit", "ca_for_plain_http_canonical_tests")

SITE = "https://www.library.test"


def _page(path, canonical):
    url = SITE + path
    return {"url": url, "final_url": url, "status": 200, "canonical": canonical,
            "depth": 0 if path == "/" else 1, "links": {"internal": [], "external": []}}


def _run(pages):
    result = SkillResult("crawl-access-audit")
    CA._check_transport_and_hosts(result, {"origin": SITE, "pages": pages}, pages)
    return next(f for f in result.findings if f["id_hint"] == "mixed-canonical-hosts")


def test_the_homepage_canonical_to_plain_http_is_its_own_fact():
    pages = [_page("/", "http://library.test/")] + [
        _page("/collections/{}".format(n), SITE + "/collections/{}".format(n))
        for n in range(3)]
    finding = _run(pages)
    assert "plain-http://" in finding["title"]
    assert "the homepage, {}/ -> http://library.test/".format(SITE) in finding["evidence"]
    steps = finding["suggested_action"]["how_to_fix"]
    assert any(step.startswith("Write every canonical with https://") for step in steps)


def test_a_split_with_every_canonical_on_https_says_nothing_about_http():
    pages = [_page("/", "https://library.test/"), _page("/visit", SITE + "/visit")]
    finding = _run(pages)
    assert finding["title"] == "Canonical URLs are split across more than one hostname"
    assert "plain-HTTP" not in finding["evidence"]
