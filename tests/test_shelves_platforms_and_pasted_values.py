# -*- coding: utf-8 -*-
"""Seven defects found running the frozen build on eleven real sites.

Every case below is a report sentence that was wrong about a site, reduced to
the smallest snapshot that reproduces it.

  one template language, everywhere   "in Liquid, `{{ value | json }}`" printed
                                      word for word in all five reports of one
                                      batch, at a shop on a different
                                      platform, at a JavaScript storefront and
                                      at a hosted site builder
  a shelf told to be a product        a grid of 36 links to item pages, listed
                                      under "Product pages with no Product
                                      JSON-LD", with no ItemList asked for
                                      anywhere in the report
  two findings, opposite orders       one told the owner to strip the tagline
                                      out of `name`; the snippet five lines
                                      under it published the tagline
  duplicates the check could not see  four duplicate pairs, all indexable, all
                                      self-canonical, reported as "no two
                                      crawled addresses delivered the same
                                      title and the same main text"
  a platform's name read as the site's the WebSite's `provider`, which names
                                      the SaaS the shop runs on
  values that are not what the site says `"telephone": "[9910901961]"`, a
                                      description starting mid-word, a
                                      promotional banner published as a logo,
                                      and a login page published as `sameAs`
  evidence that does not show the fault 90 characters of a product title
                                      containing no escaped entity at all

Every host and brand here is invented. Naming a *platform* whose signature the
code recognises is what `test_marketplace.py::test_no_real_domain_names_anywhere`
allows, and is what `PLATFORM_SIGNATURES` already does.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult, publishing_platform  # noqa: E402
from page_extract import extract_page  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module(os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py"),
             "sd_for_shelf_tests")

SITE = "https://tallow-and-wick.test"


def _page(url, page_type="other", title="", links=(), headings=None,
          paragraphs=(), body_text="", jsonld=(), jsonld_types=(), og=None,
          nav=(), external=(), contact_facts=None, canonical=""):
    """A snapshot page record carrying only the keys these checks read."""
    return {
        "url": SITE + url,
        "page_type": page_type,
        "status": 200,
        "title": title,
        "canonical": canonical,
        "headings": headings or {},
        "paragraphs": list(paragraphs),
        "body_text": body_text,
        "links": {
            "internal": [{"url": SITE + u, "text": t} for u, t in links],
            "nav": [{"url": SITE + u, "text": t} for u, t in nav],
            "footer": [],
            "external": [{"url": u, "text": t} for u, t in external],
        },
        "jsonld": list(jsonld),
        "jsonld_types": list(jsonld_types),
        "og": og or {},
        "contact_facts": contact_facts or {},
    }


def _by_type(pages):
    out = {}
    for page in pages:
        out.setdefault(page["page_type"], []).append(page)
    return out


# --------------------------------------------------------------------------
# 1 - one hosted shop platform's template language, printed at every site
# --------------------------------------------------------------------------

ORIGIN = "https://tallow-and-wick.example"


def _platform_snapshot(extra_head="", headers=None, pages=6):
    """A snapshot whose pages carry whatever platform signals the head holds."""
    html = ("<html><head>{}</head><body><main><h1>Tallow and Wick</h1>"
            "<p>Tallow and Wick pours candles by hand.</p></main></body></html>"
            ).format(extra_head)
    page = extract_page(url=ORIGIN + "/", final_url=ORIGIN + "/", status=200,
                        headers=headers or {}, html=html, redirect_chain=[],
                        elapsed_ms=10, depth=0, source="seed", origin=ORIGIN)
    return {"brand": {"host": "tallow-and-wick.example"},
            "pages": [dict(page) for _ in range(pages)]}


def _escaped_page():
    """One page whose JSON-LD values were run through an HTML-escape filter."""
    return {
        "url": ORIGIN + "/",
        "page_type": "home",
        "status": 200,
        "jsonld": [{"@type": "Organization",
                    "name": "Tallow &amp; Wick",
                    "description": "Hand-poured candles &#39;made slowly&#39;."}],
        "jsonld_types": ["organization"],
    }


def _escaping_finding(snapshot):
    result = SkillResult("structured-data-audit")
    SD._check_jsonld_validity(result, [_escaped_page()], publishing_platform(snapshot))
    matching = [f for f in result.findings
                if f["id_hint"] == "jsonld-values-escaped-as-html"]
    assert matching, "the escaping finding did not fire"
    return matching[0]


def _steps(finding):
    # `how_to_fix` lives under `suggested_action`, which is where `make_finding`
    # puts it and where every consumer reads it.
    return " ".join(finding["suggested_action"]["how_to_fix"])


def test_the_shops_own_template_language_is_named_and_only_there():
    """A hosted shop platform's own filter, on a site running that platform."""
    snapshot = _platform_snapshot(
        extra_head='<meta name="generator" content="Shopify">'
                   '<script src="https://cdn.shopify.com/s/files/1/app.js"></script>',
        headers={"x-shopid": "12345"})
    assert publishing_platform(snapshot).named, "the fixture must name the platform"
    steps = _steps(_escaping_finding(snapshot))
    assert "Liquid" in steps
    assert "| json" in steps


