# -*- coding: utf-8 -*-
"""Two readings the audit got wrong, and the four findings they invented.

Both are reading defects, not judgement defects: the checks downstream did
exactly what they are written to do with the values handed to them.

  a redirect to `https://www.example.test:443/`   three findings on one site.
                                                  36 pages accused of declaring
                                                  a canonical "on a different
                                                  domain", with an example
                                                  pointing at itself; canonicals
                                                  reported as "split across more
                                                  than one hostname", the two
                                                  hostnames being one hostname
                                                  spelled with and without the
                                                  port the scheme implies; and a
                                                  page named as carrying no
                                                  JSON-LD block in the same
                                                  report that named it as
                                                  carrying one that fails to
                                                  parse

  a scope menu in front of a search box          the paste-ready SearchAction
                                                  shipped `?s=` when the box a
                                                  visitor types into is named
                                                  `q`, so the search URL the
                                                  report told a site to publish
                                                  returns nothing
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    make_soup, normalise_url, origin_of, same_site, site_label, SkillResult,
    strip_default_port, strip_www,
)
from crawl import dedup_key  # noqa: E402
from page_extract import _forms  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
             "ca_for_url_and_form_reading")
SD = _module(os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py"),
             "sd_for_url_and_form_reading")

SITE = "https://www.a-publisher-that-does-not-exist.test"
PORTED = "https://www.a-publisher-that-does-not-exist.test:443"


# --------------------------------------------------------------------------
# `:443` on https is the port the scheme already implies
# --------------------------------------------------------------------------

def test_the_default_port_comes_off_a_normalised_url():
    """`https://host:443/x` and `https://host/x` are one address.

    A server may spell the port out in a `Location` header, and one does. Every
    URL recorded after that redirect carried it, including the ones the report
    printed back to the reader.
    """
    assert normalise_url(PORTED + "/175x") == SITE + "/175x"
    assert normalise_url("http://a-site.test:80/a") == "http://a-site.test/a"


def test_a_port_the_scheme_does_not_imply_is_kept():
    """Only the default comes off. `:8443` is where the site actually answers,
    and dropping it would point every URL in the report at a closed port."""
    assert normalise_url("https://a-site.test:8443/a") == "https://a-site.test:8443/a"
    assert normalise_url("http://a-site.test:8080/a") == "http://a-site.test:8080/a"


def test_the_default_port_does_not_swallow_a_host_or_an_address_literal():
    """A netloc with no port, an IPv6 literal and userinfo all survive."""
    assert strip_default_port("a-site.test") == "a-site.test"
    assert strip_default_port("[::1]") == "[::1]"
    assert strip_default_port("[::1]:443", "https") == "[::1]"
    assert strip_default_port("[::1]:8080", "https") == "[::1]:8080"
    assert strip_default_port("reader:secret@a-site.test") == "reader:secret@a-site.test"


def test_the_default_port_survives_where_it_is_not_the_default():
    """`:443` spelled on an http URL is not the default for that scheme, and a
    caller that knows the scheme says so."""
    assert strip_default_port("a-site.test:443", "http") == "a-site.test:443"
    assert strip_default_port("a-site.test:80", "https") == "a-site.test:80"


def test_two_spellings_of_one_host_compare_equal():
    assert strip_www("www.a-site.test:443") == "a-site.test"
    assert same_site(SITE + "/a", PORTED + "/b")
    assert not same_site(SITE + "/a", "https://another-site.test/b")


def test_the_origin_and_the_site_label_lose_the_default_port():
    """`site_label` is printed as the `site` field of every report."""
    assert origin_of(PORTED + "/175x") == SITE
    assert site_label(PORTED + "/175x") == "www.a-publisher-that-does-not-exist.test"


def test_one_page_reached_by_two_spellings_is_crawled_once():
    """Without this the crawler fetches the same page twice and every finding
    on it is counted twice."""
    assert dedup_key(PORTED + "/175x") == dedup_key(SITE + "/175x")


# --------------------------------------------------------------------------
# A relative canonical names no domain, so it cannot name a different one
# --------------------------------------------------------------------------

def _page_with_canonical(path, canonical, url_prefix=SITE):
    return {"url": url_prefix + path, "status": 200, "canonical": canonical}


def test_a_self_canonical_on_a_ported_url_is_not_off_domain():
    """The finding read "36 pages declare a canonical URL on a different
    domain", and its own example was `.../175x -> .../175x`. The site's
    canonicals are relative - `<link rel="canonical" href="/188-2" />` - so no
    domain is declared on any page of it.

    The origin here is the bare domain because that is what someone typed, and
    the site redirects it to the `www` host with the port spelled out. The
    audit compared "the origin's host" against "the canonical's host" and read
    those as two domains.
    """
    bare = "https://a-publisher-that-does-not-exist.test"
    snapshot = {"origin": bare, "pages": []}
    pages = [_page_with_canonical("/175x", PORTED + "/175x", PORTED),
             _page_with_canonical("/188-2", PORTED + "/188-2", PORTED)]
    result = SkillResult("crawl-access-audit")
    CA._check_canonicals(result, snapshot, pages)

    assert not [f for f in result.findings
                if f["root_cause"] == "canonical-broken"], (
        "a canonical resolving to the page it sits on is not off-domain, "
        "whichever way the port is spelled")


def test_a_canonical_genuinely_off_domain_is_still_reported():
    """The check must keep firing: a template hard-coding a previous site's
    domain is the defect it exists for."""
    snapshot = {"origin": SITE, "pages": []}
    pages = [_page_with_canonical("/a", "https://a-previous-site.test/a")]
    result = SkillResult("crawl-access-audit")
    CA._check_canonicals(result, snapshot, pages)

    assert [f["id_hint"] for f in result.findings] == ["canonical-points-off-domain"]


def test_two_spellings_of_one_hostname_are_not_a_host_split():
    """The evidence read: "Canonical hostnames seen: www.example.test (23
    pages), www.example.test:443 (36 pages)". That is one hostname."""
    snapshot = {"origin": SITE, "pages": []}
    pages = [_page_with_canonical("/a", SITE + "/a"),
             _page_with_canonical("/b", PORTED + "/b")]
    result = SkillResult("crawl-access-audit")
    CA._check_transport_and_hosts(result, snapshot, pages)

    assert not [f for f in result.findings if f["id_hint"] == "mixed-canonical-hosts"]


