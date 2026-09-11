# -*- coding: utf-8 -*-
"""Facts, snippet values, dates and addresses read off pages that meant something else.

Every case here is one line a report printed about a site when the page it came
from said something different:

  another party's notice as the footer  a newspaper's reprinted notice taken as
                                        the site's own year, and the site's own
                                        "(c) ... <name>.org" left out as a credit
  business facts asked of an essay      a one-page essay told it never states
                                        its founding or its location
  company platforms for an essay        LinkedIn and Instagram, while it links
                                        its own source repository
  a magazine masthead as the logo       the first image alphabetically, filed
                                        under the magazine's folder
  a placeholder beside a definition     "neither page carries a description"
                                        about a site that defines itself
  a citation number as a telephone      "(2025) 101326" from a bibliography
  a venue as the site's address         a workshop's host's street
  an adverb as a person                 "Newly created databases ..."
  a free line over a price list         "free and works great" on a page of
                                        yearly support prices
  a translation as a second name        a Latin name beside its CJK original
  a dated measurement as stale copy     "as of <full date>" beside a size
  an app page expected to be dated      an exhibition-guide app's page
  a directory and its index file        `/` and `/home.html`, not reported
  an office selector as a filter        a form whose parameter picks the office
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SiteKind, SkillResult, UNDETERMINED  # noqa: E402


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module("structured-data-audit", "structured_for_facts_and_dates")
FACTS = _module("fact-extractability-audit", "facts_for_facts_and_dates")
FRESHNESS = _module("freshness-corroboration-audit", "freshness_for_facts_and_dates")

PROJECT_HOST = "https://www.quillmark.test"
ESSAY_HOST = "https://hushline.test"
BANK_HOST = "https://www.ferncastle-reserve.test"
UNIVERSITY_HOST = "https://www.ferncastle-u.test"
AUDIT_DAY = datetime.date(2026, 9, 11)


def _page(url, page_type="other", **extra):
    page = {"url": url, "final_url": url, "status": 200, "page_type": page_type,
            "lang": "en", "title": "", "meta_description": "", "paragraphs": [],
            "headings": {}, "links": {"internal": [], "external": []}, "og": {},
            "jsonld": [], "jsonld_types": [], "body_text": "", "text": "",
            "content_type": "text/html"}
    page.update(extra)
    if page["body_text"] and not page["text"]:
        page["text"] = page["body_text"]
    return page


def _snapshot(pages, origin, name):
    return {"site": origin, "origin": origin, "pages": pages, "sitemaps": [],
            "brand": {"name": name, "host": origin.split("//")[1]},
            "crawl": {"pages_crawled": len(pages), "pages_ok": len(pages),
                      "render_mode": "static", "user_agent": "test-agent",
                      "elapsed_s": 1.0, "notes": []}}


def _everything(result):
    """Every string the result carries, findings and declines alike."""
    return json.dumps(vars(result), default=str, ensure_ascii=False)


def _payload(snippet):
    return json.loads(re.sub(r"</?script[^>]*>", "", snippet))


# --------------------------------------------------------------------------
# 1. Whose copyright notice it is
# --------------------------------------------------------------------------

def _licence_page():
    body = ("If you wish to use older versions, they have all been re-licensed under the "
            "same terms. Copyright © 1994–2026 Quillmark.test, Brookfield University. "
            "Permission is hereby granted, free of charge, to any person obtaining a copy.")
    return _page(PROJECT_HOST + "/license.html", body_text=body,
                 dates={"copyright_years": [1994, 2026]})


def _press_page():
    body = ("Quillmark in the press. Quillmark wins a prize at a national congress. "
            "Reprint from O Courier, 07 Jul 2015. Copyright © 2015 O Courier. Todos os "
            "direitos reservados. The surprising journey of a language. Reprint from "
            "Weekly Ledger, 21 Apr 2013. Copyright © 2013 Weekly Ledger. All rights reserved. "
            "Reprint from Gazeta Nacional, 2011. Copyright © 2011 Gazeta Nacional. Todos os "
            "direitos reservados. Last update: 2024")
    return _page(PROJECT_HOST + "/press.html", "press", body_text=body,
                 dates={"copyright_years": [2011, 2013, 2015]})


def test_a_notice_naming_the_sites_own_address_is_the_sites_notice():
    """The brand this audit arrived with was the homepage title, "The
    Programming Language <Name>", which no notice repeats. The site's own
    "(c) 1994-2026 <name>.org" was left out as another party's, and a
    newspaper's 2015 notice on the press page became "the copyright year in
    the footer is 2015"."""
    pages = [_licence_page(), _press_page()]
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_copyright_year(result, pages, AUDIT_DAY,
                                    "The Programming Language Quillmark",
                                    "www.quillmark.test")
    assert not [f for f in result.findings if f["id_hint"] == "footer-copyright-year-is-stale"]
    assert "2026" in _everything(result)


