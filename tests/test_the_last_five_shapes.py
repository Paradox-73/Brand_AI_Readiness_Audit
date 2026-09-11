# -*- coding: utf-8 -*-
"""Five shapes from real sites that the checks read wrong until the end.

* A regional storefront's blog index carrying a megabyte of script and no link
  to any post, with no post crawled beneath it, was told to "write two or three
  sentences of body copy". Its list of posts is filled in by the script.
* A holding company's site gives a street address and "write us at the address
  shown above", and no telephone or email - and was told it never states a
  contact method.
* A national library's system writes an `Article` block on every page it
  serves, directions and jobs included, and those undated pages were asked for
  an article's author.
* The same holding company publishes every news release, quarterly report and
  shareholder letter as a PDF with nothing on the page but the link, and none
  of it counted as a document of record.
* A publisher answers every missing address with a redirect to its own error
  page, which answers 200 - and the check passed it as a site that tells a
  missing page from a real one.

Every host and organisation below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _module("render-readability-audit", "render_for_last_shapes")
STRUCTURED = _module("structured-data-audit", "structured_for_last_shapes")
ACCESS = _module("crawl-access-audit", "access_for_last_shapes")
FACTS = _module("fact-extractability-audit", "facts_for_last_shapes")

SHOP = "https://furniture-shop.test"


# --------------------------------------------------------------------------
# A blog index whose list the script fills in
# --------------------------------------------------------------------------

def _index(path, links=()):
    return {"url": SHOP + path, "final_url": SHOP + path, "status": 200,
            "scripts": {"inline_bytes": 800000}, "spa_shell": {},
            "links": {"internal": [{"url": SHOP + link} for link in links]}}


def test_a_blog_index_linking_nothing_beneath_it_is_set_aside_without_a_crawled_post():
    page = _index("/au/blog", links=("/au/", "/au/sofas"))
    assert RENDER._children_left_unlinked(page, [page, _index("/au/sofas")]) == []


def test_a_short_section_that_is_not_an_index_is_still_judged_as_usual():
    page = _index("/au/sofas", links=("/au/",))
    assert RENDER._children_left_unlinked(page, [page]) is None


def test_an_index_that_links_a_post_beneath_it_is_judged_as_usual():
    page = _index("/au/blog", links=("/au/blog/caring-for-linen",))
    assert RENDER._children_left_unlinked(page, [page]) is None


def test_an_index_with_no_script_to_fill_it_is_judged_as_usual():
    page = dict(_index("/au/blog"), scripts={"inline_bytes": 2000})
    assert RENDER._children_left_unlinked(page, [page]) is None


# --------------------------------------------------------------------------
# A postal address is a contact route
# --------------------------------------------------------------------------

HOLDING = "https://holding-group.test"


def _holding_snapshot():
    from page_extract import extract_page
    html = ("<html lang=\"en\"><head><title>Kestrel Holdings</title></head><body>"
            "<h1>Kestrel Holdings</h1>"
            "<p>Kestrel Holdings is a holding company that owns businesses in insurance, "
            "rail and energy.</p>"
            "<p>Kestrel Holdings, 3555 Farnam Street, Omaha, NE 68131.</p>"
            "<p>If you have a question for the company, write us at the address shown "
            "above.</p></body></html>")
    page = extract_page(url=HOLDING + "/", final_url=HOLDING + "/", status=200,
                        headers={"content-type": "text/html"}, html=html,
                        redirect_chain=[], elapsed_ms=5, depth=0,
                        source="homepage", origin=HOLDING)
    page["page_type"] = "home"
    return {"origin": HOLDING, "pages": [page], "sitemaps": [],
            "crawl": {"brand_name": "Kestrel Holdings", "render_mode": "static"}}


def _findings_of(result):
    if isinstance(result, dict):
        return result.get("findings") or []
    return getattr(result, "findings", []) or []


def test_a_site_giving_only_a_postal_address_is_not_told_it_has_no_contact_route():
    findings = _findings_of(FACTS.run(_holding_snapshot()))
    about_contact = [f for f in findings
                     if "contact" in (f.get("title") or "").lower()
                     and f.get("root_cause") == "missing-core-fact"]
    assert not about_contact, [f.get("title") for f in about_contact]


# --------------------------------------------------------------------------
# An undated page a CMS stamps as an Article
# --------------------------------------------------------------------------

LIBRARY = "https://national-library.test"


def _article_page(path, dated):
    return {"url": LIBRARY + path, "final_url": LIBRARY + path, "status": 200,
            "page_type": "article",
            "jsonld": [{"@type": "Article", "headline": "Directions"}],
            "jsonld_types": ["article"],
            "dates": {"has_any": dated, "visible": ["6 April 2026"] if dated else []},
            "headings": {"h1": ["Directions"]}, "links": {"internal": []},
            "body_text": "How to reach the reading rooms by rail and by bus. " * 6}


def _grade(pages):
    result = SkillResult("structured-data-audit")
    STRUCTURED._check_article(result, {"article": pages})
    return [f for f in result.findings if f["id_hint"] == "article-schema-missing-properties"]


def test_an_undated_page_the_system_stamps_as_an_article_is_not_asked_for_an_author():
    assert _grade([_article_page("/access", dated=False),
                   _article_page("/employ", dated=False)]) == []


def test_a_dated_article_missing_its_author_is_still_graded():
    findings = _grade([_article_page("/news/reading-room-reopens", dated=True)])
    assert len(findings) == 1


# --------------------------------------------------------------------------
# A company's documents of record
# --------------------------------------------------------------------------

def test_corporate_filings_are_documents_of_record():
    for text in ("News Release", "Quarterly Report", "Letter to Shareholders",
                 "Form 10-K", "Proxy Statement", "Interim Results", "Annual Report"):
        assert RENDER.PUBLIC_RECORD_PDF_RE.search(text), text


def test_ordinary_link_text_is_still_not_a_document_of_record():
    for text in ("Plan your visit", "Report a problem", "Results for 'sofa'",
                 "Read the letter from our founder"):
        assert not RENDER.PUBLIC_RECORD_PDF_RE.search(text), text


# --------------------------------------------------------------------------
# A redirect to the site's own error page
# --------------------------------------------------------------------------

PUBLISHER = "https://arabic-publisher.test"


class _Hop:
    def __init__(self, status):
        self.status_code = status


class _Response:
    def __init__(self, landed, title):
        self.status_code = 200
        self.url = landed
        self.history = [_Hop(302)]
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.content = ("<html><head><title>{0}</title></head><body><h1>{0}</h1>"
                        "<p>Something went wrong.</p></body></html>".format(title)).encode()


class _Fetcher:
    budget_left = True
    time_left = True

    def __init__(self, response):
        self.response = response

    def try_get(self, url):
        return self.response


def _probe(landed, title):
    snapshot = {"origin": PUBLISHER,
                "pages": [{"url": PUBLISHER + "/", "final_url": PUBLISHER + "/",
                           "status": 200, "page_type": "home",
                           "canonical": PUBLISHER + "/"}]}
    result = SkillResult("crawl-access-audit")
    ACCESS._check_soft_404_handling(result, snapshot, _Fetcher(_Response(landed, title)))
    return result


def test_a_missing_address_redirected_to_an_error_page_answering_200_is_a_soft_404():
    result = _probe(PUBLISHER + "/error.aspx", "Error")
    findings = [f for f in result.findings if f["id_hint"] == "unknown-paths-return-200"]
    assert len(findings) == 1
    first_step = findings[0]["suggested_action"]["how_to_fix"][0]
    assert "error.aspx" in first_step and "404" in first_step


def test_a_redirect_to_a_search_page_is_still_the_site_telling_them_apart():
    result = _probe(PUBLISHER + "/search?q=missing", "Search the archive")
    assert not [f for f in result.findings if f["id_hint"] == "unknown-paths-return-200"]