def test_www_and_the_bare_domain_are_still_a_host_split():
    """`www.` is a real split with real work behind it, and folding the port
    away may not fold that away too."""
    snapshot = {"origin": SITE, "pages": []}
    bare = "https://a-publisher-that-does-not-exist.test"
    pages = [_page_with_canonical("/a", SITE + "/a"),
             _page_with_canonical("/b", bare + "/b")]
    result = SkillResult("crawl-access-audit")
    CA._check_transport_and_hosts(result, snapshot, pages)

    assert [f["id_hint"] for f in result.findings
            if f["id_hint"] == "mixed-canonical-hosts"] == ["mixed-canonical-hosts"]


def test_the_structured_data_origin_comparison_ignores_the_default_port():
    assert SD._bare_host(PORTED + "/a") == SD._bare_host(SITE + "/a")


# --------------------------------------------------------------------------
# A JSON-LD block that broke is not a JSON-LD block that was never written
# --------------------------------------------------------------------------

def _microdata_page(path, errors=()):
    """A page carrying microdata and no JSON-LD the parser could read.

    `jsonld` holds the blocks that parsed, so it is empty in both cases: the
    page that shipped no block at all, and the page whose block has a syntax
    error in it. `jsonld_errors` is the only thing that tells them apart.
    """
    return {"url": SITE + path, "status": 200, "page_type": "other",
            "jsonld": [], "jsonld_errors": list(errors), "microdata_count": 6,
            "microdata_types": ["book", "offer"]}


BROKEN = [{"error": "Expecting ',' delimiter: line 8 column 3",
           "excerpt": '{"@type": "Book" "name": "A Title"}'}]


def test_a_page_whose_jsonld_broke_is_not_listed_as_having_none():
    """One report named the same URL in both findings: as a page with
    "microdata attributes but no JSON-LD block", and as a page whose JSON-LD
    fails to parse. The page ships a `<script type="application/ld+json">`
    block, so only the second is true.

    The advice differs, which is why the two may not be merged. A site that
    shipped a broken block needs the syntax error found; a site that shipped
    none needs the markup written.
    """
    pages = [_microdata_page("/bd/isbn/978-4-000000-00-0", BROKEN)]
    result = SkillResult("structured-data-audit")
    SD._check_microdata_only(result, pages)

    assert not result.findings, (
        "a page carrying a JSON-LD block that does not parse has not chosen "
        "microdata in place of JSON-LD")
    reason = " ".join(entry["reason"] for entry in result.not_applicable)
    assert "does not parse" in reason, (
        "the skip has to say what was found instead, or the page silently "
        "disappears from both checks")