def test_notices_on_a_page_of_reprinted_clippings_are_their_publishers():
    """With no notice of its own, a page reprinting three papers' articles
    carries three papers' years, and none of them is the site's."""
    pages = [_press_page()]
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_copyright_year(result, pages, AUDIT_DAY,
                                    "The Programming Language Quillmark",
                                    "www.quillmark.test")
    assert not result.findings, [f["title"] for f in result.findings]


def test_a_bare_footer_notice_still_counts_as_the_sites():
    """The guard: an ordinary stale footer naming nobody is still reported."""
    body = ("Welcome to the Quillmark home page. " * 30) + "Copyright © 2019 All rights reserved."
    pages = [_page(PROJECT_HOST + "/", "home", body_text=body,
                   dates={"copyright_years": [2019]})]
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_copyright_year(result, pages, AUDIT_DAY, "Quillmark",
                                    "www.quillmark.test")
    assert [f for f in result.findings if f["id_hint"] == "footer-copyright-year-is-stale"]


# --------------------------------------------------------------------------
# 2 and 3. A one-page essay in several languages
# --------------------------------------------------------------------------

_ESSAY = ("Please do not ask whether anyone is around to help before you ask your "
          "question in a chat room. Ask the question itself, with the detail that lets "
          "someone answer it, and wait patiently for a reply from whoever knows. ")


def _essay_pages():
    source = {"url": "https://github.com/someauthor/hushline.test", "text": "Source on GitHub"}
    pages = []
    for path, lang in (("/", "en"), ("/de/", "de"), ("/fr/", "fr"), ("/tr/", "tr")):
        body = _ESSAY * 8
        pages.append(_page(ESSAY_HOST + path, "home", lang=lang, title="Just ask",
                           body_text=body, word_count=len(body.split()),
                           links={"internal": [], "external": [source], "footer": [source],
                                  "nav": []}))
    return pages


def test_an_essay_is_not_asked_for_its_founding_or_its_location():
    """A one-page essay in nine languages was told it never states its
    founding facts or its location, and handed "<Name> was founded in <year>
    in <place>" to publish."""
    pages = _essay_pages()
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, _snapshot(pages, ESSAY_HOST, "Just ask"), pages,
                            "Just ask", english=True)
    ids = {f["id_hint"] for f in result.findings}
    assert "core-fact-missing-founding-facts" not in ids
    assert "core-fact-missing-location-or-service-area" not in ids
    # And no company founding sentence offered to paste anywhere in it.
    assert "was founded in" not in _everything(result)


def test_an_essay_is_not_sent_to_a_press_kit():
    """The definition sentence's "repeat it on your LinkedIn page and in your
    press kit" is a company's list; an unplaced one-document site gets the
    list of places that describe a document."""
    step = FACTS._repeat_the_definition_step(SiteKind(UNDETERMINED, "low", ()),
                                             one_document=True)
    assert "LinkedIn" not in step and "press kit" not in step


def test_a_repository_named_after_the_site_is_its_profile_and_the_advice_fits():
    """The essay's footer links `<host>/<author>/<the site's address>`. The
    author's account is not the site's, the repository is - and the fix
    named LinkedIn, Instagram, YouTube, X and Facebook."""
    pages = _essay_pages()
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_authoritative_profiles(
        result, _snapshot(pages, ESSAY_HOST, "Just ask"), pages)
    finding = next(f for f in result.findings
                   if f["id_hint"] == "thin-off-site-profile-footprint")
    text = json.dumps(finding, ensure_ascii=False)
    assert finding["title"].startswith("Only 1 off-site profile")
    assert "LinkedIn, Instagram" not in text
    assert "repository host" in text


# --------------------------------------------------------------------------
# 4. The Organization block's logo and description
# --------------------------------------------------------------------------

