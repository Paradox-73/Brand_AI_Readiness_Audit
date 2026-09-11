# -*- coding: utf-8 -*-
"""Five readings that each turned a true fact about a shop into a false finding.

  a category called a cart        a handicrafts shop files its baskets at
                                  `/c/basket` ("Buy Basket Online"). The crawl
                                  refused the whole category and the sitemap
                                  check published it as a cart address.
  editions called unlinked        a retailer declares every country edition of
                                  every address as `<xhtml:link hreflang>` in
                                  its sitemap; the crawl had the file and the
                                  report said no edition links to another.
  a name hidden by its mark       "<Name>®" declared, "<Name> is a lifestyle
                                  brand ..." written, and no definition found.
  a slogan read as a definition   "<Name> is about bringing the world a little
                                  closer together" quoted as what the brand is.
  a storefront typed as a FAQ     one storefront per country at `/sg`, `/au`,
                                  `/uk` behind a picker at `/`, each typed by
                                  its FAQPage markup, and only `/` called home.

Every host and name here is invented.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

import audit_common as AC  # noqa: E402
import crawl  # noqa: E402
from audit_common import Fetcher, SkillResult, detect_page_type, is_forbidden_path  # noqa: E402
from fixture_server import FixtureServer  # noqa: E402
from page_extract import extract_page  # noqa: E402


def _module(name, alias):
    path = os.path.join(ROOT, "skills", name, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module("crawl-access-audit", "ca_for_paths_editions_tests")
FACTS = _module("fact-extractability-audit", "facts_for_paths_editions_tests")

SHOP = "https://woven-goods.test"


# --------------------------------------------------------------------------
# A basket word is a cart only where a cart lives
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/basket", "/basket/", "/basket.aspx", "/checkout/basket", "/en-gb/basket",
    "/cart", "/cart/", "/checkout", "/my-account/",
])
def test_a_real_basket_is_still_refused_and_still_named(path):
    assert is_forbidden_path(SHOP + path)
    assert CA._per_visitor_kind(SHOP + path) in ("cart", "checkout", "account")


@pytest.mark.parametrize("path", [
    "/c/basket", "/c/basket?page=2", "/collections/basket", "/category/basket/",
    "/basketball",
])
def test_a_category_named_basket_is_read_and_not_called_a_cart(path):
    assert not is_forbidden_path(SHOP + path), "the crawler still refuses " + path
    assert CA._per_visitor_kind(SHOP + path) == "", path


def test_a_sitemap_listing_a_basket_category_raises_nothing():
    urls = [SHOP + "/c/basket", SHOP + "/c/bedsheets", SHOP + "/p/one-mug.html"]
    result = SkillResult("crawl-access-audit")
    CA._check_sitemap_lists_private_paths(result, [{"urls": [{"loc": u} for u in urls]}])
    assert result.findings == []


def test_a_fetched_listing_outranks_the_word_in_its_address():
    """`/basket` in first position, but the crawl read it and it is a shelf."""
    urls = [SHOP + "/basket", SHOP + "/cart"]
    pages = [{"url": SHOP + "/basket", "status": 200, "page_type": "category",
              "jsonld_types": ["ItemList"], "headings": {}, "links": {}}]
    result = SkillResult("crawl-access-audit")
    CA._check_sitemap_lists_private_paths(
        result, [{"urls": [{"loc": u} for u in urls]}], pages)
    finding = result.findings[0]
    assert finding["affected_pages"] == [SHOP + "/cart"]
    assert SHOP + "/basket (" not in finding["evidence"]


# --------------------------------------------------------------------------
# Editions declared in the sitemap are declared
# --------------------------------------------------------------------------

EDITIONS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="https://an-invented-namespace.test/1999/xhtml">
  <url><loc>{base}/sg</loc>
    <xhtml:link rel="alternate" hreflang="en-sg" href="{base}/sg"/>
    <xhtml:link rel="alternate" hreflang="en-au" href="{base}/au"/>
    <xhtml:link rel="alternate" hreflang="en-ca" href="{base}/ca"/>
  </url>
  <url><loc>{base}/sg/sofas</loc>
    <xhtml:link rel="alternate" hreflang="en-sg" href="/sg/sofas"/>
    <xhtml:link rel="alternate" hreflang="en-au" href="/au/sofas"/>
  </url>
  <url><loc>{base}/sg/about</loc></url>
</urlset>
"""


