"""Kinds of site, writing systems, dates in the template and what a page is.

Each section names the wrong answer a real report gave, in invented terms:

1. **An institution is not a person.** A national university under an academic
   suffix was advised as one researcher - "what you work on, where you are
   based", "your researcher identifier".
2. **One document is not a company.** A one-page essay and its translations
   was told to claim company profiles, state a founding year and a street
   address.
3. **Kanji and kana are one page's writing.** Japanese pages declaring
   `lang="ja"` were reported "written in the latin script", because han and
   kana were each outnumbered by Latin brand names.
4. **A project's own repository account is its profile.** The extractor half:
   `github.com/<name>/<name>` linked from a download page is attributed.
5. **Joining is not paying.** A museum's membership sign-up pages, with forms
   and no figure, were typed `pricing`.
6. **A clock in the footer is not a publication date.** A footer copyright
   line and a "last updated" stamp that moves on every load dated every page.

And from the storefront pass:

A1. A retailer with product pages, a basket and showrooms is an online seller.
A2. "Founded in <city> in <year>, <Brand> creates <things>" is a definition.
A3. UTF-8 read as a single-byte encoding is decoded as UTF-8.
A4. A store app's wishlist route is a visitor's private area.
A5. One FAQ block on a landing page does not make it a FAQ page.

The two-letter `.zz` label stands in for a country code: the registry rule reads
a role label only in front of one, and `.test` has four letters.
"""

from __future__ import annotations

import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION, PERSONAL_OR_ACADEMIC, UNDETERMINED,
    defining_sentence, detect_page_type, detect_site_language, dominant_script,
    is_forbidden_path, response_text, site_kind,
)
from page_extract import extract_page  # noqa: E402


def _page(url, page_type="other", text="", **extra):
    page = {"url": url, "status": 200, "page_type": page_type, "body_text": text,
            "jsonld_types": [], "links": {"internal": [], "external": []}}
    page.update(extra)
    return page


def _snapshot(origin, pages, brand=None):
    return {"origin": origin, "pages": list(pages), "brand": brand or {}}


def _extract(url, html):
    origin = "/".join(url.split("/")[:3])
    return extract_page(url, url, 200, {"content-type": "text/html"}, html, [], 10,
                        1, "bfs", origin)


# --------------------------------------------------------------------------
# 1. An institution under an academic suffix
# --------------------------------------------------------------------------

def test_a_university_named_in_its_own_language_is_an_institution_not_a_person():
    origin = "https://www.hoshino.ac.zz"
    kind = site_kind(_snapshot(origin, [
        _page(origin + "/", "home", headings={"h1": [u"星野大学"]},
              title=u"星野大学"),
        _page(origin + "/en/", "home", headings={"h1": ["Hoshino University"]}),
    ], brand={"name": u"星野大学"}))
    assert kind.kind == ORGANISATION, kind
    assert kind.is_certainly(ORGANISATION)
    assert u"星野大学" in kind.why()
    assert "suffix reserved for education" in kind.why()


def test_a_professor_under_the_same_suffix_stays_a_personal_site():
    """Her page is titled with the university's name, which is where she works
    and not what the site is. The role word in the title vetoes the name."""
    origin = "https://jquill.ac.zz"
    kind = site_kind(_snapshot(origin, [
        _page(origin + "/", "home", headings={"h1": ["Jane Quill"]},
              title="Jane Quill | Professor of Physics, Hoshino University"),
    ], brand={"name": "Jane Quill"}))
    assert kind.kind == PERSONAL_OR_ACADEMIC, kind


def test_university_markup_counts_only_when_it_is_the_site_not_an_affiliation():
    origin = "https://www.hoshino.ac.zz"
    own = _page(origin + "/", "home",
                jsonld=[{"@type": "CollegeOrUniversity", "name": "Hoshino"}],
                jsonld_types=["CollegeOrUniversity"])
    assert site_kind(_snapshot(origin, [own])).kind == ORGANISATION

    person = _page(origin + "/", "home",
                   jsonld=[{"@type": "Person", "name": "Jane Quill"},
                           {"@type": "CollegeOrUniversity", "name": "Hoshino",
                            "_nested_in": "affiliation"}],
                   jsonld_types=["Person", "CollegeOrUniversity"])
    assert site_kind(_snapshot(origin, [person])).kind == PERSONAL_OR_ACADEMIC