def test_the_logo_is_the_sites_mark_and_not_a_magazines_masthead():
    """A central bank's block shipped its magazine's masthead because "b"
    sorts before "l"; its own marks sat under `/logo/` on every page."""
    masthead = BANK_HOST + "/content/dam/fr/fr-magazine/Logo%20Masthead.JPG"
    mark = BANK_HOST + "/content/dam/fr/logo/logo-top-blue.png"
    pages = [
        _page(BANK_HOST + "/", "home", images={"missing_alt_sample": [masthead, mark]}),
        _page(BANK_HOST + "/about", "about", images={"missing_alt_sample": [masthead, mark]}),
    ]
    snapshot = _snapshot(pages, BANK_HOST, "Ferncastle Reserve")
    assert SD._site_logo(snapshot, pages, {"name": "Ferncastle Reserve"}) == mark


def test_the_description_is_the_sites_own_definition_before_a_placeholder():
    """The front door is in another language and defines nothing; the English
    about page opens with the definition, quoted by the other skill three
    sections earlier."""
    definition = ("The Ferncastle Reserve is a public institution with a mandate to keep "
                  "prices stable for everyone who lives in the country.")
    pages = [
        _page(BANK_HOST + "/", "home", lang="th", paragraphs=["ยินดีต้อนรับ"]),
        _page(BANK_HOST + "/en/about-us.html", "about", lang="en", paragraphs=[definition]),
    ]
    snapshot = _snapshot(pages, BANK_HOST, "Ferncastle Reserve")
    payload = _payload(SD._org_snippet(snapshot, pages, {"name": "Ferncastle Reserve"}))
    assert "public institution" in payload["description"]


# --------------------------------------------------------------------------
# 5. Appendix facts that are not facts
# --------------------------------------------------------------------------

def test_a_citation_year_and_number_is_not_a_telephone_number():
    page = _page(PROJECT_HOST + "/docs.html",
                 body_text="The evolution, continued, Journal of Languages 83 (2025) 101326. [doi]")
    assert FACTS._usable_phones({"phones": ["(2025) 101326"]}, page) == []


def test_a_run_of_years_and_months_is_not_a_telephone_number():
    page = _page(UNIVERSITY_HOST + "/news.html", body_text="Archive 2018 2017.12 2017.11")
    assert FACTS._usable_phones({"phones": ["2018 2017.12"]}, page) == []


def test_an_address_on_a_workshop_page_is_the_venues():
    """"The workshop will be held at <company>'s Headquarters <street>" was
    printed as the project's own location, and the venue's switchboard was the
    next candidate for its contact route."""
    venue = _page(PROJECT_HOST + "/wshop12.html", title="Quillmark: workshop 2012",
                  headings={"h1": ["Workshop 2012"]},
                  body_text="Venue The workshop will be held at Examplecorp's Headquarters "
                            "12061 Example Way, Reston, VA 20190. See map and directions.")
    assert FACTS._an_events_page(venue, "12061 Example Way")
    untitled = _page(PROJECT_HOST + "/meeting.html", title="Quillmark: 2012",
                     body_text="The meeting will be held at 12061 Example Way, Reston.")
    assert FACTS._an_events_page(untitled, "12061 Example Way")


def test_an_address_on_the_contact_page_is_still_the_sites():
    """The guard: an ordinary contact page's address is the site's own."""
    contact = _page(PROJECT_HOST + "/contact.html", "contact", title="Contact Quillmark",
                    headings={"h1": ["Contact us"]},
                    body_text="Write to us at 4 Harbour Row, Brookfield, BF1 2AB.")
    assert not FACTS._an_events_page(contact, "4 Harbour Row")


def test_a_sentence_about_software_names_no_person():
    """Each of these was printed as a team fact once the one before it was
    ruled out."""
    for sentence in ("Newly created databases are initially empty.",
                     "Measure write performance by adding the --update option.",
                     "Because writes are so much slower than reads, few blobs are replaced.",
                     "Default builds of Quillmark contain the right objects for each system.",
                     "As well as aggregate functions, Quillmark features built-in window "
                     "functions based on those supported by OrbitSQL.",
                     "You can help by buying a book published by Quillmark.test today."):
        match = FACTS.TEAM_FACT_RE.search(sentence)
        assert not (match and FACTS._states_a_named_person(sentence, match, "Quillmark")), \
            sentence


def test_a_named_maintainer_is_still_a_person():
    sentence = "After eight years elsewhere, Wilma continues to maintain Corvid."
    match = FACTS.TEAM_FACT_RE.search(sentence)
    assert match and FACTS._states_a_named_person(sentence, match, "Corvid")


