# -*- coding: utf-8 -*-
"""Names, languages, addresses, missing pages, capped sitemaps and page counts.

Each block below is one rule, written against invented hosts and brands:

  an account is the site's name, not a slug that contains it
  a tie between two declared languages goes to the homepage, not the alphabet
  a Korean, Japanese or Chinese street is read in its own order
  a missing-page notice is recognised in the languages the audit names
  a sitemap read to the audit's own cap is a floor, not a total
  a self-described public institution is a public body
  a three-letter language code in the path is an edition
  a skipped non-HTML response is not a page
"""

from __future__ import annotations

import pytest

from conftest import SCRIPTS  # noqa: F401  (puts the scripts on sys.path)

import audit_common as AC  # noqa: E402
from page_extract import (  # noqa: E402
    _off_site_profiles, _social_profiles, _street_hint, extract_page)


def _external(*urls):
    return [{"url": url, "text": ""} for url in urls]


def _profiles(base, *urls):
    links = _external(*urls)
    soup = AC.make_soup(u"<p>x</p>")
    found = {e["url"]: e["attributed"] for e in _off_site_profiles(links, [], soup, base)}
    return found, _social_profiles(links, [], soup, base)


# --------------------------------------------------------------------------
# An account is the site's name, not a slug that contains it
# --------------------------------------------------------------------------

DOCS = "https://www.widgetdb.test/"


def test_a_package_of_a_different_name_is_not_the_sites_account():
    found, counted = _profiles(DOCS, "https://registry.test/package/widgetdb-wasm-http")
    assert found == {"https://registry.test/package/widgetdb-wasm-http": False}
    assert counted == {}


def test_an_article_slug_mentioning_the_site_is_not_an_account():
    slug = "https://blog.test/an-unscientific-benchmark-of-widgetdb-vs-the-file-system/"
    found, counted = _profiles(DOCS, slug)
    assert not found.get(slug)
    assert counted == {}


@pytest.mark.parametrize("url", [
    "https://github.com/widgetdb",
    "https://x.com/widgetdb_hq",
    "https://x.com/WidgetDBOfficial",
    "https://instagram.com/widgetdb.jp",
])
def test_the_name_alone_or_with_a_short_affix_is_still_the_site(url):
    found, _counted = _profiles(DOCS, url)
    assert found[url] is True


def test_a_hosted_docs_subdomain_named_after_the_site_still_counts():
    url = "https://widgetdb.docs-host.test/en/stable/"
    found, _counted = _profiles(DOCS, url)
    assert found[url] is True


def test_another_organisations_site_under_a_country_registry_is_not_a_profile():
    base = "https://www.relic.go.test/"
    found, counted = _profiles(base, "http://www.relic.or.test/",
                               "http://www.relic.or.test/english/")
    assert not any(found.values())
    assert counted == {}


def test_a_programme_page_on_a_brand_subdomain_is_not_a_profile():
    base = "https://www.shopname.test/"
    url = "https://shopname.influence.test/join/summer-campaign"
    found, counted = _profiles(base, url)
    assert not found.get(url)
    assert url not in counted.values()


def test_a_package_and_an_archive_opening_with_the_name_are_not_a_family():
    found, counted = _profiles(
        DOCS, "https://registry.test/package/widgetdb-wasm-http",
        "https://lists.test/widgetdb-users/")
    assert not any(found.values())
    assert counted == {}


# --------------------------------------------------------------------------
# A tie between declared languages goes to the front door
# --------------------------------------------------------------------------

ESSAY = "https://essay.test"
ALTERNATES = [{"hreflang": "x-default", "url": ESSAY + "/"},
              {"hreflang": "en", "url": ESSAY + "/en"},
              {"hreflang": "pt", "url": ESSAY + "/pt"}]


def _page(path, lang, **extra):
    page = {"url": ESSAY + path, "final_url": ESSAY + path, "status": 200,
            "lang": lang, "hreflang": ALTERNATES,
            "body_text": "Say what you need in the first message, not hello."}
    page.update(extra)
    return page


def test_a_tie_goes_to_the_homepages_language_not_the_alphabet():
    pages = [_page("/", "en"), _page("/en", "en"), _page("/pt", "pt"),
             _page("/pt-br", "pt-BR")]
    record = AC.detect_site_language(pages)
    assert record["code"] == "en"
    assert record["prose_checks_apply"] is True


