# -*- coding: utf-8 -*-
"""Two defects measured on a build where the address always won.

First, on an Indonesian shoe shop running a headless storefront:

    "out-4/snapshot.json records page_type: category for it; 0 of 26 pages
    were typed product."

All eleven product pages sit under `/shop/<handle>`. `shop` is a listing word,
so the address said `category`, and the twelve English phrases the classifier
falls back to - "add to cart", "in stock", "free shipping" - appear nowhere on
a page written in Bahasa Indonesia. Two things followed. A listing-only
finding fired on a page whose `<title>` names one shoe. And the single most
valuable recommendation for a shop with no structured data at all - publish
`Product` and `Offer` with a price and an availability - was never made,
because the audit believed the site had no product page on it.

The correction must not undo the opposite one. An earlier fix stopped a grid
of 36 links to item pages being typed `product` and told to publish `Product`
markup, which would declare a whole shelf to be one item at one price; that
case is pinned in `test_shelves_platforms_and_pasted_values.py` and repeated
here from the other side, through the extractor rather than through a hand-
written record.

Second, on a controlled fixture: one page shipped
`<meta name="robots" content="noindex,nofollow">`, was typed `other`, was
graded by the hygiene checks, and the check that exists to report the
directive said "no crawled content page carries a noindex directive".

Every host and brand below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import DISTINCT_PRICE_CEILING, SkillResult  # noqa: E402
from page_extract import extract_page, retype_from_the_page_itself  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module(os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py"),
             "sd_for_page_typing_tests")
RR = _module(os.path.join(ROOT, "skills", "render-readability-audit", "scripts", "check.py"),
             "rr_for_page_typing_tests")

SITE = "https://langkah-kaki.test"

# The eleven shoes the real shop sells, as handles under `/shop/`.
HANDLES = [
    "sepatu-retrograde-low-gd", "sepatu-retrograde-high-og",
    "sepatu-gazelle-mono-bw", "sepatu-velocity-canvas-navy",
    "sepatu-velocity-canvas-sand", "sepatu-trekker-suede-olive",
    "sepatu-trekker-suede-clay", "sepatu-court-classic-white",
    "sepatu-court-classic-black", "sepatu-runner-mesh-grey",
    "sepatu-runner-mesh-teal",
]


def _extract(path, html):
    return extract_page(url=SITE + path, final_url=SITE + path, status=200,
                        headers={"content-type": "text/html"}, html=html,
                        redirect_chain=[], elapsed_ms=12, depth=1,
                        source="link", origin=SITE)


# --------------------------------------------------------------------------
# The shop in question: a headless storefront, written in Indonesian
# --------------------------------------------------------------------------

def _product_html(handle, name, price="Rp 1.198.000"):
    """One item page as this class of storefront delivers it.

    No JSON-LD, no English anywhere, and the buy control is a form whose
    action and whose button class are the only English on the page - because
    they are identifiers a developer typed, not words the shop wrote.
    """
    return (
        "<html lang=\"id\"><head><title>{name} | Langkah Kaki®</title>"
        "<meta name=\"description\" content=\"{name}, sepatu kanvas buatan tangan.\">"
        "</head><body><header><nav>"
        "<a href=\"/\">Beranda</a><a href=\"/shop\">Belanja</a>"
        "<a href=\"/tentang-kami\">Tentang Kami</a></nav></header>"
        "<main><h1>{name}</h1>"
        "<div class=\"price\">{price}</div>"
        "<p>Sepatu kanvas dengan sol karet vulkanisir, dijahit tangan di Bandung. "
        "Tersedia dalam ukuran 39 sampai 45. Pengiriman dari gudang kami setiap "
        "hari kerja.</p>"
        "<form action=\"/cart/add\" method=\"post\">"
        "<select name=\"ukuran\"><option>39</option><option>40</option>"
        "<option>41</option></select>"
        "<button type=\"submit\" class=\"btn add-to-cart\">Masukkan Keranjang</button>"
        "</form>"
        "<section><h2>Lihat juga</h2>"
        "<a href=\"/shop/{other}\">Sepatu lain</a></section>"
        "</main></body></html>"
    ).format(name=name, price=price, other=HANDLES[0], handle=handle)


def _shop_index_html():
    """The shelf those items sit on: a tile per shoe, quick-add under each."""
    tiles = "".join(
        "<div class=\"tile\"><a href=\"/shop/{h}\">{h}</a>"
        "<span class=\"price\">Rp {n}98.000</span>"
        "<form action=\"/cart/add\"><button class=\"add-to-cart\">+</button></form>"
        "</div>".format(h=handle, n=n + 1)
        for n, handle in enumerate(HANDLES))
    return ("<html lang=\"id\"><head><title>Belanja | Langkah Kaki®</title></head>"
            "<body><main><h1>Belanja</h1>{}</main></body></html>".format(tiles))


def _shop_snapshot():
    """The 26-page crawl, reduced to the pages whose type is in question."""
    pages = [_extract("/shop/" + handle,
                      _product_html(handle, handle.replace("sepatu-", "").replace("-", " ")))
             for handle in HANDLES]
    pages.append(_extract("/shop", _shop_index_html()))
    return pages


def test_eleven_product_pages_under_a_listing_slug_are_typed_product():
    """0 of 26 before. The address says `/shop/...`, which is a listing word;
    the page carries one buy control, one price and one subject."""
    pages = _shop_snapshot()
    products = [p for p in pages if p["page_type"] == "product"]
    assert len(products) == len(HANDLES), (
        "typed: " + repr(sorted((p["url"], p["page_type"]) for p in pages)))
    assert all("/shop/" in p["url"] for p in products)


def test_the_shelf_those_items_sit_on_is_still_a_listing():
    """The same slug, one segment up, with a quick-add control per tile."""
    index = [p for p in _shop_snapshot() if p["url"].endswith("/shop")][0]
    assert index["page_type"] == "category"
    assert index["add_to_cart_controls"]["count"] == len(HANDLES), (
        "a shelf carries one control per item, and that count is what "
        "separates it from an item page")


def test_the_reason_is_recorded_beside_the_type():
    """A type is an assertion every downstream check is keyed to, so a page
    called something other than what its address said has to say which
    measurement did it."""
    page = _extract("/shop/" + HANDLES[0], _product_html(HANDLES[0], "Retrograde GD"))
    assert page["page_type_evidence"]
    assert "add-to-basket" in page["page_type_evidence"]
    assert "Retrograde GD" in page["page_type_evidence"]


def test_a_page_the_address_named_keeps_its_own_reason_empty():
    """Nothing is recorded where the address and the page agreed."""
    page = _extract("/tentang-kami", "<html><head><title>Tentang Kami</title></head>"
                                     "<body><main><h1>Tentang Kami</h1>"
                                     "<p>Kami membuat sepatu.</p></main></body></html>")
    assert page["page_type_evidence"] == ""


def test_the_recommendation_the_shop_needed_now_reaches_it():
    """The miss, which cost more than the false positive: with no product page
    on the site, `Product` and `Offer` are asked for nowhere."""
    pages = _shop_snapshot()
    by_type = {}
    for page in pages:
        by_type.setdefault(page["page_type"], []).append(page)
    result = SkillResult("structured-data-audit")
    SD._check_product(result, by_type, {"name": "Langkah Kaki"})
    findings = [f for f in result.findings if f["id_hint"] == "no-product-schema"]
    assert findings, "the shop with no JSON-LD is still never asked for Product markup"
    # `affected_page_count` is the number that was not truncated;
    # `affected_pages` is capped at five for display. Counting the display list
    # is exactly the mistake the count field exists to stop, so this checks
    # both directions: all eleven are reported, and every page named is one of
    # them.
    assert findings[0]["affected_page_count"] == len(HANDLES)
    named = set(findings[0]["affected_pages"])
    assert named and named <= {SITE + "/shop/" + h for h in HANDLES}
    steps = " ".join(findings[0]["suggested_action"]["how_to_fix"])
    assert "availability" in steps and "price" in steps


def test_the_listing_only_finding_no_longer_fires_on_a_single_product():
    """The false positive as the report printed it: "Listing pages containing a
    load-more control with no numbered pagination links in the HTML:
    <a single shoe>". A page that is one product is not a listing page."""
    pages = _shop_snapshot()
    product = _extract("/shop/" + HANDLES[0],
                       _product_html(HANDLES[0], "Retrograde GD Black White")
                       .replace("Lihat juga", "Lihat juga - view more"))
    assert product["page_type"] == "product"
    result = SkillResult("render-readability-audit")
    RR._check_pagination(result, {"pages": pages + [product]}, pages + [product])
    for finding in result.findings:
        assert product["url"] not in str(finding), finding


