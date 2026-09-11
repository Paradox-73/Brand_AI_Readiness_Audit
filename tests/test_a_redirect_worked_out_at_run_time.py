# -*- coding: utf-8 -*-
"""A front page that is a script choosing where to send the reader.

A one-page site's root address delivers eight characters of text and a script
that picks a language edition and runs `window.location = destination`. There
is no quoted address in it, so the reading that records a script redirect only
when it can name the destination recorded nothing - and the page was then told
to "write two or three sentences of body copy", and the homepage check told it
to "ship the top-level menu as real <a href> elements". Neither fix touches the
cause. The page is a redirect that only something running JavaScript follows.

So the extractor records a redirect whose destination is worked out at run
time as well as one it can name, and the two checks that were wrong about it
read that record.

Every host below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402
from page_extract import extract_page  # noqa: E402

SITE = "https://no-greeting.test"

COMPUTED = """<!DOCTYPE html><html><head><title>Hello</title>
<link rel="alternate" hreflang="en" href="/en/">
<link rel="alternate" hreflang="de" href="/de/">
<script>
  var lang = (navigator.language || "en").slice(0, 2);
  var destination = "/" + lang + "/";
  window.location = destination;
</script></head><body><p>Loading</p></body></html>"""

LITERAL = """<!DOCTYPE html><html><head><title>Hello</title>
<script>window.location.replace("/en/");</script></head><body></body></html>"""

RELOAD = """<!DOCTYPE html><html><head><title>Page</title>
<script>function again() { location.href = location.href; }</script></head>
<body><h1>A page</h1><p>Real text that stays where it is.</p></body></html>"""

PLAIN = """<!DOCTYPE html><html><head><title>Page</title>
<script>var colour = "blue"; document.title = colour;</script></head>
<body><h1>A page</h1><p>Nothing here sends the browser anywhere.</p></body></html>"""


def _extract(html, path="/"):
    return extract_page(url=SITE + path, final_url=SITE + path, status=200,
                        headers={"content-type": "text/html"}, html=html,
                        redirect_chain=[], elapsed_ms=5, depth=0,
                        source="homepage", origin=SITE)


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# What the extractor records
# --------------------------------------------------------------------------

def test_a_destination_worked_out_at_run_time_is_still_a_redirect():
    record = _extract(COMPUTED)["script_redirect"]
    assert record, "the page is a redirect and nothing recorded it"
    assert record.get("computed") is True
    # Nothing to follow: the crawl queues only an address it can name, and
    # inventing one would be inventing a page.
    assert record.get("url") == ""


def test_a_quoted_same_site_destination_is_recorded_as_an_address():
    record = _extract(LITERAL)["script_redirect"]
    assert record == {"url": SITE + "/en/"}


def test_a_script_reloading_its_own_address_is_not_a_redirect():
    assert _extract(RELOAD)["script_redirect"] is None


def test_a_script_that_never_touches_location_records_nothing():
    assert _extract(PLAIN)["script_redirect"] is None


# --------------------------------------------------------------------------
# What the homepage check prescribes
# --------------------------------------------------------------------------

ENGAGEMENT = _module("engagement-audit", "engagement_for_run_time_redirects")


def _home(**extra):
    page = {"url": SITE + "/", "final_url": SITE + "/", "status": 200,
            "page_type": "home", "source": "homepage", "text_len": 8,
            "links": {"internal": [], "external": [], "nav": [], "footer": []},
            "chrome_signature": {"nav_paths": [], "nav_path_count": 0}}
    page.update(extra)
    return page


def _front_door(home):
    others = [{"url": SITE + path, "final_url": SITE + path, "status": 200,
               "page_type": "other", "source": "hreflang", "text_len": 1800,
               "links": {"internal": []}} for path in ("/en/", "/de/")]
    snapshot = {"origin": SITE, "pages": [home] + others, "sitemaps": [],
                "crawl": {"render_mode": "static"}}
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_front_door(result, snapshot, home)
    return [f for f in result.findings if f["root_cause"] == "unreachable-from-homepage"]


def test_a_front_page_that_redirects_by_script_is_told_to_redirect_on_the_server():
    findings = _front_door(_home(script_redirect={"url": "", "computed": True}))
    assert len(findings) == 1
    finding = findings[0]
    assert "script that sends the browser" in finding["evidence"]
    assert "an address it works out as it runs" in finding["evidence"]
    first_step = finding["suggested_action"]["how_to_fix"][0]
    assert "server redirect" in first_step


def test_a_front_page_with_no_links_and_no_redirect_keeps_the_menu_advice():
    """The guard: the redirect wording is for the redirect case only."""
    findings = _front_door(_home())
    assert len(findings) == 1
    steps = findings[0]["suggested_action"]["how_to_fix"]
    assert "server redirect" not in steps[0]
    assert "<a href>" in steps[0]