def test_without_a_root_page_the_x_default_edition_decides():
    alternates = [{"hreflang": "x-default", "url": ESSAY + "/en"}]
    pages = [_page("/pt", "pt", hreflang=alternates), _page("/pt-br", "pt", hreflang=alternates),
             _page("/en", "en", hreflang=alternates), _page("/en/more", "en", hreflang=alternates)]
    assert AC.detect_site_language(pages)["code"] == "en"


def test_a_clear_majority_still_wins_over_the_homepage():
    pages = [_page("/", "en"), _page("/pt", "pt"), _page("/pt/a", "pt")]
    assert AC.detect_site_language(pages)["code"] == "pt"


# --------------------------------------------------------------------------
# Streets written largest unit first
# --------------------------------------------------------------------------

KR_FOOTER = (u"<footer><p>06000 서울시 강남구 가상로 12(가상동 1-2) "
             u"대표전화 02-1234-5678</p></footer>")
JP_FOOTER = u"<footer><p>本館 〒100-0000 架空区見本町1-2-3 03-1234-5678（代表）</p></footer>"
JP_WITH_PREFECTURE = u"<footer><p>〒600-0000 京都府架空郡見本町見本台8-1-3</p></footer>"
CN_FOOTER = u"<footer><p>地址：北京市架空区示例路12号 邮编 100000</p></footer>"


def _contact(body, lang="ko"):
    html = (u'<html lang="{}"><head><title>Home</title></head><body><main><h1>Home</h1>'
            u"<p>Welcome.</p></main>{}</body></html>").format(lang, body)
    url = "https://www.relic.go.test/"
    record = extract_page(url, url, 200, {}, html, [], 0, 0, "seed", url)
    return record["contact_facts"]


def test_a_korean_road_address_in_the_footer_is_an_address():
    facts = _contact(KR_FOOTER)
    assert facts["street_hint"].startswith(u"서울시 강남구 가상로 12")
    assert facts["postcode_hint"] == "06000"
    assert facts["has_address"] is True


def test_a_postmarked_japanese_address_without_its_prefecture_is_an_address():
    facts = _contact(JP_FOOTER, "ja")
    assert facts["street_hint"] == u"架空区見本町1-2-3"
    assert facts["has_address"] is True


def test_a_japanese_address_with_hyphenated_block_numbers_is_an_address():
    facts = _contact(JP_WITH_PREFECTURE, "ja")
    assert facts["street_hint"] == u"京都府架空郡見本町見本台8-1-3"
    assert facts["has_address"] is True


def test_a_chinese_road_address_is_an_address():
    facts = _contact(CN_FOOTER, "zh")
    assert facts["street_hint"] == u"北京市架空区示例路12号"
    assert facts["has_address"] is True


@pytest.mark.parametrize("text", [
    # 로 is also a particle; a count, a date or a sum after it is not a number
    # on a building.
    u"서울시 강남구 주민들로 3명이 참여했다",
    u"서울시 강남구 기준으로 2024년 조사",
    u"서울시 강남구 예산으로 500원",
    # A road and a number with no city or district in front of them.
    u"가상로 12 번지에서",
    # A Chinese city and road with no 号 closing the number.
    u"北京市架空区示例路12",
])
def test_the_guards_still_hold(text):
    assert _street_hint(text) is None


# --------------------------------------------------------------------------
# A missing-page notice, in the languages this audit names
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    u"페이지를 찾을 수 없습니다", u"ページが見つかりません", u"页面不存在", u"ไม่พบหน้าที่คุณต้องการ",
    u"الصفحة غير موجودة", u"صفحه مورد نظر یافت نشد", u"הדף לא נמצא",
    u"Страница не найдена", u"Seite nicht gefunden", u"Page introuvable",
    u"Página no encontrada", u"Página não encontrada", u"Pagina non trovata",
    u"Pagina niet gevonden", u"Halaman tidak ditemukan", u"Không tìm thấy trang",
])
def test_a_title_saying_the_page_is_missing_is_read_in_its_own_language(title):
    assert AC.looks_like_soft_404({"title": title, "headings": {"h1": []}})


def test_a_short_notice_with_no_heading_is_read_from_its_body():
    page = {"title": u"알림", "headings": {"h1": []},
            "body_text": u"웹 페이지를 찾을 수 없습니다. 이전 페이지"}
    assert AC.looks_like_soft_404(page)