def test_a_javascript_storefront_is_never_told_about_liquid():
    """The step that reached a JavaScript storefront, a WooCommerce shop and a
    hosted site builder verbatim. None of the three has a Liquid template."""
    snapshot = _platform_snapshot(
        extra_head='<meta name="generator" content="Nuxt">'
                   '<script src="/_nuxt/entry.js"></script>'
                   '<div id="__nuxt"></div>')
    steps = _steps(_escaping_finding(snapshot))
    assert "Liquid" not in steps
    assert "JSON.stringify" in steps


def test_a_wordpress_site_is_sent_to_the_plugin_and_not_to_the_theme():
    """On the WooCommerce site the block is emitted by an SEO plugin, so
    neither Liquid nor the theme is where the edit goes."""
    snapshot = _platform_snapshot(
        extra_head='<meta name="generator" content="WordPress 6.5">'
                   '<script src="/wp-content/themes/x/main.js"></script>',
        headers={"x-pingback": ORIGIN + "/xmlrpc.php"})
    steps = _steps(_escaping_finding(snapshot))
    assert "Liquid" not in steps
    assert "plugin" in steps.lower()
    assert "wp_json_encode" in steps


def test_a_site_nobody_can_name_gets_no_engine_syntax_at_all():
    """Where the platform is unknown the mechanism is described and no
    template language is named."""
    steps = _steps(_escaping_finding(_platform_snapshot()))
    for engine in ("Liquid", "Twig", "Handlebars", "JSON.stringify",
                   "wp_json_encode", "jsonify"):
        assert engine not in steps, "named {} on a site nobody can name".format(engine)
    assert "print each value as JSON" in steps


def test_the_escaping_fix_and_the_parse_error_fix_do_not_contradict_each_other():
    """The malformed-JSON step read "escape the values rather than
    interpolating them raw", directly under a finding reporting a template that
    escaped the values. A reader cannot follow both."""
    # Comments stripped first. The comment above the corrected step quotes the
    # old wording to say what was wrong with it, and a test that cannot tell a
    # quotation from an instruction would forbid the repository from recording
    # its own history.
    source = open(os.path.join(ROOT, "skills", "structured-data-audit",
                               "scripts", "check.py"), encoding="utf-8").read()
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "escape the values rather than" not in code


def test_every_check_that_talks_about_a_template_takes_the_platform():
    """Two reports out of five reached their last page without one sentence
    saying which file or which screen the reader was being sent to."""
    source = open(os.path.join(ROOT, "skills", "structured-data-audit",
                               "scripts", "check.py"), encoding="utf-8").read()
    routed = ("_check_jsonld_validity", "_check_organization", "_check_product",
              "_check_article", "_check_faq", "_check_breadcrumbs",
              "_check_website_searchaction", "_check_consistency",
              "_check_per_location_markup", "_check_listing_markup")
    for name in routed:
        assert re.search(r"def {}\(.*platform".format(name), source), \
            "{} still cannot say which template".format(name)


# --------------------------------------------------------------------------
# 2 - a listing page told to add Product markup
# --------------------------------------------------------------------------

def _shelf(count=36):
    """A grid of item tiles: many links to item pages, and no subheadings.

    The shape every heading-based test misses. A tile's name is a link inside a
    div, so the page has no h2 and no h3, and both `is_listing_page` and
    "carries nothing but other pages' titles" come back False.
    """
    return _page("/shop-all", "product", title="Shop All",
                 headings={"h1": ["Shop All"]},
                 links=[("/product-page/candle-{}".format(n), "Candle {}".format(n))
                        for n in range(count)],
                 body_text="Shop All. " + " ".join(
                     "Candle {} 24.00".format(n) for n in range(count)))