def test_the_sitemap_alternates_are_read_from_each_entry():
    root = ET.fromstring(EDITIONS_SITEMAP.format(base=SHOP).encode("utf-8"))
    entries = list(root)
    assert crawl._sitemap_alternates(entries[0], SHOP + "/sitemap.xml") == [
        ("en-sg", SHOP + "/sg"), ("en-au", SHOP + "/au"), ("en-ca", SHOP + "/ca")]
    # A relative `href` is resolved against the sitemap, like a relative `<loc>`.
    assert crawl._sitemap_alternates(entries[1], SHOP + "/sitemap.xml")[1] == (
        "en-au", SHOP + "/au/sofas")
    assert crawl._sitemap_alternates(entries[2], SHOP + "/sitemap.xml") == []


@pytest.fixture(scope="module")
def editions_site(tmp_path_factory):
    root = tmp_path_factory.mktemp("editions_site")
    (root / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    return root


def test_the_crawl_records_the_alternates_of_the_sitemap_it_read(editions_site):
    with FixtureServer(str(editions_site)) as server:
        (editions_site / "sitemap.xml").write_text(
            EDITIONS_SITEMAP.format(base=server.base_url.rstrip("/")), encoding="utf-8")
        records, _in_robots = crawl.fetch_sitemaps(
            Fetcher(max_requests=6), server.base_url.rstrip("/"), {"sitemaps": []},
            time.monotonic() + 30)
    read = [r for r in records if r.get("urls")]
    assert read, "the fixture sitemap was not read"
    record = read[0]
    assert record["hreflang_url_count"] == 2
    assert record["hreflang_codes"] == ["en-au", "en-ca", "en-sg"]
    assert record["hreflang_example"]["loc"].endswith("/sg")


def _edition_pages():
    return [{"url": SHOP + "/" + code, "final_url": SHOP + "/" + code, "status": 200,
             "lang": "en-" + code.upper(), "page_type": "home", "depth": 1}
            for code in ("au", "ca", "sg")]


def test_editions_declared_in_the_sitemap_are_not_reported_as_unlinked():
    pages = _edition_pages()
    snapshot = {"origin": SHOP, "pages": pages, "sitemaps": [{
        "url": SHOP + "/sitemap.xml", "status": 200,
        "urls": [{"loc": SHOP + "/sg", "lastmod": ""}],
        "hreflang_url_count": 1, "hreflang_codes": ["en-au", "en-ca", "en-sg"]}]}
    result = SkillResult("crawl-access-audit")
    fetcher = Fetcher(max_requests=4)
    CA._check_hreflang(result, snapshot, pages, fetcher, robots={})
    assert result.findings == []
    assert fetcher.count == 0, "the sitemap settled it; no page needed reading"
    assert "sitemap" in result.not_applicable[0]["reason"]


def test_a_sitemap_never_read_for_alternates_is_not_reported_as_holding_none():
    """A snapshot written before the crawl read alternates cannot say either
    way, and the finding may not say what the snapshot cannot."""
    pages = _edition_pages()
    snapshot = {"origin": SHOP, "pages": pages, "sitemaps": [{
        "url": SHOP + "/sitemap.xml", "status": 200,
        "urls": [{"loc": SHOP + "/sg", "lastmod": ""}]}]}
    result = SkillResult("crawl-access-audit")
    CA._check_hreflang(result, snapshot, pages, Fetcher(max_requests=4), robots={})
    assert result.findings == []
    assert "could not be read" in result.not_applicable[0]["reason"]


# --------------------------------------------------------------------------
# A registered or trade mark on a declared name
# --------------------------------------------------------------------------

DECORATED = u"Lanternwick®"
DEFINITION = u"Lanternwick is a lifestyle brand in home decor solutions for small flats."


@pytest.mark.parametrize("mark", [u"®", u"™", u"©", u" ™"])
def test_a_marked_name_finds_its_own_definition(mark):
    name = u"Lanternwick" + mark
    assert AC.defining_sentence(DEFINITION, name) == DEFINITION.rstrip(".")
    assert FACTS._find_definition(DEFINITION, name) is not None


def test_the_mark_printed_in_the_sentence_is_read_as_part_of_the_name():
    text = u"Lanternwick® is a lifestyle brand in home decor solutions for small flats."
    assert AC.defining_sentence(text, "Lanternwick").startswith(u"Lanternwick® is a")
    assert AC.defining_sentence(text, DECORATED).startswith(u"Lanternwick® is a")


def test_every_name_comparison_sees_through_the_mark():
    assert "Lanternwick" in AC.name_forms(DECORATED)
    assert AC.brand_key(u"Lanternwick ®") == "lanternwick"
    assert AC.name_appears_in(DECORATED, "Welcome to Lanternwick, lamps for small flats")
    assert AC.names_match(DECORATED, "Lanternwick")
    assert "Lanternwick" in AC.brand_forms(DECORATED)


# --------------------------------------------------------------------------
# A slogan is not a definition
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Lanternwick is about bringing the world a little closer together.",
    "Lanternwick is all about bringing light to small flats everywhere.",
    "Lanternwick is more than a shop for lamps and candles in small flats.",
    "Lanternwick is not just a lamp shop but a way of seeing your home.",
    "Lanternwick is here to light every small flat in the country.",
    "Lanternwick is  about bringing the world a little closer together.",
    "Lanternwick is proud to light every small flat in the country.",
])
def test_a_slogan_after_the_copula_is_not_a_definition(text):
    assert AC.defining_sentence(text, "Lanternwick", {"name": "Lanternwick"}) == ""


