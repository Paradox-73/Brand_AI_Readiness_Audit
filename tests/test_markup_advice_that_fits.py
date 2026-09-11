"""Markup advice has to fit the markup the site actually published.

Each group below is a case where the structured-data skill gave advice that
was true of some site but not of the one in front of it:

  1  escaped values graded high whatever they were - an author-avatar URL's
     `&amp;`, and curly quotes on one old news clipping, both at the top of a
     report's "Start here"
  2  an offer declaring a price of 0 on an about page of a shop, and the
     advice to complete it - which makes a false "free" claim valid
  3  a guide page declaring a video graded as an Article with holes in it
  4  the site's own footer profiles dropped from `sameAs`, and one account
     listed twice through a platform's country edition
  5  an organisation's `description` taken from a page three sections deep
  6  "the site publishes none" said of a description every page carries

Every host here is invented and ends in `.test`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module(os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py"),
             "sd_for_markup_advice_tests")

SITE = "https://ashcombe-wallcoverings.test"


def _page(path, page_type="other", jsonld=(), meta_description="", published="",
          footer=(), profiles=None, title="", body_text=""):
    """A snapshot page record carrying only the keys these checks read."""
    nodes = list(jsonld)
    return {
        "url": SITE + path,
        "final_url": SITE + path,
        "page_type": page_type,
        "status": 200,
        "title": title,
        "meta_description": meta_description,
        "headings": {},
        "paragraphs": [],
        "body_text": body_text,
        "links": {"internal": [], "nav": [], "external": [],
                  "footer": [{"url": u, "text": ""} for u in footer]},
        "jsonld": nodes,
        "jsonld_types": sorted({t.lower() for n in nodes
                                for t in ([n.get("@type")] if isinstance(n.get("@type"), str)
                                          else n.get("@type") or [])}),
        "social_profiles": dict(profiles or {}),
        "dates": {"jsonld_date_published": published},
    }


def _findings(result, id_hint):
    return [f for f in result.findings if f["id_hint"] == id_hint]


# --------------------------------------------------------------------------
# 1 - escaped values, graded by what they reach
# --------------------------------------------------------------------------

def _escaping(pages):
    result = SkillResult("structured-data-audit")
    SD._check_jsonld_validity(result, pages)
    found = _findings(result, "jsonld-values-escaped-as-html")
    assert found, "the escaping finding did not fire"
    return found[0]


def _avatar(n):
    return {"@type": "ImageObject",
            "url": "https://avatars.invented-host.test/a{}.png?s=96&amp;d=mm&amp;r=g".format(n)}


def test_escaped_addresses_alone_are_low_and_say_what_they_break():
    """Only the `&amp;` in an avatar URL a blog plugin writes. No name or text
    is touched, so it is not the high-severity template fault."""
    pages = [_page("/blog/post-{}/".format(n), "article",
                   jsonld=[{"@type": "BlogPosting", "headline": "Post {}".format(n)}, _avatar(n)])
             for n in range(5)]
    finding = _escaping(pages)
    assert finding["severity"] == "low"
    assert "addresses" in finding["title"]
    rationale = finding["suggested_action"]["rationale"]
    assert "second spelling of the company's name" not in rationale
    assert "amp;d" in rationale, "the rationale must say what the escaping actually breaks"


def test_escaped_text_on_a_few_current_pages_is_medium():
    """Three page names on a thirty-page site, none of them the company's."""
    pages = [_page("/p{}/".format(n), published="2026-05-01",
                   jsonld=[{"@type": "WebPage", "name": "Page {}".format(n)}])
             for n in range(27)]
    pages += [_page("/policy-{}/".format(n), published="2026-05-01",
                    jsonld=[{"@type": "WebPage", "name": "Terms &#038; Conditions {}".format(n)}])
              for n in range(3)]
    finding = _escaping(pages)
    assert finding["severity"] == "medium"
    assert "&#038;" in finding["evidence"]