def test_many_faculties_linked_from_the_site_make_it_an_institution():
    origin = "https://www.hoshino.ac.zz"
    faculties = ["Faculty of Law", "Faculty of Medicine", "Faculty of Letters",
                 "Department of Physics", "Graduate School of Engineering"]
    home = _page(origin + "/", "home", links={
        "internal": [{"url": origin + "/f{}".format(i), "text": label}
                     for i, label in enumerate(faculties)],
        "external": []})
    kind = site_kind(_snapshot(origin, [home]))
    assert kind.kind == ORGANISATION, kind
    assert "5 faculties and departments" in kind.why()


# --------------------------------------------------------------------------
# 2. One document in several languages
# --------------------------------------------------------------------------

_ESSAY = ("Do not ask whether anyone here knows the tool. Ask the question you "
          "actually have, with the error message and what you tried. ") * 12


def _essay_site(extra=()):
    origin = "https://askfirst.test"
    pages = [_page(origin + "/", "home", _ESSAY, lang="en",
                   headings={"h1": ["Ask the question"]})]
    for code in ("de", "fr", "ar", "ckb"):
        pages.append(_page("{}/{}/".format(origin, code), "other", _ESSAY,
                           lang="ku" if code == "ckb" else code))
    return _snapshot(origin, pages + list(extra), brand={"name": "Ask the question"})


def test_one_page_and_its_translations_is_a_single_document_not_a_business():
    kind = site_kind(_essay_site())
    assert kind.kind == PERSONAL_OR_ACADEMIC, kind
    assert "one document in 5 language editions" in kind.why()
    assert kind.is_certainly(PERSONAL_OR_ACADEMIC)


def test_an_about_page_or_a_contact_page_is_a_body_not_a_single_document():
    about = _page("https://askfirst.test/about", "about", "Who we are.")
    assert site_kind(_essay_site([about])).kind != PERSONAL_OR_ACADEMIC


def test_a_short_page_with_nothing_else_still_decides_nothing():
    kind = site_kind(_snapshot("https://quiet.test", [_page("https://quiet.test/", "home")]))
    assert kind.kind == UNDETERMINED


# --------------------------------------------------------------------------
# 3. Kanji and kana are one page's writing
# --------------------------------------------------------------------------

def test_han_and_kana_together_outweigh_latin_brand_names():
    text = u"漢" * 1599 + u"かな" * 961 + "Brand" * 403
    assert dominant_script(text) in ("han", "kana")


def test_a_declared_japanese_site_is_not_reported_as_latin_script():
    """Latin leads the plurality, the declared scripts hold a large share, and
    the record must name the declared script rather than contradict it."""
    sample = u"漢字" * 50 + u"かな" * 75 + "ProductCode" * 45
    pages = [{"url": "https://hoshino.test/", "status": 200, "lang": "ja",
              "body_text": sample}]
    record = detect_site_language(pages)
    assert record["code"] == "ja"
    assert record["script"] in ("kana", "han"), record
    assert record["source"] == "declared"


def test_english_quoting_one_japanese_name_is_still_latin():
    assert dominant_script("An English page about a tea called " + u"玉露" * 3
                           + " and nothing else in it at all.") == "latin"


# --------------------------------------------------------------------------
# 4. The project's own repository account, from a download page
# --------------------------------------------------------------------------

def test_a_repository_named_after_the_site_is_attributed_and_a_strangers_is_not():
    html = """<html lang="en"><head><title>Download | Quillbase</title></head><body>
    <h1>Download Quillbase</h1>
    <p>The source is mirrored at <a href="https://github.com/quillbase/quillbase">
    https://github.com/quillbase/quillbase</a>.</p>
    <p>We test with <a href="https://github.com/somebody/fuzzer">a fuzzer</a>.</p>
    </body></html>"""
    record = _extract("https://www.quillbase.test/download.html", html)
    assert record["social_profiles"].get("GitHub") == "https://github.com/quillbase"
    stranger = [p for p in record["off_site_profiles"]
                if p["url"] == "https://github.com/somebody"]
    assert not any(p["attributed"] for p in stranger)