def test_the_definition_after_a_slogan_is_still_found():
    text = ("Lanternwick is about bringing the world a little closer together. "
            "Lanternwick is a lamp maker for small flats.")
    assert AC.defining_sentence(text, "Lanternwick") == "Lanternwick is a lamp maker for small flats"


@pytest.mark.parametrize("text", [
    "Lanternwick is a lamp maker for small flats.",
    "Lanternwick is independent software for planning cargo across the region.",
    "Lanternwick is one of the largest lamp makers in the country.",
    "Lanternwick is the largest lamp maker in the country.",
    "Lanternwick is where-to-buy software for wholesale lamp traders.",
])
def test_a_real_copula_definition_is_still_found(text):
    """"is where-to-buy software" keeps its hyphen: the word boundary after
    `where` is a hyphen, so the shape is caught only as a whole word - that
    case is the one pinned as rejected-by-design below."""
    found = AC.defining_sentence(text, "Lanternwick")
    if text.startswith("Lanternwick is where"):
        assert found == ""
    else:
        assert found


# --------------------------------------------------------------------------
# One region's storefront behind a picker at the root
# --------------------------------------------------------------------------

ORIGIN = "https://sofa-atlas.test"

PICKER = """<!DOCTYPE html><html lang="en-US"><head><meta charset="utf-8">
<title>Choose your region | Sofa Atlas</title></head><body>
<h1>Choose your region</h1>
<ul><li><a href="/sg">Singapore</a></li><li><a href="/au">Australia</a></li>
<li><a href="/uk">United Kingdom</a></li><li><a href="/it">Our IT team</a></li></ul>
</body></html>"""