def test_the_example_quoted_is_text_and_not_the_avatar_url():
    """Where both kinds are present the evidence shows the one that set the grade."""
    pages = [_page("/blog/a/", "article", published="2026-05-01", jsonld=[_avatar(1)]),
             _page("/flooring/", published="2026-05-01",
                   jsonld=[{"@type": "WebPage", "name": "Our Floors &#8217; Range"}])]
    finding = _escaping(pages)
    assert "&#8217;" in finding["evidence"]
    assert "avatars.invented-host" not in finding["evidence"]


def test_the_company_name_escaped_across_the_site_stays_high():
    """The case the finding was written for is unchanged."""
    pages = [_page("/p{}/".format(n), published="2026-05-01",
                   jsonld=[{"@type": "Organization", "name": "Tan &amp; Loom"}])
             for n in range(4)]
    finding = _escaping(pages)
    assert finding["severity"] == "high"
    assert "second spelling" in finding["suggested_action"]["rationale"]


def test_escaping_on_one_old_news_clipping_is_low():
    """Curly quotes in the breadcrumb of one clipping from 2018, on a site whose
    other pages are current. Not a site-wide template fault."""
    pages = [_page("/news/{}/".format(n), "article", published="2026-0{}-01".format(n + 1),
                   jsonld=[{"@type": "NewsArticle", "headline": "Item {}".format(n)}])
             for n in range(5)]
    pages.append(_page("/clipping/2988/", published="2018-02-15",
                       jsonld=[{"@type": "ListItem",
                                "name": "Students win &#8220;smart nurse bag&#8221; prize"}]))
    finding = _escaping(pages)
    assert finding["severity"] == "low"
    assert "one page" in finding["suggested_action"]["rationale"]


def test_the_organisation_name_escaped_on_one_page_is_medium_at_most():
    pages = [_page("/", "home", published="2026-05-01",
                   jsonld=[{"@type": "Organization", "name": "Tan &amp; Loom"}]),
             _page("/about/", "about", published="2026-05-01",
                   jsonld=[{"@type": "WebPage", "name": "About"}])]
    assert _escaping(pages)["severity"] == "medium"


# --------------------------------------------------------------------------
# 2 - an offer of 0 on a shop's about page is not to be completed
# --------------------------------------------------------------------------

def _product_page():
    return _page("/product/installation-kit/", "product", body_text="Installation kit Rs. 499",
                 jsonld=[{"@type": "Product", "name": "Installation kit",
                          "offers": {"@type": "Offer", "price": "499", "priceCurrency": "INR",
                                     "availability": "https://schema.org/InStock"}}])


def _free_service(path, page_type):
    return _page(path, page_type,
                 jsonld=[{"@type": "Service", "name": "Wallpaper - Ashcombe",
                          "offers": {"@type": "Offer", "price": "0"}}])


def _offer_finding(pages):
    by_type = {}
    for page in pages:
        by_type.setdefault(page["page_type"], []).append(page)
    result = SkillResult("structured-data-audit")
    SD._check_product(result, by_type, {"name": "Ashcombe"})
    found = _findings(result, "product-schema-missing-offer-details")
    assert found, "the offer finding did not fire"
    return found[0]


def test_a_zero_price_offer_on_a_shops_about_page_is_never_completed():
    finding = _offer_finding([_product_page(), _free_service("/about/", "about"),
                              _free_service("/about/contact-us/", "contact")])
    action = finding["suggested_action"]
    assert "price of 0" in finding["title"]
    assert "real price" in action["summary"]
    assert "Do not complete" in " ".join(action["how_to_fix"])
    assert "priceCurrency" not in action["snippet"], "the snippet completed the false claim"
    assert '"offers"' not in action["snippet"]


def test_a_free_offer_on_a_site_that_sells_nothing_is_still_completed():
    """The guard. With no product page and no cart, a price of 0 may be a real
    free plan, and the ordinary advice stands."""
    finding = _offer_finding([_page("/", "home", jsonld=[
        {"@type": "SoftwareApplication", "name": "Ashcombe Planner",
         "offers": {"@type": "Offer", "price": "0"}}])])
    assert "incomplete" in finding["title"]
    assert "priceCurrency" in finding["suggested_action"]["snippet"]


