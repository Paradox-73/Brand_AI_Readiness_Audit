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

from audit_common import (  # noqa: E402
    CONTENT_TYPES, DEEP_TYPES, example_urls, find_prices, is_multi_location,
    is_question_heading,
    BRANCH_PAGE_MINIMUM, fold_declared_duplicates,
    load_snapshot, name_forms, pages_of, pages_with_their_own_address, pct,
    plural, price_value, PRICE_NUMBER_RE, profile_names_brand, sample,
    sentences, SkillResult, truncate, written_in_the_same_script
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

    if not pages:
        for name in ("organization-markup", "per-location-markup",
                     "product-markup", "article-markup",
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
    _check_per_location_markup(result, snapshot, pages)
    _check_product(result, by_type, brand)
    _check_article(result, by_type)
    _check_faq(result, by_type)
    _check_breadcrumbs(result, pages, by_type)
    _check_website_searchaction(result, by_type)
    _check_consistency(result, pages, brand)
    _check_titles_and_descriptions(result, all_pages)
    _check_open_graph(result, all_pages)
    _check_lang(result, all_pages)
    _check_microdata_only(result, all_pages)

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
    if direct:
        return direct
    for holder in _ALTERNATIVE_HOMES.get(key, ()):
        nested = node.get(holder)
        if isinstance(nested, dict):
            nested = [nested]
        for candidate in nested or []:
            if isinstance(candidate, dict) and _prop(candidate, key):
                return _prop(candidate, key)
    return ""


def _missing_props(node, props):
    return [p for p in props if not _prop_anywhere(node, p)]


# schema.org types that mean "somewhere a customer physically goes".
_VISITABLE_TYPES = {"localbusiness", "store", "restaurant", "hotel", "cafe",
                    "bakery", "bar", "clothingstore", "groceryStore".lower(),
                    "healthandbeautybusiness", "medicalclinic", "dentist",
                    "autorepair", "professionalservice", "foodestablishment"}


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
    visitable type somewhere, it publishes opening hours, or the crawl found
    location pages, which is what a business with premises has.
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
    location_pages = 0
    declares_visitable = False
    for page in (snapshot.get("pages") or []):
        if page.get("page_type") == "location":
            location_pages += 1
        for node in page.get("jsonld") or []:
            types = node.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if not {str(t).split("/")[-1].lower() for t in types} & _VISITABLE_TYPES:
                continue
            declares_visitable = True
            address = node.get("address")
            if isinstance(address, list):
                address = address[0] if address else None
            if isinstance(address, dict):
                address_forms.append(json.dumps(address, sort_keys=True))

    if len(set(address_forms)) > 1 or location_pages > 1:
        return "Organization"
    if declares_visitable or facts.get("opening_hours") or location_pages == 1:
        return "LocalBusiness"
    return "Organization"


def _site_logo(snapshot, pages):
    """The site's logo URL, or None if we never saw one.

    This used to invent `<origin>/logo.png` when no og:image was found, and put
    it in a snippet labelled paste-ready beside `name` and `url`, which were
    discovered. Nothing marked one as real and the other as a guess. On a site
    with no images at all that shipped a 404 into structured data, which is
    worse than an obvious placeholder because it looks finished.
    """
    for page in pages:
        og_image = (page.get("og") or {}).get("og:image")
        if og_image:
            return og_image
    for page in pages:
        for image in (page.get("images") or []):
            src = image.get("src") if isinstance(image, dict) else image
            if src and "logo" in str(src).lower():
                return src
    return None


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


def _brand_definition(pages, brand):
    """The site's own sentence defining itself, if it has written one.

    `fact-extractability-audit` looks for exactly this - "<Brand> is a ..." -
    and the audit prints it in the report. The Organization snippet was still
    emitting `<one sentence: what you do, for whom>` while the answer sat two
    sections above it.

    Read only from pages that speak for the business. Falling through to any
    page that happened to contain the brand name and a copula published a
    product blurb as the company's own description.
    """
    name = (brand or {}).get("name") or ""
    if not name:
        return ""
    lowered = name.lower()
    ordered = [page for page_type in _WHOLE_BRAND_PAGE_TYPES
               for page in pages if page.get("page_type") == page_type]
    for page in ordered:
        for paragraph in (page.get("paragraphs") or [])[:12]:
            for sentence in sentences(paragraph):
                text = sentence.strip()
                if 40 <= len(text) <= 300 and lowered in text.lower() and \
                        re.search(r"\b(is|are|makes|provides|offers|builds|designs)\b",
                                  text, re.I):
                    return text
    return ""


def _site_description(pages):
    """The site's own description of itself, from a page that speaks for it.

    Same provenance rule as `_brand_definition`, and the same reason: falling
    through to any page with a meta description would put a single product's
    marketing line into the site-wide Organization block on exactly the sites
    whose homepage has no description - which is every site this finding
    fires on.
    """
    for page_type in _WHOLE_BRAND_PAGE_TYPES:
        for page in pages:
            if page.get("page_type") == page_type and page.get("meta_description"):
                return page["meta_description"]
    return ""


def _all_same_as(pages, brand=None):
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
    # One URL, one entry. A city's footer links its Facebook page with and
    # without a trailing slash, and both went into `sameAs` - so the snippet
    # asserted the same account twice and the profile count read one higher
    # than the number of accounts. Two strings, one profile.
    urls = {}
    for page in pages:
        for url in (page.get("social_profiles") or {}).values():
            urls.setdefault(re.sub(r"[?#].*$", "", url).rstrip("/").lower(), url)
    urls = set(urls.values())
    key = re.sub(r"[^a-z0-9]", "", str((brand or {}).get("name") or "").lower())
    if len(key) < 3:
        return sorted(urls)
    return sorted(u for u in urls if _handle_matches_brand(u, key))


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
        title="{} JSON-LD that does not parse".format(
            plural(len(broken), "page contains", "pages contain")),
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
        rationale="Invalid JSON-LD is discarded entirely by every consumer. The "
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
                         len(pages), ", ".join(example_urls([p["url"] for p in identity_pages], 3))),
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
            rationale="Organization markup is the one place a machine can read the "
                      "brand's identity as data rather than inferring it from prose. Without it, "
                      "who you are is a guess.",
            affected_pages=[p["url"] for p in identity_pages],
            snippet=_org_snippet(snapshot, pages, brand),
        )
        return

    page, node = org_nodes[0]
    missing = _missing_props(node, HIGH_VALUE_PROPS["Organization"])

    # A brand that links to no profiles anywhere cannot fill `sameAs`, and this
    # check only ever tested that the key was present - so leaving our own
    # placeholder `.../<your-company>` in the snippet passed, and deleting it,
    # which is the honest thing to do, failed. The tool rewarded pasting
    # placeholder text and penalised removing it. If the site declares no
    # profiles at all, the absence of `sameAs` is not the finding to raise here;
    # freshness-corroboration-audit already reports having none, which is the
    # actual problem and has a different fix.
    if "sameAs" in missing and not _all_same_as(pages):
        missing = [m for m in missing if m != "sameAs"]

    # Same reasoning for a logo we could not find: we no longer invent one, so
    # we do not then mark the site down for not having the one we invented.
    if "logo" in missing and not _site_logo(snapshot, pages):
        missing = [m for m in missing if m != "logo"]

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
            rationale="`sameAs` is how a machine confirms that the company on this "
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
    """
    same_as = _all_same_as(pages, brand)
    facts = _contact_facts_for_snippet(pages, is_multi_location(snapshot))

    payload = {
        "@context": "https://schema.org",
        "@type": _identity_type(snapshot, pages, facts),
        "name": brand.get("name") or snapshot["site"],
        "url": snapshot["origin"].rstrip("/") + "/",
    }
    logo = _site_logo(snapshot, pages)
    if logo:
        payload["logo"] = logo
    description = _site_description(pages) or _brand_definition(pages, brand)
    if description:
        payload["description"] = description
    if facts.get("address"):
        payload["address"] = {"@type": "PostalAddress", "streetAddress": facts["address"]}
        for key, field in (("locality", "addressLocality"), ("region", "addressRegion"),
                           ("postal_code", "postalCode"), ("country", "addressCountry")):
            if facts.get(key):
                payload["address"][field] = facts[key]
    if facts.get("telephone"):
        payload["telephone"] = facts["telephone"]
    if same_as:
        payload["sameAs"] = same_as
    return '<script type="application/ld+json">\n{}\n</script>'.format(
        json.dumps(payload, indent=2, ensure_ascii=False))


def _contact_facts_for_snippet(pages, branches=False):
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
        if not branches and not out.get("telephone") and facts.get("declared_phones"):
            out["telephone"] = facts["declared_phones"][0]
        if facts.get("has_address"):
            out["has_address"] = True

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
    declared = _declared_address(pages, whole_brand_only=branches)
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
    value = node.get("@type")
    values = [value] if isinstance(value, str) else (value or [])
    declared = {str(v).split("/")[-1].lower() for v in values}
    if declared & _VISITABLE_TYPES:
        return False
    return bool(declared & _WHOLE_BRAND_JSONLD_TYPES)


def _declared_address(pages, whole_brand_only=False):
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
    """
    for page in pages:
        for node in page.get("jsonld") or []:
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
    types = offer.get("@type")
    types = [types] if isinstance(types, str) else (types or [])
    if any(str(t).split("/")[-1].lower() == "aggregateoffer" for t in types):
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



