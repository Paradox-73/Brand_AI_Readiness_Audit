# -*- coding: utf-8 -*-
"""Four things one report said about a page that the page itself contradicts.

Every one of these was published at the top of a real report, and in each case
the page held the evidence against it.

*A price the page prints, reported as contradicting the markup.* A collection
page printed `Regular price INR 18,450.00` beside an item and declared
`content="18450.0"` for it. The joint-top finding, at high severity, said the
declared price "is not among the prices the page prints beside that name (500,
1000, 2000, 2001)" - four figures out of the filter sidebar. A window of fixed
width around an item's name is a guess about where its tile ends, and on a grid
of forty items it lands in a neighbour.

*A recommendation carousel's prices read as the page's own.* The same finding
named one item's price on three unrelated product URLs - pages that do not sell
that item.

*An unfinished page told to add FAQPage markup.* A shop's `/pages/faqs` whose
entire main content is the string `FAQs text goes here` was named as a page
that answers questions, in the same report that called that URL too short to
quote from.

*A declared language contradicted by the letters, detected and never reported.*
A site serving `<html lang="en-US">` over Arabic pages had the contradiction
detected - the skip text says so in words - and used only to decide which
checks not to run.

*A description one finding calls wrong, offered by another as the fix.* A
library's paste-ready Organization block carried an opening-hours dump as its
`description`, taken from the meta description the same report reported as a
defect.

Every brand, item, host and address below is invented.
"""

from __future__ import annotations

import importlib.util
import json
import os
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
             "sd_for_grid_price_tests")
COMPOSE = _module(os.path.join(SCRIPTS, "compose_report.py"),
                  "compose_for_grid_price_tests")

SHOP = "https://a-handloom-shop-that-does-not-exist.test"
LIBRARY = "https://a-library-that-does-not-exist.test"
KITCHEN = "https://a-kitchen-that-does-not-exist.test"

BRAND = {"name": "Threadmarket"}


def _page(path, site=SHOP, **extra):
    """A snapshot page carrying only the keys these checks read."""
    page = {
        "url": site + path,
        "page_type": "other",
        "status": 200,
        "title": "",
        "headings": {},
        "sections": [],
        "paragraphs": [],
        "body_text": "",
        "control_text": "",
        "links": {"internal": []},
        "jsonld": [],
        "jsonld_types": [],
        "og": {},
    }
    page.update(extra)
    return page


def _offer(name, price):
    return {"@type": "Product", "name": name,
            "offers": {"@type": "Offer", "price": price, "priceCurrency": "INR"}}


def _consistency(pages, brand=None):
    result = SkillResult("structured-data-audit")
    SD._check_consistency(result, pages, brand if brand is not None else dict(BRAND))
    return result


# --------------------------------------------------------------------------
# 1. A number the page prints is not a number the page contradicts
# --------------------------------------------------------------------------

# The sidebar a faceted grid renders above its tiles, and the tile itself. The
# item's name and its price are more than `_PRICE_NEAR_NAME_CHARS` apart,
# because the tile prints the weave, the fibre and the dispatch time between
# them - which is what put the sidebar's four figures inside the window and the
# item's own price outside it.
_TILE_COPY = "Woven on a pit loom in undyed cotton and dispatched within a week. " * 5
_GRID_TEXT = ("Refine by price INR 500 INR 1000 INR 2000 INR 2001 "
              "Marsh Reed Tote " + _TILE_COPY + "Regular price INR 18450.00")


def _grid_page():
    return _page("/collections/new", page_type="category",
                 title="New arrivals", headings={"h1": ["New arrivals"]},
                 body_text=_GRID_TEXT,
                 jsonld=[_offer("Marsh Reed Tote", "18450.0")],
                 jsonld_types=["Product"])


def test_the_window_really_does_read_the_sidebar_rather_than_the_tile():
    """The measurement the fix rests on, stated before the fix is asserted.

    The prices found beside the item's name are the filter sidebar's, and the
    price printed for the item is not among them. Anything below this line is
    about what the check does with that, not about whether it happens.
    """
    beside = SD._prices_stated_for({"@type": "Product", "name": "Marsh Reed Tote"},
                                   _grid_page(), ["Threadmarket"])
    values = sorted(SD._price_value(p) for p in beside)
    assert values == [500.0, 1000.0, 2000.0, 2001.0]