def test_a_promoted_page_is_one_the_markup_check_still_grades():
    """The classifier and `_not_the_type_it_was_given` have to agree. A page
    promoted here and set aside there is a page nothing ever grades, which is
    the miss dressed up as a fix."""
    for page in _shop_snapshot():
        if page["page_type"] != "product":
            continue
        assert SD._not_the_type_it_was_given(page) == "", page["url"]


# --------------------------------------------------------------------------
# The other direction: a shelf is never one thing for sale
# --------------------------------------------------------------------------

def _grid_html(count=36, quick_add=False):
    """A grid of item tiles. A tile's name is a link inside a div, so the page
    has no h2 and no h3 and every heading-based listing test comes back
    False - the shape that reached the product check in an earlier build."""
    tiles = "".join(
        "<div class=\"tile\"><a href=\"/product-page/candle-{n}\">Candle {n}</a>"
        "<span>£{n}.00</span>{add}</div>".format(
            n=n + 1,
            add=("<button class=\"add-to-cart\">Add</button>" if quick_add else ""))
        for n in range(count))
    return ("<html><head><title>Shop All</title></head><body><main>"
            "<h1>Shop All</h1>{}</main></body></html>".format(tiles))


def test_a_grid_of_thirty_six_item_tiles_is_not_promoted():
    """With a quick-add button under every tile, and without one."""
    for quick_add in (False, True):
        page = _extract("/shop-all", _grid_html(quick_add=quick_add))
        # Never one thing for sale. Not that it is any particular other thing:
        # `/shop-all` is one segment and the listing vocabulary knows `shop`,
        # so the address alone leaves it `other`. What this row is about is
        # that the page's own content must not promote it.
        assert page["page_type"] != "product", quick_add
        assert page["page_type_evidence"] == ""