def test_a_grid_of_item_tiles_is_not_a_product_page():
    """`/shop-all` is titled "Shop All", carries 36 links to item pages and
    publishes no JSON-LD. It was named under "Product pages with no Product
    JSON-LD", and `Product` markup on it would declare the whole shelf to be
    one item at one price."""
    why = SD._not_the_type_it_was_given(_shelf())
    assert why, "the shelf still reads as one thing for sale"
    assert "36" in why and "/product-page" in why


def test_an_item_page_with_a_related_strip_is_still_an_item_page():
    """The guard the shelf test has to survive. A product page's "you may also
    like" row links to its own siblings, and a page is never read as the index
    of the family it belongs to, however many of them it links to."""
    item = _page("/product-page/candle-4", "product",
                 title="Beeswax pillar candle",
                 headings={"h1": ["Beeswax pillar candle"]},
                 links=[("/product-page/candle-{}".format(n), "Candle {}".format(n))
                        for n in range(12)],
                 body_text="Beeswax pillar candle. 24.00 GBP.")
    assert SD._not_the_type_it_was_given(item) == ""


def test_an_index_of_dated_notices_is_not_an_article():
    """A town's news index was typed `article` and told it has no Article
    markup, and the paste-ready snippet declared the index's own title, "list of
    new information", as the `headline` of a dated authored piece. The same
    report's date check excludes index pages because "an index carries the dates
    of the items on it"."""
    index = _page("/shinchaku", "article", title="List of new information",
                  headings={"h1": ["List of new information"]},
                  links=[("/shinchaku/2026/notice-{}".format(n), "Notice {}".format(n))
                         for n in range(14)])
    result = SkillResult("structured-data-audit")
    SD._check_article(result, _by_type([index]))
    assert result.findings == [], "the index was graded as a post"
    assert index["title"] not in json.dumps(result.not_applicable), (
        "the index's own title must never reach a headline")


def test_the_shelf_gets_the_ask_a_shelf_actually_has():
    """"the right ask is `ItemList`/`CollectionPage`, which the report never
    makes"."""
    result = SkillResult("structured-data-audit")
    shelf = _shelf()
    SD._check_listing_markup(result, _by_type([shelf]))
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding["affected_pages"] == [shelf["url"]]
    steps = " ".join(finding["suggested_action"]["how_to_fix"])
    assert "ItemList" in steps
    assert "Do not put `Product` markup on these pages" in steps


def test_the_shelf_snippet_is_json_ld_naming_pages_the_shelf_links_to():
    """The paste-ready block, proved rather than asserted: it parses, it is an
    ItemList, and every address in it is one the page really links to."""
    result = SkillResult("structured-data-audit")
    shelf = _shelf()
    SD._check_listing_markup(result, _by_type([shelf]))
    snippet = result.findings[0]["suggested_action"]["snippet"]
    payload = json.loads(re.sub(r"</?script[^>]*>", "", snippet).strip())
    assert payload["@type"] == "ItemList"
    linked = {entry["url"] for entry in shelf["links"]["internal"]}
    for item in payload["itemListElement"]:
        assert item["url"] in linked, "the snippet lists a page the shelf does not"
    assert [item["position"] for item in payload["itemListElement"]] == \
        list(range(1, len(payload["itemListElement"]) + 1))


def test_declaring_the_type_the_snippet_asks_for_clears_the_finding():
    """Following the advice has to resolve the finding. The block the snippet
    hands over declares ItemList; a page carrying it is not reported again."""
    shelf = _shelf()
    fixed = dict(shelf, jsonld_types=["ItemList"],
                 jsonld=[{"@type": "ItemList", "itemListElement": []}])
    result = SkillResult("structured-data-audit")
    SD._check_listing_markup(result, _by_type([fixed]))
    assert result.findings == []
    assert result.not_applicable, "a check that stays quiet has to say why"


def test_a_shelf_is_never_asked_for_product_markup_and_a_real_item_page_still_is():
    """Both halves of the correction in one run: the shelf leaves the product
    population, and the item page that really has no markup stays in it."""
    shelf = _shelf()
    item = _page("/product-page/candle-4", "product", title="Beeswax pillar",
                 headings={"h1": ["Beeswax pillar"]},
                 body_text="Beeswax pillar candle. 24.00 GBP.")
    result = SkillResult("structured-data-audit")
    SD._check_product(result, _by_type([shelf, item]), {"name": "Tallow and Wick"})
    findings = [f for f in result.findings if f["id_hint"] == "no-product-schema"]
    assert findings, "the item page with no Product markup must still be reported"
    assert findings[0]["affected_pages"] == [item["url"]]
    assert shelf["url"] not in json.dumps(findings[0])


