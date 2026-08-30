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
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_SHARED = os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                       "audit-orchestrator", "scripts")
sys.path.insert(0, _SHARED)

# This skill reads the marketplace's shared library. One definition of the
# finding schema, the root-cause vocabulary and the page-type detector keeps six
# skills from drifting apart. The trade-off is that a skill folder lifted out of
# the marketplace on its own cannot run, so say that plainly instead of failing
# with an import traceback.
if not os.path.isfile(os.path.join(_SHARED, "audit_common.py")):
    raise SystemExit(os.linesep.join([
        "Cannot find the shared library that this skill depends on.",
        "  Looked in: " + _SHARED,
        "",
        "This skill belongs to the brand-ai-readiness-audit marketplace and reads",
        "skills/audit-orchestrator/scripts/audit_common.py. Copy or run the whole",
        "marketplace rather than a single skill directory.",
        "",
        "To perform these checks without the marketplace, follow the Procedure",
        "section of this skill's SKILL.md by hand. It states every check in prose",
        "and produces the same findings.",
    ]))

from audit_common import (  # noqa: E402
    CONTENT_TYPES, DEEP_TYPES, SkillResult, find_prices, load_snapshot,
    name_forms, pages_of, pct, sample, truncate,
)

SKILL = "structured-data-audit"

ORG_TYPES = {"organization", "localbusiness", "corporation", "store", "restaurant",
             "ngo", "educationalorganization", "governmentorganization", "onlinestore",
             "professionalservice", "medicalbusiness", "financialservice", "brand"}
# A personal site's identity is a Person, not an Organization. Demanding
# Organization markup from an individual's blog is a false positive, so Person
# satisfies the identity check even though it is not an organisation type.
IDENTITY_TYPES = ORG_TYPES | {"person"}
PRODUCT_TYPES = {"product", "productgroup", "productmodel", "vehicle", "individualproduct"}
ARTICLE_TYPES = {"article", "blogposting", "newsarticle", "techarticle", "report",
                 "scholarlyarticle", "liveblogposting"}
FAQ_TYPES = {"faqpage", "qapage"}

# High-value properties per type. Missing ones are a medium finding, not a
# critical one: the markup exists and works, it is just less useful than it
# could be.
HIGH_VALUE_PROPS = {
    "Organization": ["name", "url", "logo", "description", "sameAs"],
    "Product": ["name", "description", "image", "brand"],
    "Offer": ["price", "priceCurrency", "availability"],
    "Article": ["headline", "datePublished", "dateModified", "author"],
}

# Title and description lengths. These are the ranges that survive truncation
# in search results and in assistant citations.
TITLE_MIN, TITLE_MAX = 15, 90   # 75 flagged ordinary retail titles; real ones run long
DESC_MIN, DESC_MAX = 50, 165

PRICE_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d{1,2})?")