def test_a_grid_that_hides_its_prices_is_still_not_promoted():
    """The price ceiling is not the only guard. A grid printing one price, or
    none, is still a grid: it links 36 ways out and its heading is a shelf."""
    page = _extract("/shop-all", _grid_html(quick_add=True).replace(
        "<span>£", "<span>").replace(".00</span>", "</span>"))
    assert page["page_type"] != "product"
    assert page["page_type_evidence"] == ""


def test_a_hosted_platforms_collection_page_is_never_promoted():
    """`/collections/<handle>` renders its items' prices and their quick-buy
    buttons, which is exactly what a content reading sees. The site's own
    address scheme saying "everything filed under this heading" outranks it."""
    html = _grid_html(count=4, quick_add=True).replace(
        "<h1>Shop All</h1>", "<h1>Beeswax candles</h1>")
    page = _extract("/collections/beeswax", html)
    assert page["page_type"] == "category"


def test_a_search_result_address_is_never_promoted():
    page = _extract("/shop/search?q=candle",
                    _product_html("x", "Candle").replace("<h1>", "<h1>Results for "))
    assert page["page_type"] != "product"


def test_a_page_whose_address_names_the_section_is_never_promoted():
    """One buy control on `/shop` itself. The address ends at the grouping
    word, so it is the way into the catalogue, not one thing in it."""
    html = _product_html("x", "Belanja").replace("<h1>x", "<h1>Belanja")
    page = _extract("/shop", html)
    assert page["page_type"] == "category"