def test_page_serving_is_not_a_service_area():
    sentence = ("We made a special version to be included in a browser extension for "
                "dynamic page serving and CGI programming in Quillmark.")
    match = FACTS.SERVICE_AREA_RE.search(sentence)
    assert match and not FACTS._states_a_service_area(sentence, match)


def test_yearly_support_prices_are_the_sites_pricing():
    """"<Name> is free and works great." was printed as the pricing, and the
    site as one where nothing is for sale, beside a page of yearly prices."""
    body = ("Professional support. Annual Maintenance Subscription. Private email advice "
            "from the developers. $1500/year. Technical Support. $8K-85K/year. "
            "Further information. Quillmark is free and works great. An annual maintenance "
            "subscription costs $1500 per year. The cost of technical support is generally "
            "in the range of $8000 to $35000 per year.")
    support = _page(PROJECT_HOST + "/prosupport.html", title="Quillmark Professional Support",
                    body_text=body, prices=["$1500", "$8", "$8000", "$35000"])
    # Every other fact present, so the facts found are printed rather than
    # the ones missing.
    home = _page(PROJECT_HOST + "/", "home",
                 body_text="Quillmark is a small database engine. Quillmark was founded in "
                           "2001 in Brookfield by a small team. Write to us at 4 Harbour Row, "
                           "Brookfield, BF1 2AB.",
                 contact_facts={"chrome_emails": ["hello@quillmark.test"],
                                "emails": ["hello@quillmark.test"], "has_address": True,
                                "street_hint": "4 Harbour Row", "postcode_hint": "BF1 2AB"})
    pages = [home, support]
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, _snapshot(pages, PROJECT_HOST, "Quillmark"), pages,
                            "Quillmark", english=True)
    text = _everything(result)
    assert "per year" in text
    assert "free and works great" not in text


# --------------------------------------------------------------------------
# 6. A name and its translation
# --------------------------------------------------------------------------

def test_a_latin_name_beside_its_cjk_original_is_a_translation():
    """Japanese pages carrying the English name in og:site_name, beside the
    Japanese one on the rest: "the brand name is written more than one way",
    with a fix that would delete one of the institution's two languages."""
    pages = [_page(UNIVERSITY_HOST + "/", "home", lang="ja"),
             _page(UNIVERSITY_HOST + "/contact/mail.php?dir=a", "contact", lang="ja"),
             _page(UNIVERSITY_HOST + "/admissions/form.php", "contact", lang="ja")]
    brand = {"name": "藤城大学",
             "authoritative_variants": ["藤城大学", "Ferncastle University"],
             "authoritative_sources": ["og:site_name", "og:site_name-off-home"],
             "declared_on": {"藤城大学": [pages[0]["url"]],
                             "Ferncastle University": [pages[1]["url"], pages[2]["url"]]},
             "alternate_names": [], "source": "og:site_name", "pages_seen": 3}
    result = SkillResult("fact-extractability-audit")
    FACTS._check_naming_consistency(result, {"pages": pages}, pages, brand)
    assert not result.findings, [f["evidence"] for f in result.findings]


def test_two_latin_spellings_still_disagree():
    """The guard: two different names in one script are still reported."""
    pages = [_page(UNIVERSITY_HOST + "/", "home"), _page(UNIVERSITY_HOST + "/about", "about")]
    brand = {"name": "Ferncastle University",
             "authoritative_variants": ["Ferncastle University", "Brookline College"],
             "authoritative_sources": ["og:site_name", "jsonld:Organization"],
             "declared_on": {"Ferncastle University": [pages[0]["url"]],
                             "Brookline College": [pages[1]["url"]]},
             "alternate_names": [], "source": "og:site_name", "pages_seen": 2}
    result = SkillResult("fact-extractability-audit")
    FACTS._check_naming_consistency(result, {"pages": pages}, pages, brand)
    assert [f for f in result.findings if f["id_hint"] == "brand-name-written-inconsistently"]


# --------------------------------------------------------------------------
# 7 and 8. Dates
# --------------------------------------------------------------------------

def test_a_figure_stamped_with_its_measurement_day_is_not_stale_copy():
    page = _page(PROJECT_HOST + "/footprint.html",
                 body_text="As of 2023-07-04, the size of the Quillmark library is generally "
                           "less than 1 megabyte.")
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_stale_year_references(result, [page], AUDIT_DAY, "Quillmark")
    assert not result.findings, [f["evidence"] for f in result.findings]