def _check_per_location_markup(result, snapshot, pages):
    """A page describing one branch should say so in markup.

    The single highest-value structured-data fix a multi-location business can
    make, and there was no check for it. A pizza chain with sixty branches got
    a report that never used the word `LocalBusiness`: its branch pages each
    print a street and a postcode in plain text, declare no place markup at
    all, and so were invisible to every signal that would have identified the
    site as a chain. The markup that would have found the chain was the markup
    the chain was missing.

    `pages_with_their_own_address` reads the addresses off the pages instead,
    which is evidence the site cannot fail to provide.
    """
    result.check("per-location-markup")
    branch_pages = pages_with_their_own_address(snapshot)
    if len(branch_pages) < BRANCH_PAGE_MINIMUM:
        result.skip("per-location-markup",
                    "{} page(s) carry an address of their own, too few to treat this as a "
                    "business with separate places to visit".format(len(branch_pages)))
        return

    unmarked = []
    for page in branch_pages:
        declared = {str(t).split("/")[-1].lower() for t in page.get("jsonld_types") or []}
        if not declared & _VISITABLE_TYPES:
            unmarked.append(page)
    if not unmarked:
        result.skip("per-location-markup",
                    "all {} page(s) carrying their own address declare a visitable "
                    "schema.org type".format(len(branch_pages)))
        return

    example = unmarked[0]
    facts = example.get("contact_facts") or {}
    result.add(
        id_hint="branch-pages-carry-no-location-markup",
        title="{} describe a place to visit without saying so in markup".format(
            plural(len(unmarked), "page")),
        severity="high" if len(unmarked) >= 5 else "medium", confidence="high",
        evidence="{} of {} page(s) that print their own street address declare no "
                 "LocalBusiness, Store, Restaurant or similar visitable type. Examples: "
                 "{}. Each of those addresses is different from the others, which is what "
                 "a business with separate branches looks like.".format(
                     len(unmarked), len(branch_pages),
                     ", ".join(example_urls([p["url"] for p in unmarked], 3))),
        mechanism="C", root_cause="no-org-schema",
        summary="Add one LocalBusiness block per branch page, each with that branch's own "
                "address, phone and opening hours.",
        how_to_fix=[
            "On each branch page, add a LocalBusiness (or the closest subtype: Store, "
            "Restaurant, Dentist) JSON-LD block naming that branch.",
            "Give each one its own `address`, `telephone` and `openingHoursSpecification`. "
            "Do not reuse the head-office values - the point is that they differ.",
            "Set `parentOrganization` on each branch to the Organization block in your "
            "site-wide template, so a machine can see they are one business.",
            "Keep the site-wide Organization block as it is. It describes the company; these "
            "describe the places.",
        ],
        effort="medium", owner="developer",
        rationale="\"Near me\" and \"which branch\" questions are answered from per-place "
                  "markup. A chain with one company-level block is one result; a chain with a "
                  "block per branch is a result in every town it operates in.",
        affected_pages=[p["url"] for p in unmarked],
        snippet=json.dumps({
            "@context": "https://schema.org",
            "@type": "LocalBusiness",
            "name": "{} - <branch name>".format(
                (snapshot.get("brand") or {}).get("name") or "<brand>"),
            "url": example["url"],
            "address": {
                "@type": "PostalAddress",
                "streetAddress": facts.get("street_hint") or "<street>",
                "postalCode": facts.get("postcode_hint") or "<postal code>",
            },
            "telephone": (facts.get("declared_phones") or ["<phone>"])[0],
            "parentOrganization": {
                "@type": "Organization",
                "name": (snapshot.get("brand") or {}).get("name") or "<brand>",
            },
        }, indent=2),
    )