def test_a_pricing_page_keeps_the_type_its_address_declared():
    """Only `category` and `other` are overturned. Every other type was
    decided by a slug this audit recognises, and a page that says what it is
    in its own address is not overruled by its furniture."""
    html = _product_html("plans", "Paket Langganan").replace(
        "<title>", "<title>Harga - ")
    page = _extract("/pricing", html)
    assert page["page_type"] == "pricing"


def test_two_buy_controls_are_not_one_thing_for_sale():
    """The count is the rule, not the presence. Asserted directly on the
    function so the boundary cannot drift without a test noticing."""
    evidence = {
        "add_to_cart_controls": {"count": 2, "evidence": ["<form action=\"/cart/add\">"]},
        "meta": {}, "body_text": "Sepatu kanvas. Rp 1.198.000.",
        "headings": {"h1": ["Sepatu kanvas"]}, "links": {}, "jsonld_types": [],
        "title": "Sepatu kanvas",
    }
    assert retype_from_the_page_itself("category", SITE + "/shop/x", evidence)[0] == "category"
    evidence["add_to_cart_controls"]["count"] = 1
    assert retype_from_the_page_itself("category", SITE + "/shop/x", evidence)[0] == "product"


def test_the_price_ceiling_is_the_shared_one():
    """`DISTINCT_PRICE_CEILING` is the library's measured separator between an
    item and a grid, and this reads it rather than carrying its own number."""
    prices = " ".join("Rp {}00.000".format(n + 1) for n in range(DISTINCT_PRICE_CEILING))
    evidence = {
        "add_to_cart_controls": {"count": 1, "evidence": ["<form action=\"/cart/add\">"]},
        "meta": {}, "body_text": "Sepatu kanvas. " + prices,
        "headings": {"h1": ["Sepatu kanvas"]}, "links": {}, "jsonld_types": [],
        "title": "Sepatu kanvas",
    }
    assert retype_from_the_page_itself("category", SITE + "/shop/x", evidence)[0] == "category"


# --------------------------------------------------------------------------
# The second route in: a storefront whose buy button arrives with the script
# --------------------------------------------------------------------------

def test_a_page_declaring_open_graph_product_is_one_thing_for_sale():
    """No buy control in the delivered HTML, because it renders after the
    script runs. `og:type: product` is the page saying the same thing in
    markup, one page one product by construction, and it is spelled the same
    way whatever language the shop trades in."""
    html = ("<html lang=\"id\"><head><title>Retrograde GD | Langkah Kaki</title>"
            "<meta property=\"og:type\" content=\"product\">"
            "<meta property=\"product:price:amount\" content=\"1198000\">"
            "</head><body><main><h1>Retrograde GD Black White</h1>"
            "<p>Sepatu kanvas buatan tangan.</p></main></body></html>")
    page = _extract("/shop/sepatu-retrograde-low-gd", html)
    assert page["page_type"] == "product"
    assert "og:type" in page["page_type_evidence"]


def test_open_graph_product_does_not_promote_a_faceted_listing():
    """Some storefronts stamp the product vocabulary on the collection page
    too. The address still outranks it."""
    html = ("<html><head><title>Beeswax candles</title>"
            "<meta property=\"og:type\" content=\"product\"></head>"
            "<body><main><h1>Beeswax candles</h1></main></body></html>")
    assert _extract("/collections/beeswax", html)["page_type"] == "category"


# --------------------------------------------------------------------------
# The control is markup, so it reads the same in every language
# --------------------------------------------------------------------------

def test_the_buy_control_is_found_with_no_english_on_the_page():
    page = _extract("/shop/" + HANDLES[0], _product_html(HANDLES[0], "Retrograde GD"))
    assert page["add_to_cart_controls"]["count"] == 1
    assert "/cart/add" in page["add_to_cart_controls"]["evidence"][0]


def test_a_form_and_the_button_inside_it_are_one_control():
    """Outermost only. Counting a `<form action="/cart/add">` and the
    `<button class="add-to-cart">` inside it as two would make every product
    page look like a shelf of two things."""
    page = _extract("/shop/" + HANDLES[0], _product_html(HANDLES[0], "Retrograde GD"))
    assert page["add_to_cart_controls"]["count"] == 1