# --------------------------------------------------------------------------
# 3 - two findings giving opposite instructions for one property
# --------------------------------------------------------------------------

SHOP_NAME = "The Bicyclist: Handmade Leather Goods"


def _shop_pages():
    """A hosted-shop crawl where one address has no title of its own.

    An app-proxy page falls back to the shop name, so its `<title>` is the shop
    name word for word - and `og:site_name` says the same string on every page.
    """
    return [
        _page("/", "home", title="Handmade leather goods",
              og={"og:site_name": SHOP_NAME}),
        _page("/apps/track123", "other", title=SHOP_NAME,
              og={"og:site_name": SHOP_NAME}),
    ]


def test_a_page_whose_title_is_the_shop_name_does_not_convict_the_name():
    """The finding read "`name` is "<shop>" - it is the <title> of
    .../apps/track123, word for word", at high severity, and ordered the
    tagline removed. That address has no title of its own."""
    assert SD._name_is_really_a_page_title(SHOP_NAME, _shop_pages()) == ""


def test_a_name_that_really_is_one_pages_seo_title_is_still_caught():
    """The guard the fix above has to survive. A clinic's only identity block
    sets `name` to one page's whole title."""
    pages = [_page("/", "home", title="Home"),
             _page("/blog/gum-care", "article",
                   title="Gum care after 40 - what to expect")]
    why = SD._name_is_really_a_page_title(
        "Gum care after 40 - what to expect", pages)
    assert why and "/blog/gum-care" in why


def test_the_snippet_never_republishes_the_name_the_finding_condemned():
    """"Set `name` to the organisation's name on its own ... with no tagline",
    and five lines below it the block published that exact string."""
    pages = [_page("/", "home", title="Bicycle bags"),
             _page("/collections/bags", "category", title=SHOP_NAME)]
    snapshot = {"site": SITE, "origin": SITE, "pages": pages,
                "brand": {"name": SHOP_NAME}}
    node = {"@type": "Organization", "name": SHOP_NAME}
    snippet = SD._org_snippet(snapshot, pages, {"name": SHOP_NAME}, node=node)
    payload = json.loads(re.sub(r"</?script[^>]*>", "", snippet).strip())
    assert payload["name"] != SHOP_NAME, (
        "the snippet republished the value the finding had just condemned")
    assert payload["name"].startswith("<"), "an invented name is worse than a placeholder"


# --------------------------------------------------------------------------
# 4 - the duplicate-page test required the title and the body to match
# --------------------------------------------------------------------------

ABOUT = ("Tallow and Wick has poured candles in the old creamery since 2009. "
         "Every wick is cotton and every wax is rapeseed grown within forty "
         "miles of the workshop. We sell through the shop, at three markets a "
         "month, and by post. The workshop is open on Saturdays and visitors "
         "are welcome to watch a pour.")


def test_two_addresses_with_one_body_and_two_titles_are_one_page():
    """`/about/` and `/about-us/` carry identical body copy under different
    titles. The check was an AND, so it reported "no two crawled addresses
    delivered the same title and the same main text"."""
    groups = SD._one_page_at_several_addresses([
        _page("/about/", title="About us", body_text=ABOUT),
        _page("/about-us/", title="Our story", body_text=ABOUT),
    ])
    assert len(groups) == 1 and len(groups[0]) == 2


def test_bodies_that_are_nearly_the_same_are_one_page_too():
    """`/contact/` and `/contact-us/` measured 0.93 alike. Exact equality could
    never see them."""
    other = ABOUT.replace("on Saturdays", "on Saturday mornings")
    groups = SD._one_page_at_several_addresses([
        _page("/contact/", title="Contact", body_text=ABOUT),
        _page("/contact-us/", title="Contact us", body_text=other),
    ])
    assert len(groups) == 1 and len(groups[0]) == 2


def test_two_different_pages_on_one_template_are_not_folded_together():
    """The guard. Two item pages share their chrome and almost none of their
    sentences, and folding them would delete a page from every count this skill
    prints."""
    first = ("The beeswax pillar burns for forty hours and is poured in a "
             "single mould. Add to basket. Free delivery over thirty pounds. "
             "Returns within fourteen days. Tallow and Wick, the old creamery.")
    second = ("The rapeseed jar is scented with bergamot and burns for twenty "
              "hours. Add to basket. Free delivery over thirty pounds. "
              "Returns within fourteen days. Tallow and Wick, the old creamery.")
    groups = SD._one_page_at_several_addresses([
        _page("/product-page/pillar", title="Beeswax pillar", body_text=first),
        _page("/product-page/jar", title="Rapeseed jar", body_text=second),
    ])
    assert groups == []