def _check_product(result, by_type, brand):
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
            title="{} of {} have no Product markup".format(
                len(without), plural(len(products), "product page")),
            # Leans the right way in the study (23% of unnamed brands versus 6%
            # of named ones) but on too few sites to justify `high`.
            severity="medium", confidence="high",
            evidence="Product pages with no Product JSON-LD: {}.".format(
                ", ".join(sample([p["url"] for p in without]))),
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
            rationale="Price and availability are exactly the facts a shopper asks "
                      "an assistant for. Stated only as styled text, they are ambiguous; stated "
                      "as Offer properties, they are unambiguous data.",
            affected_pages=[p["url"] for p in without],
            snippet=_product_snippet(products[0], (brand or {}).get("name") or ""),
        )
        return

    incomplete = []
    for page in products:
        for node in _nodes_of(page, PRODUCT_TYPES):
            offer = _offer_of(node)
            if not isinstance(offer, dict):
                incomplete.append((page, "no offers object"))
                break
            missing = _missing_props(offer, _offer_props(offer))
            if missing:
                incomplete.append((page, "offer missing " + ", ".join(missing)))
                break

    if incomplete:
        result.add(
            id_hint="product-schema-missing-offer-details",
            title="Product markup is present but the Offer is incomplete on {}".format(
                plural(len(incomplete), "page")),
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
            rationale="A Product without a priced Offer answers 'what is this' but "
                      "not 'what does it cost', which is the question being asked.",
            affected_pages=[p["url"] for p, _ in incomplete],
            snippet=_product_snippet(products[0], (brand or {}).get("name") or ""),
        )
    else:
        result.skip("product-markup",
                    "all {} detected product page(s) carry Product markup with a complete "
                    "Offer".format(len(products)))