# --------------------------------------------------------------------------
# 3 - a page that declares another kind of work is not an Article with holes
# --------------------------------------------------------------------------

def _article_result(pages):
    result = SkillResult("structured-data-audit")
    SD._check_article(result, {"article": pages})
    return result


def test_a_guide_that_declares_a_video_is_not_graded_as_an_article():
    guide = _page("/guides/how-to-install-wallpaper/", "article", jsonld=[
        {"@type": "Service", "name": "How to install wallpaper?",
         "offers": {"@type": "Offer", "price": "0"}},
        {"@type": "WebPage", "name": "How to install wallpaper?"},
        {"@type": "VideoObject", "name": "How to install wallpaper?",
         "uploadDate": "2016-04-12", "embedUrl": "https://video.invented-host.test/e/1"}])
    result = _article_result([guide])
    assert not _findings(result, "article-schema-missing-properties")
    assert not _findings(result, "no-article-schema")


def test_a_howto_page_is_not_told_it_has_no_article_markup():
    guide = _page("/guides/measuring-your-walls/", "article",
                  jsonld=[{"@type": "HowTo", "name": "Measuring your walls"}])
    assert not _findings(_article_result([guide]), "no-article-schema")


def test_an_article_missing_its_author_is_still_graded():
    """The guard: a real Article block with holes is still reported."""
    post = _page("/blog/how-long-does-wallpaper-last/", "article", jsonld=[
        {"@type": "Article", "headline": "How long does wallpaper last?",
         "datePublished": "2026-03-06"}])
    found = _findings(_article_result([post]), "article-schema-missing-properties")
    assert found and "author" in found[0]["evidence"]


# --------------------------------------------------------------------------
# 4 - the site's own footer profiles, once each
# --------------------------------------------------------------------------

_FOOTER = ("https://www.facebook.com/oldslug/", "https://www.instagram.com/ashcombe/",
           "https://www.youtube.com/channel/UCinventedChannel01")


def _with_footer(path):
    return _page(path, footer=_FOOTER,
                 profiles={"Facebook": _FOOTER[0], "Instagram": _FOOTER[1],
                           "YouTube": _FOOTER[2]})


def test_profiles_the_sitewide_footer_links_reach_same_as():
    """Handles that do not spell the name, published in the footer of every page."""
    pages = [_with_footer("/p{}/".format(n)) for n in range(4)]
    found = SD._all_same_as(pages, {"name": "Ashcombe"})
    assert "https://www.facebook.com/oldslug/" in found
    assert "https://www.youtube.com/channel/UCinventedChannel01" in found
    assert "https://www.instagram.com/ashcombe/" in found


def test_a_profile_one_article_links_is_still_left_out():
    """The guard: a byline's personal account is not in the site's chrome."""
    pages = [_with_footer("/p{}/".format(n)) for n in range(4)]
    pages[0]["social_profiles"]["X"] = "https://x.com/somewriter406"
    assert "https://x.com/somewriter406" not in SD._all_same_as(pages, {"name": "Ashcombe"})


def test_one_account_on_two_country_editions_is_one_entry():
    found = SD._dedupe_profiles(["https://in.pinterest.com/ashcombe/",
                                 "https://www.pinterest.com/ashcombe/"])
    assert found == ["https://www.pinterest.com/ashcombe/"]


# --------------------------------------------------------------------------
# 5 and 6 - where the organisation's description comes from, and what the
# placeholder says when it cannot come from anywhere
# --------------------------------------------------------------------------

def test_an_about_page_three_sections_deep_does_not_describe_the_organisation():
    pages = [_page("/", "home"), _page("/about/", "about"),
             _page("/about/symbols/identity/", "about",
                   meta_description="Fonts, images, videos and templates.")]
    assert SD._site_description(pages) == ""