def test_a_long_page_mentioning_a_missing_page_is_not_one():
    body = (u"Das Handbuch erklärt, was die Meldung Seite nicht gefunden bedeutet. "
            + u"Weitere Absätze über Server und Konfiguration. " * 30)
    assert not AC.looks_like_soft_404({"title": u"Handbuch", "headings": {"h1": [u"Handbuch"]},
                                       "body_text": body})


def test_an_ordinary_korean_page_is_not_a_missing_page():
    assert not AC.looks_like_soft_404({"title": u"전시 안내", "headings": {"h1": [u"전시"]},
                                       "body_text": u"현재 전시를 안내합니다."})


# --------------------------------------------------------------------------
# A sitemap read to the audit's own cap
# --------------------------------------------------------------------------

LIBRARY = "https://www.library.test"


def _sitemaps(url_count, lastmods):
    return [{"url": LIBRARY + "/sitemap.xml", "status": 200, "is_index": True,
             "child_sitemaps": [LIBRARY + "/sitemaps/0.xml.gz"], "url_count": 0,
             "lastmod_count": 0},
            {"url": LIBRARY + "/sitemaps/0.xml.gz", "status": 200, "child_sitemaps": [],
             "url_count": url_count, "lastmod_count": lastmods}]


def test_a_file_read_to_the_cap_is_a_floor_and_says_what_shares_are_of():
    scope = AC.sitemap_scope(_sitemaps(AC.SITEMAP_URLS_READ_PER_FILE, 47))
    assert scope["complete"] is False
    phrase = AC.sitemap_total_phrase(scope, "URLs")
    assert phrase.startswith("at least 5,000 URLs")
    assert "this audit read the first 5,000" in phrase
    assert "share" in phrase


def test_a_file_that_ended_on_its_own_is_the_total():
    scope = AC.sitemap_scope(_sitemaps(1234, 12))
    assert scope["complete"] is True
    assert AC.sitemap_total_phrase(scope) == "1,234 entries"


# --------------------------------------------------------------------------
# A public institution saying what it is
# --------------------------------------------------------------------------

def _bank_snapshot(brand, body):
    origin = "https://www.centralbank.or.test"
    page = {"url": origin + "/en/about-us.html", "final_url": origin + "/en/about-us.html",
            "status": 200, "page_type": "about", "lang": "en", "title": "About us",
            "headings": {"h1": ["About us"]}, "body_text": body, "text": body,
            "links": {"internal": [], "external": []}, "jsonld": [], "jsonld_types": []}
    home = dict(page, url=origin + "/", final_url=origin + "/", page_type="home",
                body_text="Welcome.", text="Welcome.")
    return {"origin": origin, "brand": {"name": brand}, "pages": [home, page]}


def test_a_self_described_public_institution_is_a_public_body():
    kind = AC.site_kind(_bank_snapshot(
        "Bank of Examplia",
        "About the Bank. The Bank of Examplia is a public institution with a mandate to "
        "foster a stable and inclusive financial environment."))
    assert kind.kind == AC.PUBLIC_BODY
    assert kind.determined


@pytest.mark.parametrize("brand,body", [
    # The sentence is about somebody else.
    ("Acme Lending", "Acme Lending is a lender. The Bank of Examplia is the regulator of "
                     "every bank in the country."),
    # The noun is a product.
    ("Acme Valves", "Acme Valves is a pressure regulator manufacturer based in Examplia."),
])
def test_a_regulator_named_by_somebody_else_or_sold_is_not_a_public_body(brand, body):
    assert AC.site_kind(_bank_snapshot(brand, body)).kind != AC.PUBLIC_BODY


# --------------------------------------------------------------------------
# Three-letter language codes in the path
# --------------------------------------------------------------------------

MUSEUM = "https://www.relic.go.test"


def _museum_page(path, lang):
    return {"url": MUSEUM + path, "status": 200, "lang": lang}


def test_uppercase_three_letter_codes_are_editions():
    pages = [_museum_page("/ENG/main/index.do", "en"), _museum_page("/JPN/main/index.do", "ja"),
             _museum_page("/CHN/main/index.do", "zh"),
             _museum_page("/RELIC/contents/M0102.do", "ko")]
    editions = AC.locale_editions(pages)
    assert editions == {MUSEUM + "/ENG/main/index.do": "eng",
                        MUSEUM + "/JPN/main/index.do": "jpn",
                        MUSEUM + "/CHN/main/index.do": "chn"}