def test_a_price_the_page_prints_is_never_reported_as_contradicting_it():
    """The finding, verbatim: `Offer price 18450.0 for "..." is not among the
    prices the page prints beside that name (500, 1000, 2000, 2001)`. The page
    prints `Regular price INR 18450.00` for that item and the markup carries
    the same number."""
    result = _consistency([_grid_page()])
    assert not result.findings, [f["evidence"] for f in result.findings]


def test_a_price_printed_nowhere_on_the_page_is_still_a_conflict():
    """The guard. Reading the whole page for the declared figure widens what
    counts as printed; it may not silence the check."""
    page = _grid_page()
    page["jsonld"] = [_offer("Marsh Reed Tote", "3300.0")]
    result = _consistency([page])
    assert result.findings, "3300 is printed nowhere on this page"
    assert result.findings[0]["severity"] == "high"


# --------------------------------------------------------------------------
# 2. One tile of a grid ends where the next item's name begins
# --------------------------------------------------------------------------

def test_the_span_around_a_name_stops_at_the_next_items_name():
    """`_tile_around` on its own: the boundary a grid actually has."""
    text = "Marsh Reed Tote no price here Ochre Field Bag INR 4200.00"
    kept = SD._tile_around(text, 0, len("Marsh Reed Tote"), ["Ochre Field Bag"])
    assert "4200" not in kept
    assert kept.startswith("Marsh Reed Tote")


def test_a_neighbouring_tiles_price_is_not_this_items_price():
    """The second cause traced: the text window lands in a neighbouring
    tile. This grid prints a price for one item and none for the other, and
    the priceless item's markup was graded against its neighbour's figure."""
    page = _page("/collections/bags", page_type="category",
                 title="Bags", headings={"h1": ["Bags"]},
                 body_text=("Marsh Reed Tote sold out for this season "
                            "Ochre Field Bag Regular price INR 4200.00"),
                 jsonld=[_offer("Marsh Reed Tote", "3900.0"),
                         _offer("Ochre Field Bag", "4200.0")],
                 jsonld_types=["Product"])
    result = _consistency([page])
    assert not result.findings, (
        "the page prints no price for the first item, so its markup "
        "contradicts nothing: {}".format([f["evidence"] for f in result.findings]))


# --------------------------------------------------------------------------
# 3. A carousel sells nothing on the page it appears on
# --------------------------------------------------------------------------

def test_a_recommendation_carousels_item_is_not_graded_against_this_page():
    """The finding reported one item's price on three unrelated product URLs.
    Those pages carry that item in a "you may also like" strip; they do not
    sell it, and they print no price for it."""
    page = _page("/products/marsh-reed-tote", page_type="product",
                 title="Marsh Reed Tote", headings={"h1": ["Marsh Reed Tote"]},
                 body_text=("Marsh Reed Tote Regular price INR 12000.00. "
                            "You may also like Ochre Field Bag"),
                 jsonld=[_offer("Marsh Reed Tote", "12000.0"),
                         _offer("Ochre Field Bag", "9850.0")],
                 jsonld_types=["Product"])
    result = _consistency([page])
    assert not result.findings, [f["evidence"] for f in result.findings]


def test_the_pages_own_subject_is_still_graded():
    """The same page, with the subject's own markup wrong. Ignoring the
    carousel may not stop the check reading the item the page is about."""
    page = _page("/products/marsh-reed-tote", page_type="product",
                 title="Marsh Reed Tote", headings={"h1": ["Marsh Reed Tote"]},
                 body_text="Marsh Reed Tote Regular price INR 12000.00.",
                 jsonld=[_offer("Marsh Reed Tote", "999.0")],
                 jsonld_types=["Product"])
    assert _consistency([page]).findings


# --------------------------------------------------------------------------
# 4. A page with nothing on it answers no questions
# --------------------------------------------------------------------------

_PLACEHOLDER_PAGE_TEXT = "FAQs text goes here"


def _faq_result(pages):
    result = SkillResult("structured-data-audit")
    by_type = {}
    for page in pages:
        by_type.setdefault(page["page_type"], []).append(page)
    SD._check_faq(result, by_type, pages)
    return result


def test_an_unfinished_page_is_not_a_page_of_questions_and_answers():
    """`FAQs text goes here` - nineteen characters, no question asked and none
    answered. The same report called this URL "too little text to be quoted"
    while naming it as a page that answers questions."""
    page = _page("/pages/faqs", page_type="faq", title="FAQs",
                 headings={"h1": ["FAQs"]},
                 body_text=_PLACEHOLDER_PAGE_TEXT)
    assert not SD._answers_questions(page)
    assert not _faq_result([page]).findings