# --------------------------------------------------------------------------
# 5. A membership sign-up is not a pricing page without a price
# --------------------------------------------------------------------------

def test_a_membership_sign_up_form_with_no_figure_is_not_pricing():
    url = "https://museum.test/visit/contents/membership.do?step=agree"
    meta = {"text": "Join the museum. Choose a membership type: adult or child. "
                    "Agree to the terms, verify your phone, enter your details.",
            "headings": {"h1": ["Join"]}, "jsonld_types": [], "title": "Join"}
    assert detect_page_type(url, meta) != "pricing"


def test_a_membership_page_that_prices_its_tiers_is_still_pricing():
    url = "https://museum.test/membership"
    meta = {"text": "Adult membership costs $60 a year. Family membership costs $95 a year.",
            "headings": {"h1": ["Membership"]}, "jsonld_types": [], "title": "Membership"}
    assert detect_page_type(url, meta) == "pricing"
    assert detect_page_type("https://shop.test/pricing",
                            dict(meta, text="Contact us for a quote.")) == "pricing"


# --------------------------------------------------------------------------
# 6. A date in the site's header, footer or copyright line
# --------------------------------------------------------------------------

_FOOTER_CLOCK = (u'<html lang="ar"><head><title>سجل الزوار</title></head><body>'
                 u'<main><h1>سجل الزوار</h1><p>اكتب رأيك في الموقع وشاركنا ملاحظاتك '
                 u'حول المقالات والكتب المنشورة فيه.</p></main>'
                 u'<footer><p>حقوق النشر محفوظة © 1448هـ / 2026م لموقع المثال '
                 u'آخر تحديث للشبكة بتاريخ: 30/3/1448هـ</p></footer></body></html>')


def test_a_footer_copyright_line_and_update_stamp_do_not_date_the_page():
    dates = _extract("https://example-network.test/guestbook", _FOOTER_CLOCK)["dates"]
    assert dates["has_any"] is False, dates
    assert dates["non_gregorian"] == []
    assert dates["chrome"]


def test_a_clock_in_the_site_header_is_chrome_without_any_copyright_mark():
    html = (u'<html lang="ar"><body><header><div>الأحد 30/3/1448هـ</div>'
            u'</header><main><h1>مقالة</h1><p>نص المقالة بلا تاريخ.</p></main></body></html>')
    dates = _extract("https://example-network.test/a", html)["dates"]
    assert dates["has_any"] is False, dates


def test_a_date_in_the_articles_own_header_is_still_the_pages_date():
    html = ('<html lang="en"><body><header class="site-header">Example Network</header>'
            '<article><header><h1>Budget notes</h1><p>Published 2026-03-12</p></header>'
            '<p>The budget was set in the spring.</p></article></body></html>')
    dates = _extract("https://example-network.test/b", html)["dates"]
    assert dates["has_any"] is True
    assert any("2026-03-12" in v for v in dates["visible"])


# --------------------------------------------------------------------------
# A1. Online seller with showrooms
# --------------------------------------------------------------------------

def test_a_retailer_with_a_basket_and_showrooms_is_an_online_seller():
    origin = "https://sofa-atlas.test"
    kind = site_kind(_snapshot(origin, [
        _page(origin + "/", "home", links={"internal": [{"url": origin + "/cart"}],
                                           "external": []}),
        _page(origin + "/products/cloud-sofa", "product",
              "Cloud sofa. $1,299. Add to cart.", jsonld_types=["Product", "Offer"]),
        _page(origin + "/showrooms", "location", "Visit our showroom at 1 Invented Lane."),
    ]))
    assert kind.kind == ONLINE_SELLER, kind
    assert "places to visit" in kind.why()


def test_a_cafe_that_sells_nothing_online_is_still_a_local_business():
    kind = site_kind(_snapshot("https://cafe.test", [
        _page("https://cafe.test/", "home"),
        _page("https://cafe.test/find-us", "location", "1 Invented Lane"),
    ]))
    assert kind.kind == LOCAL_BUSINESS