STOREFRONT = """<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8">
<title>Modern Furniture Store Online | Sofa Atlas</title>
<script type="application/ld+json">{{"@context": "https://schema.org",
 "@type": "FAQPage", "mainEntity": [{{"@type": "Question", "name": "Do you deliver?",
 "acceptedAnswer": {{"@type": "Answer", "text": "Yes, everywhere in the region."}}}}]}}
</script></head><body><h1>Modern furniture, delivered</h1>
<p>Sofas, tables and beds for every room, delivered across the region in two weeks.</p>
<h2>Do you deliver?</h2><p>Yes, everywhere in the region.</p>
<h2>Can I return a sofa?</h2><p>Within a hundred days, collected from your door.</p>
<h2>How long does delivery take?</h2><p>Two weeks from the day you order.</p>
<h2>Where are your showrooms?</h2><p>In every capital we deliver to.</p>
</body></html>"""


def _extract(path, html):
    url = ORIGIN + path
    return extract_page(url, url, 200, {"content-type": "text/html"}, html, [], 10,
                        0 if path == "/" else 1, "homepage" if path == "/" else "bfs",
                        ORIGIN)


def test_a_regional_storefront_is_typed_home_once_the_root_is_known():
    root = _extract("/", PICKER)
    pages = [root]
    for path, lang in (("/sg", "en-SG"), ("/au", "en-AU"), ("/uk", "en-GB")):
        page = _extract(path, STOREFRONT.format(lang=lang))
        # Page by page, a storefront carrying FAQPage markup reads as a FAQ,
        # which is the reading this has to correct and not the defect itself.
        assert page["page_type"] == "faq", path
        pages.append(page)
    crawl._type_regional_homepages(pages, root)
    assert [p["page_type"] for p in pages] == ["home", "home", "home", "home"]
    assert "region" in pages[1]["page_type_source"]


def test_a_two_letter_section_linked_from_the_root_stays_what_it_is():
    """`/it` is linked from the same picker, but the page is an English
    department page, and a page's own `lang` is what names its region."""
    root = _extract("/", PICKER)
    team = _extract("/it", STOREFRONT.format(lang="en"))
    crawl._type_regional_homepages([root, team], root)
    assert team["page_type"] == "faq"


def test_the_classifier_reads_the_root_links_when_given_them():
    links = [ORIGIN + "/sg", ORIGIN + "/au", ORIGIN + "/about"]
    meta = {"text": "", "headings": {}, "jsonld_types": ["FAQPage"], "title": "",
            "lang": "en-SG"}
    assert detect_page_type(ORIGIN + "/sg", meta) == "faq"
    assert detect_page_type(ORIGIN + "/sg", dict(meta, root_links=links)) == "home"
    # One region-shaped link is not a picker.
    assert detect_page_type(ORIGIN + "/sg", dict(meta, root_links=[ORIGIN + "/sg"])) == "faq"
    # A region the page does not declare is not its region.
    assert detect_page_type(ORIGIN + "/au",
                            dict(meta, root_links=links)) == "faq"
    # A deeper address is a page of the storefront, not its homepage.
    assert detect_page_type(ORIGIN + "/sg/sofas", dict(meta, root_links=links)) == "faq"


# ==========================================================================
# The same rules on awkward sites
# ==========================================================================

# --------------------------------------------------------------------------
# A root that is only a pointer: follow the pointer
# --------------------------------------------------------------------------