def test_a_page_with_no_jsonld_block_at_all_is_still_reported():
    pages = [_microdata_page("/bd/isbn/978-4-000000-00-1")]
    result = SkillResult("structured-data-audit")
    SD._check_microdata_only(result, pages)

    assert [f["root_cause"] for f in result.findings] == ["microdata-only"]
    assert "no JSON-LD block at all" in result.findings[0]["evidence"]


def test_no_url_is_named_by_both_findings():
    """The two findings contradict each other about any URL they share, so no
    URL may appear in both."""
    broke = _microdata_page("/bd/isbn/978-4-000000-00-0", BROKEN)
    absent = _microdata_page("/bd/isbn/978-4-000000-00-1")
    pages = [broke, absent]

    validity = SkillResult("structured-data-audit")
    SD._check_jsonld_validity(validity, pages)
    microdata = SkillResult("structured-data-audit")
    SD._check_microdata_only(microdata, pages)

    named_by_validity = {url for f in validity.findings
                         for url in f.get("affected_pages") or []}
    named_by_microdata = {url for f in microdata.findings
                          for url in f.get("affected_pages") or []}
    assert broke["url"] in named_by_validity
    assert absent["url"] in named_by_microdata
    assert not (named_by_validity & named_by_microdata)


# --------------------------------------------------------------------------
# The query field is the box someone types into, not the menu beside it
# --------------------------------------------------------------------------

SCOPE_MENU_SEARCH = """
<html><body>
<form method="GET" action="search">
  <select name="s" id="searchtype">
    <option value="d">Search Documentation</option>
    <option value="c">Search Changelog</option>
  </select>
  <input type="text" name="q" id="searchbox" value="">
  <input type="submit" value="Go">
</form>
</body></html>
"""


def test_a_scope_menu_in_front_of_the_box_is_not_the_query_field():
    """The extractor took "the first named visible field", and a `<select>` is
    a visible field. A documentation site puts a two-option menu - which index
    to search - in front of its search box, so the report declared the search
    target as `.../search?s={search_term_string}`. `s` carries a one-letter
    scope code; the words go in `q`, and the suggested URL returns nothing."""
    form = _forms(make_soup(SCOPE_MENU_SEARCH))[0]

    assert form["query_field"] == "q"
    assert form["action"] == "search"
    assert form["is_search"]


def test_the_search_target_built_from_that_form_is_the_working_one():
    """End of the same path: the paste-ready SearchAction a developer copies."""
    form = _forms(make_soup(SCOPE_MENU_SEARCH))[0]
    home = {"url": SITE + "/index.html", "page_type": "home", "status": 200,
            "title": "Docs", "headings": {}, "jsonld": [], "jsonld_types": [],
            "microdata_types": [], "has_search": True, "forms": [form],
            "links": {"internal": []}}
    result = SkillResult("structured-data-audit")
    SD._check_website_searchaction(result, {"home": [home]})

    snippet = result.findings[0]["suggested_action"]["snippet"]
    assert "search?q={search_term_string}" in snippet
    assert "?s=" not in snippet


def test_a_typed_box_of_any_search_flavour_is_found():
    html = ('<form action="/find"><input type="search" name="query">'
            '<button type="submit">Go</button></form>')
    assert _forms(make_soup(html))[0]["query_field"] == "query"


def test_a_hidden_field_in_front_of_the_box_is_not_the_query_field():
    html = ('<form action="/search"><input type="hidden" name="lang" value="ja">'
            '<input type="text" name="keywords"></form>')
    assert _forms(make_soup(html))[0]["query_field"] == "keywords"


def test_a_control_that_is_not_typed_into_is_never_the_query_field():
    """A date stepper, a checkbox and a menu all precede the box on real search
    pages, and none of them holds the words someone searched for."""
    html = ('<form action="/search">'
            '<input type="date" name="from">'
            '<input type="checkbox" name="exact" value="1">'
            '<select name="section"><option>All</option></select>'
            '<input name="terms"></form>')
    assert _forms(make_soup(html))[0]["query_field"] == "terms"


def test_a_form_with_nothing_to_type_into_guesses_nothing():
    """The designed behaviour when the form cannot be read: say so rather than
    invent a field name. The SearchAction check prints that refusal."""
    html = '<form action="/search"><select name="s"><option>Docs</option></select></form>'
    assert _forms(make_soup(html))[0]["query_field"] == ""