def test_ordinary_three_letter_sections_are_not_editions():
    pages = [_museum_page("/api/v1", "en"), _museum_page("/faq/", "en"),
             _museum_page("/spa/treatments", "en"), _museum_page("/app/", "en")]
    assert AC.locale_editions(pages) == {}


def test_a_three_letter_edition_root_is_a_homepage():
    assert AC.detect_page_type(MUSEUM + "/eng", {"lang": "en"}) == "home"


# --------------------------------------------------------------------------
# A skipped non-HTML response is not a page
# --------------------------------------------------------------------------

def test_skipped_non_html_records_are_not_counted_as_pages():
    pages = [{"url": MUSEUM + "/", "status": 200, "page_type": "home", "lang": "ko"},
             {"url": MUSEUM + "/a", "status": 200, "page_type": "other", "lang": "ko"},
             {"url": MUSEUM + "/eng/guide.pdf", "status": 200, "skipped": "non-HTML content type"},
             {"url": MUSEUM + "/jpn/map.pdf", "status": 200, "skipped": "non-HTML content type"},
             {"url": MUSEUM + "/b.jpg", "status": 200, "skipped": "non-HTML content type"}]
    snapshot = {"origin": MUSEUM, "pages": pages}
    assert AC.pages_the_crawl_read(snapshot) == 2
    # Two PDFs under language-shaped paths are not two editions.
    assert AC.locale_editions(pages) == {}
    note = AC.unclassified_note(pages)
    assert "1 of the 2 crawled pages" in note


# --------------------------------------------------------------------------
# A definition says what the brand is, under the name it uses
# --------------------------------------------------------------------------

def test_a_country_after_the_declared_name_comes_off():
    assert "Harbour Loom" in AC.brand_forms("Harbour Loom Singapore", None)
    assert AC.defining_sentence(
        "Harbour Loom is a multidisciplinary design studio based in Singapore.",
        "Harbour Loom Singapore") == (
        "Harbour Loom is a multidisciplinary design studio based in Singapore")


@pytest.mark.parametrize("declared,token", [
    ("Harbour Trust of Singapore", ""),   # the place is part of the name
    ("Sky Singapore", "skysg"),           # one word left, and the domain spells more
    ("Talk To Us", ""),                   # a pronoun, not a country code
])
def test_a_place_that_is_part_of_the_name_stays(declared, token):
    assert AC.name_forms(declared, token) == [declared]


@pytest.mark.parametrize("sentence", [
    "Harbour Loom makes its debut in the country's capital with the opening of the "
    "Harbour Loom Design House in a shopping centre, a milestone for the brand.",
    "Harbour Loom makes a splash at this year's design week with a new collection.",
    "Harbour Loom is not affiliated with any marketplace, neither do we partner with "
    "online distributors.",
    "Harbour Loom is opening its first store in the capital next month.",
])
def test_news_and_negatives_are_not_definitions(sentence):
    assert AC.defining_sentence(sentence, "Harbour Loom") == ""


@pytest.mark.parametrize("text", [
    "I am an international customer, can I order a lamp from abroad? Yes, we ship worldwide.",
    "I am an international customer. What are the payment methods available to me? You "
    "may pay by card.",
])
def test_a_question_in_the_customers_voice_is_not_a_definition(text):
    assert AC.defining_sentence(text, "Harbour Loom") == ""


def test_a_first_person_definition_followed_by_a_statement_still_defines():
    assert AC.defining_sentence("We are a furniture workshop on the market square. Every "
                                "table is made to order.", "Harbour Loom").startswith(
        "We are a furniture workshop")


def test_a_defining_verb_with_an_ordinary_object_still_defines():
    assert AC.defining_sentence("Harbour Loom provides a way to publish a catalogue "
                                "without a developer.", "Harbour Loom")


def test_a_question_heading_run_into_the_sentence_still_defines():
    text = (u"Shop Customised Home Decor Items Online Who We Are? Loomcraft is a lifestyle "
            u"brand in home decor solutions that believes a home is more than walls.")
    assert AC.defining_sentence(text, u"Loomcraft®", {"domain_token": "loomcraft"}).startswith(
        u"Loomcraft is a lifestyle brand")


def test_campaign_copy_addressed_to_the_reader_is_not_a_self_description():
    assert AC.addresses_the_reader("Your home should feel like an extension of you.")
    assert AC.addresses_the_reader(u"“You deserve better sleep.”")
    assert not AC.addresses_the_reader("Harbour Loom is a furniture brand for small homes.")