POINTER_ROOT = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Pointer</title>
<link rel="alternate" hreflang="en" href="/en/">
<link rel="alternate" hreflang="de" href="/de/">
<link rel="alternate" hreflang="x-default" href="/en/">
<script>var lang = navigator.language; window.location = "/en/";</script>
</head><body></body></html>"""

EDITION = """<!DOCTYPE html><html lang="{code}"><head><meta charset="utf-8">
<title>An Invented Etiquette Note</title></head><body><h1>Say what you need</h1>
<p>Write the whole question in your first message rather than a greeting on its
own, so the person reading it can answer without waiting for a second one.</p>
</body></html>"""


def test_the_page_records_its_alternates_and_its_script_redirect():
    base = "https://pointer-note.test"
    page = extract_page(base + "/", base + "/", 200, {"content-type": "text/html"},
                        POINTER_ROOT, [], 5, 0, "homepage", base)
    assert [a["url"] for a in page["hreflang"]] == [base + "/en/", base + "/de/", base + "/en/"]
    assert page["script_redirect"] == {"url": base + "/en/"}


@pytest.mark.parametrize("script", [
    'window.location.href = "https://elsewhere.test/en/";',  # another site
    'if (location.href == "/en/") {}',                       # a comparison, not a move
])
def test_a_script_redirect_is_only_a_fixed_address_on_the_same_site(script):
    base = "https://pointer-note.test"
    html = "<html><head><script>{}</script></head><body></body></html>".format(script)
    page = extract_page(base + "/", base + "/", 200, {"content-type": "text/html"},
                        html, [], 5, 0, "homepage", base)
    assert page["script_redirect"] is None


def test_a_computed_destination_is_recorded_with_no_address_to_follow():
    """Still a redirect, so no check calls the page thin copy; but there is no
    literal address, so the crawl has nothing to queue from it."""
    base = "https://pointer-note.test"
    html = ("<html><head><script>window.location = destination;</script></head>"
            "<body></body></html>")
    page = extract_page(base + "/", base + "/", 200, {"content-type": "text/html"},
                        html, [], 5, 0, "homepage", base)
    assert page["script_redirect"] == {"url": "", "computed": True}


def test_the_crawl_follows_the_alternates_and_the_script_from_an_empty_root(tmp_path):
    from conftest import run_script
    site = tmp_path / "pointer_site"
    site.mkdir()
    (site / "index.html").write_text(POINTER_ROOT, encoding="utf-8")
    for code in ("en", "de"):
        (site / code).mkdir()
        (site / code / "index.html").write_text(EDITION.format(code=code), encoding="utf-8")
    out = tmp_path / "snapshot.json"
    with FixtureServer(str(site)) as server:
        run_script([os.path.join(SCRIPTS, "crawl.py"), server.base_url, "--out", str(out),
                    "--budget", "60", "--delay", "0", "--no-render"], "crawl.py [pointer]")
    snapshot = json.load(open(str(out), encoding="utf-8"))
    by_path = {"/" + p["url"].split("://")[-1].split("/", 1)[-1]: p
               for p in snapshot["pages"]}
    assert "/en/" in by_path and "/de/" in by_path, sorted(by_path)
    assert by_path["/en/"]["source"] == "script-redirect"
    assert by_path["/de/"]["source"] == "hreflang"


# --------------------------------------------------------------------------
# A family of accounts named after a site whose name no handle can spell
# --------------------------------------------------------------------------

def _external(*urls):
    return [{"url": url, "text": ""} for url in urls]


def test_several_handles_opening_with_the_domain_word_are_the_sites_own():
    from page_extract import _off_site_profiles, _social_profiles
    base = "https://www.ndx.go.test/"
    links = _external("https://x.com/NDXJP", "https://x.com/NDXKIDS",
                      "https://instagram.com/ndximagebank",
                      "https://www.facebook.com/NDXexhibition",
                      "https://x.com/unrelated_desk")
    found = {e["url"]: e["attributed"] for e in _off_site_profiles(
        links, [], AC.make_soup(u"<p>x</p>"), base)}
    assert found["https://x.com/NDXJP"] and found["https://instagram.com/ndximagebank"]
    assert found["https://www.facebook.com/NDXexhibition"]
    # An account that does not open with the site's word is still nobody's.
    assert not found["https://x.com/unrelated_desk"]
    assert set(_social_profiles(links, [], AC.make_soup(u"<p>x</p>"), base)) == {
        "X", "Instagram", "Facebook"}


def test_one_handle_opening_with_the_domain_word_is_still_a_coincidence():
    from page_extract import _off_site_profiles
    found = _off_site_profiles(_external("https://instagram.com/ndxbakery"), [],
                               AC.make_soup(u"<p>x</p>"), "https://www.ndx.go.test/")
    assert [e["attributed"] for e in found] == [False]


# --------------------------------------------------------------------------
# robots.txt rules that close the machinery, not the content
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rule", [
    "/ForgetPassword.aspx", "/Newsletter_Unsubscribe.aspx", "/trackback/",
    "/wp-cron.php", "/WebResource.axd", "/feed/", "/SignIn", "/xmlrpc.php",
])
def test_a_rule_closing_a_handler_is_plumbing(rule):
    assert CA._names_plumbing(rule)


@pytest.mark.parametrize("rule", [
    "/products/", "/feedback/", "/passwords/", "/research/", "/news/", "/Catalogue.aspx",
])
def test_a_rule_closing_a_section_is_not_plumbing(rule):
    assert not CA._names_plumbing(rule)


def test_two_handlers_are_not_reported_as_real_content():
    robots = {"status": 200, "url": SHOP + "/robots.txt", "sitemaps": [],
              "raw": "User-agent: *\nDisallow: /ForgetPassword.aspx\n"
                     "Disallow: /Newsletter_Unsubscribe.aspx\n",
              "groups": [{"agents": ["*"], "disallow": ["/ForgetPassword.aspx",
                                                         "/Newsletter_Unsubscribe.aspx"],
                          "allow": []}]}
    snapshot = {"origin": SHOP, "robots": robots, "sitemaps": [],
                "pages": [{"url": SHOP + "/", "final_url": SHOP + "/", "status": 200,
                           "page_type": "home", "links": {}, "headings": {}}]}
    result = CA.run(snapshot, allow_network=False)
    assert "robots-blocks-content-paths" not in {f["id_hint"] for f in result.findings}


# --------------------------------------------------------------------------
# A certificate chain one short is not a host that never answered
# --------------------------------------------------------------------------

CHAIN_ERROR = ("HTTPSConnectionPool(host='www.library.test', port=443): Max retries exceeded "
               "with url: / (Caused by SSLError(SSLCertVerificationError(1, '[SSL: "
               "CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local "
               "issuer certificate (_ssl.c:1010)')))")


def test_an_incomplete_chain_is_named_as_one():
    assert AC.incomplete_certificate_chain(CHAIN_ERROR)
    assert "intermediate" in AC.explain_fetch_error(CHAIN_ERROR)
    assert not AC.incomplete_certificate_chain("certificate verify failed: certificate has expired")
    assert "intermediate" not in AC.explain_fetch_error(
        "certificate verify failed: certificate has expired")


def test_the_homepage_finding_prescribes_the_intermediate_and_not_a_log_search():
    origin = "https://www.library.test"
    snapshot = {"origin": origin, "robots": {}, "sitemaps": [],
                "pages": [{"url": origin + "/", "status": None, "error": CHAIN_ERROR,
                           "links": {}}]}
    result = CA.run(snapshot, allow_network=False)
    home = [f for f in result.findings if f["id_hint"] == "homepage-not-reachable"]
    assert home, [f["id_hint"] for f in result.findings]
    text = json.dumps(home[0])
    assert "intermediate" in text and "CDN log" not in text
    assert home[0]["suggested_action"]["effort"] == "low"
    assert not any("did not respond at all" in n["reason"] for n in result.not_applicable)


# --------------------------------------------------------------------------
# A lastmod that is only the day of the request
# --------------------------------------------------------------------------

def test_a_date_equal_to_the_day_of_the_request_is_not_a_lastmod():
    same_day = {"headers": {"date": "Fri, 11 Sep 2026 08:00:00 GMT",
                            "last-modified": "Fri, 11 Sep 2026 08:00:00 GMT"},
                "dates": {"jsonld_date_modified": "2026-09-11T08:00:00Z"}}
    assert CA._observed_lastmod(same_day) == ""
    earlier = {"headers": {"date": "Fri, 11 Sep 2026 08:00:00 GMT",
                           "last-modified": "Mon, 03 Aug 2026 10:00:00 GMT"}}
    assert CA._observed_lastmod(earlier) == "2026-08-03"


# --------------------------------------------------------------------------
# Dates in the shapes a library and a Nordic site print; amounts are not years
# --------------------------------------------------------------------------

def test_year_first_and_dotted_day_first_dates_are_read():
    from page_extract import DATE_TEXT_RE
    text = "Notice 2026.09.10, filed 2026/09/10, posted 10.09.2026; build 2026.1.300."
    assert [m.group(0) for m in DATE_TEXT_RE.finditer(text)] == [
        "2026.09.10", "2026/09/10", "10.09.2026"]


@pytest.mark.parametrize("text", [
    u"ราคา 2500 บาท",      # "price 2,500 baht"
    u"จำนวน 2450 เล่ม",    # "2,450 volumes"
    u"ปี 2500 บาท",        # a year word, and then an amount after all
])
def test_a_number_beside_a_currency_or_counting_word_is_not_a_year(text):
    assert AC.thai_era_dates(text) == []


def test_an_abbreviated_thai_month_still_dates_the_year():
    assert AC.thai_era_dates(u"5 ส.ค. 2569") == [(u"5 ส.ค. 2569", 2026)]


# --------------------------------------------------------------------------
# What kind of site: a list of links is not an article, a team is not a company
# --------------------------------------------------------------------------

def _year_list_meta(links, words):
    return {"text": " ".join(["word"] * words), "headings": {"h2": ["2019 News"]},
            "jsonld_types": [], "title": "2019 News",
            "links": {"internal": [{"url": "https://holding.test/news/r{}.pdf".format(n),
                                    "text": "Release {}".format(n)} for n in range(links)]}}


def test_a_yearly_page_of_links_is_a_listing():
    assert detect_page_type("https://holding.test/news/2019news.html",
                            _year_list_meta(40, 110)) == "category"


def test_an_article_with_many_links_is_still_an_article():
    assert detect_page_type("https://holding.test/news/annual-letter.html",
                            _year_list_meta(40, 900)) == "article"


def _docs_snapshot(docs_pages=8):
    origin = "https://framework-docs.test"
    links = [{"url": origin + path, "text": path} for path in
             ["/guide/intro", "/api/", "/about/team"]]
    home = {"url": origin + "/", "final_url": origin + "/", "status": 200, "page_type": "home",
            "text": u"本中文文档采用 (CC BY-NC-SA 4.0) 进行许可。", "body_text": "",
            "links": {"internal": links,
                      "external": [{"url": "https://github.com/framework-docs",
                                    "text": "GitHub"}]},
            "jsonld_types": [], "headings": {}}
    # The documentation tree itself: crawled pages under `/guide/`, typed
    # `other` the way a translated docs tree's pages were.
    tree = [{"url": origin + "/guide/topic-{}.html".format(n), "status": 200,
             "page_type": "other", "links": {}, "headings": {}}
            for n in range(docs_pages)]
    return {"origin": origin, "brand": {"name": "Framework Docs"}, "pages": [home] + tree}


def test_a_docs_site_with_a_licence_line_and_a_team_page_is_a_project():
    kind = AC.site_kind(_docs_snapshot())
    assert kind.kind == AC.PROJECT, kind


def test_a_team_page_beside_one_docs_page_is_still_not_a_project():
    """A vendor with a repository, a licence line, a team page and a single
    docs page has not shown a documentation tree, so its team page still
    counts as the company it is."""
    assert AC.site_kind(_docs_snapshot(docs_pages=1)).kind != AC.PROJECT


def test_a_team_page_beside_careers_is_still_an_organisation():
    snapshot = _docs_snapshot()
    snapshot["pages"].append({"url": "https://framework-docs.test/careers", "status": 200,
                              "page_type": "careers", "links": {}, "headings": {}})
    assert AC.site_kind(snapshot).kind != AC.PROJECT
