#!/usr/bin/env python3
"""structured-data-audit: are the facts machine-readable? (mechanism C)

Expectations are keyed to the page type detected during the crawl, so the skill
never asks for Product schema on a site that sells nothing. Makes no network
requests.

Usage:
    python check.py --snapshot snapshot.json --out structured-data-audit.findings.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
from urllib.parse import parse_qs, urljoin, urlparse
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))

# The marketplace's shared library: one definition of the finding schema, the
# root-cause vocabulary and the page-type detector, so six skills cannot drift
# apart on any of the three.
#
# Looked for beside this file first, then in the orchestrator. `package.py`
# writes a copy into every skill directory when it builds the submission, so a
# skill folder lifted out on its own still runs; the checkout keeps a single
# source of truth so the copies cannot diverge from it.
_SHARED_CANDIDATES = (
    _HERE,
    os.path.join(os.path.dirname(os.path.dirname(_HERE)), "audit-orchestrator", "scripts"),
)
_SHARED = next(
    (path for path in _SHARED_CANDIDATES
     if os.path.isfile(os.path.join(path, "audit_common.py"))),
    None,
)
if _SHARED is None:
    raise SystemExit(os.linesep.join([
        "Cannot find the shared library that this skill depends on.",
        "  Looked in: " + "; ".join(_SHARED_CANDIDATES),
        "",
        "This skill reads audit_common.py, which should sit either beside this",
        "file or in skills/audit-orchestrator/scripts/. Copy the whole",
        "marketplace, or rebuild the submission with package.py.",
        "",
        "To perform these checks without it, follow the Procedure section of",
        "this skill's SKILL.md by hand. It states every check in prose and",
        "produces the same findings.",
    ]))
sys.path.insert(0, _SHARED)

from audit_common import (
    # Imported rather than copied, underscore and all. "Is this an HTML
    # character reference" was written out twice, here and in the shared
    # library, and two spellings of one test is the drift this codebase exists
    # to avoid: `_as_published` decodes with the shared answer, and
    # `_escapes_html_inside_json` reports on the local one, so a reference the
    # decoder handles and the reporter does not would be a value silently
    # cleaned and never named.
    _CHARACTER_REFERENCE_RE,  # noqa: E402
    # The three address readings inside `is_listing_page` that this file has to
    # ask on their own. `_why_this_page_is_a_list` lets a page's own `Product`
    # block overrule an *inferred* listing and never a *declared* one, and
    # `is_listing_page` returns one boolean for six readings - three of them the
    # site's own address scheme and three of them inferences. Imported rather
    # than restated, underscore and all, for the reason `_CHARACTER_REFERENCE_RE`
    # is: two spellings of one test is the drift this codebase exists to avoid.
    _LISTING_PATH_RE,  # noqa: E402
    _YEAR_SEGMENT_RE,  # noqa: E402
    LISTING_SEGMENTS,  # noqa: E402
    _LISTING_JSONLD_TYPES,  # noqa: E402
    unclassified_note,  # noqa: E402
    as_published, brand_forms, brand_pattern, comparison_key, DEEP_TYPES,
    defining_sentence, detect_site_language, DISTINCT_PRICE_CEILING,
    claimed_by_the_site, could_be_an_account, cut_at_read_cap,
    dominant_script, example_urls,
    find_prices, is_faceted_listing, is_listing_page, is_multi_location,
    location_pages_with_an_address,
    is_question_heading, is_search_result_page, jsonld_type_names, listing_key,
    BRANCH_PAGE_MINIMUM, fold_declared_duplicates,
    load_snapshot, name_appears_in, names_match, ORG_IDENTITY_TYPES,
    pages_of, pages_with_their_own_address,
    plural, price_value, PRICE_NUMBER_RE, primary_subtag, profile_names_brand,
    same_site, sample,
    script_counts, sentences, site_kind, SkillResult, speaks_about_itself,
    speaks_for_the_brand, strip_www, publishing_platform, template_change_steps,
    where_the_template_is, who_edits_the_template,
    truncate, LOCAL_BUSINESS, PUBLIC_BODY, ONLINE_SELLER, PROJECT, PUBLICATION,
    SEVERITY_RANK, EFFORT_DIVISOR, CONFIDENCE_LEVELS,
    UNSPACED_SCRIPTS, VISITABLE_JSONLD_TYPES, written_in_the_same_script
)

SKILL = "structured-data-audit"

# `ORG_IDENTITY_TYPES` and `VISITABLE_JSONLD_TYPES` are imported above rather
# than restated here. Both used to be local copies, and the visitable copy had
# gone stale: it was missing `fastfoodrestaurant`, `hospital`, `library` and
# `museum`, and still carried `"groceryStore".lower()` from the hand-copy.
# `_identity_type` reads it to choose between `Organization` and
# `LocalBusiness`, so a museum or a hospital whose markup correctly says
# `Museum` or `Hospital` was handed a paste-ready snippet naming the wrong
# identity type.
#
# A "product page" is whatever the page-type detector calls one, and schema.org
# has a specific type for most of the things sold that way. `Book` is what a
# bookshop marks a title with, `SoftwareApplication` what a SaaS pricing page
# uses, `Course` what a training provider uses, `Event` what a ticket page uses,
# `MenuItem` what a restaurant uses. All are correct, all are richer than
# `Product`, and a check that knew only `Product` told each of them to replace
# working markup with something more generic.
PRODUCT_TYPES = {"product", "productgroup", "productmodel", "vehicle",
                 "individualproduct", "book", "softwareapplication",
                 "mobileapplication", "webapplication", "course", "event",
                 "menuitem", "service", "trip", "ticket"}
# The NewsArticle subtypes Google documents, plus the two shapes a content site
# most often uses instead of `Article`: `Recipe` on a food blog and `Review` on
# a review site. A publisher using the more specific type is doing better than
# one using `Article`, and was being told it had no markup at all.
ARTICLE_TYPES = {"article", "blogposting", "newsarticle", "techarticle", "report",
                 "scholarlyarticle", "liveblogposting", "recipe", "review",
                 "reportagenewsarticle", "opinionnewsarticle", "analysisnewsarticle",
                 "reviewnewsarticle", "backgroundnewsarticle", "askpublicnewsarticle",
                 "advertisercontentarticle", "medicalscholarlyarticle",
                 "socialmediaposting", "discussionforumposting", "podcastepisode",
                 "videoobject"}
# Creative works that are not articles. A post marked up as one of the first
# four is accepted above as marked up, and rightly; none of them promises an
# article's headline, dates and author, so none is graded as an Article with
# holes in it. A guide page declaring `Service`, `WebPage` and `VideoObject`
# was reported as "Article markup is missing ... headline, datePublished,
# dateModified, author", with a snippet putting a `headline` on the video.
_OTHER_CREATIVE_WORK_NAMES = {
    "videoobject": "VideoObject", "podcastepisode": "PodcastEpisode", "recipe": "Recipe",
    "review": "Review", "howto": "HowTo", "course": "Course", "book": "Book",
    "movie": "Movie", "musicrecording": "MusicRecording", "audioobject": "AudioObject",
    "dataset": "Dataset", "softwaresourcecode": "SoftwareSourceCode",
}
_OTHER_CREATIVE_WORKS = set(_OTHER_CREATIVE_WORK_NAMES)
# The types that do promise those four properties.
_ARTICLE_FAMILY = ARTICLE_TYPES - _OTHER_CREATIVE_WORKS
FAQ_TYPES = {"faqpage", "qapage"}

# The subset of PRODUCT_TYPES that schema.org gives a `brand` property. The
# rest are CreativeWork or Event subtypes, where `brand` is not a property at
# all.
_BRANDED_TYPES = {"product", "productgroup", "productmodel", "vehicle",
                  "individualproduct", "service"}

# High-value properties per type. Missing ones are a medium finding, not a
# critical one: the markup exists and works, it is just less useful than it
# could be.
HIGH_VALUE_PROPS = {
    "Organization": ["name", "url", "logo", "description", "sameAs"],
    # No "Product" entry. It listed name, description, image and brand and was
    # never read - _check_product validates the Offer only. A list that grades
    # nothing, while the snippet demanded two of its keys (image and sku, both
    # unfillable on a site with no images), had the tool disagreeing with
    # itself about what mattered. Restore it only alongside a check that reads it.
    "Offer": ["price", "priceCurrency", "availability"],
    "Article": ["headline", "datePublished", "dateModified", "author"],
}

# Title and description lengths. These are the ranges that survive truncation
# in search results and in assistant citations.
TITLE_MIN, TITLE_MAX = 15, 90   # 75 flagged ordinary retail titles; real ones run long
DESC_MIN, DESC_MAX = 50, 165

# What one character of an unspaced script is worth, in characters of a script
# that writes with spaces.
#
# `len(title)` is a count of Python characters, and the range above was set by
# looking at English titles, so the check silently meant "15 to 90 Latin
# characters" everywhere. On a city government's site written in Japanese it
# reported that 40 of 57 titles were too short: the offenders were complete,
# specific titles of the shape "<city> / disaster and crime prevention" and
# "<city> / home", nine and seven characters long, which is a normal and
# informative page title in that script. A Han or Kana character carries
# roughly two to three Latin characters of meaning, so counting them one for
# one makes every such site fail a rule about how much a title says.
#
# Measured per title rather than per site, and from the characters themselves
# rather than from the declared language, so a Japanese page inside an English
# site is measured in its own script and a site that declares no language at
# all is still measured correctly. The snapshot's `prose_checks_apply` flag is
# not the lever here: it marks the checks tuned to English *prose*, and how
# much a title says is measurable in any language once the unit is right.
UNSPACED_CHARACTER_WIDTH = 2.5

# One reader for both sides of every price comparison, in audit_common, so the
# markup and the page can never be parsed by two different rules.
_price_value = price_value


def _format_price(value):
    return "{:.2f}".format(value).rstrip("0").rstrip(".")


def run(snapshot):
    result = SkillResult(SKILL)
    pages = pages_of(snapshot, content_only=True)
    # Markup expectations are keyed to page type, so those checks read content
    # pages. HTML hygiene is not: a `lang` attribute, a <title>, a meta
    # description and Open Graph tags are expected on every page a machine can
    # fetch, and reading only the content pages made these checks describe a
    # site from a handful of it. One report said "2 pages do not declare a
    # language" about a site where 57 of the 60 crawled pages had no `lang`,
    # because the check had silently looked at three of them and the finding
    # never said so. Someone fixing the two named would have left 55 behind.
    all_pages = pages_of(snapshot)
    brand = snapshot.get("brand") or {}

    # A page the browser never reached is the delivered stub, not the page a
    # visitor sees. The crawl sets `render_skipped` on a 200 response carrying
    # almost no text that no render pass covered, and on a client-rendered site
    # the title, the description, the Open Graph tags and the JSON-LD all
    # arrive with the JavaScript. Every check in this skill grades delivered
    # markup, so every one of them would be describing the stub.
    #
    # A software vendor's report counted seven such pages, 45 to 65 characters
    # each, in three separate findings - among them "7 of 28 page(s) have no
    # meta description", about pages a browser shows a description on.
    #
    # The stub itself is a finding, and it belongs to render-readability-audit,
    # which keeps these pages for exactly that reason. Excluded here, and named
    # in a signal so the exclusion is visible rather than silent.
    unread = [p for p in all_pages if p.get("render_skipped")]
    if unread:
        result.signal("pages_not_judged_as_unrendered_stubs",
                      sorted(p["url"] for p in unread)[:10])
        pages = [p for p in pages if not p.get("render_skipped")]
        all_pages = [p for p in all_pages if not p.get("render_skipped")]

    # A page cut off at the crawl's read cap is the first part of a page. Its
    # JSON-LD, its FAQ answers and its closing markup can sit past the cut, so
    # every "this page has no X" read off it would be a statement about bytes
    # nobody fetched - and a JSON-LD block cut in half reads as one that does
    # not parse. Set aside from every check here and named, the way an
    # unrendered stub is above.
    truncated = [p for p in all_pages if cut_at_read_cap(p)]
    if truncated:
        result.signal("pages_not_judged_as_truncated_at_the_read_cap",
                      sorted(p["url"] for p in truncated)[:10])
        cut = {id(p) for p in truncated}
        pages = [p for p in pages if id(p) not in cut]
        all_pages = [p for p in all_pages if id(p) not in cut]

    # A page assembled from what somebody typed is not a document this site
    # published, so there is no markup it is failing to carry. Every check here
    # grades a page against what a page of its kind should declare, and a
    # results page has no kind: it exists for the duration of one query.
    #
    # A tea retailer's report said "3 of 3 product pages have no Product
    # markup", and one of the three was `/shop/search/main/product?...`. Asking
    # that address for a `Product` block is asking the site to publish markup
    # describing a thing that does not exist as a document - and the fix, added
    # to the product template, would put a Product block on every search a
    # visitor ever runs.
    #
    # Excluded from both populations rather than from one check, because the
    # same address was also counted in the title, description, Open Graph and
    # `lang` denominators, where it inflates every "N of M pages" this skill
    # prints. Named in a signal, so the exclusion is visible rather than silent.
    generated = [p for p in all_pages if is_search_result_page(p.get("url") or "")]
    if generated:
        result.signal("search_result_pages_not_graded",
                      sorted(p["url"] for p in generated)[:10])
        typed_out = {id(p) for p in generated}
        pages = [p for p in pages if id(p) not in typed_out]
        all_pages = [p for p in all_pages if id(p) not in typed_out]

    if not pages:
        reason = "no content pages returned HTTP 200"
        # Said as what it is. A crawl that reached nothing but search-results
        # addresses did reach pages; none of them is a document this site
        # publishes, and printing "no content pages returned HTTP 200" about
        # that crawl states something false about the origin.
        if generated:
            reason = ("every content page this crawl reached is a search-results address, "
                      "which is a page assembled from what somebody typed rather than a "
                      "document the site publishes, so there is no markup any of them is "
                      "failing to carry")
        if unread:
            reason = ("every content page crawled delivered almost no text until JavaScript "
                      "ran, so anything graded here would be the delivered stub rather than "
                      "the page a visitor sees. render-readability-audit reports the shell "
                      "itself, which is the finding on a site in this state")
        for name in ("organization-markup", "per-location-markup",
                     "product-markup", "listing-markup", "article-markup",
                     "faq-markup", "breadcrumb-markup", "jsonld-validity",
                     "jsonld-matches-visible-text", "brand-name-in-markup",
                     "title-and-description", "homepage-title",
                     "canonical-declaration",
                     "open-graph-tags", "html-lang-attribute",
                     "website-searchaction-markup", "microdata-and-rdfa"):
            result.skip(name, reason)
        return result

    by_type = defaultdict(list)
    for page in pages:
        by_type[page["page_type"]].append(page)

    # Resolved once and handed to the four checks whose fix ends "edit the
    # template". Every one of them used to end there and stop, on sites whose
    # platform the crawl had already recorded a signal for on every page.
    platform = publishing_platform(snapshot)

    # Every check that ends "edit the template" takes the platform. Five of
    # them did not, and on two sites out of five in one batch of runs the only
    # findings a report carried were theirs - so the report reached its last
    # page without one sentence saying which file or which settings screen the
    # reader was being sent to.
    _check_jsonld_validity(result, pages, platform)
    _check_organization(result, snapshot, pages, by_type, brand, platform)
    _check_per_location_markup(result, snapshot, pages, platform)
    _check_product(result, by_type, brand, platform)
    # After the Product check and separate from it. The pages this reports on
    # are the ones that check turned away, and a finding may only claim the
    # checks its own function registered.
    _check_listing_markup(result, by_type, platform)
    _check_article(result, by_type, platform)
    _check_faq(result, by_type, all_pages, platform)
    _check_breadcrumbs(result, pages, by_type, platform)
    _check_website_searchaction(result, by_type, platform)
    _check_consistency(result, pages, brand, platform)
    # Two values inside the markup disagreeing with each other, which is a
    # different question from markup disagreeing with the visible page and so
    # gets its own function and its own check name. On one real site a
    # `Brand` node named a different string from the `Organization` on 14
    # product pages and no check in this skill read the `Brand` node at all.
    _check_declared_brand_names(result, snapshot, pages, brand, platform)
    # The crawl's own count goes with the pages, because the title may call
    # them "crawled" only where the two agree. See the title below.
    _check_titles_and_descriptions(result, all_pages, crawled=len(snapshot.get("pages") or []))
    # `all_pages`, because the homepage is the front door whether or not the
    # type detector called it a content page, and `_is_the_homepage` falls back
    # to the audited origin when it did not.
    _check_homepage_title(result, snapshot, all_pages, brand, platform)
    _check_canonical_declaration(result, all_pages, platform)
    _check_open_graph(result, all_pages, platform)
    _check_lang(result, all_pages, platform)
    _check_microdata_only(result, all_pages)
    # Last, because it reads the findings the checks above produced. A site
    # that publishes no markup at all fails every one of them at once, and
    # six true findings for one condition is one problem reported six times.
    _fold_absent_markup(result, all_pages)

    result.signal("has_faq_schema", any(_types_on(p) & FAQ_TYPES for p in pages))
    result.signal("has_org_schema", any(_types_on(p) & ORG_IDENTITY_TYPES for p in pages))
    result.signal("page_types_seen", sorted(by_type.keys()))
    result.signal("article_pages", len(by_type.get("article", [])))
    return result


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _types_on(page):
    return {t.lower() for t in page.get("jsonld_types") or []}


def _inline_types_on(page):
    """schema.org types this page declares as Microdata or RDFa.

    A second, independent reading of the same question `_types_on` answers
    from JSON-LD. Without it every schema check had one detector, and a site
    that marks its facts up inline rather than in a script block was told at
    high confidence that it carries no markup at all - which is what the
    mutation "the facts are marked up as microdata rather than JSON-LD" does
    to the fixture, and what a real site using a CMS that emits Microdata
    looks like.
    """
    return {t.lower() for t in page.get("microdata_types") or []}


def _declares_type(page, wanted):
    """Does this page declare any of these types, in either notation?"""
    return bool((_types_on(page) | _inline_types_on(page)) & wanted)


def _nodes_of(page, wanted):
    """Every item of one of these types, in whichever notation the site used.

    JSON-LD, Microdata and RDFa state the same things, and reading only the
    first told a shop whose theme emits `itemprop="price"` that its Product
    block omits the price, the availability and the SKU - all three of them on
    the page, in the notation this audit was not reading. `page_extract`
    shapes an inline item like a JSON-LD node so that one funnel serves all
    three and no check has to know which notation it is grading.
    """
    out = []
    for node in (page.get("jsonld") or []) + (page.get("microdata_items") or []):
        if isinstance(node, dict) and jsonld_type_names(node.get("@type")) & wanted:
            out.append(node)
    return out


# The mark a documentation generator prints beside a heading as its own
# anchor link. It is furniture, not part of the heading, and a snippet built
# from a heading carried it into a paste-ready `"headline"`. Written with
# `chr()` so this file stays ASCII and cannot lose a character in transit:
# pilcrow, section sign, link, anchor and infinity, which are the five these
# generators use.
_HEADERLINK_GLYPHS = (chr(0x00B6) + chr(0x00A7) + chr(0x1F517) + chr(0x2693)
                      + chr(0x221E))
_HEADERLINK_TAIL_RE = re.compile(r"[\s" + _HEADERLINK_GLYPHS + r"]*[" + _HEADERLINK_GLYPHS
                                 + r"][\s" + _HEADERLINK_GLYPHS + r"]*$")

# The same anchor written as a hash. Only where a word runs straight into it,
# so a heading that ends in a real number sign is left alone.
_HEADERLINK_HASH_RE = re.compile(r"(?<=\w)#\s*$")


def _without_headerlink_glyphs(value):
    """A heading without the anchor mark its generator prints beside it.

    A documentation tree's class pages end every heading with a pilcrow, and a
    paste-ready Article block was offered with `"headline": "Journal<pilcrow>"`.
    Same rule as the entity decoding below: nothing this skill emits may carry
    a string it can see is malformed.
    """
    if not isinstance(value, str) or len(value) < 2:
        return value
    trimmed = _HEADERLINK_TAIL_RE.sub("", value)
    trimmed = _HEADERLINK_HASH_RE.sub("", trimmed)
    return trimmed.strip() or value


def _as_published(value):
    """A JSON-LD string value with any HTML character references decoded.

    Inside `<script type="application/ld+json">` there is no HTML parsing, so
    `&#39;` is not an apostrophe: it is those five characters, and that is what
    every consumer reads. A shop whose theme ran values through an HTML-escape
    filter published one identity block saying `"name": "<brand>'s Wholefoods"`
    and two saying `"name": "<brand>&#39;s Wholefoods"`, and the audit read
    them as two different names and told the owner to pick a capitalisation.

    Decoded on the way in, everywhere, rather than only where the cause finding
    below fired. Two reasons. The escaping is a property of the string, so a
    grader that behaved one way when a detector had noticed and another way
    when it had not would be two definitions of "the same name". And the point
    of decoding here is to compare what the site meant, so the fault is
    reported once, as the template bug it is, instead of once per symptom.

    The decode itself is `audit_common.as_published`, which every skill and the
    crawl now share, so no two of them can disagree about what a site
    published. It was written here first; the shared one also fixed a fault
    this copy had, in that a bare `html.unescape` decodes semicolon-less legacy
    references and turns the name "Salt&notes" into "Salt<not sign>tes" - a
    corruption invented by the decoder. What stays local is the headerlink
    glyph, which is a JSON-LD `headline` problem and nothing else's.
    """
    return _without_headerlink_glyphs(as_published(value))


def _escapes_html_inside_json(value):
    """Does this value carry an HTML character reference that should not be there?

    Read from the raw node, before `_as_published` has cleaned anything, which
    is the whole reason the two are separate functions: the graders compare
    decoded strings so one template bug does not become a dozen findings, and
    this reads the same strings undecoded so the template bug itself gets one.
    """
    return isinstance(value, str) and bool(_CHARACTER_REFERENCE_RE.search(value))


def _named(value):
    """The readable identity of a property value: a name, a URL, or an id."""
    if isinstance(value, dict):
        return _as_published(value.get("name") or value.get("url")
                             or value.get("@id") or "")
    return _as_published(value)


def _prop(node, key):
    """Read a property, following one level of nesting and @id-free objects.

    The list branch used to return `first.get("name")` alone, so the two shapes
    real templates emit for a list - a multi-author post declaring
    `"author": [{"@type": "Person", "@id": "...#p", "url": "..."}]`, and a logo
    published as `"logo": [{"@type": "ImageObject", "url": "..."}]` - both read
    as absent, and the site was told to add markup it already carries.
    """
    value = node.get(key)
    if isinstance(value, list):
        # The first entry that says something, not the first entry. A theme
        # loop over unfilled social fields emits
        # `"sameAs": ["", "", "", "<a real profile URL>", ...]`, and reading
        # position zero reported `sameAs` as a property the block omits - on a
        # block carrying three real profile URLs. The owner opens View Source,
        # sees `sameAs` there, and stops believing the rest of the report.
        value = next((v for v in value if _named(v) not in (None, "")), None)
    value = _named(value)
    # `0` is a value: a free tier is an Offer whose price is zero, not an Offer
    # with no price. `""` and `None` are the absences.
    return "" if value is None else value


def _without_private_keys(value):
    """A node without the crawler's own bookkeeping. `_nested_in` is ours.

    It is added to every node lifted out of a parent during extraction, and
    pasting it back into a site's markup would publish a key schema.org has
    never heard of.
    """
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if not str(k).startswith("_")}
    return value


def _declared(node, key):
    """A property exactly as the site published it, object shape and all.

    `_prop` flattens an object down to its name, which is what grading needs
    and the opposite of what a snippet needs: an `author` published as
    `{"@type": "Person", "name": "Hannah Ritchie", "url": ".../team/hannah"}`
    has to go back out with its URL intact. Flattened to the name, or replaced
    by a placeholder, the paste-ready block is worse than the markup the page
    already carries.
    """
    value = node.get(key)
    if isinstance(value, list):
        kept = [_without_private_keys(v) for v in value if v not in (None, "", {}, [])]
        return kept or ""
    value = _without_private_keys(value)
    return "" if value in (None, "", {}, []) else value


def _states_nothing(node, key):
    """Does the block declare this property and put nothing readable in it?

    "Absent" and "present and empty" are different defects with different
    fixes, and calling the second the first is how a report told an owner to
    add a `sameAs` they could see in their own View Source. The first wants the
    property written; the second wants the template's blank fields filled, or
    the loop stopped from emitting `""`.
    """
    return key in node and _prop_anywhere(node, key) == ""


def _blank_entries(node, key):
    """How many values of this property are empty, where some are not.

    The half of the same fault that is invisible from the outside: a block
    whose `sameAs` holds three real URLs and five empty strings passes every
    presence test and publishes five assertions that this organisation is the
    same as nothing at all.
    """
    value = node.get(key)
    if not isinstance(value, list):
        return 0
    return len([v for v in value if _named(v) in (None, "")])


def _deeply_as_published(value):
    """A snippet payload with every HTML character reference in it decoded.

    Unconditional, and applied to every block this skill emits. A snippet is
    the tool's own writing: a character reference inside a JSON string value is
    malformed wherever it was read from, and shipping one means the owner who
    pastes the fix publishes the bug. One report offered a paste-ready
    Organization block reading `"name": "<brand>&#39;s Wholefoods"`, and an
    FAQ block whose answer said `beans &amp; peas` - directly under the step
    "the answer text in the markup must match the answer on that page word for
    word".
    """
    if isinstance(value, dict):
        return {k: _deeply_as_published(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deeply_as_published(v) for v in value]
    return _as_published(value)


def _snippet_block(payload):
    """One paste-ready `<script type="application/ld+json">` block.

    The single place this skill turns a payload into a snippet, so the cleaning
    above cannot be applied to five of the six blocks it emits.
    """
    return '<script type="application/ld+json">\n{}\n</script>'.format(
        json.dumps(_deeply_as_published(payload), indent=2, ensure_ascii=False))


# Where a property is also allowed to live. schema.org lets one value be
# stated in more than one valid place, and a checker that reads only the
# shortest form reports the longer, richer form as absent.
#
# `Offer.priceSpecification` is the case that bit: a restaurant's shop states
# every price as
#
#     "offers": [{"@type": "Offer",
#                 "priceSpecification": [{"@type": "UnitPriceSpecification",
#                                         "price": "30.00",
#                                         "priceCurrency": "GBP"}]}]
#
# which is valid, is what WooCommerce emits, and carries more than a flat
# `price` does. Reading only `offer["price"]` reported eighteen pages of
# correct markup as offers "missing price and priceCurrency", and the fix
# would have had the owner flatten working data.
_ALTERNATIVE_HOMES = {
    "price": ("priceSpecification",),
    "priceCurrency": ("priceSpecification",),
    "lowPrice": ("priceSpecification",),
    "highPrice": ("priceSpecification",),
}


def _prop_anywhere(node, key):
    """The property, from the node or from a nested object allowed to hold it."""
    direct = _prop(node, key)
    if direct != "":
        return direct
    for holder in _ALTERNATIVE_HOMES.get(key, ()):
        nested = node.get(holder)
        if isinstance(nested, dict):
            nested = [nested]
        for candidate in nested or []:
            if isinstance(candidate, dict) and _prop(candidate, key) != "":
                return _prop(candidate, key)
    return ""


def _missing_props(node, props):
    # `not value` counted a price of 0 as a missing price, so a free tier, a
    # free-shipping Offer and a zero-cost sample were each reported as markup
    # with a hole in it. Only an empty string means the property is absent.
    return [p for p in props if _prop_anywhere(node, p) == ""]


def _identity_type(snapshot, pages, facts):
    """Organization, or LocalBusiness when the site is somewhere you can go.

    `LocalBusiness` was chosen whenever a postal address was found anywhere on
    the site. Almost every organisation publishes an address, for legal
    reasons, so this handed a paste-ready snippet declaring a global software
    foundation and an international disaster-relief charity to be local
    businesses - schema.org's type for a single-location storefront, clinic or
    restaurant that customers physically visit.

    `Organization` is the safe answer: it is true of every one of them, and
    `LocalBusiness` is a narrowing claim that has to be earned. Three things
    earn it, any one of which is the site saying so itself rather than us
    inferring it from an address in a footer: the site already declares a
    visitable type somewhere, it publishes opening hours, or the crawl found a
    location page printing a street address of its own, which is what a
    business with premises has.
    """
    # A chain is an Organization that has local businesses, not a local
    # business. A national charity with donation centres and a restaurant group
    # with five hundred branches both declare `LocalBusiness` per branch, which
    # is correct - and both were handed a site-level snippet declaring the whole
    # organisation to be one storefront. Several branches means several
    # addresses, so count them.
    # A list, not a set: `test_every_check_registers_itself_before_it_can_fire`
    # reads these files with an AST walk and counts every `.add(` call as a
    # finding being emitted, which is the right rule for `SkillResult.add`.
    address_forms = []
    # Location pages that print an address of their own, one per address - not
    # pages whose path says `location`. The path is all `page_type` reads, and
    # a language foundation's event calendar keeps one venue listing per
    # meeting at `/events/<calendar>/locations/<id>/`, no street and no
    # postcode on any of them: one such page handed that site a LocalBusiness
    # snippet, and four would have called it a chain.
    location_pages = len(location_pages_with_an_address(snapshot))
    declares_visitable = False
    for page in (snapshot.get("pages") or []):
        for node in page.get("jsonld") or []:
            if not jsonld_type_names(node.get("@type")) & VISITABLE_JSONLD_TYPES:
                continue
            declares_visitable = True
            address = node.get("address")
            if isinstance(address, list):
                address = address[0] if address else None
            if isinstance(address, dict):
                address_forms.append(json.dumps(address, sort_keys=True))

    if len(set(address_forms)) > 1 or location_pages > 1:
        return "Organization"
    # "Is this site a place you can walk into" is one question, and it was
    # being answered twice: here, from three signals, and in `site_kind`, from
    # the whole structure of the site. Two answers to one question drift, and
    # the narrower one drifts wrong - a university library with opening hours,
    # or any body whose address suffix a registry controls, met the three
    # signals above and was handed a site-wide block declaring the whole
    # institution to be a single storefront. `might_be` is the gate rather than
    # `is_certainly`, so a site the classifier cannot place keeps the answer it
    # has today; only a confident determination of something else takes the
    # narrowing claim away.
    if not site_kind(snapshot).might_be(LOCAL_BUSINESS, PUBLIC_BODY):
        return "Organization"
    if declares_visitable or facts.get("opening_hours") or location_pages == 1:
        return "LocalBusiness"
    return "Organization"


# "logo" as a whole word inside a URL, so `/wp-content/logo.svg` counts and
# `/account/logout` does not. A trailing `s` is allowed because a theme that
# keeps its mark in `/assets/logos/mark.png` is still naming it a logo.
_LOGO_IN_URL_RE = re.compile(r"logos?(?![a-z])", re.I)


def _image_url(value):
    """The URL out of a `logo` or `image`, however schema.org lets it be written.

    A string, an `ImageObject` with a `url` or a `contentUrl`, or a list of
    either. `_named` cannot do this job: it reads `name` first, so an
    `ImageObject` carrying `{"name": "Logo", "url": "..."}` came back as the
    word "Logo", which is not an image.
    """
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or value.get("@id")
    value = str(value or "").strip()
    if not value:
        return ""
    # A path or an address, never a caption. A relative `logo.png` is a value
    # the site published and has to survive; the word "Logo" is not an image
    # and must not be pasted into a field a knowledge panel fetches.
    if not (re.match(r"^(?:https?:)?//|^/", value) or "/" in value
            or _IMAGE_EXTENSION_RE.search(value)):
        return ""
    # And an image, not a page. A fashion retailer's Corporation block sets
    # `logo` to its own `/intl` storefront address, and two paste-ready blocks
    # copied that page address into the field a knowledge panel renders as the
    # brand's mark. See `_is_a_page_address`.
    return "" if _is_a_page_address(value) else value


# The file types an image address ends in, before any query string.
_IMAGE_EXTENSION_RE = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|avif|ico|bmp|tiff?|heic)(?:$|[?#])", re.I)

# Path words that name where images are kept, for an image served without an
# extension from a media host or a transform endpoint.
_IMAGE_PATH_WORDS = frozenset({
    "image", "images", "img", "imgs", "media", "asset", "assets", "cdn", "upload", "uploads",
    "files", "static", "logo", "logos", "icon", "icons", "brand", "branding", "photo",
    "photos", "picture", "pictures",
})


def _is_a_page_address(value):
    """Is this URL a web page rather than an image?

    An image address ends in an image type, or sits under a path that names
    where images are kept. Anything else - `https://<shop>/intl`, `/about` -
    is a page, and a page is not a logo whatever field it was put in.
    """
    path = urlparse(str(value or "")).path
    if _IMAGE_EXTENSION_RE.search(path) or _IMAGE_EXTENSION_RE.search(str(value or "")):
        return False
    words = set(re.split(r"[^a-z0-9]+", path.lower()))
    return not (words & _IMAGE_PATH_WORDS) and not str(value).startswith("data:image/")


def _logo_is_a_page(node):
    """The page address a node declares as its `logo`, or ""."""
    raw = node.get("logo") if isinstance(node, dict) else None
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if isinstance(raw, dict):
        raw = raw.get("url") or raw.get("contentUrl") or ""
    raw = str(raw or "").strip()
    return raw if raw and "/" in raw and _is_a_page_address(raw) else ""


def _is_the_homepage(page, snapshot):
    """Is this the site's front door, by its type or by its address?

    Both readings, because a crawl can type the homepage as something else -
    a shop front page types as a listing - and a snapshot assembled by hand
    for one check may carry no `page_type` at all.
    """
    if page.get("page_type") == "home":
        return True
    origin = str((snapshot or {}).get("origin") or "").rstrip("/")
    if not origin:
        return False
    address = str(page.get("final_url") or page.get("url") or "").rstrip("/")
    return bool(address) and address == origin


def _brand_url(declared, snapshot):
    """The address to publish as the brand's own home on the web.

    The site's declared `url` when it names this site, and the audited origin
    otherwise. Same class of error as the logo and the description: a value
    that is real, published on the site, and about somebody else. An
    Organization block naming a parent group's site, a directory listing or a
    campaign microsite would otherwise be pasted back as this site's canonical
    home, and `url` is the property a machine follows to find the entity again.
    """
    origin = str((snapshot or {}).get("origin") or "").rstrip("/")
    home = origin + "/" if origin else ""
    if isinstance(declared, list):
        declared = declared[0] if declared else ""
    if isinstance(declared, dict):
        declared = declared.get("url") or declared.get("@id") or ""
    declared = str(declared or "").strip()
    # Plain http for a site served over https is not the site's address. A
    # national library's identity markup says `"url": "http://nlai.ir"`, its
    # canonical tag says the same, and every page is served at
    # `https://www.<host>/` - so the block pasted the one address a browser is
    # moved away from as the organisation's home. The address the site is
    # served at wins, and `_org_snippet_notes` says the canonical is wrong.
    if declared and origin and urlparse(declared).scheme.lower() == "http" \
            and urlparse(origin).scheme.lower() == "https" and same_site(declared, origin):
        return home
    if declared and (not origin or same_site(declared, origin)):
        # On this site, but not necessarily at its front door. A clinic's only
        # identity block sets `url` to the blog it renders on, so the address a
        # machine follows to find the organisation again is a section of the
        # site. `url` on an identity node is the organisation's home, and the
        # audited origin is the one address that cannot be wrong about it.
        if home and urlparse(declared).path.rstrip("/") != urlparse(origin).path.rstrip("/"):
            return home
        return declared
    return home or declared


# The shared name reducer, so an alt text and a brand name compare the same way
# they do everywhere else in the marketplace.
from audit_common import brand_key  # noqa: E402


def _site_logo(snapshot, pages, brand=None):
    """A URL the site itself presents as its mark, or None if it publishes none.

    This used to invent `<origin>/logo.png` when no og:image was found, and put
    it in a snippet labelled paste-ready beside `name` and `url`, which were
    discovered. Nothing marked one as real and the other as a guess. On a site
    with no images at all that shipped a 404 into structured data, which is
    worse than an obvious placeholder because it looks finished.

    It then took the first og:image on any page, and a museum's block was
    handed the og:image of its `/events` page: a 2.1 MB photograph of a talk,
    which nothing on the site calls a logo, while the homepage's own og:image -
    filename `site-logo` - sat unread. `logo` is the field a knowledge panel
    renders as the brand mark, so a wrong value here is not a document detail;
    it is the picture beside the company's name in every assistant that reads
    the markup.

    An og:image is a social card: the picture a page wants shown when *that
    page* is shared. On an events page, an article or a product page it is a
    picture of the story, not of the publisher, so it can never stand as the
    brand's mark. Three sources can, each read off the front door only, in this
    order - see `_logo_reading`:

      1. a `logo` the site declared in the homepage's own identity markup,
         which is the site saying outright "this is my logo"
      2. an image the homepage presents as its logo, or whose own filename
         calls it one, and which the site puts on more than one page, so it is
         a template asset rather than one illustration inside one article
      3. the homepage's own og:image, and only where its own filename calls it
         a logo

    The filename test on the third source is not a tightening for its own
    sake; it is what that source was found on. The museum case above is a
    homepage og:image whose filename is `site-logo`, and that is what made it
    readable as a mark. Without the test, "the homepage's card is about the
    publisher" is an assumption, and a Japanese municipality's homepage card is
    a 660x660 promotional banner for a travel magazine with a photograph of a
    television personality on it - published, silently, as that council's logo,
    in a report where every other site correctly got the placeholder. A social
    card is whatever the marketing team put there this month; a filename saying
    "logo" is the site calling the file its mark.

    Nothing else, and where none of them exists the caller emits the
    placeholder. A placeholder says "not found on the site, fill this in" and
    costs the owner a minute; a confident wrong URL is published.
    """
    return _logo_reading(snapshot, pages, brand)[0]


def _is_a_favicon(url):
    """Is this address the browser-tab icon rather than a logo?

    A Persian national library's block shipped `"logo":
    "/uploads/1/2025/Dec/13/favicon_1.ico"` - the 16-pixel tab icon, read off a
    news article's `publisher` node. A knowledge panel renders `logo` as the
    brand mark, and an `.ico` is not one.
    """
    path = urlparse(str(url or "")).path.lower()
    return path.endswith(".ico") or "favicon" in path


def _is_a_touch_icon(url):
    """The home-screen icon a phone saves: square, small, and a last resort."""
    return "touch-icon" in urlparse(str(url or "")).path.lower()


def _front_doors(snapshot, pages):
    """The homepage and its language editions, never a sub-section's own home.

    A national library's block shipped `/brasil/e/common/images/img/logo_site.gif`
    - the banner of an exhibition site that lives in one folder of the library's
    site and carries its own logo on its own pages. `_section_depth` sets a
    leading language segment aside, so `/` and `/en/` are front doors and
    `/brasil/` is not.
    """
    seen_pages = {id(p) for p in pages}
    everywhere = list(pages) + [p for p in (snapshot.get("pages") or [])
                                if id(p) not in seen_pages]
    return [p for p in everywhere
            if _is_the_homepage(p, snapshot) and _section_depth(p) == 0], everywhere


def _absolute_image(url, page, everywhere=()):
    """`url` as an absolute address, preferring a spelling the crawl recorded.

    A relative `logo` pasted into a block on another host points nowhere. The
    crawl usually holds the image's absolute address already, in an image
    sample or a logo candidate, and that spelling is used where one ends with
    the same path, so the value stays one the site itself published.
    """
    text = str(url or "").strip()
    if not text or re.match(r"^https?://", text, re.I):
        return text
    path = urlparse(text).path
    for other in everywhere:
        images = other.get("images") if isinstance(other.get("images"), dict) else {}
        recorded = ([c.get("src") for c in (images.get("logo_candidates") or [])
                     if isinstance(c, dict)]
                    + list(images.get("undescribed_sample") or [])
                    + list(images.get("missing_alt_sample") or []))
        for seen in recorded:
            seen = str(seen or "")
            if path and re.match(r"^https?://", seen, re.I) and urlparse(seen).path == path:
                return seen
    base = str((page or {}).get("final_url") or (page or {}).get("url") or "")
    return urljoin(base, text) if base else text


def _logo_reading(snapshot, pages, brand=None):
    """(logo URL or None, a sentence for the fix steps or "").

    Read off the front door only, in the order a site states its own mark:

      1. a `logo` on the homepage's own identity markup - not on a node another
         node names as its publisher or brand, which is a reference
      2. an image the homepage presents as its logo: the crawl's logo
         candidates, which are images whose address, alt text, class or
         container calls them one - a header's home-link image is exactly that
      3. an image on the homepage whose own filename calls it a logo, the
         homepage's og:image included

    Never a favicon, and a touch icon only when nothing else qualifies, with
    the sentence saying so. Always absolute.
    """
    homes, everywhere = _front_doors(snapshot, pages)

    def usable(url):
        return bool(url) and not _is_a_favicon(url)

    touch = None
    for home in homes:
        for node in _nodes_of(home, ORG_IDENTITY_TYPES):
            if not _speaks_for_the_site(node, brand) or _only_referenced(node):
                continue
            declared = _image_url(node.get("logo"))
            if usable(declared):
                if _is_a_touch_icon(declared):
                    touch = touch or _absolute_image(declared, home, everywhere)
                    continue
                return _absolute_image(declared, home, everywhere), ""

    # `page["images"]` is the summary `page_extract` writes, not a list of image
    # elements, so the loop that used to run here iterated a dict and compared
    # its own key names - "count", "missing_alt_count" - against the word logo.
    # It could never match, which is why the og:image branch above was the only
    # one that ever answered. The URL lists inside that summary are the only
    # image addresses the snapshot carries.
    brand_key_ = brand_key(str((brand or {}).get("name") or ""))
    seen = defaultdict(set)
    named_by_alt = set()
    # Which of those the front door itself carries, and which the crawl read
    # as an image the page presents as its logo rather than one whose filename
    # happens to say so.
    home_ids = {id(h) for h in homes}
    on_front, presented = {}, set()
    for page in everywhere:
        images = page.get("images")
        if not isinstance(images, dict):
            continue
        for candidate in images.get("logo_candidates") or []:
            if not isinstance(candidate, dict):
                continue
            url = str(candidate.get("src") or "")
            alt = brand_key(str(candidate.get("alt") or ""))
            # A candidate whose alt text names the brand is the site calling
            # the file its own mark, whatever the file is named.
            if url and brand_key_ and alt and (brand_key_ in alt or alt in brand_key_):
                seen[url].add(page.get("url") or "")
                named_by_alt.add(url)
                presented.add(url)
            elif url and _LOGO_IN_URL_RE.search(url):
                seen[url].add(page.get("url") or "")
                presented.add(url)
            if url and id(page) in home_ids:
                on_front.setdefault(url, page)
        for url in ((images.get("undescribed_sample") or [])
                    + (images.get("missing_alt_sample") or [])):
            if url and _LOGO_IN_URL_RE.search(str(url)):
                seen[str(url)].add(page.get("url") or "")
                if id(page) in home_ids:
                    on_front.setdefault(str(url), page)
    # More than one page, or the whole crawl was one page. A partner strip and
    # an inline diagram both sit on a single page; a header mark is on every
    # page the template renders, and the filename test alone cannot tell those
    # apart.
    #
    # And never an image filed under a section of the site that publishes
    # somebody else's work. A central bank's Organization block shipped
    # `.../bot-magazine/Logo%20Phrasiam.JPG` - the masthead of a magazine it
    # publishes, carried on its Thai pages - because candidates were taken in
    # alphabetical order and "b" sorts before "l", while the bank's own marks
    # sat at `.../logo/logo-top-blue.png` on every English page. Among the rest,
    # the ones the site files as its mark - under a `logo` or `brand` folder, or
    # named for the header - come first, then the ones on the most pages.
    #
    # And only an image the front door carries. A sub-site filed in one folder
    # of the site renders its own banner on every one of its own pages, which
    # passes the "more than one page" test as easily as the real header mark.
    eligible = [url for url in seen
                if url in on_front
                and (len(seen[url]) > 1 or len(everywhere) <= 1)
                and not _filed_under_somebody_elses_section(url)
                and not _is_a_favicon(url)]
    marks = [url for url in eligible if not _is_a_touch_icon(url)]
    if marks:
        best = min(marks, key=lambda url: (0 if url in presented else 1,
                                           0 if url in named_by_alt else 1,
                                           _logo_placement_rank(url), -len(seen[url]), url))
        return _absolute_image(best, on_front[best], everywhere), ""
    touch = touch or next((_absolute_image(url, on_front[url], everywhere)
                           for url in sorted(eligible)), None)

    # The homepage's own social card, only where its filename calls it a logo.
    # See the docstring of `_site_logo` for the municipality whose card was a
    # travel-magazine banner.
    for home in homes:
        og_image = str((home.get("og") or {}).get("og:image") or "")
        if og_image and _LOGO_IN_URL_RE.search(og_image) and not _is_a_favicon(og_image):
            if _is_a_touch_icon(og_image):
                touch = touch or _absolute_image(og_image, home, everywhere)
                continue
            return _absolute_image(og_image, home, everywhere), ""

    if touch:
        return touch, ("`logo` in the block is the site's touch icon, {} - the small square "
                       "icon a phone saves to its home screen. It is the only image the "
                       "homepage presents as its mark, so it is used as a last resort; "
                       "replace it with the logo file itself.".format(touch))
    return None, ""


# Path words for sections of a site that carry other people's material - a
# magazine it publishes, its news, its partners and sponsors - whose images are
# not the site's own mark however their files are named.
_SOMEBODY_ELSES_SECTION_WORDS = frozenset({
    "magazine", "magazines", "news", "blog", "blogs", "press", "event", "events",
    "article", "articles", "partner", "partners", "sponsor", "sponsors", "campaign",
    "campaigns", "story", "stories", "publication", "publications", "award", "awards",
    "client", "clients", "customer", "customers", "member", "members", "brands",
})

# Path words a site files its own mark under, or names it after.
_OWN_MARK_WORDS = frozenset({"logo", "logos", "brand", "branding", "header", "top",
                             "site", "main", "primary", "identity"})


def _path_words(url):
    """The words of an image address's path, split on separators and escapes."""
    path = urlparse(str(url or "")).path.lower().replace("%20", " ")
    return re.split(r"[^a-z0-9]+", path)


def _filed_under_somebody_elses_section(url):
    """Is this image in a folder for a magazine, news, partners and the like?"""
    segments = [s for s in urlparse(str(url or "")).path.lower().split("/") if s][:-1]
    return any(word in _SOMEBODY_ELSES_SECTION_WORDS
               for segment in segments
               for word in re.split(r"[^a-z0-9]+", segment.replace("%20", " ")))


def _logo_placement_rank(url):
    """0 where the address files the image as the site's own mark, else 1."""
    return 0 if set(_path_words(url)) & _OWN_MARK_WORDS - {"logo", "logos"} or \
        any(s in ("logo", "logos", "brand", "branding")
            for s in urlparse(str(url or "")).path.lower().split("/")[:-1]) else 1


# Pages that speak for the whole business. A sentence describing the brand can
# only come from one of these.
#
# A restaurant's paste-ready LocalBusiness block came out with
#
#     "description": "Brawn's blue tote is not only bold in colour but also in
#                     versatility - this bag has it all for your every day
#                     storage needs."
#
# The homepage has no defining sentence, which the same report correctly said
# in a separate finding. So the search fell through, in URL order, to a product
# page, where "Brawn's blue tote **is** not only bold..." matched the pattern
# on the word "is". The report simultaneously said no page states what the
# brand is, and handed the owner markup stating that the restaurant "has it all
# for your every day storage needs".
#
# The value was real, present on the site, and on the wrong subject - which is
# a class the "only observed values" rule does not catch, because the string
# genuinely is observed. The rule it needs is about provenance: a claim about
# the whole brand may only be read off a page that is about the whole brand.
_WHOLE_BRAND_PAGE_TYPES = ("home", "about", "contact")


# What a description has to be before it can be pasted, and the two ways a
# scraped one is not.
#
# A national archive's Organization block came out with
#
#     "description": "En Espanol The National Records Office is the nation's
#                     record keeper. ... available to you, whether"
#
# Two defects in one string. "En Espanol" is the label on the language-switcher
# link, sitting in the same run of visible text as the opening sentence and
# swept in with it; and the string stops mid-clause at "whether", which is
# where the crawl's 600-character paragraph limit landed. Nothing in it is
# invented and none of it can be pasted, which is the whole point of a snippet.
#
# The rule is that a description starts where a sentence starts and ends where
# one ends.
#
# The terminators are the ones `audit_common.sentences` splits on, read from
# the other end: the Latin stops, the full-width stops used with Chinese,
# Japanese and Korean, and the danda, Urdu stop and Arabic question mark. A
# closing quote or bracket may follow any of them.
_SENTENCE_END_RE = re.compile(
    "[.!?。！？।॥۔؟][\"'’”)\\]]*$")

# Punctuation a template puts between a menu label and the text after it.
_LABEL_SEPARATORS = " \t|·•-–—:, "

# How many labels may be stripped off the front. A header stacks two or three -
# a skip link, a language switch, a menu toggle - and never a paragraph of them.
_LEADING_LABELS_MAX = 3

# One or two words and then a spaced separator, at the very front of what is
# left after a link label has been stripped. See `_without_a_leading_link_label`
# for the measurement that put it there.
_LABEL_TAIL_RE = re.compile(
    r"^\S{1,20}(?:\s+\S{1,20})?\s+[|" + chr(0x00B7) + chr(0x2022) + "\\-"
    + chr(0x2013) + chr(0x2014) + r":]\s+")


def _link_labels(pages):
    """Every link label the crawl recorded on these pages, as comparison keys.

    `listing_key` is the same normalisation `is_listing_page` uses to compare a
    heading against a link label, so "this text is a link on the page" has one
    spelling wherever the question is asked.
    """
    labels = set()
    for page in pages:
        for link in _links_on(page):
            key = listing_key(link.get("text"))
            if key:
                labels.add(key)
    return labels


def _without_a_leading_link_label(text, labels, brand_name=""):
    """The text with any menu label the page put in front of it removed.

    Only where the label ended a thought rather than beginning one. A label is
    stripped when the words after it start a new sentence - a capital, with the
    label separated from it by nothing but space or a bullet. "Acme Tools is a
    maker of rope" keeps its opening even though the logo link is labelled
    "Acme Tools", because "is" continues the label instead of following it.

    The brand's own name is never stripped: opening a description with it is
    how a description about a brand is written.
    """
    for _ in range(_LEADING_LABELS_MAX):
        lowered = text.lower()
        found = ""
        for label in labels:
            if len(label) < 2 or len(label) >= len(text):
                continue
            if not lowered.startswith(label):
                continue
            # The label has to end where a word ends, or "en" would be stripped
            # off the front of "energy prices".
            if text[len(label)] not in _LABEL_SEPARATORS:
                continue
            if brand_name and names_match(brand_name, label):
                continue
            rest = text[len(label):].lstrip(_LABEL_SEPARATORS)
            if not rest[:1].isupper():
                continue
            if len(label) > len(found):
                found = label
        if not found:
            break
        text = text[len(found):].lstrip(_LABEL_SEPARATORS)
        # The label may have been a prefix of a longer one. A soap maker's
        # homepage writes "Natural Handmade Soaps Online - Our Soaps are Made
        # from Natural Ingredients", and its logo link is labelled with the
        # first three words of that. Stripping only the label leaves "Online -
        # Our Soaps are Made from...", which starts in the middle of the shop's
        # own name, reads as broken English, and appears nowhere on the site -
        # and it was published as the paste-ready `description`.
        #
        # A word or two and then the separator a template puts between a name
        # and the sentence after it is the rest of that name, not the start of
        # the description. Only reachable once a label has already been
        # stripped, so a description that legitimately opens "Fresh bread -
        # baked daily" is untouched.
        rest = _LABEL_TAIL_RE.sub("", text, count=1)
        if rest and rest != text and rest[:1].isupper():
            text = rest
    return text


def _paste_ready_description(value, labels, brand_name=""):
    """A description that begins and ends at a sentence boundary, or nothing.

    Anything that is not a string is handed back untouched: a site may publish
    `description` as an object, and rewriting one is not this function's job.
    """
    if not isinstance(value, str):
        return value
    text = " ".join(value.split())
    # An ellipsis is a cut mark, ours or the site's: `truncate` appends U+2026
    # at the paragraph limit and plenty of templates write three full stops
    # themselves. Whatever it terminates is a fragment either way.
    cut = False
    for mark in _ELLIPSIS_ENDS:
        if text.endswith(mark):
            text = text[: -len(mark)].rstrip()
            cut = True
            break
    text = _without_a_leading_link_label(text, labels, brand_name)
    parts = sentences(text)
    # One sentence with no full stop is a tagline the site wrote whole - "Fresh
    # bread, baked in the shop since 1904" - and dropping it would lose a true
    # value. It is only a fragment when something followed it, or when a cut
    # mark says it was severed.
    if len(parts) > 1 or cut:
        while parts and not _SENTENCE_END_RE.search(parts[-1]):
            parts.pop()
        text = " ".join(parts)
    # Page furniture flattened into the tag rather than a sentence. An
    # Indonesian shop's team page carries the meta description "Our Team
    # _______________ Meet the people who bring life to our small social
    # enterprise!", and the snippet published it as the company's own
    # `description`: a heading, the rule the theme draws under it, and then
    # the page's first line.
    if _RULE_LINE_RE.search(text) or _SECTION_LABEL_START_RE.match(text):
        return ""
    return text


# A rule a template draws with characters: a run of underscores, dashes, equals
# signs, box-drawing lines or asterisks. No sentence contains one.
_RULE_LINE_RE = re.compile(
    "_{3,}|-{4,}|={3,}|\\*{3,}|~{3,}|[" + chr(0x2013) + chr(0x2014) + chr(0x2500)
    + chr(0x2501) + "]{2,}")

# A section label at the very front, followed by something other than a verb:
# "Our Team Meet the people ...", "About Us We are ...". "Our team is small"
# is a sentence about the team and is left alone.
_SECTION_LABEL_START_RE = re.compile(
    r"^(?:about(?:\s+us)?|our\s+(?:team|story|people|mission|vision|values|history|"
    r"founders?)|meet\s+(?:the|our)\s+(?:team|founders?|people|makers)|who\s+we\s+are|"
    r"contact(?:\s+us)?|get\s+in\s+touch)\b"
    r"(?!\s+(?:is|are|was|were|has|have|began|started|consists|includes)\b)"
    r"\s*[:|" + chr(0x00B7) + chr(0x2022) + r"-]?\s+[A-Z]", re.I)


# --------------------------------------------------------------------------
# What counts as the brand's defining sentence
#
# The rule - four sentence shapes and three tests on whatever they match - is
# `defining_sentence` in the shared library. `fact-extractability-audit` reads
# the same function to decide its `no-entity-definition` finding, and this
# skill reads it for the `description` of the paste-ready Organization block a
# developer is told to copy.
#
# It used to be written twice, once in each skill, because the two cannot
# import each other: they talk only through the snapshot and `compose_report`,
# this skill runs first in `SUB_SKILLS` so its signals are not written yet, and
# `package.py` ships each skill directory as something that runs on its own.
# The weaker copy fed the artefact. An open-source project's block came out
# declaring that the organisation *is* "A new minor release, Example 8.1
# 'Hoare', is now available for download." - the top news item on its own
# homepage - because the rule here accepted any 40-to-300-character sentence
# holding the brand name and the bare word "is". Three findings earlier the
# same report printed what the stricter rule found on the about page: the
# multimedia framework sentence off the about page. One report, two answers to
# one question, and the wrong one in the block that gets published.
#
# What this skill still adds is a filter, not a shape: `_outlives_the_news`
# below, passed to `defining_sentence` as its `accept` callback, because a
# description goes into a site-wide template and a release note is not an
# identity.
# --------------------------------------------------------------------------

def _defining_sentence(text, brand_name, brand=None, accept=None):
    """A quotable one-line definition in `text`, or "".

    The shared rule, under the name this skill has always called it by. A
    rejected match keeps the search going instead of ending it, so one
    unusable definition near the top of a page does not hide the usable one
    below it - see `defining_sentence` in the shared library.
    """
    return defining_sentence(text, brand_name, brand, accept=accept)


# A description outlives the page it was read off, so it has to survive that
# page's own news cycle.
#
# The Organization block goes into a site-wide template and is then read as the
# company's identity by every machine that visits, for as long as nobody edits
# it. A sentence naming a version, a release, a specific calendar date or an
# event is a piece of news: true this month, wrong next quarter, and by then it
# is telling every assistant that the organisation *is* a point release.
#
# This is about the shape of the sentence, not about any site: what it looks
# for is a moment. An identity sentence has none - "<Brand> is a neighbourhood
# wine bar and kitchen" is as true in a year - while "since 1934" names a
# founding rather than an occasion and is left alone, which is why a bare year
# is not one of these patterns.
_NEWS_RE = re.compile(
    # A version, build or issue number, however it is introduced.
    r"\b(?:v|ver|version|release|edition|update|patch|build|issue)\.?\s*\d"
    # A version number written in full. Two parts alone are ambiguous - a price
    # and a rating both look like that - so a bare number needs three.
    r"|\bv\d+(?:\.\d+)+\b|\b\d+\.\d+\.\d+\b"
    # Announcing something as new, imminent, or just over.
    r"|\bnow (?:available|live|open|on sale|shipping|out)\b"
    r"|\b(?:released|releasing|launched|launching|announced|announcing|unveiled|"
    r"unveiling|out now|coming soon|register now|book now)\b"
    r"|\bavailable (?:for download|now|from)\b"
    r"|\blatest (?:version|release|issue|episode|edition|news|update)\b"
    # A specific calendar date, which pins the sentence to one day.
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b",
    re.I)

# A number in dotted form straight after the brand's own name is a version of
# it: "Example 8.1", "Example v2.0". Two parts are enough here, because what
# makes it a version is the name in front of it.
_NEWS_VERSION_AFTER_NAME = r"\s+v?\d+(?:\.\d+)+\b"


def _outlives_the_news(text, brand_name=""):
    """`text` back if it will still be true next quarter, otherwise "".

    Applied to the descriptions this skill *derives* - a page's meta
    description, and the defining sentence read out of its prose - and not to a
    `description` the site declared on its own Organization block. That one is
    the site's own deliberate statement of who it is, and a snippet that
    quietly drops it deletes real data from the block the owner is told to
    paste.
    """
    if not isinstance(text, str) or not text.strip():
        return ""
    if _NEWS_RE.search(text):
        return ""
    for form in brand_forms(brand_name):
        pattern = brand_pattern(form)
        if pattern and re.search(
                r"\b(?:{})\b{}".format(pattern, _NEWS_VERSION_AFTER_NAME), text, re.I):
            return ""
    return text


def _brand_definition(pages, brand):
    """The site's own sentence defining itself, if it has written one.

    `fact-extractability-audit` looks for exactly this - "<Brand> is a ..." -
    and the audit prints it in the report. The Organization snippet was still
    emitting `<one sentence: what you do, for whom>` while the answer sat two
    sections above it.

    Read only from pages that speak for the business. Falling through to any
    page that happened to contain the brand name and a copula published a
    product blurb as the company's own description.

    Judged by `_defining_sentence`, which is the rule the other skill uses; see
    the block comment above it. The rule that used to sit here - the brand name
    and the bare word "is", somewhere in a sentence of 40 to 300 characters -
    read a homepage's top news item as the organisation's identity.
    """
    name = (brand or {}).get("name") or ""
    if not name:
        return ""
    # The front door's language first, then any language. A central bank's
    # front door is in Thai and says nothing that defines it; its English about
    # page opens "The Bank of Thailand is a public institution with a mandate
    # ...", which the other skill quoted three sections earlier, while the
    # Organization block's `description` said "neither the homepage nor the
    # about page carries a description". The language rule exists to stop an
    # edition's line being chosen over the front door's own; where the front
    # door has none, the site's own definition in another of its languages is
    # still the site defining itself, and a placeholder is not better.
    ordered = _pages_that_speak_for_the_site(pages)
    ordered += [p for p in _pages_that_speak_for_the_site(pages, any_language=True)
                if not any(p is q for q in ordered)]
    # A definition that is news is skipped and the search continues.
    # "<Brand> 3.0 is a major new release" is a definition by shape, and the
    # sentence the site means by it sits further down the same page.
    lasting = lambda sentence: bool(_outlives_the_news(sentence, name))  # noqa: E731
    for page in ordered:
        for paragraph in (page.get("paragraphs") or [])[:12]:
            # The whole paragraph, not a sentence at a time: the tests that
            # separate a definition from a clause inside somebody else's
            # sentence need the words on either side of the match.
            found = _defining_sentence(paragraph, name, brand, accept=lasting)
            if found:
                return found
    return ""


# The labels a template prints in front of a contact detail. Two or more of
# them, each followed by a colon, is a details table flattened into one
# attribute rather than a sentence about the organisation.
_DETAILS_LABEL_RE = re.compile(
    r"\b(hours?|opening hours|phone|telephone|tel|fax|e-?mail|address|location|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*:", re.I)

# How many of those labels make it a table. Two: one "Hours:" inside a real
# sentence about a shop is how a shop describes itself.
_DETAILS_LABELS_MINIMUM = 2


def _reads_as_a_details_table(text):
    """Is this a list of contact details rather than a description?

    A public library's `Organization` block carried, as its `description`,
    "Hours: Monday: 12-8pm ... Email: General E-Mail Library". It is the site's
    real meta description and it is not a description of the organisation: an
    assistant asked what this library is gets an opening-hours dump.
    """
    if not isinstance(text, str):
        return False
    return len(set(m.group(1).lower()
                   for m in _DETAILS_LABEL_RE.finditer(text))) >= _DETAILS_LABELS_MINIMUM


def _this_report_calls_this_description_wrong(description, pages):
    """Would this same report name this meta description as a defect?

    A value one finding calls wrong may not be published by another finding as
    a paste-ready fix, and the library above is exactly that: the same report
    reported that site's meta descriptions as a defect and then offered one of
    them back inside the block the reader is told to copy.

    Two tests, both of them the hygiene check's own. The first is the sentence
    that check prints - "N of M pages share a meta description with another
    page... the most repeated reads ..." - so a description more than one
    crawled page carries is one this report has already called a defect. The
    second is what a description is for.
    """
    if _reads_as_a_details_table(description):
        return True
    return len([p for p in pages if p.get("meta_description") == description]) > 1


def _site_description(pages):
    """The site's own description of itself, from a page that speaks for it.

    Same provenance rule as `_brand_definition`, and the same reason: falling
    through to any page with a meta description would put a single product's
    marketing line into the site-wide Organization block on exactly the sites
    whose homepage has no description - which is every site this finding
    fires on.

    A description this report separately calls a defect is skipped rather than
    republished; see `_this_report_calls_this_description_wrong`.
    """
    for page in _pages_that_speak_for_the_site(pages):
        if not page.get("meta_description"):
            continue
        if _this_report_calls_this_description_wrong(page["meta_description"], pages):
            continue
        return page["meta_description"]
    return ""


# A leading path segment that names a language or a country edition rather
# than a section: `/en/`, `/zh/`, `/pt-br/`.
_EDITION_SEGMENT_RE = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2,4})?$", re.I)

# How deep an about or contact page may sit and still be the site's own. Two
# section segments holds `/about/`, `/about-us/` and `/company/about/`.
_WHOLE_SITE_PAGE_DEPTH = 2


def _section_depth(page):
    """Path segments below the site root, a leading language segment not counted."""
    segments = [s for s in urlparse(str(page.get("url") or "")).path.split("/") if s]
    if segments and _EDITION_SEGMENT_RE.match(segments[0]):
        segments = segments[1:]
    return len(segments)


def _pages_that_speak_for_the_site(pages, any_language=False):
    """The homepage, then the about page, then the contact page.

    `any_language` lifts the language rule below, for a caller that has
    already tried the front door's language and found nothing.

    The page type alone used to decide, so every page the classifier called
    "about" spoke for the whole organisation. A university's brand-assets page
    sits at `/about/symbols/identity/`, is typed about, and describes itself as
    "fonts, images, videos, various templates" - which became the university's
    `description` in the paste-ready block. An about page three sections down
    is about one of the organisation's things, not about the organisation.
    Within each type the shallowest page comes first.

    And only pages in the homepage's own language. A one-page site published
    in nineteen languages has nineteen pages typed home, each at the depth of
    the root once its language segment is set aside, and sorting them by
    address put `/cs` straight after `/`. The English description was skipped
    as one shared by two addresses, and the Organization block went out with
    `"description": "prosím nezdravte jenom lidi v chatu"` - while the top of
    the same report quoted the English one. A language edition speaks for its
    readers; the organisation speaks through its own front door, in the
    language that door is written in. A page that declares no language is
    kept, because nothing says it is an edition.
    """
    home_language = "" if any_language else _homepage_language(pages)
    ordered = []
    for page_type in _WHOLE_BRAND_PAGE_TYPES:
        typed = [p for p in pages if p.get("page_type") == page_type]
        if page_type != "home":
            typed = [p for p in typed if _section_depth(p) <= _WHOLE_SITE_PAGE_DEPTH]
        if home_language:
            typed = [p for p in typed
                     if primary_subtag(p.get("lang")) in ("", home_language)]
        ordered += sorted(typed, key=lambda p: (_section_depth(p), str(p.get("url") or "")))
    return ordered


def _homepage_language(pages):
    """The language the site's front door declares, or '' where it declares none.

    The front door is the home page with the fewest path segments, counting a
    language segment as a segment - `/` before `/en`, and `/en` before
    `/en/start`. Where that page declares no language the answer is '', and
    nothing is set aside: an edition cannot be told from the original without
    knowing what the original is written in.
    """
    homes = [p for p in pages if p.get("page_type") == "home"]
    if not homes:
        return ""
    depth = lambda p: len([s for s in urlparse(str(p.get("url") or "")).path.split("/")  # noqa: E731
                           if s])
    front = min(homes, key=lambda p: (depth(p), len(str(p.get("url") or "")),
                                      str(p.get("url") or "")))
    return primary_subtag(front.get("lang")) or ""


def _description_placeholder(pages):
    """What to say in `description` when nothing the site wrote can go there.

    It used to say "the site publishes none" whatever the reason. A framework's
    documentation site carries one meta description on all 52 of its pages,
    and the same report quoted it in another finding - so the snippet stated
    something the reader could see was false. The description is still not
    used, for the reason `_this_report_calls_this_description_wrong` gives;
    the placeholder now says it exists and why it was left out.
    """
    lead = "<one sentence saying what this organisation is and who it is for"
    for page in _pages_that_speak_for_the_site(pages):
        text = " ".join(str(page.get("meta_description") or "").split())
        if not text:
            continue
        if _reads_as_a_details_table(text):
            # Named, not quoted. A run of opening hours and phone numbers
            # pasted into the placeholder puts the very text this rule keeps
            # out of `description` into the block the reader copies.
            return ("{}. The {} description is a list of opening hours and contact "
                    "details rather than a sentence, so it was left out - write one "
                    "sentence here>".format(
                        lead, "homepage's" if page.get("page_type") == "home"
                        else "{} page's".format(page.get("page_type"))))
        else:
            shared = len([p for p in pages if p.get("meta_description") == page.get(
                "meta_description")])
            why = ("is the meta description on {} of the {} pages crawled, so it describes "
                   "no one page".format(shared, len(pages)) if shared > 1 else
                   "does not read as a complete sentence that will still be true next "
                   "quarter")
        quoted = re.sub(r"[<>]", "", truncate(text, 120)).replace('"', "'")
        return "{}. The {} description, '{}', {} - use it here only if it says what the " \
               "organisation is>".format(lead, "homepage's" if page.get("page_type") == "home"
                                         else "{} page's".format(page.get("page_type")),
                                         quoted, why)
    return ("{} - neither the homepage nor the about page carries a description, fill this "
            "in>".format(lead))


# One account, however many hostnames reach it. Each entry maps a host to the
# platform it belongs to and the host that platform itself publishes.
#
# jetbrains got `https://twitter.com/jetbrains` and `https://x.com/jetbrains`
# in one `sameAs` array, plus `youtube.com/user/AcmeToolsTV` with and without
# the `www.`. Four entries, two accounts, and `sameAs` is the array whose whole
# job is to say "these are the same entity" - so it published the claim that
# one account is two.
_PROFILE_HOSTS = {
    "twitter.com": ("x", "x.com"),
    "mobile.twitter.com": ("x", "x.com"),
    "x.com": ("x", "x.com"),
    "youtube.com": ("youtube", "www.youtube.com"),
    "m.youtube.com": ("youtube", "www.youtube.com"),
    "facebook.com": ("facebook", "www.facebook.com"),
    "fb.com": ("facebook", "www.facebook.com"),
    "m.facebook.com": ("facebook", "www.facebook.com"),
    "linkedin.com": ("linkedin", "www.linkedin.com"),
    "instagram.com": ("instagram", "www.instagram.com"),
    "github.com": ("github", "github.com"),
    "tiktok.com": ("tiktok", "www.tiktok.com"),
    "pinterest.com": ("pinterest", "www.pinterest.com"),
}

# A country edition of a platform's own host: `in.pinterest.com`,
# `uk.linkedin.com`. One account, reached through a regional front door, and a
# shop whose menu links `in.pinterest.com/<x>/` on one page and
# `www.pinterest.com/<x>/` on another had both in its `sameAs`.
_COUNTRY_EDITION_RE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?\.(.+)$")


def _profile_identity(url):
    """(platform, account), the host this URL uses, and the platform's own host.

    The account is the path, lowercased: `youtube.com/user/AcmeToolsTV` and
    `www.youtube.com/user/jetbrainstv` are one channel written two ways.
    """
    cleaned = re.sub(r"[?#].*$", "", str(url or "")).rstrip("/")
    match = re.match(r"^https?://([^/]+)(/.*)?$", cleaned, re.I)
    if not match:
        return (cleaned.lower(), ""), "", None
    host = match.group(1).lower()
    bare = host[4:] if host.startswith("www.") else host
    edition = _COUNTRY_EDITION_RE.match(bare)
    if bare not in _PROFILE_HOSTS and edition and edition.group(1) in _PROFILE_HOSTS:
        bare = edition.group(1)
    path = (match.group(2) or "").lower()
    platform, canonical = _PROFILE_HOSTS.get(bare, (bare, None))
    return (platform, path), host, canonical


def _dedupe_profiles(urls):
    """One URL per account, preferring the host the platform itself publishes."""
    best = {}
    # Sorted, so which of two equally good spellings survives cannot change
    # between runs over one snapshot.
    for url in sorted(set(urls)):
        identity, host, canonical = _profile_identity(url)
        held = best.get(identity)
        if held is None:
            best[identity] = url
            continue
        held_host = _profile_identity(held)[1]
        # The platform's own host wins; between two URLs on the same host the
        # shorter one does, because the difference is a query string and a
        # `?lang=en` is a tracking parameter rather than part of the account.
        if (canonical and host == canonical and held_host != canonical) or \
                (host == held_host and len(url) < len(held)):
            best[identity] = url
    return sorted(best.values())


# Paths that are something a visitor *does* rather than somewhere an account
# *is*. `sameAs` says "this account is me", and an address that signs somebody
# in, shares a page or composes a message is not an account at all.
#
# A Japanese municipality's paste-ready block listed `.../users/sign_in?ref=sideNav`
# among its `sameAs` entries - the sign-in form of a platform the council
# publishes on, reached from its own sidebar. Following it a machine finds a
# login screen and no identity.
_NOT_A_PROFILE_PATH_RE = re.compile(
    r"/(?:sign[_-]?in|sign[_-]?up|signin|signup|login|log[_-]?in|logout|log[_-]?out|"
    r"register|registration|account|settings|password|share|sharer|intent|"
    r"dialog|oauth|auth|sso|subscribe|unsubscribe|cart|checkout|search|"
    # A sign-up page for somebody's programme: an influencer platform's
    # `<brand>.<platform>/join/<campaign>` reached a furniture shop's
    # `sameAs`. Joining is something a visitor does.
    r"join|apply|invite|refer|referral|affiliates?|ambassadors?)"
    r"(?:[/?.]|$)", re.I)

# The first path segment of an address that is one piece of writing on a
# publication rather than an account on a platform.
_ARTICLE_SECTION_SEGMENTS = frozenset({
    "news", "article", "articles", "story", "stories", "review", "reviews", "blog", "blogs",
    "post", "posts",
})


def _is_one_article(path):
    """Is this path one article - a headline slug - rather than an account?

    A furniture shop's `sameAs` carried a magazine's review of one of its
    coffee tables, `/<brand>-nesting-coffee-table-review-37439576`. A review of
    a product is somebody else's writing about the brand, and `sameAs` says
    "this account is me". A slug of four words or more with a long number in
    it, or of six words or more, is a headline; so is anything filed under a
    news or blog section.
    """
    segments = [s for s in (path or "").split("/") if s]
    if not segments:
        return False
    if segments[0].lower() in _ARTICLE_SECTION_SEGMENTS and len(segments) > 1:
        return True
    last = segments[-1]
    words = [w for w in re.split(r"[-_]+", last) if w]
    return len(words) >= 6 or (len(words) >= 4 and bool(re.search(r"[0-9]{5,}", last)))

# Query parameters that mark an address as one campaign's copy of a page. A
# profile URL does not carry them; a link to one issue of a newsletter, sent in
# one mailing, does.
#
# Another report's `sameAs` carried a single newsletter issue with its campaign
# identifiers still attached. That is not the brand's account on that platform;
# it is one thing the brand published there, and declaring it as an identity
# tells a machine the organisation *is* that issue.
_CAMPAIGN_PARAMETER_RE = re.compile(
    r"(?:^|[?&])(?:utm_[a-z_]+|mc_cid|mc_eid|fbclid|gclid|igshid|ref|ref_src|"
    r"campaign|cid|eid|source)=", re.I)


def _is_an_account_and_not_an_action(url):
    """May this address be published as `sameAs`?

    Applied to the profiles read off the site *and* to the ones the site
    already declares. The reason the declared ones are otherwise trusted is
    that the site is the authority on *whose* account something is - it is not
    the authority on whether an address is an account, and a login screen is
    not one however it got into the array.
    """
    text = str(url or "")
    if not text:
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    if _NOT_A_PROFILE_PATH_RE.search(parsed.path or ""):
        return False
    if parsed.query and _CAMPAIGN_PARAMETER_RE.search(parsed.query):
        return False
    if _is_one_article(parsed.path or ""):
        return False
    return True


def _all_same_as(pages, brand=None, declared=()):
    """Profile URLs safe to publish as `sameAs` - the brand's own, only.

    `sameAs` is an assertion of identity: it says "these accounts are me". The
    list was every off-site profile link found anywhere on the site, and a
    company's pages link to plenty of accounts that are not the company. Real
    examples that reached a paste-ready snippet: a co-founder's personal X
    account lifted from an about-page byline, five GitHub file and pull-request
    URLs from a documentation repository, third-party tools named in news
    posts, and the Wikipedia article on ACID transactions.

    Shape is checked during extraction. What shape cannot tell is whose account
    it is, so the handle has to look like the brand: `x.com/postgresql` yes,
    `x.com/james406` no. A handle is allowed to differ from the brand name in
    the ordinary ways - dropped spaces, a `get` or `the` prefix, an `hq` or
    `app` suffix - and anything further apart is left out rather than asserted.
    """
    # One account, one entry. A city's footer links its Facebook page with and
    # without a trailing slash, and both went into `sameAs` - so the snippet
    # asserted the same account twice and the profile count read one higher
    # than the number of accounts. Two strings, one profile.
    urls = _dedupe_profiles([url for page in pages
                             for url in (page.get("social_profiles") or {}).values()])
    key = re.sub(r"[^a-z0-9]", "", str((brand or {}).get("name") or "").lower())
    if len(key) >= 3:
        # Or the site publishes it as its own, in the header or footer most of
        # its pages share. A home-decor shop's footer links six accounts on
        # every page, four under a handle that is not its name - an older
        # company slug, a numbered video channel - and the handle test alone
        # kept two of the six. Where the link sits settles whose account it is better than how
        # the handle is spelled; a citation is in one article's body, never in
        # the chrome of every page.
        chrome = _profiles_in_the_site_chrome(pages)
        urls = [u for u in urls
                if _handle_matches_brand(u, key) or _profile_identity(u)[0] in chrome]
    # The handle test above is the only filter there used to be, and it strips
    # the brand name down to `[a-z0-9]` - which is the empty string for a name
    # written in Japanese, Korean, Arabic, Hebrew, Thai or Devanagari. On every
    # such site `len(key) >= 3` is false, the filter never runs, and each of
    # those brands got the unfiltered list of every off-site link the crawl
    # saw. That is where a municipality's sign-in URL and another site's single
    # newsletter issue reached a paste-ready `sameAs`. This test needs no name
    # and no script, so it runs on every site.
    urls = [u for u in urls if _is_an_account_and_not_an_action(u)]
    # And on a platform where an address is an account at all. An Indonesian
    # shop's block listed a fashion magazine's feature about the shop,
    # `<magazine>.test/<brand>/`, beside its Instagram and LinkedIn: the slug is
    # the brand's name, so the handle test passed, and it sat in a row of
    # account links on one page, so the crawl called it published. On a
    # magazine's host the last segment of a path is a story, and `sameAs` says
    # "this account is me". So a host has to be one where brands keep accounts,
    # unless the site's own `sameAs` or `rel="me"` claims the address - the
    # same rule, from the same function, that decides which accounts
    # `freshness-corroboration-audit` counts, so the block declares no account
    # that count left out. See `could_be_an_account`.
    claimed = claimed_by_the_site(
        [u for page in pages for u in (page.get("declared_profiles") or {}).values()]
        + [u for u in (declared or []) if isinstance(u, str)])
    urls = [u for u in urls if could_be_an_account(u, claimed)]
    # A profile the site already declares in its own `sameAs` is the site
    # asserting the account is its own, which is a stronger statement than any
    # test we could apply to the handle. It goes in whatever it looks like -
    # and through the same deduplication, because a site that lists both
    # `twitter.com/x` and `x.com/x` is where half of these duplicates came from.
    #
    # Except the two things `sameAs` can never be. The site itself: a fashion
    # retailer's block lists its own `/intl` storefront as "the same entity as"
    # the organisation, which says nothing. And a jobs board: an applicant
    # tracking system's page is where the company's vacancies are posted, not
    # an account that describes it.
    own = _own_hosts(pages, brand)
    return _dedupe_profiles([u for u in list(urls) + [u for u in (declared or [])
                                                      if isinstance(u, str)
                                                      and _is_an_account_and_not_an_action(u)]
                             if not _never_same_as(u, own)])


# Applicant tracking and careers hosts. A vacancy listing is not a profile.
# Held as host labels - the words the products choose - rather than as
# addresses, the way `_PUBLIC_LABELS` and the platform table are held: a label
# is the product we recognise, and its suffix is a registry's.
_JOBS_BOARD_LABELS = frozenset({
    "workable", "greenhouse", "lever", "smartrecruiters", "recruitee", "bamboohr",
    "ashbyhq", "jobvite", "icims", "myworkdayjobs", "breezy", "teamtailor", "personio",
})

# The first label of a host a company runs its own vacancies on.
_JOBS_HOST_PREFIXES = frozenset({"apply", "jobs", "careers", "career", "recruiting", "hire"})


def _own_hosts(pages, brand=None):
    """The audited site's own hosts, `www.` set aside."""
    hosts = {strip_www(str((brand or {}).get("host") or "").lower())}
    hosts.update(strip_www(urlparse(str(p.get("url") or "")).netloc.lower()) for p in pages)
    hosts.discard("")
    return hosts


def _never_same_as(url, own_hosts):
    """Is this address the site itself, or a jobs board, rather than a profile?"""
    host = strip_www(urlparse(str(url or "")).netloc.lower())
    if not host:
        return False
    if host in own_hosts:
        return True
    labels = host.split(".")
    return bool(set(labels[:-1]) & _JOBS_BOARD_LABELS) or (
        len(labels) > 2 and labels[0] in _JOBS_HOST_PREFIXES)


def _profiles_in_the_site_chrome(pages):
    """Accounts the site's own header or footer links on most of its pages.

    Returned as `_profile_identity` keys, so a footer that writes the address
    one way and a page body that writes it another are still one account. A
    majority of the pages, not any one: a blog template's author box sits in
    the chrome of the posts it renders and nowhere else.
    """
    counts = {}
    for page in pages:
        seen = set()
        links = page.get("links") or {}
        for bucket in ("nav", "footer"):
            for link in links.get(bucket) or []:
                url = link.get("url") if isinstance(link, dict) else None
                if url:
                    seen.add(_profile_identity(url)[0])
        for identity in seen:
            counts[identity] = counts.get(identity, 0) + 1
    need = len(pages) // 2 + 1 if len(pages) > 1 else 1
    return {identity for identity, count in counts.items() if count >= need}


def _handle_matches_brand(url, key):
    """Does this profile URL's handle name the brand?

    Kept as a thin wrapper: `profile_names_brand` in `audit_common` is the one
    implementation, because the corroboration count needed the same test and
    had none - so a contributor's personal GitHub account and a Wikipedia
    article about a computer-science term were both counted as a brand's own
    profiles. Two skills asking the same question must not answer it twice.
    """
    return profile_names_brand(url, key)


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def _is_empty_block(error):
    """An `ld+json` tag whose content is nothing at all, not broken syntax."""
    return "empty" in str((error or {}).get("error") or "").lower()


# The `@type` a failed block declares, read out of the excerpt the parser kept.
_TYPE_IN_EXCERPT_RE = re.compile(r'"@type"\s*:\s*"([A-Za-z]+)"')


def _unparsed_block_declares(page, type_names=()):
    """The wanted `@type` a failed block on this page names, in its own spelling, or "".

    Split out of `_unreadable_block_reason` because two callers want different
    halves of it. That one wants a sentence; the site-wide identity claim wants
    the fact, so it can tell a broken block that says `"@type": "Organization"`
    - which is the missing block, wherever on the site it sits - from a broken
    block that could be anything.
    """
    errors = [e for e in (page.get("jsonld_errors") or []) if not _is_empty_block(e)]
    if not errors:
        return ""
    excerpt = " ".join(str(e.get("excerpt") or "") for e in errors)
    wanted = {str(t).lower() for t in type_names or ()}
    for match in _TYPE_IN_EXCERPT_RE.finditer(excerpt):
        if match.group(1).lower() in wanted:
            return match.group(1)
    return ""


def _unreadable_block_reason(page, type_names=()):
    """Why this page's markup cannot be called absent, or "".

    A block that failed to parse is not a block that was never written. One
    report said "13 of 13 article pages have no Article markup", told the owner
    to add the snippet to the blog post template, and said in a different
    finding about the same thirteen URLs that their JSON-LD dies on a raw
    newline inside `articleBody`. The pages have the markup. Following the
    first finding adds a second Article block beside a broken one, and the two
    findings contradict each other about the same thirteen pages.

    The type is named when the kept excerpt says it, in the site's own
    spelling, and the reason stays general when it does not: the rule is that
    an unparseable block forbids the absence claim, and knowing which type it
    declared only makes the sentence more useful.
    """
    if not [e for e in (page.get("jsonld_errors") or []) if not _is_empty_block(e)]:
        return ""
    declared = _unparsed_block_declares(page, type_names)
    if declared:
        return "it already ships a block declaring {} that does not parse".format(declared)
    return "it already ships a JSON-LD block that does not parse"


def _set_aside_unparsed_markup(pages, type_names=()):
    """`(pages, unreadable)`: the ones whose markup really is absent, and the rest.

    Every check in this file that counts "pages without type X" runs its
    candidates through here first, so no two findings in one report can
    disagree about whether the same URLs carry markup.
    """
    unreadable = [(p, why) for p in pages
                  for why in [_unreadable_block_reason(p, type_names)] if why]
    blocked = {p["url"] for p, _ in unreadable}
    return [p for p in pages if p["url"] not in blocked], unreadable


# The finding a page with an unparseable block is already reported under,
# named in the words the reader will see above it. `_check_jsonld_validity`
# titles it "N pages contain JSON-LD that does not parse". Every decline forced
# by a parse error says this, because a check that goes quiet without naming
# where the subject went reads as a check that found nothing: one report said
# a shop's homepage declares no Organization type and no WebSite with a
# potentialAction, and both were inside a block the same report quoted,
# two findings earlier, as the block that does not parse.
_THE_PARSE_FINDING = 'the "JSON-LD that does not parse" finding'


def _markup_declared_but_unreadable(unreadable, what, type_names=(),
                                    subject="page", subjects="pages"):
    """The decline sentence for an absence claim a parse error forbids.

    One sentence for every absence check in this file, so a reader meeting it
    under the Organization check and under the Article check reads the same
    explanation and is sent to the same place. Naming the parse finding is the
    half that stops the reader wondering: the pages did not vanish, they moved
    to the finding that reports the syntax error, and fixing that error is what
    brings this check back.

    The declared type is printed once at the end rather than once per page,
    because it is the same string every time - the pages share a template - and
    repeating it after each URL made the sentence read as three findings.
    """
    urls = sorted(page["url"] for page, _ in unreadable)
    declared = sorted({name for page, _ in unreadable
                       for name in [_unparsed_block_declares(page, type_names)] if name})
    return ("markup declared but unreadable - {} carrying a JSON-LD block the parser could "
            "not read, so this check cannot say {} markup is absent there: {}.{} See {}, "
            "which names those pages and is the one to act on; a second block beside a "
            "broken one fixes nothing.".format(
                plural(len(unreadable), subject, subjects), what, ", ".join(urls[:3]),
                " The excerpt kept of {} declares {}.".format(
                    "one of those blocks" if len(unreadable) > 1 else "that block",
                    ", ".join(declared)) if declared else "",
                _THE_PARSE_FINDING))


def _fix_the_parse_error_instead(unreadable, what):
    """The step to print alongside a finding that had to set pages aside.

    They are not in the count above and not in the affected list, because they
    are not missing the markup - so the step says where they went. Without it
    an owner applies this fix site-wide and adds a second block beside a broken
    one on exactly the pages that already had the markup.
    """
    return ("Not counted above: {} that already carry {} markup which does not parse - {}. "
            "The block is there and every consumer discards it, so fix the syntax error "
            "those pages are reported under rather than adding a second block beside it."
            .format(plural(len(unreadable), "page", "pages"), what,
                    ", ".join(sorted(p["url"] for p, _ in unreadable)[:3])))


def _values_escaped_as_html(page):
    """`(property, value)` for values on this page written as escaped HTML.

    Walks the blocks the page published, undecoded. Our own bookkeeping keys
    are skipped: `_nested_in` is added during extraction and is not the site's.
    """
    found = []
    stack = list(page.get("jsonld") or [])
    while stack and len(found) < 8:
        node = stack.pop(0)
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if str(key).startswith("_"):
                continue
            if isinstance(value, (dict, list)):
                stack.append(value)
            elif _escapes_html_inside_json(value):
                found.append((str(key), value))
    return found


# The characters an HTML-escape filter replaces, named once. Used by the
# evidence window below to find the part of a value worth quoting.
_ESCAPED_ENTITY_RE = _CHARACTER_REFERENCE_RE


def _centred_on(value, limit=90):
    """`value`, cut to `limit` around the first character reference in it.

    The escaping finding could not be verified from its own evidence: the
    report quoted 90 characters off the front of a product title and the
    escaped entity sat further along, so the excerpt shown contained no
    `&#39;` and no `&amp;` at all. The reader was asked to believe a claim
    about a character the evidence did not print.

    `truncate` cuts from the start, which is right for a value whose fault is
    the whole value and wrong for one whose fault is at character 140. Where
    there is a character reference, the window is centred on it and the sides
    that were cut are marked, so the quoted string is never mistaken for the
    whole value.
    """
    text = str(value or "")
    if len(text) <= limit:
        return text
    match = _ESCAPED_ENTITY_RE.search(text)
    if match is None:
        return truncate(text, limit)
    # Room for the reference itself, then as much on each side as is left.
    span = match.end() - match.start()
    side = max(0, (limit - span) // 2)
    start = max(0, match.start() - side)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    excerpt = text[start:end]
    return ("..." if start > 0 else "") + excerpt + ("..." if end < len(text) else "")


# How each platform's own template language turns a value into JSON.
#
# Rows are here for the reason `PLATFORM_SIGNATURES` rows are: the spelling is
# the platform's own and there is no second reading of it. A platform whose
# JSON-LD is written somewhere other than a template file it owns - a hosted
# builder's head-code box, a plugin - has no row, and its reader gets the
# mechanism sentence with no syntax in it.
#
# What the missing table cost. Five real reports all carried the same
# step, word for word: "in Liquid, `{{ value | json }}` rather than
# `{{ value | escape }}`". Liquid is one hosted shop platform's template
# language, and that step was printed at a WooCommerce site, a Nuxt storefront
# and a Wix site, none of which has a Liquid template to edit.
_JSON_FILTER_BY_PLATFORM = {
    "Shopify": "This site's templates are Liquid, where the JSON filter is "
               "`{{ value | json }}` and the HTML-escape filter it has to replace is "
               "`{{ value | escape }}`.",
    "WordPress": "On WordPress the JSON-LD block is usually written by an SEO plugin "
                 "rather than by the theme, so look at the plugin first: where the "
                 "escaped characters came from text typed into one of its fields, "
                 "correcting them there fixes every page and touches no template. Where "
                 "the theme does write the block, the PHP serialiser is "
                 "`wp_json_encode( $value )` and the escaper to remove is `esc_html()` "
                 "or `esc_attr()`.",
    "Drupal": "This site's templates are Twig, where the JSON filter is "
              "`{{ value|json_encode }}` and Twig's automatic HTML escaping is what has "
              "to be turned off for that value.",
    "Joomla": "The templates are PHP, so print each value with `json_encode( $value )` "
              "rather than with `htmlspecialchars()`.",
    "Adobe Commerce": "The templates are PHTML, so build the block as a PHP array and "
                      "print it with `json_encode()`, rather than passing each value "
                      "through `$escaper->escapeHtml()`.",
    "BigCommerce": "The Stencil templates are Handlebars, where the JSON helper is "
                   "`{{json value}}` and the escaping form to replace is the plain "
                   "`{{value}}`.",
    "Hugo": "Hugo's templates serialise with `{{ $value | jsonify }}`; the escaping "
            "form to replace is the bare `{{ $value }}`.",
    "Jekyll": "Jekyll's templates are Liquid, where the JSON filter is "
              "`{{ value | jsonify }}` and the HTML-escape filter it has to replace is "
              "`{{ value | escape }}`.",
    "Next.js": "The block is written in JavaScript, so build it as an object and set "
               "`dangerouslySetInnerHTML` from `JSON.stringify(value)` rather than "
               "interpolating an escaped string.",
    "Nuxt": "The block is written in JavaScript, so build it as an object and serialise "
            "it with `JSON.stringify(value)` rather than interpolating a value through "
            "a template expression, which escapes for HTML.",
    "Gatsby": "The block is written in JavaScript, so build it as an object and set "
              "`dangerouslySetInnerHTML` from `JSON.stringify(value)` rather than "
              "interpolating an escaped string.",
    "Astro": "The block is written in JavaScript, so use "
             "`<script type=\"application/ld+json\" set:html={JSON.stringify(value)} />` "
             "rather than interpolating the value, which escapes for HTML.",
}


# The properties a template fills from the page's rendered HTML body. An
# escaped entity in one of these was already in that HTML - the post's
# content, stored as markup - before any filter touched it.
_BODY_TEXT_PROPERTIES = frozenset({"articlebody", "text", "reviewbody", "commenttext"})


# A tag written as a tag: `<p>`, `</strong>`, `<br/>`. An HTML-escape filter
# turns every one of these into `&lt;p&gt;`, so a value carrying a raw tag
# beside a character reference was not produced by that filter.
_RAW_TAG_RE = re.compile(r"</?[a-z][a-z0-9]*(?:\s[^<>]*)?/?>", re.I)


def _escaped_only_in_body_text(escaped):
    """Were these references already in the content before any filter ran?

    Two readings, either of which settles it. Every escaped value sits in a
    body-text property like `articleBody`, which a template fills from stored
    HTML. Or a value carries a raw tag beside its references: a cookware
    shop's `text` values read `<p>1. Preserves &gt;90% of nutrition</p>`, and
    an escape filter would have written that `<p>` as `&lt;p&gt;` - so the
    `&gt;` came from the product's stored HTML, and swapping one filter for
    another would change nothing.
    """
    pairs = [(str(key).lower(), str(value)) for _page, found in escaped for key, value in found]
    if not pairs:
        return False
    if all(key in _BODY_TEXT_PROPERTIES for key, _ in pairs):
        return True
    return any(_RAW_TAG_RE.search(value) for _, value in pairs)


def _json_serialisation_steps(platform, body_text=False):
    """The fix steps for a block whose values were escaped as HTML.

    The mechanism first, in words that hold whatever writes the block, and the
    platform's own syntax only where the detector named the platform outright
    *and* this file has a row for it. `template_change_steps` is not reused
    here: this fix is not "put a block in the shared template", it is "change
    how one existing template prints a value", so what the reader needs from
    the platform is the filter name and who can change it.

    `body_text` is the case where swapping filters is the wrong fix. A shop's
    blog template already printed `articleBody` through the JSON filter - the
    block carried `\\u0026amp;`, which is that filter writing an `&` - and
    the report's third "Start here" item told it to replace the HTML-escape
    filter with the JSON one. The `&amp;` was in the post's stored HTML; the
    JSON filter faithfully serialised it. What fixes that is serialising the
    post's text rather than its markup.
    """
    platform = _as_platform_or_none(platform)
    if body_text:
        liquid = platform.named and platform.name in ("Shopify", "Jekyll")
        return [
            "The escaped characters come from the content itself - a post body, a product "
            "description, an answer - which the template fills from stored HTML. The `&amp;` "
            "is already in that HTML before any filter runs, so replacing one filter with "
            "another changes nothing.",
            "Serialise the plain text instead: strip the HTML tags and decode the character "
            "references, then print it as JSON{}. Or leave the long body fields out - "
            "`headline`, a one-sentence `description` and the dates are what a consumer "
            "reads.".format(
                " - in Liquid, `{{ article.content | strip_html | json }}`, and the same "
                "`strip_html` before `json` on a product's description" if liquid else ""),
            "Re-check one of the named pages in View Source: the value should carry `&`, not "
            "`&amp;` or `\\u0026amp;`.",
            who_edits_the_template(platform),
        ]
    steps = [
        "In whatever writes this block, print each value as JSON rather than passing it "
        "through the filter that escapes text for HTML.",
    ]
    named = _JSON_FILTER_BY_PLATFORM.get(platform.name) if platform.named else None
    if named:
        steps.append(named)
    else:
        steps.append(
            "This audit cannot name the template language here, so it cannot name the "
            "filter. Find the template that writes the "
            "`<script type=\"application/ld+json\">` tag and look at how each value is "
            "printed: whichever function or filter is turning `&`, `<`, `>`, `\"` and "
            "`'` into `&amp;`, `&lt;`, `&gt;`, `&quot;` and `&#39;` is the one to "
            "replace with your engine's JSON serialiser.")
    steps.append(
        "The escape filter is right for text that lands in HTML and wrong inside a "
        "script element, where the JSON parser is the only reader and it does not "
        "decode character references.")
    steps.append(
        "Re-check one of the named pages in View Source: the value should carry the "
        "punctuation itself, not `&#39;` or `&amp;`.")
    steps.append(who_edits_the_template(platform))
    return steps


def _as_platform_or_none(platform):
    """A `PublishingPlatform` from whatever a caller had to hand.

    The shared library's own `_as_platform` is private to it, and every check
    in this file may be called with a resolved platform, a snapshot or nothing
    at all - the last of those from the unit tests, which build a page list and
    no snapshot. Routing through `publishing_platform` here keeps `.named`
    meaning the same thing in every branch above.
    """
    if platform is None:
        return publishing_platform({})
    if isinstance(platform, dict):
        return publishing_platform(platform)
    return platform


# Properties whose value is an address rather than words. An escaped `&amp;`
# in one of these renames a query parameter or splits one identifier into two;
# it does not put a wrong spelling in front of anyone.
_ADDRESS_KEYS = {"@id", "@context", "url", "sameas", "image", "logo", "contenturl",
                 "embedurl", "thumbnailurl", "mainentityofpage", "item", "target",
                 "urltemplate", "downloadurl", "installurl", "hasmap", "discussionurl",
                 "license", "codeRepository".lower(), "archivedat", "significantlink"}

# An absolute or scheme-relative address, whole. Anything with a space in it
# is words that happen to start with a scheme.
_WHOLE_ADDRESS_RE = re.compile(r"^(?:https?:)?//\S+$", re.I)

# The types whose `name` is the organisation's own. An escaped value on one of
# these is the case the finding was written for: the company's name published
# in a second spelling, which every consumer then reads as a second identity.
_IDENTITY_NODE_TYPES = set(ORG_IDENTITY_TYPES) | {"website", "brand"}

# How far behind the newest page a page has to be before its escaping is read
# as something left over rather than something the templates still do.
_OLD_PAGE_YEARS = 3

# A four-digit year standing alone as a path segment: `/news/2018/`.
_YEAR_IN_PATH_RE = re.compile(r"/((?:19|20)\d{2})(?=/|$)")


def _is_an_address(key, value):
    """Is this escaped value an address rather than words someone reads?"""
    return (str(key).lower() in _ADDRESS_KEYS
            or bool(_WHOLE_ADDRESS_RE.match(str(value or "").strip())))


def _escaped_values_in_context(page):
    """`(types, key, value)` for each escaped value on this page.

    `_values_escaped_as_html` with the one thing it drops kept: the type of the
    node the value sits on. An untyped object inherits the type of the node it
    hangs off, so an author's `image: {"url": ...}` reads as part of the Person
    it belongs to.
    """
    found = []
    stack = [(node, frozenset()) for node in (page.get("jsonld") or [])]
    while stack:
        node, inherited = stack.pop(0)
        if isinstance(node, list):
            stack.extend((item, inherited) for item in node)
            continue
        if not isinstance(node, dict):
            continue
        types = frozenset(jsonld_type_names(node.get("@type"))) or inherited
        for key, value in node.items():
            if str(key).startswith("_"):
                continue
            if isinstance(value, (dict, list)):
                stack.append((value, types))
            elif _escapes_html_inside_json(value):
                found.append((types, str(key), value))
    return found


def _published_year(page):
    """The year this page says it was first published, or None.

    `datePublished` rather than `dateModified`: a CMS that regenerates every
    page stamps this year's modification date on a clipping from 2018, and
    that date says when the template last ran, not how old the words are.
    """
    dates = page.get("dates") or {}
    stamp = str(dates.get("jsonld_date_published") or "")
    match = re.match(r"^((?:19|20)\d{2})", stamp)
    if match:
        return int(match.group(1))
    path = urlparse(str(page.get("url") or "")).path
    years = [int(y) for y in _YEAR_IN_PATH_RE.findall(path)]
    return min(years) if years else None


def _how_far_the_escaping_reaches(escaped, pages):
    """Severity, example and rationale for the escaped-values finding.

    The finding exists because one template filter, run over every value,
    quietly misspells everything a site says about itself. That is high, and
    it is what the rationale always said. It is not what every site this fires
    on has:

      only addresses are escaped    `?s=96&amp;d=mm` in the avatar URL a blog
                                    plugin writes. The image still loads; no
                                    word anyone reads is changed. Low.
      one page, or only old pages   curly quotes in the breadcrumb of one news
                                    clipping from 2018. Whatever did it is not
                                    what renders the rest of the site. Medium
                                    where the organisation's own name is among
                                    them, low where it is only that page's own
                                    title or text.
      text on a few current pages   page names and descriptions, not the
                                    company's name. Medium.
      the organisation's own name,  high, as before.
      or text on a quarter of the
      pages or more

    The example quoted is the most serious value found, so the evidence line
    shows the reason for the grade rather than whichever page sorted first.
    """
    readings = [(page, _escaped_values_in_context(page)) for page, _pairs in escaped]
    text = [(page, [(t, k, v) for t, k, v in found if not _is_an_address(k, v)])
            for page, found in readings]
    text = [(page, found) for page, found in text if found]
    identity = [(page, [(t, k, v) for t, k, v in found if t & _IDENTITY_NODE_TYPES])
                for page, found in text]
    identity = [(page, found) for page, found in identity if found]

    years = [y for y in (_published_year(p) for p in pages) if y]
    newest = max(years) if years else None

    def is_old(page):
        year = _published_year(page)
        return bool(newest and year and newest - year >= _OLD_PAGE_YEARS)

    text_pages = [page for page, _ in text]
    confined = bool(text_pages) and (len(text_pages) == 1 or all(is_old(p) for p in text_pages))

    if identity:
        page, found = identity[0]
    elif text:
        page, found = text[0]
    else:
        page, found = readings[0]
    first = found[0] if found else (frozenset(), escaped[0][1][0][0], escaped[0][1][0][1])
    example = (page, [(first[1], first[2])])

    if not text_pages:
        keys = sorted({k for _page, found in readings for _t, k, _v in found})
        return {
            "severity": "low", "text_pages": [], "example": example,
            "rationale": (
                "Every escaped value here is an address ({}), not words anyone reads. What "
                "it breaks is narrow: a consumer that follows the address reads `&amp;` as "
                "those five characters, so the query parameter after it arrives under a "
                "different name (`amp;d` rather than `d`) and its setting is lost, and an "
                "`@id` written this way no longer matches the same address written plainly "
                "elsewhere, so two nodes meant to be one are read as two. No name, "
                "description or other text is misspelled.".format(
                    ", ".join("`{}`".format(k) for k in keys[:4]))),
        }
    if confined:
        where = ("one page" if len(text_pages) == 1 else
                 "pages first published {} or more years before the newest page crawled"
                 .format(_OLD_PAGE_YEARS))
        return {
            "severity": "medium" if identity else "low", "text_pages": text_pages,
            "example": example,
            "rationale": (
                "The escaped text is on {} only, so it is not a template escaping every "
                "value the site publishes: the rest of the site's markup reads correctly. "
                "What reads wrong is {} - a consumer quotes it with the character "
                "reference in it.".format(
                    where, "the organisation's own name there" if identity else
                    "that page's own title or text")),
        }
    if identity or (len(text_pages) >= 3 and len(text_pages) * 4 >= len(pages)):
        return {
            "severity": "high", "text_pages": text_pages, "example": example,
            "rationale": (
                "Escaped values parse as valid JSON and are therefore never reported by a "
                "validator, while every string they carry is subtly wrong. The damage shows "
                "up somewhere else entirely - as a second spelling of the company's name, "
                "or as markup that appears to contradict the page - so the site is graded "
                "on symptoms and the one-line cause is never found."
                if identity else
                "Escaped values parse as valid JSON and are therefore never reported by a "
                "validator, and here they are on {} of the {} pages graded: the names and "
                "descriptions an assistant quotes when it names these pages carry the "
                "character references in them.".format(len(text_pages), len(pages))),
        }
    return {
        "severity": "medium", "text_pages": text_pages, "example": example,
        "rationale": (
            "The escaped text is on {} of the {} pages graded, and it is page names and "
            "descriptions rather than the organisation's own name - so what a consumer "
            "gets wrong is how it quotes those pages, with the character reference in "
            "them. Escaped values parse as valid JSON, so no validator reports them."
            .format(len(text_pages), len(pages))),
    }


def _check_jsonld_validity(result, pages, platform=None):
    result.check("jsonld-validity")
    # The cause, reported once, above the two parse faults. A theme that runs
    # values through an HTML-escape filter inside a `<script>` element is one
    # template bug, and left undiagnosed it surfaces as a scatter of unrelated
    # symptoms: two spellings of the same company name reported as two
    # identities to choose between, a name that "appears nowhere in the page
    # text", an answer that does not match the page word for word. Every one of
    # those is the same missing `| json`.
    escaped = [(p, pairs) for p in pages for pairs in [_values_escaped_as_html(p)] if pairs]
    if escaped:
        reach = _how_far_the_escaping_reaches(escaped, pages)
        page, pairs = reach["example"]
        result.add(
            id_hint="jsonld-values-escaped-as-html",
            title="{} JSON-LD {} escaped as HTML".format(
                plural(len(escaped), "page publishes", "pages publish"),
                "values" if reach["text_pages"] else "addresses"),
            # Graded by what the escaping reaches, not by the fact of it. The
            # finding was high on every site it fired on, and reached a report's
            # "Start here" twice for things that break very little: the `&amp;`
            # inside an author-avatar URL a blog plugin writes, and a pair of
            # curly quotes in the breadcrumb of one news clipping from 2018. See
            # `_how_far_the_escaping_reaches`.
            severity=reach["severity"], confidence="high",
            # The value in backticks, not in quotation marks. `compose_report`
            # decodes character references at the render boundary so no report
            # prose prints `&amp;amp;` at a reader, and it skips code spans -
            # so a quoted `&#39;` here would be rendered as an apostrophe and
            # this finding would print, as its example, a value with nothing
            # wrong with it. The one string in this report that has to survive
            # the decode is the escaped value itself.
            # `_centred_on`, not `truncate`. The quoted excerpt has to contain
            # the character reference the finding is about, and cutting 90
            # characters off the front of a long product title showed the
            # reader a window with no escaped entity anywhere in it.
            evidence='Example: {} declares {} as `{}`. Inside <script '
                     'type="application/ld+json"> there is no HTML parsing, so a consumer '
                     'reads those characters literally rather than the punctuation they '
                     'stand for.'.format(page["url"], "`{}`".format(pairs[0][0]),
                                         _centred_on(pairs[0][1], 90)),
            mechanism="C", root_cause="invalid-jsonld",
            summary=("Serialise the plain text of the content, not its stored HTML, into the "
                     "JSON-LD." if _escaped_only_in_body_text(escaped) else
                     "Serialise JSON-LD values as JSON rather than running them through the "
                     "template's HTML-escape filter."),
            how_to_fix=_json_serialisation_steps(
                platform, body_text=_escaped_only_in_body_text(escaped)),
            effort="low", owner="developer",
            rationale=reach["rationale"],
            affected_pages=sorted(p["url"] for p, _ in escaped),
        )

    # A block that parses can still hold a value no consumer can read. A
    # national library's template wrote `"datePublished":
    # "2025-08-11T02:26:36.174Z+09:00"` into the Article block of 47 of its 60
    # pages - UTC and nine hours ahead of it, in one value - and this check
    # passed every one of them, because it only asked whether the JSON parsed.
    bad_dates = _malformed_dates(pages)
    if bad_dates:
        page, key, value, why = bad_dates[0]
        dated_pages = sorted({p["url"] for p, _, _, _ in bad_dates})
        keys = sorted({k for _, k, _, _ in bad_dates})
        result.add(
            id_hint="jsonld-date-is-not-a-date",
            title="{} a JSON-LD date that is not a date".format(
                plural(len(dated_pages), "page declares", "pages declare")),
            severity="medium", confidence="high",
            # The value in backticks: it is the site's own string, and the
            # owner will search their template for it exactly as written.
            evidence="Example: {} declares `{}` as `{}` - {}. Seen in {} on {} of the {} "
                     "crawled.".format(page["url"], key, value, why,
                                       ", ".join("`{}`".format(k) for k in keys),
                                       len(dated_pages), plural(len(pages), "content page")),
            mechanism="C", root_cause="invalid-jsonld",
            summary="Write every JSON-LD date as an ISO 8601 date, with at most one time zone.",
            how_to_fix=[
                "Find where the template prints `{}` and format the value as ISO 8601: "
                "`YYYY-MM-DD`, or `YYYY-MM-DDThh:mm:ss` followed by either `Z` or an offset "
                "such as `+09:00` - never both.".format(key),
                "If the template appends an offset to a timestamp the CMS already wrote in "
                "UTC, drop one of the two: `2025-08-11T02:26:36Z` and "
                "`2025-08-11T11:26:36+09:00` are the same moment, written correctly.",
                "Re-test one of the named pages with the schema.org validator.",
                who_edits_the_template(platform),
            ],
            effort="low", owner="developer",
            rationale="A date a consumer cannot parse is a date the page does not have: the "
                      "article reads as undated to every system that reads the markup for "
                      "when it was written.",
            affected_pages=dated_pages,
        )

    broken = [p for p in pages if p.get("jsonld_errors")]
    if not broken:
        # Never listed as clean on a run where this check has already reported
        # something.
        if not escaped and not bad_dates:
            result.skip("jsonld-validity",
                        "every JSON-LD block on the crawled pages parsed as valid JSON, and "
                        "no value in one is written with HTML character references")
        return

    # Two different faults, and they were reported as one. A block that fails
    # to parse has a syntax error to find; a block whose content is a single
    # newline has no syntax to fix, and telling its owner to "paste the failing
    # block into any JSON validator and fix the syntax error" sends them to
    # validate an empty string. That advice was the number-one "Start here"
    # item, at high severity, in one report.
    malformed = [p for p in broken
                 if any(not _is_empty_block(e) for e in p["jsonld_errors"])]
    empty = [p for p in broken if any(_is_empty_block(e) for e in p["jsonld_errors"])]

    if malformed:
        first = next(e for e in malformed[0]["jsonld_errors"] if not _is_empty_block(e))
        excerpt = truncate(first.get("excerpt", ""), 120)
        result.add(
            id_hint="jsonld-does-not-parse",
            title="{} JSON-LD that does not parse".format(
                plural(len(malformed), "page contains", "pages contain")),
            severity="high", confidence="high",
            # The label only when there is something to label. `Excerpt:` with
            # nothing after it was printed on every page whose error carried no
            # excerpt, which reads as an excerpt that failed to render.
            # The excerpt in backticks, for the same reason the escaped-value
            # finding above uses them: it is a fragment of the site's own
            # source, and `compose_report` decodes character references in
            # report prose. An excerpt printed with its `&#39;` resolved is a
            # string the owner will not find when they search their template
            # for it.
            evidence="Example: {} -> {}.{}".format(
                malformed[0]["url"], first.get("error"),
                " Excerpt: `{}`".format(excerpt) if excerpt else ""),
            mechanism="C", root_cause="invalid-jsonld",
            summary="Fix the malformed JSON-LD so the markup you already wrote is actually readable.",
            how_to_fix=[
                "Paste the failing block into any JSON validator and fix the syntax error.",
                "The usual causes are a trailing comma, an unescaped quote inside a description, "
                "or a template variable that rendered empty.",
                # Said as "serialise as JSON", never as "escape the values".
                # The step used to read "escape the values rather than
                # interpolating them raw", and the finding directly above this
                # one in the same report is about a template that escaped the
                # values - as HTML, which is the only escaping most template
                # languages offer by default. One report telling a reader to
                # escape and not to escape is a report they cannot act on.
                "If the block is generated by a template, print each value through the "
                "engine's JSON serialiser rather than interpolating it raw. That is not the "
                "same as the HTML-escape filter, which produces valid JSON carrying wrong "
                "strings.",
                "Re-test with Google's Rich Results Test or the schema.org validator.",
                who_edits_the_template(platform),
            ],
            effort="low", owner="developer",
            rationale="Invalid JSON-LD is discarded entirely by every consumer. The "
                      "site has paid the cost of adding structured data and receives none of the "
                      "benefit, which is worse than having none, because nobody notices.",
            affected_pages=[p["url"] for p in malformed],
        )

    if empty:
        result.add(
            id_hint="empty-jsonld-block",
            title="{} an empty JSON-LD block".format(
                plural(len(empty), "page ships", "pages ship")),
            # Not high. An empty block breaks nothing that works; it is a
            # template that was wired up and then had nothing to say, so the
            # page carries the tag and none of the data. That is a hole to
            # fill, not markup being discarded.
            severity="medium", confidence="high",
            evidence="Example: {} carries a <script type=\"application/ld+json\"> tag whose "
                     "content is empty. There is no syntax error in it and nothing in it to "
                     "read. Seen on {} of the {} crawled.".format(
                         empty[0]["url"], len(empty), plural(len(pages), "content page")),
            mechanism="C", root_cause="invalid-jsonld",
            summary="Find the template that writes this tag and either give it data or stop "
                    "writing the tag.",
            how_to_fix=[
                "This is not a syntax error, so there is nothing to paste into a validator: "
                "the tag rendered with no content at all.",
                "Find the template or plugin that emits the tag and check the variable it "
                "interpolates. An empty block usually means the value it expected - the "
                "Organization block, the Article block - was absent on these page types.",
                "Either populate it with the markup the page type should carry, or stop "
                "emitting the tag on the page types that have nothing to put in it.",
                "Re-check one of the named pages in View Source: the tag should either hold "
                "JSON or be gone.",
                who_edits_the_template(platform),
            ],
            effort="low", owner="developer",
            rationale="The page looks marked up and is not. Nothing warns anyone, "
                      "because the tag is there and every consumer simply reads nothing out "
                      "of it.",
            affected_pages=[p["url"] for p in empty],
        )


# schema.org writes its type names in CamelCase and the crawl records them
# lower-cased, so quoting the recorded value straight back ("the site declares
# localbusiness") names a type schema.org does not have and a developer cannot
# search for. Every identity type is spelled out here rather than guessed at by
# capitalising, because the guess gets `CollegeOrUniversity` wrong.
_IDENTITY_TYPE_NAMES = {
    "organization": "Organization", "localbusiness": "LocalBusiness",
    "corporation": "Corporation", "store": "Store", "restaurant": "Restaurant",
    "ngo": "NGO", "educationalorganization": "EducationalOrganization",
    "governmentorganization": "GovernmentOrganization", "onlinestore": "OnlineStore",
    "professionalservice": "ProfessionalService", "medicalbusiness": "MedicalBusiness",
    "financialservice": "FinancialService", "brand": "Brand", "museum": "Museum",
    "touristattraction": "TouristAttraction", "collegeoruniversity": "CollegeOrUniversity",
    "library": "Library", "nonprofitorganization": "NonprofitOrganization",
    "performinggroup": "PerformingGroup", "sportsorganization": "SportsOrganization",
}


def _identity_type_names(recorded):
    """The recorded type names, spelled the way schema.org spells them."""
    return sorted(_IDENTITY_TYPE_NAMES.get(name, name) for name in recorded)


# Why each Organization property is worth having, one sentence each, so the
# finding can explain the hole this site actually has.
#
# The rationale used to be one fixed sentence about `sameAs`, printed whatever
# was missing. A software vendor whose live block lists five `sameAs` profiles
# read "`sameAs` is how a machine confirms that the company on this site is the
# same company on those profiles. Without it the site is one unlinked claim
# among many" in a finding whose own evidence said the missing properties were
# `name` and `description`. An owner who checks the block finds the property
# there and stops reading the report.
_ORG_PROP_RATIONALE = {
    "name": "`name` is the string a consumer repeats as the company's name; without it "
            "the identity block names nobody.",
    "url": "`url` is what ties this identity to this site rather than to whoever copied "
           "the block.",
    "logo": "`logo` is the image shown beside the name, and the only one stated as data "
            "rather than guessed from the page.",
    "description": "`description` is the sentence a consumer repeats when it says what this "
                   "organisation is.",
    "sameAs": "`sameAs` is how a machine confirms that the company on this site is the same "
              "company on those profiles. Without it the site is one unlinked claim among "
              "many.",
}

# What to put in each one, for the same reason.
_ORG_PROP_FIX = {
    "name": "`name` is the organisation's name as you write it everywhere else, not a page "
            "title and not a tagline.",
    "url": "`url` is this site's own home page address.",
    "logo": "`logo` should point at your actual logo file, not at the social sharing card.",
    "description": "`description` should be the same sentence you use everywhere else.",
    # `sameAs` is not here. Its step names platforms, and a fixed list of them
    # is what put "Crunchbase, GitHub" in front of a sandal shop - twenty lines
    # above the same report's own off-site advice, which had been gated by what
    # kind of site this is and named Instagram, TikTok and the marketplaces the
    # shop already sells on. Two lists in one report naming different platforms
    # for the same field is the report arguing with itself. See
    # `_same_as_fix_step`.
}


def _same_as_fix_step(pages, brand, snapshot):
    """What to put in `sameAs`, named for this site rather than for shops.

    Best answer first: the profiles this site already links to. `sameAs` says
    "these accounts are me", and a site's own footer is the site saying exactly
    that - so where the crawl found any, the step names them and the owner has
    nothing to research.

    Where it found none, the step falls back to a description of what to go and
    claim rather than to brand names. The catalogue that maps a site kind to
    platform names lives in the skill that reports off-site presence, and one
    marketplace may not hold two of them; what this step owes that finding is
    not to contradict it, which a phrase describing the kind of place cannot do.
    """
    found = _all_same_as(pages, brand)
    if found:
        # The last clause is the half of this defect this skill can honestly reach. A
        # school's report offered a YouTube channel URL in this snippet while a
        # finding further down the same report said that URL returns HTTP 404
        # and that the fix is to "delete the entry rather than leaving it".
        # Nothing here can tell: the only code that fetches these addresses is
        # `_check_profile_links_resolve` in `freshness-corroboration-audit`,
        # which is `SUB_SKILLS[4]` to this skill's `SUB_SKILLS[2]` and hands its
        # verdict to nothing but its own findings file. The crawl records the
        # links and never their status. So the step says what is true - that
        # this audit read these off the pages and did not follow them - and the
        # two instructions stop being opposites. Removing the dead entry from
        # the block itself needs both findings files at once, which is
        # `compose_report.py` and not this file.
        return ("`sameAs` should list the profiles you control. This site already links to "
                "{}, so start with {} - one entry each, as absolute URLs. These were read "
                "off your own pages and not followed, so drop any that no longer "
                "resolve.".format(
                    ", ".join(found[:4]), "those" if len(found) > 1 else "that"))
    kind = site_kind(snapshot)
    # Not "the directories your industry uses": an industry is a thing a
    # business has, and this is the line every site the classifier could not
    # place receives - an essay, a scholarship network, a central bank.
    where = ("the registers and directories where organisations like this one are listed, "
             "and the platforms you already publish on")
    if kind.is_certainly(LOCAL_BUSINESS):
        where = ("the mapping service people search you in, the review sites your customers "
                 "use, and your trade association's directory")
    elif kind.is_certainly(PUBLIC_BODY):
        where = ("the public registers that list your body, your open-data portal, and the "
                 "channels you publish notices on")
    return ("`sameAs` should list every profile you control, as absolute URLs - {}. Claim "
            "them before declaring them: a `sameAs` pointing at an account you do not run "
            "is an identity claim about somebody else.".format(where))


def _org_prop_fix(prop, pages, brand, snapshot):
    """The fix step for one Organization property, tailored where it must be."""
    if prop == "sameAs":
        return _same_as_fix_step(pages, brand, snapshot)
    return _ORG_PROP_FIX[prop]


def _org_missing_rationale(missing):
    """One sentence per property this block actually omits."""
    said = [_ORG_PROP_RATIONALE[prop] for prop in HIGH_VALUE_PROPS["Organization"]
            if prop in missing and prop in _ORG_PROP_RATIONALE]
    if not said:
        return ("Organization markup is read property by property, and the properties this "
                "block omits are the ones a consumer has to guess at instead.")
    return " ".join(said)


def _check_organization(result, snapshot, pages, by_type, brand, platform=None):
    result.check("organization-markup")
    identity_pages = by_type.get("home", []) + by_type.get("about", []) + by_type.get("contact", [])
    if not identity_pages:
        result.skip("organization-markup",
                    "no home, about or contact page was crawled, so there is no page where "
                    "organisation-level markup would be expected")
        return

    # Only nodes that speak for the site. A `publisher` block naming the brand
    # is the site declaring its own identity and stays - that is the shape a
    # statistics charity publishes on 47 article pages - while an Organization
    # lifted out of an Event's `performer` or a Product's `brand` names
    # somebody else, and grading it would report another company's markup as
    # this site's and paste that company's values into the snippet under it.
    org_nodes = [(p, n) for p in pages for n in _nodes_of(p, ORG_IDENTITY_TYPES)
                 if _speaks_for_the_site(n, brand)]
    # A node nested under another node's `publisher`, `founder`, `author` or
    # `brand` is a reference that other node makes, not the site declaring its
    # identity. A furniture retailer declares no identity block anywhere, and
    # its two blog posts name "<Brand>" as the Article's publisher: that stub
    # was graded as "Organization markup is present but omits url, description,
    # sameAs", and the homepage was told it lacks what "the rest of the site"
    # has. See `_REFERENCE_ROLES`.
    referenced = [(p, n) for p, n in org_nodes if _only_referenced(n)]
    org_nodes = [(p, n) for p, n in org_nodes if not _only_referenced(n)]
    # The homepage, first. `pages_of` sorts by URL alone, so the node that got
    # graded was whichever page sorted first - and a charity was graded on a
    # footer `Store` stub from one shop page out of sixty while its complete
    # homepage Organization went unread. Among equally-placed candidates, the
    # most complete one wins, because grading the thinnest node and reporting
    # what it lacks describes the stub rather than the site.
    org_nodes.sort(key=lambda pair: (
        0 if pair[0].get("page_type") == "home" else
        1 if pair[0].get("page_type") in ("about", "contact") else 2,
        len(_missing_props(pair[1], HIGH_VALUE_PROPS["Organization"])),
        pair[0]["url"]))

    # A Person node only means "this is a personal site" when it sits on an
    # identity page and is not the byline of an article. Yoast and most
    # WordPress SEO plugins emit the author as a top-level `@graph` node on
    # every post, so any company blog with no Organization markup had this
    # check skipped entirely - the single most important structured-data
    # question, silently declined, on exactly the sites that fail it.
    person_nodes = [(p, n) for p in identity_pages
                    for n in _nodes_of(p, {"person"})
                    if not _nodes_of(p, ARTICLE_TYPES)]

    if not org_nodes and person_nodes:
        result.skip("organization-markup",
                    "the site declares a Person rather than an Organization on {}, which is "
                    "the correct identity type for a personal site".format(person_nodes[0][0]["url"]))
        return

    # A block that does not parse is a block this site wrote. `org_nodes` is
    # built from `jsonld`, which holds only what parsed, so on a page with a
    # syntax error this check is reading its own blind spot and would publish
    # it as a fact about the site.
    #
    # Scoped to the identity pages, not to the whole crawl, for the same reason
    # the Article check scopes its gate to the article pages: those are the
    # pages this finding names in `affected_pages`, the pages an identity block
    # belongs on, and the pages it would be added to. A broken Recipe block on
    # one deep page would otherwise silence the single most important
    # structured-data question on a site that genuinely declares no identity
    # anywhere. The second clause catches the case the first cannot: a broken
    # block whose kept excerpt names an identity type outright, wherever it
    # sits, is the missing block until proved otherwise.
    _, unreadable_identity = _set_aside_unparsed_markup(identity_pages, ORG_IDENTITY_TYPES)
    blocked = {p["url"] for p, _ in unreadable_identity}
    unreadable_identity += [
        (p, _unreadable_block_reason(p, ORG_IDENTITY_TYPES)) for p in pages
        if p["url"] not in blocked and _unparsed_block_declares(p, ORG_IDENTITY_TYPES)]
    if unreadable_identity:
        result.signal("identity_pages_whose_markup_does_not_parse",
                      sorted("{} ({})".format(p["url"], why)
                             for p, why in unreadable_identity)[:10])

    if not org_nodes:
        # JSON-LD is not the only place a site declares who it is, and reading
        # it alone is how this check produced its worst false positive: a site
        # that publishes its identity as inline Microdata was told it declares
        # no Organization anywhere on the site. Two further declarations sit in
        # the snapshot already and were not being read.
        #
        # `microdata_types` holds the schema.org type names the page declares
        # in `itemtype` and `typeof` attributes, which is the same claim a
        # JSON-LD `@type` makes. This branch used to read `microdata_count`
        # instead, a count of `itemscope` elements that cannot say what those
        # elements are - and then printed "No page carries inline Microdata
        # attributes either", a sentence about types backed by a count.
        # `_inline_types_on`, four lines up in this same file, has read the
        # types all along.
        #
        # `rdfa_count` is deliberately not read. It counts
        # `[typeof], [vocab]`, and both are already folded into
        # `microdata_types` with the type name attached, which a count is not.
        inline_org = [p for p in pages if _inline_types_on(p) & ORG_IDENTITY_TYPES]
        if inline_org:
            result.skip(
                "organization-markup",
                "the site declares {} inline as Microdata or RDFa on {}, which states the "
                "same identity a JSON-LD block states. The properties of an inline block "
                "are not graded here, because the crawl records the types it declares and "
                "not the values inside it".format(
                    ", ".join(_identity_type_names(
                        _inline_types_on(inline_org[0]) & ORG_IDENTITY_TYPES)),
                    inline_org[0]["url"]))
            return
        # After the inline read, because inline markup is a better answer than
        # a decline: a site that declares its identity in `itemtype` has said
        # who it is whatever its script blocks do. Before the finding, because
        # "None declares an Organization-level JSON-LD type" is exactly the
        # sentence a parse error makes unsupportable.
        if unreadable_identity:
            result.skip("organization-markup",
                        _markup_declared_but_unreadable(
                            # "page", not "identity page": this list holds the
                            # identity pages that could not be read plus any
                            # page anywhere whose broken block names an
                            # identity type outright, and the second kind is
                            # not an identity page.
                            unreadable_identity, "Organization-level",
                            ORG_IDENTITY_TYPES, "page", "pages"))
            return
        # Pages that declare some schema.org type inline, none of them an
        # identity type. Named in the evidence because "no identity markup
        # anywhere" reads differently on a site with no inline markup at all
        # than on one whose inline markup describes something else.
        inline_typed = sorted({t for p in pages for t in _inline_types_on(p)})
        site_name = next((str((p.get("og") or {}).get("og:site_name") or "").strip()
                          for p in pages if (p.get("og") or {}).get("og:site_name")), "")
        # Every homepage, where a site has several. A retailer's root is a
        # country picker and each of its five regional homepages is a
        # storefront of its own; saying only that the picker lacks the block
        # blames the one page nobody shops on.
        homes = by_type.get("home", [])
        referencing = sorted({p["url"] for p, _ in referenced})
        result.add(
            id_hint="no-organization-schema",
            title="No Organization or LocalBusiness markup anywhere on the site",
            # Measured, not assumed: 62% of the brands assistants name in our
            # within-category study also had no Organization markup, and
            # JSON-LD coverage was actually *higher* among the brands they
            # ignore. Absence is worth fixing - it is the one place identity is
            # stated as data - but calling it `high` overstates what the
            # evidence supports. See references/cited-vs-uncited-study.md.
            severity="medium", confidence="high",
            evidence="Checked {} content page(s) including {}. None declares an "
                     "Organization-level JSON-LD type.{}{}{}{}".format(
                         len(pages),
                         ", ".join(example_urls([p["url"] for p in identity_pages], 3)),
                         " That includes every one of the {} homepages this site serves ({}), "
                         "not only the first.".format(
                             len(homes), ", ".join(p["url"] for p in homes[:6]))
                         if len(homes) > 1 else "",
                         " {} {} an organisation only as the publisher, author or brand "
                         "of something on the page ({}), which says who that thing belongs "
                         "to rather than declaring the site's identity.".format(
                             plural(len(referencing), "page"),
                             "names" if len(referencing) == 1 else "name",
                             ", ".join(example_urls(referencing, 2)))
                         if referencing else "",
                         " Inline Microdata and RDFa on those pages declare {} (the "
                         "itemtype and typeof values, lower-cased as the crawl records "
                         "them), none of which is an identity type.".format(
                             ", ".join(inline_typed[:8])) if inline_typed else
                         " No page declares any schema.org type inline as Microdata or "
                         "RDFa either.",
                         " og:site_name declares \"{}\", which names the site but is not "
                         "Organization markup.".format(truncate(site_name, 60)) if site_name
                         else " No page sets og:site_name either."),
            # The constraint is a vocabulary, not a place: the type names this
            # audit accepts as an identity claim. A site declaring something
            # outside that list reads here as declaring nothing, and a reader
            # cannot tell that from "we read the JSON-LD blocks", which is what
            # this tuple used to say.
            checked=("the @type of every JSON-LD block on every crawled page, matched "
                     "against the {} schema.org types this audit accepts as an identity "
                     "claim: Organization, LocalBusiness, and the named subtypes such as "
                     "Store, Restaurant, NGO, Museum and CollegeOrUniversity. A type "
                     "outside that list reads here as no identity markup".format(
                         len(ORG_IDENTITY_TYPES)),
                     "itemtype and typeof attributes on the same pages, matched against the "
                     "same type list, for an identity declared inline as Microdata or RDFa "
                     "rather than in a script block",
                     "the og:site_name tag on those pages, which names the site without "
                     "being schema.org markup"),
            mechanism="C", root_cause="no-org-schema",
            summary="Add one Organization JSON-LD block to the site template so it appears on "
                    "every page.",
            how_to_fix=[
                # Where the template is and who edits it, in this site's own
                # vocabulary wherever the platform detector could name it.
                # "Add the snippet to the site-wide template" was repeated forty
                # times across four real reports - on shops whose own
                # platform CDN address this audit had already parsed, quoted in
                # its evidence, and never drawn a conclusion from.
                *template_change_steps(platform, "the snippet below"),
                *_software_entity_step(snapshot),
                *_org_snippet_steps(snapshot, pages, brand),
                "Replace the placeholder values with the real logo URL and the profile URLs "
                "you actually control.",
                "Keep the `description` identical to the boilerplate you use on LinkedIn and "
                "in your press kit, word for word.",
                "Validate with the schema.org validator, then re-check one page in View Source.",
            ],
            effort="low", owner="developer",
            rationale="Organization markup is the one place a machine can read the "
                      "brand's identity as data rather than inferring it from prose. Without it, "
                      "who you are is a guess.",
            affected_pages=[p["url"] for p in identity_pages],
            snippet=_org_snippet(snapshot, pages, brand),
        )
        return

    page, node = org_nodes[0]

    # Markup everywhere except the one page that matters most. Reported as its
    # own finding rather than folded into the missing-properties one below,
    # because the two are different claims with different root causes, different
    # fixes and different affected pages, and they can both be true at once: one
    # says the block the site publishes is thin, this one says the front door
    # does not publish it at all. A finding carries exactly one root cause.
    # Before anything is recommended for copying, is the block worth copying?
    # Two values that can be judged with no brand name and no language: a name
    # carrying the punctuation a page title uses, and an identity `url` naming
    # a section of the site rather than the site.
    title_name = _name_is_really_a_page_title(_declared(node, "name"), pages)
    section_url = _org_url_is_a_section(node, snapshot)
    # A third value that can be judged with no brand name and no language: a
    # `logo` that is a web page. See `_is_a_page_address`.
    page_logo = _logo_is_a_page(node)

    home = _homepage_without_org_markup(by_type)
    # The finding this guards against was published on a real site: "https://<shop>/
    # declares no Organization-level JSON-LD type and no inline Microdata or
    # RDFa saying the same", published about a homepage whose second ld+json
    # block holds an Organization and a WebSite and is one comma short of
    # parsing. `_homepage_without_org_markup` reads `jsonld_types`, which lists
    # the blocks that parsed, so on that homepage it reports the parser's blind
    # spot. The prescribed fix - move the block into the site-wide template -
    # is a template migration for a syntax error, and the block is already in
    # the site-wide template.
    set_aside_home = []
    if home is not None:
        why = _unreadable_block_reason(home, ORG_IDENTITY_TYPES)
        if why:
            # No signal of its own: the homepage is one of the identity pages,
            # so it is already named in `identity_pages_whose_markup_does_not
            # _parse` above, and two keys holding the same URL is two places
            # for one fact to be wrong.
            set_aside_home = [(home, why)]
            home = None
    if home is not None:
        declaring = sorted({p["url"] for p, _ in org_nodes})
        result.add(
            id_hint="homepage-declares-no-organization-schema",
            title="The homepage declares no Organization markup, though the rest of the site does",
            severity="medium", confidence="high",
            evidence="{} declares no Organization-level JSON-LD type and no inline Microdata or "
                     "RDFa saying the same, while {} of the {} crawled do, including {}."
                     .format(home["url"], plural(len(declaring), "page"), len(pages),
                             ", ".join(example_urls(declaring, 2))),
            # Two sources, both read here and both on the homepage itself: the
            # JSON-LD types, and the schema.org types declared inline. A claim
            # about the homepage rests on the homepage, never on a count of
            # the other pages.
            checked=("the @type of every JSON-LD block on the homepage",
                     "itemtype and typeof attributes on the homepage, for Microdata or RDFa "
                     "declaring the same identity"),
            mechanism="C", root_cause="no-org-schema",
            summary="Emit the Organization block from the site-wide template so the homepage "
                    "carries it too.",
            how_to_fix=[
                "Move the Organization block out of the template that produces it today and "
                "into the site-wide template, so every page including the homepage carries it.",
                # "Keep the values byte-identical" is only advice when the
                # block is right. On a clinic whose block sets `name` to the
                # page's SEO title and `url` to the blog it renders on, that
                # step propagated both faults onto the front door, and the
                # snippet under it carried them too.
                ("Correct the values first - see the finding about this block - and then keep "
                 "them identical everywhere. The block the other pages publish is the one that "
                 "is wrong here, so copying it as it stands spreads the fault to the homepage."
                 if (title_name or section_url or page_logo) else
                 "Keep the values byte-identical to the block the other pages already publish. "
                 "A second identity worded differently is worse than one."),
                where_the_template_is(platform),
                who_edits_the_template(platform),
                *_org_snippet_steps(snapshot, pages, brand, node=node),
                "Confirm the change in View Source on the homepage, not on an inner page.",
            ],
            effort="low", owner="developer",
            rationale="The homepage is the page a consumer reaches first and the "
                      "conventional place a WebSite and Organization node are read from. Markup "
                      "on every page except that one leaves the site's identity unstated exactly "
                      "where it is looked for.",
            affected_pages=[home["url"]],
            snippet=_org_snippet(snapshot, pages, brand, node=node),
        )

    if title_name or section_url or page_logo:
        wrong = []
        if title_name:
            wrong.append('`name` is "{}" - {}'.format(
                truncate(str(_declared(node, "name")), 90), title_name))
        if section_url:
            wrong.append("`url` is {}, which is a section of the site rather than the "
                         "site".format(section_url))
        if page_logo:
            wrong.append("`logo` is {}, which is a web page rather than an image".format(
                page_logo))
        steps = []
        if page_logo:
            steps.append(
                "Set `logo` to the address of the logo image itself - a PNG, SVG or JPEG file "
                "- rather than a page of the site. The field is what a knowledge panel "
                "renders as the brand's mark, and a page cannot be rendered as one.")
        if title_name:
            # Two tests fire this branch and they justify different sentences.
            # "with no separator, no location prefix and no tagline" was
            # printed on both, so a shop whose `name` carries no separator at
            # all was ordered to remove one - and a second finding four
            # sections later, in another skill, named that same string as the
            # site's canonical name and told the owner to propagate it. The
            # step now describes the value that was actually read.
            if "separator" in title_name:
                steps.append(
                    "Set `name` to the organisation's name on its own - what a person says "
                    "when they name it - with no separator, no location prefix and no "
                    "tagline. The page title is a different string with a different job, "
                    "and the template is filling one from the other.")
            else:
                steps.append(
                    "Set `name` to the organisation's name on its own. The value in the "
                    "block is the whole <title> of one page, which is what a template "
                    "writes when it fills `name` from the title variable rather than from "
                    "a name of its own.")
        if section_url:
            steps.append(
                "Set `url` to the site's own home page address. `url` is what a machine "
                "follows to find this organisation again, and it currently leads to one "
                "section of the site.")
        steps.append("Fix the template rather than the page: this block is rendered by one "
                     "template, so every page carrying it carries the same values.")
        steps.append(where_the_template_is(platform))
        steps.append(who_edits_the_template(platform))
        result.add(
            id_hint="organization-identity-values-are-wrong",
            title="The Organization block states the page's title as the organisation's name"
                  if title_name else
                  "The Organization block points `url` at a section of the site"
                  if section_url else
                  "The Organization block's `logo` is a web page, not an image",
            severity="high", confidence="high",
            evidence="{} declares {}, and {}. This is the site's own statement of who it is, "
                     "so it is what an assistant repeats.".format(
                         page["url"], _prop(node, "@type") or "Organization",
                         "; ".join(wrong)),
            # What each half rests on, so a reader can check the test rather
            # than the verdict. Neither is a detector that came back empty:
            # the separator is a character in the value, and the title
            # comparison is against titles the crawl recorded in full.
            checked=("the `name` on the site's own identity block, for the vertical line "
                     "or guillemet a page title uses between its parts - punctuation no "
                     "organisation's name contains",
                     "the same `name` against the <title> of every crawled page, compared "
                     "on letters and digits so capitalisation and spacing do not decide it",
                     "the `url` on the same block against the address this audit was "
                     "pointed at, to see whether it names the site or one section of it"),
            mechanism="C", root_cause="schema-text-mismatch",
            summary="Give the identity block the organisation's own name and its own home "
                    "page address.",
            how_to_fix=steps + _org_snippet_steps(snapshot, pages, brand, node=node),
            effort="low", owner="developer",
            rationale="Structured data is read as the site owner's own assertion, so a name "
                      "that is really a page title is repeated as the organisation's name, "
                      "separators and all. Nothing warns anyone: the block is valid, it "
                      "parses, and every consumer believes it.",
            affected_pages=sorted({p["url"] for p, _ in org_nodes}),
            # Corrected, not copied. `_org_snippet` drops a name that reads as
            # a page title and pulls `url` back to the site root, so what is
            # offered here is the block the site should have rather than the
            # block it has.
            snippet=_org_snippet(snapshot, pages, brand, node=node),
        )

    missing = _missing_props(node, HIGH_VALUE_PROPS["Organization"])

    # A brand that links to no profiles anywhere cannot fill `sameAs`, and this
    # check only ever tested that the key was present - so leaving our own
    # placeholder `.../<your-company>` in the snippet passed, and deleting it,
    # which is the honest thing to do, failed. The tool rewarded pasting
    # placeholder text and penalised removing it. If the site declares no
    # profiles at all, the absence of `sameAs` is not the finding to raise here;
    # freshness-corroboration-audit already reports having none, which is the
    # actual problem and has a different fix.
    #
    # `consulted` is built as each source is actually read, so the report can
    # never name a source this run short-circuited past. When only
    # `description` is missing the two site-wide lookups below never run, the
    # claim rests on the block itself, and the confidence clamp says so.
    consulted = ["every property on the Organization block, including nested "
                 "objects such as address and contactPoint"]
    if "sameAs" in missing:
        consulted.append("every off-site profile link found anywhere on the site, "
                         "which is where `sameAs` would be filled from")
        if not _all_same_as(pages):
            missing = [m for m in missing if m != "sameAs"]

    # Same reasoning for a logo we could not find: we no longer invent one, so
    # we do not then mark the site down for not having the one we invented.
    if "logo" in missing:
        # Named as the sources actually read. It used to say "og:image", which
        # is no longer true of any page but the homepage - and a `checked[]`
        # entry claiming a reading the code does not make is the same defect
        # as a value the site does not publish.
        consulted.append("every `logo` declared in the site's own identity markup, the "
                         "homepage's own og:image, and any image the site puts on more "
                         "than one page whose filename calls it a logo")
        if not _site_logo(snapshot, pages, brand):
            missing = [m for m in missing if m != "logo"]

    # "Absent" and "present and empty" are different defects with different
    # fixes. A block whose `sameAs` is a theme loop over unfilled social fields
    # was reported as omitting `sameAs`; the owner opened View Source, saw the
    # property, and had no reason to believe the rest of the report.
    stating_nothing = [p for p in missing if _states_nothing(node, p)]
    part_blank = [(p, _blank_entries(node, p))
                  for p in HIGH_VALUE_PROPS["Organization"]
                  if p not in missing and _blank_entries(node, p)]

    if missing or part_blank:
        # Every page whose Organization markup has the same holes, not the one
        # page that happened to be graded. A statistics charity publishes a
        # byte-identical `publisher` block on all 47 of its article pages, and
        # the finding said "Seen on 1 of the 47 article pages" while the next
        # finding in the same report, about the same 47 pages, said 47 of 47.
        graded_name = str(_prop(node, "name") or "").strip().lower()
        affected = sorted({
            p["url"] for p, n in org_nodes
            if str(_prop(n, "name") or "").strip().lower() == graded_name
            and set(missing) <= set(_missing_props(n, HIGH_VALUE_PROPS["Organization"]))})
        # Properties this site declares that the snippet does not model. With
        # any of these present the snippet is something to merge into the block
        # the site has, and pasting it over the top deletes real data - five
        # `ContactPoint` entries with sales numbers, in the case that found it.
        extras = _org_extra_props(node)
        absent = [p for p in missing if p not in stating_nothing]
        # Counted as one number over one set: the properties a consumer cannot
        # read off this block, whether they are absent, present and empty, or
        # padded with empty entries. Every sentence below names which is which,
        # because the fixes differ and because an owner who can see the
        # property in View Source stops reading a report that calls it missing.
        unusable = missing + [p for p, _ in part_blank]
        how_to_fix = []
        if absent:
            how_to_fix.append("Add the missing properties to the Organization block you "
                              "already have: {}.".format(", ".join(absent)))
        # Said as what it is, and never as an absence. The property is in the
        # block; what is in the property is nothing.
        if stating_nothing:
            how_to_fix.append(
                "{} already in the block, holding nothing: {}. Fill {} in the theme or CMS "
                "settings the template reads, or stop the template writing the property "
                "when it has no value - an empty string is a published assertion that the "
                "answer is blank.".format(
                    plural(len(stating_nothing), "property is", "properties are"),
                    ", ".join("`{}`".format(p) for p in stating_nothing),
                    "them" if len(stating_nothing) > 1 else "it"))
        for prop, blanks in part_blank:
            how_to_fix.append(
                "`{}` holds {} alongside its real {}. Fill the blank fields in the theme "
                "settings, or the loop that writes them keeps emitting empty strings.".format(
                    prop, plural(blanks, "empty string", "empty strings"),
                    "values" if prop != "sameAs" else "profile URLs"))
        if extras:
            how_to_fix.append(
                "The snippet below is an addition, not a replacement. Your block also "
                "declares {}, which the snippet does not model; keep {} exactly as {} "
                "now.".format(", ".join(extras[:6]),
                              "those" if len(extras) > 1 else "it",
                              "they are" if len(extras) > 1 else "it is"))
        # Advice about the properties this block omits, and no advice about the
        # ones it carries. Both bullets used to print every time: a software
        # vendor whose block lists five `sameAs` profiles was told to fill in
        # `sameAs`, in a finding whose own evidence line said the two missing
        # properties were `name` and `description`.
        # Said, because the snippet changes two values the block already has.
        academic = _academic_identity(snapshot, pages, brand,
                                      _prop(node, "@type") or "Organization",
                                      _snippet_name(node, snapshot, pages, brand))
        if academic and academic[2]:
            how_to_fix.append(
                "The snippet declares `CollegeOrUniversity`, schema.org's type for a "
                "university or college, rather than `{}`, with the full name the site itself "
                "shows, \"{}\", as `name` and {} as `alternateName`.".format(
                    _prop(node, "@type") or "Organization", academic[1],
                    ", ".join('"{}"'.format(n) for n in academic[2])))
        how_to_fix += [_org_prop_fix(prop, pages, brand, snapshot)
                       for prop in HIGH_VALUE_PROPS["Organization"] if prop in unusable]
        how_to_fix += _org_snippet_steps(snapshot, pages, brand, node=node)
        how_to_fix.append("Re-validate after the change.")
        said = []
        if absent:
            said.append("omits {}".format(", ".join(absent)))
        if stating_nothing:
            said.append("declares {} with nothing in {}".format(
                ", ".join(stating_nothing), "them" if len(stating_nothing) > 1 else "it"))
        for prop, blanks in part_blank:
            said.append("declares {} with {} among its entries".format(
                prop, plural(blanks, "empty string", "empty strings")))
        result.add(
            id_hint="organization-schema-missing-properties",
            title="Organization markup is present but {} high-value propert{} unusable".format(
                len(unusable), "y is" if len(unusable) == 1 else "ies are"),
            severity="medium", confidence="high",
            evidence="{} declares {} and {}. The same markup is on {} of the {} "
                     "crawled.".format(
                         page["url"], _prop(node, "@type") or "Organization",
                         "; ".join(said), plural(len(affected), "page"), len(pages)),
            checked=tuple(consulted),
            mechanism="C", root_cause="missing-schema-props",
            summary="Fill in the Organization properties a consumer cannot read off this "
                    "block: {}.".format(", ".join(unusable)),
            how_to_fix=how_to_fix,
            effort="low", owner="developer",
            rationale=_org_missing_rationale(unusable),
            affected_pages=affected or [page["url"]],
            snippet=_org_snippet(snapshot, pages, brand, node=node),
        )
    elif home is None and not (title_name or section_url):
        # Only when nothing else in this check fired. A check that has already
        # reported a finding must never also be listed as clean.
        #
        # Two different quiet answers, and printing the first one over the
        # second was the shape the parse-error defect took here: "markup is
        # present on every page and carries everything" is a *presence* claim,
        # and it is as unsupportable about a homepage whose block does not
        # parse as the absence claim it replaced. Where the homepage was set
        # aside, the decline says so and names the finding that holds it.
        result.skip("organization-markup",
                    _markup_declared_but_unreadable(
                        set_aside_home, "Organization-level", ORG_IDENTITY_TYPES,
                        "homepage", "homepages")
                    if set_aside_home else
                    "Organization-level markup is present on {} and carries name, url, logo, "
                    "description and sameAs".format(page["url"]))


def _homepage_without_org_markup(by_type):
    """The homepage, when the site declares identity markup but not on it.

    A statistics charity publishes a byte-identical publisher block on 47 of
    the 53 pages crawled and nothing at all on its homepage: the crawl records
    an empty `jsonld_types` for the site root, and a browser render of the same
    URL finds no ld+json block either. The report graded the article pages,
    said two high-value properties were missing, and never said the front door
    carries no markup. That is a different problem with a different fix.
    """
    homes = [p for p in (by_type or {}).get("home", []) if not p.get("render_skipped")]
    # A page the browser never reached cannot support a claim about what it
    # declares. The crawl sets `render_skipped` on a thin page it did not
    # render, and absence measured on a page that was not fully read is not
    # absence - it is the same mistake as reporting a challenge page as a site
    # with no content.
    if not homes:
        return None
    if any(_nodes_of(p, ORG_IDENTITY_TYPES) or _declares_type(p, ORG_IDENTITY_TYPES)
           for p in homes):
        return None
    return homes[0]


# What the Organization snippet knows how to write. Anything a site declares
# outside this list survives only if the owner is told to keep it.
_ORG_SNIPPET_PROPS = ("@context", "@type", "name", "url", "logo", "description",
                      "address", "telephone", "sameAs")


def _org_extra_props(node):
    """Properties the site declares that this snippet cannot carry.

    a software vendor's live Organization block holds five `ContactPoint` entries
    with real sales numbers and a `logo` `ImageObject` of a stated 300x300,
    and the instruction printed above the generated block was "Paste the
    snippet below into the <head> of your site-wide template". Following it
    deletes every ContactPoint and swaps the logo for the og:image social
    card. A snippet that models less than the page already has is an addition,
    and the finding has to say so in words.
    """
    return sorted(str(k) for k in (node or {})
                  if not str(k).startswith(("@", "_")) and k not in _ORG_SNIPPET_PROPS)


# The properties through which one node names another organisation: who
# published it, who founded it, who wrote it, whose brand a product carries.
# An Organization found under one of these is a reference, and whatever it
# names, it is not the block in which the site declares who it is.
_REFERENCE_ROLES = frozenset({
    "publisher", "author", "creator", "founder", "founders", "brand", "manufacturer",
    "seller", "provider", "sponsor", "funder", "organizer", "performer", "memberof",
    "parentorganization", "suborganization", "sourceorganization", "copyrightholder",
    "contributor", "editor", "producer", "offeredby", "worksfor", "affiliation",
})


def _only_referenced(node):
    """Is this node nested under a property that names somebody, not the site's own block?"""
    role = str((node or {}).get("_nested_in") or "").replace("_", "").lower()
    return role in _REFERENCE_ROLES


def _speaks_for_the_site(node, brand):
    """May this node's values be published as facts about the audited site?

    `speaks_for_the_brand` is the shared answer. Extraction flattens a nested
    node so a presence check can see it, and a flattened node is
    indistinguishable from one the page declared about itself - so an Event's
    `location`, a Product's `brand` and an Article's `author` all arrive here
    looking like the site's own claims about itself. They are claims about
    somebody else, unless the name on them is the brand's.
    """
    return speaks_for_the_brand(node, str((brand or {}).get("name") or ""))


def _existing_org_node(snapshot, pages, brand=None):
    """The Organization node the site already publishes, the fullest one first.

    Read from the content pages when they have one, and from the whole crawl
    when they do not: a site can declare its Organization on a page this
    skill's content filter never sees, and the snippet must still carry that
    block's own values rather than inventing replacements for them.

    Only from a node that speaks for the site. An Organization lifted out of an
    Event's `performer` is a claim about a band, and every value on it - its
    name, its URL, its logo, its `sameAs` - would go into a block a developer
    is told to paste as the audited company's own identity.
    """
    candidates = [n for p in pages for n in _nodes_of(p, ORG_IDENTITY_TYPES)
                  if _speaks_for_the_site(n, brand)]
    if not candidates:
        candidates = [n for p in (snapshot.get("pages") or [])
                      for n in _nodes_of(p, ORG_IDENTITY_TYPES)
                      if _speaks_for_the_site(n, brand)]
    candidates.sort(key=lambda n: len(_missing_props(n, HIGH_VALUE_PROPS["Organization"])))
    return candidates[0] if candidates else {}


# Characters a template puts between the parts of a page title. None of them
# occurs inside an organisation's own name, in any script. Written with `chr()`
# so this file stays ASCII: vertical line, its fullwidth form used across East
# Asia, and the four guillemets.
_TITLE_SEPARATORS = (chr(0x007C) + chr(0xFF5C) + chr(0x00BB) + chr(0x00AB)
                     + chr(0x203A) + chr(0x2039))
_TITLE_SEPARATOR_RE = re.compile("[" + re.escape(_TITLE_SEPARATORS) + "]")


def _name_is_really_a_page_title(name, pages=()):
    """Why this declared name is a page title rather than a name, or "".

    A dental clinic's only Organization block sets `name` and `description` to
    the page's SEO title, separators and all - the town, the speciality, the
    practice, a programme name, all divided by fullwidth vertical lines. An
    assistant reading that block states the whole string as the clinic's name.

    Worth its own test because of what the audit did with it next: the finding
    about the homepage carrying no identity markup told the owner to keep the
    values byte-identical to the block the other pages publish, and generated a
    snippet carrying that title onto the homepage. Copying a value is only
    advice when the value is right, so the value has to be judged first.

    Needs no brand name and no language, which is why it can run before the
    brand work lands: the separator is the site's own punctuation, and the
    second test compares the value against the page's own <title>.
    """
    text = str(name or "").strip()
    if not text:
        return ""
    if _TITLE_SEPARATOR_RE.search(text):
        return ("it is divided by the separator a page title uses between its parts, "
                "which no organisation's name contains")
    key = comparison_key(text)
    if not key:
        return ""
    # A page whose title *is* the shop name cannot satisfy this test.
    #
    # A leather-goods shop's report opened with "`name` is "<shop> Handmade
    # Leather Goods" - it is the <title> of https://<site>/apps/track123, word
    # for word", at high severity, and ordered the tagline removed. That
    # address is a hosted-store app proxy: it has no title of its own, so the
    # theme falls back to the shop name, and the string this check caught it
    # matching is the shop's own name arriving from the other direction. The
    # same report's next finding named that exact string as the site's
    # canonical name and told the owner to propagate it everywhere.
    #
    # `og:site_name` is the site saying outright what it calls itself, and it
    # declared this string on all 58 crawled pages. Where the site says the
    # value is its name, the value is not a page title.
    for page in pages or ():
        if comparison_key((page.get("og") or {}).get("og:site_name") or "") == key:
            return ""
    matched = [p for p in (pages or ())
               if comparison_key(p.get("title") or "") == key]
    # Exactly one page, for the same reason. A string that is the whole title
    # of several crawled pages is what the template writes when a page supplies
    # no title of its own, which is the shop name again rather than any one
    # page's own title.
    if len(matched) != 1:
        return ""
    return "it is the <title> of {}, word for word".format(matched[0]["url"])


def _org_url_is_a_section(node, snapshot):
    """The declared identity `url`, when it names a subdirectory of the site.

    `url` on an Organization says "this is my home on the web". The clinic's
    block pointed it at the blog the block happened to be rendered on, so the
    address an assistant follows to find the organisation again is a section of
    the site rather than the site.
    """
    declared = _declared(node, "url")
    if isinstance(declared, list):
        declared = declared[0] if declared else ""
    if isinstance(declared, dict):
        declared = declared.get("url") or declared.get("@id") or ""
    declared = str(declared or "").strip()
    origin = str((snapshot or {}).get("origin") or "").rstrip("/")
    if not declared or not origin or not same_site(declared, origin):
        return ""
    if urlparse(declared).path.rstrip("/") == urlparse(origin).path.rstrip("/"):
        return ""
    return declared


# What the snippet writes where the value the site declared was rejected and
# nothing else the crawl holds is a different string.
_NAME_PLACEHOLDER = ("<the organisation's name on its own, as a person says it - "
                     "the value on the site reads as a page title, fill this in>")


def _snippet_name(declared, snapshot, pages, brand):
    """The name to publish in the paste-ready Organization block.

    The site's own `name`, unless this audit has just said in words that the
    value is a page title rather than a name - and then a string that is not
    that same value.

    The second half is the whole point. A leather-goods shop's finding read
    "Set `name` to the organisation's name on its own ... with no separator, no
    location prefix and no tagline", and five lines below it the paste-ready
    block published `"name": "<the exact string the finding had just
    condemned>"`. The fallbacks are `brand["name"]` and the site host, and on
    that site `brand["name"]` is read from `og:site_name`, which carries the
    same string - so rejecting the declared value changed nothing and the
    finding argued with its own snippet.

    A placeholder is the honest answer there. It costs the owner one line and
    it cannot publish the fault the finding is about.
    """
    value = _declared(declared, "name")
    if not _name_is_really_a_page_title(value, pages):
        chosen = value or (brand or {}).get("name") or snapshot["site"]
        return chosen
    # `brand["name"]` is the only alternative offered here. The host is not,
    # and used to be: on the shop this was written for it produced
    # `"name": "https://<the shop>.test"`, which is an address and not a name.
    # A hostname is what a reader would have to correct by hand anyway, and a
    # value that has to be corrected is a placeholder pretending not to be one.
    rejected = comparison_key(str(value or ""))
    fallback = str((brand or {}).get("name") or "").strip()
    if fallback and comparison_key(fallback) != rejected:
        return fallback
    return _NAME_PLACEHOLDER


# The word an institution's own name uses to say it grants degrees. Latin-
# script forms are matched as whole words; the others are written the way the
# institutions write them, inside a longer name: Chinese and Japanese,
# traditional Chinese, Korean, Thai, Arabic and Russian.
_UNIVERSITY_WORD_RE = re.compile(
    r"\b(?:university|universit(?:e|é|à|ät|at|eit|eti|as|ade|ad|et)|"
    r"uniwersytet|college|polytechnic|institute of technology|hochschule)\b|"
    "大学|大學|대학교|"
    "มหาวิทยาลัย|"
    "جامعة|"
    "университет",
    re.I)

# What a template puts between a page's own title and the site's name: a
# hyphen, a bar, a colon, an en or em dash, or a middle dot.
_SITE_NAME_SEPARATOR_RE = re.compile(r"\s+[-|:–—·]\s+")


def _names_the_site_shows(brand, pages):
    """({name: pages showing it}, {names the brand record already holds}).

    Read from the ends of page titles and from `og:site_name`, which is where a
    site prints its own name on every page, and from the names the crawl
    already resolved for it. Nothing here is composed by this audit.
    """
    counts = {}
    for page in pages:
        seen = set()
        parts = [part.strip() for part in _SITE_NAME_SEPARATOR_RE.split(
            " ".join(str(page.get("title") or "").split())) if part.strip()]
        if len(parts) > 1:
            seen.update((parts[0], parts[-1]))
        site_name = " ".join(str((page.get("og") or {}).get("og:site_name") or "").split())
        if site_name:
            seen.add(site_name)
        for name in seen:
            counts[name] = counts.get(name, 0) + 1
    own = set()
    for key in ("fallback_candidates", "authoritative_variants", "alternate_names"):
        own.update(str(n).strip() for n in (brand or {}).get(key) or [] if str(n).strip())
    own.update(str(c.get("name")).strip() for c in (brand or {}).get("name_candidates") or []
               if isinstance(c, dict) and c.get("name"))
    return counts, own


def _academic_identity(snapshot, pages, brand, current_type, current_name):
    """`(type, name, alternateName)` for a university its own block calls an
    Organization under a short name, or None.

    A university declares plain `Organization` with its nickname as `name`,
    and the snippet copied both back. schema.org has `CollegeOrUniversity` for
    exactly this, and the site prints its full name at the end of every title.
    The full name has to be one the site shows site-wide, or one that begins
    with the short name and is shown on more than one page - so a single page
    titled after a product with "college" in it cannot rename a shop - and the
    short name is kept, as `alternateName`, because it is what people call it.
    """
    if str(current_type or "") not in ("Organization", "EducationalOrganization"):
        return None
    # Not asked to be academic, only not to be confidently something a
    # university is not. The site-kind reader places a university on an
    # academic suffix; one on an ordinary address it can read as an
    # organisation with people, which a university also is - and there the
    # degree-granting word in the name the site prints on every page is the
    # academic signal.
    if site_kind(snapshot).is_certainly(LOCAL_BUSINESS, ONLINE_SELLER, PROJECT, PUBLICATION):
        return None
    short = str(current_name or "").strip()
    if not short or short.startswith("<"):
        return None
    if _UNIVERSITY_WORD_RE.search(short):
        return ("CollegeOrUniversity", short, [])
    counts, own = _names_the_site_shows(brand, pages)
    folded = short.casefold()

    def shown_as_the_sites_name(name):
        if name in own or counts.get(name, 0) * 2 >= max(len(pages), 2):
            return True
        return counts.get(name, 0) >= 2 and name.casefold().startswith(folded)

    candidates = sorted(n for n in set(counts) | own
                        if _UNIVERSITY_WORD_RE.search(n) and n.casefold() != folded
                        and shown_as_the_sites_name(n))
    if not candidates:
        return None
    by_reach = lambda n: (-counts.get(n, 0), n)  # noqa: E731
    spelled_out = sorted((n for n in candidates if n.casefold().startswith(folded)), key=by_reach)
    full = (spelled_out or sorted(candidates, key=by_reach))[0]
    return ("CollegeOrUniversity", full, [short] + [n for n in candidates if n != full])


def _org_snippet(snapshot, pages, brand, node=None):
    """Organization, or LocalBusiness when the site shows a real-world address.

    Three things changed here, all the same mistake in different clothes.

    `sameAs` used to ship `.../<your-company>` placeholder URLs when the brand
    linked to no profiles. A developer then has two choices: publish two dead
    URLs, or delete the key and trip our own follow-up check, which tests for
    the key's presence and not its contents. That rewarded pasting placeholder
    text and penalised removing it. The key is now omitted when there is
    nothing true to put in it, and the follow-up check ignores an absent
    `sameAs` when the site genuinely has no profiles to declare.

    `logo` used to be invented. `description` used to be a placeholder while
    the site's own defining sentence sat elsewhere in the same report.

    And the finding is titled "No Organization **or LocalBusiness** markup" on
    a site whose address, telephone and opening hours the audit has already
    parsed - then emitted bare Organization every time. A business someone
    visits gets LocalBusiness, which is the type those facts belong to; see
    `_identity_type` for why an address alone is not enough to say so.

    And the site's own values win over ours wherever it has published one. The
    generated block used to be built from scratch, so a company that declares
    a 300x300 `logo` `ImageObject` got its logo replaced by the og:image
    social card, and a declared `PostalAddress` was rebuilt from a different
    reading of the same site. Replacing a value the site published with our
    reconstruction of it is a regression the audit had the data to avoid.
    """
    declared = node if node is not None else _existing_org_node(snapshot, pages, brand)
    facts = _contact_facts_for_snippet(pages, is_multi_location(snapshot),
                                       str((brand or {}).get("name") or ""))
    declared_same_as = _declared(declared, "sameAs")
    if isinstance(declared_same_as, str):
        declared_same_as = [declared_same_as] if declared_same_as else []
    same_as = _all_same_as(pages, brand, declared=declared_same_as)

    payload = {
        "@context": "https://schema.org",
        # The site's own type, when it declared one. `_identity_type` is the
        # answer for a site with no Organization markup at all; overruling a
        # `Museum` or a `Store` the site published is not this snippet's job.
        "@type": _prop(declared, "@type") or _identity_type(snapshot, pages, facts),
        # The site's own name, unless the site's own name is its page title.
        # `_name_is_really_a_page_title` says why; a snippet that copies a
        # title into `name` publishes the fault it was generated to fix.
        "name": _snippet_name(declared, snapshot, pages, brand),
        # A declared `url` only when it points at the site being audited.
        # `url` on an Organization block says "this is my home on the web", and
        # a node that speaks for the brand can still carry somebody else's
        # address - a directory listing, a parent group's site, a campaign
        # microsite - which the snippet would then publish as this site's own
        # canonical home. The audited origin is the one URL that cannot be
        # wrong about which site this is.
        "url": _brand_url(_declared(declared, "url"), snapshot),
    }
    # A university under its nickname gets its own type and its full name, the
    # nickname kept beside it. See `_academic_identity`.
    academic = _academic_identity(snapshot, pages, brand, payload["@type"], payload["name"])
    if academic:
        kind, full, alternates = academic
        rest = {k: v for k, v in payload.items() if k not in ("@context", "@type", "name")}
        payload = {"@context": payload["@context"], "@type": kind, "name": full}
        if alternates:
            payload["alternateName"] = alternates if len(alternates) > 1 else alternates[0]
        payload.update(rest)
    # `logo` is read from something the site presents as its mark, never from
    # another page's social card. See `_site_logo`.
    # An admitted placeholder where the site publishes nothing that qualifies.
    # Omitting the key left the block silent about a property the finding above
    # it asks for, and the alternative that used to fill it was another page's
    # social card. `hold_back_invented_values` in `compose_report` writes this
    # same sentence over any snippet value the crawl never saw, and reads a
    # value in angle brackets as already honest about being one.
    payload["logo"] = (_image_url(_declared(declared, "logo"))
                       or _site_logo(snapshot, pages, brand)
                       or "<logo URL - not found on the site, fill this in>")
    # Each source cleaned on its own, and the first one that survives wins. A
    # meta description that is nothing but a truncated fragment used to be
    # published as the company's own sentence; cleaned to nothing, it now falls
    # through to the site's defining sentence the way an absent one already did.
    #
    # The two derived sources are also held to outliving their own page's news
    # cycle; the declared one is not. A `description` the site put on its own
    # Organization block is its deliberate statement of who it is, and dropping
    # it would delete real data from the block the owner is told to paste. A
    # meta description and a sentence read out of prose are ours to judge, and
    # a homepage's top news item published as an identity goes stale in weeks.
    labels = _link_labels(pages)
    brand_name = str((brand or {}).get("name") or "")
    description = next((cleaned for cleaned in (
        _paste_ready_description(_declared(declared, "description"), labels, brand_name),
        _outlives_the_news(
            _paste_ready_description(_site_description(pages), labels, brand_name),
            brand_name),
        _paste_ready_description(_brand_definition(pages, brand), labels, brand_name),
    ) if cleaned), "")
    # An admitted placeholder where the site has written nothing usable about
    # itself, exactly as `logo` above does. Leaving the key out was silent
    # about a property the finding asks for, and the source that used to fill
    # it in that case was whatever the meta description happened to hold - on a
    # public library, "Hours: Monday: 12-8pm ... Email: General E-Mail Library",
    # in the same report that reported that site's meta descriptions as a
    # defect. A value one finding calls wrong cannot be another finding's fix.
    payload["description"] = description or _description_placeholder(pages)
    declared_address = _declared(declared, "address")
    if declared_address:
        payload["address"] = declared_address
    elif facts.get("address"):
        payload["address"] = {"@type": "PostalAddress", "streetAddress": facts["address"]}
        for key, field in (("locality", "addressLocality"), ("region", "addressRegion"),
                           ("postal_code", "postalCode"), ("country", "addressCountry")):
            if facts.get(key):
                payload["address"][field] = facts[key]
    # Through `_publishable_telephone`, which is where a value that is not a
    # number stops. The site's own declared value goes through it too: a
    # template that wrote `[9910901961]` into a `tel:` href will have written
    # the same string into its markup.
    telephone = _publishable_telephone(
        _declared(declared, "telephone") or facts.get("telephone"), pages)
    if telephone:
        payload["telephone"] = telephone
    if same_as:
        payload["sameAs"] = same_as
    software = _software_entity(snapshot, declared, payload)
    return _snippet_block(software or payload)


def _software_entity(snapshot, declared, payload):
    """The block for a site that is a program rather than an organisation, or None.

    A database engine's documentation site - a repository, a licence, nothing
    for sale, no team page - was classified by this same audit as a software
    project, and handed an `Organization` block naming the program as the
    organisation, with a logo placeholder and no word about what it is.
    schema.org has a type for the thing the site is about, and an assistant
    asked what the program is reads it there.

    Only where the site declared no type of its own, and only on a confident
    determination: a company that ships software is still an Organization.
    Every value is one the Organization block had already read off the site;
    the ones that belong to an organisation - an address, a telephone - stay
    out, and a logo becomes `image` only when the site shows one.
    """
    if _prop(declared or {}, "@type") or not site_kind(snapshot).is_certainly(PROJECT):
        return None
    software = {"@context": payload["@context"], "@type": "SoftwareApplication",
                "name": payload.get("name"), "url": payload.get("url")}
    description = str(payload.get("description") or "")
    software["description"] = (description if not description.startswith("<") else
                               "<one sentence saying what this program does and who it is "
                               "for - fill this in>")
    logo = str(payload.get("logo") or "")
    if logo and not logo.startswith("<"):
        software["image"] = logo
    if payload.get("sameAs"):
        software["sameAs"] = payload["sameAs"]
    return software


def _software_entity_step(snapshot, declared=None):
    """The fix step that says why the block below is not an Organization, or []."""
    if _prop(declared or {}, "@type") or not site_kind(snapshot).is_certainly(PROJECT):
        return []
    return ["This site reads as a software project ({}), so the block below declares the "
            "program itself as a `SoftwareApplication` rather than an Organization. If a "
            "company or a foundation stands behind it, add an Organization node for that "
            "body beside it and point the program's `publisher` at it.".format(
                site_kind(snapshot).why())]


def _org_snippet_steps(snapshot, pages, brand, node=None):
    """The fix steps saying why the block's `logo` or `telephone` is not what it seems.

    `_org_snippet` pastes a touch icon as `logo` when nothing else qualifies,
    and leaves `telephone` out when a chat link's number has no country code.
    Both are right, and both are invisible in the block itself: the owner sees
    a small icon, or no number, and no reason. `_logo_reading` and
    `_telephone_verdict` each write the reason, and it goes here, beside the
    block, rather than nowhere.
    """
    declared = node if node is not None else _existing_org_node(snapshot, pages, brand)
    steps = []
    # A `logo` the site declared wins in the block, so no reading of ours is
    # used and there is nothing to explain.
    if not _image_url(_declared(declared, "logo")):
        steps.append(_logo_reading(snapshot, pages, brand)[1])
    facts = _contact_facts_for_snippet(pages, is_multi_location(snapshot),
                                       str((brand or {}).get("name") or ""))
    steps.append(_telephone_verdict(
        _declared(declared, "telephone") or facts.get("telephone"), pages)[1])
    return [step for step in steps if step]


# A telephone number as a telephone number is written: an optional leading
# plus, then digits and the punctuation people group them with. Square
# brackets, letters and anything else are not in it.
_DIALABLE_RE = re.compile(r"^\+?[0-9][0-9 ()./-]{5,}$")

# Where a site writes a telephone number into a link other than `tel:`. These
# are messaging platforms whose share links carry the number in the address,
# and naming them here is the same thing `_PROFILE_HOSTS` above does: a product
# this code has to recognise, never a site being audited.
_PHONE_IN_LINK_RE = re.compile(
    r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=|web\.whatsapp\.com/send\?phone=|"
    r"whatsapp\.com/send\?phone=|t\.me/|telegram\.me/)\+?([0-9]{7,20})", re.I)


def _numbers_the_site_writes_into_links(pages):
    """Every telephone number this site puts in a link, as the site wrote it.

    `tel:` hrefs, which the crawl already records as `declared_phones`, plus
    the messaging links above. The second source exists because a great many
    small shops publish no `tel:` link at all and one chat link instead, so the
    only place the site writes its number in full is that address.
    """
    written = []
    for page in pages:
        for value in ((page.get("contact_facts") or {}).get("declared_phones") or []):
            text = str(value or "").strip()
            if text:
                written.append(text)
        links = page.get("links") or {}
        for bucket in ("internal", "external", "nav", "footer"):
            for entry in (links.get(bucket) or []):
                if not isinstance(entry, dict):
                    continue
                match = _PHONE_IN_LINK_RE.search(str(entry.get("url") or ""))
                if match:
                    written.append("+" + match.group(1))
    return written


def _publishable_telephone(value, pages):
    """`value` as a number worth pasting, in the fullest form the site writes it.

    Two faults in one snippet, both of them published to a leather-goods shop
    twice in one report as `"telephone": "[9910901961]"`.

      the square brackets   they are characters in a JSON string, so pasting
                            the block publishes a `telephone` of
                            `[9910901961]`, which nothing can dial
      the missing prefix    the only place that site writes its number is a
                            chat link carrying `+91` in front of it, and the
                            snippet published the number without the country
                            code - a different number, in a different country

    So a value is checked for being dialable at all, and then replaced by the
    longest number the site itself writes that ends with the same digits. That
    second step is what restores a dropped country code, and it can only ever
    substitute a string the site published.

    Returns "" where neither the value nor anything the site writes is a
    dialable number. The callers then omit the property or print their
    placeholder, which is the same discipline `logo` and `description` follow:
    a placeholder costs a minute and a wrong number is published.

    And "" where the number's country code cannot be established. See
    `_telephone_verdict`, which also says why.
    """
    return _telephone_verdict(value, pages)[0]


def _telephone_verdict(value, pages):
    """(number to publish or "", why it was left out or "").

    A chat link's number has to carry its country code, and the crawl writes a
    plus in front of whatever digits it finds there. An Indian shop's only
    number is `wa.me/8291579185` - ten digits, the owner's national number
    with the code left off - and the block published `"telephone":
    "+8291579185"`, which dials South Korea. So a number whose plus is ours
    rather than the site's is published only where its country code can be
    established: it begins with the calling code of the country the site's own
    signals place it in, or, where no signal says, it is long enough to hold a
    country code at all.
    """
    text = " ".join(str(value or "").split())
    if not text:
        return "", ""
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) < 7:
        return "", ""
    best, best_digits = "", digits
    for other in _numbers_the_site_writes_into_links(pages):
        other_digits = re.sub(r"[^0-9]", "", other)
        if len(other_digits) > len(best_digits) and other_digits.endswith(digits):
            best, best_digits = " ".join(other.split()), other_digits
    if best and _DIALABLE_RE.match(best):
        chosen = best
    elif _DIALABLE_RE.match(text):
        chosen = text
    else:
        return "", ""
    number = re.sub(r"[^0-9]", "", chosen)
    if not chosen.startswith("+") or not _only_in_a_chat_link(number, pages) \
            or _written_with_its_country_code(number, pages):
        return chosen, ""
    country, how = _site_country(pages)
    code = _CALLING_CODES.get(country, "")
    if code:
        if number.startswith(code):
            return chosen, ""
        read_as = _calling_code_of(number)
        return "", (
            "`telephone` is left out of the block. The only place the site writes {} is a "
            "chat link, which has to carry the number's country code, and this one does "
            "not: {} ({}), where numbers start +{}, and read as it stands it would start {} "
            "- a number in another country. Add the line yourself with its country code, "
            "+{} followed by the number, if that is the business's own line.".format(
                number, "the site is in {}".format(_COUNTRY_NAMES.get(country, country.upper())),
                how, code,
                "+{} ({})".format(read_as, _COUNTRY_NAMES.get(
                    _COUNTRY_OF_CODE.get(read_as, ""), "another country's code"))
                if read_as else "with a code no country uses", code))
    if len(number) < _SHORTEST_NUMBER_WITH_A_COUNTRY_CODE:
        return "", (
            "`telephone` is left out of the block. The only place the site writes {} is a "
            "chat link, which has to carry the number's country code, and {} digits is too "
            "short to hold one; nothing on the site says which country it is in. Add the "
            "line yourself with its country code.".format(number, len(number)))
    return chosen, ""


# How many digits a number needs before it can hold a country code as well as
# a subscriber number. Ten is an Indian or a North American number with no
# code in front; very few countries' full international numbers are that short.
_SHORTEST_NUMBER_WITH_A_COUNTRY_CODE = 11

# Calling codes by ISO 3166 country. Held as data about numbering plans, the way
# `_PHONE_IN_LINK_RE` holds messaging hosts: a fact this code has to know, never
# a site being audited. A country missing here is a country whose sites fall to
# the length test above, which is the cautious direction.
_CALLING_CODES = {
    "in": "91", "id": "62", "jp": "81", "kr": "82", "cn": "86", "tw": "886",
    "hk": "852", "sg": "65", "my": "60", "th": "66", "vn": "84", "ph": "63",
    "pk": "92", "bd": "880", "lk": "94", "np": "977", "ae": "971", "sa": "966",
    "ir": "98", "tr": "90", "il": "972", "eg": "20", "ng": "234", "ke": "254",
    "za": "27", "gb": "44", "ie": "353", "fr": "33", "de": "49", "es": "34",
    "it": "39", "nl": "31", "be": "32", "pt": "351", "ch": "41", "at": "43",
    "se": "46", "no": "47", "dk": "45", "fi": "358", "pl": "48", "cz": "420",
    "gr": "30", "ru": "7", "ua": "380", "br": "55", "mx": "52", "ar": "54",
    "cl": "56", "pe": "51", "au": "61", "nz": "64", "us": "1", "ca": "1",
}
_COUNTRY_NAMES = {
    "in": "India", "id": "Indonesia", "jp": "Japan", "kr": "South Korea", "cn": "China",
    "tw": "Taiwan", "hk": "Hong Kong", "sg": "Singapore", "my": "Malaysia",
    "th": "Thailand", "vn": "Vietnam", "ph": "the Philippines", "pk": "Pakistan",
    "bd": "Bangladesh", "lk": "Sri Lanka", "np": "Nepal", "ae": "the UAE",
    "sa": "Saudi Arabia", "ir": "Iran", "tr": "Turkey", "il": "Israel", "eg": "Egypt",
    "ng": "Nigeria", "ke": "Kenya", "za": "South Africa", "gb": "the UK",
    "ie": "Ireland", "fr": "France", "de": "Germany", "es": "Spain", "it": "Italy",
    "nl": "the Netherlands", "be": "Belgium", "pt": "Portugal", "ch": "Switzerland",
    "at": "Austria", "se": "Sweden", "no": "Norway", "dk": "Denmark", "fi": "Finland",
    "pl": "Poland", "cz": "Czechia", "gr": "Greece", "ru": "Russia", "ua": "Ukraine",
    "br": "Brazil", "mx": "Mexico", "ar": "Argentina", "cl": "Chile", "pe": "Peru",
    "au": "Australia", "nz": "New Zealand", "us": "the US", "ca": "Canada",
}
# The first country listed for a code, for naming the one a number would dial.
_COUNTRY_OF_CODE = {}
for _iso, _code in _CALLING_CODES.items():
    _COUNTRY_OF_CODE.setdefault(_code, _iso)

# Country-code top-level domains sold to anyone as generic words. A `.co` or
# `.io` address says nothing about where a business is.
_GENERIC_COUNTRY_DOMAINS = frozenset({
    "co", "io", "ai", "tv", "me", "fm", "ly", "to", "gg", "cc", "ws", "sh", "ac",
    "vc", "gd", "so", "la", "im", "is"})

# The one ccTLD whose country code is spelled differently.
_DOMAIN_COUNTRY = {"uk": "gb"}


def _calling_code_of(number):
    """The calling code a digit string begins with, longest match first, or ""."""
    for length in (3, 2, 1):
        if number[:length] in _COUNTRY_OF_CODE:
            return number[:length]
    return ""


def _region_of(tag):
    """The region subtag of a language tag: `en-IN` and `en_IN` are both "in"."""
    for part in re.split(r"[-_]", str(tag or ""))[1:]:
        if len(part) == 2 and part.isalpha():
            return part.lower()
    return ""


def _site_country(pages):
    """(country, how this audit knows) from the site's own signals, or ("", "").

    Four signals, each something the site states: its address's country
    domain, the region on its `lang` attributes, the region on its `og:locale`,
    and a country declared on its own PostalAddress. Where two of them name
    different countries the answer is "" - a British shop on a `.in` address
    is not guessed at.
    """
    found = {}
    for page in pages:
        host = (urlparse(str(page.get("final_url") or page.get("url") or "")).hostname
                or "").lower()
        label = host.rsplit(".", 1)[-1] if "." in host else ""
        if len(label) == 2 and label.isalpha() and label not in _GENERIC_COUNTRY_DOMAINS:
            country = _DOMAIN_COUNTRY.get(label, label)
            found.setdefault(country, "its address ends .{}".format(label))
        region = _region_of(page.get("lang"))
        if region:
            found.setdefault(region, 'its pages declare lang="{}"'.format(page.get("lang")))
        locale = _region_of((page.get("og") or {}).get("og:locale"))
        if locale:
            found.setdefault(locale, 'its og:locale is "{}"'.format(
                (page.get("og") or {}).get("og:locale")))
        for node in _nodes_of(page, ORG_IDENTITY_TYPES):
            address = node.get("address")
            if isinstance(address, list):
                address = address[0] if address else None
            if isinstance(address, dict):
                declared = str(address.get("addressCountry") or "").strip().lower()
                if len(declared) == 2 and declared.isalpha():
                    found.setdefault(declared, "its own markup declares addressCountry "
                                               "{}".format(declared.upper()))
    if len(found) != 1:
        return "", ""
    return next(iter(found.items()))


def _only_in_a_chat_link(number, pages):
    """Is a messaging link the one place these digits appear with a plus?"""
    for page in pages:
        links = page.get("links") or {}
        for bucket in ("internal", "external", "nav", "footer"):
            for entry in (links.get(bucket) or []):
                if not isinstance(entry, dict):
                    continue
                match = _PHONE_IN_LINK_RE.search(str(entry.get("url") or ""))
                if match and match.group(1) == number:
                    return True
    return False


def _written_with_its_country_code(number, pages):
    """Does the site itself write these digits after a `+` or an `00`?

    In its visible text, or inside a link address - `wa.me/+91...` is the site
    writing its own plus, and `tel:+91...` the same.
    """
    for page in pages:
        squeezed = re.sub(r"[\s().\-]", "", str(page.get("text") or ""))
        if "+" + number in squeezed or "00" + number in squeezed:
            return True
        links = page.get("links") or {}
        for bucket in ("internal", "external", "nav", "footer"):
            for entry in (links.get(bucket) or []):
                if not isinstance(entry, dict):
                    continue
                address = str(entry.get("url") or "").replace("%2B", "+").replace("%2b", "+")
                if "+" + number in re.sub(r"[\s().\-]", "", address):
                    return True
    return False


def _contact_facts_for_snippet(pages, branches=False, brand_name=""):
    """Real-world location facts the crawl already extracted.

    Only values the site *declared* are treated as safe to paste. A telephone
    number in a `tel:` link is declared; one scraped out of prose by a regular
    expression is a guess, and a guess does not belong in structured data that
    a machine will repeat as fact. The street and postcode are pattern matches,
    so they go in with an instruction to check them rather than silently.
    """
    out = {}
    for page in pages:
        facts = page.get("contact_facts") or {}
        # On a site with branches this is one shop's number. A bakery with
        # three shops was offered the Cherche-Midi shop's line as the
        # `telephone` for its whole company record, which would tell every
        # machine reading the site that one shop's phone is the only phone.
        #
        # And from a page that speaks for the business, wherever the crawl
        # typed one. A `tel:` link is declared markup, which is why it is
        # trusted at all, but nothing in it says whose line it is: the same
        # class of error as a logo taken from another page's social card. A
        # supplier directory, a press page naming an agency's switchboard and
        # an emergency-services notice all publish `tel:` links that are not
        # the company's, and the site-wide number a visitor would call is in
        # the template on the home, about and contact pages anyway.
        if not branches and not out.get("telephone") \
                and page.get("page_type") in _WHOLE_BRAND_PAGE_TYPES \
                and facts.get("declared_phones"):
            out["telephone"] = facts["declared_phones"][0]
        if facts.get("has_address"):
            out["has_address"] = True
        # Opening hours, read from the site's own markup. `_identity_type`
        # tests this key as one of the three ways a site earns `LocalBusiness`
        # rather than a bare `Organization`, and nothing ever set it - a
        # `grep` over the whole repository found one read and no write - so
        # that branch was dead for the life of the build, and a single-site
        # restaurant publishing its hours got the wrong snippet.
        for node in page.get("jsonld") or []:
            if node.get("openingHoursSpecification") or node.get("openingHours"):
                out["opening_hours"] = True
                break

    # The address comes from markup or it does not go in.
    #
    # It used to come from `street_hint` and `postcode_hint`, which are regular
    # expressions run over page text, and the docstring above said they went in
    # "with an instruction to check them". Nobody checks a paste-ready snippet.
    # What four real sites got handed:
    #
    #   "2026 Shake Shack Hits the Road"  a year and a blog headline
    #   "9 Let's Circle"                  a $9 nail polish called "Let's Circle Back"
    #   "1 million row"                   a sentence about database rows
    #   postalCode "15880"                a cosmetic colour-index code, CI 15880
    #   postalCode "00005"                the price "$0.00005 / event"
    #
    # Every one of those was offered as the address to publish in schema.org
    # Organization markup - the field search engines and assistants treat as
    # ground truth about where a company is. Two of the four sites have no
    # premises at all. A pattern that matches an address shape is evidence the
    # site *mentions* something address-like, which is all `has_address` claims;
    # it is not a fact about the organisation, and only a `PostalAddress` the
    # site declared itself is.
    declared = _declared_address(pages, whole_brand_only=branches, brand_name=brand_name)
    if declared:
        out.update(declared)
    return out


# schema.org types that describe the business rather than one of its places.
# `LocalBusiness` and its subtypes describe a place, and a chain declares one
# per branch - which is correct, and is exactly why their addresses must not be
# read as the company's.
_WHOLE_BRAND_JSONLD_TYPES = frozenset({
    "organization", "corporation", "ngo", "educationalorganization",
    "governmentorganization", "performinggroup", "sportsorganization",
    "onlinestore", "onlinebusiness", "brand", "airline", "consortium",
    "librarysystem", "medicalorganization", "newsmediaorganization",
    "project", "researchorganization", "fundingscheme", "workersunion",
    "politicalparty",
})


def _speaks_for_the_whole_brand(node):
    """Is this node about the business, rather than about one of its places?"""
    declared = jsonld_type_names(node.get("@type"))
    if declared & VISITABLE_JSONLD_TYPES:
        return False
    return bool(declared & _WHOLE_BRAND_JSONLD_TYPES)


def _declared_address(pages, whole_brand_only=False, brand_name=""):
    """A PostalAddress the site published in its own markup, or nothing.

    A gym chain's report offered its homepage this paste-ready Organization
    block:

        "streetAddress": "122 The Broadway", "postalCode": "SW19 1RH"

    That is the Wimbledon branch's own address, lifted from the one branch page
    the crawl happened to reach, and presented as the company's. The registered
    office is four miles and one postcode away, printed on the site's own
    privacy page.

    The telephone reader above already refuses to do this - "a bakery with
    three shops was offered the Cherche-Midi shop's line as the `telephone` for
    its whole company record" - and the guard was never extended to the
    address, which is the same mistake with a longer value.

    `whole_brand_only` is set on a multi-location site, and then reads an
    address only from a node that speaks for the business. A single-location
    business is left alone: its one place's address really is the company's,
    and that is the common case.

    That guard only ever asked what *type* the node is, and it runs on chains.
    A ticket marketplace is neither: one company, no branches, and a performer
    page whose Event declares a `location`. Extraction flattens that venue into
    a node of its own, so the first `address` on the site belonged to an arena,
    and the paste-ready block installed it as the company's head office - the
    one fabricated fact in 54 findings across six sites, in the field a
    consumer treats as ground truth about where a company is. Whose node it is
    is a separate question from what type it is, and `speaks_for_the_brand`
    answers it: a node lifted out of a subject-changing property states a fact
    about that subject, not about the site.
    """
    for page in pages:
        for node in page.get("jsonld") or []:
            if not speaks_for_the_brand(node, brand_name):
                continue
            if whole_brand_only and not _speaks_for_the_whole_brand(node):
                continue
            address = node.get("address")
            if isinstance(address, list):
                address = address[0] if address else None
            if not isinstance(address, dict):
                continue
            street = str(address.get("streetAddress") or "").strip()
            if not street:
                continue
            found = {"address": street}
            for key, field in (("postal_code", "postalCode"),
                               ("locality", "addressLocality"),
                               ("region", "addressRegion"),
                               ("country", "addressCountry")):
                value = address.get(field)
                if isinstance(value, dict):
                    value = value.get("name")
                if isinstance(value, str) and value.strip():
                    found[key] = value.strip()
            return found
    return {}


def _offer_props(offer):
    """The properties this kind of Offer is supposed to carry.

    `AggregateOffer` states a range with `lowPrice` and `highPrice` and has no
    single `price`, which is correct and is what this report's own advice tells
    people to use for a range. Grading it against the flat `Offer` shape
    reported a coffee roaster's valid range pricing as an offer "missing
    price", and the suggested fix would have replaced a correct range with a
    single made-up number.
    """
    if "aggregateoffer" in jsonld_type_names(offer.get("@type")):
        return ["lowPrice", "highPrice", "priceCurrency"]
    return HIGH_VALUE_PROPS["Offer"]


def _offer_of(node):
    """The Offer on a product node, including the one inside a variant.

    `ProductGroup` with `hasVariant[].offers` is how a shop with sizes or
    colours is supposed to publish its prices, and it carries strictly more
    than a flat `Product.offers`: a price per variant, plus return and shipping
    policies. Reading only the top level, an eyewear retailer's complete,
    schema-valid markup was reported as having "no offers object" with high
    confidence, and the paste-ready fix would have replaced it with a flatter
    block that had none of that. The report offered a downgrade as a repair.
    """
    offers = node.get("offers")
    offer = offers[0] if isinstance(offers, list) and offers else offers
    if isinstance(offer, dict):
        return offer
    variants = node.get("hasVariant")
    if isinstance(variants, dict):
        variants = [variants]
    for variant in variants or []:
        if not isinstance(variant, dict):
            continue
        nested = variant.get("offers")
        nested = nested[0] if isinstance(nested, list) and nested else nested
        if isinstance(nested, dict):
            return nested
    return None


# The keys under which a lifted node is still the page's own subject.
#
# `page_extract._flatten_jsonld` lifts every nested node out of its parent and
# marks it with `_nested_in`, so a page's own `Product` arrives at these checks
# looking the same whether the site wrote it at the top level or hung it off
# the `WebPage` that describes the address. These three keys are schema.org's
# way of saying "this page is about that thing", so a node lifted from one of
# them is the page speaking about itself.
_SUBJECT_KEYS = {"mainentity", "mainentityofpage", "about"}

# The keys under which a lifted node is a different thing living somewhere
# else. Every one of them takes a `Thing` that is not what the block is about:
# the product a review reviews, the products a product resembles, the entries
# of a list.
_MENTION_KEYS = {"itemreviewed", "issimilarto", "isrelatedto", "relateditem",
                 "isaccessoryorsparepartfor", "isconsumablefor",
                 "itemlistelement"}


def _mentions_another_thing(node):
    """Is this block a reference to something that lives on another page?

    One shop's product page states its price the way schema.org allows -
    `offers[0].priceSpecification[0]` - and carries a customer review whose
    `itemReviewed` names the same product again, with no `offers` on it because
    a review is not a price list. Extraction lifts that second block out, so the
    page arrived here declaring two `Product` nodes: the priced one the site
    published and a bare mention of it. The Offer check read the mention and
    reported the page as having "no offers object", quoting a page whose own
    snapshot in the same run holds the Offer. Four of that site's twelve
    identical product pages were named and eight were not, because only four of
    them had a review.

    A mention is never the page's own markup. Nothing here may make a claim
    about a page from one.
    """
    return str((node or {}).get("_nested_in") or "").lower() in _MENTION_KEYS


def _own_sellable_nodes(page):
    """The sellable blocks this page publishes as its own subject."""
    return [node for node in _nodes_of(page, PRODUCT_TYPES)
            if str(node.get("_nested_in") or "").lower() in _SUBJECT_KEYS
            or not node.get("_nested_in")]


def _declares_one_thing_for_sale(page):
    """Does this page's own markup say it is one item at one price?

    One node, not any node. A grid whose template emits a `Product` block per
    tile declares many things for sale and is still a shelf - which is the
    defect this guard must not reopen, a grid of 36 links to item pages told to
    publish `Product` markup.
    """
    return len(_own_sellable_nodes(page)) == 1



# Marks that name a delivery point at a post office rather than a place with a
# door. A volunteer non-profit's PO Box was counted as one of three "branches"
# and the paste-ready block told it to publish `LocalBusiness` markup for a
# pigeonhole in a sorting office - an address no visitor can go to, and the one
# thing `LocalBusiness` is for.
#
# Multi-word or unambiguous forms only. A bare `BP` is a fuel company before it
# is a boite postale and a bare `apartado` is an ordinary Spanish noun, and
# disowning a real branch address is the more expensive of the two mistakes.
_POST_BOX_RE = re.compile(
    r"(?:p\.?\s?o\.?\s?box|post\s?office\s?box|postal\s?box|p\.?\s?m\.?\s?b\.?|"
    r"private\s?bag|postbus|postfach|postboks|bo[iî]te\s?postale|"
    r"apartado\s+(?:postal|de\s+correos)|casella\s?postale|caixa\s?postal)"
    # Nothing but separators between the mark and the number the street
    # pattern read as a house number. A page that prints a PO Box on one line
    # and a real street on another says both, and only the address the mark
    # actually introduces is a pigeonhole.
    r"[\s.,:;#–—-]{0,4}$", re.I)

# Page types a site publishes about itself and the places it keeps. An address
# on one of these is the site's own by what the page is for, whatever words
# happen to surround it - which is what keeps a branch page printing a bare
# address in an `<address>` element from being disowned below.
_PAGES_ABOUT_OUR_OWN_PLACES = frozenset({"location", "contact", "home", "about"})

# How much of the page around the address is read to decide whose it is.
# Asymmetric on purpose: a footer address sits under the copyright line that
# names the brand, so the reach backwards has to clear it, while everything
# after the postcode is the next fact on the page.
_ATTRIBUTION_CHARS_BEFORE = 160
_ATTRIBUTION_CHARS_AFTER = 80


def _where_the_address_sits(page):
    """`(the street hint, its offset in the page text, the page text)`.

    Offset -1 when the recorded hint cannot be found in the stored text.
    `contact_facts` is read from a copy of the page with other people's words
    removed and with URLs stripped out, so the two strings can disagree; when
    they do, nothing around the address can be read and the tests below say so
    rather than guessing.
    """
    hint = ((page.get("contact_facts") or {}).get("street_hint") or "").strip()
    # `truncate` marks a string it cut. The mark is not on the page.
    hint = hint.rstrip("…").rstrip(".").strip()
    text = page.get("text") or ""
    if not hint or not text:
        return hint, -1, text
    return hint, text.find(hint), text


def _address_is_a_post_box(page):
    """Is the address this page prints a mailbox rather than somewhere to go?

    The street patterns in `page_extract.py` have no notion of a post box, and
    they do not need one for their own purpose: `PO Box 1234, <town> <code>`
    matches the capitalised-street tier because the box number reads as a house
    number and the town and postcode follow it exactly as they follow a street.
    It is a postal address and `has_address` is right about it. It is not a
    place, so it must not count towards "this site has separate places to
    visit" and must never be published as a `LocalBusiness`.
    """
    hint, at, text = _where_the_address_sits(page)
    if at < 0:
        return False
    return bool(_POST_BOX_RE.search(text[max(0, at - 24):at]))


def _address_belongs_to_the_site(page, brand_name=""):
    """Is the address on this page the site's own, or somebody else's?

    A volunteer non-profit was told it looks like "a business with separate
    branches" on the strength of three addresses: its own PO Box, an address
    inside a book review - which is the book's author's - and a directory of
    freelance consultants listing four different people's towns. The snippet
    would have had a charity publish other people's addresses as its own
    locations, which is the exact defect this skill's own SKILL.md says the
    address reader prevents; it prevented it for markup and nothing enforced it
    for addresses read off the page text.

    Two ways an address is the site's, and a page needs only one:

      * the page is one a site publishes about itself and its places - a
        location, contact, home or about page. A branch page that prints a bare
        address in an `<address>` element and no words at all around it is
        still the site's place, and that is the commonest branch page there is.
      * the words around it speak for the site. `speaks_about_itself` is the
        shared rule for that - first person, or the brand's own name - and it
        already refuses "The <Something> Foundation" naming somebody else.

    A page about a person, a book or a supplier satisfies neither, which is
    what separates the three false branches above from a real one.
    """
    if page.get("page_type") in _PAGES_ABOUT_OUR_OWN_PLACES:
        return True
    hint, at, text = _where_the_address_sits(page)
    if at < 0:
        # Unreadable rather than disowned. The address was recorded, the page
        # around it cannot be quoted, and refusing on that would drop real
        # branches for a reason that is ours and not the site's.
        return True
    window = text[max(0, at - _ATTRIBUTION_CHARS_BEFORE):
                  at + len(hint) + _ATTRIBUTION_CHARS_AFTER]
    return speaks_about_itself(window, brand_name)


def _places_this_site_can_be_visited_at(snapshot):
    """Pages with an address of their own that is the site's own place.

    `pages_with_their_own_address` answers "does this page print an address no
    other page prints", which is the right question for finding a chain whose
    branch pages carry no markup at all. It cannot answer whose the address is
    or whether it is a place, and both were being assumed.
    """
    brand_name = (snapshot.get("brand") or {}).get("name") or ""
    return [page for page in pages_with_their_own_address(snapshot)
            if not _address_is_a_post_box(page)
            and _address_belongs_to_the_site(page, brand_name)]


def _check_per_location_markup(result, snapshot, pages, platform=None):
    """A page describing one branch should say so in markup.

    The single highest-value structured-data fix a multi-location business can
    make, and there was no check for it. A pizza chain with sixty branches got
    a report that never used the word `LocalBusiness`: its branch pages each
    print a street and a postcode in plain text, declare no place markup at
    all, and so were invisible to every signal that would have identified the
    site as a chain. The markup that would have found the chain was the markup
    the chain was missing.

    `pages_with_their_own_address` reads the addresses off the pages instead,
    which is evidence the site cannot fail to provide. What it cannot say is
    whose address it is or whether it is a place at all, and
    `_places_this_site_can_be_visited_at` asks both before anything here counts
    a page as a branch.
    """
    result.check("per-location-markup")
    printed = pages_with_their_own_address(snapshot)
    branch_pages = _places_this_site_can_be_visited_at(snapshot)
    set_aside = len(printed) - len(branch_pages)
    if len(branch_pages) < BRANCH_PAGE_MINIMUM:
        result.skip("per-location-markup",
                    "{} page(s) carry an address of their own that is a place of this "
                    "site's, too few to treat this as a business with separate places to "
                    "visit{}".format(
                        len(branch_pages),
                        # Named, because a reader counting addresses on their
                        # own site will get a different number and is entitled
                        # to know which ones were not counted and why.
                        "; {} further page(s) print an address that is a post-office box "
                        "or belongs to somebody the page is about".format(set_aside)
                        if set_aside else ""))
        return

    unmarked = []
    for page in branch_pages:
        # Either notation. A restaurant group whose branch pages carry
        # `itemtype="https://schema.org/Restaurant"` has said where the place
        # is; reading JSON-LD alone reported every one of them as silent.
        declared = jsonld_type_names(page.get("jsonld_types")) | _inline_types_on(page)
        if not declared & VISITABLE_JSONLD_TYPES:
            unmarked.append(page)
    # `jsonld_type_names(page["jsonld_types"])` lists the types of the blocks
    # that parsed, so a branch page whose LocalBusiness block dies on a syntax
    # error reads here as a branch page that declares nothing. The finding this
    # guards is a high-severity one - "5 pages describe a place to visit
    # without saying so in markup" - and its fix adds a second block beside the
    # broken one. Same gate as the Product, Article and FAQ checks; this one
    # was missing it.
    unmarked, unreadable = _set_aside_unparsed_markup(unmarked, VISITABLE_JSONLD_TYPES)
    if unreadable:
        result.signal("branch_pages_whose_markup_does_not_parse",
                      sorted("{} ({})".format(p["url"], why) for p, why in unreadable)[:10])
    if not unmarked and unreadable:
        result.skip("per-location-markup",
                    _markup_declared_but_unreadable(unreadable, "LocalBusiness or Place",
                                                    VISITABLE_JSONLD_TYPES,
                                                    "page carrying its own address",
                                                    "pages carrying their own address"))
        return
    if not unmarked:
        result.skip("per-location-markup",
                    "all {} page(s) carrying their own address declare a visitable "
                    "schema.org type".format(len(branch_pages)))
        return

    example = unmarked[0]
    facts = example.get("contact_facts") or {}
    # The finding stays whatever the site is: a page printing a street address
    # and declaring no place markup is a page a machine cannot answer "where"
    # from, and that is true of a campus, a depot, a field office and a branch
    # alike. What is not true of all of them is the shape of the fix. "One
    # LocalBusiness block per branch, each with its own telephone and
    # openingHoursSpecification" describes a chain of shops; a university
    # department and a research group have places without trading hours, and
    # advice to publish hours they do not keep is advice they cannot follow.
    #
    # So the observation is ungated and only the wording moves. `might_be`,
    # again, so an unplaceable site keeps today's words.
    trades_from_its_places = site_kind(snapshot).might_be(LOCAL_BUSINESS, PUBLIC_BODY)
    if trades_from_its_places:
        summary = ("Add one LocalBusiness block per branch page, each with that branch's own "
                   "address, phone and opening hours.")
        steps = [
            "On each branch page, add a LocalBusiness (or the closest subtype: Store, "
            "Restaurant, Dentist) JSON-LD block naming that branch.",
            "Give each one its own `address`, `telephone` and `openingHoursSpecification`. "
            "Do not reuse the head-office values - the point is that they differ.",
            "Set `parentOrganization` on each branch to the Organization block in your "
            "site-wide template, so a machine can see they are one business.",
            "Keep the site-wide Organization block as it is. It describes the company; these "
            "describe the places.",
            where_the_template_is(platform),
            who_edits_the_template(platform),
        ]
        place_type = "LocalBusiness"
    else:
        summary = ("Add one Place block per page that describes a place, each with that "
                   "place's own address.")
        steps = [
            "On each page that describes a place, add a Place JSON-LD block naming it.",
            "Give each one its own `address`. Add `telephone` and "
            "`openingHoursSpecification` only where that place really has its own line "
            "and its own hours - invented hours are worse than none.",
            "Set `containedInPlace` or `parentOrganization` on each one, pointing at the "
            "Organization block in your site-wide template, so a machine can see they "
            "belong to one body.",
            "Keep the site-wide Organization block as it is. It describes the body; these "
            "describe the places.",
            where_the_template_is(platform),
            who_edits_the_template(platform),
        ]
        place_type = "Place"
    # The observation is the same sentence for every site: these pages print
    # their own address and declare no place markup. Only the clause reading
    # what that pattern means about the site moves with the wording.
    looks_like = ("which is what a business with separate branches looks like"
                  if trades_from_its_places else
                  "which is what a body with separate places looks like")
    rationale = ('"Near me" and "which branch" questions are answered from per-place '
                 "markup. A chain with one company-level block is one result; a chain with "
                 "a block per branch is a result in every town it operates in."
                 if trades_from_its_places else
                 '"Where is it" and "which one is nearest" questions are answered from '
                 "per-place markup. One body-level block is one result; a block per place "
                 "is a result in every town it has one in.")
    # The name of the thing the branch belongs to, and the word for a place of
    # it. A campus is not a branch and a field office does not trade.
    place_word = "branch" if trades_from_its_places else "place"
    snippet_payload = {
        "@context": "https://schema.org",
        "@type": place_type,
        "name": "{} - <{} name>".format(
            (snapshot.get("brand") or {}).get("name") or "<brand>", place_word),
        "url": example["url"],
        "address": {
            "@type": "PostalAddress",
            # Placeholders, never the hints. `street_hint` and `postcode_hint`
            # are regular expressions run over page text, and the address
            # reader two hundred lines above already refuses them with the list
            # of what they produced: a blog headline as a street, a colour-index
            # code as a postcode, the digits of a price. This snippet was still
            # pasting them in, and `pages_with_their_own_address` only returns
            # pages where both hints matched, so the fallback below could never
            # fire - the guess was published every single time.
            "streetAddress": "<this {}'s street address>".format(place_word),
            "postalCode": "<this {}'s postal code>".format(place_word),
        },
        "parentOrganization": {
            "@type": "Organization",
            "name": (snapshot.get("brand") or {}).get("name") or "<brand>",
        },
    }
    # A telephone placeholder is an instruction to publish a line per place,
    # and a place that has no line of its own cannot follow it. Where the site
    # declared one it is a real value and goes in either way; where it did not,
    # only a site whose places trade is asked to find one.
    declared_phone = _publishable_telephone(
        (facts.get("declared_phones") or [""])[0], pages)
    if declared_phone:
        snippet_payload["telephone"] = declared_phone
    elif trades_from_its_places:
        snippet_payload["telephone"] = "<this branch's phone>"
    result.add(
        id_hint="branch-pages-carry-no-location-markup",
        title="{} describe a place to visit without saying so in markup".format(
            plural(len(unmarked), "page")),
        severity="high" if len(unmarked) >= 5 else "medium", confidence="high",
        evidence="{} of {} page(s) that print a street address of this site's own "
                 "declare no LocalBusiness, Store, Restaurant or similar visitable type. "
                 "Examples: {}. Each of those addresses is different from the others, "
                 "{}.{}".format(
                     len(unmarked), len(branch_pages),
                     ", ".join(example_urls([p["url"] for p in unmarked], 3)),
                     looks_like,
                     " {} further page(s) print an address that was not counted: a "
                     "post-office box, which is not a place to visit, or an address in "
                     "text about somebody the page is about rather than about this "
                     "site.".format(set_aside) if set_aside else ""),
        # The visitable-type reading is one source and the report should say
        # so. The only place this looks for a visitable type is `jsonld_types`
        # plus the inline attributes named beside it.
        #
        # The third is the one that decides which pages are on the list at all.
        # Without it a volunteer non-profit was told it looks like a business
        # with separate branches on the strength of its own PO Box, a book
        # review quoting the author's address, and a directory of freelance
        # consultants listing four other people's towns.
        checked=("the JSON-LD types declared on each page that prints its own "
                 "street address",
                 "itemtype and typeof attributes on those pages, for Microdata or "
                 "RDFa declaring the place instead",
                 "whose address each one is: a post-office box is not a place anyone "
                 "visits, and an address is counted as this site's only on a page the "
                 "site publishes about its own places - a location, contact, home or "
                 "about page - or where the words around it are in the first person "
                 "or name the brand"),
        mechanism="C", root_cause="no-org-schema",
        summary=summary,
        how_to_fix=([_fix_the_parse_error_instead(unreadable, place_type)]
                    if unreadable else []) + steps,
        effort="medium", owner="developer",
        rationale=rationale,
        affected_pages=[p["url"] for p in unmarked],
        # Through `_snippet_block`, like every other snippet in this file, so
        # one place decides how a block is wrapped and what may be inside it.
        snippet=_snippet_block(snippet_payload),
    )


def _check_product(result, by_type, brand, platform=None):
    """Judge the nodes a page declares, wherever in the site the page sits.

    Gating the whole check on `by_type["product"]` meant a cloud host's
    homepage `SoftwareApplication` - a type that is in `PRODUCT_TYPES` - was
    never examined, and its `Offer` carrying a `priceCurrency`, no `price` and
    no `priceSpecification` went unreported. Google's Rich Results test rejects
    that Offer. The report instead said no product detail pages were found, so
    Product and Offer markup was not expected, about markup the site had
    already published.

    Two questions, two scopes, and the split is the point:

      * "this page sells something and declares no Product markup" is an
        expectation about pages the crawl calls product detail pages, and
        cannot be asked of an about page;
      * "this Offer object is incomplete" is a defect in markup the site chose
        to publish, and is read wherever the site published it.

    A node with no Offer at all stays on the first side of that line. A dish on
    a menu that is not sold separately and a Course listed without a fee are
    complete markup, not omissions.
    """
    result.check("product-markup")
    # The type the crawl assigned is a candidate, not a verdict, and this check
    # used to treat it as one. `_not_the_type_it_was_given` is the same question
    # the Article check below already asks, asked here too - which is the whole
    # of the fix: a gift category, a best-sellers list and a search-results
    # address were reported as "3 of 3 product pages have no Product markup",
    # and the remedy offered was to add a `Product` block to the template that
    # renders all three.
    #
    # Named in a signal rather than dropped quietly, the same way the Article
    # check names the indexes it does not grade.
    graded = [(p, _not_the_type_it_was_given(p)) for p in by_type.get("product", [])]
    mistyped = [(p, why) for p, why in graded if why]
    products = [p for p, why in graded if not why]
    if mistyped:
        result.signal("pages_not_graded_as_product_pages",
                      sorted("{} ({})".format(p["url"], why) for p, why in mistyped)[:10])
    declaring = sorted((p for pages in (by_type or {}).values() for p in pages
                        if _nodes_of(p, PRODUCT_TYPES)),
                       key=lambda p: p["url"])
    if not products and not declaring:
        if mistyped:
            result.skip("product-markup",
                        "no product detail page was crawled. The {} whose type or address "
                        "suggested one turned out to be a list rather than a thing - {} - and "
                        "a list carries no Product markup of its own".format(
                            plural(len(mistyped), "page"),
                            "; ".join("{}: {}".format(p["url"], why)
                                      for p, why in sorted(mistyped,
                                                           key=lambda x: x[0]["url"])[:3])))
            return
        result.skip("product-markup",
                    "no product detail pages were detected on this site, and no crawled page "
                    "declares a Product, Offer or other sellable type anywhere in its markup."
                    + unclassified_note(by_type))
        return
    if not products:
        return _check_offers_wherever_declared(result, declaring, brand, products, mistyped,
                                              platform, sells=_the_site_sells(by_type, products))

    # Either notation counts. A shop whose product pages carry
    # `itemtype="https://schema.org/Product"` has Product markup, and reading
    # JSON-LD alone reported it as having none at all.
    without = [p for p in products
               if not _nodes_of(p, PRODUCT_TYPES) and not _declares_type(p, PRODUCT_TYPES)]
    # Markup that fails to parse is markup. See `_unreadable_block_reason`.
    without, unreadable = _set_aside_unparsed_markup(without, PRODUCT_TYPES)
    if unreadable:
        result.signal("product_pages_whose_markup_does_not_parse",
                      sorted("{} ({})".format(p["url"], why) for p, why in unreadable)[:10])
    if not without and unreadable:
        result.skip("product-markup",
                    _markup_declared_but_unreadable(unreadable, "Product", PRODUCT_TYPES,
                                                    "product page", "product pages"))
        return
    if without:
        result.add(
            id_hint="no-product-schema",
            title="{} of {} product pages have no Product markup".format(
                len(without), len(products)) if len(products) > 1 else
                "The one product page crawled has no Product markup",
            # Leans the right way in the study (23% of unnamed brands versus 6%
            # of named ones) but on too few sites to justify `high`.
            severity="medium", confidence="high",
            evidence="Product pages with no Product JSON-LD: {}.".format(
                ", ".join(sample([p["url"] for p in without]))),
            # One source. `PRODUCT_TYPES` is matched against the `@type` of the
            # JSON-LD blocks on the page and nothing else, so a shop that marks
            # its prices up as inline Microdata is listed here as having no
            # Product markup at all.
            # Again the vocabulary, not the location: a bookshop marking a
            # title as `Book`, a SaaS pricing page using
            # `SoftwareApplication` and a training provider using `Course`
            # all count here, and only this sentence says so.
            checked=("the @type of every JSON-LD block on each detected product page, "
                     "matched against the {} schema.org types this audit accepts as a "
                     "sellable thing, among them Product, Book, SoftwareApplication, "
                     "Course, Event, MenuItem and Service. A type outside that list "
                     "reads here as no product markup".format(len(PRODUCT_TYPES)),
                     "itemtype and typeof attributes on the same pages, matched against "
                     "the same type list, for Microdata or RDFa saying the same thing"),
            mechanism="C", root_cause="no-product-schema",
            summary="Add Product plus Offer JSON-LD to the product page template.",
            how_to_fix=([_fix_the_parse_error_instead(unreadable, "Product")]
                        if unreadable else []) + [
                "Add the snippet below to the product template, populated from the same fields "
                "your page already renders.",
                "`offers.price` must be a plain number with no currency symbol; the symbol goes "
                "in `priceCurrency`.",
                "`availability` must be one of the schema.org values, for example "
                "https://schema.org/InStock.",
                "Because this is one template, the fix covers every product page at once.",
                # Who, in this site's own words wherever the platform detector
                # could name it. Not `where_the_template_is`: that sentence
                # names the template every page shares, and the block being
                # added here belongs to the product template alone - naming
                # the shared one would send the reader to the wrong file.
                who_edits_the_template(platform),
            ],
            effort="medium", owner="developer",
            rationale="Price and availability are exactly the facts a shopper asks "
                      "an assistant for. Stated only as styled text, they are ambiguous; stated "
                      "as Offer properties, they are unambiguous data.",
            affected_pages=[p["url"] for p in without],
            snippet=_product_snippet(without[0], (brand or {}).get("name") or ""),
        )
        return

    return _check_offers_wherever_declared(result, declaring, brand, products, mistyped,
                                              platform, sells=_the_site_sells(by_type, products))


def _check_offers_wherever_declared(result, declaring, brand, products, mistyped=(), platform=None,
                                    sells=False):
    """Grade the Offer on every sellable node the site declares, on any page.

    `products` is only used to decide whether a node with no Offer at all is a
    defect. See `_check_product` for why the two questions have different
    scopes.

    `mistyped` is the pages the crawl called product pages and that turned out
    to be lists. It exists so the reason `product-markup` declined names them:
    "no product detail pages were detected" is a different sentence from "three
    were detected and none of them was one", and a reader whose shop is full of
    products deserves the second.
    """
    # Registered here rather than inherited from `_check_product`. A finding
    # may only claim the checks its own function registered, and this one is a
    # different question with a different scope: "is this Offer complete",
    # judged on every page that declares a sellable node, against "does a
    # product page declare one at all".
    result.check("offer-completeness")
    product_urls = {p["url"] for p in products}
    incomplete = []
    # Offers that state a price of nothing, on a page that sells nothing, on a
    # site that sells. Kept apart from `incomplete` because the repair is the
    # opposite one: completing them is what makes the false claim valid.
    unpriced = []
    for page in declaring:
        # Blocks the page publishes about itself. A `Product` lifted out of a
        # review's `itemReviewed` or out of a related-items list names something
        # that lives on another page, and grading it here made this check say
        # "no offers object" about a page whose own `Product` carries one - see
        # `_mentions_another_thing`.
        nodes = [n for n in _nodes_of(page, PRODUCT_TYPES)
                 if not _mentions_another_thing(n)]
        priced = [(n, _offer_of(n)) for n in nodes]
        priced = [(n, offer) for n, offer in priced if isinstance(offer, dict)]
        if not priced:
            # Said only where the page prices nothing at all. The old loop
            # stopped at the first block with no `offers` and named the page,
            # so one unpriced block beside a complete one was enough to publish
            # a claim the run's own snapshot contradicts.
            if nodes and page["url"] in product_urls:
                incomplete.append((page, nodes[0], "no offers object"))
            continue
        for node, offer in priced:
            missing = _missing_props(offer, _offer_props(offer))
            if missing:
                # A home-decor shop's SEO plugin writes a `Service` block on its
                # about, contact and FAQ pages carrying `"offers": {"price": "0"}`
                # and nothing else. This finding called that an Offer "missing
                # priceCurrency, availability", and the snippet kept the zero -
                # so the advice was to finish a claim that the shop gives its
                # service away, on a site whose every other page prices it.
                if sells and _states_no_price(offer) \
                        and page.get("page_type") not in _PAGE_TYPES_THAT_PRICE_THINGS:
                    unpriced.append((page, node))
                else:
                    incomplete.append((page, node, "{} offer missing {}".format(
                        _prop(node, "@type") or "Product", ", ".join(missing))))
                break

    if unpriced and not incomplete:
        page, node = sorted(unpriced, key=lambda x: x[0]["url"])[0]
        kind = _prop(node, "@type") or "Product"
        result.add(
            id_hint="product-schema-missing-offer-details",
            title="Offer markup states a price of 0 on {} of a site that sells".format(
                plural(len(unpriced), "page")),
            severity="medium", confidence="high",
            evidence="; ".join(
                "{} ({} offer with a price of {} and no currency or availability)".format(
                    p["url"], _prop(n, "@type") or "Product",
                    _price_as_stated(_offer_of(n)))
                for p, n in sorted(unpriced, key=lambda x: x[0]["url"])[:5]),
            checked=("the offers object on every Product, Service or other sellable block on "
                     "any crawled page, and the type of the page it sits on",
                     "whether the site sells: a product detail page, or an add-to-cart "
                     "control on any crawled page"),
            mechanism="C", root_cause="missing-schema-props",
            summary="Give the {} its real price, or remove the offer from it.".format(kind),
            how_to_fix=[
                "Do not complete this offer. Adding a currency and an availability to it "
                "turns a price of 0 into a valid, machine-readable statement that the {} "
                "is free.".format(kind),
                "If the {} has one price, put that number in `price` with its currency in "
                "`priceCurrency`, bound to the same field the visible price uses.".format(kind),
                "If it has no single price - it is quoted, or it varies by order - remove "
                "the `offers` object and keep the rest of the block, as in the snippet "
                "below.",
                "These blocks sit on pages that sell nothing themselves ({}), which usually "
                "means a plugin writes the same block on every page; fix it in that "
                "plugin's settings rather than page by page.".format(
                    ", ".join(sorted({str(p.get("page_type") or "other")
                                      for p, _n in unpriced}))),
            ],
            effort="low", owner="developer",
            rationale="A price of 0 is not an absent price: an assistant asked what this "
                      "costs can repeat it as free, on a site whose own pages say otherwise.",
            affected_pages=[p["url"] for p, _n in unpriced],
            snippet=_block_without_its_offer(node),
        )
        return

    if incomplete:
        result.add(
            id_hint="product-schema-missing-offer-details",
            # Not "Product markup is present": the block whose Offer is
            # incomplete is often a SoftwareApplication, a Course or a
            # MenuItem, and naming it Product in the title is the same downgrade
            # the snippet used to make in code.
            title="Offer markup is present but incomplete on {}".format(
                plural(len(incomplete), "page")),
            severity="medium", confidence="high",
            evidence="; ".join(
                "{} ({})".format(p["url"], why)
                for p, _n, why in sorted(incomplete, key=lambda x: x[0]["url"])[:5]),
            # One source: the Offer inside the page's own sellable block.
            # `_offer_of` looks in two places within it - `offers` and
            # `hasVariant[].offers` - and `_missing_props` reads
            # `priceSpecification` as well, but all of them are the same JSON-LD
            # document, so this is one detector described accurately, not two.
            checked=("the offers object on every Product, SoftwareApplication, Course, Event, "
                     "MenuItem or other sellable block on any crawled page, including the "
                     "offers on any hasVariant entries and any nested priceSpecification",
                     # Said because the reader has to be able to check it. A
                     # page naming a second product it does not sell - the item
                     # a review reviews, an entry in a related-items list - is
                     # not graded on that block, and a page is called unpriced
                     # only where no block it publishes about itself carries an
                     # Offer.
                     "which block on the page each reading came from: a block naming a "
                     "thing that lives on another page, such as a review's itemReviewed "
                     "or an entry in a related-items list, is not read as this page's own "
                     "offer"),
            mechanism="C", root_cause="missing-schema-props",
            summary="Complete the Offer object with price, priceCurrency and availability.",
            how_to_fix=[
                "Add the missing Offer properties wherever the block is generated. An Offer "
                "with a currency and no `price` and no `priceSpecification` is rejected "
                "outright rather than read as a price of nothing.",
                # The step this finding was missing. A report told a shop to
                # replace a valid `priceSpecification` with a flat `price`,
                # which is a downgrade of working markup dressed as a repair.
                "Leave the properties it already states as they are. A price stated inside "
                "`priceSpecification` is valid and carries more than a flat `price` does - "
                "the unit, the validity dates, whether tax is included - so add what is "
                "missing beside it rather than flattening it.",
                "Bind them to the same variables the visible price uses so they cannot drift apart.",
                "Validate one page with the schema.org validator after the change.",
            ] + ([
                # Named here rather than folded into the list above. Completing
                # these is the one repair that makes them worse.
                "Not these: {} state a price of 0 on a page that sells nothing. Give each "
                "its real price, or remove its `offers` object - do not complete it.".format(
                    ", ".join(sorted(p["url"] for p, _n in unpriced)[:3]))
            ] if unpriced else []),
            effort="low", owner="developer",
            rationale="A Product without a priced Offer answers 'what is this' but "
                      "not 'what does it cost', which is the question being asked.",
            affected_pages=[p["url"] for p, _n, _w in incomplete],
            snippet=_product_snippet(incomplete[0][0], (brand or {}).get("name") or "",
                                     node=incomplete[0][1]),
        )
        return
    # Both names are answered, because both were registered. `product-markup`
    # asks whether a product page declares a block; `offer-completeness` asks
    # whether the blocks that exist are usable, on any page. A check registered
    # and left unanswered is reported as having run clean, which is the one
    # thing the appendix must never say falsely.
    if products:
        result.skip("product-markup",
                    "all {} detected product page(s) carry Product markup".format(len(products)))
    elif mistyped:
        result.skip("product-markup",
                    "no product detail page was crawled. The {} whose type or address suggested "
                    "one turned out to be a list rather than a thing - {} - and a list carries "
                    "no Product markup of its own".format(
                        plural(len(mistyped), "page"),
                        "; ".join("{}: {}".format(p["url"], why)
                                  for p, why in sorted(mistyped, key=lambda x: x[0]["url"])[:3])))
    else:
        result.skip("product-markup",
                    "no product detail pages were detected on this site")
    if declaring:
        result.skip("offer-completeness",
                    "every sellable block on the {} that declare one carries a complete "
                    "Offer - a wider set than the product pages above, because a category, "
                    "home or event page can declare a sellable type too".format(
                        plural(len(declaring), "crawled page")))
    else:
        result.skip("offer-completeness",
                    "no crawled page declares a Product, SoftwareApplication, Course, Event "
                    "or other sellable type, so there is no Offer to grade")


# Page types on which an offer is the page's own business. A free tier on a
# pricing page and a free sample on its own product page are real prices of
# nothing; an offer of 0 on an about page is a plugin's default.
_PAGE_TYPES_THAT_PRICE_THINGS = {"product", "pricing", "listing", "category"}


def _the_site_sells(by_type, products):
    """Does this site sell? A product detail page, or an add-to-cart control.

    Not "does any page print a price": a software site with a free plan and a
    paid one prints prices, and its free plan's `price: 0` is true.
    """
    if products:
        return True
    return any(((p.get("add_to_cart_controls") or {}).get("count") or 0) > 0
               for pages in (by_type or {}).values() for p in pages
               if isinstance(p.get("add_to_cart_controls"), dict))


def _states_no_price(offer):
    """Does this Offer declare a price, and is that price 0 or empty?

    An Offer with no `price` key at all is the ordinary incomplete case and
    is not this. A price inside `priceSpecification` counts as a price.
    """
    if not isinstance(offer, dict):
        return False
    value = _prop_anywhere(offer, "price")
    if value == "":
        return "price" in offer
    try:
        return float(str(value).replace(",", "").strip()) == 0
    except ValueError:
        return False


def _price_as_stated(offer):
    """The price an Offer declares, quoted as written."""
    value = _prop_anywhere(offer or {}, "price") if isinstance(offer, dict) else ""
    return '"{}"'.format(value) if value != "" else "nothing"


def _block_without_its_offer(node):
    """The site's own block, exactly as published, with `offers` taken out."""
    payload = {"@context": "https://schema.org"}
    for key, value in (node or {}).items():
        if str(key).startswith("_") or key in ("@context", "offers"):
            continue
        payload[key] = _declared(node, key)
    return _snippet_block(payload)


def _the_products_own_page(page, node=None):
    """Is this page the one product's own page, so its values speak for it?"""
    if isinstance(node, dict) and node:
        return _is_the_page_subject(node, page)
    return page.get("page_type") == "product" and not is_listing_page(page)


def _the_pages_product_name(page):
    """The page's heading or title, where it names this page's product, or ''.

    A clothing shop's product block went out named "Online Shopping for Men &
    Women Clothing, Accessories at <Shop>" - the title the site prints on every
    page - for a page whose address names a pair of sneakers. A heading that
    shares no word with the product's own address slug is the site's, not the
    product's.
    """
    slug = [w for w in re.split(r"[^a-z0-9]+", urlparse(str(page.get("url") or ""))
                                .path.rsplit("/", 1)[-1].lower()) if len(w) >= 3]
    for label in list((page.get("headings") or {}).get("h1") or []) + [page.get("title") or ""]:
        label = " ".join(str(label or "").split())
        if not label:
            continue
        words = set(re.split(r"[^a-z0-9]+", label.lower()))
        if not slug or len([w for w in slug if w in words]) >= min(2, len(slug)):
            return label
    return ""


def _product_snippet(page, brand_name="", node=None):
    """One product's markup, filled in from that product's own page.

    `image` and `sku` used to be `<absolute image URL>` and `<your SKU>`. On a
    site with no images at all the first is unsatisfiable, and a two-person
    pottery may have no SKUs - so the snippet demanded two values that could
    not be supplied, while our own checks never looked at either. A key we
    cannot fill and do not grade does not belong in paste-ready code.

    `node` is the block the finding is about, when there is one. Its `@type`,
    its `name` and the currency its Offer already declares are reused, because
    the alternative is handing a cloud host a `Product` block to replace the
    `SoftwareApplication` it correctly publishes. The finding is that one
    property is missing, and the repair for that is not a change of type.
    """
    # Neither of these is defaulted any more. `availability` was hardcoded to
    # InStock, which is a claim about the warehouse that no part of this audit
    # can see, and `currency` defaulted to USD, so a page with no price at all
    # emitted `"priceCurrency": "USD"` beside an admitted `<price>` placeholder
    # - an invented fact sitting next to an honest blank, in the same object.
    # A `$` figure on a .ca or .au site was called USD for the same reason.
    #
    # The page's own values - its first price, its meta description, its
    # og:image, its heading - speak for the product only on that product's own
    # page. A rug's block was filled from the accessories listing it appeared
    # on: that page's "Shop our modern home decor ..." as the rug's description
    # and the listing's thumbnail as its image. Where the page is not the
    # product's own, the block takes what the product's markup declares and
    # leaves out what it does not.
    own_page = _the_products_own_page(page, node)
    prices = find_prices(page.get("body_text") or "") if own_page else []
    number = ""
    currency = ""
    if prices:
        match = PRICE_NUMBER_RE.search(prices[0])
        if match:
            number = match.group(0).replace(",", "")
        for symbol, code in (("£", "GBP"), ("€", "EUR"), ("₹", "INR"),
                             ("¥", "JPY"), ("₩", "KRW"), ("R$", "BRL"),
                             ("CHF", "CHF"), ("kr", "SEK")):
            if symbol in prices[0]:
                currency = code
                break
        else:
            # `$` is ambiguous - USD, CAD, AUD, NZD, SGD, HKD and more write
            # it - so it is left for the owner rather than guessed at.
            iso = re.search(r"\b([A-Z]{3})\b", prices[0])
            currency = iso.group(1) if iso else ""
    declared_offer = _offer_of(node) if isinstance(node, dict) else None
    if isinstance(declared_offer, dict):
        # The site's own values win wherever it published one. Every one of
        # these was observed in the page's markup; none is guessed.
        currency = str(_prop_anywhere(declared_offer, "priceCurrency") or "") or currency
        number = str(_prop_anywhere(declared_offer, "price") or "") or number
    # A price the site states inside `priceSpecification` is carried back out
    # in `priceSpecification`, not flattened into `price`.
    #
    # `_prop_anywhere` reads that nested form, so a site publishing it already
    # satisfies this check - and the snippet then handed the owner a block that
    # deleted the nested object and replaced it with a flat `price` beside a
    # `<ISO 4217 code, e.g. USD>` placeholder. That is
    # prescribing a fix the site already has, and losing the unit, the validity
    # window and the tax flag that only `UnitPriceSpecification` can state. A
    # snippet may add what is missing; it may not take away what is there.
    spec = (_declared(declared_offer, "priceSpecification")
            if isinstance(declared_offer, dict) else "")
    spec_states = {key for key in ("price", "priceCurrency")
                   if spec and isinstance(declared_offer, dict)
                   and _prop(declared_offer, key) == ""
                   and _prop_anywhere(declared_offer, key) != ""}
    offers = {
        "@type": (_prop(declared_offer, "@type")
                  if isinstance(declared_offer, dict) else "") or "Offer",
        "url": (str(_prop(declared_offer, "url") or "")
                if isinstance(declared_offer, dict) else "") or page["url"],
    }
    if spec_states:
        offers["priceSpecification"] = spec
    if "price" not in spec_states:
        offers["price"] = number or "<price as a plain number>"
    if "priceCurrency" not in spec_states:
        offers["priceCurrency"] = currency or "<ISO 4217 code, e.g. USD>"
    offers["availability"] = ((str(_prop(declared_offer, "availability") or "")
                               if isinstance(declared_offer, dict) else "")
                              or "<https://schema.org/InStock or .../OutOfStock>")
    payload = {
        "@context": "https://schema.org",
        "@type": (_prop(node, "@type") if isinstance(node, dict) else "") or "Product",
        "name": (str(_prop(node, "name") or "") if isinstance(node, dict) else "")
                or _the_pages_product_name(page) or "<product name>",
        "offers": offers,
    }
    # Optional keys only when the site actually supplies them. A key we cannot
    # fill and do not grade does not belong in code labelled paste-ready.
    declared_description = str(_prop(node, "description") or "") if isinstance(node, dict) else ""
    description = _paste_ready_description(
        declared_description or (page.get("meta_description") or "" if own_page else ""),
        _link_labels([page]), brand_name)
    if description:
        payload["description"] = description
    declared_image = _image_url(_declared(node, "image")) if isinstance(node, dict) else ""
    image = declared_image or ((page.get("og") or {}).get("og:image") if own_page else "")
    # A logo is the site's mark, not a picture of the product. A clothing
    # shop's product block went out with the site's sticky-header logo as its
    # `image`, because that is what its og:image held - a file called
    # `newlogosticky.png`, which the whole-word test `_site_logo` uses lets
    # through. For a product any "logo" in the file name settles it: a
    # product photograph is never named that.
    file_name = urlparse(str(image or "")).path.rsplit("/", 1)[-1]
    if image and not re.search(r"logo", file_name, re.I):
        payload["image"] = image
    # `brand` only where schema.org allows it. It is a property of Product and
    # its subtypes, of Service and of Organization, and not of the
    # CreativeWork branch - so putting it on the SoftwareApplication a cloud
    # host declares, or on a Book or a Course, adds a property the type does
    # not have to a block the reader is told to paste.
    if brand_name and jsonld_type_names(payload["@type"]) & _BRANDED_TYPES:
        payload["brand"] = {"@type": "Brand", "name": brand_name}

    return _snippet_block(payload)


# A path whose segments after the year are the date it archives, and nothing
# else. A permalink ends in a slug - `/blog/2025/hiring-a-second-engineer` -
# and an archive index ends in the month or the day: `/blog/2025/1/`,
# `/news/2024/06/12/`.
#
# The page-type detector reads a year followed by any further segment as a
# permalink, so every monthly archive on a blog arrives here typed `article`.
# Five of them on one site were reported as posts carrying no Article markup,
# and the paste-ready snippet gave an archive listing a single post a
# `headline` of "JANUARY 2025 News Archive", a `datePublished`, a `dateModified`
# and a `Person` author - which tells a machine that a page of links to dated
# pieces is itself one dated authored piece. The same report's appendix already
# excluded those five from the date check, so the audit knew what they were and
# this check did not ask.
#
# Two digits at most, and inside the calendar's own ranges: a post published at
# `/blog/2025/4471` is an article with a numeric slug, not the 4,471st month of
# 2025.
_DATED_ARCHIVE_PATH_RE = re.compile(
    r"/(?:19|20)\d{2}"
    r"(?:/(?:0?[1-9]|1[0-2]))?"
    r"(?:/(?:0?[1-9]|[12]\d|3[01]))?"
    r"/?$")


# A query string asking for one page of a longer list. Anchored on the
# parameter name so `?page=3` is caught and `?pagename=about`, which is how one
# CMS addresses a single page, is not.
_PAGINATION_QUERY_RE = re.compile(
    r"(?:^|&)(?:page|paged|pg|start|offset|from)=\d+(?:&|$)", re.I)


def _is_only_a_list_of_other_pages(page):
    """True for a page carrying no words of its own, only links to pages.

    `is_listing_page` asks whether most of a page's subheadings are also link
    labels on it, and wants three of them before it will answer - below that, a
    page linking to a few of its own sections would qualify as an index. A
    monthly archive listing one post has one subheading, so it falls through
    that branch and is graded as the post it links to.

    The count is what the threshold is protecting against, and it stops
    mattering once the page has nothing else on it: every heading is the title
    of a page somewhere else, and no paragraph on it is the page speaking for
    itself. That is an index whether it lists one item or forty.
    """
    headings = page.get("headings") or {}
    titles = [listing_key(h) for h in (headings.get("h2") or []) + (headings.get("h3") or [])
              if (h or "").strip()]
    if not titles:
        return False
    labels = {listing_key(link.get("text"))
              for link in ((page.get("links") or {}).get("internal") or [])}
    labels.discard("")
    if any(title not in labels for title in titles):
        return False
    # A paragraph that is not itself a link label is the page writing prose,
    # and a page that writes prose is not only a list of other pages.
    return not [p for p in (page.get("paragraphs") or []) if listing_key(p) not in labels]


# How many addresses in one family a page has to link to before it is the shelf
# they sit on rather than one of them.
#
# Eight, and the number matters less than the family test beside it. Every
# reading this replaces went through headings: `is_listing_page` compares a
# page's subheadings against its own link labels, and `_is_only_a_list_of_other_pages`
# needs subheadings too. A grid built out of tiles has no h2 or h3 at all - the
# item's name is a link inside a div - so both come back False and the page is
# graded as the thing it lists.
#
# Two sites, one shape. A candle shop's `/shop-all` carries 36 links to
# `/product-page/<item>`, no JSON-LD and no subheadings; it was reported as a
# product page with no Product markup. A town's news index carries a run of
# links to dated notices under its own path; it was reported as an article page
# with no Article markup, and the paste-ready snippet declared the index's own
# title, "list of new information", as the `headline` of a dated authored piece.
#
# Eight is above what a related-items strip carries - four to eight is the
# usual row on both hosted store platforms - and the family test below is what
# actually separates the two cases, because a related-items strip on a product
# page links to that page's own siblings and is excluded whatever its size.
_SHELF_FAMILY_MINIMUM = 8


def _the_shelf_this_page_links_to(page):
    """(parent path, how many) for the family of pages this page is a shelf of.

    A family is a set of addresses sharing a parent path - `/product-page/a`,
    `/product-page/b` - and the page is a shelf of that family only when it is
    not a member of it. That exclusion is the whole test: a product page's
    "you may also like" strip links to its own siblings, so it shares their
    parent and is never read as their index, however many of them there are.
    A shelf sits somewhere else - one level up, or at an address of its own -
    and links down into the family.

    Navigation and footer links are dropped first. They are the same on every
    page by construction, so counting them would make every page on a site
    whose header lists twelve categories a shelf of categories.
    """
    links = page.get("links") or {}
    own = urlparse(str(page.get("url") or "")).path.rstrip("/") or "/"
    own_parent = own.rsplit("/", 1)[0] or "/"
    chrome = {str(entry.get("url") or "")
              for bucket in ("nav", "footer")
              for entry in (links.get(bucket) or []) if isinstance(entry, dict)}
    families = defaultdict(set)
    for entry in (links.get("internal") or []):
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "")
        if not url or url in chrome:
            continue
        path = urlparse(url).path.rstrip("/")
        if not path or path == own:
            continue
        parent = path.rsplit("/", 1)[0] or "/"
        if parent == own_parent:
            continue
        families[parent].add(path)
    if not families:
        return "", 0
    parent = max(sorted(families), key=lambda key: len(families[key]))
    return parent, len(families[parent])


def _the_site_itself_says_this_is_a_list(page):
    """Has the site *declared* this address a list, rather than us inferring it?

    Two kinds of statement, and only these two:

      its address     `/collections/<handle>`, `/blog`, `/tag/<x>`, `/2024/08`,
                      a search-results URL - the site's own scheme for naming a
                      grouping;
      its own markup   a `CollectionPage`, `ItemList`, `SearchResultsPage` or
                      `Blog` block on the page.

    Everything else in `_why_this_page_is_a_list` is an inference this audit
    makes *about* the page - from the crawl's page-type guess, from how many of
    its subheadings are also link labels, from how many outbound links share a
    parent path. Those are the readings the "this page sells one thing" guard
    below is allowed to overrule. A declaration is not, which is the direction
    an earlier fix settled deliberately: a `/collections/handbags` address is
    a grouping whatever blocks the theme prints on it, and a page whose own
    markup says `CollectionPage` has answered the question itself.

    The address halves of `is_listing_page` are re-asked here rather than read
    off its one boolean, because that function answers six questions at once
    and this file needs to know which of the six said yes.
    """
    url = page.get("url") or ""
    if is_faceted_listing(url) or is_search_result_page(url):
        return True
    path = urlparse(url).path or ""
    if _DATED_ARCHIVE_PATH_RE.search(path) or _LISTING_PATH_RE.search(path):
        return True
    parts = [p for p in path.split("/") if p]
    if parts and len(parts) <= 2:
        last = parts[-1].lower()
        if last in LISTING_SEGMENTS or _YEAR_SEGMENT_RE.match(last):
            return True
    declared = _types_on(page) | _inline_types_on(page)
    return bool(declared & (_LISTING_JSONLD_TYPES | LISTING_TYPES))


def _sells_exactly_one_thing_and_says_so(page):
    """The guard, in the one form every branch below is asked to respect.

    `_declares_one_thing_for_sale` on its own is not enough to overrule a
    listing reading: a shop's `/collections/featured` can carry one `Product`
    block for the item the theme puts at the top of the grid. Pairing it with
    `_the_site_itself_says_this_is_a_list` is what keeps the override to the
    case it was written for - a page the site has said nothing about, that
    prices exactly one item.
    """
    return (_declares_one_thing_for_sale(page)
            and not _the_site_itself_says_this_is_a_list(page))


def _why_this_page_is_a_list(page):
    """Why this page is a list of things rather than one of them, or "".

    The shared `is_listing_page` and `is_faceted_listing`, plus the two shapes
    neither can reach from a snapshot: a dated archive path, and a page whose
    whole content is a list. Kept as one function so no branch of this file can
    call a page an index while another calls it an article, which is how a set
    of monthly archives came to be excluded from one check in a report and
    named by another.

    The reason is returned rather than a bare `True` because the checks that
    ask have to print it: a page silently dropped from a count is a count
    nobody can reconcile, and the reader whose page was dropped is entitled to
    know which test dropped it.
    """
    url = page.get("url") or ""
    # Asked once, respected by every branch below. A satchel's
    # detail page - `Product`, one `Offer`, 5300 INR, InStock - published under
    # "lists other pages rather than describing one" while the same report's
    # citation table quoted the same address as a product; the guard that fixed
    # it was wired into the link-counting branch alone, so the five readings
    # above it could raise the identical contradiction and an audit of this file
    # found that they still could. It is one question, so it is asked once.
    #
    # Which branches it can actually reach is decided by
    # `_the_site_itself_says_this_is_a_list`, not by which `if` it sits on:
    # every address reading and every declared-type reading makes `sells_one`
    # False, so the two address branches here are unguarded in the source and
    # immune in effect. What is left to overrule is exactly the three inferences
    # - the crawl's page-type guess, the subheading-versus-link-label share, and
    # the outbound-link family count.
    sells_one = _sells_exactly_one_thing_and_says_so(page)
    # The site's own address scheme saying "everything filed under this
    # heading". `is_listing_page` does not read it, so a shop's
    # `/collections/<handle>` reached the product check as a page selling one
    # thing whenever its grid printed a price - which every grid does.
    if is_faceted_listing(url):
        return ("its address names a grouping rather than a thing, so the page is "
                "everything filed under that heading")
    # Guarded because three of this function's six readings are in here and
    # three of those three are inferences. `page_type == "category"` is the
    # crawl's guess, and it has now been seen inverted on
    # client-rendered storefronts - 0 of 4 `/product/` URLs typed `product` on
    # one of them - so a priced detail page arrives here already mislabelled.
    # The subheading share is the other: a detail page whose h2s are
    # "Description", "Reviews", "Delivery" and which links to each of them by
    # the same words scores three of three.
    if is_listing_page(page) and not sells_one:
        return ("its subheadings are also the labels of links leaving it, or its "
                "address or its own markup says it is a list of other pages")
    if _DATED_ARCHIVE_PATH_RE.search(urlparse(url).path or ""):
        return "its address ends at the date it archives rather than at a slug"
    # Page two of something is page two of a list. `_FACETED_LISTING_RE` knows
    # the `/page/2` path form and nothing knew the query form, so a shop's
    # `?page=3` index of posts reached the FAQ check as a page of questions and
    # answers - its "questions" were post titles and its "answers" were the
    # truncated excerpts under them, and it was handed a paste-ready FAQPage
    # block for both.
    #
    # Guarded, unlike the two path readings, and the difference is what the
    # parameter says. `/collections/all` names a grouping; `?page=2` names an
    # offset into some list on the page without saying the page is that list,
    # and a shop that paginates a product's reviews serves the priced item
    # again at `/products/<slug>?page=2`. Where the path *does* name a
    # grouping, `is_faceted_listing` has already answered above.
    if _PAGINATION_QUERY_RE.search(urlparse(url).query or "") and not sells_one:
        return "its address asks for a numbered page of a list rather than for a thing"
    # Guarded: "no words of its own" is a reading of the page's prose, and a
    # detail page whose whole description is rendered by script - the shape
    # `render-readability-audit` exists for - has no paragraphs to read while
    # its `Product` block states the price outright.
    if _is_only_a_list_of_other_pages(page) and not sells_one:
        return "it carries no words of its own, only the titles of other pages"
    # Last, because it is the only test here that does not need a heading. See
    # `_the_shelf_this_page_links_to`: every reading above this line goes
    # through subheadings, and a grid of tiles has none.
    #
    # And it is the only test here that reads nothing but outbound links, which
    # is what made it wrong on a page that says what it is. A satchel's detail
    # page declared `Product` with an `Offer` - 5300 INR, InStock - and linked
    # to nine category addresses under `/collections`, so this branch called it
    # the shelf those nine sit on. The report then listed it as "lists other
    # pages rather than describing one" while its own "What an assistant would
    # quote" table quoted the same address as a product: "Belle is a mid-sized
    # satchel to haul around your stuff easily." Two parts of one document
    # disagreeing about one page. The same shape appeared on two more product
    # pages of a second shop.
    #
    # A page's own markup outranks a count of its links. The guard requires the
    # page to declare exactly one thing for sale, so a grid whose template emits
    # a `Product` per tile is still a shelf - that direction is the earlier
    # defect this must not reopen - and it stands down wherever the site has
    # named the address a grouping or declared a listing type on it.
    shelf, count = _the_shelf_this_page_links_to(page)
    if count >= _SHELF_FAMILY_MINIMUM and not sells_one:
        return ("it links to {} different pages under {}, which is the shelf those "
                "pages sit on rather than one of them".format(count, shelf))
    return ""


# The types that say "this page is a list of things", either of which answers
# the question a shelf leaves open.
LISTING_TYPES = {"itemlist", "collectionpage", "offercatalog"}


def _check_listing_markup(result, by_type, platform=None):
    """The ask a shelf actually has, which no finding in this file used to make.

    A candle shop's report named four addresses under the heading "Product
    pages with no Product JSON-LD". One of them is titled "Shop All", carries
    36 links to product detail pages and publishes no JSON-LD at all. It is a
    category listing, `Product` markup on it would be wrong - it would declare
    the shelf to be one item with one price - and the right ask, `ItemList` or
    `CollectionPage`, appeared nowhere in the report.

    `_why_this_page_is_a_list` now keeps those pages out of the Product and
    Article checks, which removes the false demand. This is the other half:
    having decided the page is a shelf, say what a shelf should declare.

    Registered as its own check and called from `run`, never from inside
    `_check_product`. `SkillResult.add` claims every check its caller's stack
    registered, so raising this finding under the product check's roof would
    have marked `product-markup` as fired on a site whose product pages are
    fine.

    `root_cause` is `no-product-schema` because the shared vocabulary has no
    entry for a catalogue listing, and inventing one here is not this file's
    change to make - `ROOT_CAUSES` lives in the shared library. What the two
    have in common is real: a shop whose catalogue is not machine-readable.
    """
    result.check("listing-markup")
    # Only the pages the crawl called product pages and that turned out to be
    # shelves. That is exactly the population the Product check just turned
    # away, and it is the whole of what this finding is for: the report that
    # produced it listed four such addresses under "Product pages with no
    # Product JSON-LD".
    #
    # A page the crawl typed `category` is deliberately not here. It was never
    # asked for Product markup, so it is not a wrong demand being corrected -
    # and a blog index is a `category` page, which would turn "add ItemList" into
    # advice for every index on every site with a blog. That is a bigger claim
    # than the evidence supports and it is not the defect being fixed.
    shelves = [(page, why) for page in by_type.get("product", [])
               for why in [_why_this_page_is_a_list(page)] if why]
    shelves.sort(key=lambda pair: pair[0]["url"])
    if not shelves:
        result.skip("listing-markup",
                    "no crawled page that the crawl typed as a product page turned out to "
                    "be a list of other pages, so there is no shelf here that was asked for "
                    "the wrong markup")
        return
    without = [(p, why) for p, why in shelves
               if not (_types_on(p) & LISTING_TYPES)
               and not (_inline_types_on(p) & LISTING_TYPES)]
    # `_types_on` reads `jsonld_types`, which lists the blocks that parsed, so
    # a shelf whose ItemList block has a syntax error in it arrives here
    # looking exactly like a shelf that never declared one. Same gate as the
    # Product, Article and FAQ checks; this one was missing it.
    kept, unreadable = _set_aside_unparsed_markup([p for p, _ in without], LISTING_TYPES)
    if unreadable:
        result.signal("shelf_pages_whose_markup_does_not_parse",
                      sorted("{} ({})".format(p["url"], why) for p, why in unreadable)[:10])
        keep = {p["url"] for p in kept}
        without = [(p, why) for p, why in without if p["url"] in keep]
    if not without and unreadable:
        result.skip("listing-markup",
                    _markup_declared_but_unreadable(unreadable, "ItemList or CollectionPage",
                                                    LISTING_TYPES,
                                                    "shelf page", "shelf pages"))
        return
    if not without:
        result.skip("listing-markup",
                    "all {} that list other pages declare ItemList, CollectionPage or "
                    "OfferCatalog".format(plural(len(shelves), "crawled shelf page",
                                                 "crawled shelf pages")))
        return
    example, why = without[0]
    result.add(
        id_hint="listing-pages-have-no-itemlist-markup",
        title="{} that list other pages declare no ItemList or CollectionPage".format(
            plural(len(without), "page", "pages")),
        # Lower than the missing-Product finding on a detail page. A shelf with
        # no ItemList is a page a machine has to read as prose; a detail page
        # with no Product markup is the fact itself unstated.
        severity="low", confidence="high",
        evidence="{} lists other pages rather than describing one - {} - and declares "
                 "neither ItemList nor CollectionPage. The full list: {}.".format(
                     example["url"], why,
                     ", ".join(example_urls([p["url"] for p, _ in without]))),
        checked=("the @type of every JSON-LD block on each page the crawl typed as a "
                 "product page and that turned out to list other pages, matched "
                 "against ItemList, CollectionPage and OfferCatalog",
                 "itemtype and typeof attributes on the same pages, for Microdata or "
                 "RDFa declaring one of those types instead",
                 "the links on each page, to establish that it is a shelf: a page is "
                 "counted here only where it links to a family of pages it is not "
                 "itself a member of, or where one of the address and heading tests "
                 "already called it a list"),
        mechanism="C", root_cause="no-product-schema",
        summary="Declare these listing pages as ItemList or CollectionPage naming the "
                "pages they link to.",
        how_to_fix=([_fix_the_parse_error_instead(unreadable, "ItemList or CollectionPage")]
                    if unreadable else []) + [
            "Add an `ItemList` block to the listing template whose `itemListElement` "
            "entries are `ListItem` objects, each with a `position` and a `url` pointing "
            "at the item's own page.",
            # Said in the finding rather than left implicit, because the report
            # this replaces asked for the opposite.
            "Do not put `Product` markup on these pages. A listing is not one thing for "
            "sale, and a `Product` block on it declares the whole shelf to be a single "
            "item - the price, the availability and the name would all describe nothing "
            "that exists.",
            "Leave the `Product` markup on the item pages themselves, where the price and "
            "availability belong. `ItemList` points at them; it does not repeat them.",
            "Where the page is the landing page for a category rather than a bare grid - "
            "it has a heading and a paragraph of its own - `CollectionPage` with a "
            "`mainEntity` of the `ItemList` says both things at once.",
            who_edits_the_template(platform),
        ],
        effort="low", owner="developer",
        rationale="A shelf with no markup is a page a machine has to read as prose to "
                  "discover it is a list, and the item pages it points at are then found "
                  "only by following links. An ItemList states the collection and its "
                  "members as data, which is what a consumer answering \"what do they "
                  "sell\" is looking for.",
        affected_pages=[p["url"] for p, _ in without],
        snippet=_snippet_block({
            "@context": "https://schema.org",
            "@type": "ItemList",
            "url": example["url"],
            "name": (example.get("title") or "").strip() or "<the name of this listing>",
            "itemListElement": [
                {"@type": "ListItem", "position": index + 1, "url": url}
                for index, url in enumerate(_shelf_members(example)[:3])
            ] or [{"@type": "ListItem", "position": 1,
                   "url": "<the address of the first item on this page>"}],
        }),
    )


def _shelf_members(page):
    """The addresses of the pages this shelf lists, for the snippet.

    Read back through the same family test the classification used, so the
    snippet cannot list pages the finding did not count as members.
    """
    parent, _count = _the_shelf_this_page_links_to(page)
    if not parent:
        return []
    links = page.get("links") or {}
    chrome = {str(entry.get("url") or "")
              for bucket in ("nav", "footer")
              for entry in (links.get(bucket) or []) if isinstance(entry, dict)}
    out = []
    for entry in (links.get("internal") or []):
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "")
        if not url or url in chrome:
            continue
        path = urlparse(url).path.rstrip("/")
        if path and (path.rsplit("/", 1)[0] or "/") == parent and url not in out:
            out.append(url)
    return out


def _distinct_prices_on(page):
    """How many different amounts of money this page prints.

    `find_prices` rather than a bare number pattern, so a free-delivery floor
    in a site-wide banner and a figure inside an article are read the way every
    other price in this marketplace is read. Compared as numbers, because a
    page printing "MRP Rs. 399 Rs. 399" beside one item states one price twice
    and not two prices.
    """
    values = {v for v in (price_value(p) for p in
                          find_prices(page.get("body_text") or "", limit=120))
              if v is not None}
    return len(values)


def _not_the_type_it_was_given(page):
    """Why this page has not earned the type the crawl gave it, or "".

    The one answer every check in this file asks before grading a page against
    an expectation keyed to its type. Some checks asked it and some did not,
    and the ones that did not produced a whole class of false finding that
    kept coming back: "3 of 3 product pages have no Product markup" about a gift
    category, a best-sellers list and a search-results address, none of which
    is a product page and none of which should carry `Product` markup.

    Two questions, because a page can fail to be what it was called in two
    different ways:

      it is a list of things          `_why_this_page_is_a_list`, above
      it prices more things than one  the distinct-price ceiling below

    The second is asked only of a page called a product page. `DISTINCT_PRICE_CEILING`
    is the shared library's own measured separator between a detail page and a
    grid - 11 of 11 detail pages kept and 29 of 29 listings excluded on the
    data it was swept on - and the classifier skips it whenever the path is two
    or more segments deep and any price appears, which is every grid on a site
    that puts a language and a country in front of its paths. Asked here, the
    ceiling applies wherever the address happens to sit.

    Not asked of an article: a review of six products prints six prices and is
    still one piece of writing, and dropping it would cost the Article check
    the pages it exists for.
    """
    listed = _why_this_page_is_a_list(page)
    if listed:
        return listed
    if page.get("page_type") == "product":
        priced = _distinct_prices_on(page)
        if priced >= DISTINCT_PRICE_CEILING:
            return ("it prints {} different prices, which is a grid of things for sale "
                    "rather than one thing".format(priced))
    return ""


def _check_article(result, by_type, platform=None):
    result.check("article-markup")
    detected = by_type.get("article", [])
    # An index of posts is not a post, and the slug never says which one a URL
    # is. The page-type detector reads the last URL segment against a fixed
    # list of English section words, which puts three whole classes of index
    # page in front of this check:
    #
    #   a shop platform lets the owner name each blog handle, so the index of
    #   one sits at `/blogs/tasting-notes` and the word "blog" is not the
    #   segment that decides;
    #   a blogging platform puts the section word first, so a category archive
    #   is `/category/news/` and the last segment is the category name;
    #   a section landing page names itself after its subject.
    #
    # All three were reported as articles missing Article markup, and the
    # paste-ready snippet was the real damage: it offered an archive listing
    # `"headline": "Posts Categorized: News"` with `mainEntityOfPage` pointing
    # at the archive, which tells a machine that a page of links to other
    # people's dated pieces is itself one dated authored piece.
    #
    # `_not_the_type_it_was_given` asks the page instead of the URL - a page
    # whose subheadings are also its link labels is a list of links, whatever
    # it is called - and it is the same question `_headings_that_are_links_away`
    # asks one heading at a time further down this file, and the same one the
    # Product check above now asks. One answer, so no check in this file can
    # call a page an index while another calls it an article.
    listings = [p for p in detected if _not_the_type_it_was_given(p)]
    indexed = {p["url"] for p in listings}
    articles = [p for p in detected if p["url"] not in indexed]
    if listings:
        result.signal("index_pages_not_graded_as_articles", sorted(indexed)[:10])
    if not articles:
        reason = ("no article or blog post pages were crawled."
                  + unclassified_note(by_type))
        if listings:
            reason = ("the {} whose URL reads like a blog or news page is an index of "
                      "links to other pages rather than a post, so Article markup would "
                      "describe it as a dated authored piece".format(
                          plural(len(listings), "page")))
        result.skip("article-markup", reason)
        return

    # A page whose own markup says it is some other kind of work - a how-to, a
    # video, a course - has told a machine what it is. Its URL sitting under
    # `/guides/` or `/blog/` is weaker evidence than that.
    without = [p for p in articles
               if not _nodes_of(p, ARTICLE_TYPES) and not _declares_type(p, ARTICLE_TYPES)
               and not _declares_type(p, _OTHER_CREATIVE_WORKS)]
    # A page whose JSON-LD failed to parse has markup; it has a syntax error.
    # Thirteen posts were reported here as having no Article markup in the same
    # report that reported their Article block dying on a raw newline.
    without, unreadable = _set_aside_unparsed_markup(without, ARTICLE_TYPES)
    if unreadable:
        result.signal("article_pages_whose_markup_does_not_parse",
                      sorted("{} ({})".format(p["url"], why) for p, why in unreadable)[:10])
    if not without and unreadable:
        result.skip("article-markup",
                    _markup_declared_but_unreadable(unreadable, "Article", ARTICLE_TYPES,
                                                    "article page", "article pages"))
        _record_author_signal(result, articles)
        return
    if without:
        result.add(
            id_hint="no-article-schema",
            title="{} of {} article pages have no Article markup".format(
                len(without), len(articles)) if len(articles) > 1 else
                "The one article page crawled has no Article markup",
            severity="medium", confidence="high",
            evidence="Article pages with no Article JSON-LD: {}.".format(
                ", ".join(example_urls([p["url"] for p in without]))),
            # One source, the same shape as the Product check: `ARTICLE_TYPES`
            # is matched against the `@type` of the page's JSON-LD blocks and
            # nowhere else.
            # The limit is the type list, not the place. A publisher marking a
            # post up as `Recipe` or `PodcastEpisode` is doing better than one
            # using `Article`, and a reader cannot tell from "we read the
            # JSON-LD" whether those count here. They do; a type outside the
            # list does not.
            checked=("the @type of every JSON-LD block on each detected article page, "
                     "matched against the {} schema.org article types this audit "
                     "accepts, among them Article, BlogPosting, NewsArticle, "
                     "TechArticle, Recipe, Review and PodcastEpisode. A post marked "
                     "up as a type outside that list reads here as unmarked".format(
                         len(ARTICLE_TYPES)),
                     "itemtype and typeof attributes on the same pages, matched against "
                     "the same type list, for Microdata or RDFa saying the same thing"),
            mechanism="C", root_cause="no-article-schema",
            summary="Add Article JSON-LD with datePublished, dateModified and author to the post template.",
            how_to_fix=([_fix_the_parse_error_instead(unreadable, "Article")]
                        if unreadable else []) + [
                "Add the snippet below to the blog post template.",
                "`dateModified` must update when the post is edited; a static value is worse "
                "than none because it asserts freshness that is not real.",
                "`author` should be a Person with a URL to an author page where possible.",
                # The post template, not the shared one, so the owner sentence
                # goes in and the location sentence does not. See the same
                # comment in the Product check above.
                who_edits_the_template(platform),
            ],
            effort="low", owner="developer",
            rationale="Without a machine-readable date, a consumer cannot tell "
                      "whether a post is current, and undated content is discounted against "
                      "dated competitors saying the same thing.",
            affected_pages=[p["url"] for p in without],
            # The snippet is filled from a page that has the problem, not from
            # whichever article sorted first - which could be one whose markup
            # is already complete, so the paste-ready block modelled a page the
            # finding does not name.
            snippet=_article_snippet(without[0]),
        )
        _record_author_signal(result, articles)
        return

    incomplete = []
    # Only a block that is an article is graded as one. See
    # `_OTHER_CREATIVE_WORKS`: a page whose only accepted block is a video or
    # a recipe has no Article to be missing anything.
    other_works = [p for p in articles if not _nodes_of(p, _ARTICLE_FAMILY)]
    # A page that prints no date anywhere is not graded for an Article's dates
    # and author. A national library's system writes an `Article` block on
    # every page it serves - directions, jobs, help - and those pages were
    # asked for an author they never had, because the block is the system's
    # default and not a claim that the page is a piece of writing. A post that
    # prints no date is still reported, by the date-signal check that exists
    # for exactly that.
    # Undated means measured and found to carry no date: the page's date
    # reading ran and came back empty, and its own Article block declares no
    # date either. A record with no date reading at all is graded as before.
    undated = [p for p in articles
               if (p.get("dates") or {}).get("has_any") is False
               and not any(node.get("datePublished") or node.get("dateModified")
                           for node in _nodes_of(p, _ARTICLE_FAMILY))]
    if undated:
        result.signal("article_pages_not_graded_for_properties_because_undated",
                      sorted(p["url"] for p in undated)[:10])
    for page in articles:
        if page in undated:
            continue
        for node in _nodes_of(page, _ARTICLE_FAMILY):
            missing = _missing_props(node, HIGH_VALUE_PROPS["Article"])
            if missing:
                incomplete.append((page, ", ".join(missing)))
            break

    if incomplete:
        result.add(
            id_hint="article-schema-missing-properties",
            title="Article markup is missing dates or author on {}".format(
                plural(len(incomplete), "page")),
            severity="medium", confidence="high",
            evidence="; ".join("{} (missing {})".format(p["url"], why)
                               for p, why in sorted(incomplete, key=lambda x: x[0]["url"])[:5]),
            # One source. Only the first Article node on the page is graded,
            # and only that node's own properties plus the nested objects
            # `_prop_anywhere` knows about are read. A date published in a
            # second JSON-LD block on the same page is not seen.
            checked=("headline, datePublished, dateModified and author on the page's "
                     "Article block, including nested objects such as author and "
                     "publisher",),
            mechanism="D", root_cause="missing-schema-props",
            summary="Populate datePublished, dateModified and author on every article.",
            how_to_fix=[
                "Bind these properties to the post's real publish and edit timestamps in the CMS.",
                "Add an author object naming a real person, linked to an author page.",
            ],
            effort="low", owner="developer",
            rationale="Date and author are the two properties that let a consumer "
                      "decide whether to trust and quote a piece of writing.",
            affected_pages=[p["url"] for p, _ in incomplete],
            # The first page with the problem, not the first page. `articles[0]`
            # is whichever article sorted first by URL, and on a site where that
            # one is complete the snippet modelled a page this finding does not
            # name - carrying its dates and author into advice about others.
            snippet=_article_snippet(incomplete[0][0]),
        )
    else:
        graded = len(articles) - len(other_works)
        reason = ("all {} article page(s) carry Article markup with dates and an "
                  "author".format(graded))
        if other_works:
            reason = (("all {} article page(s) that declare an Article carry dates and an "
                       "author; " .format(graded) if graded else "")
                      + "{} declare{} a different kind of work - {} - and {} not graded "
                        "as an Article".format(
                            plural(len(other_works), "page"),
                            "s" if len(other_works) == 1 else "",
                            ", ".join(sorted({_OTHER_CREATIVE_WORK_NAMES[t] for p in other_works
                                              for t in _types_on(p) | _inline_types_on(p)
                                              if t in _OTHER_CREATIVE_WORKS})) or "none named",
                            "is" if len(other_works) == 1 else "are"))
        result.skip("article-markup", reason)

    _record_author_signal(result, articles)


def _record_author_signal(result, articles):
    """How many articles name nobody.

    `compose_report` gates the "give articles named authors" recommendation on
    this, and it used to be set only on the path where every article already
    carried markup - so the recommendation could never fire on the sites with
    the worst attribution, which are the ones it is for. An article with no
    Article markup at all names no author either, and counts.
    """
    result.signal("articles_without_author", len([
        p for p in articles
        if not any(_prop(n, "author") for n in _nodes_of(p, ARTICLE_TYPES))
    ]))


# A date as ISO 8601 writes it, which is what schema.org asks every date
# property to hold: a year, then optionally the month, the day and a time, and
# at most one statement of the time zone. The space some systems print between
# the date and the time is accepted - it is RFC 3339's allowance, and every
# consumer reads it - so what is reported below is a value no reader can parse
# with confidence, not a style.
_ISO_8601_DATE_RE = re.compile(
    r"^(?P<y>[0-9]{4})(?:-(?P<m>[0-9]{2})(?:-(?P<d>[0-9]{2})"
    r"(?:[T ](?P<h>[0-9]{2}):(?P<min>[0-9]{2})(?::(?P<s>[0-9]{2})(?:[.,][0-9]+)?)?"
    r"(?:Z|[+-][0-9]{2}(?::?[0-9]{2})?)?)?)?)?$|^[0-9]{8}$")

# Both zone statements at once: `Z` says the time is UTC and `+09:00` says it is
# nine hours ahead of it. One template wrote exactly that into the Article
# block of 47 of 60 pages.
_TWO_ZONES_RE = re.compile(r"Z\s*([+-][0-9]{2}(?::?[0-9]{2})?)$")

# The schema.org properties whose value is a Date or a DateTime.
_DATE_PROPERTIES = frozenset({
    "datePublished", "dateModified", "dateCreated", "uploadDate", "startDate", "endDate",
    "validFrom", "validThrough", "priceValidUntil", "availabilityStarts", "availabilityEnds",
    "birthDate", "deathDate", "foundingDate", "dissolutionDate", "expires", "datePosted",
    "releaseDate", "previousStartDate", "lastReviewed",
})


def _why_not_a_date(value):
    """Why this string is not an ISO 8601 date, or '' if it is one.

    Only strings are judged. A number or an object in a date property is a
    different fault, and a check that names one fault should not guess at
    another.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    match = _ISO_8601_DATE_RE.match(text)
    if match:
        parts = match.groupdict()
        month, day, hour, minute = (int(parts[k]) if parts.get(k) else None
                                    for k in ("m", "d", "h", "min"))
        if month is not None and not 1 <= month <= 12:
            return "its month is {:02d}".format(month)
        if day is not None and not 1 <= day <= 31:
            return "its day is {:02d}".format(day)
        if hour is not None and (hour > 24 or (minute or 0) > 59):
            return "its time is not a time of day"
        return ""
    zones = _TWO_ZONES_RE.search(text)
    if zones:
        return ("it gives the time zone twice - `Z` (UTC) and `{}` - and a time can be in "
                "only one".format(zones.group(1)))
    return "it is not written as YYYY-MM-DD, optionally followed by a time"


def _malformed_dates(pages):
    """[(page, property, value, why)] for every date property that is not a date.

    Every node on every page, nested ones included: an `Offer` inside a
    `Product` carries `priceValidUntil`, and an `Event` inside a page's
    `@graph` carries `startDate`.
    """
    found = []
    for page in pages:
        stack = list(page.get("jsonld") or [])
        seen_on_page = set()
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            for key, value in node.items():
                if key in _DATE_PROPERTIES:
                    why = _why_not_a_date(value)
                    if why and (key, value) not in seen_on_page:
                        seen_on_page.add((key, value))
                        found.append((page, key, value, why))
                elif isinstance(value, (dict, list)):
                    stack.append(value)
    return found


def _declared_date(node, key, placeholder):
    """The site's own date for `key`, or a placeholder that says why it is not used.

    A paste-ready block must not carry a value that is not a date. A national
    library's Article snippet copied `"datePublished":
    "2025-08-11T02:26:36.174Z+09:00"` back to the owner, a value carrying two
    time zones that no consumer can read - the fix repeated the fault. The
    placeholder quotes the value, so the owner can find it in the template.
    """
    value = _declared(node, key)
    why = _why_not_a_date(value)
    if not why:
        return value or placeholder
    return "<YYYY-MM-DD - the published value, '{}', is not a date: {}>".format(
        re.sub(r"[<>]", "", str(value)).replace('"', "'"), why)


def _article_snippet(page):
    """This page's Article markup, placeholders only where the page has nothing.

    ourworldindata's `/age-structure` declares
    `"datePublished": "2019-09-20T21:55:00.000Z"` and two named authors with
    their own URLs; only `dateModified` was absent. The snippet offered back
    `"datePublished": "<YYYY-MM-DD>"` and `"author": {"name": "<author
    name>"}`, so the paste-ready fix was a downgrade of markup the audit had
    already read and had in hand - and a different finding in the same report
    carried its values through correctly, so the tool contradicted itself.

    Every value here is either read off this page or an admitted placeholder.
    """
    # An article block first. A video on the same page is not the thing a
    # headline and an author belong to.
    node = next(iter(_nodes_of(page, _ARTICLE_FAMILY) or _nodes_of(page, ARTICLE_TYPES)), {})
    # The block's own headline, then the page's own H1, then an admitted
    # placeholder. Never the <title>: a browser-compatibility database serves
    # one site-wide title on every page, so this snippet arrived carrying that
    # sentence as the headline of an article it does not describe. Paste it on
    # ten posts and all ten declare the same headline, and nothing in the block
    # says a value is missing. A placeholder asks to be filled in; a plausible
    # wrong value does not.
    headline = _declared(node, "headline") or next(
        (h.strip() for h in (page.get("headings") or {}).get("h1") or [] if h.strip()), "")
    payload = {
        "@context": "https://schema.org",
        # The site's own choice of type. `BlogPosting` and `NewsArticle` are
        # more specific than `Article`, and replacing one with `Article` in a
        # snippet labelled paste-ready is a downgrade.
        "@type": _prop(node, "@type") or "Article",
        "headline": headline or "<headline>",
        "description": _paste_ready_description(
            _declared(node, "description") or page.get("meta_description") or "",
            _link_labels([page])) or "<one sentence summary>",
        # Through `_declared_date`, so a published value that is not a date is
        # named in a placeholder rather than copied into the fix.
        "datePublished": _declared_date(node, "datePublished", "<YYYY-MM-DD>"),
        "dateModified": _declared_date(node, "dateModified",
                                       "<YYYY-MM-DD, updated on every edit>"),
        "author": _declared(node, "author")
                  or {"@type": "Person", "name": "<author name>",
                      "url": "<author page URL>"},
        "publisher": _declared(node, "publisher")
                     or {"@type": "Organization", "name": "<brand name>",
                         "logo": {"@type": "ImageObject", "url": "<logo URL>"}},
        "mainEntityOfPage": _declared(node, "mainEntityOfPage") or page["url"],
    }
    return _snippet_block(payload)


# How a page says it is answering questions, in its title or a heading.
_FAQ_LABEL_RE = re.compile(
    r"\b(faqs?|frequently[- ]asked[- ]questions?|common questions|"
    r"questions (and|&) answers|q\s*(&|and)\s*a)\b", re.I)


def _headings_that_are_links_away(page):
    """Heading texts on this page that are the text of a link to another page.

    A news feed is a list of cards, and a card's heading is its link. A
    statistics charity's homepage carries `h3` titles reading "What is Moore's
    Law?" and "How does the World Bank classify countries by income?", each one
    the anchor text of a link to the article that answers it. The homepage
    answers none of them, and it and 22 other index pages were reported as
    pages that answer questions but declare no FAQPage type.

    A question heading that is the text of a link to another page is a
    headline. In-page jump links never reach here: the extractor drops any
    `href` starting with `#`, and a link back to this page's own URL is
    excluded below.
    """
    own = (page.get("url") or "").split("#")[0].rstrip("/")
    anchors = set()
    links = page.get("links") or {}
    for entry in list(links.get("internal") or []) + list(links.get("external") or []):
        if not isinstance(entry, dict):
            continue
        target = str(entry.get("url") or "").split("#")[0].rstrip("/")
        if not target or target == own:
            continue
        key = comparison_key(entry.get("text") or "")
        if key:
            anchors.add(key)
    return anchors


# Below this, a heading is too short for "it appears inside this anchor's text"
# to mean anything. "Why?" as a substring of a paragraph-long card matches by
# accident; a real question heading is longer than this.
_LINK_TITLE_MIN_CHARS = 12


def _reads_as_a_link_title(heading, anchors):
    """Is this heading the anchor text of a link, or the start of one?

    Both shapes occur. `<h3><a>What is Moore's Law?</a></h3>` gives an anchor
    whose text is the heading exactly. A whole card wrapped in one link gives
    an anchor whose text is the heading followed by the excerpt, so the heading
    is a substring of it.
    """
    key = comparison_key(heading)
    if not key:
        return False
    if key in anchors:
        return True
    if len(key) < _LINK_TITLE_MIN_CHARS:
        return False
    return any(key in anchor for anchor in anchors)


# A heading carried by at least this share of the crawled pages belongs to the
# template, not to any one page. Half is deliberately loose: a sidebar widget
# or a footer block is usually on every page, and a heading that a genuine
# Q&A block happens to share with half the site is not a question that page is
# the answer to either.
CHROME_HEADING_SHARE = 0.5

# Below this many pages there is no "most pages" to measure. On a three-page
# crawl a heading on two of them is as likely to be one page's real subject as
# it is to be furniture, and calling it chrome would delete real questions
# from a small site's only FAQ.
CHROME_MINIMUM_PAGES = 4


def _headings_of(page):
    """The subheading texts this page shows, as comparison keys."""
    headings = page.get("headings") or {}
    found = [h for level in ("h2", "h3") for h in (headings.get(level) or [])]
    found += [s.get("heading") for s in (page.get("sections") or [])]
    keys = {comparison_key(h) for h in found if h}
    keys.discard("")
    return keys


def _site_furniture_headings(pages):
    """Headings that are on most of the crawled pages, so belong to no page.

    `_reads_as_a_link_title` catches a question this page points at. This
    catches a question the template prints beside every page's content, which
    the same finding was publishing as a question the page answers: a
    library's sidebar widget headed "Questions?" above a phone number and a
    text-message number appeared on every page of the site, and the generated
    FAQPage snippet turned it into a `Question` node with an `acceptedAnswer`.
    An owner pasting that ships markup asserting that their contact block is a
    frequently asked question.

    Repetition across the crawl is the measurable form of "this is the
    template speaking", and the snapshot holds every page's headings, so it
    costs one pass to ask.
    """
    if len(pages) < CHROME_MINIMUM_PAGES:
        return frozenset()
    seen = Counter()
    for page in pages:
        seen.update(_headings_of(page))
    cutoff = len(pages) * CHROME_HEADING_SHARE
    return frozenset(key for key, count in seen.items() if count >= cutoff)


def _questions_asked_on(page, furniture=frozenset()):
    """The headings on this page a visitor would read as questions.

    Headings that are link titles are left out: they are questions this page
    points at, not questions it answers, and the whole finding downstream is
    about a page that answers questions without saying so in markup. Headings
    the whole site carries are left out for the same reason one step further
    out - they are the template's, not this page's.

    Asked, not answered. Every caller wants `_question_headings_on` below,
    which is this list with the unanswered ones taken out; this one exists so
    that "the page asks no questions at all" can be told from "the page asks
    them and answers none", which are different pages and want different
    treatment.
    """
    headings = page.get("headings") or {}
    found = [h for level in ("h2", "h3") for h in (headings.get(level) or [])]
    found += [s.get("heading") for s in (page.get("sections") or [])]
    anchors = _headings_that_are_links_away(page)
    return sorted({h for h in found
                   if h and is_question_heading(h)
                   and not _reads_as_a_link_title(h, anchors)
                   and comparison_key(h) not in furniture})


# The shortest run of prose the crawl will record as the paragraph under a
# heading: `_sections` in `page_extract.py` takes the first `<p>`, `<li>`,
# `<dd>` or `<td>` under an H2 and keeps it only at this length or above.
# Reused here so that "does the page answer this question" is settled the same
# way wherever the question sits. Under a heading with less than this there is
# something beside the heading, not an answer to it.
ANSWER_MIN_CHARS = 40

# How far the answer reader will go looking. One heading may be printed in a
# jump menu and again over its own answer, and the second copy is the one that
# matters, so repeats are followed; a page that prints one string hundreds of
# times is pathological and the scan below is quadratic in what it finds.
_HEADING_OCCURRENCE_LIMIT = 8
_HEADING_MARK_LIMIT = 400

# How many answered questions make a page a page of questions and answers.
# Two, which is the floor `_faq_snippet` has always applied to the markup it
# writes - "if fewer than two real questions remain, emit a placeholder rather
# than a fabrication" - stated once and applied to the claim as well as to the
# snippet, so the finding cannot name a page the snippet then refuses to
# describe.
MIN_ANSWERED_QUESTIONS = 2


def _all_heading_texts(page):
    """Every heading string this page carries, at any level."""
    headings = page.get("headings") or {}
    found = [h for level in ("h1", "h2", "h3") for h in (headings.get(level) or [])]
    found += [s.get("heading") for s in (page.get("sections") or [])]
    return [h for h in found if h]


def _page_prose(page):
    """The page's own words, with the template's removed where they could be.

    `body_text` is the content region with header, navigation, footer and
    aside taken out, so the run after the last heading on the page is the
    page's own text and not the site-wide footer - which would otherwise read
    as an answer to whatever question the page ends on. Where the extractor
    could not find a content region `body_text` is empty and the whole visible
    text is all there is to read.
    """
    return (page.get("body_text") or "").strip() or (page.get("text") or "")


def _text_under_each_heading(page):
    """`{heading: the words the page prints between it and the next heading}`.

    `sections` answers this for an H2 and for nothing else, and a
    question-and-answer block written with H3s is ordinary. The page text
    holds every heading in document order, so the span from the end of one
    heading to the start of the next is what sits under it, whatever level it
    was written at.

    Where a heading occurs more than once - a jump menu at the top of a long
    FAQ prints every question before the page answers any of them - the
    longest span wins. The menu entry is followed by the next menu entry; the
    real one is followed by the answer.
    """
    prose = _page_prose(page)
    if not prose:
        return {}
    marks = []
    for heading in _all_heading_texts(page):
        at, seen = 0, 0
        while seen < _HEADING_OCCURRENCE_LIMIT and len(marks) < _HEADING_MARK_LIMIT:
            at = prose.find(heading, at)
            if at < 0:
                break
            marks.append((at, at + len(heading), heading))
            at += 1
            seen += 1
    marks.sort()
    under = {}
    for index in range(len(marks)):
        end, heading = marks[index][1], marks[index][2]
        stop = len(prose)
        # The next heading that starts after this one ends, not simply the
        # next mark: one heading can be a substring of another - "Do you
        # deliver?" inside "Do you deliver on Saturdays?" - and the inner
        # match would otherwise close the outer heading's span at zero
        # characters and report an answered question as unanswered.
        for later in range(index + 1, len(marks)):
            if marks[later][0] >= end:
                stop = marks[later][0]
                break
        gap = prose[end:stop].strip()
        if len(gap) > len(under.get(heading, "")):
            under[heading] = gap
    return under


def _answers_on(page, furniture=frozenset()):
    """`{question heading: its answer}` for the questions this page answers.

    The test that was missing, and the one the whole finding rests on. A media
    site's games page carries the titles of three videos it links to, each one
    written as a question and each one a heading. Nothing on the page answers
    any of them - the answers are in the videos - and the report named it as
    "1 page of questions and answers has no FAQPage markup" and shipped a
    paste-ready `FAQPage` block whose three `acceptedAnswer` values had no
    source anywhere on the page. That is invalid markup of the kind a search
    engine demotes a site for, offered second in what to fix first.

    `_reads_as_a_link_title` exists to catch exactly that page and could not
    see it. It compares a heading against the anchor text the crawl recorded,
    and `_links` records one entry per destination URL, first anchor wins: a
    card whose thumbnail image is a link to the video, printed before the
    title link to the same video, contributes an entry whose text is empty and
    swallows the title anchor as a duplicate. So the question was never in the
    anchor set. `_site_furniture_headings` could not see it either - three
    video titles on one page are that page's own content, not the template's.

    Both of those ask "did this heading come from somewhere else". The
    question they were standing in for is "does this page answer it", and that
    one is answerable directly and does not depend on how the card was marked
    up. Two sources, because the crawl records the paragraph under an H2 and
    nothing under an H3:

      * the section paragraph the extractor already read, which it only keeps
        at `ANSWER_MIN_CHARS` or above;
      * the words the page prints between that heading and the next one.

    A question answered only by the second is answered but not quotable as one
    paragraph, so it maps to the empty string and the snippet asks the owner
    for the wording instead of inventing it.
    """
    paragraphs = {}
    for section in page.get("sections") or []:
        heading = section.get("heading")
        if heading and heading not in paragraphs:
            paragraphs[heading] = (section.get("first_paragraph") or "").strip()
    under, answers = None, {}
    for heading in _questions_asked_on(page, furniture):
        paragraph = paragraphs.get(heading) or ""
        if paragraph:
            answers[heading] = paragraph
            continue
        if under is None:
            under = _text_under_each_heading(page)
        if len(under.get(heading, "")) >= ANSWER_MIN_CHARS:
            answers[heading] = ""
    return answers


def _question_headings_on(page, furniture=frozenset()):
    """The questions this page both asks and answers."""
    return sorted(_answers_on(page, furniture))


def _calls_itself_an_faq(page):
    """Does the page use the word FAQ about itself?"""
    headings = page.get("headings") or {}
    labels = [page.get("title") or ""]
    for level in ("h1", "h2", "h3"):
        labels += [h for h in (headings.get(level) or [])]
    labels += [s.get("heading") or "" for s in (page.get("sections") or [])]
    return any(_FAQ_LABEL_RE.search(label) for label in labels if label)


# The question marks a page can be written with: ASCII, the fullwidth mark used
# in CJK typesetting, and the Arabic one. Written with chr() so this file stays
# ASCII and cannot lose a character in transit.
_QUESTION_MARKS = "?" + chr(0xFF1F) + chr(0x061F)


def _prints_questions_and_room_to_answer_them(page):
    """Does this page's own text hold questions, and words enough to answer them?

    The test the `<dl>` branch below was missing. That branch admits a page on
    its own label - the word FAQ in the title - precisely because a
    `<dl><dt>question<dd>answer</dl>` block carries no heading for a heading
    reader to find. What it never asked was whether the page carries anything
    at all.

    A shop's `/pages/faqs` was named in a medium finding as a page that answers
    questions and declares no FAQPage type. Its entire main content is the
    placeholder string `FAQs text goes here` - nineteen characters, no
    question, no answer - and the same report's own extractability finding
    called that URL "293 chars... too little text to be quoted". Two findings
    in one report about one page, and they cannot both be true.

    Question marks are the one trace a `<dl>` block leaves in the page text, so
    they are what is counted, against the same floor of two the rest of this
    check applies. The length floor is two answers' worth at `ANSWER_MIN_CHARS`
    each, which is the shortest thing this check has ever been willing to call
    an answer.
    """
    prose = _page_prose(page).strip()
    asked = sum(prose.count(mark) for mark in _QUESTION_MARKS)
    return (asked >= MIN_ANSWERED_QUESTIONS
            and len(prose) >= MIN_ANSWERED_QUESTIONS * ANSWER_MIN_CHARS)


def _answers_questions(page, furniture=frozenset()):
    """Is this a page that answers questions, or one that only lists them?

    The gate on every page this finding names, including the ones the crawl
    already typed `faq`. A page is typed `faq` from four question-shaped H2s
    or from its own address, and neither of those says an answer is printed on
    it - a page of links whose labels are questions satisfies both.

    The second branch is for the shape a heading reader cannot see: a
    question-and-answer list written as `<dl><dt>question<dd>answer</dl>`
    carries no heading at all, and the site's own label for the page - the
    word FAQ in its title or a heading - is evidence of what it is that no
    reading of headings could supply. It requires the *absence* of question
    headings, so it cannot readmit a page whose headings are questions the
    page does not answer, which is the page this gate exists to keep out.

    It also requires the page to have written something, because "no question
    headings" is as true of an unfinished page as of a `<dl>` block - see
    `_prints_questions_and_room_to_answer_them`.
    """
    if len(_question_headings_on(page, furniture)) >= MIN_ANSWERED_QUESTIONS:
        return True
    return (not _questions_asked_on(page, furniture)
            and _calls_itself_an_faq(page)
            and _prints_questions_and_room_to_answer_them(page))


def _unmarked_qa_pages(by_type, furniture=frozenset()):
    """Pages answering questions without a page type or markup that says so.

    a browser-compatibility database's about page prints the heading `FAQ` and under it `May I use
    your data in my presentation/article/site, etc?` with its answer. The
    page-type detector calls a page an FAQ only when four of its H2s are
    questions and they are half of all H2s, which is the right rule for
    classifying a whole page and blind to a Q&A section inside a page about
    something else. So the appendix said "no FAQ pages were detected on this
    site" and the recommendations then proposed publishing FAQ content the
    site had already written.

    Three question headings, or two on a page that calls the block an FAQ.
    Below that it is a page with a question in a subheading, not a Q&A block.
    Answered ones throughout: `_question_headings_on` counts a question only
    where the page prints an answer under it, so a listing whose card titles
    are questions never reaches the threshold however many cards it has.
    """
    out = []
    for page_type, pages in (by_type or {}).items():
        if page_type == "faq":
            continue
        for page in pages:
            if _nodes_of(page, FAQ_TYPES):
                continue
            # A list of other pages is not a page of questions and answers,
            # whatever its headings read like. The `faq`-typed candidates above
            # have been through this gate since the help-centre index that
            # forced it; these had not, and that is how a paginated blog index
            # whose card titles end in question marks was handed a paste-ready
            # FAQPage block, and how a shop's news listing was named as an FAQ
            # page in a report whose own next sentence called it a category
            # page. `_not_the_type_it_was_given` is the answer the Product and
            # Article checks use, so no two of the three can disagree about
            # one page.
            if _not_the_type_it_was_given(page):
                continue
            questions = _question_headings_on(page, furniture)
            if len(questions) >= 3 or (len(questions) >= 2 and _calls_itself_an_faq(page)):
                out.append(page)
    return sorted(out, key=lambda p: p["url"])


def _has_an_answer_worth_marking_up(page, furniture=frozenset()):
    """Would the snippet for this page carry any real answer text at all?

    The finding's own second step says the answer text in the markup must match
    the answer on the page word for word. A block whose every `acceptedAnswer`
    is `<the answer, verbatim from the page>` states nothing to match, and the
    page it was written for is a page with no answers on it: a paginated blog
    index whose "questions" are post titles and whose "answers" are the
    truncated excerpts under them, which end in an ellipsis and so become
    placeholders one by one.

    Read off the finished snippet rather than guessed at, so the claim and the
    block underneath it cannot disagree.
    """
    answers = _answers_on(page, furniture)
    recorded = [a for a in answers.values() if a]
    # No paragraph was recorded under any of them, which is what a Q&A block
    # written with H3s looks like: `sections` covers H2s only, so the page
    # answers every question and the crawl kept the wording of none. The
    # snippet asks the owner for the wording and says so, and that is a page
    # worth marking up.
    if not recorded:
        return True
    # Paragraphs were recorded and every one of them is a fragment. That is a
    # feed of cards, not a page of answers: the "answer" is a truncated excerpt
    # under a headline, and nothing on the page matches what the block would
    # claim.
    return any(_answer_or_placeholder(a) != _ANSWER_PLACEHOLDER for a in recorded)


def _faq_blocks_with_empty_answers(pages):
    """[(page, empty, total)] for FAQPage blocks whose answers hold no text."""
    found, seen = [], set()
    for page in pages:
        if page.get("url") in seen:
            continue
        for node in _nodes_of(page, FAQ_TYPES):
            questions = node.get("mainEntity")
            questions = [questions] if isinstance(questions, dict) else questions or []
            questions = [q for q in questions if isinstance(q, dict)]
            if not questions:
                continue
            empty = 0
            for question in questions:
                answer = question.get("acceptedAnswer")
                answer = answer[0] if isinstance(answer, list) and answer else answer
                text = str((answer or {}).get("text") or "") if isinstance(answer, dict) else ""
                if not re.sub(r"<[^>]+>|&nbsp;|\s", "", text):
                    empty += 1
            if empty:
                seen.add(page.get("url"))
                found.append((page, empty, len(questions)))
                break
    return found


def _report_empty_faq_answers(result, hollow, platform=None):
    result.check("faq-markup")
    page, empty, total = hollow[0]
    result.add(
        id_hint="faq-markup-answers-are-empty",
        title="{} FAQPage markup whose answers are empty".format(
            plural(len(hollow), "page carries", "pages carry")),
        severity="medium", confidence="high",
        evidence="Example: {} declares {} {} whose `acceptedAnswer.text` is empty. Seen on "
                 "{}.".format(page["url"], empty, "question" if empty == 1 else "questions",
                              plural(len(hollow), "page")),
        checked=("the `acceptedAnswer.text` of every Question in every FAQPage block on every "
                 "crawled page, read after removing tags and non-breaking spaces",),
        mechanism="C", root_cause="missing-schema-props",
        summary="Fill each acceptedAnswer with the answer text the page shows, or remove the "
                "block.",
        how_to_fix=[
            "Bind `acceptedAnswer.text` to the same field that renders the visible answer, so "
            "each question carries the words the page prints beneath it.",
            "Where the page shows no answers, stop emitting the FAQPage block on it: a question "
            "with no answer is not a question and answer.",
            who_edits_the_template(platform),
        ],
        effort="low", owner="developer",
        rationale="An answer is the one part of FAQPage markup a consumer quotes. Empty, the "
                  "block hands over questions with nothing to say about them.",
        affected_pages=[p["url"] for p, _, _ in hollow],
    )


def _check_faq(result, by_type, all_pages=(), platform=None):
    result.check("faq-markup")
    # Measured over every page the crawl fetched, not only the content pages
    # this check grades: a sidebar heading is furniture because the template
    # prints it everywhere, and a legal page or a category page carrying it is
    # as much evidence of that as an about page is.
    furniture = _site_furniture_headings(list(all_pages) or [
        p for pages in (by_type or {}).values() for p in pages])
    # A page the crawl typed `faq` is a candidate, not a verdict. `_answers_questions`
    # is what turns it into one, and the pages it turns away are pages whose
    # question-shaped headings are answered somewhere else - a listing of
    # linked titles, which `FAQPage` markup would misdescribe.
    typed = by_type.get("faq", [])
    # ... and neither is a page that is a list of other pages. A help centre's
    # index links out to one page per question, so its headings read as
    # questions and `_answers_questions` can see a paragraph under each of
    # them. `_not_the_type_it_was_given` is the same answer the Product and
    # Article checks ask for, so the three cannot disagree about one page.
    faqs = [p for p in typed
            if _answers_questions(p, furniture) and not _not_the_type_it_was_given(p)]
    answering = {p["url"] for p in faqs}
    listing_only = [p for p in typed if p["url"] not in answering]
    without = [p for p in faqs if not _nodes_of(p, FAQ_TYPES)]
    unmarked = _unmarked_qa_pages(by_type, furniture)
    seen = {p["url"] for p in without}
    without += [p for p in unmarked if p["url"] not in seen]
    result.signal("pages_with_unmarked_qa", len(unmarked))
    # A block that failed to parse is markup, not an absence, the same rule the
    # Product and Article checks apply.
    without, unreadable = _set_aside_unparsed_markup(without, FAQ_TYPES)
    # And a page whose every answer would be a placeholder has nothing to mark
    # up. The snippet is the evidence: a paginated index of posts produced a
    # `FAQPage` block of post titles whose answers were all
    # `<the answer, verbatim from the page>`, under a step demanding the answer
    # match the page word for word.
    nothing_to_quote = [p for p in without
                        if not _has_an_answer_worth_marking_up(p, furniture)]
    if nothing_to_quote:
        skipped = {p["url"] for p in nothing_to_quote}
        without = [p for p in without if p["url"] not in skipped]
        result.signal("pages_with_no_answer_text_to_mark_up", sorted(skipped)[:10])
    if unreadable:
        result.signal("faq_pages_whose_markup_does_not_parse",
                      sorted("{} ({})".format(p["url"], why) for p, why in unreadable)[:10])
    # Markup that is present and says nothing. A furniture shop's five regional
    # homepages each carry an FAQPage block of four or five questions whose
    # `acceptedAnswer.text` is the empty string, and this check - which only
    # asked whether a page of questions had the type at all - passed them.
    hollow = _faq_blocks_with_empty_answers(list(all_pages) or [
        p for group in (by_type or {}).values() for p in group])
    if hollow:
        _report_empty_faq_answers(result, hollow, platform)
    if not faqs and not unmarked:
        if hollow:
            return
        result.skip("faq-markup",
                    "no page that prints an answer under a question heading was detected "
                    "on this site{}.{}".format(
                        # Said out loud rather than left as a silent exclusion.
                        # A reader whose page is full of questions is entitled
                        # to know it was read and why it is not listed.
                        ", though {} carry question-shaped headings whose answers are on "
                        "the pages they link to".format(
                            plural(len(listing_only), "page does", "pages do"))
                        if listing_only else "",
                        unclassified_note(by_type)))
        return
    if not without:
        if hollow:
            return
        # The set-aside was computed above and then not said. "All detected FAQ
        # pages carry FAQPage markup" is false about a page whose FAQPage block
        # is in the crawl and does not parse - it is the same sentence the
        # Organization and WebSite checks were caught printing, one step
        # further on: not an absence asserted, but a presence asserted, about a
        # block no consumer can read.
        result.skip("faq-markup",
                    _markup_declared_but_unreadable(unreadable, "FAQPage", FAQ_TYPES,
                                                    "page of questions and answers",
                                                    "pages of questions and answers")
                    if unreadable else "all detected FAQ pages carry FAQPage markup")
        return

    questions = _question_headings_on(without[0], furniture)
    # The snippet is one page's questions and answers. Where the finding names
    # more than one page, the first step has to say so in words: a report that
    # printed the /donate page's Q&A under a list of 23 pages, and told the
    # reader to add "the snippet below", was instructing them to publish
    # donation answers in the markup of a biodiversity page - the exact
    # mismatch the step below it calls spam.
    if len(without) > 1:
        first_step = ("Add FAQPage markup to each page listed, holding that page's own "
                      "questions and its own answer text verbatim. The snippet below is an "
                      "example built from {} alone; rewrite it per page rather than pasting "
                      "it as it stands.".format(without[0]["url"]))
    else:
        first_step = ("Add FAQPage markup listing each question on {} and its answer text "
                      "verbatim.".format(without[0]["url"]))
    # A block whose every answer is a placeholder is not paste-ready, whatever
    # the finding calls it. A wallpaper shop's FAQ page produced sixteen
    # questions each answered `<the answer, verbatim from the page>`: the crawl
    # kept only the first few hundred characters of each answer, so none could
    # be quoted word for word. Where that is so, the questions are given as a
    # list and the step says the answers have to be copied in from the page;
    # no block is offered.
    payload = _faq_payload(without[0], furniture)
    unread = _faq_answers_unread(payload)
    extra = {}
    if unread:
        questions_listed = [str(e.get("name") or "") for e in payload.get("mainEntity") or []
                            if not str(e.get("name") or "").startswith("<")]
        first_step = (
            "Add FAQPage markup to {} by hand: none of its answers could be read off the page "
            "in full, so no block is given here. Write one Question per question the page "
            "asks, and copy the answer the page prints beneath it, word for word, into "
            "`acceptedAnswer.text`.{}".format(
                without[0]["url"],
                " The questions are: {}.".format("; ".join(
                    '"{}"'.format(truncate(q, 90)) for q in questions_listed[:FAQ_SNIPPET_MAX]))
                if questions_listed else ""))
        result.signal("faq_answers_not_readable", without[0]["url"])
    else:
        extra["snippet"] = _snippet_block(payload)
    result.add(
        id_hint="no-faq-schema",
        title="{} no FAQPage markup".format(
            plural(len(without), "page of questions and answers has",
                   "pages of questions and answers have")),
        severity="medium", confidence="high",
        evidence="Pages that answer questions but declare no FAQPage type: {}.{}".format(
            ", ".join(example_urls([p["url"] for p in without])),
            " Questions read off {}: {}.".format(
                without[0]["url"], "; ".join(truncate(q, 90) for q in questions[:3]))
            if questions else ""),
        # Four sources, all of them read above. The page type comes from the
        # crawl's classifier; the markup lookup is `_nodes_of(page, FAQ_TYPES)`;
        # the headings come from `_question_headings_on`, which asks
        # `audit_common.is_question_heading`, and from `_calls_itself_an_faq`.
        # The third is why a browser-compatibility database's Q&A block inside
        # an about page is named here at all: on the page type alone the report
        # said no FAQ existed while telling the owner to write one. The fourth
        # is the one that decides whether there is anything here to mark up at
        # all, and it is named so a reader can check it against their page.
        checked=("the FAQPage and QAPage JSON-LD types on each page",
                 "the page type the crawl assigned each page",
                 "headings that read as questions, and any title or heading using "
                 "the word FAQ. A question heading that is also the text of a link "
                 "away, or that appears on {}% or more of the crawled pages and so "
                 "belongs to the template rather than to this page, is not counted "
                 "here and is not in the snippet".format(int(CHROME_HEADING_SHARE * 100)),
                 "the text each of those headings has under it, on the page itself: "
                 "the paragraph the crawl recorded beneath it, or the words between "
                 "it and the next heading. A question with nothing under it is a "
                 "question this page asks and another page answers, and it is "
                 "neither counted here nor written into the snippet"),
        mechanism="C", root_cause="no-faq-schema",
        summary="Wrap the existing questions and answers in FAQPage JSON-LD.",
        how_to_fix=[
            first_step,
            "The answer text in the markup must match the answer on that page word for word; "
            "a mismatch is treated as spam by several consumers. A placeholder in the "
            "snippet marks text this audit could not confirm word for word on the page; "
            "write the real answer there rather than publishing the placeholder.",
            # A page of questions is the one thing a school, a council and a
            # shop all publish, and this check is gated on nothing, so the step
            # may not say what the site sells.
            "Phrase the questions the way a person would ask an assistant, not as the "
            "internal section headings a page of questions is usually written from.",
            who_edits_the_template(platform),
        ],
        effort="low", owner="developer",
        rationale="An FAQPage block hands over question-and-answer pairs already "
                  "shaped like the thing an assistant is trying to produce, which makes them "
                  "unusually easy to quote.",
        affected_pages=[p["url"] for p in without],
        **extra
    )


# Enough for any real FAQ page. It used to be three, undisclosed, so a page
# with five questions produced markup covering three - and the FAQ check then
# passed that markup, so the truncation was never caught by anything.
FAQ_SNIPPET_MAX = 20

# The characters a shortened string ends in. `truncate` appends U+2026 when it
# cuts a paragraph at its limit, and some pages write three full stops
# themselves.
_ELLIPSIS_ENDS = ("…", "...")

# What the snippet prints where the wording of an answer could not be confirmed
# on the page. Named once, because `_has_an_answer_worth_marking_up` asks
# whether a snippet is nothing but these.
_ANSWER_PLACEHOLDER = "<the answer, verbatim from the page>"


def _answer_or_placeholder(paragraph):
    """The paragraph, unless it is a fragment, in which case a placeholder.

    The instruction printed above this snippet says the answer text in the
    markup must match the answer on the page, and warns that a mismatch is
    treated as spam by several consumers. A paragraph the crawl cut off at 500
    characters ends in an ellipsis and matches nothing on the page, so shipping
    it inside `acceptedAnswer.text` breaks the rule the same finding states two
    lines earlier. One report did exactly that, with an answer ending
    "...we understand that this means your ...".
    """
    answer = (paragraph or "").strip()
    if not answer or answer.endswith(_ELLIPSIS_ENDS) or answer.startswith(_ELLIPSIS_ENDS):
        return _ANSWER_PLACEHOLDER
    return answer


def _faq_snippet(page, furniture=frozenset()):
    """The paste-ready FAQPage block for this page. See `_faq_payload`."""
    return _snippet_block(_faq_payload(page, furniture))


def _faq_answers_unread(payload):
    """Is every answer in this FAQPage payload a placeholder?"""
    entities = payload.get("mainEntity") or []
    return bool(entities) and all(
        str(((e.get("acceptedAnswer") or {}).get("text")) or "").startswith("<")
        for e in entities)


def _faq_payload(page, furniture=frozenset()):
    """Every question on the page, and nothing that is not a question.

    The instruction printed above this snippet says "listing each question and
    its answer text verbatim". The snippet did not do what its own instruction
    said. It dropped 40% of a five-question page, with no ellipsis and no note;
    that was fixed. What it also did was take every section heading on the page
    and file it as a `Question`, whether or not it was one, and three separate
    real sites showed what that produces:

      * a contributor style guide whose headings are policy statements
        ("Duplication is evil", "Code style") became twenty-four `Question`
        nodes asking nothing;
      * a shop's help page carries a short FAQ followed by the full privacy
        policy and terms of service, and the whole legal text was marked up as
        question-and-answer pairs;
      * a product page's "Solved community questions" widget supplied a
        heading that was paired with an unrelated paragraph as its answer;
      * a library's sidebar heading "Questions?", printed above a phone number
        on every page of the site, became a `Question` node with an
        `acceptedAnswer` on four separate pages;
      * a media site's games page, whose three headings are the titles of the
        videos it links to, became three `Question` nodes whose answers exist
        in no form anywhere on the page.

    All of them are the pattern Google's own FAQ guidance names as ineligible,
    and the finding directly above the snippet warns the reader that a
    mismatch between markup and page "is treated as spam by several
    consumers". So only headings that are actually questions go in, only where
    the page prints something under them, and a page with fewer than two of
    those gets the placeholder rather than a fabrication.
    """
    # `_answers_on` has already applied every test the list below used to
    # apply inline - it is a question, it is not the label of a link away, it
    # is not site furniture - and one more they could not: the page answers
    # it. One definition, so the snippet cannot publish a question the finding
    # above it did not count.
    answers = _answers_on(page, furniture)
    sections = [s for s in (page.get("sections") or [])
                if s.get("heading") in answers][:FAQ_SNIPPET_MAX]
    entities = []
    for section in sections:
        answer = _answer_or_placeholder(section.get("first_paragraph"))
        entities.append({
            "@type": "Question",
            "name": section["heading"],
            "acceptedAnswer": {"@type": "Answer", "text": answer},
        })
    if len(entities) < MIN_ANSWERED_QUESTIONS:
        # A Q&A block written with H3s. `sections` are built from H2s, so a
        # page whose questions sit a level down produced no entities at all -
        # on the very pages this finding now reaches, where the block is inside
        # a page about something else. The questions were read off the page and
        # the page was checked for an answer under each of them; only the
        # wording of the answer is blank, and it says so.
        named = {e["name"] for e in entities}
        for heading in sorted(answers)[:FAQ_SNIPPET_MAX]:
            if heading in named:
                continue
            entities.append({
                "@type": "Question",
                "name": heading,
                "acceptedAnswer": {"@type": "Answer",
                                   "text": _ANSWER_PLACEHOLDER},
            })
    if len(entities) < MIN_ANSWERED_QUESTIONS:
        entities = [{"@type": "Question", "name": "<question as a visitor would ask it>",
                     "acceptedAnswer": {"@type": "Answer", "text": "<the answer, verbatim>"}}]
    # The snippet is one page's Q&A and nothing else. Saying so belongs in the
    # fix text, not inside the block: a snippet is paste-ready JSON-LD, and an
    # HTML comment in front of it makes the whole thing fail to parse, which is
    # worse than the confusion it was added to prevent. `_check_faq` names the
    # page the questions came from in its first step instead.
    return {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": entities}


def _check_breadcrumbs(result, pages, by_type, platform=None):
    result.check("breadcrumb-markup")
    # Deep means two path segments or more. `depth` is link distance from the
    # homepage, and a database project's `/docs.html`, `/android/` and `/sqlar/`
    # sit one click and one segment from the root - there is no trail to mark
    # up above a page whose parent is the homepage. The engagement skill's
    # visible-breadcrumb check draws the same line.
    crawled_deep = [p for p in pages if p["page_type"] in DEEP_TYPES and p.get("depth", 0) >= 1
                    and len([s for s in urlparse(p.get("url") or "").path.split("/") if s]) >= 2]
    if len(crawled_deep) < 3:
        result.skip("breadcrumb-markup",
                    "fewer than 3 deep pages were crawled, so breadcrumb markup would not "
                    "meaningfully change how the site is understood")
        return
    # Same rule as the Product, Article and FAQ checks: a page whose JSON-LD
    # does not parse either has BreadcrumbList markup or does not, and this
    # check cannot tell which, because `jsonld_types` holds the blocks that
    # parsed. Set aside before the ratio rather than after it: this is the one
    # absence check in the file that fires on a proportion, so counting an
    # unreadable page as one without markup does not just widen a list, it can
    # be what pushes the site over the line into a finding.
    deep, unreadable = _set_aside_unparsed_markup(crawled_deep, {"breadcrumblist"})
    if unreadable:
        result.signal("deep_pages_whose_markup_does_not_parse",
                      sorted("{} ({})".format(p["url"], why) for p, why in unreadable)[:10])
    if len(deep) < 3:
        result.skip("breadcrumb-markup",
                    _markup_declared_but_unreadable(unreadable, "BreadcrumbList", {"breadcrumblist"},
                                                    "deep page", "deep pages")
                    + " That leaves fewer than 3 deep pages this check can read.")
        return
    # The crawler already resolved either notation into `breadcrumb.markup`,
    # covering JSON-LD and `itemtype="...BreadcrumbList"` Microdata alike. This
    # read `jsonld_types` alone, so a site publishing correct Microdata
    # breadcrumbs - which plenty of older templates do - was told it has none.
    without = [p for p in deep
               if "breadcrumblist" not in _types_on(p)
               and not (p.get("breadcrumb") or {}).get("markup")]
    if len(without) < len(deep) * 0.5:
        result.skip("breadcrumb-markup",
                    "{} of {} deep pages already declare BreadcrumbList".format(
                        len(deep) - len(without), len(deep)))
        return

    result.add(
        id_hint="no-breadcrumb-schema",
        title="Deep pages have no BreadcrumbList markup",
        severity="low", confidence="high",
        evidence="{} of {} crawled deep pages declare no BreadcrumbList. Examples: {}.".format(
            len(without), len(deep), ", ".join(example_urls([p["url"] for p in without]))),
        # Two, and both are read in the filter above. The second is the reason
        # a restaurant group's older template is no longer told it has no
        # breadcrumb markup: the crawler's breadcrumb reading also matches
        # `itemtype="...BreadcrumbList"`, which `jsonld_types` alone misses.
        checked=("BreadcrumbList in the JSON-LD types on each deep page",
                 "the crawler's breadcrumb reading, which also covers "
                 "itemtype=\"BreadcrumbList\" microdata"),
        mechanism="C", root_cause="no-breadcrumb-markup",
        summary="Add BreadcrumbList JSON-LD to deep page templates.",
        how_to_fix=([_fix_the_parse_error_instead(unreadable, "BreadcrumbList")]
                    if unreadable else []) + [
            "Add BreadcrumbList markup mirroring the visible breadcrumb trail.",
            "Include the home page as position 1 and the current page as the last item.",
            "If there is no visible breadcrumb, add one; it helps visitors as much as machines.",
            who_edits_the_template(platform),
        ],
        effort="low", owner="developer",
        rationale="Breadcrumbs state where a page sits in the site's structure, "
                  "which tells a consumer what category the content belongs to without having "
                  "to infer it from the URL.",
        affected_pages=[p["url"] for p in without],
    )


def _links_on(page):
    """Every link the extractor recorded for a page, as {url, text} entries."""
    links = page.get("links") or {}
    out = []
    for bucket in ("internal", "nav", "footer"):
        out += [entry for entry in (links.get(bucket) or []) if isinstance(entry, dict)]
    return out


def _bare_host(url):
    """The hostname, without `www.` and without a default port, for comparing
    two URLs' origins.

    Hand-rolled here, which meant `www.example.test:443` and
    `www.example.test` compared as two different origins on a site whose
    redirect spells the port out. `strip_www` in the shared library takes both
    off.
    """
    return strip_www(urlparse(str(url or "")).netloc.lower())


# A path or query that is the site's search, and a link that is labelled as it.
_SEARCH_URL_RE = re.compile(r"/search|[?&](q|s|query|search)=", re.I)
_SEARCH_TEXT_RE = re.compile(r"^\s*(search|search this site|site search)\b", re.I)


def _form_target(page, form):
    """Where this form submits, as an absolute URL.

    Resolved against the page the form sits on, because a relative action means
    something different on `/docs/` than on `/`.
    """
    return urljoin(page.get("url") or "", str(form.get("action") or "").strip())


def _looks_like_a_search_form(form):
    """The extractor's own answer, plus a query-shaped action it does not read.

    `is_search` is set for `role=search`, an `input[type=search]`, and an action
    or a legend saying search. A form posting to `/find?q=` says the same thing
    in the only place left.
    """
    return bool(form.get("is_search")
                or _SEARCH_URL_RE.search(str(form.get("action") or "")))


def _search_forms(pages, host):
    """Search forms split by whose index they query: this site's, or another's.

    Two of six audited sites put a general web-search form in their header, and
    both were told to publish
    `"target": "https://<a search engine>/search?q={search_term_string}"`. A
    `SearchAction` declares the endpoint of the *site's own* search, so an
    external engine's address is not a sitelinks-searchbox declaration and buys
    the site nothing. The true and more useful statement about those two sites
    is the opposite one: neither has a search index of its own.

    An empty or relative action resolves back to the page it is on, so it is
    always this site's.
    """
    ours, theirs = [], []
    for page in pages:
        for form in (page.get("forms") or []):
            if not _looks_like_a_search_form(form):
                continue
            target_host = _bare_host(_form_target(page, form))
            if not host or not target_host or target_host == host:
                ours.append((page, form))
            else:
                theirs.append((page, form))
    return ours, theirs


def _search_evidence(pages, home):
    """How this site shows it has on-site search, in its own words, or nothing.

    `has_search` comes from the extractor and finds `input[type=search]`,
    `form[role=search]` and a form whose action or text says search. Those are
    the search boxes rendered as a form in the delivered HTML, and nothing
    else, so this check declined with "the site has no on-site search, so
    SearchAction markup would describe a capability that does not exist" on
    two sites that have one: a statistics charity, whose `/search` answers 200
    and whose homepage links to it, and a software vendor, whose search runs at
    `?s=full&q=...`. A link to the search page is the site saying it has one.

    Every branch here asks whose index the query reaches, because a box that
    sends the visitor to another site's results is not this site's search
    however it is built.
    """
    host = _bare_host(home.get("url"))
    ordered = [home] + [p for p in pages if p is not home]
    ours, _ = _search_forms(ordered, host)
    if ours:
        page, form = ours[0]
        return "{} renders a search form posting to {}".format(
            page["url"], _form_target(page, form))
    # `has_search` is a single boolean over several signals, one of which is a
    # form, so a page whose only search form is somebody else's must not be
    # read through it. Where a page declares no search form at all, what the
    # extractor saw was a bare search input or a same-document link, and both
    # of those query this site by construction.
    for page in ordered:
        if page.get("has_search") and not any(
                _looks_like_a_search_form(f) for f in (page.get("forms") or [])):
            return "{} renders a search control".format(page["url"])
    for page in ordered:
        for node in page.get("jsonld") or []:
            actions = node.get("potentialAction")
            for action in (actions if isinstance(actions, list) else [actions]):
                if isinstance(action, dict) and \
                        "searchaction" in jsonld_type_names(action.get("@type")):
                    return "{} already declares a SearchAction".format(page["url"])
    # This site's own search, not a link to somebody else's: a footer link to
    # `google.com/search?q=site:...` is not on-site search, and the query-shaped
    # part of the pattern would otherwise match it.
    for page in ordered:
        for link in _links_on(page):
            url = link.get("url") or ""
            if host and _bare_host(url) != host:
                continue
            if _SEARCH_URL_RE.search(url) or _SEARCH_TEXT_RE.match(link.get("text") or ""):
                return "{} links to {}".format(page["url"], url)
    return ""


# The types this check asks the homepage for. Used only to name the type in the
# decline sentence when a broken block's kept excerpt happens to declare one -
# the decline itself does not depend on knowing which type broke.
WEBSITE_TYPES = frozenset({"website", "searchaction"})


def _homepages_declaring_a_search_action(homes):
    """The homepages carrying WebSite markup or a SearchAction on a top-level node."""
    declaring = []
    for page in homes:
        if "website" in _types_on(page):
            declaring.append(page)
            continue
        for node in page.get("jsonld") or []:
            if not isinstance(node, dict) or node.get("_nested_in"):
                continue
            actions = node.get("potentialAction")
            if any(isinstance(action, dict)
                   and "searchaction" in jsonld_type_names(action.get("@type"))
                   for action in (actions if isinstance(actions, list) else [actions])):
                declaring.append(page)
                break
    return declaring


def _check_website_searchaction(result, by_type, platform=None):
    result.check("website-searchaction-markup")
    homes = by_type.get("home", [])
    if not homes:
        result.skip("website-searchaction-markup", "the homepage was not crawled")
        return
    home = homes[0]

    # A WebSite block the homepage ships and the parser could not read is not a
    # WebSite block the homepage never wrote. On an Indian handbag shop the
    # homepage's second ld+json block held an Organization and a WebSite with a
    # potentialAction, one comma short of parsing, and this check published
    # "declares no WebSite type with a potentialAction" about it - in a report
    # that quoted that very block, two findings earlier, under the JSON-LD
    # validity check. Both the finding below and the two declines above it rest
    # on `jsonld_types` and on `_search_evidence`, and each reads the blocks
    # that parsed, so neither can say anything about a page with a parse error
    # on it. Before the search-evidence branch, not after: "no page declares a
    # SearchAction" is the same false sentence in a decline's clothing.
    _, unreadable = _set_aside_unparsed_markup([home], WEBSITE_TYPES)
    if unreadable:
        result.signal("homepage_website_markup_does_not_parse",
                      sorted("{} ({})".format(p["url"], why) for p, why in unreadable))
        result.skip("website-searchaction-markup",
                    _markup_declared_but_unreadable(unreadable, "WebSite", WEBSITE_TYPES,
                                                    "homepage", "homepages"))
        return

    everything = [p for pages in (by_type or {}).values() for p in pages]
    ordered = [home] + [p for p in everything if p is not home]
    host = _bare_host(home.get("url"))
    ours, theirs = _search_forms(ordered, host)
    evidence = _search_evidence(everything, home)
    if not evidence:
        # A box that submits somewhere else is worth saying out loud, and it is
        # the opposite of what this check used to say about it. Both sites this
        # happened to were handed a SearchAction naming a general web search
        # engine as their own endpoint - markup no consumer can use, about an
        # index the site does not own - when what is true of them is that they
        # have no on-site search at all.
        if theirs:
            page, form = theirs[0]
            result.signal("search_box_queries_another_site", page["url"])
            result.skip(
                "website-searchaction-markup",
                "the search box on {} submits to {}, which is another site's search index "
                "rather than this site's own. A SearchAction declares where this site's "
                "own search runs, so publishing that address would assert an index this "
                "site does not have; what the crawl found is a site with no on-site "
                "search".format(page["url"], _form_target(page, form)))
            return
        result.skip("website-searchaction-markup",
                    "no page renders a search form, links to a search page or declares a "
                    "SearchAction, so SearchAction markup would describe a capability this "
                    "crawl saw no sign of")
        return
    if "website" in _types_on(home):
        result.skip("website-searchaction-markup", "the homepage already declares WebSite markup")
        return
    # A SearchAction is a SearchAction whichever node carries it, and every
    # homepage is a homepage. A shop's `OnlineStore` node carries its
    # potentialAction, and a retailer's root is a country picker whose five
    # regional homepages each declare WebSite with a SearchAction - both were
    # told they "declare no WebSite type with a potentialAction", in a finding
    # whose own evidence line said the site "already declares a SearchAction".
    declaring = _homepages_declaring_a_search_action(homes)
    if declaring:
        result.skip("website-searchaction-markup",
                    "{} already {} a SearchAction on a node that describes the site itself "
                    "- WebSite, the organisation, or the store - which is what a consumer "
                    "reads it from".format(
                        ", ".join(p["url"] for p in declaring[:5]),
                        "declares" if len(declaring) == 1 else "declare"))
        return

    # What a page's own search form says, rather than a guess at it. The
    # homepage first, then any other page: the header is site-wide, and reading
    # only the homepage threw away a form the crawl had already parsed. Only a
    # form querying this site's own index is read, so the target cannot come
    # out pointing at somebody else's search engine.
    form_page, form = ours[0] if ours else (home, {})
    action = (form.get("action") or "").strip()
    field = (form.get("query_field") or "").strip()
    if action and field:
        # Resolved against the page the form is on, not the homepage: a
        # relative action means something different on `/docs/` than on `/`.
        target = "{}{}{}={{search_term_string}}".format(
            urljoin(form_page["url"], action), "&" if "?" in action else "?", field)
        read_from_page = True
    else:
        target = home["url"].rstrip("/") + "/search?q={search_term_string}"
        read_from_page = False

    # What was observed, never what the block underneath contains. The
    # sentence used to read "the snippet below uses the target and query field
    # the site's own search form declares", and it was printed above a snippet
    # reading `"target": "<target - not found on the site, fill this in>"`.
    # Nothing here was wrong about the form: the target is assembled here out
    # of the form's action and the page it sits on, so the finished URL is a
    # string no page ever showed, and the composer's last pass - which replaces
    # any snippet value the crawl never recorded, and is the reason this audit
    # does not publish invented facts - swapped it for a placeholder after this
    # sentence was written. That pass now verifies an assembled URL by its
    # parts, so a target built from a form the crawl read normally survives it.
    #
    # The sentence still may not describe the block. A check runs before that
    # pass and cannot see its outcome, and the outcome still varies: a form
    # posting to a search host the site never names is replaced, correctly.
    # Stating the two values read off the form says the same thing, stays true
    # whichever way the pass goes, and leaves the reader holding the pieces to
    # fill a placeholder in on the occasions one appears.
    if read_from_page:
        provenance = (" The search form on {} declares action \"{}\" and query field "
                      "\"{}\", which make the search target {}.".format(
                          form_page["url"], action, field, target))
    else:
        provenance = (" No search form on the crawled pages declares both an action and a "
                      "field name, so the target has to be filled in from a real search "
                      "before this is published.")
    result.add(
        id_hint="no-website-searchaction",
        title="On-site search exists but is not described in WebSite markup",
        severity="low", confidence="medium",
        evidence="This site has on-site search - {} - and {} declares no WebSite type with a "
                 "potentialAction.{}".format(evidence, home["url"], provenance),
        # Two places were read, and both are JSON-LD: the homepage's own types,
        # and `_search_evidence`, which scans `potentialAction` on every node
        # of every crawled page for a SearchAction. A site that declares its
        # search in inline Microdata is not covered by either.
        checked=("the WebSite JSON-LD type on the homepage",
                 "SearchAction potentialAction blocks in the JSON-LD on every "
                 "crawled page"),
        mechanism="C", root_cause="no-website-schema",
        summary="Add WebSite JSON-LD with a SearchAction to the homepage.",
        how_to_fix=[
            "Check the target URL against a real search: run one and compare the address."
            if read_from_page else
            "Replace the target URL with your real search URL pattern.",
            # A WebSite block describes the site, so it belongs in the template
            # every page shares rather than on one page - which is the one
            # check in this file that wants both helper sentences. The step it
            # replaces read "Add the snippet below to the homepage head" and
            # stopped there, and "the homepage head" is not a place a reader on
            # a hosted builder can find.
            *template_change_steps(platform, "the snippet below"),
        ],
        effort="low", owner="developer",
        rationale="This declares that the site has a searchable index, which lets "
                  "consumers reach content directly instead of only through crawled links.",
        affected_pages=[home["url"]],
        snippet=_snippet_block({
            "@context": "https://schema.org",
            "@type": "WebSite",
            "url": home["url"],
            "potentialAction": {
                "@type": "SearchAction",
                "target": target,
                # The name here is the token inside the braces in `target`,
                # not the HTML form's field name. Emitting the form field -
                # `required name=s` for a WordPress search - names a variable
                # the target never defines, so Google discards the whole
                # SearchAction. `references/jsonld-templates.md` had this
                # right and the code did not.
                "query-input": "required name=search_term_string",
            },
        }),
    )


# Pages whose job is to state who the site is. An Organization name that
# disagrees with one of these is a real contradiction; one that does not
# appear in a recipe is not.
_IDENTITY_PAGE_TYPES = frozenset({"home", "about", "contact"})


def _name_is_on_the_page(declared, haystack, brand_forms=()):
    """Does the page use this declared name, in any of the ways a page writes it?

    The comparison itself is `audit_common.name_appears_in`, which every check
    in the marketplace now shares. Four checks each grew their own version and
    each knew a different subset of the variation the web contains - a legal
    suffix here, a plural there, a domain suffix somewhere else - and each
    produced a false positive on the part it did not know about.

    What is local to this check is the second question: a trading name the site
    declares for itself, where the declared name contains it. A restaurant
    group's branch pages carry the group's Organization node named for the
    whole company and say the short name, and 44 of them were reported as
    structured data contradicting the page.
    """
    if name_appears_in(declared, haystack):
        return True
    declared_key = comparison_key(declared)
    for form in brand_forms:
        key = comparison_key(form)
        if not key or key == declared_key or key not in declared_key:
            continue
        if name_appears_in(form, haystack):
            return True
    return False


# How much of the page counts as "beside this item's name". A price a template
# prints for one item sits within a line or two of the name it belongs to;
# anything further away is a price for some other item on the same page.
_PRICE_NEAR_NAME_CHARS = 200

# A run of the same name is a repeated template block, not a longer page. Caps
# the scan so a catalogue naming one item two hundred times cannot turn one
# comparison into two hundred window slices.
_PRICE_NAME_HITS_MAX = 20


# Leading punctuation a template puts between a brand and a product name:
# space, hyphen, colon, pipe, comma, and the two long dashes. Built with
# chr() so this file stays ASCII and cannot lose a character in transit.
_NAME_SEPARATORS = " -:|," + chr(0x2013) + chr(0x2014)


def _item_name_forms(name, brand_forms=()):
    """The declared name, and the same name without the brand written in front.

    A shop names an Offer "<brand> Starter plan" in its markup and prints
    "Starter plan $480 per month" on the page. Searching for the declared
    string alone finds nothing, so the price the page does state for that item
    is invisible and the item drops out of the comparison entirely.
    """
    forms = []
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if len(name) < 3:
        return forms
    forms.append(name)
    for brand in brand_forms:
        brand = re.sub(r"\s+", " ", str(brand or "")).strip()
        if not brand or not name.lower().startswith(brand.lower()):
            continue
        # The separators a template puts between a brand and a product name.
        # Written with escapes, because a source file that keeps its
        # punctuation as literal characters is one copy-paste from losing it.
        shorter = name[len(brand):].lstrip(_NAME_SEPARATORS).strip()
        if len(shorter) >= 3 and shorter not in forms:
            forms.append(shorter)
    return forms


def _offer_prices(offers):
    """The numeric prices of an `offers` value, however it is written."""
    if isinstance(offers, dict):
        offers = [offers]
    found = set()
    for offer in offers if isinstance(offers, list) else []:
        if not isinstance(offer, dict):
            continue
        for key in ("price", "lowPrice", "highPrice"):
            value = _price_value(str(_prop_anywhere(offer, key) or ""))
            if value is not None:
                found.add(value)
    return found


def _the_products_offer_prices(node, priced_nodes=()):
    """Every price this one product offers: its own offers and each variant's.

    Read three ways, because the extractor keeps a `ProductGroup`'s variants
    both inside it and lifted out beside it: the node's own `offers`, the
    `offers` of each entry in its `hasVariant`, and the lifted variants that
    name it as their parent. A variant node gets its siblings the same way.
    """
    prices = _offer_prices(node.get("offers"))
    variants = node.get("hasVariant")
    for variant in ([variants] if isinstance(variants, dict) else variants or []):
        if isinstance(variant, dict):
            prices |= _offer_prices(variant.get("offers"))
    name = str(_prop(node, "name") or "")
    parent = str(node.get("_parent_name") or "") if node.get("_nested_in") == "hasVariant" else ""
    for other in priced_nodes:
        if other is node or other.get("_nested_in") != "hasVariant":
            continue
        family = str(other.get("_parent_name") or "")
        if family and family in (name, parent):
            prices |= _offer_prices(other.get("offers"))
    return prices


def _states_out_of_stock(offer):
    """Does this Offer say it cannot be bought?"""
    availability = str(_prop(offer, "availability") or "").lower() if isinstance(offer, dict) else ""
    return availability.endswith(("outofstock", "soldout", "discontinued"))


def _is_the_page_subject(node, page):
    """Is this node the thing the page is about, or one entry in a list on it?

    Nodes lifted out of a parent during extraction carry `_nested_in`, and the
    extractor's own note on that key says presence checks want them and
    agreement checks must not: a MenuItem inside a MenuSection inside a Menu is
    a catalogue entry, not a claim about the page it happens to sit on.
    """
    if node.get("_nested_in"):
        return False
    key = comparison_key(_prop(node, "name"))
    if not key:
        return False
    labels = list((page.get("headings") or {}).get("h1") or [])
    if page.get("title"):
        labels.append(page["title"])
    for label in labels:
        label_key = comparison_key(label)
        if label_key and (key == label_key or key in label_key):
            return True
    return False


# How many currency figures are read off a whole page when the question is
# "does this page print this amount anywhere". A forty-item grid prints a list
# price and a sale price per tile, so eighty is an ordinary page and the shared
# reader's own default of five is the first five of them.
_PRICES_READ_PER_PAGE = 400


def _page_prints_the_price(page, value, cache=None):
    """Does this page print this amount of money anywhere in its own text?

    The guard the whole finding was missing. A collection page printed
    `Regular price INR 18,450.00` beside an item and declared
    `content="18450.0"` for it, and the report's joint-top finding, at high
    severity, said the declared price "is not among the prices the page prints
    beside that name (500, 1000, 2000, 2001)" - four figures out of the filter
    sidebar. The markup and the page agreed exactly; only the window around the
    name had gone astray.

    Whatever the window says, a page that prints the number does not contradict
    markup carrying the same number, so the accusation cannot be made. This
    reads `body_text` alone and not the variant menus: the caller already
    treats a menu as evidence where the node is the page's subject, and a menu
    carries no name beside it, so counting one here would let any item on a
    grid be excused by any other item's variant price.
    """
    if cache is not None and page["url"] in cache:
        printed = cache[page["url"]]
    else:
        printed = {v for v in (_price_value(p) for p in find_prices(
            page.get("body_text") or "", limit=_PRICES_READ_PER_PAGE)) if v is not None}
        if cache is not None:
            cache[page["url"]] = printed
    return any(abs(value - v) < 0.01 for v in printed)


def _tile_around(text, start, end, other_names):
    """The span of `text` around `[start, end)` that no other item's name enters.

    A window of fixed width around a name is a guess about where one item's
    tile ends, and on a grid of forty items it is wrong most of the time - the
    second contributing cause of the wrong prices: the window lands in a
    neighbouring tile. The names of the other items the same page declares are
    the boundary the grid actually has, so the window is cut at the nearest one
    on each side. A price the page prints between two other items' names is
    that neighbour's price, not this item's.
    """
    lo, hi = 0, len(text)
    for name in other_names:
        for match in re.finditer(re.escape(name), text, re.I):
            if match.end() <= start:
                lo = max(lo, match.end())
            elif match.start() >= end:
                hi = min(hi, match.start())
                break
    return text[lo:hi]


def _prices_stated_for(node, page, brand_forms=(), other_names=()):
    """The prices this page prints beside this item's name. Empty means none.

    A price-agreement check only means something where the page states a price
    for the item whose markup is being read. A restaurant group's set-menu page
    lists dish names and prints no per-dish price anywhere on it, while
    `hasMenu -> hasMenuSection -> hasMenuItem -> offers.price` carries one per
    dish. Measuring those against every price printed anywhere on the page -
    the two set-menu prices - produced twelve conflicts and the site's only
    high-severity finding, about markup that contradicts nothing, because the
    page states no per-dish price for it to contradict.

    `other_names` are the names of the other items this page's own markup
    declares, and they close the window at the tile boundary; see
    `_tile_around`.
    """
    text = page.get("body_text") or ""
    if not text:
        return []
    for form in _item_name_forms(_prop(node, "name"), brand_forms):
        # A name that contains this one, or is contained by it, cannot mark a
        # boundary: a shop that sells both "Field Bag" and "Ochre Field Bag"
        # would have the shorter name cut the longer one's window at its own
        # letters, leaving nothing on either side of it.
        lowered = form.lower()
        neighbours = [n for n in other_names
                      if len(n) >= 3 and n.lower() not in lowered
                      and lowered not in n.lower()]
        found, hits = [], 0
        for match in re.finditer(re.escape(form), text, re.I):
            hits += 1
            if hits > _PRICE_NAME_HITS_MAX:
                break
            start = max(0, match.start() - _PRICE_NEAR_NAME_CHARS)
            window = text[start:match.end() + _PRICE_NEAR_NAME_CHARS]
            found.extend(find_prices(
                _tile_around(window, match.start() - start, match.end() - start,
                             neighbours),
                limit=8))
        if hits:
            return found
    return []


# --------------------------------------------------------------------------
# The name the site's own markup gives the maker of what it sells
#
# A leather-goods shop declared `"brand":{"@type":"Brand","name":"<one
# string>"}` on 12 product pages and a second string on 2 more, against a third
# string in its `Organization` block. Three spellings of one brand, in the
# site's own markup, across 14 pages - and no check read the `Brand` node at
# all, so the audit's only naming finding came from elsewhere and quoted a pair
# of strings that could not be found in any of the four sources that finding
# named.
#
# `Brand.name` is the site's own assertion about who makes what it sells.
# Disagreeing with the `Organization` block is the site contradicting itself in
# the one notation that is read as a statement of fact, and it is checkable
# from two values that are both in the snapshot.
# --------------------------------------------------------------------------

# How many pages have to carry a spelling before a difference from the
# Organization block is a template fault rather than one page's typo. Two, the
# same floor the description half of the hygiene check uses.
_BRAND_SPELLING_MINIMUM = 2


def _brand_names_in_markup(pages):
    """Every name the site's markup gives as a brand, with the page it is on.

    Both shapes real templates emit. `"brand": "<name>"` is a bare string and
    `"brand": {"@type": "Brand", "name": "<name>"}` an object; `_prop` reads
    either. The extractor also lifts a nested `Brand` out of its parent and
    records it as a node of its own, so `_nodes_of(page, {"brand"})` catches a
    site that declares the Brand somewhere this check would otherwise miss.

    Returns (name, url) pairs, deduplicated, so a catalogue page repeating one
    brand across forty items contributes it once.
    """
    found = []
    seen = set()
    for page in pages:
        url = page.get("url") or ""
        # A `brand` written as a reference - `{"@id": "<site>/#brand"}` - names
        # the node with that `@id` on the same page, and the name is on that
        # node. Read as it stood, the reference was compared as a spelling of
        # the brand: "states its brand as '<Name>' on 30 pages;
        # 'https://<site>/#brand' on 13", about a Brand node named "<Name>".
        by_id = {str(node.get("@id")): node for node in (page.get("jsonld") or [])
                 if isinstance(node, dict) and node.get("@id")}
        names = [_resolved_brand_name(node.get("brand"), by_id)
                 for node in _nodes_of(page, _BRANDED_TYPES)]
        names += [str(_prop(node, "name") or "").strip()
                  for node in _nodes_of(page, {"brand"})]
        for name in names:
            if name and (name, url) not in seen:
                seen.add((name, url))
                found.append((name, url))
    return found


def _resolved_brand_name(value, by_id):
    """The name a `brand` value gives, following an `@id` reference on the page.

    "" where the value is a reference to nothing on the page: an address is
    not a name, and reporting it as one is the fault this exists to stop.
    """
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        name = value.get("name")
        if name:
            return str(name).strip()
        value = value.get("@id") or ""
    value = str(value or "").strip()
    if value in by_id:
        return str(by_id[value].get("name") or "").strip()
    if re.match(r"^(?:https?:)?//|^#", value):
        return ""
    return value


def _spellings_of(pairs):
    """Group (name, url) pairs by the letters of the name.

    `comparison_key` strips punctuation, spacing and case, so "Acme Leathers"
    and "acme  leathers." are one spelling and not two. Anything that survives
    as a separate key is a difference a machine reading the markup would see.
    """
    groups = {}
    for name, url in pairs:
        groups.setdefault(comparison_key(name), []).append((name, url))
    return groups


# Words that name a kind of goods and never a maker. A value from this list in
# a `brand` field is a template filling the maker's field from the product
# type or the collection, not a company.
_CATEGORY_WORDS = frozenset({
    "accessories", "accessory", "apparel", "clothing", "clothes", "equipment", "equipments",
    "gear", "tools", "beans", "merchandise", "merch", "products", "product", "goods", "items",
    "default", "vendor", "brand", "unbranded", "generic", "general", "misc", "miscellaneous",
    "other", "others", "gifts", "gift", "decor", "furniture", "bags", "shoes", "footwear",
    "jewellery", "jewelry", "electronics", "books", "toys", "beauty", "skincare", "supplies",
    "parts", "spares", "sale", "new", "featured", "collection", "bundle", "bundles", "combo",
    "combos", "kit", "kits", "samples", "none", "na", "n/a", "test",
})


def _category_names_on_the_site(pages):
    """{comparison key: label} for the site's own collections and menu entries.

    Read from the labels the site puts in its menu and footer, and from the
    words of every `/collections/<slug>` or `/category/<slug>` address it links
    to. A shop's collections and product types are what a mis-mapped `brand`
    field is filled from, so this is the list such a value is found in.
    """
    names = {}
    for page in pages:
        links = page.get("links") or {}
        for where in ("nav", "footer", "internal"):
            for link in links.get(where) or []:
                if not isinstance(link, dict):
                    continue
                label = " ".join(str(link.get("text") or "").split())
                if where != "internal" and label and len(label) <= 40:
                    names.setdefault(comparison_key(label), label)
                match = re.search(r"/(?:collections|category|categories|product-category|shop)/"
                                  r"([^/?#]+)", str(link.get("url") or ""))
                if match:
                    words = " ".join(w for w in re.split(r"[-_]+", match.group(1)) if w)
                    names.setdefault(comparison_key(words), words)
                    for word in words.split():
                        if len(word) >= 4:
                            names.setdefault(comparison_key(word), word)
    names.pop("", None)
    return names


def _singular_forms(key):
    return {key, key[:-1] if key.endswith("s") else key + "s",
            key[:-2] if key.endswith("es") else key}


def _brands_that_are_categories(declared, others, own_brand_declared, pages):
    """{brand value: why} for `brand` values that are a category, not a maker.

    A coffee roaster's own products carried `"brand": "RTB"` on four pages,
    `"Beans"` on three and `"Equipments"` on two - its own product types, the
    first the initials of its "Ready to Brew" menu entry - and the check passed
    them as "brand names [that] name other companies". They name no company.

    Three readings. A word from `_CATEGORY_WORDS`. The name of one of the
    site's own collections or menu entries, on a shop whose markup names its
    own brand on other products - a multi-brand retailer's `/collections/<a
    maker>` page is a maker's name, and such a shop does not mark its own name
    as a brand. And a short capitalised code that is the initials of one of
    those entries.
    """
    labels = _category_names_on_the_site(pages)
    initials = {}
    for label in labels.values():
        words = [w for w in re.split(r"\s+", label) if w]
        if len(words) >= 2:
            initials.setdefault("".join(w[0] for w in words).upper(), label)
    found = {}
    for name in others:
        key = comparison_key(name)
        if not key:
            continue
        if _singular_forms(key) & _CATEGORY_WORDS:
            found[name] = "a word for a kind of goods rather than a maker"
            continue
        if not own_brand_declared:
            continue
        hit = next((labels[k] for k in _singular_forms(key) if k in labels), None)
        if hit:
            found[name] = 'the name of this site\'s own "{}" collection or menu entry'.format(hit)
            continue
        if re.fullmatch(r"[A-Z]{2,5}", name.strip()) and name.strip() in initials:
            found[name] = 'the initials of this site\'s own "{}" menu entry'.format(
                initials[name.strip()])
    return found


def _report_brands_that_are_categories(result, misfilled, declared, platform=None):
    result.check("brand-name-in-markup")
    pages_by_name = {}
    for name, url in declared:
        if name in misfilled:
            pages_by_name.setdefault(name, set()).add(url)
    ordered = sorted(misfilled, key=lambda n: (-len(pages_by_name.get(n, ())), n))
    result.add(
        id_hint="product-brand-is-a-category",
        title="{} a category where the maker's name belongs".format(
            plural(sum(len(pages_by_name.get(n, ())) for n in ordered),
                   "product page's markup gives", "product pages' markup gives")),
        severity="medium", confidence="high",
        evidence="The `brand` on products this site sells reads {}. None of these is a "
                 "company: each is {}.".format(
                     "; ".join('"{}" on {}'.format(n, plural(len(pages_by_name.get(n, ())),
                                                             "page")) for n in ordered[:4]),
                     "; ".join('"{}" {}'.format(n, misfilled[n]) for n in ordered[:4])),
        checked=("the `brand` property of every Product and other sellable JSON-LD node on "
                 "every crawled page, as a bare string or a Brand object",
                 "the labels of the site's own menu and footer links, and the words of every "
                 "collection or category address it links to",
                 "a closed vocabulary of words that name a kind of goods rather than a maker"),
        mechanism="C", root_cause="schema-text-mismatch",
        summary="Fill `brand` with the maker's name, not the product's category or type.",
        how_to_fix=[
            "Set `brand` on these products to the company that makes them - on the shop's own "
            "products, the shop's own brand name.",
            "The values match the site's own product types and collections, so the template is "
            "reading the wrong field into `brand`: find where the product block is written and "
            "point `brand` at the maker (the vendor field, where the platform has one).",
            who_edits_the_template(platform),
        ],
        effort="low", owner="developer",
        rationale="`brand` is read as who makes the thing. A category there tells a machine the "
                  "product is made by a company called \"Beans\", and the real maker is named "
                  "nowhere in the markup.",
        affected_pages=sorted({url for n in ordered for url in pages_by_name.get(n, ())}),
    )


def _check_declared_brand_names(result, snapshot, pages, brand, platform=None):
    """Does the site's markup give its own brand one name?

    The gate that keeps a shop selling other people's goods out of this
    finding: a declared brand is compared only when it is a spelling of a name
    the site asserts for itself. A camera shop declaring `"brand": "<a camera
    maker>"` on every product is publishing correct markup about somebody else,
    and `names_match` - which requires every word of the shorter name to appear
    in the longer - says so.
    """
    result.check("brand-name-in-markup")
    declared = _brand_names_in_markup(pages)
    if not declared:
        result.skip("brand-name-in-markup",
                    "no crawled page declares a `brand` on a product or a Brand node beside "
                    "one, so the markup states no maker for what this site sells and there "
                    "is nothing to compare against its Organization name")
        return

    node = _existing_org_node(snapshot, pages, brand)
    org_name = str(_prop(node, "name") or "").strip() if node else ""
    site_names = _names_the_site_asserts(snapshot, pages, brand)

    own = [(name, url) for name, url in declared
           if any(names_match(name, asserted) for asserted in site_names)]
    others = sorted({name for name, _ in declared} - {name for name, _ in own})
    # A category in the maker's field is not another company. See
    # `_brands_that_are_categories`.
    misfilled = _brands_that_are_categories(declared, others, bool(own), pages)
    if misfilled:
        _report_brands_that_are_categories(result, misfilled, declared, platform)
        others = [name for name in others if name not in misfilled]
        if not own or not others:
            return
    if not own:
        result.skip("brand-name-in-markup",
                    "the {} declared as a brand in this site's product markup - {} - "
                    "{} a spelling of any name the site asserts for itself, so the markup "
                    "names other companies as the makers of what it sells, which is what a "
                    "shop selling other people's goods correctly declares".format(
                        plural(len(others), "name"), ", ".join('"{}"'.format(n)
                                                               for n in others[:4]),
                        "is not" if len(others) == 1 else "is none of them"))
        return

    groups = _spellings_of(own)
    org_key = comparison_key(org_name)
    # The Organization name joins the comparison only where every brand this
    # site declares is its own. A retailer that sells other makers' goods and
    # also stocks its own house brand can legitimately have a house-brand name
    # that is not the shop's name, and reporting that as a contradiction would
    # be this check making the same unverifiable claim it exists to replace.
    disagreeing = dict(groups)
    if org_key and not others and org_key not in groups:
        disagreeing[org_key] = [(org_name, "")]

    if len(disagreeing) < 2:
        settled = own[0][0]
        if misfilled:
            return
        if others:
            result.skip("brand-name-in-markup",
                        'every product this site marks up as its own names the brand "{}" '
                        'and nothing else. The other {} declared on this site name other '
                        "companies, which is markup about somebody else and not a "
                        "disagreement about this site's own name".format(
                            settled, plural(len(others), "brand name")))
            return
        result.skip("brand-name-in-markup",
                    'the site declares its brand as "{}" on {}, and {}'.format(
                        settled, plural(len({u for _, u in own}), "crawled page"),
                        "its Organization block declares the same string"
                        if org_key else
                        "no Organization block declares a name to disagree with it"))
        return

    # A one-spelling difference against the Organization block is only a
    # template fault when the template produced it on more than one page.
    if len(groups) < 2 and len({url for _, url in own}) < _BRAND_SPELLING_MINIMUM:
        if misfilled:
            return
        result.skip("brand-name-in-markup",
                    'one crawled page declares the brand as "{}" while the Organization '
                    'block declares "{}". One page is a typo rather than a template that '
                    "writes two names, so it is not reported as one".format(
                        own[0][0], org_name))
        return

    quoted = []
    affected = set()
    for key in sorted(disagreeing, key=lambda k: (-len(disagreeing[k]), k)):
        entries = disagreeing[key]
        name = entries[0][0]
        where = sorted({url for _, url in entries})
        if key == org_key and key not in groups:
            quoted.append('"{}" on the site\'s own Organization block'.format(name))
            continue
        affected.update(where)
        quoted.append('"{}" on {} ({})'.format(
            name, plural(len(where), "crawled page"), ", ".join(example_urls(where, 2))))

    result.add(
        id_hint="markup-declares-more-than-one-brand-name",
        title="The site's markup declares {} spellings of its own brand name".format(
            len(disagreeing)),
        severity="medium", confidence="high",
        evidence="This site's structured data states its brand as {}. Every string quoted "
                 "here was read out of the markup named beside it. A consumer reading the "
                 "markup has to decide whether these are one company or {}.".format(
                     "; ".join(quoted), len(disagreeing)),
        # Three sources, all read above, and every string in the evidence line
        # comes from one of them. The finding this replaces on the naming side
        # quoted a pair of names that were in none of the sources it listed,
        # which is worse than saying nothing: a claim a reader cannot check
        # against their own site is a claim they cannot act on.
        checked=("the `brand` property of every Product, Service and other sellable JSON-LD "
                 "or Microdata node on every crawled page, read as a bare string or as a "
                 "Brand object, whichever the template emits",
                 "every Brand-typed node on those pages, including one lifted out of the "
                 "product that declares it",
                 "the `name` on the site's own Organization or LocalBusiness block - the "
                 "block that speaks for the site, never one lifted out of a Product or an "
                 "Event - and the name variants the crawl recorded, which is how a brand "
                 "belonging to another company is told apart from this site's own"),
        # `name-inconsistency` belongs to fact-extractability-audit, which
        # compares the names a site writes in prose and in its site-wide tags.
        # This is a different reading with a different fix: two values inside
        # the structured data disagreeing with each other, repaired in the
        # product template rather than in the copy.
        mechanism="C", root_cause="schema-text-mismatch",
        summary="Give the brand one spelling and emit that same string from the product "
                "template and the Organization block.",
        how_to_fix=[
            "Decide which spelling is the brand's, character for character, and set "
            "`brand.name` in the product template to it, so every product page emits the "
            "same string.",
            "Set the `name` on the Organization block to that same string where the shop "
            "and the brand are one thing.",
            "Where the shop and the brand really are two entities, declare the brand once "
            "as its own Brand node with an `@id`, and point each product's `brand` at that "
            "`@id` instead of repeating the name - one declaration cannot disagree with "
            "itself.",
            "Add the other spellings to the Organization's `alternateName`, so they are "
            "declared as the same entity rather than left to be guessed at.",
            who_edits_the_template(platform),
        ],
        effort="low", owner="developer",
        rationale="Structured data is read as the site's own assertion, so two spellings "
                  "of one brand are read as two assertions. Each halves the evidence for "
                  "the other, and a consumer cannot tell that both name one company.",
        affected_pages=sorted(affected),
    )


def _check_consistency(result, pages, brand, platform=None):
    """Markup that contradicts the visible page is worse than missing markup.

    A missing fact leaves a machine uncertain. A contradictory one teaches it
    something false with the confidence that structured data carries.
    """
    result.check("jsonld-matches-visible-text")
    # The other ways this site writes its own name, taken from the crawl's own
    # brand detection rather than guessed here.
    brand_forms = [str(v) for v in ((brand or {}).get("authoritative_variants") or [])]
    if (brand or {}).get("name"):
        brand_forms.append(str(brand["name"]))
    identity_pages = [p for p in pages if p.get("page_type") in _IDENTITY_PAGE_TYPES]
    conflicts = []
    # Pages where a comparison was actually possible: something was declared
    # and something was on the page to compare it against.
    compared = 0

    # One reading of each page's printed prices, shared by every node on it.
    printed_per_page = {}
    # Products whose first listed offer is a variant nobody can buy, while the
    # page shows another variant's price. Not a contradiction; see below.
    out_of_stock_first = []

    for page in pages:
        text = page.get("body_text") or ""

        priced_nodes = _nodes_of(page, PRODUCT_TYPES)
        # Every other item this page's markup names, used twice below: to close
        # the price window at the tile boundary, and to tell a page about one
        # thing from a page listing many.
        node_names = [str(_prop(n, "name") or "").strip() for n in priced_nodes]
        # A page that declares what it is about has a subject, and every other
        # item on it is a recommendation, a "you may also like" strip or a
        # related-products carousel. Its prices are that item's own, stated on
        # the page that sells it, and this page prints none of them.
        #
        # The finding reported price 9850.0 for one item on three unrelated
        # product URLs - one carousel's contents accused of contradicting three
        # pages that do not sell that item, at high severity.
        page_has_a_subject = any(_is_the_page_subject(n, page) for n in priced_nodes)

        for index, node in enumerate(priced_nodes):
            offer = _offer_of(node)
            if not isinstance(offer, dict):
                continue
            declared = str(_prop(offer, "price") or "").strip()
            if not declared:
                continue
            if page_has_a_subject and not _is_the_page_subject(node, page):
                continue
            # Which prices this declared price is answerable to. A product
            # detail page is about one thing, so every price printed on it is a
            # price for that thing. A page that lists many items prints each
            # item's price beside that item, and a page that prints no price
            # for an item states nothing the item's markup can contradict.
            #
            # Reading the whole page for every node is what turned a set menu
            # priced at two figures, listing dish names with no per-dish price,
            # into twelve high-severity conflicts against the per-dish prices
            # in its hasMenuItem markup.
            if _is_the_page_subject(node, page):
                # Every price on the page, not the first eight. A shop that
                # shows an upsell carousel, a size table or a "customers also
                # bought" strip above the fold pushes its own price past
                # position eight, so the correct declared price was reported as
                # contradicting the page - at high severity, with an evidence
                # line naming four other products' prices. The display is
                # capped separately, below, so nothing else changes.
                #
                # The page's form controls are read too. On a storefront the
                # variant row - size, colour, stock state and the price of
                # each - is a `<select>`, and every variant price but the
                # default one is written inside an `<option>`. That text is
                # deliberately kept out of `body_text` because forty size
                # labels are not prose, so a page declaring an Offer price of
                # 569.00 was measured against the single 609 printed beside
                # its heading and correct markup was reported at high
                # severity as contradicting the page. A visitor picking a
                # size sees the 569; the audit was accusing the site of
                # hiding a price it prints.
                visible = find_prices(
                    "{} {}".format(text, page.get("control_text") or ""), limit=120)
                where = "the prices shown on the page, including its size and variant menus"
            else:
                # No control text here, deliberately. `_prices_stated_for`
                # reads the prices printed within a window of the item's own
                # name, and a menu's labels carry no name beside them: glued
                # onto the end of the body text they would sit inside the
                # window of whichever item's name happens to fall last on the
                # page, and one item on a grid would be graded against
                # another item's variant prices.
                visible = _prices_stated_for(
                    node, page, brand_forms,
                    [n for position, n in enumerate(node_names)
                     if n and position != index])
                where = "the prices the page prints beside that name"
            if not visible:
                continue
            declared_number = _price_value(declared)
            visible_numbers = {v for v in (_price_value(p) for p in visible) if v is not None}
            if declared_number is not None and visible_numbers:
                compared += 1
            # Compare numerically: "480.00" in markup and "$480" on the page are
            # the same price, and a string comparison would call them a conflict.
            if declared_number is not None and visible_numbers and not any(
                    abs(declared_number - v) < 0.01 for v in visible_numbers):
                # The window disagrees; the page itself may not. Where the page
                # prints the declared number anywhere in its own text, the
                # window landed in the wrong tile and there is no contradiction
                # to report. See `_page_prints_the_price`: the finding this
                # guard removes was one site's joint-top item, at high severity,
                # about a price printed in full beside the item it belongs to.
                if _page_prints_the_price(page, declared_number, printed_per_page):
                    continue
                # One product, several offers: a variant per size. A page shows
                # the default variant's price and the menu holds the rest, so a
                # page showing any of them agrees with the markup. A cooking
                # pot's block listed Small (2720, out of stock), Medium (3000)
                # and Large (2600); the page showed "Rs. 3,000.00", the first
                # listed offer was read as the product's price, and the report
                # said at high severity that the markup contradicts the page.
                family = _the_products_offer_prices(node, priced_nodes)
                shown = sorted(v for v in family if any(abs(v - s) < 0.01 for s in visible_numbers)
                               or _page_prints_the_price(page, v, printed_per_page))
                if shown:
                    if _states_out_of_stock(offer):
                        out_of_stock_first.append((page, str(_prop(node, "name") or ""),
                                                   declared, shown[0]))
                    continue
                conflicts.append((page, 'Offer price {} for "{}" is not among {} ({})'.format(
                    declared, truncate(str(_prop(node, "name") or ""), 60), where,
                    ", ".join(_format_price(v) for v in sorted(visible_numbers)[:4]))))

        # Site-wide Organization markup is chrome, not a claim about this
        # page. A restaurant group ships one Organization node in its template,
        # so every recipe and every journal post carries the group's legal
        # name - and requiring that full legal name to appear in the
        # text of a chutney recipe produced 33 conflicts, the report's only
        # high-severity finding, about a site doing nothing wrong.
        #
        # The question the check exists to answer is whether the site's own
        # identity markup contradicts the pages that state that identity. Those
        # are the home, about and contact pages. Where the crawl reached none
        # of them there is nothing better to compare against, so every page is
        # used and the finding says so.
        if identity_pages and page.get("page_type") not in _IDENTITY_PAGE_TYPES:
            continue

        for node in _nodes_of(page, ORG_IDENTITY_TYPES):
            # Top-level declarations only. An `Article.publisher` names who
            # published the piece, not what the page is about, so requiring the
            # publisher's name to appear in the article's own text reported
            # every correctly marked-up blog post on a site as "structured data
            # contradicts what the page actually says" - at high severity, and
            # about markup that is right.
            if node.get("_nested_in"):
                continue
            declared = str(_prop(node, "name") or "").strip()
            if not declared or len(declared) < 3:
                continue
            # The whole page, not the first 4,000 characters. A restaurant
            # group's declared name sat at character 9,336 of its homepage, so
            # the check reported that no form of the name appears "in the page
            # title, headings or body text" while naming a place it had only
            # partly read. A name is short and `name_appears_in` is a word
            # test; there is nothing to save by truncating.
            haystack = "{} {} {}".format(page.get("title") or "", text,
                                         " ".join((page.get("headings") or {}).get("h1") or []))
            # A Latin-script name cannot appear in an Arabic page, and its
            # absence there says nothing about the markup. One foundation's
            # Arabic edition was told at high severity that its structured
            # data contradicted the page, about markup that was correct and a
            # page that was correct.
            if not written_in_the_same_script(declared, haystack):
                continue
            compared += 1
            # A formal name in markup and a trading name in the prose is normal
            # and correct. The conflict is when *no* form of the declared name
            # appears, not when the legal suffix is missing from the sentence.
            if not _name_is_on_the_page(declared, haystack, brand_forms):
                conflicts.append((page, 'no form of the Organization name "{}" appears in the '
                                        'page title, headings or body text, and neither does '
                                        'every word of it'.format(declared)))

    if out_of_stock_first:
        page, name, declared, shown = out_of_stock_first[0]
        result.add(
            id_hint="first-offer-is-out-of-stock",
            title="{} an out-of-stock variant as the first offer".format(
                plural(len(out_of_stock_first), "product's markup lists",
                       "products' markup lists")),
            # Low: the markup and the page agree - the shown price is one of
            # the product's offers. What is off is the order, and a reader
            # that takes the first offer as the price takes one nobody can buy.
            severity="low", confidence="high",
            evidence='Example: {} - the first offer in the markup for "{}" is {}, marked '
                     "OutOfStock, while the page shows {}, which is another of the same "
                     "product's offers.".format(page["url"], truncate(name, 60), declared,
                                                _format_price(shown)),
            checked=("the `availability` of the first offer in the product's block",
                     "every other offer of the same product - its own `offers` list and each "
                     "variant's under `hasVariant` - against the prices the page shows"),
            mechanism="C", root_cause="schema-text-mismatch",
            summary="List the variant the page shows first, or mark the in-stock offer as the "
                    "product's price.",
            how_to_fix=[
                "Order the variant offers so the one the page selects by default comes first.",
                "Or give the product a top-level `offers` for the default variant, and keep "
                "the per-variant offers under `hasVariant`.",
                who_edits_the_template(platform),
            ],
            effort="low", owner="developer",
            rationale="A consumer reading one price from a block reads the first. When that is "
                      "an out-of-stock variant, the price it repeats is one nobody can pay.",
            affected_pages=sorted({p["url"] for p, _, _, _ in out_of_stock_first}),
        )

    if not conflicts:
        if out_of_stock_first:
            return
        # A pass over nothing is not a pass. Four sites in one batch carry no
        # JSON-LD at all - a library system, a podcast, a storefront and a
        # research society - and each was told "the names and prices declared
        # in JSON-LD match what the pages display", in the same report whose
        # top finding was that the site declares no JSON-LD anywhere.
        if not compared:
            result.skip("jsonld-matches-visible-text",
                        "no crawled page declares a name or a price in JSON-LD, so there was "
                        "nothing to compare against the visible text. This is not a pass: it "
                        "is the absence of the markup that would be checked")
            return
        result.skip("jsonld-matches-visible-text",
                    "the names and prices declared in JSON-LD match what the pages display, "
                    "on the {}".format(plural(compared, "crawled page that declares one",
                                              "crawled pages that declare one")))
        return

    result.add(
        id_hint="jsonld-contradicts-visible-text",
        title="Structured data contradicts what the page actually says",
        severity="high", confidence="medium",
        evidence="{} conflict(s) found. Examples: {}.".format(
            len(conflicts),
            "; ".join("{}: {}".format(p["url"], why)
                      for p, why in sorted(conflicts, key=lambda x: x[0]["url"])[:4])),
        checked=("the whole visible text of each page, its title and its h1, for any form of "
                 "the declared name including a plural, a legal suffix or a trading name",
                 "the prices the page prints, compared as numbers rather than as strings, "
                 "and beside the item's own name where the node is not the page's subject - "
                 "cut short at the next item's name on either side, because that is where "
                 "one tile of a grid ends",
                 "the whole of each page's text for the declared figure itself, so a page "
                 "that prints the number anywhere is never named here, whatever the text "
                 "beside the name said",
                 "the labels inside the page's own size and variant menus, where the node is "
                 "the thing the page is about, because a storefront writes every variant "
                 "price but the default one inside an <option>"),
        mechanism="C", root_cause="schema-text-mismatch",
        summary="Bind the structured data to the same source as the visible content so the two "
                "cannot drift apart.",
        how_to_fix=[
            "For each conflict, decide which value is correct and correct the other.",
            "In the template, populate the JSON-LD from the same variables that render the "
            "visible price and name; hand-written markup drifts within weeks.",
            # The step used to end "use `highPrice`/`lowPrice` on an
            # AggregateOffer for ranges", which is advice for a Product sold at
            # several prices and wrong for most of the types this finding
            # reaches. A dish priced individually carries its own Offer on the
            # MenuItem; a set menu priced as a whole carries one Offer on the
            # menu. Neither is a range, and following the old step would have
            # replaced correct per-item prices with an invented span.
            # Not "the one a buyer would pay": this finding compares any
            # declared value against the page, and it reaches a museum's
            # admission and a course fee as readily as a shop's price.
            "Where a page shows several prices (a sale price beside a list price, a per-unit "
            "price beside a pack price), declare the one that is actually charged.",
            "Attach each price to the thing it is a price for: the item's own Offer for an "
            "item sold on its own, one Offer on the parent for a bundle or set priced as a "
            "whole, and `lowPrice`/`highPrice` on an AggregateOffer only where the page really "
            "does state a range.",
            who_edits_the_template(platform),
        ],
        effort="medium", owner="developer",
        rationale="Structured data is treated as an assertion by the site owner. "
                  "A wrong assertion is repeated confidently, so a contradiction does more "
                  "damage than an omission.",
        affected_pages=[p["url"] for p, _ in conflicts],
    )


def _title_width(title):
    """How much this title says, in characters of a space-separated script.

    The same number as `len(title)` for anything written in Latin, Greek,
    Cyrillic or any other script that puts spaces between words, and the
    reason a title in Japanese, Chinese or Thai is now compared against the
    same range as one in English rather than against a rule that could only
    ever fail it. See `UNSPACED_CHARACTER_WIDTH`.
    """
    text = title or ""
    counts = script_counts(text)
    unspaced = sum(n for script, n in counts.items() if script in UNSPACED_SCRIPTS)
    return len(text) - unspaced + unspaced * UNSPACED_CHARACTER_WIDTH


# Printed wherever the range is quoted, so a reader whose titles are in an
# unspaced script is not left comparing a nine-character title against "15" and
# concluding the audit cannot read their site.
_TITLE_RANGE_NOTE = ("a character in a script that writes without spaces, such as "
                     "Japanese, counts as {} because it carries that much more"
                     .format(UNSPACED_CHARACTER_WIDTH))


# A title that opens with a written-out web address. A scheme, or the `www.`
# that people write in place of one, is the page saying "this is an address"
# in the one place it is supposed to say what the page is.
#
# A bare hostname is deliberately not here. `<shop>.<invented>.test - Boots` is
# a site writing its domain as its name, which is uninformative and is not this
# defect: it names no page, so no page variable went missing to produce it, and
# a pattern loose enough to catch it also catches ordinary titles that begin
# with a dotted word.
_ADDRESS_TITLE_RE = re.compile(r"^\s*(?:https?://|www\.)\S+")

# How much of the title the address has to be before the title is an address
# and nothing else. Anchoring at the start is not enough on its own:
# `<address> - Blue Boot, Invented Shop` still tells a reader what the page is,
# and reporting it beside a title that is only an address would make the
# finding's own sentence false for half the pages it names. Below this share
# the title carries a name as well, which is a cosmetic fault and a different
# one.
_ADDRESS_TITLE_SHARE = 0.6


def _title_is_an_address(title):
    """Is this <title> the page's own address rather than a name for the page?

    A template that never substituted its page variable, which every other
    test in this check passes over: the title is present, is not short, is not
    long, and is unique precisely because each page's address differs. An
    assistant asked what the page is gets the address it already had.

    Measured in `_title_width` on both sides so the share means the same thing
    for a title whose remaining words are Japanese, where seven characters
    carry as much as twenty do in English.
    """
    text = (title or "").strip()
    match = _ADDRESS_TITLE_RE.match(text)
    if not match:
        return False
    return _title_width(match.group(0).strip()) >= _title_width(text) * _ADDRESS_TITLE_SHARE


# --------------------------------------------------------------------------
# A homepage titled after a promotion
#
# A candle shop's front page was titled "Warehouse Sale | <brand>". That string
# is what an assistant reads as what the front page of the business is, and it
# names a promotion that ends rather than the business that stays. The audit
# said nothing about it: the word "Warehouse" appeared 0 times in the 70 KB
# report.
#
# Three title tests already existed and none of them asks this. Length asks how
# much a title says; duplication asks whether two pages share one; and
# `_name_is_really_a_page_title` asks the opposite question, whether a declared
# `name` is a title. The homepage title is the one title with a fixed job - it
# has to say what the site is - and nothing was asking whether it does.
# --------------------------------------------------------------------------

# Where a theme divides a title into parts. Wider than `_TITLE_SEPARATORS`,
# which answers a different question - which characters never occur inside an
# organisation's own name - and so leaves out the colon, the bullet and the
# dashes that a title is just as often split on.
#
# The dash forms require whitespace on both sides so a hyphenated name stays
# one segment: "Well-Made Candles" is one part of a title, not two.
_TITLE_PART_RE = re.compile(
    "[" + re.escape(_TITLE_SEPARATORS + ":" + chr(0x2022) + chr(0x00B7)) + "]"
    + "|\\s+[-" + chr(0x2013) + chr(0x2014) + "]\\s+")

# A promotion, a campaign, or a site saying it is not trading yet. Every entry
# names a state that ends, which is what separates it from a descriptor: a shop
# is still a candle shop next month and is not still having a warehouse sale.
#
# `sale` carries a lookbehind because "for sale" is what an estate agent and a
# used-car dealer write as an ordinary description of the business, and it is
# the one common phrase in which the word is permanent. `\b` keeps "wholesale"
# and "sales" out on its own.
#
# The list is English plus the single unambiguous word for a clearance sale in
# six other European languages. It is deliberately not a general translation:
# a word short enough or common enough to collide across languages would fire
# this finding on titles that are correct, and `checked[]` below states the
# limit so a reader whose promotion is named in another language knows this
# check did not look for it.
_EVENT_TITLE_RE = re.compile(
    "\\b(?:"
    # The site says it is not open, not finished, or not trading.
    "coming soon|opening soon|launching soon|back soon|"
    "under construction|under maintenance|under development|"
    "closing down|closing sale|temporarily closed|grand opening|"
    # A promotion, which has an end date.
    "clearance|black friday|cyber monday|boxing day|singles day|"
    "(?<!for )sale|discounts?|\\d+ ?% ?off|"
    "free shipping|buy one get one|limited time|last chance|"
    # The same word in the languages whose spelling of it cannot be mistaken
    # for anything else: Spanish, French, German, Dutch, Portuguese, Italian
    # and Norwegian.
    "rebajas|soldes|ausverkauf|uitverkoop|liquidação|saldi|utsalg"
    ")\\b", re.I | re.U)

# How long the leading segment may be before it stops being a label. A campaign
# banner is short - "Warehouse Sale", "Coming Soon", "50% Off Everything" - and
# a longer opening segment is a sentence about the business that happens to
# contain one of the words above, such as a shop explaining what it sells.
_EVENT_SEGMENT_MAX_WORDS = 6


def _leading_title_segment(title):
    """The first part of a title, up to whatever the theme divides it with."""
    parts = [part.strip() for part in _TITLE_PART_RE.split(title or "")]
    parts = [part for part in parts if part]
    return parts[0] if parts else (title or "").strip()


def _homepage_title_names_an_event(title, site_names=()):
    """The promotion this homepage <title> leads with, or "".

    The leading segment is what is graded, because that is where the answer to
    "what is this site" belongs and it is what a truncated title leaves behind.

    The guard on the other side matters as much as the test. "<brand> -
    handmade candles" is a correct and extremely common homepage title: a name
    followed by a genuine descriptor. What separates it from a promotion is
    that its leading segment is the business, so a leading segment carrying any
    form of a name the site declares for itself is never reported here,
    whatever else is in it. `site_names` holds the names the site asserts - the
    Organization `name`, og:site_name and the variants the crawl recorded - and
    never a name guessed from a title, which would make the guard circular.
    """
    text = (title or "").strip()
    if not text:
        return ""
    lead = _leading_title_segment(text)
    if not lead:
        return ""
    if len([w for w in re.findall(r"\w+", lead, re.U) if w]) > _EVENT_SEGMENT_MAX_WORDS:
        return ""
    for name in site_names:
        if name and name_appears_in(name, lead):
            return ""
    match = _EVENT_TITLE_RE.search(lead)
    return match.group(0).strip() if match else ""


def _names_the_site_asserts(snapshot, pages, brand):
    """The names the site states for itself, in the places it states them.

    Declared names only. A name split out of a page title is a guess, and using
    one here would let a homepage title excuse itself: "Warehouse Sale" read as
    a name would match the segment it was taken from.
    """
    names = []
    node = _existing_org_node(snapshot, pages, brand)
    declared = str(_prop(node, "name") or "").strip() if node else ""
    if declared:
        names.append(declared)
    if (brand or {}).get("name"):
        names.append(str(brand["name"]))
    for key in ("authoritative_variants", "alternate_names"):
        names += [str(v) for v in ((brand or {}).get(key) or []) if v]
    for page in pages:
        value = str((page.get("og") or {}).get("og:site_name") or "").strip()
        if value:
            names.append(value)
    return [n for n in dict.fromkeys(names) if n]


def _check_homepage_title(result, snapshot, pages, brand, platform=None):
    """Does the front door's <title> say what the business is?

    Its own function rather than a line inside the hygiene check below, because
    `SkillResult` stamps a finding with every check its own function
    registered: registered alongside `title-and-description`, this question
    would be marked answered every time the hygiene finding fired, on sites
    where the homepage title was never examined.
    """
    result.check("homepage-title")
    home = next((p for p in pages if _is_the_homepage(p, snapshot)), None)
    if home is None:
        result.skip("homepage-title",
                    "the crawl reached no homepage, so there is no front-door title to read")
        return
    title = str(home.get("title") or "").strip()
    if not title:
        result.skip("homepage-title",
                    "the homepage carries no <title> at all, which the title and description "
                    "check reports rather than this one")
        return
    site_names = _names_the_site_asserts(snapshot, pages, brand)
    event = _homepage_title_names_an_event(title, site_names)
    if not event:
        result.skip("homepage-title",
                    'the homepage is titled "{}", which does not lead with a promotion, a '
                    "campaign or a not-open-yet notice".format(truncate(title, 60)))
        return

    lead = _leading_title_segment(title)
    result.add(
        id_hint="homepage-title-names-a-promotion",
        title="The homepage is titled after a promotion rather than the business",
        # Medium, not high. Nothing is broken - the page serves, the title
        # parses - and the cost is that the one string naming the business
        # names something else. It is also a minute's work, which is why it is
        # worth saying at all.
        severity="medium", confidence="high",
        evidence='{} is titled "{}". The part before the first separator is "{}", which '
                 'names a promotion ("{}") rather than the business. That leading segment '
                 "is what an assistant reads as what the front page of this site is, and it "
                 "is what survives when the title is truncated - so the site's front door "
                 "is described by something that ends on a date.".format(
                     home["url"], truncate(title, 90), truncate(lead, 60), event),
        checked=("the <title> of the homepage, split at the characters a theme puts between "
                 "a title's parts - the vertical line and its fullwidth form, the "
                 "guillemets, the colon, the bullet and a dash with spaces around it - so "
                 "the leading segment is the part graded",
                 "that leading segment against every name the site asserts for itself: the "
                 "`name` on its Organization block, og:site_name, and the name variants the "
                 "crawl recorded. A leading segment carrying any form of one of those is "
                 "the business naming itself and is never reported here",
                 "the same segment against a list of English words for a promotion or a "
                 "not-trading-yet state - sale, clearance, discount, N% off, free shipping, "
                 "coming soon, under construction and their neighbours - plus the word for "
                 "a clearance sale in six other European languages. A promotion named in "
                 "any other language is not caught by this check"),
        mechanism="B", root_cause="meta-hygiene",
        summary="Title the homepage after the business and what it sells, and leave the "
                "promotion to the page itself.",
        how_to_fix=[
            # The name is the site's own where it declares one, and an honest
            # blank where it does not. The same rule the language fix follows:
            # an example value in a fix step is read off this site or it is not
            # printed.
            'Set the homepage <title> to "{} - <what you sell, in three or four words>". '
            "The business name leads, and a descriptor after it is correct and normal; a "
            "campaign name in that position is not.".format(
                site_names[0] if site_names else "<the business name>"),
            "Announce the promotion on the page - the banner, an H1, a link - where it can "
            "be taken down the day it ends. The <title> outlives it: every index, cache and "
            "assistant that read the page keeps the string it was given.",
            "Where the theme fills the homepage title from an announcement or campaign "
            "setting, set the homepage's own title field instead, so the next campaign does "
            "not overwrite it.",
            who_edits_the_template(platform),
        ],
        effort="low", owner="content owner",
        rationale="The homepage <title> is the shortest statement a site makes of what it "
                  "is, and it is quoted directly. Named after a promotion, it answers "
                  "\"what is this business\" with an event that ends, and every consumer "
                  "that stored the answer keeps it after the sale is over.",
        affected_pages=[home["url"]],
    )


# A string with no letter and no digit in it. `##` is a meta description
# found on a live site: present, unique, not a title, and therefore
# invisible to a check that tests presence, duplication and length. So is `-`,
# so is `|`, so is `...`. A description exists to state the page's fact, and a
# string with no word in it states nothing.
_A_WORD_RE = re.compile(r"[^\W\d_]|\d", re.UNICODE)


def _says_nothing(value):
    """Is this tag's whole content punctuation?"""
    text = str(value or "").strip()
    return bool(text) and not _A_WORD_RE.search(text)


def _escaped_twice(value):
    """Does this parsed attribute value still carry a character reference?

    An HTML parser resolves `&amp;` in a `content` attribute to `&`, so a
    correctly escaped value arrives here clean. A value that arrives still
    holding `&amp;` or `&#39;` was escaped twice - the template ran an
    already-escaped string through its escape filter again - and a search
    result prints those five characters at a reader.

    The same fault, and the same one-line fix, as
    `jsonld-values-escaped-as-html` reports inside `<script>`. On one site it
    was reported in one place and not the other.
    """
    return isinstance(value, str) and bool(_CHARACTER_REFERENCE_RE.search(value))


def _edition_keys(pages):
    """{id(page): the page it is an edition of}, for regional and language editions.

    Two readings. The same path under different edition prefixes - `/au/x`,
    `/sg/x` - is one page in two editions, and so is a page and the addresses
    its `hreflang` alternates name.
    """
    parent = {}

    def root(key):
        while parent.setdefault(key, key) != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def join(a, b):
        parent[root(a)] = root(b)

    keys = {}
    for page in pages:
        parsed = urlparse(str(page.get("final_url") or page.get("url") or ""))
        segments = [s for s in parsed.path.split("/") if s]
        if segments and _EDITION_SEGMENT_RE.match(segments[0]):
            segments = segments[1:]
        key = (parsed.netloc.lower(), "/".join(segments), parsed.query)
        keys[id(page)] = key
        root(key)
        for alternate in page.get("hreflang") or []:
            href = alternate.get("href") if isinstance(alternate, dict) else alternate
            if href:
                other = urlparse(str(href))
                other_segments = [s for s in other.path.split("/") if s]
                if other_segments and _EDITION_SEGMENT_RE.match(other_segments[0]):
                    other_segments = other_segments[1:]
                join((other.netloc.lower(), "/".join(other_segments), other.query), key)
    return {page_id: root(key) for page_id, key in keys.items()}


def _shared_across_pages(pages, field, value, editions):
    """Do pages that are not editions of one page carry this value?"""
    return len({editions.get(id(p)) for p in pages if p.get(field) == value}) > 1


# The words a page carries in the editor and not in public: a landing-page tag,
# a draft marker, a copy the editor made. Strong ones make a title a working
# label beside one word of its own; weak ones only on their own.
_WORKING_LABEL_STRONG = frozenset({"lp", "draft", "untitled", "wip", "tbd", "placeholder",
                                   "lorem", "ipsum", "copy"})
_WORKING_LABEL_WEAK = frozenset({"test", "testing", "temp", "staging", "dummy"})
_TITLE_FILLER_WORDS = frozenset({"app", "page", "new", "home", "landing", "site", "web",
                                 "mobile", "the", "a", "of", "and", "final", "version"})


def _title_is_a_working_label(title):
    """Is this <title> the name a page had in the editor, like "App LP - 2023"?

    A coffee roaster's application landing page publishes "App LP - 2023" as
    both its `<title>` and its og:title, and the title check passed it. A
    landing-page tag, a year and one generic word is not a name for a reader.
    Read as words: a strong marker beside at most one word of the page's own,
    or a weak marker with none - so "Draft Beer Menu" and "Test Kitchen
    Recipes" are titles.
    """
    words = re.findall(r"[^\W\d_]+", str(title or "").lower())
    if not words:
        return False
    strong = [w for w in words if w in _WORKING_LABEL_STRONG]
    weak = [w for w in words if w in _WORKING_LABEL_WEAK]
    own = [w for w in words if w not in _WORKING_LABEL_STRONG | _WORKING_LABEL_WEAK
           | _TITLE_FILLER_WORDS]
    return bool((strong and len(own) <= 1) or (weak and not own))


def _check_titles_and_descriptions(result, pages, crawled=None):
    result.check("title-and-description")
    # Every page the crawl read, before the folds below take out the copies.
    # The folds shrink the denominator, and a report whose other checks count
    # every page read printed "54 of the 58 crawled pages share a meta
    # description" a few lines above "10 of the 59 crawled pages have no
    # top-level heading" - on a programming-language foundation's site, where
    # two user-group event pages were near-identical and one was folded. Two sizes of one crawl read as a contradiction unless each
    # says which pages it counted.
    read = list(pages)
    # A URL and its own declared canonical target are one page, and the site is
    # the authority on that. Counting both reported two of eleven "duplicate
    # title" pairs on a broadcaster that were a URL and its canonical, and
    # inflated the denominator underneath them at the same time.
    pages = fold_declared_duplicates(pages)
    # And a site that declares no canonical at all is the case the fold above
    # cannot reach, which is exactly the site this counting hurts most. A
    # documentation tree publishing every version at its own path was told "60
    # of 60 pages share a <title> with another page (29 distinct titles
    # reused)", owner "content owner", effort "half a day to a day" - and 29 of
    # the 30 collisions were one page counted twice. Two addresses that deliver
    # the same page are one page here, whatever the canonical says or fails to
    # say; the duplication itself is the canonical check's finding.
    pages = _fold_identical_pages(pages)
    # Above the counting, because the counting has a pass branch that
    # returns. A site whose titles and descriptions are all present,
    # unique and the right length can still have every one of them run
    # through the escape filter twice, and that site is exactly the one
    # this reading is for - it was found on a shop with no other meta
    # fault at all.
    _report_meta_escaped_twice(result, pages)
    missing_title = [p for p in pages if not p.get("title")]
    # Two readings of one question. `<meta name="description">` is the tag this
    # finding is about, and `og:description` states the same fact independently
    # and is already in the snapshot. A page carrying one and not the other has
    # a description; only a page carrying neither has none, and claiming
    # otherwise is an absence asserted on a single detector - the shape of every
    # false positive this marketplace has made.
    missing_desc = [p for p in pages if not p.get("meta_description")
                    and not (p.get("og") or {}).get("og:description")]
    og_only = [p for p in pages if not p.get("meta_description")
               and (p.get("og") or {}).get("og:description")]

    titles = Counter(p["title"] for p in pages if p.get("title"))
    descriptions = Counter(p["meta_description"] for p in pages if p.get("meta_description"))
    # Editions of one page are one page here. A retailer publishes its story
    # page at `/au/our-story`, `/ca/our-story`, `/sg/our-story` and
    # `/uk/our-story` with one description, and was told "17 of 38 pages share a
    # meta description with another page" - within any one region the
    # descriptions differ. A value repeated only across the editions of one
    # page is that page's value. See `_edition_keys`.
    editions = _edition_keys(pages)
    duplicate_titles = [t for t, n in titles.items()
                        if n > 1 and _shared_across_pages(pages, "title", t, editions)]
    duplicate_descriptions = [d for d, n in descriptions.items()
                              if n > 1 and _shared_across_pages(pages, "meta_description", d,
                                                                editions)]
    result.signal("regional_editions_folded",
                  len(pages) - len(set(editions.values())) if editions else 0)
    # A working label published as the page's title: "App LP - 2023". See
    # `_title_is_a_working_label`.
    working_titles = [p for p in pages if _title_is_a_working_label(p.get("title"))]

    problems = []
    if missing_title:
        problems.append("{} page(s) have no <title>".format(len(missing_title)))
    # A page with neither, and a page with only Open Graph, are both missing
    # the meta description - they differ in how badly.
    #
    # Treating `og:description` as a substitute went too far the other way: a
    # site that removed every meta description and kept its Open Graph tags
    # produced no finding at all, and that site really has lost the tag search
    # engines read for a snippet. Open Graph describes the page to a sharing
    # surface; the meta description describes it to a result page. They are the
    # same sentence serving two consumers, so one covering for the other lowers
    # the severity and never removes the fact.
    # One page without a description is an oversight, not a template fault.
    undescribed = missing_desc + og_only
    # Whether the missing descriptions are reported at all. Below the line they
    # are neither a sentence in the evidence nor a page in the affected list
    # nor a reason for `medium`: a coffee roaster with one working-label title
    # was listed with nine undescribed collection pages the evidence never
    # mentioned, and the finding took its severity from them.
    undescribed_reported = len(undescribed) >= max(2, len(pages) * 0.25)
    if undescribed_reported:
        if missing_desc:
            problems.append("{} of {} page(s) have no meta description and no og:description "
                            "either".format(len(missing_desc), len(pages)))
        if og_only:
            problems.append("{} of {} page(s) have no meta description, though they do carry "
                            "an og:description saying the same thing".format(
                                len(og_only), len(pages)))
    # Present, unique, and saying nothing. Counted with the missing ones in
    # `affected` below, because the fix is the same sentence.
    wordless = [p for p in pages
                if _says_nothing(p.get("meta_description"))
                or _says_nothing(p.get("title"))]
    if wordless:
        example = (wordless[0].get("meta_description")
                   if _says_nothing(wordless[0].get("meta_description"))
                   else wordless[0].get("title"))
        problems.append(
            "{} of {} page(s) carry a title or meta description with no word in it at all, "
            "such as \"{}\" on {}".format(
                len(wordless), len(pages), truncate(str(example).strip(), 30),
                wordless[0]["url"]))

    if duplicate_titles:
        # Count the pages affected, not the number of repeated strings. "2
        # titles are reused" on a seven-page site where six pages share a title
        # with another reads as "two pages have a problem", which is wrong by a
        # factor of three - and it is the number a content owner budgets from.
        # Name the worst offender too: "faq.html is titled About" is a
        # thirty-second fix that was invisible inside the aggregate.
        affected = [p for p in pages if p.get("title") in duplicate_titles]
        worst = max(duplicate_titles, key=lambda x: titles[x])
        sharing = sorted(p["url"] for p in pages if p.get("title") == worst)
        problems.append(
            "{} of {} page(s) share a <title> with another page ({} distinct title(s) reused); "
            "the most repeated is \"{}\" on {}".format(
                len(affected), len(pages), len(duplicate_titles), truncate(worst, 60),
                ", ".join(sharing[:4])))
    if duplicate_descriptions:
        # Pages first, the same way the title half above counts them, and the
        # worst offender named. "1 meta description(s) are reused" was printed
        # about a browser-compatibility database where 57 of 60 pages carry the
        # identical description: the title half of the same sentence printed a
        # page count and the description half printed a count of distinct
        # repeated strings, so a reader saw "1" and moved on. It is also why the
        # composer suppressed the per-page list underneath as disagreeing with
        # the number above it.
        sharing_desc = sorted(p["url"] for p in pages
                              if p.get("meta_description") in duplicate_descriptions)
        worst_desc = max(duplicate_descriptions, key=lambda d: descriptions[d])
        problems.append(
            "{} of {} page(s) share a meta description with another page ({} distinct "
            "description(s) reused); the most repeated is on {} of them and reads \"{}\""
            .format(len(sharing_desc), len(pages), len(duplicate_descriptions),
                    descriptions[worst_desc], truncate(worst_desc, 90)))

    # Length is only worth mentioning when it is a template-wide habit. One or
    # two titles a few characters over the ideal range is not a defect, and
    # reporting it makes the whole report look like a checklist.
    long_titles = [p for p in pages if p.get("title") and _title_width(p["title"]) > TITLE_MAX]
    short_titles = [p for p in pages if p.get("title") and _title_width(p["title"]) < TITLE_MIN]
    # Reported from the first page, not as a habit like length below. One title
    # that is its own address is already a template that failed, and the pages
    # it fails on are whichever ones the crawl happened to reach.
    url_titles = [p for p in pages if _title_is_an_address(p.get("title"))]
    if url_titles:
        problems.append(
            "{} of {} page(s) have a <title> that is the page's own address rather than a "
            "name for the page, such as \"{}\" on {}".format(
                len(url_titles), len(pages), truncate(url_titles[0]["title"], 60),
                url_titles[0]["url"]))
    if working_titles:
        first = working_titles[0]
        problems.append(
            "{} of {} page(s) publish a working label as their <title>{}, such as \"{}\" on "
            "{} - the name a page had in the editor, not a name for a reader".format(
                len(working_titles), len(pages),
                " and og:title" if all((p.get("og") or {}).get("og:title") == p.get("title")
                                       for p in working_titles) else "",
                truncate(first["title"], 60), first["url"]))
    address_titles = list(url_titles)
    url_titles = url_titles + [p for p in working_titles if p not in url_titles]
    off_length = long_titles + short_titles
    length_reported = len(off_length) >= max(4, len(pages) * 0.6)
    if length_reported:
        problems.append("{} of {} title(s) fall outside {}-{} characters ({})".format(
            len(off_length), len(pages), TITLE_MIN, TITLE_MAX, _TITLE_RANGE_NOTE))

    # The title names the fault that reaches the most pages, with its count.
    # "Page titles and meta descriptions need attention" was the same sentence
    # on eleven of twelve sites audited together, whether 59 of 59 pages had no
    # description or two pages shared one, and a reader could not tell which
    # from the heading.
    headlines = []
    if missing_title:
        headlines.append((len(missing_title), "have no <title>"))
    if undescribed_reported and missing_desc:
        headlines.append((len(missing_desc), "have no meta description"))
    elif undescribed_reported:
        headlines.append((len(og_only), "have a description only in their Open Graph tags"))
    if duplicate_titles:
        headlines.append((len([p for p in pages if p.get("title") in duplicate_titles]),
                          "share a <title> with another page"))
    if duplicate_descriptions:
        headlines.append((len([p for p in pages
                               if p.get("meta_description") in duplicate_descriptions]),
                          "share a meta description with another page"))
    if address_titles:
        headlines.append((len(address_titles), "are titled with their own address"))
    if working_titles:
        headlines.append((len(working_titles), "carry an editor's working label as the <title>"))
    if wordless:
        headlines.append((len(wordless), "carry a title or description with no word in it"))
    if length_reported:
        headlines.append((len(off_length), "have a <title> outside {}-{} characters".format(
            TITLE_MIN, TITLE_MAX)))

    if not problems:
        # "every crawled page has a unique title and meta description" was
        # printed about a newspaper where 14 of 60 pages carry no meta
        # description at all - the check tests duplication and length, and
        # never claimed to test presence, but the sentence did.
        with_description = len([p for p in pages if p.get("meta_description")
                                or (p.get("og") or {}).get("og:description")])
        result.skip("title-and-description",
                    "no two of the {} carry the same title, and no two of the {} carry the "
                    "same meta description; none is its own address, and none is far outside "
                    "the {}-{} character range"
                    .format(plural(len(pages), "crawled page"),
                            plural(with_description, "page with a description",
                                   "pages with a description"),
                            TITLE_MIN, TITLE_MAX))
        return

    affected = {p["url"] for p in missing_title + url_titles + wordless}
    if undescribed_reported:
        affected.update(p["url"] for p in undescribed)
    if length_reported:
        affected.update(p["url"] for p in off_length)
    for page in pages:
        if page.get("title") in duplicate_titles or page.get("meta_description") in duplicate_descriptions:
            affected.add(page["url"])

    # A page described only in Open Graph is not the severe case: the sentence
    # exists, in the wrong tag.
    #
    # A title that is the page's own address weighs the same as no title at
    # all, because it answers the question the title exists to answer with the
    # address the asker already had. A working label on one page does not: it
    # is one page's name left over from the editor, and the rest of the site's
    # titles are fine.
    severe = bool(missing_title or (undescribed_reported and missing_desc) or duplicate_titles
                  or address_titles or len(working_titles) > 1)
    count, fault = max(headlines, key=lambda item: item[0]) if headlines else (
        len(affected), "need a title or description fix")
    if count == 1:
        verb, _, rest = fault.partition(" ")
        fault = {"have": "has", "share": "shares", "are": "is", "publish": "publishes",
                 "carry": "carries", "need": "needs"}.get(verb, verb) + " " + rest
    kept = {id(p) for p in pages}
    counted_once = [p for p in read if id(p) not in kept]
    # "Crawled" only where the number is the crawl's. A project-management
    # product's report said "21 of the 58 crawled pages have no meta description" beside "the
    # 58 of 60 page(s) this audit read": the crawl fetched 60 and two of them
    # were not pages, so 58 is what was read, and calling it what was crawled
    # contradicted the 60 in the same report.
    if counted_once:
        noun = ("distinct page this audit read", "distinct pages this audit read")
    elif crawled is not None and crawled != len(read):
        noun = ("page this audit read", "pages this audit read")
    else:
        noun = ("crawled page", "crawled pages")
    title = "{} of the {} {}".format(count, plural(len(pages), *noun), fault)
    if len(headlines) > 1:
        more = len(headlines) - 1
        title += ", and {} more title or description {}".format(
            more, "fault" if more == 1 else "faults")
    result.add(
        id_hint="title-and-description-hygiene",
        title=title,
        severity="medium" if severe else "low", confidence="high",
        evidence="{}. Affected pages include: {}.{}".format(
            "; ".join(problems), ", ".join(example_urls(sorted(affected) or [p["url"] for p in pages])),
            # The population, named, wherever it is not every page read.
            # "Repeats", not "is": the folded page that prompted this is a
            # second event page for one user group whose text differs by four
            # characters.
            " Counted over {} distinct pages: of the {} this crawl read, {} ({}) {} another "
            "crawled page, by its text or by the canonical it declares, and {} counted once "
            "with it.".format(
                len(pages), len(read), len(counted_once),
                ", ".join(example_urls(sorted(p["url"] for p in counted_once))),
                "repeats" if len(counted_once) == 1 else "repeat",
                "is" if len(counted_once) == 1 else "are")
            if counted_once else ""),
        # `meta-hygiene` asserts a negative - "N pages have no meta
        # description" - so it has to name what came back empty. Both sources
        # are read above and both are read off every page: the description tag
        # itself, and `og:description`, which states the same fact and which a
        # page can carry on its own. The canonical fold is named too, because
        # the counts in every sentence here are counts after it.
        checked=("the <title> element and the meta description tag on every crawled page",
                 "the og:description tag on the same pages, which states the same fact "
                 "independently of the meta description",
                 "each page's declared canonical, so a URL and its canonical target are "
                 "counted as one page"),
        mechanism="B", root_cause="meta-hygiene",
        summary="Give every page a unique title and a meta description that states the page's "
                "specific fact.",
        how_to_fix=[
            "Write a unique <title> of {}-{} characters for each page, leading with the "
            "specific subject rather than the brand name ({}).".format(
                TITLE_MIN, TITLE_MAX, _TITLE_RANGE_NOTE),
            # "the price" led that list of examples on every site this check
            # reaches, and it reaches all of them - it was printed for a
            # library, a school and a personal homepage. The three examples now
            # name facts any page can carry.
            "Write a unique meta description of {}-{} characters that states the concrete "
            "fact on the page (the date, the place, the answer).".format(DESC_MIN, DESC_MAX),
            "Duplicated titles usually come from a template that omits the page variable; fix "
            "the template rather than each page.",
            "A title that reads as a web address is the same fault one step further on: the "
            "template fell back to the page's URL. Give it the page's own name.",
        ],
        effort="medium", owner="content owner",
        rationale="The title and description are the shortest description of a "
                  "page a consumer sees, and are often quoted directly. Duplicates make "
                  "different pages look like the same page.",
        affected_pages=sorted(affected),
    )


def _report_meta_escaped_twice(result, pages):
    """A title or meta description a template escaped twice.

    Its own finding rather than a line in the hygiene one, because the fix is
    not the hygiene fix. "Write a unique meta description that states the
    concrete fact" is wrong advice for a sentence that is already written and
    already correct: nothing about the copy needs changing and one filter in
    one template does. Owner and effort say so too - a developer, low - where
    the hygiene finding says a content owner and medium.
    """
    # Registered here as well as in the caller. `SkillResult.check` is
    # idempotent on the id and records which function the finding came from,
    # so a check that fires inside a helper is attributed to that helper
    # rather than looking like a check that ran and passed.
    result.check("title-and-description")
    escaped = []
    for page in pages:
        for where, value in (("meta description", page.get("meta_description")),
                             ("<title>", page.get("title"))):
            if _escaped_twice(value):
                escaped.append((page, where, value))
                break
    if not escaped:
        return
    page, where, value = escaped[0]
    result.add(
        id_hint="meta-values-escaped-twice",
        title="{} escaped twice in the page template".format(
            plural(len(escaped), "page has its title or description",
                   "pages have their titles or descriptions")),
        severity="medium", confidence="high",
        # `_centred_on`, for the same reason the JSON-LD finding uses it: the
        # excerpt has to contain the character reference the finding is about,
        # and a description runs to 160 characters.
        evidence="Example: the {} on {} reads `{}`. An HTML parser resolves `&amp;` in a "
                 "content attribute to `&` on its own, so a value still carrying one was "
                 "escaped by the template after it was already escaped, and a search result "
                 "prints those five characters at a reader.".format(
                     where, page["url"], _centred_on(str(value), 90)),
        mechanism="C", root_cause="template-escapes-values-twice",
        summary="Stop the page template escaping title and description values that are "
                "already escaped.",
        how_to_fix=[
            "Find where the template writes the title and description into the <head>. The "
            "value reaching it is already safe for an attribute, so a second escape filter "
            "on top of it is what produces this.",
            "Remove the extra filter rather than editing the copy: the sentence itself is "
            "correct and every page using that template carries the same fault.",
            "The same filter usually runs over the JSON-LD block as well. If this report "
            "also names JSON-LD values escaped as HTML, the two are one fix.",
        ],
        effort="low", owner="developer",
        rationale="A search result and an assistant both print the description as it is "
                  "published. `&amp;` in place of an ampersand is visible to every reader "
                  "and makes the page look unmaintained, while a validator sees nothing "
                  "wrong because the markup is valid.",
        affected_pages=sorted(p["url"] for p, _, _ in escaped),
    )


# How many addresses have to be caught in the trap before it is a template
# fault rather than one stray link. Three, the same floor the description half
# of the hygiene check uses: two is an accident and three is a pattern that
# will keep producing more, because a filter combination is generated rather
# than authored and there is no limit to how many of them exist.
PARAMETER_TWIN_MINIMUM = 3


def _declares_which_page_it_is(page):
    """Does this page state, in either tag, which address is the real one?

    Two tags, read independently, because the claim underneath is a negative
    and one detector coming back empty is a guess with a number on it. A
    `<link rel="canonical">` is the statement a crawler acts on; `og:url` is
    the same fact for a sharing surface, and plenty of templates carry one
    without the other. A page carrying either has named itself.
    """
    return bool((page.get("canonical") or "").strip()
                or ((page.get("og") or {}).get("og:url") or "").strip())


# How much text a page has to carry before "these two deliver the same page"
# says anything. Two near-empty pages are not one page, and a site's error
# template would otherwise fold every 404 into a single duplicate group.
_SAME_CONTENT_MIN_CHARS = 200


# How alike two pages' main text has to be before they are one page at two
# addresses, and the unit the likeness is measured in.
#
# Both exist because the test used to be an AND: `_delivered_content_key` glued
# the title in front of the body and grouped on exact equality, so two
# addresses counted as one page only when the title *and* every character of
# the body matched. A retailer shipping four duplicate pairs - all indexable,
# all self-canonical, all in the sitemap - was told "no two crawled addresses
# delivered the same title and the same main text, so declaring no canonical
# costs this crawl nothing". Its `/about/` and `/about-us/` carry identical
# body copy under different titles; its `/contact/` and `/contact-us/` are 0.93
# alike. On both pairs the body matched and only the title differed, which is
# the half of the test that is evidence.
#
# The title is not read here at all now. It is a label a template writes; the
# body is what the two addresses deliver, and the canonical tag exists to
# settle which address delivers it.
#
# 0.85 of the three-word runs in common. Whole-word runs rather than whole
# words, because two different product pages on one template share most of
# their words - "add to basket", "free delivery", the footer - and almost none
# of their three-word runs.
_SAME_BODY_SIMILARITY = 0.85
_SHINGLE_WORDS = 3
# A script that writes without spaces yields one enormous "word" per run, so
# word runs compare nothing there. Characters are the unit that works in every
# script; five of them is about the same amount of meaning as three English
# words. See `UNSPACED_CHARACTER_WIDTH` for the same problem in the title check.
_UNSPACED_SHINGLE_CHARS = 5


def _body_shingles(page):
    """The runs of this page's main text, as a set two pages can be compared on.

    Empty for a page with too little text to say anything: two near-empty
    pages are not one page, and a site's error template would otherwise fold
    every 404 into a single duplicate group.
    """
    body = (page.get("body_text") or "").strip()
    if len(body) < _SAME_CONTENT_MIN_CHARS:
        return frozenset()
    lowered = body.lower()
    words = re.findall(r"\w+", lowered)
    # Far fewer words than characters is a script that does not write spaces.
    if len(words) * 4 < len(lowered):
        units, size = list(re.sub(r"\s+", "", lowered)), _UNSPACED_SHINGLE_CHARS
    else:
        units, size = words, _SHINGLE_WORDS
    if len(units) < size:
        return frozenset()
    return frozenset(tuple(units[index:index + size])
                     for index in range(len(units) - size + 1))


def _how_alike(left, right):
    """Share of the runs these two pages have in common, 0 to 1."""
    if not left or not right:
        return 0.0
    # A ratio of sizes below the threshold cannot reach it, whatever overlaps,
    # so the intersection is never built for a long page and a short one.
    small, large = sorted((len(left), len(right)))
    if small < large * _SAME_BODY_SIMILARITY:
        return 0.0
    return len(left & right) / float(len(left | right))


def _one_page_at_several_addresses(pages):
    """Groups of crawled addresses that delivered the same page.

    A documentation host publishes every version of a project at its own path,
    so `/en/latest/reference.html` and `/en/stable/reference.html` are the same
    22kB of HTML at two addresses, and the crawl walked both trees. Nothing in
    this marketplace asked whether a canonical *should* exist: the canonical
    checks grade the targets of the canonicals a site declares, so a site
    declaring none was filed under "did not apply" - twice, in one appendix,
    on the site where duplication was the largest discoverability defect it
    had.

    Two uses, and they have to be one function. The finding below reports the
    duplication; the title-hygiene check folds these groups before counting
    duplicate titles, because 60 of 60 pages "sharing a title" on that site
    was 29 pairs of one page counted twice - billed to a content owner at half
    a day to a day of writing.

    Grouped on the body alone, and on how alike two bodies are rather than on
    whether they are identical. See `_SAME_BODY_SIMILARITY`.
    """
    shingles = {}
    ordered = []
    for page in pages:
        runs = _body_shingles(page)
        if runs:
            shingles[id(page)] = runs
            ordered.append(page)

    # Union-find over the pages with readable text. Transitive on purpose: a
    # template rendered at three addresses is one group even where the middle
    # one is what ties the outer two together.
    parent = {id(p): id(p) for p in ordered}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    # Addresses on one path that differ in a parameter selecting the page are
    # different pages, however alike their text. A university's mail form is
    # the same form for every office it serves, and its `dir` parameter picks
    # the office. See `_parameters_that_select`.
    selecting = _selecting_parameters_by_path(ordered)

    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if root(id(left)) == root(id(right)):
                continue
            if _selected_apart(left, right, selecting):
                continue
            if _how_alike(shingles[id(left)], shingles[id(right)]) >= _SAME_BODY_SIMILARITY:
                parent[root(id(left))] = root(id(right))

    # A directory and its index file, serving the same text. Read apart from the
    # likeness above, because that test sets aside pages too short to compare
    # and a front door that is mostly a menu is exactly that: a project's `/`
    # and `/home.html` deliver the same 129 characters under the same title, no
    # canonical on either, and the appendix said "no two crawled addresses
    # delivered the same main text". Same text, word for word, is not a
    # likeness to measure.
    for alias, directory in _index_file_aliases(pages):
        for page in (alias, directory):
            if id(page) not in parent:
                parent[id(page)] = id(page)
                ordered.append(page)
        if root(id(alias)) != root(id(directory)):
            parent[root(id(alias))] = root(id(directory))

    grouped = defaultdict(list)
    for page in ordered:
        grouped[root(id(page))].append(page)
    return sorted((sorted(group, key=lambda p: (len(p["url"]), p["url"]))
                   for group in grouped.values() if len(group) > 1),
                  key=lambda g: g[0]["url"])


# The file names a web server answers a directory's address with. A directory
# reachable at both `/docs/` and `/docs/index.html` is one page at two
# addresses by the server's own construction.
_INDEX_FILE_RE = re.compile(
    r"^(?:index|home|default|main|start)\.(?:html?|php|aspx?|jsp|cfm|shtml)$", re.I)


def _index_file_aliases(pages):
    """(index-file page, directory page) pairs that delivered the same text.

    The text has to be identical once whitespace is folded, and not empty, and
    the two have to sit on one host with the index file directly in the
    directory. A same-named file one level down is a different page.
    """
    by_address = {}
    for page in pages:
        address = str(page.get("final_url") or page.get("url") or "")
        parts = urlparse(address)
        by_address[(parts.netloc.lower(), parts.path or "/")] = page
    pairs = []
    for (host, path), page in sorted(by_address.items()):
        directory, _, filename = path.rpartition("/")
        if not _INDEX_FILE_RE.match(filename):
            continue
        other = by_address.get((host, directory + "/"))
        if other is None or other is page:
            continue
        text = " ".join(str(page.get("body_text") or "").split())
        if text and text == " ".join(str(other.get("body_text") or "").split()):
            pairs.append((page, other))
    return pairs


def _is_an_index_file_group(group):
    """Does this group of copies hold a directory and its own index file?"""
    urls = {str(p.get("final_url") or p.get("url") or "") for p in group}
    return any(page in group and other in group
               for page, other in _index_file_aliases(group)) and len(urls) > 1


def _fold_identical_pages(pages):
    """One record per page, where several addresses delivered the same page.

    The shortest address in each group is kept, which is the one a site
    canonicalises onto when it gets around to it. Counting the others as pages
    of their own inflates every "N of M pages" this skill prints and turns a
    crawl artefact into a writing job.
    """
    duplicates = set()
    for group in _one_page_at_several_addresses(pages):
        duplicates.update(id(p) for p in group[1:])
    if not duplicates:
        return list(pages)
    return [p for p in pages if id(p) not in duplicates]


def _what_the_page_says_it_is(page):
    """The title and top heading, folded, as the page's own statement of itself."""
    headings = (page.get("headings") or {}).get("h1") or []
    return (" ".join(str(page.get("title") or "").split()),
            " ".join(" ".join(str(h).split()) for h in headings))


def _parameters_that_select(entries):
    """Query parameters whose value changes what the page says it is.

    A university's mail form is one path, `news_mail_j.php`, and its `dir`
    parameter names the office the message goes to - one address heads its
    form "<Institute> へのお問い合わせ内容", the others render the office name
    elsewhere. They were grouped as one page reached through tracking
    parameters, and the fix pointed every one of them at the bare address: a
    canonical that tells every crawler all seven offices' forms are the first
    one. A parameter is a filter when changing it leaves the title and heading
    alone; where changing it changes either, it selects a different page, and
    addresses that differ in it are not twins.
    """
    parsed = [(parse_qs(query, keep_blank_values=True), _what_the_page_says_it_is(page))
              for query, page in entries]
    names = sorted({name for values, _said in parsed for name in values})
    selecting = []
    for name in names:
        said_by_value = defaultdict(set)
        for values, said in parsed:
            said_by_value[tuple(values.get(name) or ())].add(said)
        if len(said_by_value) < 2:
            continue
        # Changing this parameter's value changed the page's own statement
        # for at least one value: its pages do not all say the same thing.
        if len({said for group in said_by_value.values() for said in group}) > 1 and any(
                said_by_value[a] != said_by_value[b]
                for a in said_by_value for b in said_by_value if a != b):
            selecting.append(name)
    return selecting


def _address_parts(page):
    """(host, path, query) of the address a page was delivered at."""
    parsed = urlparse(str(page.get("final_url") or page.get("url") or ""))
    return parsed.netloc.lower(), parsed.path.rstrip("/") or "/", parsed.query


def _selecting_parameters_by_path(pages):
    """{(host, path): [parameters that select the page]} across these pages."""
    by_path = defaultdict(list)
    for page in pages:
        host, path, query = _address_parts(page)
        if query:
            by_path[(host, path)].append((query, page))
    return {key: _parameters_that_select(entries)
            for key, entries in by_path.items() if len(entries) > 1}


def _selected_apart(left, right, selecting):
    """Are these two addresses one path with different values of a selecting parameter?"""
    left_host, left_path, left_query = _address_parts(left)
    right_host, right_path, right_query = _address_parts(right)
    if (left_host, left_path) != (right_host, right_path):
        return False
    names = selecting.get((left_host, left_path)) or ()
    if not names:
        return False
    left_values = parse_qs(left_query, keep_blank_values=True)
    right_values = parse_qs(right_query, keep_blank_values=True)
    return any(left_values.get(name) != right_values.get(name) for name in names)


def _parameter_twins(pages):
    """Sets of crawled addresses that are one page reached several ways.

    A group is one path, fetched more than once with different query strings,
    every member carrying the same <title>, and no member declaring which of
    them is the page. All four conditions matter:

      the same path        - `/a` and `/b` are two pages however alike
      different queries    - the parameters are the only difference
      the same title       - the page's own statement that these are one page.
                             Without it, `?id=41` and `?id=42` on a catalogue
                             would be caught, and telling that site to
                             canonicalise them onto one address would delete
                             every item but one from every index
      nothing declared     - a group that names a canonical is already fixed,
                             whether or not the canonical is any good; whether
                             it resolves is crawl-access-audit's question

    Returns (groups, twinned), where `twinned` counts the addresses that met
    the first two conditions and failed one of the last two. The decline needs
    it: "no page was reached twice" and "three were, and each said it was a
    different page" are different answers, and a reader whose site is full of
    filter URLs would read the first as this check not having looked.
    """
    by_path = defaultdict(list)
    for page in pages:
        address = page.get("final_url") or page.get("url") or ""
        try:
            parsed = urlparse(address)
        except ValueError:
            continue
        if not parsed.query:
            continue
        by_path[(parsed.netloc.lower(), parsed.path.rstrip("/") or "/")].append(
            (parsed.query, page))

    groups = []
    twinned = set()
    for (host, path), entries in sorted(by_path.items()):
        if len({query for query, _page in entries}) < 2:
            continue
        twinned.update(page["url"] for _query, page in entries)
        # A parameter whose value changes what the page says about itself
        # selects a page; it does not filter one. See `_parameters_that_select`.
        selecting = _parameters_that_select(entries)
        by_title = defaultdict(list)
        for query, page in entries:
            title = " ".join((page.get("title") or "").split())
            if title:
                values = parse_qs(query, keep_blank_values=True)
                key = (title,) + tuple(tuple(values.get(name) or ()) for name in selecting)
                by_title[key].append((query, page))
        for key, members in sorted(by_title.items()):
            title = key[0]
            if len({query for query, _page in members}) < 2:
                continue
            if any(_declares_which_page_it_is(page) for _query, page in members):
                continue
            groups.append((
                "{}://{}{}".format(urlparse(members[0][1].get("url") or "").scheme or "https",
                                   host, path),
                title,
                [page for _query, page in members]))
    return groups, sorted(twinned)


def _check_canonical_declaration(result, pages, platform=None):
    """Addresses that differ only by their parameters, and name no canonical.

    A retailer's report said, correctly, that 46 of its 60 crawled pages share
    a title, and separately that no page anywhere declares a canonical. It
    never joined the two. The 46 were one listing reached through filter and
    sort parameters, and every one of them is telling each crawler that this
    combination of filters is a page of its own - which is where the duplicate
    titles come from, and declaring a canonical is the one edit that resolves
    them.

    Neither half was anybody's finding. The duplicate titles are counted by the
    hygiene check above, which reports them as a writing problem and offers to
    fix the template that omits the page variable - advice that cannot work,
    because the template is not omitting anything: the pages really do have the
    same title, because they really are the same page. The canonical checks
    live in the access skill, which declines honestly when a crawl carries no
    canonical to resolve, because it grades the targets of the canonicals a
    site declares and a site that declares none has none to grade.

    So this is stated where the evidence is: the crawl's own list of addresses,
    the title on each, and the two tags that would have said which address is
    the page.
    """
    result.check("canonical-declaration")
    # The other shape of the same fault, and the one nothing in this
    # marketplace asked about: the same page served at two paths, with no
    # parameters involved and nothing saying which address is the page. See
    # `_one_page_at_several_addresses`.
    copies = [group for group in _one_page_at_several_addresses(pages)
              if not any(_declares_which_page_it_is(p) for p in group)]
    copied = sorted({p["url"] for group in copies for p in group})
    # Groups whose members do not all carry the same <title>. Counted so the
    # evidence can say so: the check that this replaces required the titles to
    # match as well, and missed four duplicate pairs on one retailer because of
    # it.
    differing_titles = len([group for group in copies
                            if len({" ".join((p.get("title") or "").split())
                                    for p in group}) > 1])
    # A directory answering at its index file as well is a server shape, not a
    # stray link, so one such pair is enough. See `_index_file_aliases`.
    index_groups = [group for group in copies if _is_an_index_file_group(group)]
    reported_copies = len(copied) >= PARAMETER_TWIN_MINIMUM or bool(index_groups)
    if reported_copies:
        widest = max(copies, key=len)
        result.add(
            id_hint="the-same-page-at-several-addresses-with-no-canonical",
            title="{} served at more than one address, and none says which is the "
                  "real one".format(plural(len(copies), "page is", "pages are")),
            severity="medium", confidence="high",
            evidence=(
                "{} deliver the same main text as another crawled address, in {}, and no "
                "member of any group declares a canonical URL or an og:url. The clearest "
                "is {}, at {}.{}"
                .format(plural(len(copied), "crawled address", "crawled addresses"),
                        plural(len(copies), "group"),
                        truncate(widest[0].get("title") or widest[0]["url"], 60),
                        ", ".join(p["url"] for p in widest[:3]),
                        # Said out loud, because a reader looking at two
                        # different titles will otherwise conclude the pages
                        # are different. A title is a label a template writes;
                        # the text under it is what the address delivered, and
                        # the pair whose bodies match is a duplicate pair
                        # whatever the two titles say.
                        " The titles differ within {}; the text under them does "
                        "not, which is what makes them one page.".format(
                            plural(differing_titles, "group", "groups"))
                        if differing_titles else "")),
            # The strongest kind of absence claim this audit can make: the
            # pages were read in full, both tags were read off every one of
            # them, and the duplication is measured rather than inferred.
            checked=("the main text of every crawled page, compared against every other "
                     "crawled page as the share of three-word runs they have in common, "
                     "so a group is addresses that delivered the same page rather than "
                     "addresses that look alike. The <title> is not part of that "
                     "comparison: it is a label the template writes, and two addresses "
                     "serving one page under two titles are still one page",
                     "the <link rel=\"canonical\"> element on every page in each group",
                     "the og:url tag on the same pages, which states the same address "
                     "independently and which a template can carry without a canonical"),
            mechanism="B", root_cause="meta-hygiene",
            summary="Declare a canonical on every page so one address is the page and the "
                    "copies point at it.",
            how_to_fix=[
                "Decide which address is the real one for each group - for a versioned tree "
                "that is normally the current release rather than the development build - "
                "and add `<link rel=\"canonical\">` on every copy pointing at it.",
                "Emit the canonical from the template, not per page: the copies exist "
                "because one template renders at several paths, so one edit covers all of "
                "them.",
                "Do not block the copies in robots.txt instead. A URL a crawler may not "
                "fetch is a URL whose canonical nobody reads, so the duplicates stay and "
                "the signal that would have merged them is hidden.",
                "Any duplicate titles reported separately are the same pages: they share a "
                "title because they are one page, so this is the fix for those too rather "
                "than writing different titles for them.",
            ],
            effort="low", owner="developer",
            rationale="Whatever authority the page has is divided among its copies, and a "
                      "consumer choosing between two identical documents has nothing to "
                      "choose on. The site knows which address is the real one and is the "
                      "only party that can say so.",
            affected_pages=copied,
        )

    groups, twinned = _parameter_twins(pages)
    caught = sorted({page["url"] for _path, _title, members in groups for page in members})
    if len(caught) < PARAMETER_TWIN_MINIMUM:
        if reported_copies:
            # Already reported above, and a check that has fired must never
            # also be listed as clean.
            return
        if caught:
            result.skip("canonical-declaration",
                        "{} of the {} were reached at more than one address differing only in "
                        "their query parameters, which is below the {} that would make this a "
                        "template producing addresses rather than a stray link".format(
                            plural(len(caught), "page"), plural(len(pages), "crawled page"),
                            PARAMETER_TWIN_MINIMUM))
        elif twinned:
            result.skip("canonical-declaration",
                        "{} of the {} were reached at more than one address differing only in "
                        "their query parameters, and no two of them share a path and a title "
                        "while declaring no canonical - so the parameters select different "
                        "pages rather than filtering one".format(
                            plural(len(twinned), "page"), plural(len(pages), "crawled page")))
        elif copied:
            # Said as it is. The sentence below used to be printed here too,
            # claiming no two addresses delivered the same text on a crawl where
            # two had.
            result.skip("canonical-declaration",
                        "{} delivered the same main text as another crawled address with no "
                        "canonical declared ({}), which is below the {} that would make this a "
                        "template serving one page at several addresses rather than a stray "
                        "link".format(plural(len(copied), "crawled address",
                                             "crawled addresses"),
                                      ", ".join(copied[:3]), PARAMETER_TWIN_MINIMUM))
        else:
            result.skip("canonical-declaration",
                        "no crawled page was reached at a second address differing only in its "
                        "query parameters, and no two crawled addresses delivered the same main "
                        "text, so declaring no canonical costs this crawl nothing that can be "
                        "seen from it")
        return

    path, title, members = max(groups, key=lambda g: (len(g[2]), g[0]))
    queries = sorted({urlparse(p.get("final_url") or p.get("url") or "").query
                      for p in members})
    site_wide = not [p for p in pages if (p.get("canonical") or "").strip()]

    result.add(
        id_hint="filter-parameters-produce-pages-with-no-canonical",
        title="{} reachable at several addresses that declare no canonical".format(
            plural(len(caught), "page is", "pages are")),
        severity="medium", confidence="high",
        evidence=(
            "{} in {} share a path and a <title> with another address that differs from it "
            "only in its query string, and none of them declares a canonical. The largest "
            "group is {} of them on {}, all titled \"{}\", reached with query strings such as "
            "{}.{}".format(
                plural(len(caught), "crawled address", "crawled addresses"),
                plural(len(groups), "group"),
                len(members), path, truncate(title, 60),
                ", ".join("`{}`".format(truncate(q, 60)) for q in queries[:3]),
                " No crawled page on this site declares a canonical at all."
                if site_wide else "")),
        # Two sources, both read in `_declares_which_page_it_is` and both
        # capable of answering on their own. A site carrying `og:url` and no
        # canonical has still said which address the page is, and reporting it
        # here would be the single-detector absence claim this marketplace
        # keeps making.
        checked=("the <link rel=\"canonical\"> element on every crawled page",
                 "the og:url tag on the same pages, which states the same address "
                 "independently of the canonical and which a template can carry "
                 "without one"),
        mechanism="B", root_cause="meta-hygiene",
        summary="Declare a canonical on the listing template so every filter and sort "
                "combination points at one address.",
        how_to_fix=[
            # The whole repair is one tag on one line, so it goes in the step
            # rather than in a `snippet` block. Every other snippet this skill
            # ships is JSON-LD, and the pass that proves a snippet works parses
            # it as JSON-LD; a `<link>` element would be handed to that pass as
            # code nothing can check.
            "Add `<link rel=\"canonical\" href=\"{}\">` to the template that renders these "
            "pages, so every filter and sort combination names the address with the "
            "parameters removed.".format(path),
            "Keep a parameter in the canonical only where it selects different content rather "
            "than filtering one list. The test is the one used here: pages that carry the same "
            "<title> are the same page, and pages that carry different titles are not.",
            "Do not block the parameter addresses in robots.txt instead. A URL a crawler may "
            "not fetch is a URL whose canonical nobody reads, so the duplicates stay and the "
            "signal that would have merged them is hidden.",
            "This also resolves the shared titles reported separately: those pages share a "
            "title because they are one page, so the fix is to say so rather than to write "
            "different titles for them.",
            where_the_template_is(platform),
            who_edits_the_template(platform),
        ],
        effort="low", owner="developer",
        rationale="Every combination of filters is a separate address, and there is no "
                  "limit to how many combinations exist. With no canonical, each one is "
                  "offered to a crawler as a page in its own right, so the crawl budget goes "
                  "on copies and whatever authority the real listing has is divided among "
                  "them.",
        affected_pages=caught,
    )


OPEN_GRAPH_TAGS = ("og:title", "og:description", "og:image")

# One step per tag, each true of that tag. The single step read "Add `og:image`
# to your base template, populated from the page's existing title and meta
# description" on a report that had correctly narrowed the finding to
# `og:image` alone. You cannot populate an image from a title: the sentence was
# written for og:title and og:description and was never re-read after the
# narrowing substituted a different tag into it.
OPEN_GRAPH_FIX_STEPS = {
    "og:title": "Fill `og:title` from the same variable that renders the page's <title>, "
                "without the site-name suffix a <title> usually carries.",
    "og:description": "Fill `og:description` from the page's meta description, and where a "
                      "page has none, from the first sentence of its main content.",
    # Neutral about what the page is, because this step is printed on every
    # page missing the tag and this check is gated on nothing. Reports printed
    # "the article's lead image, the product photograph" verbatim for a Thai
    # school, a Japanese shrine, a public library and a personal homepage, none
    # of which has a product. Reworded rather than branched per site kind, the
    # way the same vocabulary was swept out of three other skills: "the
    # picture the page leads with" is true of an article, an item for sale, a
    # notice board and a poem.
    "og:image": "Point `og:image` at an absolute URL for an image the page already has - the "
                "picture the page leads with, the one a reader would recognise it by - with "
                "one site-wide image as the fallback for pages that carry none. It should be "
                "at least 1200x630 pixels.",
}


# The words a template puts in `og:title` when nobody filled it in: the name of
# the menu entry for the front page, in the languages this audit has met.
# Compared as a whole value, so "Home Insurance Explained" is a title.
_GENERIC_OG_TITLES = frozenset(" ".join(t.casefold().split()) for t in (
    "home", "homepage", "home page", "main", "main page", "index", "start", "untitled",
    "welcome", "default", "page", "new page",
    "首页", "主页", "首頁", "主頁", "ホーム", "トップ", "トップページ", "홈", "메인", "메인페이지",
    "accueil", "inicio", "início", "página inicial", "startseite", "home - ", "главная",
    "головна", "strona główna", "anasayfa", "trang chủ", "beranda", "หน้าหลัก",
    "الرئيسية", "الصفحة الرئيسية", "ראשי", "דף הבית", "pagina iniziale", "hjem", "etusivu",
))

# A breadcrumb in `og:site_name`: an arrow between two words, or a slash with
# space on both sides of it. A slash inside a name, as in "AC/DC", is not one.
_BREADCRUMB_IN_A_NAME_RE = re.compile(r"\S\s*[>›»]\s*\S|\S\s+/\s+\S")

# How many pages have to share one `og:title` before it is the template's
# title rather than the page's. Three, and more than half of the pages that
# carry the tag: a small site whose two landing pages share a title is not a
# template problem.
_SHARED_OG_TITLE_MIN_PAGES = 3


def _open_graph_values_that_say_nothing(pages):
    """(generic titles, one title shared by most pages, breadcrumb site names).

    Presence is not the whole of the tag. A museum's template writes
    `og:title` "Home" on every page that carries one and writes the page's
    breadcrumb trail - "<Name>>관람 정보>전시해설>…" - into `og:site_name`, and
    the check passed it with "5 of 60 pages are missing an Open Graph tag":
    every share card from that site reads "Home", and every system that reads
    `og:site_name` as the site's name reads a navigation path instead.

    The homepage is left out of the shared-title count, because the site's
    name is a correct title for the front door and a one-page site published
    in several languages has one front door per language.
    """
    with_title = [(p, " ".join(str((p.get("og") or {}).get("og:title") or "").split()))
                  for p in pages]
    with_title = [(p, t) for p, t in with_title if t]
    generic = [(p, t) for p, t in with_title if t.casefold() in _GENERIC_OG_TITLES]
    # One front page titled "Home" is one tag to change, not a template writing
    # the same word everywhere. It counts where it is more than one page, or
    # every page that carries the tag.
    if len(generic) < 2 and len(generic) < len(with_title):
        generic = []
    shared = []
    inner = [(p, t) for p, t in with_title if p.get("page_type") != "home"]
    counts = {}
    for _, title in inner:
        counts[title] = counts.get(title, 0) + 1
    if counts:
        title, seen = max(counts.items(), key=lambda item: (item[1], item[0]))
        if seen >= _SHARED_OG_TITLE_MIN_PAGES and seen * 2 > len(inner):
            shared = [(p, t) for p, t in inner if t == title]
    breadcrumbs = []
    for page in pages:
        name = " ".join(str((page.get("og") or {}).get("og:site_name") or "").split())
        if name and _BREADCRUMB_IN_A_NAME_RE.search(name):
            breadcrumbs.append((page, name))
    return generic, shared, breadcrumbs


def _report_open_graph_values(result, pages, platform=None):
    """The value half of the Open Graph check. True if it reported anything."""
    # Registered here as well as in the caller, so this helper cannot report
    # under a check that was never run.
    result.check("open-graph-tags")
    generic, shared, breadcrumbs = _open_graph_values_that_say_nothing(pages)
    title_pages = {p["url"]: t for p, t in generic + shared}
    if not title_pages and not breadcrumbs:
        return False
    parts, steps = [], []
    if title_pages:
        common = max(set(title_pages.values()), key=list(title_pages.values()).count)
        parts.append('`og:title` is "{}" on {} of the {} pages crawled{}'.format(
            common, list(title_pages.values()).count(common), len(pages),
            ", a word that names no page" if common.casefold() in _GENERIC_OG_TITLES
            else ", so it describes none of them"))
        steps.append("Set `og:title` on each page to that page's own title - its heading, "
                     "without the site name - not one value for the whole site.")
    if breadcrumbs:
        parts.append('`og:site_name` carries a navigation path rather than a name on {} of '
                     'the {} pages, for example "{}" on {}'.format(
                         len(breadcrumbs), len(pages), truncate(breadcrumbs[0][1], 80),
                         breadcrumbs[0][0]["url"]))
        steps.append("Set `og:site_name` to the site's name alone, the same string on every "
                     "page; the breadcrumb belongs in the page, or in BreadcrumbList markup.")
    affected = sorted(set(title_pages) | {p["url"] for p, _ in breadcrumbs})
    result.add(
        id_hint="open-graph-values-name-no-page",
        title="Open Graph tags are present but do not name the page{}".format(
            " or the site" if breadcrumbs else ""),
        # Medium where the site's name is the casualty: `og:site_name` is read
        # as the name a site gives itself, and a path in it is a wrong name.
        severity="medium" if breadcrumbs else "low", confidence="high",
        evidence="{}.".format("; ".join(parts)),
        checked=("the og:title and og:site_name values on every crawled page, compared "
                 "against each other and against the words a template leaves in a title "
                 "nobody filled in",),
        mechanism="B", root_cause="open-graph-incomplete",
        summary="Give each page its own og:title and give og:site_name the site's name alone.",
        how_to_fix=steps + [where_the_template_is(platform), who_edits_the_template(platform)],
        effort="low", owner="developer",
        rationale="A share card and an answer surface show og:title as the page's name and "
                  "og:site_name as the site's. A value copied onto every page names neither.",
        affected_pages=affected,
    )
    return True


def _check_open_graph(result, pages, platform=None):
    result.check("open-graph-tags")
    # Values first, so a site whose tags are all present and all say "Home" is
    # not listed below as one whose tags are fine.
    reported_values = _report_open_graph_values(result, pages, platform)
    # Which tags are actually missing, not "at least one of three". A
    # restaurant group carrying og:title and og:description on every page and
    # og:image on seventeen of fifty-nine was told "Open Graph tags are missing
    # on 42 of 59 pages" and instructed to "add the three Open Graph tags to
    # your base template" - two of which were already there.
    missing_by_tag = {tag: [p for p in pages if not (p.get("og") or {}).get(tag)]
                      for tag in OPEN_GRAPH_TAGS}
    absent = [tag for tag in OPEN_GRAPH_TAGS if missing_by_tag[tag]]
    incomplete = [p for p in pages
                  if not all((p.get("og") or {}).get(k) for k in OPEN_GRAPH_TAGS)]
    if not incomplete:
        if not reported_values:
            result.skip("open-graph-tags",
                        "every crawled page carries og:title, og:description and og:image")
        return
    if len(incomplete) < len(pages) * 0.4:
        if not reported_values:
            result.skip("open-graph-tags",
                        "{} of {} pages are missing an Open Graph tag, below the threshold "
                        "where this is a template problem".format(len(incomplete), len(pages)))
        return

    result.add(
        id_hint="incomplete-open-graph-tags",
        title="{} missing on {} of {} pages".format(
            plural(len(absent), "Open Graph tag is", "Open Graph tags are"),
            max(len(missing_by_tag[t]) for t in absent), len(pages)),
        severity="low", confidence="high",
        evidence="{}. Pages affected: {}.".format(
            "; ".join("`{}` is absent from {} of {} pages".format(
                tag, len(missing_by_tag[tag]), len(pages)) for tag in absent),
            ", ".join(example_urls([p["url"] for p in incomplete]))),
        # One source: the `og` dictionary the crawl read off each page. The
        # crawl also records `twitter`, which carries the same three facts
        # under `twitter:title`, `twitter:description` and `twitter:image`, and
        # this check does not read it. Naming it here would name a source the
        # code never consults, so it stays at one and the clamp applies.
        checked=("the og: meta tags on every crawled page",),
        mechanism="B", root_cause="open-graph-incomplete",
        summary="Add og:title, og:description and og:image to the site template.",
        how_to_fix=(
            ["Add {} to your base template.".format(
                " and ".join("`{}`".format(t) for t in absent))]
            + [OPEN_GRAPH_FIX_STEPS[tag] for tag in absent]
            # The reader is being told to edit one template and has not been
            # told where it is. On a platform this audit can name, that is a
            # settings screen or a named file rather than a search.
            + [where_the_template_is(platform), who_edits_the_template(platform)]),
        effort="low", owner="developer",
        rationale="Open Graph tags are what every sharing surface and several "
                  "answer surfaces read to build a preview card, so they decide how the page "
                  "looks wherever it is repeated.",
        affected_pages=[p["url"] for p in incomplete],
    )


def _pages_that_declare_a_language_they_are_not_written_in(pages, site_language):
    """The pages whose `lang` says one language and whose letters say another.

    `detect_site_language` in the shared library already makes this
    identification - a restaurant chain's report printed its sentence verbatim,
    "this site is in EN (declared as en, contradicted by page text written in
    the arabic script)" - and the only thing done with it was to decide which
    English-prose checks to skip. The `lang` check read `not p.get("lang")` and
    so fired on nothing at all: every page of that site carries
    `<html lang="en-US">` over Arabic text, which tells every assistant the site
    is in a language it is not, and no finding said so.

    The record is read rather than the sentence parsed. `pages_declaring` is
    non-zero only on the branch where a `lang` attribute decided the answer, and
    `source` is the bare word "declared" on that branch unless the writing
    system overruled it, so the two fields together isolate the contradiction
    without matching on prose that may be reworded.

    Each page is then confirmed on its own text. A site with one Arabic section
    inside an English site is not a site with a wrong `lang`, and naming a page
    whose own letters were never counted would be asserting the site-wide
    reading about a page that may be the exception to it.
    """
    if site_language.get("source") == "declared":
        return []
    if not site_language.get("pages_declaring"):
        return []
    code = site_language.get("code") or ""
    script = site_language.get("script") or ""
    if not code or not script:
        return []
    found = []
    for page in pages:
        if page.get("status") != 200:
            continue
        if primary_subtag(page.get("lang")) != code:
            continue
        if dominant_script(_page_prose(page)) != script:
            continue
        found.append(page)
    return found


def _check_lang(result, pages, platform=None):
    result.check("html-lang-attribute")
    site_language = detect_site_language(pages)
    # Asked before the "every page declares one" pass below, because a page
    # declaring the wrong language declares one. That pass was the whole answer
    # this check gave a site serving `lang="en-US"` on Arabic pages.
    wrong = _pages_that_declare_a_language_they_are_not_written_in(pages, site_language)
    if wrong:
        declared_code = site_language.get("code") or ""
        result.add(
            id_hint="lang-contradicts-page-text",
            title="{} a language they are not written in".format(
                plural(len(wrong), "page declares", "pages declare")),
            severity="medium", confidence="high",
            evidence='These pages declare `lang="{}"` on <html> and are written in the {} '
                     "script: {}. A declared language is read as a statement by the site, so "
                     "an assistant filtering for {} content collects pages no {} reader can "
                     "use, and one filtering for the language these pages are actually in "
                     "never sees them.".format(
                         declared_code, site_language.get("script") or "",
                         ", ".join(example_urls([p["url"] for p in wrong])),
                         declared_code.upper(), declared_code.upper()),
            # Two sources that disagree, which is the point: the attribute, and
            # the letters. Named because the second one is a reading of the
            # page text and a reader is entitled to check it against their site.
            checked=("the lang attribute on the <html> element of every crawled page",
                     "the writing system of each of those pages' own text, counted letter "
                     "by letter, which is what contradicts the attribute"),
            mechanism="C", root_cause="missing-lang",
            summary="Set lang to the language each page is actually written in.",
            how_to_fix=[
                "Change the lang attribute in the base template to the language the pages "
                "are written in, with its region subtag where that matters.",
                "Where the site publishes more than one language, set lang per page from the "
                "same variable that chooses the page's text, and link the versions to each "
                "other with hreflang.",
                where_the_template_is(platform),
                who_edits_the_template(platform),
            ],
            effort="low", owner="developer",
            rationale="A wrong language is worse than none. A missing attribute leaves a "
                      "consumer to guess from the words; a declared one is taken as the "
                      "site's own statement and stops the guess from happening.",
            affected_pages=[p["url"] for p in wrong],
        )

    without = [p for p in pages if not p.get("lang")]
    if not without:
        if wrong:
            # The finding above answered this check. Saying "every crawled page
            # declares a lang attribute" underneath it would print a pass over
            # the attribute the same report has just called wrong.
            return
        result.skip("html-lang-attribute", "every crawled page declares a lang attribute")
        return

    # The `lang` attribute was the only thing this looked at, and the finding
    # is about what a consumer can know, not about one attribute. The second
    # source is already in the shared library: `detect_site_language` reads the
    # `lang` attributes of the whole crawl first and falls back to
    # `guess_language_from_text`, which scores function words in the body text.
    #
    # It is named as a source only when it comes back with nothing, because
    # that is the case where the claim is corroborated - no page says what the
    # language is and the words do not settle it either. When the rest of the
    # site declares a language, or the text reads clearly as one, the language
    # is knowable and this claim rests on the attribute alone.
    code = site_language.get("code") or ""
    consulted = ["the lang attribute on the <html> element of every crawled page"]
    if code and site_language.get("source") == "declared":
        knowable = (" {} of the crawled pages do declare `{}`, so this is a gap in the "
                    "template rather than a site that sets no language at all.".format(
                        site_language.get("pages_declaring") or 0, code))
    elif code:
        knowable = (" No crawled page declares a language. The page text reads as `{}`, "
                    "which is this audit's reading of the words and not something the "
                    "site states.".format(code))
    else:
        consulted.append("the language the rest of the site declares, and the language "
                         "the page text implies")
        knowable = (" No other page declares a language and the page text does not "
                    "clearly indicate one, so nothing on this site says what language "
                    "it is written in.")

    # The example code in the fix step is this site's own language, or there is
    # no example code at all.
    #
    # A Norwegian site was told, word for word: "Set the lang attribute in your
    # base template, for example `<html lang="en">`." A reader following that
    # literally declares Norwegian pages English - which is the exact defect
    # `lang-contradicts-page-text`, forty lines up in this same function,
    # reports at medium severity on other sites.
    #
    # `detect_site_language` already knows the difference between knowing the
    # language and knowing only the writing system, and returns no `code` for
    # the second case on purpose: Cyrillic is Russian, Ukrainian, Bulgarian and
    # Serbian, and picking one would publish an invented declaration. Where it
    # names no code this step names none either, and says outright that the
    # owner has to supply it.
    script = site_language.get("script") or ""
    if code and site_language.get("source") == "declared":
        example = ('Use `{}` - the code {} of the crawled pages already declare, so the '
                   'attribute reads `<html lang="{}">`.'.format(
                       code, site_language.get("pages_declaring") or 0, code))
    elif code:
        example = ('Use the code for the language the pages are written in. Their words '
                   'read as `{}` to this audit, which is a reading of the text and not '
                   'something the site states, so check it before you set '
                   '`<html lang="{}">`.'.format(code, code))
    elif script:
        example = ("No example code is printed here, deliberately. This audit can see "
                   "that the pages are written in the {} script and cannot tell which "
                   "language that is - the {} script is used by several - so you have to "
                   "supply the code yourself. A guessed one would be a declaration that "
                   "is wrong, which is worse for a consumer than the missing "
                   "attribute.".format(script, script))
    else:
        example = ("No example code is printed here, deliberately. No crawled page "
                   "declares a language and the words on the pages do not settle which "
                   "one this is, so you have to supply the code yourself. A guessed one "
                   "would be a declaration that is wrong, which is worse for a consumer "
                   "than the missing attribute.")

    result.add(
        id_hint="missing-html-lang",
        title="{} not declare a language".format(
            plural(len(without), "page does", "pages do")),
        severity="low", confidence="high",
        evidence="Pages with no lang attribute on <html>: {}.{}".format(
            ", ".join(example_urls([p["url"] for p in without])), knowable),
        checked=tuple(consulted),
        mechanism="C", root_cause="missing-lang",
        summary=('Set lang on the <html> element to `{}`.'.format(code) if code else
                 "Set lang on the <html> element to the code for the language these "
                 "pages are written in."),
        how_to_fix=[
            "Set the lang attribute on the <html> element in your base template. "
            + example,
            # The old step read "such as en-GB or pt-BR", two codes chosen for
            # no reason connected to the site being audited. The shape of a
            # region subtag is the thing worth stating; which region this site
            # publishes for is not something the crawl read, so it is not
            # asserted.
            "Add a region subtag only where the region changes the words - the language "
            "code, a hyphen, then the two-letter country code for the edition those pages "
            "are written for.",
            # The `<html>` element is not in the head, so this one names the
            # template and lets the reader find the tag inside it; the two
            # sentences below are the same pair every other site-wide fix here
            # ends with.
            where_the_template_is(platform),
            who_edits_the_template(platform),
        ],
        effort="low", owner="developer",
        rationale="Without a declared language a consumer has to guess, which "
                  "affects whether the page is surfaced for a given audience at all.",
        affected_pages=[p["url"] for p in without],
    )


def _check_microdata_only(result, pages):
    """Pages that chose microdata over JSON-LD - not pages whose JSON-LD broke.

    `page["jsonld"]` holds the blocks that parsed, so a page shipping one
    `<script type="application/ld+json">` with a syntax error in it has an
    empty `jsonld` list and read here as a page carrying no JSON-LD at all. A
    publisher's report named the same URL twice: once as a page with "microdata
    attributes but no JSON-LD block", and once, correctly, as a page whose
    JSON-LD does not parse. Both cannot be true of one page, and the second is.

    `jsonld_errors` is the difference. A page with one is already reported by
    the JSON-LD validity check, whose advice - find the syntax error in the
    block you wrote - is the advice that page needs. "Express the same entities
    as a JSON-LD block" is advice for a site that wrote none, and telling a
    site to write markup it has already written is how it gets ignored.
    """
    result.check("microdata-and-rdfa")
    heavy_microdata = [p for p in pages if p.get("microdata_count", 0) >= 3
                       and not (p.get("jsonld") or [])]
    microdata_only = [p for p in heavy_microdata if not (p.get("jsonld_errors") or [])]
    jsonld_broke = [p for p in heavy_microdata if p.get("jsonld_errors")]
    if not microdata_only:
        if jsonld_broke:
            result.skip(
                "microdata-and-rdfa",
                "no page relies on microdata or RDFa in place of JSON-LD. {} carry microdata "
                "alongside a JSON-LD block that does not parse, which is a broken block rather "
                "than an absent one, and the JSON-LD validity check reports those".format(
                    plural(len(jsonld_broke), "page", "pages")))
        else:
            result.skip("microdata-and-rdfa",
                        "no page relies on microdata or RDFa in place of JSON-LD")
        return
    result.add(
        id_hint="markup-uses-microdata-not-jsonld",
        title="{} inline microdata instead of JSON-LD".format(
            plural(len(microdata_only), "page uses", "pages use")),
        severity="low", confidence="medium",
        evidence="Pages with microdata attributes and no JSON-LD block at all: {}.{}".format(
            ", ".join(example_urls([p["url"] for p in microdata_only])),
            "" if not jsonld_broke else
            " A further {} carry microdata and a JSON-LD block that does not parse; those "
            "are counted by the JSON-LD validity check, not here.".format(
                plural(len(jsonld_broke), "page", "pages"))),
        mechanism="C", root_cause="microdata-only",
        summary="Migrate the markup to JSON-LD, which is the format consumers support most consistently.",
        how_to_fix=[
            "Express the same entities as a JSON-LD block in the page head.",
            "Microdata can stay in place during the transition; the two do not conflict.",
        ],
        effort="medium", owner="developer",
        rationale="Microdata is valid but sits inside the markup, so it breaks "
                  "whenever the template changes. JSON-LD is a single self-contained block and "
                  "survives redesigns.",
        affected_pages=[p["url"] for p in microdata_only],
    )


# --------------------------------------------------------------------------
# One condition, one finding
# --------------------------------------------------------------------------
#
# Counted per skill, this skill produced 40% of all findings. Measured on this
# repository's own fixture site
# with the JSON-LD removed: six findings came out, all six from this skill, and
# they are the six causes below. Every one was true and every one shipped its
# own paste-ready block. The site owner was handed six chores for one
# condition - the site publishes no structured data at all - and the reader of
# the report saw six problems where there is one.
#
# The decomposition is not what was wrong: an independent review confirmed all
# six sub-skills produce findings from one crawl. What was wrong is that one
# condition was reported six times, so it is folded here rather than anywhere
# in the orchestrator: only this skill knows that these six absences are one
# absence.
_ABSENT_MARKUP_CAUSES = ("no-org-schema", "no-product-schema", "no-article-schema",
                         "no-faq-schema", "no-website-schema", "no-breadcrumb-markup")

# The schema.org type each of those causes is asking for, in the words the fix
# steps use. Keyed on the root cause because that vocabulary is closed and
# shared; a cause with no entry falls back to the finding's own title, so a
# cause added later is named rather than dropped.
_MARKUP_TYPE_ASKED_FOR = {
    "no-org-schema": "Organization, or the LocalBusiness subtype that fits this business",
    "no-product-schema": "Product with an Offer",
    "no-article-schema": "Article",
    "no-faq-schema": "FAQPage",
    "no-website-schema": "WebSite with a SearchAction",
    "no-breadcrumb-markup": "BreadcrumbList",
}

# Which template each of those blocks belongs in, in the words an owner uses
# for it. Read off the causes actually folded, because the step that names them
# used to be a fixed sentence - "the post template, the product page, the page
# of questions" - printed whatever had been folded. It appeared
# verbatim in reports on a Thai school, a Japanese shrine, a public library
# and a personal homepage, none of which has a product page or a page of
# questions and only one of which has posts. The step is not describing this
# site's templates as a shop's; it is now listing the ones this run folded.
#
# A cause with no entry falls back to naming its markup type, the same way
# `_MARKUP_TYPE_ASKED_FOR` does, so a cause added later is named rather than
# dropped or, worse, described as somebody else's page.
_TEMPLATE_THE_BLOCK_BELONGS_IN = {
    "no-product-schema": "the template that renders one item for sale",
    "no-article-schema": "the template that renders one post or article",
    "no-faq-schema": "the template that renders a page of questions and answers",
    "no-website-schema": "the homepage template",
    "no-breadcrumb-markup": "the template that renders the trail above a page's heading",
}

# Below this there is nothing to fold. One absence finding rewritten as "the
# site publishes nothing" says less than the finding already said, and loses
# the type name out of the title.
_FOLD_MINIMUM = 2


def _publishes_no_structured_data(pages):
    """True when no crawled page carries structured data in any form.

    Three things have to be absent, not one.

    A JSON-LD block that does not parse is markup this site wrote and got
    wrong. Its owner needs the syntax error found, not a second block pasted
    beside the broken one - which is why `_set_aside_unparsed_markup` already
    keeps those pages out of every "no X markup" count, and why
    `_check_jsonld_validity` reports them under their own cause. A site in that
    state has a different condition and already has a finding for it, so it is
    not folded here.

    Microdata and RDFa are markup too. A site declaring its identity in
    `itemtype` attributes publishes structured data, and `_check_organization`
    declines on exactly that ground; calling such a site one that publishes
    none would be the false positive that check exists to avoid.
    """
    for page in pages:
        if page.get("jsonld") or page.get("jsonld_types"):
            return False
        # Markup that is present and broken. Not absence - see the docstring.
        if page.get("jsonld_errors"):
            return False
        if page.get("microdata_types") or page.get("microdata_count"):
            return False
        if page.get("rdfa_count"):
            return False
    return True


def _fold_order(finding):
    """Most value first: the site-wide identity block, then the rest.

    Identity leads because it is the one block that goes in the template every
    page shares and the one a machine reads to learn who published everything
    else. After it, severity, then the number of pages the type covers, so the
    reader spends their second edit where it reaches most of the site.
    """
    return (0 if finding["root_cause"] == "no-org-schema" else 1,
            SEVERITY_RANK[finding["severity"]],
            -finding.get("affected_page_count", 0),
            finding["root_cause"])


def _named_pages(finding):
    """", starting with <urls>" for one line of the fold's inventory.

    The examples are labelled as examples wherever the count is larger than the
    list. A finding's `affected_pages` is already trimmed to five by
    `make_finding`, so printing them behind a count without saying so would
    read as the whole population on any finding with more.
    """
    shown = example_urls(finding.get("affected_pages") or [], 2)
    if not shown:
        return ""
    total = finding.get("affected_page_count", 0)
    return "{} {}".format(
        ", starting with" if total > len(shown) else ":", ", ".join(shown))


def _fold_absent_markup(result, pages):
    """Replace every "no X markup" finding with one, on a site that has none.

    Nothing is dropped: each folded finding's own title carries its own counts
    and goes into the evidence verbatim, so a reader still sees that two
    article pages and one product page are involved, and the numbers cannot
    drift from the checks that produced them because they are those checks'
    numbers.

    Only the identity snippet survives as a paste-ready block, because it is
    the only one that belongs in the site-wide template. Pasting an Article
    block into the template every page shares would publish an Article node on
    the homepage. The remaining types are named as the next step with their
    page counts, and they come back as findings in their own right the moment
    the site carries any markup at all - which is what
    `tests/test_fixes_work.py` walks through and asserts.
    """
    if not _publishes_no_structured_data(pages):
        return
    absent = [f for f in result.findings if f["root_cause"] in _ABSENT_MARKUP_CAUSES]
    if len(absent) < _FOLD_MINIMUM:
        return

    ordered = sorted(absent, key=_fold_order)
    anchor = ordered[0]
    action = anchor["suggested_action"]

    inventory = "; ".join(
        "({}) {} - {}{}".format(
            index,
            finding["title"].rstrip("."),
            plural(finding.get("affected_page_count", 0), "page"),
            _named_pages(finding))
        for index, finding in enumerate(ordered, 1))
    remaining = "; ".join(
        "{} ({}), in {}".format(
            _MARKUP_TYPE_ASKED_FOR.get(finding["root_cause"], finding["title"]),
            plural(finding.get("affected_page_count", 0), "page"),
            _TEMPLATE_THE_BLOCK_BELONGS_IN.get(
                finding["root_cause"],
                "the template that renders the {} above".format(
                    plural(finding.get("affected_page_count", 0), "page"))))
        for finding in ordered[1:])
    # The example in the last step, taken from what was folded rather than
    # fixed at "an Article node on the homepage" - which named a type this run
    # had not necessarily found a use for.
    example_type = _MARKUP_TYPE_ASKED_FOR.get(
        ordered[1]["root_cause"], ordered[1]["title"]).split(",")[0]

    # Severity from the most urgent thing folded in, because the condition
    # contains it and publishing it lower would quietly demote a medium finding
    # to a low one. Confidence from the least sure, and effort from the largest
    # piece, because the fold asserts everything under it and the work is at
    # least the biggest of the jobs it replaced. Never the other way round in
    # any of the three.
    severity = min(ordered, key=lambda f: SEVERITY_RANK[f["severity"]])["severity"]
    confidence = max((f["confidence"] for f in ordered), key=CONFIDENCE_LEVELS.index)
    effort = max((f["suggested_action"]["effort"] for f in ordered),
                 key=lambda e: EFFORT_DIVISOR[e])

    for finding in absent:
        result.findings.remove(finding)
    result.check(anchor.get("check") or "organization-markup")
    result.add(
        id_hint="no-structured-data-at-all",
        title="This site publishes no structured data at all",
        severity=severity, confidence=confidence,
        evidence="Checked {}. Not one carries a JSON-LD block, not one declares a "
                 "schema.org type in an itemtype or typeof attribute, and not one ships "
                 "markup that failed to parse - so there is nothing here to correct, only "
                 "something to add. The {} kinds of markup this site should be publishing, "
                 "and the pages each belongs on: {}.".format(
                     plural(len(pages), "crawled page"), len(ordered), inventory),
        # Three independent readings of the same page set, all empty, plus the
        # per-type detectors whose vocabularies the folded findings named.
        checked=("every `<script type=\"application/ld+json\">` block on the {} crawled "
                 "pages: there are none".format(len(pages)),
                 "every itemtype and typeof attribute on those pages, which is how "
                 "Microdata and RDFa declare a schema.org type: there are none",
                 "the JSON-LD parse errors the crawl recorded for those pages: there are "
                 "none either, so no page is shipping a block that a consumer reads and "
                 "discards",
                 "the per-type detectors listed in the evidence above, each run in full "
                 "over the pages it applies to before this finding replaced them"),
        mechanism="C", root_cause=anchor["root_cause"],
        # Self-contained, because this sentence is reprinted in the top sheet
        # and in "Start here" with nothing above it. "Work down the list above"
        # named a list that is only above it in one of the three places.
        summary="{} Then the {} other kinds of markup this site needs, one template "
                "at a time.".format(action["summary"].rstrip(), len(ordered) - 1),
        # The anchor's own steps first, in full. They already name the file or
        # the settings screen for this platform and who edits it, because
        # `template_change_steps` produced them - so the steps added here do
        # not say any of that a second time. The three that follow say what is
        # step 1, what is not, and what happens next: the whole of what the
        # fold adds.
        how_to_fix=list(action["how_to_fix"]) + [
            "Of the {} kinds of markup named above, that block is the only one that goes "
            "in one place for the whole site, and it is the one that states who published "
            "everything else. Do that much and stop.".format(len(ordered)),
            "Then add the rest one at a time, each in the file that renders that kind of "
            "page: {}. Putting any of them where the block above goes would publish a {} "
            "node on every page of the site, including the homepage.".format(
                remaining, example_type),
            "Re-run this audit once the first block is live. This finding applies only to "
            "a site with no markup anywhere, so it stops firing, and each type above comes "
            "back as its own finding with a block filled in from this site's own values.",
        ],
        effort=effort, owner=action["owner"],
        rationale="Structured data is the only place these facts are stated as data "
                  "rather than inferred from prose, and this site states none of them that "
                  "way. One block in the shared template ends that, and the rest hang off "
                  "the identity it declares.",
        # Every crawled page, because the claim is about every crawled page.
        # Taking the union of the folded findings' own lists would understate
        # it: each of those is already trimmed to five examples.
        affected_pages=[p["url"] for p in pages],
        snippet=action.get("snippet"),
    )
    result.signal("folded_absence_causes", [f["root_cause"] for f in ordered])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-network", action="store_true", help="accepted for interface parity")
    args = parser.parse_args(argv)

    result = run(load_snapshot(args.snapshot))
    result.write(args.out)
    print("{}: {} finding(s)".format(SKILL, len(result.findings)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