def test_the_about_page_itself_still_does():
    """The guard: a shallow about page's description is used."""
    pages = [_page("/", "home"),
             _page("/about/", "about",
                   meta_description="Ashcombe prints wallcoverings to order in the county."),
             _page("/about/symbols/identity/", "about",
                   meta_description="Fonts, images, videos and templates.")]
    assert SD._site_description(pages).startswith("Ashcombe prints")


def test_a_language_edition_prefix_does_not_count_as_depth():
    assert SD._section_depth(_page("/en/about/", "about")) == 1


def test_the_placeholder_names_the_description_every_page_shares():
    shared = "Widgetry - the progressive widget framework"
    pages = [_page("/", "home", meta_description=shared),
             _page("/guide/", "documentation", meta_description=shared),
             _page("/api/", "documentation", meta_description=shared)]
    assert SD._site_description(pages) == "", "the shared description is still not published"
    placeholder = SD._description_placeholder(pages)
    assert "publishes none" not in placeholder
    assert shared in placeholder
    assert placeholder.startswith("<") and placeholder.endswith(">")
    assert "3 of the 3" in placeholder


def test_the_placeholder_says_plainly_when_there_is_nothing():
    placeholder = SD._description_placeholder([_page("/", "home"), _page("/about/", "about")])
    assert "neither the homepage nor the about page" in placeholder


def _org_payload(pages, brand):
    snapshot = {"origin": SITE, "site": "ashcombe-wallcoverings.test", "pages": pages,
                "brand": brand}
    node = next((n for n in pages[0]["jsonld"] if n.get("@type") == "Organization"), None)
    snippet = SD._org_snippet(snapshot, pages, brand, node=node)
    return json.loads(re.sub(r"</?script[^>]*>", "", snippet))


def _titled(path, title, page_type="other", name="Harlow"):
    return _page(path, page_type, title=title,
                 jsonld=[{"@type": "Organization", "name": name, "url": SITE + "/"}])


def test_a_university_under_its_nickname_is_given_its_type_and_full_name():
    pages = [_titled("/", "Harlowmere University", "home"),
             _titled("/admissions/", "Admissions - Harlowmere University"),
             _titled("/research/", "Research - Harlowmere University"),
             _titled("/library/", "Library - Harlowmere University")]
    payload = _org_payload(pages, {"name": "Harlow", "fallback_candidates": ["Harlow"]})
    assert payload["@type"] == "CollegeOrUniversity"
    assert payload["name"] == "Harlowmere University"
    alternates = payload["alternateName"]
    assert "Harlow" in (alternates if isinstance(alternates, list) else [alternates])


def test_a_shop_with_the_same_short_name_is_left_an_organization():
    """The guard: no degree-granting word in any name the site shows."""
    pages = [_titled("/", "Harlow Wallcoverings", "home"),
             _titled("/rolls/", "Rolls - Harlow Wallcoverings"),
             _titled("/murals/", "Murals - Harlow Wallcoverings")]
    payload = _org_payload(pages, {"name": "Harlow"})
    assert payload["@type"] == "Organization"
    assert payload["name"] == "Harlow"
    assert "alternateName" not in payload


def test_one_page_titled_after_a_product_cannot_rename_a_shop():
    pages = [_titled("/", "Harlow Stationery", "home"),
             _titled("/pads/", "College ruled notebooks - Harlow Stationery"),
             _titled("/pens/", "Pens - Harlow Stationery")]
    assert _org_payload(pages, {"name": "Harlow"})["@type"] == "Organization"


def test_the_organisation_snippet_no_longer_says_publishes_none():
    shared = "Widgetry - the progressive widget framework"
    pages = [_page("/", "home", meta_description=shared),
             _page("/guide/", "documentation", meta_description=shared)]
    snapshot = {"origin": SITE, "site": "ashcombe-wallcoverings.test", "pages": pages,
                "brand": {"name": "Widgetry", "host": "ashcombe-wallcoverings.test"}}
    snippet = SD._org_snippet(snapshot, pages, snapshot["brand"])
    payload = json.loads(re.sub(r"</?script[^>]*>", "", snippet))
    assert "publishes none" not in payload["description"]
    assert shared in payload["description"]