def test_a_link_to_the_basket_is_not_a_control_that_fills_it():
    """`<a href="/cart">` sits in the header of every page on the site."""
    html = ("<html><head><title>Tentang Kami</title></head><body>"
            "<header><a href=\"/cart\">Keranjang</a></header>"
            "<main><h1>Tentang Kami</h1><p>Kami membuat sepatu.</p></main>"
            "</body></html>")
    page = _extract("/tentang-kami", html)
    assert page["add_to_cart_controls"]["count"] == 0


def test_the_two_control_names_that_carry_no_verb():
    """The commonest hosted store platform submits with
    `<button type="submit" name="add">`; a plugin-based one with
    `<input name="add-to-cart">`. Neither spells a verb anywhere."""
    for control in ('<button type="submit" name="add">Beli</button>',
                    '<input type="submit" name="add-to-cart" value="12">'):
        html = ("<html><head><title>Sepatu | Langkah Kaki</title></head><body><main>"
                "<h1>Sepatu Kanvas</h1><p>Rp 1.198.000</p>{}"
                "</main></body></html>".format(control))
        page = _extract("/shop/sepatu-kanvas", html)
        assert page["add_to_cart_controls"]["count"] == 1, control
        assert page["page_type"] == "product", control


# --------------------------------------------------------------------------
# The page the site hid, and the check that reports hiding
# --------------------------------------------------------------------------

NOINDEXED = ("<html lang=\"en\"><head><title>Seasonal notice</title>"
             "<meta name=\"robots\" content=\"noindex,nofollow\">"
             "</head><body><main><h1>Seasonal notice</h1>"
             "<p>The reading room closes for stocktaking during the first week of "
             "February. Requests placed before the closure are held at the desk "
             "and can be collected when the room reopens on the following Monday. "
             "Nothing else about the service changes during that week, and the "
             "catalogue stays available online throughout.</p>"
             "<p>Readers with an outstanding loan should renew it before the "
             "closure begins, because renewals cannot be processed while the desk "
             "is unstaffed.</p></main></body></html>")


def test_a_noindexed_page_with_content_is_flagged_whatever_its_type():
    """The shape that produced the defect: the page was typed `other`, which is the one type
    that is graded by the hygiene checks and absent from the noindex check's
    list. `declares_noindex` is keyed on the directive and on nothing else, so
    no page type can slip between the two rules."""
    page = _extract("/notice-2026-02", NOINDEXED)
    assert page["page_type"] == "other", "the type that produced the defect"
    assert page.get("skipped") is None, "it holds content, so it is still graded"
    assert page["declares_noindex"] is True


def test_the_directive_is_read_from_all_three_places_it_can_live():
    generic = _extract("/a", NOINDEXED)
    per_crawler = _extract("/b", NOINDEXED.replace(
        "name=\"robots\"", "name=\"googlebot\""))
    header = extract_page(
        url=SITE + "/c", final_url=SITE + "/c", status=200,
        headers={"content-type": "text/html", "x-robots-tag": "noindex"},
        html=NOINDEXED.replace(
            "<meta name=\"robots\" content=\"noindex,nofollow\">", ""),
        redirect_chain=[], elapsed_ms=9, depth=1, source="link", origin=SITE)
    for page in (generic, per_crawler, header):
        assert page["declares_noindex"] is True, page["url"]


def test_a_page_that_hides_nothing_says_so():
    page = _extract("/notice-2026-03", NOINDEXED.replace(
        "<meta name=\"robots\" content=\"noindex,nofollow\">", ""))
    assert page["declares_noindex"] is False


def test_every_record_carries_the_answer_including_a_set_aside_one():
    """The field is on every record, so a reader never has to tell "no
    directive" apart from "nobody asked"."""
    empty = _extract("/export.ics", "")
    assert empty.get("not_gradable") is True
    assert "declares_noindex" in empty
    for page in _shop_snapshot():
        assert "declares_noindex" in page