def test_a_definition_list_of_real_questions_is_still_reported():
    """The branch this gate sits on exists for a `<dl><dt>question<dd>answer`
    block, which carries no heading for a heading reader to find. A page that
    really holds one still has to be named."""
    page = _page("/help/faq", page_type="faq", title="Delivery FAQ",
                 headings={"h1": ["Delivery FAQ"]},
                 body_text=("Do you ship outside the country? "
                            "Yes, to fourteen countries, at cost, in about ten days. "
                            "Can I return a made-to-order piece? "
                            "Yes, within thirty days, unmarked and in its wrapping."))
    assert SD._answers_questions(page)
    assert _faq_result([page]).findings


# --------------------------------------------------------------------------
# 5. A declared language the letters contradict
# --------------------------------------------------------------------------

# Two sentences of Arabic prose, long enough for `dominant_script` to decide.
# Written as escapes so this file stays ASCII and cannot lose a character in
# transit; it reads "the restaurant is open every day from ten in the morning
# until midnight, and delivery is available in every district of the city".
_ARABIC = (
    "المطعم مفتوح "
    "يوميا من العاشرة "
    "صباحا حتى منتصف "
    "الليل والتوصيل "
    "متاح في جميع "
    "أحياء المدينة"
)


def _arabic_site(lang="en-US"):
    return [_page(path, site=KITCHEN, page_type=page_type, lang=lang,
                  title=_ARABIC[:30], body_text=_ARABIC)
            for path, page_type in (("/", "home"), ("/about", "about"),
                                    ("/menu", "other"))]


def _lang_result(pages):
    result = SkillResult("structured-data-audit")
    SD._check_lang(result, pages)
    return result


def test_english_declared_over_arabic_text_is_reported():
    """The tool already detected this and printed it in a skip line - "declared
    as en, contradicted by page text written in the arabic script" - and used
    it only to choose which checks to leave out. The `lang` check fired on
    nothing, because it asked whether the attribute was absent."""
    result = _lang_result(_arabic_site())
    assert result.findings, "the contradiction was detected and never raised"
    finding = result.findings[0]
    assert finding["root_cause"] == "missing-lang"
    assert "arabic" in finding["evidence"]
    assert len(finding["affected_pages"]) == 3
    assert not any(entry["check"] == "html-lang-attribute"
                   for entry in result.not_applicable), \
        "a page declaring the wrong language may not also be passed"


def test_a_language_that_agrees_with_the_letters_is_not_reported():
    """The guard. A site whose `lang` matches its own script is a site with no
    finding here at all."""
    pages = [_page("/", page_type="home", lang="en",
                   title="Handloom bags",
                   body_text="Every bag is woven on a pit loom in undyed cotton "
                             "and finished by hand in our own workshop.")]
    result = _lang_result(pages)
    assert not result.findings
    assert [entry["check"] for entry in result.not_applicable] == ["html-lang-attribute"]


def test_a_missing_attribute_is_still_the_finding_it_was():
    """The check's original claim, unchanged."""
    pages = [_page("/", page_type="home",
                   body_text="Every bag is woven on a pit loom in undyed cotton.")]
    result = _lang_result(pages)
    assert [f["root_cause"] for f in result.findings] == ["missing-lang"]
    assert result.findings[0]["id_hint"] == "missing-html-lang"


# --------------------------------------------------------------------------
# 6. A value one finding calls wrong is not another finding's fix
# --------------------------------------------------------------------------

_HOURS_DUMP = ("Hours: Monday: 12-8pm Tuesday: 10am-6pm Saturday: closed. "
               "Phone: 555 0100. Email: General E-Mail Library")


def _org_payload(pages, brand=None):
    snapshot = {"origin": LIBRARY, "site": "a library", "pages": pages,
                "brand": brand or {"name": "Fernhill Public Library"}}
    snippet = SD._org_snippet(snapshot, pages, snapshot["brand"])
    return json.loads(snippet.split(">\n", 1)[1].rsplit("\n</script>", 1)[0])