def test_prices_as_of_an_old_year_are_still_stale_copy():
    page = _page(PROJECT_HOST + "/pricing.html",
                 body_text="Prices as of 2022 are listed below for every plan we sell.")
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_stale_year_references(result, [page], AUDIT_DAY, "Quillmark")
    assert result.findings


def _article(path, visible):
    return _page(PROJECT_HOST + path, "article", title="Notes " + path,
                 body_text="A long note about the language and how it is used. " * 20,
                 dates={"has_any": bool(visible), "visible": list(visible),
                        "machine_readable": [], "copyright_years": [], "as_of_years": [],
                        "jsonld_date_published": "", "jsonld_date_modified": ""})


def test_a_date_the_header_prints_on_every_page_dates_none_of_them():
    """A header clock put today's date on every page, and every article with
    no date of its own counted as dated because a date appeared on it."""
    clock = "2026-09-11"
    pages = [_article("/notes/%d.html" % n, [clock]) for n in range(1, 5)]
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_date_signals(result, pages)
    finding = next(f for f in result.findings if f["id_hint"] == "content-pages-carry-no-date")
    assert len(finding["affected_pages"]) == 4


def test_an_article_with_its_own_date_beside_the_clock_is_dated():
    clock = "2026-09-11"
    pages = [_article("/notes/%d.html" % n, [clock]) for n in range(1, 5)]
    pages.append(_article("/notes/5.html", [clock, "2025-03-02"]))
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_date_signals(result, pages)
    finding = next(f for f in result.findings if f["id_hint"] == "content-pages-carry-no-date")
    assert PROJECT_HOST + "/notes/5.html" not in finding["affected_pages"]


def test_an_account_in_the_header_of_a_download_page_is_counted():
    """A project's own repository account is linked from the header of its
    download page, which the content filter sets aside, and the report led
    with "No off-site profile is linked"."""
    account = "https://github.com/quillmark"
    home = _page(PROJECT_HOST + "/", "home", title="Quillmark",
                 body_text="Quillmark is a small scripting language made at a university. " * 20)
    download = _page(PROJECT_HOST + "/download.html", title="Quillmark: download",
                     body_text="", social_profiles={"GitHub": account},
                     links={"internal": [], "external": [{"url": account, "text": "GitHub"}],
                            "nav": [{"url": account, "text": "GitHub"}], "footer": []})
    snapshot = _snapshot([home, download], PROJECT_HOST, "Quillmark")
    result = FRESHNESS.run(snapshot, now=AUDIT_DAY, allow_network=False)
    assert "GitHub" in result.signals["profile_platforms"]


def test_a_page_describing_an_app_is_not_a_dated_page():
    korean = _page("https://www.ferncastle-museum.test/contents/M0102010400.do",
                   title="Ferncastle Museum>Visit>Guides>전시 해설 안내>전시안내앱",
                   headings={"h2": ["전시 해설 안내"], "h3": ["전시안내앱"]})
    english = _page("https://www.ferncastle-museum.test/visit/guide",
                    title="Visit | Mobile app", headings={"h1": ["Mobile app"]})
    assert FRESHNESS._a_page_for_doing_something_else(korean)
    assert FRESHNESS._a_page_for_doing_something_else(english)


# --------------------------------------------------------------------------
# 9 and 10. One page at two addresses, and two pages at one path
# --------------------------------------------------------------------------

def test_a_directory_and_its_index_file_are_one_page():
    """`/` and `/home.html` served the same 129 characters under one title
    with no canonical, and the appendix said no two addresses delivered the
    same main text."""
    menu = "The Programming Language Quillmark about news get started download documentation"
    pages = [_page(PROJECT_HOST + "/", "home", title="Quillmark", body_text=menu),
             _page(PROJECT_HOST + "/home.html", title="Quillmark", body_text=menu),
             _page(PROJECT_HOST + "/about.html", "about", title="About",
                   body_text="Quillmark is a small scripting language made at a university.")]
    result = SkillResult("structured-data-audit")
    SD._check_canonical_declaration(result, pages)
    assert [f for f in result.findings
            if f["id_hint"] == "the-same-page-at-several-addresses-with-no-canonical"]


_FORM = ("Contact form. Notes before you write. Please read the frequently asked questions "
         "first. A reply can take some time. Urgent matters should go to the office by "
         "telephone. Name Email Subject Message (up to 3000 characters). Your message is "
         "sent over an encrypted connection.")