def _product_snippet(page, brand_name=""):
    """One product's markup, filled in from that product's own page.

    `image` and `sku` used to be `<absolute image URL>` and `<your SKU>`. On a
    site with no images at all the first is unsatisfiable, and a two-person
    pottery may have no SKUs - so the snippet demanded two values that could
    not be supplied, while our own checks never looked at either. A key we
    cannot fill and do not grade does not belong in paste-ready code.
    """
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
        "offers": {
            "@type": "Offer",
            "url": page["url"],
            "price": number or "<price as a plain number>",
            "priceCurrency": currency,
            "availability": "https://schema.org/InStock",
        },
    }
    # Optional keys only when the site actually supplies them. A key we cannot
    # fill and do not grade does not belong in code labelled paste-ready.
    if page.get("meta_description"):
        payload["description"] = page["meta_description"]
    image = (page.get("og") or {}).get("og:image")
    if image:
        payload["image"] = image
    if brand_name:
        payload["brand"] = {"@type": "Brand", "name": brand_name}

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
            title="{} of {} have no Article markup".format(
                len(without), plural(len(articles), "article page")),
            severity="medium", confidence="high",
            evidence="Article pages with no Article JSON-LD: {}.".format(
                ", ".join(example_urls([p["url"] for p in without]))),
            mechanism="C", root_cause="no-article-schema",
            summary="Add Article JSON-LD with datePublished, dateModified and author to the post template.",
            how_to_fix=[
                "Add the snippet below to the blog post template.",
                "`dateModified` must update when the post is edited; a static value is worse "
                "than none because it asserts freshness that is not real.",
                "`author` should be a Person with a URL to an author page where possible.",
            ],
            effort="low", owner="developer",
            rationale="Without a machine-readable date, a consumer cannot tell "
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
            title="Article markup is missing dates or author on {}".format(
                plural(len(incomplete), "page")),
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
            rationale="Date and author are the two properties that let a consumer "
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
        title="{} no FAQPage markup".format(
            plural(len(without), "FAQ page has", "FAQ pages have")),
        severity="medium", confidence="high",
        evidence="Pages that read as an FAQ but declare no FAQPage type: {}.".format(
            ", ".join(example_urls([p["url"] for p in without]))),
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
        rationale="An FAQPage block hands over question-and-answer pairs already "
                  "shaped like the thing an assistant is trying to produce, which makes them "
                  "unusually easy to quote.",
        affected_pages=[p["url"] for p in without],
        snippet=_faq_snippet(without[0]),
    )