def test_the_report_says_the_titles_differ_rather_than_leaving_it_to_be_noticed():
    """A reader looking at two different titles will otherwise conclude the
    pages are different."""
    pages = [_page("/about/", title="About us", body_text=ABOUT),
             _page("/about-us/", title="Our story", body_text=ABOUT),
             _page("/shop/", title="Shop", body_text=ABOUT.replace("candles", "candle")),
             _page("/shop-2/", title="Shop", body_text=ABOUT.replace("candles", "candle"))]
    result = SkillResult("structured-data-audit")
    SD._check_canonical_declaration(result, pages)
    matching = [f for f in result.findings
                if f["id_hint"] == "the-same-page-at-several-addresses-with-no-canonical"]
    assert matching, "four duplicate addresses and no canonical went unreported"
    assert "The titles differ" in matching[0]["evidence"]
    assert "same title and the same main text" not in json.dumps(matching[0])


# --------------------------------------------------------------------------
# 5 - a platform's own name, read as a name the site claims
# --------------------------------------------------------------------------

def test_the_saas_a_shop_runs_on_does_not_speak_for_the_shop():
    """`"provider":{"@type":"Organization","name":"Plugbox","url":"https://plugbox.test"}`
    is the WebSite naming the platform it runs on, with its own separate
    address - the same category as `publisher` or `sponsor` naming somebody
    else. A report read it as a second name the site asserts for itself."""
    provider = {"@type": "Organization", "name": "Plugbox",
                "url": "https://plugbox.test", "_nested_in": "provider"}
    assert not SD._speaks_for_the_site(provider, {"name": "Brogue Ganesha"})
    own = {"@type": "Organization", "name": "Brogue Ganesha"}
    assert SD._speaks_for_the_site(own, {"name": "Brogue Ganesha"})


def test_a_provider_node_never_reaches_the_pasted_block():
    """The value that matters: the platform's name and its address must not be
    published as the shop's own identity."""
    pages = [_page("/", "home", title="Brogue Ganesha",
                   jsonld=[{"@type": "Organization", "name": "Plugbox",
                            "url": "https://plugbox.test", "_nested_in": "provider"}],
                   jsonld_types=["organization"])]
    snapshot = {"site": SITE, "origin": SITE, "pages": pages,
                "brand": {"name": "Brogue Ganesha"}}
    snippet = SD._org_snippet(snapshot, pages, {"name": "Brogue Ganesha"})
    assert "Plugbox" not in snippet
    assert "plugbox.test" not in snippet


# --------------------------------------------------------------------------
# 6 - snippet values that are not what the site says
# --------------------------------------------------------------------------

def test_a_telephone_that_is_not_a_number_is_not_published():
    """`"telephone": "[9910901961]"` shipped on two snippets in one report.
    Pasting it publishes a `telephone` of `[9910901961]`."""
    assert SD._publishable_telephone("[9910901961]", []) == ""


def test_the_country_code_the_site_writes_survives_into_the_snippet():
    """The site's only phone is inside a chat link carrying `+91`. The snippet
    dropped it, which publishes a different number in a different country."""
    pages = [_page("/contact", "contact",
                   external=[("https://wa.me/+919910901961", "Message us")])]
    assert SD._publishable_telephone("[9910901961]", pages) == "+919910901961"


def test_an_ordinary_number_is_left_exactly_as_the_site_wrote_it():
    """The guard: a value that is already a number is not rewritten."""
    assert SD._publishable_telephone("+44 20 7946 0000", []) == "+44 20 7946 0000"


def test_a_description_never_starts_in_the_middle_of_the_shops_own_name():
    """The site writes "Natural Handmade Soaps Online - Our Soaps are Made
    from...". Its logo link is labelled with the first three words, so the
    snippet published "Online - Our Soaps are Made from..." - a string that
    appears nowhere on the site and reads as broken English."""
    cleaned = SD._without_a_leading_link_label(
        "Natural Handmade Soaps Online - Our Soaps are Made from Natural Ingredients",
        {"natural handmade soaps"})
    assert cleaned == "Our Soaps are Made from Natural Ingredients"


