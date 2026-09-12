# -*- coding: utf-8 -*-
"""A 200 that is one script and nothing else is a challenge, not an empty page.

A museum answered every request with HTTP 200 and 101,079 bytes of obfuscated
script - one inline `<script>`, no title, no visible text, no link - under a
`Server` header naming a product no table knows. The report said the homepage
"answered 200 with an empty body" and that "no page answered with a
bot-manager verification page", and so missed that nothing which does not run
JavaScript ever receives the museum's pages. The shape is the signal, not the
vendor.

Every host and product name here is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult, detect_challenge, is_script_wall  # noqa: E402


def _module(name, alias):
    path = os.path.join(ROOT, "skills", name, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module("crawl-access-audit", "ca_for_script_wall_tests")

SITE = "https://museum.test"
OBFUSCATED = "var k60=function(){" + "q7[k60.L70(+ )](Y7);" * 4000 + "};k60();"
WALL = "<html><head><script>" + OBFUSCATED + "</script></head><body></body></html>"


def test_a_page_that_is_one_script_is_a_challenge_by_its_shape():
    assert is_script_wall(WALL, "", 200)
    assert detect_challenge(WALL, "", 200, {"server": "edge-guard-7"}) == CA.UNNAMED_EDGE


def test_an_application_shell_is_not_a_challenge():
    """A shell names itself with a title and a mount point, and it is the
    JavaScript-shell finding's, not this one."""
    shell = ("<html><head><title>An Invented Planner</title><script>" + OBFUSCATED
             + "</script></head><body><div id=\"app\"></div></body></html>")
    assert not is_script_wall(shell, "", 200)
    assert detect_challenge(shell, "", 200, {}) is None


def test_a_page_with_words_or_links_is_not_a_challenge():
    linked = WALL.replace("<body></body>", "<body><a href=\"/visit\">Visit</a></body>")
    assert not is_script_wall(linked, "Visit", 200)
    assert not is_script_wall(WALL, "An exhibition of invented sculpture opens in May.", 200)
    assert not is_script_wall(WALL, "", 404)


def _record(url, size=101079, inline=101000):
    return {"url": url, "final_url": url, "status": 200, "title": "", "text_len": 0,
            "html_len": size, "html_bytes": size, "challenge": None,
            "headers": {"server": "edge-guard-7"},
            "links": {"internal": [], "external": [], "internal_count": 0,
                      "external_count": 0},
            "scripts": {"count": 1, "inline_bytes": inline},
            "spa_shell": {"root_selector": None, "state_blobs": []},
            "not_gradable": True, "skipped": "answered 200 with an empty body"}


def test_the_museum_record_is_reported_as_the_access_finding_it_is():
    """Read off a record written before the crawl knew the shape: it says
    `challenge: null`, and the sitemap address answered the same bytes."""
    snapshot = {"origin": SITE, "pages": [_record(SITE + "/")],
                "sitemaps": [{"url": SITE + "/sitemap.xml", "status": 200,
                              "body_bytes": 101079}]}
    result = SkillResult("crawl-access-audit")
    CA._check_challenge_pages(result, snapshot)
    finding = next(f for f in result.findings
                   if f["id_hint"] == "bot-manager-serves-a-challenge-page")
    assert "script challenge" in finding["title"]
    assert "does not run JavaScript" in finding["title"]
    assert finding["severity"] == "critical" and finding["confidence"] == "high"
    assert "101,000 of them inline script" in finding["evidence"]
    assert SITE + "/sitemap.xml answered with the same 101,079 bytes" in finding["evidence"]
    assert "`edge-guard-7`" in finding["evidence"]
    # The wording a verification screen shows is not what identified this one.
    assert "wording they show a visitor" not in finding["evidence"]
    assert not [e for e in result.not_applicable if e["check"] == "bot-manager-challenge-page"]


def test_a_single_script_page_with_nothing_repeating_it_is_held_at_medium():
    snapshot = {"origin": SITE, "pages": [_record(SITE + "/")], "sitemaps": []}
    result = SkillResult("crawl-access-audit")
    CA._check_challenge_pages(result, snapshot)
    finding = next(f for f in result.findings
                   if f["id_hint"] == "bot-manager-serves-a-challenge-page")
    assert finding["confidence"] == "medium"