def test_a_parameter_that_picks_the_office_is_not_a_filter():
    """A mail form's `dir` names the office the message goes to: one address
    heads its form with the institute's name. Pointing all of them at the bare
    address would tell every crawler the seven offices' forms are one."""
    pages = []
    for index, office in enumerate(("h-0805", "h-0813", "h-0901", "z-0106", "z-0204",
                                    "z-0210", "z-0531")):
        pages.append(_page(UNIVERSITY_HOST + "/contact/mail.php?dir=" + office, "contact",
                           title="へのお問い合わせ内容 | 藤城大学",
                           headings={"h1": ["へのお問い合わせ内容"]}, body_text=_FORM))
    pages.append(_page(UNIVERSITY_HOST + "/contact/mail.php?dir=z-0205", "contact",
                       title="生産技術研究所へのお問い合わせ内容 | 藤城大学",
                       headings={"h1": ["生産技術研究所へのお問い合わせ内容"]},
                       body_text=_FORM.replace("Contact form.", "Institute contact form.")))
    result = SkillResult("structured-data-audit")
    SD._check_canonical_declaration(result, pages)
    assert not result.findings, [f["title"] for f in result.findings]


# --------------------------------------------------------------------------
# Shops: editions, references, identity values and working labels
# --------------------------------------------------------------------------

SHOP = "https://www.kestrelwood.test"


def test_regional_editions_of_one_page_do_not_share_a_description():
    """`/au/our-story`, `/ca/our-story` and `/sg/our-story` are one page in three
    editions. Within any one region the descriptions differ."""
    story = "Furniture shopping was always about filling a room with expensive trends."
    pages = [_page(SHOP + "/%s/our-story" % region, "about", title="Our story",
                   meta_description=story) for region in ("au", "ca", "sg")]
    pages += [_page(SHOP + "/au", "home", title="Kestrelwood Australia",
                    meta_description="Modern furniture for Australian homes, delivered."),
              _page(SHOP + "/ca", "home", title="Kestrelwood Canada",
                    meta_description="Modern furniture for Canadian homes, delivered.")]
    result = SkillResult("structured-data-audit")
    SD._check_titles_and_descriptions(result, pages)
    assert "share a meta description" not in _everything(result)
    assert "share a <title>" not in _everything(result)


def test_a_brand_written_as_a_reference_is_the_name_it_points_at():
    product = _page(SHOP + "/products/oak-chair", "product", jsonld=[
        {"@id": SHOP + "/products/oak-chair#product", "@type": "Product", "name": "Oak chair",
         "brand": {"@id": SHOP + "/#brand"}},
        {"@id": SHOP + "/#brand", "@type": "Brand", "name": "Kestrelwood"},
    ], jsonld_types=["Product", "Brand"])
    names = {name for name, _url in SD._brand_names_in_markup([product])}
    assert names == {"Kestrelwood"}


def test_an_articles_publisher_is_not_the_sites_identity_block():
    """Two blog posts name the shop as their publisher and nothing else on the
    site declares who it is. The stub was graded as the site's Organization."""
    post = _page(SHOP + "/blog/oak-care", "article", jsonld=[
        {"@type": "Article", "headline": "Caring for oak"},
        {"@type": "Organization", "name": "Kestrelwood", "_nested_in": "publisher"},
    ], jsonld_types=["Article", "Organization"])
    home = _page(SHOP + "/", "home", title="Kestrelwood")
    pages = [home, post]
    by_type = {"home": [home], "article": [post]}
    result = SkillResult("structured-data-audit")
    SD._check_organization(result, _snapshot(pages, SHOP, "Kestrelwood"), pages, by_type,
                           {"name": "Kestrelwood"})
    ids = {f["id_hint"] for f in result.findings}
    assert "no-organization-schema" in ids
    assert "organization-schema-missing-properties" not in ids
    assert "homepage-declares-no-organization-schema" not in ids