# Enough for any real FAQ page. It used to be three, undisclosed, so a page
# with five questions produced markup covering three - and the FAQ check then
# passed that markup, so the truncation was never caught by anything.
FAQ_SNIPPET_MAX = 20


def _faq_snippet(page):
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
        heading that was paired with an unrelated paragraph as its answer.

    All three are the pattern Google's own FAQ guidance names as ineligible,
    and the finding directly above the snippet warns the reader that a
    mismatch between markup and page "is treated as spam by several
    consumers". So only headings that are actually questions go in, and a page
    with fewer than two of them gets the placeholder rather than a fabrication.
    """
    sections = [s for s in (page.get("sections") or [])
                if is_question_heading(s.get("heading"))][:FAQ_SNIPPET_MAX]
    entities = []
    for section in sections:
        answer = (section.get("first_paragraph") or "").strip()
        entities.append({
            "@type": "Question",
            "name": section["heading"],
            "acceptedAnswer": {"@type": "Answer",
                               "text": answer or "<the answer, verbatim from the page>"},
        })
    if len(entities) < 2:
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
            len(without), len(deep), ", ".join(example_urls([p["url"] for p in without]))),
        mechanism="C", root_cause="no-breadcrumb-markup",
        summary="Add BreadcrumbList JSON-LD to deep page templates.",
        how_to_fix=[
            "Add BreadcrumbList markup mirroring the visible breadcrumb trail.",
            "Include the home page as position 1 and the current page as the last item.",
            "If there is no visible breadcrumb, add one; it helps visitors as much as machines.",
        ],
        effort="low", owner="developer",
        rationale="Breadcrumbs state where a page sits in the site's structure, "
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
        rationale="This declares that the site has a searchable index, which lets "
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
            offer = _offer_of(node)
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
            # A Latin-script name cannot appear in an Arabic page, and its
            # absence there says nothing about the markup. One foundation's
            # Arabic edition was told at high severity that its structured
            # data contradicted the page, about markup that was correct and a
            # page that was correct.
            if not written_in_the_same_script(declared, haystack):
                continue
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
        rationale="Structured data is treated as an assertion by the site owner. "
                  "A wrong assertion is repeated confidently, so a contradiction does more "
                  "damage than an omission.",
        affected_pages=[p["url"] for p, _ in conflicts],
    )


def _check_titles_and_descriptions(result, pages):
    result.check("title-and-description")
    # A URL and its own declared canonical target are one page, and the site is
    # the authority on that. Counting both reported two of eleven "duplicate
    # title" pairs on a broadcaster that were a URL and its canonical, and
    # inflated the denominator underneath them at the same time.
    pages = fold_declared_duplicates(pages)
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
            "; ".join(problems), ", ".join(example_urls(sorted(affected) or [p["url"] for p in pages]))),
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
        rationale="The title and description are the shortest description of a "
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
            ", ".join(example_urls([p["url"] for p in incomplete]))),
        mechanism="B", root_cause="open-graph-incomplete",
        summary="Add og:title, og:description and og:image to the site template.",
        how_to_fix=[
            "Add the three Open Graph tags to your base template, populated from the page's "
            "existing title and meta description.",
            "og:image should be an absolute URL to an image at least 1200x630 pixels.",
        ],
        effort="low", owner="developer",
        rationale="Open Graph tags are what every sharing surface and several "
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
        title="{} not declare a language".format(
            plural(len(without), "page does", "pages do")),
        severity="low", confidence="high",
        evidence="Pages with no lang attribute on <html>: {}.".format(
            ", ".join(example_urls([p["url"] for p in without]))),
        mechanism="C", root_cause="missing-lang",
        summary='Add lang to the <html> element, for example <html lang="en">.',
        how_to_fix=[
            'Set the lang attribute in your base template, for example <html lang="en">.',
            "Use a regional subtag where it matters, such as en-GB or pt-BR.",
        ],
        effort="low", owner="developer",
        rationale="Without a declared language a consumer has to guess, which "
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
        title="{} inline microdata instead of JSON-LD".format(
            plural(len(microdata_only), "page uses", "pages use")),
        severity="low", confidence="medium",
        evidence="Pages with microdata attributes but no JSON-LD block: {}.".format(
            ", ".join(example_urls([p["url"] for p in microdata_only]))),
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