def _price_value(text):
    """The numeric value of a price string, or None if there is not one."""
    match = PRICE_NUMBER_RE.search(str(text or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _format_price(value):
    return "{:.2f}".format(value).rstrip("0").rstrip(".")


def run(snapshot):
    result = SkillResult(SKILL)
    pages = pages_of(snapshot, content_only=True)
    brand = snapshot.get("brand") or {}

    if not pages:
        for name in ("organization-markup", "product-markup", "article-markup",
                     "faq-markup", "breadcrumb-markup", "jsonld-validity",
                     "jsonld-matches-visible-text", "title-and-description",
                     "open-graph-tags", "html-lang-attribute"):
            result.skip(name, "no content pages returned HTTP 200")
        return result

    by_type = defaultdict(list)
    for page in pages:
        by_type[page["page_type"]].append(page)

    _check_jsonld_validity(result, pages)
    _check_organization(result, snapshot, pages, by_type, brand)
    _check_product(result, by_type)
    _check_article(result, by_type)
    _check_faq(result, by_type)
    _check_breadcrumbs(result, pages, by_type)
    _check_website_searchaction(result, by_type)
    _check_consistency(result, pages, brand)
    _check_titles_and_descriptions(result, pages)
    _check_open_graph(result, pages)
    _check_lang(result, pages)
    _check_microdata_only(result, pages)

    result.signal("has_faq_schema", any(_types_on(p) & FAQ_TYPES for p in pages))
    result.signal("has_org_schema", any(_types_on(p) & ORG_TYPES for p in pages))
    result.signal("page_types_seen", sorted(by_type.keys()))
    result.signal("article_pages", len(by_type.get("article", [])))
    return result


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _types_on(page):
    return {t.lower() for t in page.get("jsonld_types") or []}


def _nodes_of(page, wanted):
    out = []
    for node in page.get("jsonld") or []:
        value = node.get("@type")
        values = [value] if isinstance(value, str) else (value or [])
        if any(str(v).split("/")[-1].lower() in wanted for v in values):
            out.append(node)
    return out


def _prop(node, key):
    """Read a property, following one level of nesting and @id-free objects."""
    value = node.get(key)
    if isinstance(value, dict):
        return value.get("name") or value.get("url") or value.get("@id") or ""
    if isinstance(value, list) and value:
        first = value[0]
        return first if isinstance(first, str) else (first.get("name") if isinstance(first, dict) else "")
    return value if value is not None else ""


def _missing_props(node, props):
    return [p for p in props if not _prop(node, p)]


def _site_logo(snapshot, pages):
    for page in pages:
        og_image = (page.get("og") or {}).get("og:image")
        if og_image:
            return og_image
    return snapshot["origin"].rstrip("/") + "/logo.png"


def _site_description(pages):
    home = next((p for p in pages if p["page_type"] == "home"), None)
    for page in filter(None, [home] + list(pages)):
        if page.get("meta_description"):
            return page["meta_description"]
    return ""


def _all_same_as(pages):
    urls = set()
    for page in pages:
        urls.update((page.get("social_profiles") or {}).values())
    return sorted(urls)


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def _check_jsonld_validity(result, pages):
    result.check("jsonld-validity")
    broken = [p for p in pages if p.get("jsonld_errors")]
    if not broken:
        result.skip("jsonld-validity",
                    "every JSON-LD block on the crawled pages parsed as valid JSON")
        return

    first = broken[0]["jsonld_errors"][0]
    result.add(
        id_hint="jsonld-does-not-parse",
        title="{} page(s) contain JSON-LD that does not parse".format(len(broken)),
        severity="high", confidence="high",
        evidence="Example: {} -> {}. Excerpt: {}".format(
            broken[0]["url"], first.get("error"), truncate(first.get("excerpt", ""), 120)),
        mechanism="C", root_cause="invalid-jsonld",
        summary="Fix the malformed JSON-LD so the markup you already wrote is actually readable.",
        how_to_fix=[
            "Paste the failing block into any JSON validator and fix the syntax error.",
            "The usual causes are a trailing comma, an unescaped quote inside a description, "
            "or a template variable that rendered empty.",
            "If the block is generated by a template, escape the values rather than "
            "interpolating them raw.",
            "Re-test with Google's Rich Results Test or the schema.org validator.",
        ],
        effort="low", owner="developer",
        rationale="Mechanism C: invalid JSON-LD is discarded entirely by every consumer. The "
                  "site has paid the cost of adding structured data and receives none of the "
                  "benefit, which is worse than having none, because nobody notices.",
        affected_pages=[p["url"] for p in broken],
    )


def _check_organization(result, snapshot, pages, by_type, brand):
    result.check("organization-markup")
    identity_pages = by_type.get("home", []) + by_type.get("about", []) + by_type.get("contact", [])
    if not identity_pages:
        result.skip("organization-markup",
                    "no home, about or contact page was crawled, so there is no page where "
                    "organisation-level markup would be expected")
        return

    org_nodes = [(p, n) for p in pages for n in _nodes_of(p, ORG_TYPES)]
    person_nodes = [(p, n) for p in pages for n in _nodes_of(p, {"person"})]

    if not org_nodes and person_nodes:
        result.skip("organization-markup",
                    "the site declares a Person rather than an Organization on {}, which is "
                    "the correct identity type for a personal site".format(person_nodes[0][0]["url"]))
        return

    if not org_nodes:
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
                     "Organization-level JSON-LD type.".format(
                         len(pages), ", ".join(sample([p["url"] for p in identity_pages], 3))),
            mechanism="C", root_cause="no-org-schema",
            summary="Add one Organization JSON-LD block to the site template so it appears on "
                    "every page.",
            how_to_fix=[
                "Paste the snippet below into the <head> of your site-wide template.",
                "Replace the placeholder values with the real logo URL and the profile URLs "
                "you actually control.",
                "Keep the `description` identical to the boilerplate you use on LinkedIn and "
                "in your press kit, word for word.",
                "Validate with the schema.org validator, then re-check one page in View Source.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism C: Organization markup is the one place a machine can read the "
                      "brand's identity as data rather than inferring it from prose. Without it, "
                      "who you are is a guess.",
            affected_pages=[p["url"] for p in identity_pages],
            snippet=_org_snippet(snapshot, pages, brand),
        )
        return

    page, node = org_nodes[0]
    missing = _missing_props(node, HIGH_VALUE_PROPS["Organization"])
    if missing:
        result.add(
            id_hint="organization-schema-missing-properties",
            title="Organization markup is present but missing {} high-value propert{}".format(
                len(missing), "y" if len(missing) == 1 else "ies"),
            severity="medium", confidence="high",
            evidence="{} declares {} but omits: {}.".format(
                page["url"], _prop(node, "@type") or "Organization", ", ".join(missing)),
            mechanism="C", root_cause="missing-schema-props",
            summary="Fill in the missing Organization properties, especially `sameAs` and `description`.",
            how_to_fix=[
                "Add the missing properties: {}.".format(", ".join(missing)),
                "`sameAs` should list every profile you control: LinkedIn, Wikipedia or Wikidata, "
                "Crunchbase, GitHub, and your primary social accounts.",
                "`description` should be the same sentence you use everywhere else.",
                "Re-validate after the change.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism D: `sameAs` is how a machine confirms that the company on this "
                      "site is the same company on those profiles. Without it the site is one "
                      "unlinked claim among many.",
            affected_pages=[page["url"]],
            snippet=_org_snippet(snapshot, pages, brand),
        )
    else:
        result.skip("organization-markup",
                    "Organization-level markup is present on {} and carries name, url, logo, "
                    "description and sameAs".format(page["url"]))


def _org_snippet(snapshot, pages, brand):
    same_as = _all_same_as(pages)
    payload = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": brand.get("name") or snapshot["site"],
        "url": snapshot["origin"].rstrip("/") + "/",
        "logo": _site_logo(snapshot, pages),
        "description": _site_description(pages) or "<one sentence: what you do, for whom>",
        "sameAs": same_as or ["https://www.linkedin.com/company/<your-company>",
                              "https://www.wikidata.org/wiki/<your-item>"],
    }
    return '<script type="application/ld+json">\n{}\n</script>'.format(
        json.dumps(payload, indent=2, ensure_ascii=False))


def _check_product(result, by_type):
    result.check("product-markup")
    products = by_type.get("product", [])
    if not products:
        result.skip("product-markup",
                    "no product detail pages were detected on this site, so Product and Offer "
                    "markup is not expected")
        return

    without = [p for p in products if not _nodes_of(p, PRODUCT_TYPES)]
    if without:
        result.add(
            id_hint="no-product-schema",
            title="{} of {} product page(s) have no Product markup".format(
                len(without), len(products)),
            # Leans the right way in the study (23% of unnamed brands versus 6%
            # of named ones) but on too few sites to justify `high`.
            severity="medium", confidence="high",
            evidence="Product pages with no Product JSON-LD: {}.".format(
                ", ".join(sample([p["url"] for p in without], 5))),
            mechanism="C", root_cause="no-product-schema",
            summary="Add Product plus Offer JSON-LD to the product page template.",
            how_to_fix=[
                "Add the snippet below to the product template, populated from the same fields "
                "your page already renders.",
                "`offers.price` must be a plain number with no currency symbol; the symbol goes "
                "in `priceCurrency`.",
                "`availability` must be one of the schema.org values, for example "
                "https://schema.org/InStock.",
                "Because this is one template, the fix covers every product page at once.",
            ],
            effort="medium", owner="developer",
            rationale="Mechanism C: price and availability are exactly the facts a shopper asks "
                      "an assistant for. Stated only as styled text, they are ambiguous; stated "
                      "as Offer properties, they are unambiguous data.",
            affected_pages=[p["url"] for p in without],
            snippet=_product_snippet(products[0]),
        )
        return

    incomplete = []
    for page in products:
        for node in _nodes_of(page, PRODUCT_TYPES):
            offers = node.get("offers")
            offer = offers[0] if isinstance(offers, list) and offers else offers
            if not isinstance(offer, dict):
                incomplete.append((page, "no offers object"))
                break
            missing = _missing_props(offer, HIGH_VALUE_PROPS["Offer"])
            if missing:
                incomplete.append((page, "offer missing " + ", ".join(missing)))
                break

    if incomplete:
        result.add(
            id_hint="product-schema-missing-offer-details",
            title="Product markup is present but the Offer is incomplete on {} page(s)".format(
                len(incomplete)),
            severity="medium", confidence="high",
            evidence="; ".join("{} ({})".format(p["url"], why)
                               for p, why in sorted(incomplete, key=lambda x: x[0]["url"])[:5]),
            mechanism="C", root_cause="missing-schema-props",
            summary="Complete the Offer object with price, priceCurrency and availability.",
            how_to_fix=[
                "Add the missing Offer properties in the product template.",
                "Bind them to the same variables the visible price uses so they cannot drift apart.",
                "Validate one page with the schema.org validator after the change.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism C: a Product without a priced Offer answers 'what is this' but "
                      "not 'what does it cost', which is the question being asked.",
            affected_pages=[p["url"] for p, _ in incomplete],
            snippet=_product_snippet(products[0]),
        )
    else:
        result.skip("product-markup",
                    "all {} detected product page(s) carry Product markup with a complete "
                    "Offer".format(len(products)))


def _product_snippet(page):
    prices = find_prices(page.get("body_text", ""))
    number = ""
    currency = "USD"
    if prices:
        match = PRICE_NUMBER_RE.search(prices[0])
        if match:
            number = match.group(0).replace(",", "")
        if "£" in prices[0]:
            currency = "GBP"
        elif "€" in prices[0]:
            currency = "EUR"
        elif "₹" in prices[0]:
            currency = "INR"
    payload = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": (page.get("headings", {}).get("h1") or [page.get("title", "")])[0] or "<product name>",
        "description": page.get("meta_description") or "<one sentence describing the product>",
        "image": (page.get("og") or {}).get("og:image", "<absolute image URL>"),
        "sku": "<your SKU>",
        "brand": {"@type": "Brand", "name": "<brand name>"},
        "offers": {
            "@type": "Offer",
            "url": page["url"],
            "price": number or "<price as a plain number>",
            "priceCurrency": currency,
            "availability": "https://schema.org/InStock",
        },
    }
    return '<script type="application/ld+json">\n{}\n</script>'.format(
        json.dumps(payload, indent=2, ensure_ascii=False))


def _check_article(result, by_type):
    result.check("article-markup")
    articles = by_type.get("article", [])
    if not articles:
        result.skip("article-markup", "no article or blog post pages were crawled")
        return

    without = [p for p in articles if not _nodes_of(p, ARTICLE_TYPES)]
    if without:
        result.add(
            id_hint="no-article-schema",
            title="{} of {} article page(s) have no Article markup".format(
                len(without), len(articles)),
            severity="medium", confidence="high",
            evidence="Article pages with no Article JSON-LD: {}.".format(
                ", ".join(sample([p["url"] for p in without], 5))),
            mechanism="C", root_cause="no-article-schema",
            summary="Add Article JSON-LD with datePublished, dateModified and author to the post template.",
            how_to_fix=[
                "Add the snippet below to the blog post template.",
                "`dateModified` must update when the post is edited; a static value is worse "
                "than none because it asserts freshness that is not real.",
                "`author` should be a Person with a URL to an author page where possible.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism D: without a machine-readable date, a consumer cannot tell "
                      "whether a post is current, and undated content is discounted against "
                      "dated competitors saying the same thing.",
            affected_pages=[p["url"] for p in without],
            snippet=_article_snippet(articles[0]),
        )
        return

    incomplete = []
    for page in articles:
        for node in _nodes_of(page, ARTICLE_TYPES):
            missing = _missing_props(node, HIGH_VALUE_PROPS["Article"])
            if missing:
                incomplete.append((page, ", ".join(missing)))
            break

    if incomplete:
        result.add(
            id_hint="article-schema-missing-properties",
            title="Article markup is missing dates or author on {} page(s)".format(len(incomplete)),
            severity="medium", confidence="high",
            evidence="; ".join("{} (missing {})".format(p["url"], why)
                               for p, why in sorted(incomplete, key=lambda x: x[0]["url"])[:5]),
            mechanism="D", root_cause="missing-schema-props",
            summary="Populate datePublished, dateModified and author on every article.",
            how_to_fix=[
                "Bind these properties to the post's real publish and edit timestamps in the CMS.",
                "Add an author object naming a real person, linked to an author page.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism D: date and author are the two properties that let a consumer "
                      "decide whether to trust and quote a piece of writing.",
            affected_pages=[p["url"] for p, _ in incomplete],
            snippet=_article_snippet(articles[0]),
        )
    else:
        result.skip("article-markup",
                    "all {} article page(s) carry Article markup with dates and an "
                    "author".format(len(articles)))

    result.signal("articles_without_author", len([
        p for p in articles
        if not any(_prop(n, "author") for n in _nodes_of(p, ARTICLE_TYPES))
    ]))


def _article_snippet(page):
    payload = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": (page.get("headings", {}).get("h1") or [page.get("title", "")])[0] or "<headline>",
        "description": page.get("meta_description") or "<one sentence summary>",
        "datePublished": "<YYYY-MM-DD>",
        "dateModified": "<YYYY-MM-DD, updated on every edit>",
        "author": {"@type": "Person", "name": "<author name>", "url": "<author page URL>"},
        "publisher": {"@type": "Organization", "name": "<brand name>",
                      "logo": {"@type": "ImageObject", "url": "<logo URL>"}},
        "mainEntityOfPage": page["url"],
    }
    return '<script type="application/ld+json">\n{}\n</script>'.format(
        json.dumps(payload, indent=2, ensure_ascii=False))


def _check_faq(result, by_type):
    result.check("faq-markup")
    faqs = by_type.get("faq", [])
    if not faqs:
        result.skip("faq-markup", "no FAQ pages were detected on this site")
        return
    without = [p for p in faqs if not _nodes_of(p, FAQ_TYPES)]
    if not without:
        result.skip("faq-markup", "all detected FAQ pages carry FAQPage markup")
        return

    result.add(
        id_hint="no-faq-schema",
        title="{} FAQ page(s) have no FAQPage markup".format(len(without)),
        severity="medium", confidence="high",
        evidence="Pages that read as an FAQ but declare no FAQPage type: {}.".format(
            ", ".join(sample([p["url"] for p in without], 5))),
        mechanism="C", root_cause="no-faq-schema",
        summary="Wrap the existing questions and answers in FAQPage JSON-LD.",
        how_to_fix=[
            "Add FAQPage markup listing each question and its answer text verbatim.",
            "The answer text in the markup must match the answer on the page; a mismatch is "
            "treated as spam by several consumers.",
            "Phrase the questions the way a person would ask an assistant, not as internal "
            "product headings.",
        ],
        effort="low", owner="developer",
        rationale="Mechanism B: an FAQPage block hands over question-and-answer pairs already "
                  "shaped like the thing an assistant is trying to produce, which makes them "
                  "unusually easy to quote.",
        affected_pages=[p["url"] for p in without],
        snippet=_faq_snippet(without[0]),
    )


def _faq_snippet(page):
    sections = (page.get("sections") or [])[:3]
    entities = []
    for section in sections:
        entities.append({
            "@type": "Question",
            "name": section["heading"],
            "acceptedAnswer": {"@type": "Answer",
                               "text": section["first_paragraph"] or "<the answer, verbatim from the page>"},
        })
    if not entities:
        entities = [{"@type": "Question", "name": "<question as a visitor would ask it>",
                     "acceptedAnswer": {"@type": "Answer", "text": "<the answer, verbatim>"}}]
    payload = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": entities}
    return '<script type="application/ld+json">\n{}\n</script>'.format(
        json.dumps(payload, indent=2, ensure_ascii=False))


def _check_breadcrumbs(result, pages, by_type):
    result.check("breadcrumb-markup")
    deep = [p for p in pages if p["page_type"] in DEEP_TYPES and p.get("depth", 0) >= 1]
    if len(deep) < 3:
        result.skip("breadcrumb-markup",
                    "fewer than 3 deep pages were crawled, so breadcrumb markup would not "
                    "meaningfully change how the site is understood")
        return
    without = [p for p in deep if "breadcrumblist" not in _types_on(p)]
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
            len(without), len(deep), ", ".join(sample([p["url"] for p in without], 5))),
        mechanism="C", root_cause="no-breadcrumb-markup",
        summary="Add BreadcrumbList JSON-LD to deep page templates.",
        how_to_fix=[
            "Add BreadcrumbList markup mirroring the visible breadcrumb trail.",
            "Include the home page as position 1 and the current page as the last item.",
            "If there is no visible breadcrumb, add one; it helps visitors as much as machines.",
        ],
        effort="low", owner="developer",
        rationale="Mechanism C: breadcrumbs state where a page sits in the site's structure, "
                  "which tells a consumer what category the content belongs to without having "
                  "to infer it from the URL.",
        affected_pages=[p["url"] for p in without],
    )


def _check_website_searchaction(result, by_type):
    result.check("website-searchaction-markup")
    homes = by_type.get("home", [])
    if not homes:
        result.skip("website-searchaction-markup", "the homepage was not crawled")
        return
    home = homes[0]
    if not home.get("has_search"):
        result.skip("website-searchaction-markup",
                    "the site has no on-site search, so SearchAction markup would describe a "
                    "capability that does not exist")
        return
    if "website" in _types_on(home):
        result.skip("website-searchaction-markup", "the homepage already declares WebSite markup")
        return

    result.add(
        id_hint="no-website-searchaction",
        title="On-site search exists but is not described in WebSite markup",
        severity="low", confidence="medium",
        evidence="{} contains a search form but declares no WebSite type with a "
                 "potentialAction.".format(home["url"]),
        mechanism="C", root_cause="no-website-schema",
        summary="Add WebSite JSON-LD with a SearchAction to the homepage.",
        how_to_fix=[
            "Add the snippet below to the homepage head.",
            "Replace the target URL with your real search URL pattern.",
        ],
        effort="low", owner="developer",
        rationale="Mechanism C: this declares that the site has a searchable index, which lets "
                  "consumers reach content directly instead of only through crawled links.",
        affected_pages=[home["url"]],
        snippet='<script type="application/ld+json">\n' + json.dumps({
            "@context": "https://schema.org",
            "@type": "WebSite",
            "url": home["url"],
            "potentialAction": {
                "@type": "SearchAction",
                "target": home["url"].rstrip("/") + "/search?q={search_term_string}",
                "query-input": "required name=search_term_string",
            },
        }, indent=2) + "\n</script>",
    )


def _check_consistency(result, pages, brand):
    """Markup that contradicts the visible page is worse than missing markup.

    A missing fact leaves a machine uncertain. A contradictory one teaches it
    something false with the confidence that structured data carries.
    """
    result.check("jsonld-matches-visible-text")
    conflicts = []

    for page in pages:
        text = page.get("body_text", "")

        for node in _nodes_of(page, PRODUCT_TYPES):
            offers = node.get("offers")
            offer = offers[0] if isinstance(offers, list) and offers else offers
            if not isinstance(offer, dict):
                continue
            declared = str(_prop(offer, "price") or "").strip()
            if not declared:
                continue
            visible = find_prices(text, limit=8)
            if not visible:
                continue
            declared_number = _price_value(declared)
            visible_numbers = {v for v in (_price_value(p) for p in visible) if v is not None}
            # Compare numerically: "480.00" in markup and "$480" on the page are
            # the same price, and a string comparison would call them a conflict.
            if declared_number is not None and visible_numbers and not any(
                    abs(declared_number - v) < 0.01 for v in visible_numbers):
                conflicts.append((page, "Offer price {} is not among the prices shown on the "
                                        "page ({})".format(
                                            declared,
                                            ", ".join(_format_price(v) for v in sorted(visible_numbers)[:4]))))

        for node in _nodes_of(page, ORG_TYPES):
            declared = str(_prop(node, "name") or "").strip()
            if not declared or len(declared) < 3:
                continue
            haystack = "{} {} {}".format(page.get("title", ""), text[:4000],
                                         " ".join(page.get("headings", {}).get("h1") or []))
            # A formal name in markup and a trading name in the prose is normal
            # and correct. The conflict is when *no* form of the declared name
            # appears, not when the legal suffix is missing from the sentence.
            if not any(form.lower() in haystack.lower() for form in name_forms(declared)):
                conflicts.append((page, 'Organization name "{}" does not appear in the page '
                                        'title, headings or body text, in any form'.format(declared)))

    if not conflicts:
        result.skip("jsonld-matches-visible-text",
                    "the names and prices declared in JSON-LD match what the pages display")
        return

    result.add(
        id_hint="jsonld-contradicts-visible-text",
        title="Structured data contradicts what the page actually says",
        severity="high", confidence="medium",
        evidence="{} conflict(s) found. Examples: {}.".format(
            len(conflicts),
            "; ".join("{}: {}".format(p["url"], why)
                      for p, why in sorted(conflicts, key=lambda x: x[0]["url"])[:4])),
        mechanism="C", root_cause="schema-text-mismatch",
        summary="Bind the structured data to the same source as the visible content so the two "
                "cannot drift apart.",
        how_to_fix=[
            "For each conflict, decide which value is correct and correct the other.",
            "In the template, populate the JSON-LD from the same variables that render the "
            "visible price and name; hand-written markup drifts within weeks.",
            "Where a page shows several prices (a range, a sale price), declare the one a buyer "
            "would actually pay, and use `highPrice`/`lowPrice` on an AggregateOffer for ranges.",
        ],
        effort="medium", owner="developer",
        rationale="Mechanism C: structured data is treated as an assertion by the site owner. "
                  "A wrong assertion is repeated confidently, so a contradiction does more "
                  "damage than an omission.",
        affected_pages=[p["url"] for p, _ in conflicts],
    )


def _check_titles_and_descriptions(result, pages):
    result.check("title-and-description")
    missing_title = [p for p in pages if not p.get("title")]
    missing_desc = [p for p in pages if not p.get("meta_description")]

    titles = Counter(p["title"] for p in pages if p.get("title"))
    duplicate_titles = [t for t, n in titles.items() if n > 1]
    descriptions = Counter(p["meta_description"] for p in pages if p.get("meta_description"))
    duplicate_descriptions = [d for d, n in descriptions.items() if n > 1]

    problems = []
    if missing_title:
        problems.append("{} page(s) have no <title>".format(len(missing_title)))
    # One page without a description is an oversight, not a template fault.
    if len(missing_desc) >= max(2, len(pages) * 0.25):
        problems.append("{} of {} page(s) have no meta description".format(
            len(missing_desc), len(pages)))
    if duplicate_titles:
        problems.append("{} title(s) are reused across pages".format(len(duplicate_titles)))
    if duplicate_descriptions:
        problems.append("{} meta description(s) are reused".format(len(duplicate_descriptions)))

    # Length is only worth mentioning when it is a template-wide habit. One or
    # two titles a few characters over the ideal range is not a defect, and
    # reporting it makes the whole report look like a checklist.
    long_titles = [p for p in pages if p.get("title") and len(p["title"]) > TITLE_MAX]
    short_titles = [p for p in pages if p.get("title") and len(p["title"]) < TITLE_MIN]
    off_length = long_titles + short_titles
    if len(off_length) >= max(4, len(pages) * 0.6):
        problems.append("{} of {} title(s) fall outside {}-{} chars".format(
            len(off_length), len(pages), TITLE_MIN, TITLE_MAX))

    if not problems:
        result.skip("title-and-description",
                    "every crawled page has a unique title and meta description of a "
                    "reasonable length")
        return

    affected = {p["url"] for p in missing_title + missing_desc}
    for page in pages:
        if page.get("title") in duplicate_titles or page.get("meta_description") in duplicate_descriptions:
            affected.add(page["url"])

    severe = bool(missing_title or missing_desc or duplicate_titles)
    result.add(
        id_hint="title-and-description-hygiene",
        title="Page titles and meta descriptions need attention",
        severity="medium" if severe else "low", confidence="high",
        evidence="{}. Affected pages include: {}.".format(
            "; ".join(problems), ", ".join(sample(sorted(affected) or [p["url"] for p in pages], 5))),
        mechanism="B", root_cause="meta-hygiene",
        summary="Give every page a unique title and a meta description that states the page's "
                "specific fact.",
        how_to_fix=[
            "Write a unique <title> of {}-{} characters for each page, leading with the "
            "specific subject rather than the brand name.".format(TITLE_MIN, TITLE_MAX),
            "Write a unique meta description of {}-{} characters that states the concrete "
            "fact on the page (the price, the location, the answer).".format(DESC_MIN, DESC_MAX),
            "Duplicated titles usually come from a template that omits the page variable; fix "
            "the template rather than each page.",
        ],
        effort="medium", owner="content owner",
        rationale="Mechanism B: the title and description are the shortest description of a "
                  "page a consumer sees, and are often quoted directly. Duplicates make "
                  "different pages look like the same page.",
        affected_pages=sorted(affected),
    )


def _check_open_graph(result, pages):
    result.check("open-graph-tags")
    incomplete = [p for p in pages
                  if not all((p.get("og") or {}).get(k) for k in ("og:title", "og:description", "og:image"))]
    if not incomplete:
        result.skip("open-graph-tags", "every crawled page carries og:title, og:description and og:image")
        return
    if len(incomplete) < len(pages) * 0.4:
        result.skip("open-graph-tags",
                    "{} of {} pages are missing an Open Graph tag, below the threshold where "
                    "this is a template problem".format(len(incomplete), len(pages)))
        return

    result.add(
        id_hint="incomplete-open-graph-tags",
        title="Open Graph tags are missing on {} of {} pages".format(len(incomplete), len(pages)),
        severity="low", confidence="high",
        evidence="Pages missing at least one of og:title, og:description or og:image: {}.".format(
            ", ".join(sample([p["url"] for p in incomplete], 5))),
        mechanism="B", root_cause="open-graph-incomplete",
        summary="Add og:title, og:description and og:image to the site template.",
        how_to_fix=[
            "Add the three Open Graph tags to your base template, populated from the page's "
            "existing title and meta description.",
            "og:image should be an absolute URL to an image at least 1200x630 pixels.",
        ],
        effort="low", owner="developer",
        rationale="Mechanism B: Open Graph tags are what every sharing surface and several "
                  "answer surfaces read to build a preview card, so they decide how the page "
                  "looks wherever it is repeated.",
        affected_pages=[p["url"] for p in incomplete],
    )


def _check_lang(result, pages):
    result.check("html-lang-attribute")
    without = [p for p in pages if not p.get("lang")]
    if not without:
        result.skip("html-lang-attribute", "every crawled page declares a lang attribute")
        return
    result.add(
        id_hint="missing-html-lang",
        title="{} page(s) do not declare a language".format(len(without)),
        severity="low", confidence="high",
        evidence="Pages with no lang attribute on <html>: {}.".format(
            ", ".join(sample([p["url"] for p in without], 5))),
        mechanism="C", root_cause="missing-lang",
        summary='Add lang to the <html> element, for example <html lang="en">.',
        how_to_fix=[
            'Set the lang attribute in your base template, for example <html lang="en">.',
            "Use a regional subtag where it matters, such as en-GB or pt-BR.",
        ],
        effort="low", owner="developer",
        rationale="Mechanism C: without a declared language a consumer has to guess, which "
                  "affects whether the page is surfaced for a given audience at all.",
        affected_pages=[p["url"] for p in without],
    )


def _check_microdata_only(result, pages):
    result.check("microdata-and-rdfa")
    microdata_only = [p for p in pages
                      if not (p.get("jsonld") or []) and p.get("microdata_count", 0) >= 3]
    if not microdata_only:
        result.skip("microdata-and-rdfa",
                    "no page relies on microdata or RDFa in place of JSON-LD")
        return
    result.add(
        id_hint="markup-uses-microdata-not-jsonld",
        title="{} page(s) use inline microdata instead of JSON-LD".format(len(microdata_only)),
        severity="low", confidence="medium",
        evidence="Pages with microdata attributes but no JSON-LD block: {}.".format(
            ", ".join(sample([p["url"] for p in microdata_only], 5))),
        mechanism="C", root_cause="microdata-only",
        summary="Migrate the markup to JSON-LD, which is the format consumers support most consistently.",
        how_to_fix=[
            "Express the same entities as a JSON-LD block in the page head.",
            "Microdata can stay in place during the transition; the two do not conflict.",
        ],
        effort="medium", owner="developer",
        rationale="Mechanism C: microdata is valid but sits inside the markup, so it breaks "
                  "whenever the template changes. JSON-LD is a single self-contained block and "
                  "survives redesigns.",
        affected_pages=[p["url"] for p in microdata_only],
    )


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
