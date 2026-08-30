"""Crawl paths that no test had ever executed.

Found by measuring rather than reading. Running the whole suite under coverage,
with subprocess tracking on, showed 88% of the marketplace executing and pointed
at what did not - and the gaps were not obscure error handling. They included
the input form the README documents on its first page, and the sitemap layout
every large site on the web uses.

Each test below covers a path that had never run once:

  bare hostname        `run_audit.py example.com` - the documented usage
  sitemap index        a sitemap of sitemaps, which is how big sites publish
  malformed sitemap    XML that does not parse
  robots.txt as HTML   a soft-404, where a CMS serves a page instead of rules
  malformed robots     lines with no separator, rules before any user-agent
  budget exhaustion    more pages than the crawl is allowed to fetch

A note on what coverage does and does not say. It reports which lines executed,
not which behaviours are tested: `thin-html` showed as covered for the whole
time it had no implementation, because the branch that skipped it ran. It is a
list of places to look, never a score to raise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT, SCRIPTS, run_script
from fixture_server import FixtureServer

CRAWL = os.path.join(SCRIPTS, "crawl.py")


def _page(title, body, links=()):
    anchors = "".join(
        '<p><a href="{{{{BASE}}}}/{}">{}</a></p>'.format(href, text) for href, text in links)
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<title>{title} | Kestrel Instruments</title>'
        '<meta name="description" content="Kestrel Instruments calibrates flow meters for '
        'water utilities across the north of England.">'
        '</head><body><main><h1>{title}</h1><p>{body}</p>{anchors}</main></body></html>'
    ).format(title=title, body=body, anchors=anchors)


def _build(site, pages, robots=None, files=None):
    os.makedirs(site, exist_ok=True)
    for name, html in pages.items():
        path = os.path.join(site, name.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(html)
    if robots is not None:
        with open(os.path.join(site, "robots.txt"), "w", encoding="utf-8") as handle:
            handle.write(robots)
    for name, text in (files or {}).items():
        path = os.path.join(site, name.replace("/", os.sep))
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    return site


def _crawl(base_url, out, extra=()):
    run_script([CRAWL, base_url, "--out", out, "--budget", "60", "--delay", "0"] + list(extra),
               "crawl.py")
    with open(out, encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# The input form the README documents
# --------------------------------------------------------------------------

def test_a_bare_hostname_is_accepted(tmp_path):
    """`run_audit.py example.com` is the first example in the README.

    It had never been exercised. The scheme is added silently, so a typo in that
    code path would have produced "could not parse a hostname" on the exact
    command the documentation tells a judge to run.
    """
    site = _build(str(tmp_path / "site"),
                  {"index.html": _page("Kestrel Instruments",
                                       "Kestrel Instruments calibrates flow meters.")},
                  robots="User-agent: *\nAllow: /\n")
    with FixtureServer(site) as server:
        bare = server.base_url.replace("http://", "")
        out = str(tmp_path / "snapshot.json")
        run_script([CRAWL, bare, "--out", out, "--budget", "60", "--delay", "0"],
                   "crawl.py [bare hostname]")
        with open(out, encoding="utf-8") as handle:
            snapshot = json.load(handle)
    # `example.com` is assumed to be https, which is the right default. A site
    # that answers only over http used to return nothing at all; it now falls
    # back and says so, because "your site is not on https" is a finding we
    # already have and an empty report is not.
    assert snapshot["origin"].startswith("http://"), (
        "https did not answer here, so the crawl should have fallen back: {}".format(
            snapshot["origin"]))
    assert snapshot["crawl"]["pages_ok"] >= 1, (
        "the fallback produced no pages; notes: {}".format(snapshot["crawl"].get("notes")))
    assert any("http" in note for note in snapshot["crawl"].get("notes") or []), (
        "falling back to http has to be stated, not done quietly")


def test_a_target_is_required():
    """No URL at all should say so, not raise something unreadable."""
    done = subprocess.run([sys.executable, CRAWL], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode != 0
    assert "required" in (done.stdout + done.stderr).lower()


def test_an_unparseable_target_is_rejected_clearly():
    done = subprocess.run([sys.executable, CRAWL, "http://", "--out", os.devnull],
                          cwd=ROOT, capture_output=True, text=True)
    assert done.returncode != 0
    assert "hostname" in (done.stdout + done.stderr).lower()


# --------------------------------------------------------------------------
# Sitemaps
# --------------------------------------------------------------------------

def test_a_sitemap_index_is_followed_to_its_children(tmp_path):
    """A sitemap of sitemaps, which is how every large site publishes.

    Untested until now, which meant the layout used by the biggest sites we
    audit was the one layout nothing had confirmed we could read.
    """
    site = str(tmp_path / "site")
    pages = {"index.html": _page("Kestrel Instruments", "We calibrate flow meters.",
                                 [("about.html", "About")]),
             "about.html": _page("About", "Kestrel Instruments was founded in Leeds."),
             "services.html": _page("Services", "Calibration, repair and certification.")}
    index = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
             '<sitemap><loc>{{BASE}}/sitemap-pages.xml</loc></sitemap>'
             '<sitemap><loc>{{BASE}}/sitemap-services.xml</loc></sitemap>'
             '</sitemapindex>')
    child_a = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>{{BASE}}/index.html</loc><lastmod>2026-07-01</lastmod></url>'
               '<url><loc>{{BASE}}/about.html</loc><lastmod>2026-07-02</lastmod></url>'
               '</urlset>')
    child_b = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>{{BASE}}/services.html</loc><lastmod>2026-07-03</lastmod></url>'
               '</urlset>')
    _build(site, pages,
           robots="User-agent: *\nAllow: /\n\nSitemap: {{BASE}}/sitemap.xml\n",
           files={"sitemap.xml": index, "sitemap-pages.xml": child_a,
                  "sitemap-services.xml": child_b})

    with FixtureServer(site) as server:
        snapshot = _crawl(server.base_url, str(tmp_path / "snapshot.json"))

    sitemaps = snapshot.get("sitemaps") or []
    assert any(s.get("is_index") for s in sitemaps), (
        "the index was not recognised as an index: {}".format(
            [(s.get("url"), s.get("is_index")) for s in sitemaps]))
    listed = [entry.get("loc", "") if isinstance(entry, dict) else str(entry)
              for s in sitemaps for entry in (s.get("urls") or [])]
    assert any("services.html" in u for u in listed), (
        "a URL that exists only in a child sitemap was never reached; saw {}".format(
            sorted(set(listed))[:6]))


def test_a_malformed_sitemap_is_recorded_not_fatal(tmp_path):
    site = _build(str(tmp_path / "site"),
                  {"index.html": _page("Kestrel Instruments", "We calibrate flow meters.")},
                  robots="User-agent: *\nAllow: /\n\nSitemap: {{BASE}}/sitemap.xml\n",
                  files={"sitemap.xml": "<urlset><url><loc>broken"})
    with FixtureServer(site) as server:
        snapshot = _crawl(server.base_url, str(tmp_path / "snapshot.json"))
    sitemaps = snapshot.get("sitemaps") or []
    assert sitemaps and any(s.get("parse_error") for s in sitemaps), (
        "a sitemap that does not parse should be recorded as such")
    assert snapshot["crawl"]["pages_ok"] >= 1, "the crawl should continue regardless"


# --------------------------------------------------------------------------
# robots.txt that is not robots.txt
# --------------------------------------------------------------------------

def test_robots_served_as_html_is_treated_as_absent(tmp_path):
    """A CMS answering every path with a page, including /robots.txt.

    Reading that as rules would invent blocks the site never wrote. The crawl
    has to notice and carry on as though no robots.txt exists.
    """
    site = _build(str(tmp_path / "site"),
                  {"index.html": _page("Kestrel Instruments", "We calibrate flow meters.")},
                  robots="<!DOCTYPE html><html><body><h1>Page not found</h1></body></html>")
    with FixtureServer(site) as server:
        snapshot = _crawl(server.base_url, str(tmp_path / "snapshot.json"))
    robots = snapshot.get("robots") or {}
    assert any("HTML" in e for e in (robots.get("errors") or [])), (
        "expected the HTML soft-404 to be recorded: {}".format(robots.get("errors")))
    assert snapshot["crawl"]["pages_ok"] >= 1, "the crawl should not have been halted"


def test_malformed_robots_lines_are_reported_without_stopping_the_crawl(tmp_path):
    """Rules before any user-agent, and a line with no colon."""
    site = _build(str(tmp_path / "site"),
                  {"index.html": _page("Kestrel Instruments", "We calibrate flow meters.")},
                  robots="Disallow: /early\nthis line has no separator\n"
                         "User-agent: *\nAllow: /\n")
    with FixtureServer(site) as server:
        snapshot = _crawl(server.base_url, str(tmp_path / "snapshot.json"))
    errors = (snapshot.get("robots") or {}).get("errors") or []
    assert len(errors) >= 2, "both malformed lines should be reported: {}".format(errors)
    assert snapshot["crawl"]["pages_ok"] >= 1


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------

def test_the_page_budget_stops_the_crawl_and_says_so(tmp_path):
    """More pages than we are allowed to fetch. The report must admit the limit."""
    pages = {"index.html": _page(
        "Kestrel Instruments", "We calibrate flow meters.",
        [("page-{}.html".format(n), "Page {}".format(n)) for n in range(12)])}
    for n in range(12):
        pages["page-{}.html".format(n)] = _page(
            "Page {}".format(n), "Calibration note number {}.".format(n))
    site = _build(str(tmp_path / "site"), pages, robots="User-agent: *\nAllow: /\n")
    with FixtureServer(site) as server:
        snapshot = _crawl(server.base_url, str(tmp_path / "snapshot.json"),
                          extra=["--max-pages", "4"])
    assert snapshot["crawl"]["pages_crawled"] <= 4, "the page budget was not honoured"
    assert snapshot["crawl"]["budget_exhausted"] is True, (
        "the crawl stopped at the budget but did not record that it had")


def test_the_brand_falls_back_to_the_domain_when_nothing_declares_a_name(tmp_path):
    """No JSON-LD, no og:site_name, no usable title. The host is the last resort."""
    bare = ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"></head>'
            '<body><main><p>Calibration services for water utilities.</p></main></body></html>')
    site = _build(str(tmp_path / "site"), {"index.html": bare},
                  robots="User-agent: *\nAllow: /\n")
    with FixtureServer(site) as server:
        snapshot = _crawl(server.base_url, str(tmp_path / "snapshot.json"))
    brand = snapshot["brand"]
    assert brand["name"], "a brand name should always be produced"
    assert brand["source"] in ("domain", "title"), brand