def test_an_opening_hours_dump_is_not_the_organisations_description():
    """The block published `"description": "Hours: Monday: 12-8pm ... Email:
    General E-Mail Library"`. An assistant asked what this library is gets a
    timetable."""
    pages = [_page("/", site=LIBRARY, page_type="home",
                   meta_description=_HOURS_DUMP)]
    description = _org_payload(pages)["description"]
    assert "12-8pm" not in description
    assert description.startswith("<") and description.endswith(">"), \
        "with nothing usable to say, the block says so the way `logo` does"


def test_a_description_the_same_report_calls_duplicated_is_not_republished():
    """The hygiene check in this same skill prints "N of M pages share a meta
    description with another page ... the most repeated reads ...". Handing
    that string back inside paste-ready code is the report contradicting
    itself."""
    repeated = "Books, films and music for everyone in the county."
    pages = [_page("/", site=LIBRARY, page_type="home", meta_description=repeated),
             _page("/about", site=LIBRARY, page_type="about", meta_description=repeated),
             _page("/hours", site=LIBRARY, meta_description=repeated)]
    assert "<" in _org_payload(pages)["description"]


def test_a_description_only_the_homepage_carries_is_still_published():
    """The guard. A site that wrote one sentence about itself gets that
    sentence, not a placeholder."""
    pages = [_page("/", site=LIBRARY, page_type="home",
                   meta_description="A lending library and local archive for the "
                                    "county, open six days a week."),
             _page("/hours", site=LIBRARY, meta_description="Opening times by branch.")]
    assert _org_payload(pages)["description"].startswith("A lending library")


# --------------------------------------------------------------------------
# 7. The one string in the report that has to survive the entity decode
# --------------------------------------------------------------------------

# What a theme emits when it runs a value through an HTML-escape filter inside
# a script element. The apostrophe is five characters, and that is the defect.
_ESCAPED_NAME = "Ashfold&#39;s Wholefoods"


def _escaped_markup_page():
    return _page("/", page_type="home", title="Ashfold's Wholefoods",
                 body_text="A wholefoods shop and bakery on the market square.",
                 jsonld=[{"@type": "Organization", "name": _ESCAPED_NAME,
                          "url": SHOP + "/"}],
                 jsonld_types=["Organization"])


def _escaped_value_finding():
    result = SkillResult("structured-data-audit")
    SD._check_jsonld_validity(result, [_escaped_markup_page()])
    return next(f for f in result.findings
                if f["id_hint"] == "jsonld-values-escaped-as-html")


def test_the_escaped_value_finding_still_names_the_escaped_value():
    """The finding exists to say that a value carries `&#39;` where an
    apostrophe belongs."""
    assert _ESCAPED_NAME in _escaped_value_finding()["evidence"]


def test_the_rendered_report_still_prints_the_character_reference():
    """`compose_report` decodes character references at the render boundary so
    no report prose prints `&amp;amp;` at a reader, and it skips code spans.
    Quoted in plain double quotes, this finding's own example would be decoded
    to an apostrophe - and a finding whose entire point is that the value is
    written `&#39;` would print a value with nothing wrong with it."""
    snapshot = {
        "site": "a-handloom-shop-that-does-not-exist.test",
        "origin": SHOP,
        "brand": {"name": "Ashfold's Wholefoods",
                  "host": "a-handloom-shop-that-does-not-exist.test"},
        "pages": [{"url": SHOP + "/", "final_url": SHOP + "/", "status": 200,
                   "title": "A page", "page_type": "content",
                   "body_text": "A sentence long enough to count as a page read.",
                   "paragraphs": ["A sentence long enough to count as a page read."]}],
        "sitemaps": [],
        "crawl": {"pages_crawled": 1, "pages_ok": 1, "render_mode": "static",
                  "user_agent": "test-agent", "elapsed_s": 1.0, "notes": []},
    }
    finding = _escaped_value_finding()
    finding["affected_pages"] = [SHOP + "/"]
    finding["affected_page_count"] = 1
    report = COMPOSE.compose(
        snapshot,
        [{"skill": "structured-data-audit", "findings": [finding], "signals": {},
          "checks_run": [], "not_applicable": [], "fired_checks": [],
          "extra_requests_made": 0}],
        audited_at="2026-01-01T00:00:00Z")
    markdown = COMPOSE.render_markdown(report)
    assert _ESCAPED_NAME in markdown, (
        "the renderer resolved the very entity this finding reports; the "
        "reader is shown a value with nothing wrong with it")