def test_a_founder_named_in_the_markup_is_not_a_second_name_for_the_site():
    home = _page(SHOP + "/", "home", og={"og:site_name": "Kestrelwood"}, jsonld=[
        {"@type": "Organization", "name": "Kestrelwood",
         "founder": {"@type": "Organization", "name": "Rural Crafts Trust"}},
        {"@type": "Organization", "name": "Rural Crafts Trust", "_nested_in": "founder"},
    ])
    pages = [home]
    brand = {"name": "Kestrelwood",
             "authoritative_variants": ["Kestrelwood", "Rural Crafts Trust"],
             "authoritative_sources": ["jsonld:Organization", "og:site_name"],
             "declared_on": {"Kestrelwood": [home["url"]], "Rural Crafts Trust": [home["url"]]},
             "alternate_names": [], "source": "jsonld:Organization", "pages_seen": 1}
    result = SkillResult("fact-extractability-audit")
    FACTS._check_naming_consistency(result, {"pages": pages}, pages, brand)
    assert not result.findings, [f["evidence"] for f in result.findings]


def test_a_search_action_on_the_store_node_or_a_regional_homepage_is_declared():
    action = {"@type": "SearchAction", "query-input": "required name=search_term_string",
              "target": SHOP + "/search?q={search_term_string}"}
    store = _page(SHOP + "/", "home", jsonld=[
        {"@type": "OnlineStore", "name": "Kestrelwood", "potentialAction": action}],
        jsonld_types=["OnlineStore", "SearchAction"], has_search=True)
    result = SkillResult("structured-data-audit")
    SD._check_website_searchaction(result, {"home": [store]})
    assert not result.findings, [f["evidence"] for f in result.findings]

    picker = _page(SHOP + "/", "home", has_search=True)
    regional = _page(SHOP + "/au", "home", jsonld=[
        {"@type": "WebSite", "potentialAction": action}], jsonld_types=["WebSite", "SearchAction"])
    result = SkillResult("structured-data-audit")
    SD._check_website_searchaction(result, {"home": [picker, regional]})
    assert not result.findings, [f["evidence"] for f in result.findings]


def test_a_page_address_is_never_a_logo_and_the_site_is_never_its_own_same_as():
    assert SD._image_url(SHOP + "/intl") == ""
    assert SD._image_url(SHOP + "/cdn/shop/files/logo.png?v=2") == SD._image_url(
        SHOP + "/cdn/shop/files/logo.png?v=2") != ""
    assert SD._logo_is_a_page({"logo": SHOP + "/intl"}) == SHOP + "/intl"
    home = _page(SHOP + "/", "home")
    same_as = SD._all_same_as([home], {"name": "Kestrelwood", "host": "www.kestrelwood.test"},
                              declared=[SHOP + "/intl",
                                        "https://apply.kestrelwood-jobs.test/kestrelwood",
                                        "https://www.facebook.com/kestrelwood/"])
    assert same_as == ["https://www.facebook.com/kestrelwood/"]


def test_no_paste_ready_faq_block_where_no_answer_could_be_read():
    unread = {"mainEntity": [
        {"@type": "Question", "name": "Are the wallpapers waterproof?",
         "acceptedAnswer": {"@type": "Answer", "text": "<the answer, verbatim from the page>"}}]}
    read = {"mainEntity": [
        {"@type": "Question", "name": "Are the wallpapers waterproof?",
         "acceptedAnswer": {"@type": "Answer", "text": "Yes, every one of them is."}}]}
    assert SD._faq_answers_unread(unread)
    assert not SD._faq_answers_unread(read)


def test_a_brand_story_page_is_not_an_undated_article():
    story = _article("/my/global/story", [])
    story["url"] = story["final_url"] = SHOP + "/my/global/story"
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_date_signals(result, [story])
    assert not result.findings, [f["evidence"] for f in result.findings]


def test_a_working_label_is_not_a_title():
    """"App LP - 2023" was published as a landing page's title and og:title."""
    assert SD._title_is_a_working_label("App LP - 2023")
    assert SD._title_is_a_working_label("Copy of Homepage")
    assert not SD._title_is_a_working_label("Draft Beer Menu")
    assert not SD._title_is_a_working_label("Test Kitchen Recipes")
    assert not SD._title_is_a_working_label("Our story | Kestrelwood")


def test_sort_parameters_on_one_listing_are_still_twins():
    """The guard: a parameter that changes nothing the page says about itself
    is a filter, and its addresses are still one page."""
    pages = [_page(PROJECT_HOST + "/shop?sort=" + order, "category", title="All goods")
             for order in ("price", "name", "new")]
    result = SkillResult("structured-data-audit")
    SD._check_canonical_declaration(result, pages)
    assert [f for f in result.findings
            if f["id_hint"] == "filter-parameters-produce-pages-with-no-canonical"]