def test_a_menu_label_in_front_of_a_sentence_is_still_stripped():
    """The guard. A language-switcher label sitting in the same run of text as
    the opening sentence is what this function exists to remove."""
    cleaned = SD._without_a_leading_link_label(
        "En Espanol The National Records Office is the nation's record keeper.",
        {"en espanol"})
    assert cleaned.startswith("The National Records Office")


def test_a_promotional_banner_is_not_published_as_the_logo():
    """A municipality's homepage og:image is a travel-magazine banner with a
    photograph of a television personality on it. Every other report writes the
    placeholder; this one substituted silently."""
    pages = [_page("/", "home", og={"og:image": SITE + "/media/2026-travel-fair.jpg"})]
    snapshot = {"site": SITE, "origin": SITE, "pages": pages, "brand": {}}
    assert SD._site_logo(snapshot, pages) is None


def test_a_homepage_card_that_says_it_is_the_logo_is_still_read():
    """The guard, and the case the source was found on: a museum's homepage
    og:image whose filename is `site-logo`."""
    pages = [_page("/", "home", og={"og:image": SITE + "/assets/site-logo.svg"})]
    snapshot = {"site": SITE, "origin": SITE, "pages": pages, "brand": {}}
    assert SD._site_logo(snapshot, pages) == SITE + "/assets/site-logo.svg"


def test_a_login_page_is_not_an_identity():
    """`.../users/sign_in?ref=sideNav` reached a paste-ready `sameAs`. Following
    it a machine finds a sign-in form and no account."""
    assert not SD._is_an_account_and_not_an_action(
        "https://civic-notices.test/users/sign_in?ref=sideNav")


def test_one_issue_of_a_newsletter_is_not_an_account():
    """Another report's `sameAs` carried a single newsletter issue with its
    campaign identifiers still attached."""
    assert not SD._is_an_account_and_not_an_action(
        "https://letters.test/issues/spring-fair?utm_campaign=spring&utm_medium=email")
    assert SD._is_an_account_and_not_an_action("https://letters.test/tallowandwick")


def test_the_guard_runs_on_a_site_whose_name_is_not_written_in_latin_letters():
    """The handle test strips a brand name down to `[a-z0-9]`, which is empty
    for a name written in Japanese - so on every such site no filter ran at all
    and every off-site link the crawl saw went into `sameAs`."""
    pages = [_page("/", "home")]
    pages[0]["social_profiles"] = {
        "a": "https://civic-notices.test/users/sign_in?ref=sideNav",
        "b": "https://civic-notices.test/tallowandwick",
    }
    # Both claimed with `rel="me"`, so the host test lets both through and the
    # sign-in page has to be stopped by the guard itself.
    pages[0]["declared_profiles"] = dict(pages[0]["social_profiles"])
    same_as = SD._all_same_as(pages, {"name": chr(0x6E29) + chr(0x6CC9)})
    assert same_as == ["https://civic-notices.test/tallowandwick"]


# --------------------------------------------------------------------------
# 7 - the evidence window does not contain the thing being reported
# --------------------------------------------------------------------------

def test_the_quoted_excerpt_contains_the_escaped_character():
    """The escaping finding quoted 90 characters of a product title containing
    no escaped entity at all, so a reader could not verify it from the evidence
    given."""
    value = ("Hand-poured beeswax pillar candle in a reclaimed glass jar, "
             "scented with bergamot and cedar, burns for forty hours "
             "&#39;made slowly in the old creamery&#39;")
    excerpt = SD._centred_on(value, 90)
    assert len(excerpt) <= 96, "the window may not grow to swallow the value"
    assert "&#39;" in excerpt, "the reader still cannot see the defect"


def test_a_short_value_is_printed_whole():
    """The guard: nothing is cut that fits."""
    assert SD._centred_on("Tallow &amp; Wick", 90) == "Tallow &amp; Wick"


def test_the_finding_itself_shows_the_reader_the_escaped_value():
    long_title = ("Hand-poured beeswax pillar candle in a reclaimed glass jar, "
                  "scented with bergamot and cedar and finished by hand, "
                  "burns for forty hours &#39;slowly&#39;")
    page = {"url": SITE + "/product-page/pillar", "page_type": "product",
            "status": 200, "jsonld": [{"@type": "Product", "name": long_title}],
            "jsonld_types": ["product"]}
    result = SkillResult("structured-data-audit")
    SD._check_jsonld_validity(result, [page])
    finding = next(f for f in result.findings
                   if f["id_hint"] == "jsonld-values-escaped-as-html")
    assert "&#39;" in finding["evidence"]