# --------------------------------------------------------------------------
# One homepage, and the regional storefronts behind a picker
# --------------------------------------------------------------------------

STORE = "https://www.sofa-maker.test"
REGIONS = (("au", "en-AU", "Up to $600 off"), ("ca", "en-CA", "Shop the look"),
           ("sg", "en-SG", "Deeper nights"), ("uk", "en-GB", "Up to 50% off"),
           ("us", "en-US", "Shop the look"))


def _store_page(path, lang, h1="", body="", page_type="home", links=()):
    return {"url": STORE + path, "final_url": STORE + path, "status": 200, "lang": lang,
            "page_type": page_type, "headings": {"h1": [h1] if h1 else [""]},
            "body_text": body, "body_text_len": len(body),
            "links": {"internal": [{"url": STORE + link, "text": ""} for link in links]}}


def _store_snapshot(root_body="Choose your country.", root_h1=""):
    pages = [_store_page("/", "en-US", root_h1, root_body,
                         links=["/" + code for code, _lang, _h1 in REGIONS])]
    for code, lang, h1 in REGIONS:
        pages.append(_store_page("/" + code, lang, h1, "Sofas and tables. " * 40))
        pages.append(_store_page("/" + code + "/sofas", lang, "Sofas", "Sofas. " * 40,
                                 page_type="category"))
    return {"origin": STORE, "pages": pages}


def test_a_country_picker_at_the_root_is_named_as_a_gateway():
    home = AC.site_homepage(_store_snapshot())
    assert home["url"] == STORE + "/"
    assert home["is_gateway"] is True
    assert home["regional_homes"] == sorted(STORE + "/" + code for code, _l, _h in REGIONS)


def test_a_full_homepage_with_a_country_switcher_is_still_the_homepage():
    home = AC.site_homepage(_store_snapshot(root_body="Sofas and tables. " * 60,
                                            root_h1="Furniture that gets better with living"))
    assert home["url"] == STORE + "/"
    assert home["is_gateway"] is False
    assert home["regional_homes"] == []


def test_us_and_uk_storefronts_are_editions_beside_other_countries():
    editions = AC.locale_editions(_store_snapshot()["pages"])
    assert editions[STORE + "/us"] == "us" and editions[STORE + "/uk/sofas"] == "uk"
    assert editions[STORE + "/au"] == "au"


def test_us_and_uk_alone_are_still_sections():
    pages = [_store_page("/us", "en-US"), _store_page("/uk", "en-GB")]
    assert AC.locale_editions(pages) == {}


def test_market_paths_with_a_language_are_editions():
    pages = [_store_page("/en-au", "en-AU"), _store_page("/en-ca/sofas", "en-CA"),
             _store_page("/en-us", "en-US")]
    assert set(AC.locale_editions(pages).values()) == {"en-au", "en-ca", "en-us"}


# --------------------------------------------------------------------------
# A beacon is not a picture
# --------------------------------------------------------------------------

def _images(body):
    html = (u'<html lang="en"><head><title>Shop</title></head><body><main><h1>Shop</h1>'
            u"{}</main></body></html>").format(body)
    url = "https://www.roaster.test/"
    return extract_page(url, url, 200, {}, html, [], 0, 0, "seed", url)["images"]


@pytest.mark.parametrize("tag", [
    u'<img src="https://ads.test/cde/eventTracking.htm?pixelId=16404&_w=1">',
    u'<img src="https://ads.test/p.gif?pixel_id=7">',
    u'<img src="/b/beacon.gif" style="width: 1px; height: 1px">',
])
def test_a_tracking_beacon_is_not_an_image_missing_alt_text(tag):
    images = _images(tag + u'<img src="/photos/beans.jpg" alt="Beans">')
    assert images["missing_alt_count"] == 0


def test_a_real_picture_without_alt_text_still_counts():
    images = _images(u'<img src="/photos/event-poster.jpg"><img src="/photos/track-bike.png">')
    assert images["missing_alt_count"] == 2


def test_the_undescribed_count_is_never_negative():
    images = _images(u'<img src="/i/divider.png" alt="" role="presentation">'
                     u'<img src="/i/rule.png" alt="" title="A rule">'
                     u'<img src="/photos/beans.jpg">')
    assert images["undescribed_count"] == 1