# --------------------------------------------------------------------------
# A2. A founding phrase in front of the name
# --------------------------------------------------------------------------

def test_a_participle_phrase_before_the_brand_keeps_it_the_subject():
    text = ("Founded in Lisbon in 2013, Ferndale Home creates quality, beautiful "
            "furniture for real life.")
    assert defining_sentence(text, "Ferndale Home")
    assert defining_sentence("Ferndale Home crafts solid oak tables for small kitchens.",
                             "Ferndale Home")


def test_the_news_guard_still_holds_after_a_founding_phrase():
    text = "Founded in 2013, Ferndale Home is thrilled to announce its new store."
    assert defining_sentence(text, "Ferndale Home") == ""
    assert defining_sentence("Last year, in 2013, Ferndale Home creates furniture "
                             "for real life.", "Ferndale Home") == ""


# --------------------------------------------------------------------------
# A3. UTF-8 read as a single-byte encoding
# --------------------------------------------------------------------------

class _Response:
    def __init__(self, content, content_type, apparent=None):
        self.content = content
        self.headers = {"content-type": content_type}
        self.apparent_encoding = apparent


def test_utf8_declared_as_latin1_is_decoded_as_utf8():
    body = u"<p>Our ‘Universe’ scarf – café</p>".encode("utf-8")
    text = response_text(_Response(body, "text/html; charset=iso-8859-1"))
    assert u"‘Universe’" in text
    assert u"â€" not in text
    guessed = response_text(_Response(body, "text/html", apparent="Windows-1252"))
    assert u"‘Universe’" in guessed


def test_a_page_really_in_windows_1252_is_left_as_it_is():
    body = u"<p>Café ‘menu’</p>".encode("cp1252")
    assert response_text(_Response(body, "text/html; charset=windows-1252")) == \
        u"<p>Café ‘menu’</p>"


# --------------------------------------------------------------------------
# A4. A store app's route for one visitor's state
# --------------------------------------------------------------------------

def test_a_wishlist_app_route_is_never_fetched():
    for path in ("/apps/iwish", "/apps/wishlist-plus", "/a/account-app",
                 "/en-gb/apps/reviews-widget", "/wishlist", "/pages/iwish"):
        assert is_forbidden_path("https://shop.test" + path), path
    for path in ("/apps/store-locator", "/wishlist-ideas/", "/blog/apps-we-love",
                 "/collections/accessories"):
        assert not is_forbidden_path("https://shop.test" + path), path


# --------------------------------------------------------------------------
# A5. One FAQ block on a landing page
# --------------------------------------------------------------------------

def test_a_landing_page_with_a_faq_section_is_not_a_faq_page():
    meta = {"text": "Download the app to order coffee, track deliveries and earn rewards.",
            "headings": {"h1": ["The Hillroast app"],
                         "h2": ["The Hillroast experience", "Your new go-to for coffee",
                                "Our app makes it easy to:", "Frequently asked questions",
                                "Shop online", "About us"],
                         "h3": ["Order ahead", "Track your delivery", "Earn rewards",
                                "Is the app free?", "Which phones does it run on?"]},
            "jsonld_types": ["FAQPage", "Question", "Answer"], "title": "The Hillroast app"}
    assert detect_page_type("https://hillroast.test/pages/app-homepage", meta) != "faq"


def test_a_page_of_questions_with_faq_markup_is_still_a_faq_page():
    meta = {"text": "", "headings": {"h2": ["Do you deliver?", "Can I return it?",
                                            "How long does delivery take?"]},
            "jsonld_types": ["FAQPage"], "title": "Help"}
    assert detect_page_type("https://hillroast.test/pages/help", meta) == "faq"
    named = {"text": "", "headings": {"h1": ["Frequently asked questions"],
                                      "h2": ["Orders", "Delivery", "Returns"]},
             "jsonld_types": ["FAQPage"], "title": "Hillroast"}
    assert detect_page_type("https://hillroast.test/pages/help", named) == "faq"
