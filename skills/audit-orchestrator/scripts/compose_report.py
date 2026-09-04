#!/usr/bin/env python3
"""Merge sub-skill findings into report.json and report.md.

Responsibilities that belong to the entrypoint and nowhere else:
  - deduplicate observations two skills both noticed
  - assign stable finding IDs
  - compute priority as severity x reach / effort
  - add proactive recommendations whose conditions are met
  - simulate which sentence an assistant would quote from each key page
  - render the human-readable report

Usage:
    python compose_report.py --snapshot snapshot.json \
        --findings a.json b.json ... --out-json report.json --out-md report.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit_common import (
    sitemap_scope, sitemap_total_phrase, words_are_separated,  # noqa: E402
    EFFORT_DIVISOR, MECHANISMS, SEVERITY_RANK, SEVERITY_WEIGHT, VERSION,
    load_snapshot, pages_of, plural, read_json, sentences, truncate, write_json,
)

# Sub-skill order. Earlier skills own overlapping observations, so when two
# skills see the same root cause on the same pages the earlier one's wording
# and fix survive and the later one's evidence is merged into it.
SKILL_ORDER = [
    "crawl-access-audit",
    "render-readability-audit",
    "structured-data-audit",
    "fact-extractability-audit",
    "freshness-corroboration-audit",
    "engagement-audit",
]

# Priority and severity are different questions - how bad is this, versus what
# should you do first - and they answered in the same four words. A report that
# lists a finding under "Medium findings" and then says "Priority critical" two
# lines later reads as a contradiction, whatever the arithmetic underneath. The
# bands now say what they mean as an instruction.
PRIORITY_BANDS = ((2.0, "do first"), (1.0, "do soon"), (0.45, "schedule"), (0.0, "when convenient"))

# Measured in the within-category study: brands assistants name carry a date on
# 34% of crawled pages, comparable brands they ignore on 17%. The floor sits
# between the two. See references/cited-vs-uncited-study.md.
DATE_COVERAGE_FLOOR = 0.25

EFFORT_TIME = {
    "low": "under an hour",
    "medium": "half a day to a day",
    "high": "several days of development time",
}

NUMERIC_RE = re.compile(r"\d|[$€£¥₹]")
# A line that is a price, and a line that is a way to reach somebody. Both are
# shorter than a sentence and both are what people ask assistants for.
PRICE_LINE_RE = re.compile(
    r"[$€£¥₹]\s?\d|\b\d[\d,.]*\s?(?:USD|EUR|GBP|INR|AUD|CAD|SGD|AED|JPY)\b", re.I)
CONTACT_LINE_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]{2,}"
    r"|\+\d[\d\s().-]{7,}\d"
    r"|\b(?:call|email|phone|contact) (?:us|our)\b", re.I)
# How short a sentence may be and still be worth quoting. The second is for
# scripts that write without spaces between words, where the same amount of
# meaning fits in far fewer characters.
QUOTE_MIN_CHARS = 40
QUOTE_MIN_CHARS_UNSPACED = 16
CONCRETE_RE = re.compile(
    r"\d|[$€£¥₹]|\b(?:is|are)\s+(?:an?|the)\b|\b(?:means|refers to|defined as)\b", re.I)


# --------------------------------------------------------------------------
# Merge and score
# --------------------------------------------------------------------------

def load_skill_results(paths):
    results = []
    for path in paths:
        data = read_json(path)
        results.append(data)
    order = {name: i for i, name in enumerate(SKILL_ORDER)}
    results.sort(key=lambda r: order.get(r.get("skill", ""), 99))
    return results


def dedupe(results):
    """Collapse findings that describe the same root cause on the same pages.

    Two skills legitimately notice one problem from different angles. Reporting
    it twice makes the report look padded and inflates the severity counts,
    so the earlier skill keeps the finding and the later one's evidence is
    appended to it.

    On a healthy build this never fires, and that is the point rather than a
    defect. `test_no_root_cause_has_two_owners` fails the build if any two
    skills emit the same root cause, so the cross-skill branch below is
    unreachable while the decomposition holds. It is a backstop against a
    future skill reaching into another's territory, not a working feature, and
    `merged_duplicates` being empty in every report is the evidence that the
    six skills are still separate. `test_dedupe_is_a_backstop` pins both
    halves of that: the branch is exercised on synthetic input, and the
    production impossibility is asserted.
    """
    merged = {}
    order = []
    duplicates = []
    for result in results:
        skill = result.get("skill", "unknown")
        for finding in result.get("findings", []):
            key = (finding["mechanism"], finding["root_cause"],
                   tuple(sorted(finding.get("affected_pages") or [])))
            existing = merged.get(key)
            # Merge only across skills. A single skill emitting several findings
            # that share a root cause is doing so deliberately - robots.txt can
            # block answer crawlers, training crawlers and content paths, and
            # those are three different decisions with three different fixes.
            if existing is not None and existing["detected_by"] != skill:
                existing.setdefault("also_detected_by", []).append(skill)
                existing["evidence"] = "{} Also observed by {}: {}".format(
                    existing["evidence"], skill, finding["evidence"])
                duplicates.append({"skill": skill, "id_hint": finding["id_hint"],
                                   "merged_into": existing["id_hint"]})
                continue
            record = dict(finding)
            record["detected_by"] = skill
            # Same skill, same key: keep both, but give the later one a distinct
            # slot so it is not silently overwritten.
            slot = key if existing is None else key + (finding["id_hint"],)
            merged[slot] = record
            order.append(slot)
    return [merged[k] for k in order], duplicates


# Words that make a finding a claim about something not being there.
#
# The single largest class of wrong finding across twenty-six audited sites was
# this one shape: the crawler was not allowed to look, and the report said the
# thing was not there. It appeared as "No XML sitemap is available" when the
# sitemap returned 429; as "no rules to evaluate, which means nothing is
# disallowed" about a robots.txt that returned 403 and contains a crawl delay;
# as "the site never states its founding facts" drawn from four pages of sixty
# while the page that states them sat in the same report's crawl table marked
# 403; as "no product detail pages were detected" about a retailer whose
# product pages were blocked. Four different skills, four separate fixes, and
# the next blocked site would have found a fifth.
#
# So it is caught once, here, where the whole report and the whole crawl are
# both in view - which means it also covers every check written after this one.
_ABSENCE_RE = re.compile(
    r"\b(no|not|never|none|missing|without|absent|lacks?|zero)\b", re.I)

# Below this share of readable pages, an absence claim is a claim about the
# crawler's reception rather than about the site.
#
# Half. Above it the sample is most of the site and silence means something;
# below it the unread half is exactly where an about page, a contact page or a
# product catalogue would be. Measured across the sites in these rounds:
# unblocked crawls sit at 0.93 to 1.00 readable, and the blocked ones that
# produced the false findings sit at 0.00, 0.03 and 0.07. Nothing observed
# falls between 0.07 and 0.93.
READABLE_SHARE_FOR_ABSENCE = 0.5


def claims_absence(finding, brand_name=""):
    """Does this finding assert that something is not on the site?

    The brand's own name is not part of the claim, and reading it as one is a
    live trap: a medical charity is called "Doctors Without Borders", so every
    finding naming it contained the word "without" and the whole report was
    held back as unverifiable. Names arrive quoted in these titles, so the
    quoted spans come out before the sentence is read, and the brand name comes
    out whether it is quoted or not.
    """
    title = re.sub(r'"[^"]*"', " ", finding.get("title", ""))
    if brand_name:
        title = re.sub(re.escape(brand_name), " ", title, flags=re.I)
    return bool(_ABSENCE_RE.search(title))


def withhold_absence_claims(findings, snapshot):
    """Hold back "it is not there" when the crawler was not allowed to look.

    Returns the findings to report and the ones held back. A held-back finding
    is not deleted: it is shown under its own heading, as a question the audit
    could not answer and why, which is a different and more useful thing to
    tell a reader than a defect they do not have.
    """
    brand_name = (snapshot.get("brand") or {}).get("name") or ""
    pages = snapshot.get("pages") or []
    attempted = len(pages)
    readable = len([p for p in pages
                    if p.get("status") == 200 and not p.get("skipped")
                    and not p.get("challenge")])
    if not attempted or readable >= attempted * READABLE_SHARE_FOR_ABSENCE:
        return findings, []

    keep, held = [], []
    for finding in findings:
        # The access findings are the ones explaining the block. They are the
        # only thing the reader can act on, and several of them are phrased as
        # absences themselves ("the homepage does not return HTTP 200").
        if finding.get("mechanism") == "A" or not claims_absence(finding, brand_name):
            keep.append(finding)
            continue
        held.append({
            "title": finding.get("title"),
            "detected_by": finding.get("detected_by"),
            "root_cause": finding.get("root_cause"),
            "reason": ("only {} of the {} URLs the crawl reached could be read, so this is a "
                       "fact about what this crawler was shown rather than about the site. It "
                       "is held back rather than reported as a defect".format(
                           readable, attempted)),
        })
    return keep, held


# A finding's evidence already states its denominator when it contains one of
# these shapes: "3 of 60", "12/60", "48.0%".
_DENOMINATOR_RE = re.compile(
    # "3 of 12", "3 of the 12", "3/12", "25%" - the finding sizing itself
    # outright. `the` is allowed between, because a finding that wrote its own
    # scale in ordinary English got a second, identical sentence appended after
    # it: "found on 54 of the 60 pages crawled. Seen on 54 of the 60 pages this
    # crawl read."
    r"\b\d+\s*(?:of|/)\s*(?:the\s+)?\d+\b|\d+(?:\.\d+)?%"
    # "Checked 46 content page(s)", "across 12 product pages", "of 7 article
    # pages" - the finding naming the population it looked at, in words the
    # count-pair pattern does not see. Three verification agents ranked this
    # their top or second fix: the appended sentence read "Seen on 2 of the 60
    # pages this crawl read" one clause after the evidence said "Checked 46
    # content page(s). None declares...", contradicting it in the same
    # sentence and understating a site-wide gap by fifteen times.
    r"|\b(?:checked|examined|across|among|of)\s+\d{1,4}\s+[a-z-]*\s?page",
    re.I)


def state_the_denominator(finding, pages_crawled, snapshot=None):
    """Append "on N of the M pages crawled" when the evidence names no scale.

    The second-largest class of wrong finding was a real observation sized
    wrongly by the reader, because the number that would have sized it was
    missing. "3 page(s) have no H1" on a site where fifteen did; "2 pages do
    not declare a language" where fifty-seven did not; "the site never states
    its pricing" from four pages of sixty. In each case the count was of what
    the check happened to look at, and nothing said so.

    Fixing them one at a time fixed three checks. Stating the scale of every
    finding, here, fixes the shape - including for checks not yet written,
    which is the only version of this that stays fixed.
    """
    evidence = finding.get("evidence") or ""
    # `affected_pages` is the display list and is capped at five. Counting it
    # made the sentence meant to fix understated scope understate scope itself:
    # seven findings across two sites read "Seen on 5 of the 60" when the true
    # reach was 6, 11, 15, 27, 32 and twice 60. `affected_page_count` is the
    # number that was not truncated.
    count = finding.get("affected_page_count")
    if count is None:
        count = len(finding.get("affected_pages") or [])
    if not count or not pages_crawled or _DENOMINATOR_RE.search(evidence):
        return evidence
    # The denominator is the population the check looked at, not the size of
    # the crawl. A finding about article pages that says "7 of the 60 pages"
    # reads as a 12% problem when it is a 100% one, and an owner triaging by
    # that number does the wrong thing. Where every affected page is the same
    # kind of page, that kind is the population; where they are mixed, the
    # finding really is about the whole crawl.
    population, unit = _population_for(finding, snapshot, pages_crawled)
    return "{} Seen on {} of the {} {}.".format(
        evidence.rstrip(), count, population, unit)


# Sentences that claim the whole site rather than the pages that were read.
_SITE_WIDE_RE = re.compile(
    r"anywhere on the site|across the site|site-wide|\bno page\b|\bnowhere\b"
    r"|the site never|never states|on any crawled page|any page", re.I)

# Below this the crawl is a fair sample of the site and the sentence would be
# noise. A crawl that read sixty of sixty-two pages has read the site.
COVERAGE_DISCLOSURE_RATIO = 2.0


def state_the_coverage(evidence, snapshot, pages_crawled):
    """Append how much of the site the crawl saw, to a claim about all of it.

    `state_the_denominator` sizes a finding against the pages this audit read.
    This sizes the pages it read against the site, which is a different number
    and the one a reader assumes when a sentence says "anywhere on the site".

    A global law firm's report said "No Organization or LocalBusiness markup
    anywhere on the site" and "too few pages carry an address to treat this as
    a business with separate places to visit". The crawl read 60 pages. The
    firm's own sitemap lists 2,692 lawyer profiles and its site lists 31
    offices in 26 countries, none of which the crawl reached. Both sentences
    were true of the sample and neither was true of the site.

    A gym chain's report was the same shape from the other end: 276 branch
    pages exist, the crawl reached one, and the report described per-location
    markup as though it had seen the chain.
    """
    if not evidence or not pages_crawled or not _SITE_WIDE_RE.search(evidence):
        return evidence
    scope = sitemap_scope(snapshot.get("sitemaps") if snapshot else None)
    listed = scope["urls_counted"]
    if not listed or listed < pages_crawled * COVERAGE_DISCLOSURE_RATIO:
        return evidence
    return "{} The sitemap lists {}; this crawl read {}, so this describes the pages it " \
           "reached and not every page of the site.".format(
               evidence.rstrip(), sitemap_total_phrase(scope, "URLs"),
               plural(pages_crawled, "of them"))


def _population_for(finding, snapshot, pages_crawled):
    """(how many pages this check could have looked at, what to call them)."""
    types = _types_of(finding.get("affected_pages") or [], snapshot)
    if len(types) == 1 and snapshot is not None:
        page_type = next(iter(types))
        same = sum(1 for page in pages_of(snapshot)
                   if page.get("page_type") == page_type)
        if same:
            return same, "{} pages this crawl read".format(page_type)
    return pages_crawled, "pages this crawl read"


def _types_of(urls, snapshot):
    if snapshot is None:
        return set()
    by_url = {page.get("url"): page.get("page_type") for page in pages_of(snapshot)}
    return {by_url[url] for url in urls if by_url.get(url)}



# The count the evidence line states about itself, as a pair. "0 of 12 product
# pages" -> (0, 12). Only the first such pair, which is the one the sentence is
# about; a later "3 of 5" inside an example is not the finding's own scale.
_OWN_COUNT_RE = re.compile(r"\b(\d{1,4})\s*(?:of|/)\s*(\d{1,4})\b")


def reconcile_the_example_with_the_claim(finding):
    """An example must be an instance of the thing being claimed.

    A finding read "2 of 60 pages omit a canonical tag", and printed underneath
    it "Affected pages (60 total, showing up to 5)" followed by five pages that
    all had one. The list was the whole crawl, not the offenders, so the one
    part of the finding an owner would actually click was five wrong pages.

    The check does not need to know which check produced it. The finding states
    a numerator about itself; `affected_page_count` states another. When those
    two disagree the per-page list cannot be a list of instances, whichever
    number is right, so the list is withheld and the finding says so. Printing
    nothing is strictly better than printing five compliant pages as offenders.

    Returns the reason it was withheld, or "" when the finding is consistent.
    """
    pages = finding.get("affected_pages") or []
    if not pages:
        return ""
    count = finding.get("affected_page_count") or len(pages)
    match = _OWN_COUNT_RE.search(finding.get("evidence") or "")
    if match is None:
        return ""
    claimed, population = int(match.group(1)), int(match.group(2))
    # A rate ("3 of 12 pages") sizes the finding. A total that equals the
    # population is the finding saying it covers everything, which is
    # consistent with an affected list of that size.
    if claimed == count or population == count:
        return ""
    # The claim may be about something other than pages - "0 of 3 declared
    # phone numbers", "1 of 4 offers". Only reconcile when the sentence is
    # counting pages, which is the only thing `affected_pages` can hold.
    tail = (finding.get("evidence") or "")[match.end():match.end() + 40].lower()
    head = (finding.get("evidence") or "")[:match.start()].lower()
    if "page" not in tail and "page" not in head[-40:]:
        return ""
    return ("the finding counts {} page(s) while its per-page list holds {}, so the list "
            "cannot be a list of instances".format(claimed, count))

# Values a snippet may contain without the site having to show them: the
# vocabulary of the format itself, and the shape of a placeholder.
_SCHEMA_VOCABULARY = frozenset({
    "https://schema.org", "http://schema.org", "organization", "localbusiness",
    "postaladdress", "product", "productgroup", "offer", "aggregateoffer",
    "article", "blogposting", "newsarticle", "faqpage", "question", "answer",
    "breadcrumblist", "listitem", "website", "searchaction", "entrypoint",
    "imageobject", "person", "https://schema.org/instock",
    "https://schema.org/outofstock", "instock", "outofstock", "gbp", "usd",
    "eur", "true", "false",
})
_SNIPPET_VALUE_RE = re.compile(r'"([^"\\]{2,120})"\s*:\s*"([^"\\]{1,200})"')

# Fields whose value is a claim about the world, checked however short it is.
# A price, a postcode or a phone number is exactly the kind of value that is
# both short and damaging to get wrong.
_ALWAYS_VERIFY = frozenset({
    "price", "lowprice", "highprice", "pricecurrency", "postalcode",
    "telephone", "streetaddress", "addresslocality", "addressregion",
    "addresscountry", "sku", "gtin", "email", "faxnumber", "vatid",
    "foundingdate", "datepublished", "datemodified",
})
# A shorter value than this occurs somewhere in any site's text by accident.
_MIN_MEANINGFUL_VALUE = 4


def observed_values(snapshot):
    """Everything this crawl actually saw, as one lowercase haystack."""
    parts = [str(snapshot.get("origin") or ""), str(snapshot.get("site") or "")]
    brand = snapshot.get("brand") or {}
    parts.extend(str(v) for v in (brand.get("name"), brand.get("host")) if v)
    for page in snapshot.get("pages") or []:
        for key in ("url", "final_url", "title", "text", "body_text", "meta_description",
                    "canonical"):
            value = page.get(key)
            if isinstance(value, str):
                parts.append(value)
        for value in (page.get("og") or {}).values():
            if isinstance(value, str):
                parts.append(value)
        # A telephone number the site publishes as `tel:+442072323010` never
        # appears in the visible text in that form, so the guard called a real,
        # correct number a guess and replaced it with a placeholder - which is
        # the same defect as inventing one, pointing the other way. Anything
        # the extractor captured is something the site showed.
        facts = page.get("contact_facts") or {}
        for key in ("declared_phones", "phones", "emails"):
            parts.extend(str(v) for v in (facts.get(key) or []))
        for key in ("street_hint", "postcode_hint"):
            if facts.get(key):
                parts.append(str(facts[key]))
        for link in (page.get("links", {}).get("external") or []):
            if isinstance(link, dict) and link.get("url"):
                parts.append(str(link["url"]))
        for value in (page.get("social_profiles") or {}).values():
            parts.append(str(value))
        for node in page.get("jsonld") or []:
            parts.append(_json_text(node))
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


def _json_text(node):
    if isinstance(node, dict):
        return " ".join(_json_text(v) for v in node.values())
    if isinstance(node, list):
        return " ".join(_json_text(v) for v in node)
    return str(node)


def hold_back_invented_values(snippet, haystack):
    """Replace any value in a paste-ready snippet the site never showed.

    This is the class of defect that does real damage, because a snippet is the
    part of a report a reader copies rather than reads. What reached real
    reports: a streetAddress of "1 million row" taken from a sentence about
    database rows; a postal code of "00005" taken from the price
    "$0.00005 / event"; another taken from a cosmetic ingredient's colour-index
    code; a $90 price on a $10 product; `sameAs` claiming a co-founder's
    personal account and an encyclopedia article about ACID transactions; a
    shop's own name spelt without its apostrophe.

    Each was fixed at its source, and each source was a different function. The
    property they violate is one property: a snippet may only contain what the
    audit observed. Enforced here, once, over every snippet any check produces
    now or later - so the next extractor that guesses wrong produces a
    placeholder and a note, not a fact the owner publishes.
    """
    if not snippet:
        return snippet, []

    invented = []

    def replace(match):
        key, value = match.group(1), match.group(2)
        stripped = value.strip()
        if not stripped:
            return match.group(0)
        # A placeholder is already honest about being one.
        if "<" in stripped and ">" in stripped:
            return match.group(0)
        low = stripped.lower()
        if low in _SCHEMA_VOCABULARY or low.startswith("@"):
            return match.group(0)
        # Substring matching is how a fabricated value passes. `"price": "1"`
        # went into a snippet as fact, with no warning, because the character
        # "1" occurs in every page of every site - inside a year, a phone
        # number, an address. That is the exact failure this function exists to
        # stop, so the fields that carry a claim about the world have to match
        # as a whole token, not as a fragment of a longer one.
        if key.lower() in _ALWAYS_VERIFY:
            if re.search(r"(?<![\w.]){}(?![\w])".format(re.escape(low)), haystack):
                return match.group(0)
        elif low in haystack or low.rstrip("/") in haystack:
            return match.group(0)
        invented.append("{}={!r}".format(key, truncate(stripped, 40)))
        return '"{}": "<{} - not found on the site, fill this in>"'.format(key, key)

    return _SNIPPET_VALUE_RE.sub(replace, snippet), invented


def price_one_observation_once(findings, snapshot):
    """One blocked request is one problem, however many checks noticed it.

    A bank's homepage refused the crawler once. The report carried two
    critical findings about that single fetch - one from the challenge-page
    check, one from the user-agent comparison - each with its own "do first"
    and its own several-days estimate, and a summary line reading "2 critical
    problems". A site whose homepage is blocked has one problem.

    The rule is general and does not name any check: when the crawl read fewer
    pages than it takes to observe anything twice, the access findings are all
    describing the same event, so the first stands as written and the rest are
    folded into it as corroboration.
    """
    readable = len([p for p in (snapshot.get("pages") or [])
                    if p.get("status") == 200 and not p.get("skipped")])
    if readable > 1:
        return findings

    access = [f for f in findings if f.get("mechanism") == "A"]
    if len(access) < 2:
        return findings

    primary, rest = access[0], access[1:]
    primary["evidence"] = "{} The same refusal was reached independently by {}.".format(
        primary["evidence"].rstrip(),
        ", ".join(sorted({f.get("detected_by") or "another check" for f in rest})))
    keep = []
    for finding in findings:
        if finding in rest:
            continue
        keep.append(finding)
    return keep


def score(finding, pages_crawled):
    """priority = severity weight x reach / effort.

    Reach is the share of crawled pages the finding touches, floored at 0.25 so
    a critical problem on one page still outranks cosmetic issues everywhere.
    A finding with no attributed pages is site-wide by construction (robots,
    entity ambiguity, boilerplate) and gets full reach.
    """
    count = finding.get("affected_page_count", 0)
    if count == 0:
        reach = 1.0
    else:
        reach = max(0.25, min(1.0, count / float(max(pages_crawled, 1))))
    weight = SEVERITY_WEIGHT[finding["severity"]]
    effort = EFFORT_DIVISOR[finding["suggested_action"]["effort"]]
    return round(weight * reach / effort, 3), round(reach, 3)


# However cheap and however narrow, a severe problem is not a "when
# convenient". The score decides the ordering; the label is an instruction to a
# person, and reach was overwhelming severity in it. A `high` finding on one
# page of seven scores 0.5 and read as "schedule", while a `low` one on every
# page scores 1.0 and read as "do soon" - so the report told the owner to do
# the small thing before the serious one, directly under headings that said the
# opposite.
PRIORITY_FLOOR = {"critical": "do first", "high": "do soon", "medium": "schedule"}
_LABEL_ORDER = ["do first", "do soon", "schedule", "when convenient"]


def priority_label(value, severity=None):
    label = "when convenient"
    for threshold, candidate in PRIORITY_BANDS:
        if value >= threshold:
            label = candidate
            break
    floor = PRIORITY_FLOOR.get(severity)
    if floor and _LABEL_ORDER.index(label) > _LABEL_ORDER.index(floor):
        return floor
    return label


def assign_ids(findings):
    """Stable IDs: sort by severity, mechanism, id_hint, then number upward.

    Deliberately not sorted by priority, so a finding keeps the same ID when a
    later crawl changes how many pages it touches.
    """
    ordered = sorted(findings, key=lambda f: (
        SEVERITY_RANK[f["severity"]], f["mechanism"], f["id_hint"]))
    for index, finding in enumerate(ordered, start=1):
        finding["id"] = "F-{:03d}".format(index)
    return ordered


# --------------------------------------------------------------------------
# Citation simulation
# --------------------------------------------------------------------------


# How many pages have to carry a sentence before it is furniture rather than
# content. Three, not the half-the-site threshold the whole-page boilerplate
# stripper uses: a block repeated across one subsection is that subsection's
# chrome even when it is a tenth of the crawl.
QUOTE_REPEAT_LIMIT = 3


def _quote_key(sentence):
    return re.sub(r"[^a-z0-9]+", " ", (sentence or "").lower()).strip()


def _sentences_seen_more_than_once(snapshot):
    """Sentences repeated across pages, which no page can claim as its own."""
    counts = {}
    for page in pages_of(snapshot):
        for paragraph in (page.get("paragraphs") or [])[:25]:
            for sentence in sentences(paragraph):
                key = _quote_key(sentence)
                if len(key) < 25:
                    continue
                counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count >= QUOTE_REPEAT_LIMIT}

def simulate_citations(snapshot, brand_name):
    """The sentence an assistant would most likely lift from each key page.

    Makes the extractability findings concrete: a judge can read "none found"
    beside a page and see immediately what the problem is.
    """
    out = []
    key_types = ("home", "about", "pricing", "faq", "product", "service", "contact", "location")
    pattern = r"\s+".join(re.escape(t) for t in brand_name.split()) if brand_name else None

    # A sentence that appears on several pages is that site's furniture, not
    # any one page's distinct content, and a table showing the same words twice
    # is answering a different question from the one it asks. Two real cases:
    # a court subsection's postal address, repeated verbatim across six pages
    # and offered as what an assistant would quote from each; and a footnote
    # joke - "*[product] is a web product and cannot be installed by CD" -
    # offered for both the homepage and the pricing page, over the actual
    # headline and the actual prices.
    #
    # The whole-site boilerplate stripper works at half the crawl and cannot
    # see either: six pages of sixty is ten per cent. Repetition still decides
    # it; the denominator is just the pages that carry it.
    repeated = _sentences_seen_more_than_once(snapshot)
    used = set()

    for page in pages_of(snapshot, content_only=True):
        if page["page_type"] not in key_types:
            continue
        # Quote from paragraphs, not from the flattened page text: a heading
        # and the sentence after it run together when the whole page is
        # flattened, and the result is not a sentence anyone would quote.
        candidates = []
        for paragraph in (page.get("paragraphs") or [])[:25]:
            candidates.extend(sentences(paragraph))
        if not candidates:
            candidates = sentences(page.get("body_text", ""))
        candidates = [c for c in candidates[:60]
                      if _quote_key(c) not in repeated and _quote_key(c) not in used][:40]
        best = None
        reason = ""
        # Priority 1: a definition naming the brand. That is what an assistant
        # needs before it can say anything else.
        if pattern:
            for sentence in candidates:
                if re.search(pattern, sentence, re.I) and re.search(
                        r"\b(?:is|are|was|provides|offers|helps|builds|makes)\b", sentence, re.I):
                    best, reason = sentence, "names the brand and says what it is"
                    break
        # How long a sentence has to be before it is worth quoting depends on
        # the script it is written in. Forty characters is a clause in
        # English and a whole sentence in Japanese, and the floor rejected
        # complete, fact-stating Japanese sentences on three pages of one
        # site, each reported as having nothing quotable.
        floor = QUOTE_MIN_CHARS if words_are_separated(
            page.get("body_text", "")) else QUOTE_MIN_CHARS_UNSPACED

        # Priority 2: a price or a contact detail. Both are short by nature
        # and both are exactly what somebody asks an assistant for. A
        # software company's pricing page - "Basic $10 per user/month" - and
        # its contact page - "For other questions, email us hello@..." - were
        # both reported as having nothing an assistant could quote, in a table
        # printed directly under a finding about the site being hard to quote.
        if best is None:
            for sentence in candidates:
                if len(sentence) > 300:
                    continue
                if PRICE_LINE_RE.search(sentence):
                    best, reason = sentence, "states a price a reader could act on"
                    break
                if CONTACT_LINE_RE.search(sentence):
                    best, reason = sentence, "states a way to make contact"
                    break
        # Priority 3: a self-contained sentence carrying a number.
        if best is None:
            for sentence in candidates:
                if floor <= len(sentence) <= 300 and NUMERIC_RE.search(sentence):
                    best, reason = sentence, "states a number a reader could act on"
                    break
        # Priority 4: any self-contained sentence that defines something.
        if best is None:
            for sentence in candidates:
                if floor <= len(sentence) <= 300 and CONCRETE_RE.search(sentence):
                    best, reason = sentence, "defines something, though it states no figure"
                    break
        if best is not None:
            used.add(_quote_key(best))
        out.append({
            "url": page["url"],
            "page_type": page["page_type"],
            "likely_citation": truncate(best, 260) if best else None,
            "why": reason if best else
                   "no sentence on this page both stands alone and states a fact, so an "
                   "assistant fetching it would have nothing to quote",
        })
        if len(out) >= 12:
            break
    return sorted(out, key=lambda c: c["url"])


# --------------------------------------------------------------------------
# Proactive recommendations
# --------------------------------------------------------------------------

def _plural(count, singular, plural):
    """`1 page crawled`, not `1 pages crawled`.

    It is the first line of the report a person reads.
    """
    return "{} {}".format(count, singular if count == 1 else plural)


def build_recommendations(snapshot, signals, findings):
    """Recommendations beyond the defects found, included only when relevant.

    Every entry states its condition, so nothing generic is ever appended just
    to make the report look longer.
    """
    # Every condition below is the absence of a signal, so on a site we never
    # managed to read, every signal is absent and almost every recommendation
    # fires. Audit a domain that does not resolve and the report offered six
    # improvements for it, including publishing a file at its root.
    #
    # Absence of evidence is not evidence of absence, and the entrypoint's own
    # procedure already says an unreachable homepage makes every other check
    # meaningless. That rule was honoured by the findings and not by these.
    if not pages_of(snapshot):
        return []

    root_causes = {f["root_cause"] for f in findings}
    page_types = {p["page_type"] for p in pages_of(snapshot, content_only=True)}
    origin = snapshot["origin"].rstrip("/")
    brand = (snapshot.get("brand") or {}).get("name") or snapshot["site"]
    out = []

    def add(rec_id, title, condition, summary, steps, mechanism, why, effort, owner, snippet=None):
        if not condition:
            return
        entry = {
            "id": rec_id, "type": "proactive", "title": title, "summary": summary,
            "how_to_do_it": steps, "mechanism": mechanism, "why_this_works": why,
            "effort": effort, "owner": owner,
        }
        if snippet:
            entry["snippet"] = snippet
        out.append(entry)

    add("R-LLMS-TXT", "Publish /llms.txt and /llms-full.txt",
        not signals.get("llms_txt_present"),
        "Add a short plain-text file at the site root summarising the brand, its key pages and "
        "its canonical facts.",
        ["Create /llms.txt with a one-line brand definition, the canonical boilerplate, and a "
         "linked list of your most important pages with one line of description each.",
         "Add /llms-full.txt containing the full text of those key pages if they are short.",
         "Keep it under 200 lines and update it whenever the boilerplate changes."],
        "B",
        "It hands a machine the exact summary you want quoted, instead of leaving it to "
        "assemble one from whichever page it happened to fetch.",
        "low", "marketing",
        _llms_txt_template(snapshot, brand))

    add("R-ANSWER-FIRST", "Add an answer-first summary block to every key page",
        signals.get("fluff_first"),
        "Open each key page with two or three sentences an assistant can quote without editing.",
        ["Write a 2-3 sentence block immediately under the H1 stating the page's core facts.",
         "Use plain declarative sentences, each carrying one fact.",
         "Do not hide it behind a tab, an accordion or a carousel."],
        "B",
        "Assistants lift short passages from near the top of a page. A block written to be "
        "lifted is the passage they find.",
        "low", "content owner")

    # Only when there is no FAQ content at all. A site that has an FAQ page and
    # has not marked it up already gets `no-faq-schema` as a finding, with the
    # page named and a snippet carrying its real questions - and this told the
    # same owner to "add a page of real questions" they had already written.
    # Two sections of one report, disagreeing about whether a page exists.
    has_faq_page = bool(pages_of(snapshot, types=("faq",)))
    add("R-FAQ-SCHEMA", "Publish FAQ content with FAQPage schema",
        not signals.get("has_faq_schema") and not has_faq_page,
        "Add a page of real questions, phrased the way people ask assistants, marked up as FAQPage.",
        ["Collect the ten questions your sales or support team answers most often.",
         'Phrase each heading as the customer asks it ("How much does X cost?"), not as an '
         "internal topic name.",
         "Answer each in two or three sentences, leading with the actual value.",
         "Wrap the whole set in FAQPage JSON-LD with the answer text matching the page exactly."],
        "B",
        "Question-and-answer pairs are already the shape of the thing an assistant is trying "
        "to produce, so they are unusually cheap for it to reuse.",
        "medium", "content owner")

    add("R-SAMEAS-WIKIDATA", "Corroborate the brand across independent sources",
        signals.get("profile_breadth", 0) < 6
        or not signals.get("has_wikidata_or_wikipedia"),
        "Claim the authoritative profiles, create a Wikidata item, and list them all in sameAs.",
        ["Claim or update: LinkedIn company page, Google Business Profile, Crunchbase, and one "
         "or two industry directories.",
         "Create a Wikidata item using the same one-sentence description you use on the site.",
         "List every profile URL in the Organization `sameAs` array.",
         "Make sure each profile links back to the site."],
        "D",
        "One site saying something is a claim. Several independent, verifiable sources saying "
        "the same thing is what makes it safe to repeat.",
        "medium", "marketing")

    add("R-BOILERPLATE", "Write one canonical boilerplate and reuse it verbatim",
        True,  # always: the cheapest corroboration win there is
        "Agree a single paragraph describing the brand and use it, unchanged, everywhere.",
        ["Write one paragraph: what you are, who you serve, where you are, when you started.",
         "Paste it, character for character, into the site, the Organization `description`, "
         "LinkedIn, the press kit, app store listings and every directory entry.",
         "Change it in one place and propagate; never let two versions coexist.",
         "Review it once a year rather than rewriting it per channel."],
        "D",
        "Identical wording across independent sources is the strongest agreement signal "
        "available, and it costs nothing but discipline.",
        "low", "marketing")

    add("R-PRESS-PAGE", "Publish a press page with reusable facts",
        "press" not in page_types,
        "Give journalists and analysts a single page of approved facts so third parties repeat "
        "the same ones.",
        ["Publish a page with the logo pack, the canonical boilerplate, founding facts and a "
         "named spokesperson.",
         "Include the exact figures you want repeated: headcount, founding year, locations, "
         "customer count.",
         "Link it from the footer."],
        "D",
        "Third-party coverage is where corroboration comes from. A press page decides what "
        "those third parties copy.",
        "low", "marketing")

    # The single highest-value recommendation we can make, and the only one
    # backed by a measured difference between sites assistants cite and sites
    # they ignore. Fires on a dating problem of any kind: no dates at all,
    # stale dates, or simply low coverage across the site.
    # 25%, not 50%. An earlier threshold came from comparing brand sites against
    # encyclopedias, where dated coverage runs at 85%. Compared within category,
    # brands assistants name date 34% of pages and brands they ignore date 17%.
    # A 50% bar would have fired on most of the brands that *are* named.
    date_coverage = signals.get("date_coverage")
    add("R-DATE-SIGNALS", "Date your content, and keep the dates honest",
        signals.get("no_date_signals")
        or bool({"no-date-signal", "stale-content"} & root_causes)
        or (date_coverage is not None and date_coverage < DATE_COVERAGE_FLOOR),
        "Put a visible published-or-updated date on every substantive page, mirror it in "
        "structured data, and review the top pages on a schedule."
        + ("" if date_coverage is None else
           " {}% of the pages crawled here carry any date signal.".format(int(date_coverage * 100))),
        ["Add \"Last updated <date>\" to every substantive page, wrapped in "
         "<time datetime=\"YYYY-MM-DD\">.",
         "Mirror it in `dateModified` in structured data.",
         "Put a quarterly review of the top ten pages in someone's calendar.",
         "Only move the date when the content actually changed. A date that always changes "
         "carries no information and gets discounted.",
         "Do not date pages where a date would mislead, such as a contact page."],
        "D",
        "Freshness is one of the few quality signals a machine can check cheaply, and it is "
        "the tiebreaker when two sources make the same claim. This is also the one measure "
        "that separated the two cohorts in our own field study: reference sites that "
        "assistants cite constantly dated 85% of their pages, ordinary brand sites 35%. "
        "Of everything in this catalogue it is the cheapest change with the clearest "
        "evidence behind it.",
        "low", "content owner")

    add("R-COMPARISON-PAGES", "Publish comparison and alternatives pages",
        "comparison" not in page_types,
        'Cover the framings people actually search: "X vs Y" and "X for <use case>".',
        ["List the three alternatives prospects mention most and write an honest comparison "
         "page for each.",
         "Say plainly who each option suits better; a comparison that never concedes anything "
         "is not treated as a source.",
         "Include a table of concrete differences: price, capability, minimum commitment."],
        "E",
        "Assistants answer the question as it was asked. A brand with content only for its own "
        "framing is absent from every comparison question, which is how most buyers ask.",
        "medium", "content owner")

    add("R-USE-CASE-PAGES", "Add use-case or location landing pages",
        bool({"location", "product", "service"} & page_types),
        "Give each distinct audience or place its own page, rather than one page for everyone.",
        ["List the three audiences or locations that matter commercially.",
         "Give each a page naming that audience or place in the H1 and first paragraph.",
         "Include facts specific to it: local address, sector-specific numbers, relevant "
         "customer examples."],
        "E",
        "Answers are shaped by the asker's context. Content that exists for only one framing "
        "is surfaced for only one framing.",
        "medium", "content owner")

    add("R-HTML-FOR-PDF", "Publish HTML versions of PDF-only content",
        "pdf-locked-facts" in root_causes,
        "Move the numbers out of the PDF and onto a page.",
        ["Create an HTML page carrying the same table or figures as the PDF.",
         "Keep the PDF as a download from that page, not as the only source.",
         "Generate the PDF from the HTML so the two cannot diverge."],
        "C",
        "PDFs are fetched inconsistently and quoted reluctantly. The same numbers in HTML are "
        "read every time.",
        "medium", "content owner")

    add("R-EMAIL-TEXT-FIRST", "Design newsletters and transactional email text-first",
        signals.get("newsletter_signup"),
        "Put the substance in the first two lines of readable text, never in an image.",
        ["Lead every email with two lines of plain text stating what it is and what changed.",
         "Never put the main message in an image; use images to support text, not replace it.",
         "Give every email a subject line that states the fact rather than teasing it.",
         "Include a plain-text alternative part in every send."],
        "F",
        "Inbox summaries are built from the text of a message. Substance carried in an image, "
        "or buried under filler, is simply absent from the summary the recipient reads.",
        "low", "marketing")

    add("R-WAYFINDING", "Add site search, breadcrumbs and related-content modules",
        bool({"dead-end", "orphan-pages", "no-breadcrumbs", "broken-links"} & root_causes),
        "Give every page a way onward and a way back.",
        ["Add on-site search, and describe it with WebSite + SearchAction structured data.",
         "Add breadcrumbs to every deep template, visible and in BreadcrumbList markup.",
         'Add a "Read next" module with 2-4 related internal links to article and product templates.'],
        "G",
        "A visitor sent by an answer lands deep with no journey behind them. Wayfinding is the "
        "difference between one page and a session.",
        "medium", "developer")

    add("R-BRAND-TERMINOLOGY", "Name your own components so machines must use your words",
        bool({"product", "service", "pricing"} & page_types),
        "Give the things that make the product distinctive proper names, and define those "
        "names in both the page text and the structured data.",
        ['Replace generic descriptions with a coined, capitalised term plus its definition: '
         '"a recycled sole" becomes "the Aero-Flyte sole, a recycled EVA midsole rated to '
         '800km".',
         "Define the term once, in a sentence, the first time it appears on the page.",
         "Carry the same term into structured data: Product `additionalProperty`, "
         "`material`, or a named `PropertyValue`, so it is an assertion rather than adjective.",
         "Use the identical term everywhere: site, spec sheets, packaging, retail partner "
         "feeds and press releases.",
         "Coin sparingly. Three defended terms beat twenty; a page of invented words with no "
         "definitions reads as noise and gets discarded.",],
        "B",
        "Assistants extract explicit factual entities and discard the marketing copy around "
        "them, which is why brands come back flattened into generic specifications. A named, "
        "defined component is a fact rather than an adjective, so it survives extraction and "
        "the brand's own vocabulary has to be used to describe the product accurately.",
        "medium", "marketing")

    add("R-AUTHOR-PAGES", "Give articles named authors with credentials",
        bool(signals.get("articles_without_author")) and "article" in page_types,
        "Attribute every article to a real, described person.",
        ["Create an author page per writer with their role, credentials and other articles.",
         "Link each article byline to that page and set Article `author` to the same Person.",
         "Include something verifiable: a professional qualification, years in the field, a "
         "public profile."],
        "D",
        "Attribution is a trust signal a machine can verify. Anonymous content competes badly "
        "with the same claim made by a named, credentialed person.",
        "medium", "content owner")

    return out


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------

def compose(snapshot, skill_results, audited_at=None):
    crawl = snapshot.get("crawl") or {}
    pages_crawled = max(crawl.get("pages_ok") or crawl.get("pages_crawled") or 0, 1)
    brand = snapshot.get("brand") or {}

    signals = {}
    checks_run, not_applicable = [], []
    extra_requests = 0
    fired_checks = set()
    for result in skill_results:
        signals.update(result.get("signals") or {})
        for check in result.get("checks_run", []):
            checks_run.append({"skill": result.get("skill"), "check": check})
        for check in result.get("fired_checks", []):
            fired_checks.add((result.get("skill"), check))
        not_applicable.extend(result.get("not_applicable", []))
        extra_requests += result.get("extra_requests_made", 0)

    findings, duplicates = dedupe(skill_results)
    findings, unverifiable = withhold_absence_claims(findings, snapshot)
    findings = price_one_observation_once(findings, snapshot)
    for finding in findings:
        value, reach = score(finding, pages_crawled)
        finding["suggested_action"]["priority"] = priority_label(value, finding["severity"])
        finding["suggested_action"]["priority_score"] = value
        finding["reach"] = reach
    haystack = observed_values(snapshot)
    unreconciled = []
    for finding in findings:
        # Rule 5 runs before the denominator sentence is appended, so it reads
        # the check's own count rather than one this file just wrote.
        mismatch = reconcile_the_example_with_the_claim(finding)
        if mismatch:
            unreconciled.append("{}: {}".format(finding["id_hint"], mismatch))
            finding["affected_pages"] = []
            finding["example_withheld"] = mismatch
        finding["evidence"] = state_the_coverage(
            state_the_denominator(finding, pages_crawled, snapshot),
            snapshot, pages_crawled)
        action = finding["suggested_action"]
        if action.get("snippet"):
            action["snippet"], invented = hold_back_invented_values(
                action["snippet"], haystack)
            if invented:
                action["snippet_warning"] = (
                    "{} value(s) below could not be found anywhere on this site and were "
                    "replaced with a placeholder ({}). Something guessed them; fill them in "
                    "yourself rather than publishing a guess.".format(
                        len(invented), ", ".join(sorted(invented)[:4])))
    findings = assign_ids(findings)

    # Severity first, then the priority score inside each band.
    #
    # Ranking on the score alone inverted the report against itself. `reach`
    # floors at 0.25 for a finding that lists the pages it affects, while a
    # finding listing none is treated as site-wide and gets 1.0 - so on a
    # sixty-page crawl, naming your evidence was a four-fold penalty. The
    # result on a real site: both `high` findings ranked below two `medium`
    # ones, and "add og:image" (low, site-wide, cheap) outranked "this site has
    # no machine-readable identity". The report told the owner to do the small
    # thing first, under a heading that said the opposite.
    #
    # The score still decides order within a band, which is where it earns its
    # keep: two mediums, the cheaper and wider one first.
    ranked = sorted(findings,
                    key=lambda f: (SEVERITY_RANK[f["severity"]],
                                   -f["suggested_action"]["priority_score"], f["id"]))
    start_here = _start_here_ids(ranked)

    counts = {level: sum(1 for f in findings if f["severity"] == level)
              for level in ("critical", "high", "medium", "low", "info")}

    report = {
        "site": snapshot["site"],
        "audited_at": audited_at or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "total_findings": len(findings),
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "info": counts["info"],
            "verdict": _verdict(counts, findings, crawl, signals,
                                (snapshot.get("brand") or {}).get("name") or ""),
        },
        "findings": [_public_finding(f) for f in findings],
        "start_here": start_here,
        "recommendations": build_recommendations(snapshot, signals, findings),
        "citation_simulation": simulate_citations(snapshot, brand.get("name") or ""),
        "crawl": {
            "origin": snapshot["origin"],
            "brand_name": brand.get("name"),
            "brand_name_source": brand.get("source"),
            "pages_crawled": crawl.get("pages_crawled", 0),
            "pages_ok": crawl.get("pages_ok", 0),
            "budget_exhausted": bool(crawl.get("budget_exhausted")),
            "render_mode": crawl.get("render_mode", "static"),
            "elapsed_s": crawl.get("elapsed_s"),
            "extra_requests_made": extra_requests,
            "user_agent": crawl.get("user_agent"),
            "notes": crawl.get("notes") or [],
        },
        "checks_run": sorted(checks_run, key=lambda c: (c["skill"], c["check"])),
        # Every check now lands in exactly one of three buckets: it fired, it
        # declined and said why, or it ran and was clean. Twelve used to fall
        # through all three and appear nowhere - the ones that confirm robots.txt
        # was fetched and the homepage answered, which are precisely what a
        # worried reader is looking for.
        "checks_passed": _checks_passed(checks_run, not_applicable, fired_checks),
        # Checks a finding came out of, listed so that every check which ran is
        # in exactly one of four places. Without this, a check registered
        # alongside the one a finding names appeared in none of them: not a
        # finding, not a decline, not a pass. Four of seventy-three on one real
        # site, and the README promises that never happens.
        "checks_that_found_something": _checks_that_found_something(
            checks_run, not_applicable, fired_checks, findings),
        "not_applicable": sorted(not_applicable, key=lambda n: (n.get("skill", ""), n["check"])),
        "merged_duplicates": duplicates,
        # Questions the audit could not answer, kept separate from defects it
        # found. A blocked crawl used to turn every one of these into a
        # confident claim about the site.
        "unverifiable": unverifiable,
        "auditor": {"name": "brand-ai-readiness-audit", "version": VERSION},
    }
    return report


def _public_finding(finding):
    """Required keys first, extensions after. Ordering is for readers, not code."""
    action = finding["suggested_action"]
    out = {
        "id": finding["id"],
        "title": finding["title"],
        "severity": finding["severity"],
        "evidence": finding["evidence"],
        "suggested_action": {
            "summary": action["summary"],
            "priority": action["priority"],
            "priority_score": action["priority_score"],
            "effort": action["effort"],
            "owner": action["owner"],
            "how_to_fix": action["how_to_fix"],
            "rationale": action["rationale"],
        },
        "confidence": finding["confidence"],
        "mechanism": finding["mechanism"],
        "mechanism_description": MECHANISMS[finding["mechanism"]],
        "root_cause": finding["root_cause"],
        # `id` is F-001, F-002 ... assigned in order, so fixing one renumbers
        # every finding after it and the same problem has a different id in the
        # next run. A developer comparing a before and an after had to match on
        # root_cause by hand. This is the handle to compare on: it names the
        # specific problem and does not move.
        "stable_id": finding["id_hint"],
        "check": finding.get("check"),
        "affected_pages": finding["affected_pages"],
        "affected_page_count": finding["affected_page_count"],
        "reach": finding["reach"],
        "detected_by": finding["detected_by"],
    }
    if finding.get("example_withheld"):
        out["example_withheld"] = finding["example_withheld"]
    if "snippet" in action:
        out["suggested_action"]["snippet"] = action["snippet"]
    if action.get("snippet_warning"):
        out["suggested_action"]["snippet_warning"] = action["snippet_warning"]
    if finding.get("also_detected_by"):
        out["also_detected_by"] = finding["also_detected_by"]
    return out


NON_PUBLIC_HOST_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0"
    r"|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+"
    r"|[^/\s]*\.(?:local|test|localhost|invalid|internal))", re.I)


def _non_public_host(text):
    """Is this snippet filled in with an address nobody else can reach?"""
    return bool(NON_PUBLIC_HOST_RE.search(text or ""))


def _checks_passed(checks_run, not_applicable, fired):
    """Checks that ran, produced no finding, and had no reason to decline.

    The README promises that every check which stays quiet says why. That was
    true of the checks which declined and false of the ones that simply passed:
    they appeared in neither list, so a reader could not tell that robots.txt
    had been fetched at all.
    """
    declined = {(n.get("skill"), n["check"]) for n in not_applicable}
    passed = [c for c in checks_run
              if (c["skill"], c["check"]) not in declined
              and (c["skill"], c["check"]) not in fired]
    return sorted(passed, key=lambda c: (c["skill"], c["check"]))



def _checks_that_found_something(checks_run, not_applicable, fired, findings):
    """Checks whose group produced a finding, and which no finding names.

    A finding carries the one check that was current when it was raised. Its
    siblings - registered in the same function, any of which the finding might
    equally be about - are marked as having fired, which correctly keeps them
    out of the passed list and then left them nowhere at all. They are the
    fourth bucket, and with it every check that ran is in exactly one.
    """
    named = {f.get("check") for f in findings if f.get("check")}
    declined = {(n.get("skill"), n["check"]) for n in not_applicable}
    out = [c for c in checks_run
           if (c["skill"], c["check"]) in fired
           and (c["skill"], c["check"]) not in declined
           and c["check"] not in named]
    return sorted(out, key=lambda c: (c["skill"], c["check"]))

def _llms_txt_template(snapshot, brand):
    """A starter /llms.txt listing pages that actually exist on this site.

    It used to hard-code `<origin>/pricing` and `<origin>/about`. On a site
    with neither - which is most small sites - the file labelled paste-ready
    told the owner to publish two links that 404 on their own domain, guessed
    from a naming convention the crawl had already disproved.
    """
    wanted = ("about", "pricing", "product", "service", "faq", "contact")
    titles = {}
    for page in pages_of(snapshot):
        title = (page.get("title") or "").strip()
        if title:
            titles[title] = titles.get(title, 0) + 1
    seen, lines = set(), []
    for page_type in wanted:
        for page in pages_of(snapshot, types=(page_type,)):
            if page["url"] in seen:
                continue
            seen.add(page["url"])
            # Not the page's <title> when titles are duplicated across the
            # site: this template labelled a product page "Coppergate Ceramics"
            # and the FAQ page "About", because it copied the very duplicate
            # titles the same report flags as a defect. A report that
            # propagates the bug it is reporting is worse than one that says
            # nothing.
            title = (page.get("title") or "").strip()
            label = title if title and titles.get(title, 0) == 1 else page_type.upper()[:1] + page_type[1:]
            lines.append("- [{}]({}): <one line saying what is on this page>".format(
                truncate(label, 60), page["url"]))
            break
    if not lines:
        lines.append("- [<page name>](<url>): <one line saying what is on this page>")

    header = ("# {0}\n\n> {0} is a <category> that <does what> for <whom>.\n\n"
              "## Key pages\n\n")
    return header.format(brand) + "\n".join(lines) + "\n"


def _start_here_ids(ranked, limit=3):
    """The first three things to do, drawn from findings that are worth doing.

    "Start here" answers "what should I do first", and a `low` finding is by
    definition not that. Ranking on the priority score alone put missing Open
    Graph tags - which control what a link preview looks like when shared -
    third, above fixing structured data that does not parse, because a `low`
    finding still scores 1 and a cheap site-wide fix carries full reach and the
    lowest effort divisor.

    So the list is drawn from medium and above first, falling back to the low
    findings only when there are not enough. Ordering inside each tier is still
    severity x reach / effort, which is the part that earns its keep.
    """
    actionable = [f for f in ranked if f["severity"] != "info"]
    substantive = [f for f in actionable
                   if f["severity"] in ("critical", "high", "medium")]
    remainder = [f for f in actionable if f not in substantive]
    return [f["id"] for f in (substantive + remainder)[:limit]]


def _verdict(counts, findings, crawl=None, signals=None, brand_label=""):
    """One paragraph naming what is actually wrong, not how many things are.

    This used to be a pure severity ladder, and on a site with no off-site
    presence, no identity markup, invalid markup where it existed and six of
    seven pages sharing a title, it opened with "The foundations are sound"
    because no finding happened to be `critical`. A summary that contradicts
    the report beneath it is the one sentence most likely to be the only
    sentence read.

    The counts still decide how loud to be. What the site is failing at now
    decides what to say.
    """
    # A robots-blocked run reports almost nothing, and without saying why that
    # looks like a broken audit rather than a respected instruction.
    if (crawl or {}).get("audit_blocked_by_robots"):
        return ("This site's robots.txt disallows this auditor, so no pages were fetched and "
                "the findings below come from robots.txt alone. That is the file working as "
                "intended; it also means any crawler that respects it sees exactly as little.")
    if counts["critical"]:
        return ("Machines are being shut out before they read anything. {} access or "
                "delivery, and nothing else on the site can compensate until it is "
                "fixed.".format(plural(counts["critical"], "critical problem blocks",
                                       "critical problems block")))
    # The Round-2 "invisible" diagnosis, and it outranks the severity ladder.
    # A brand that declares no identity a machine can read, and links to nothing
    # that would corroborate one, is unquotable however few findings are severe:
    # there is no fact to repeat and nothing to check it against.
    signals = signals or {}
    causes = {f["root_cause"] for f in findings}
    breadth = signals.get("profile_breadth")
    undeclared = bool(causes & {"no-org-schema", "no-entity-definition"})
    uncorroborated = ("weak-corroboration" in causes
                      or (breadth is not None and breadth <= 1))
    if undeclared and uncorroborated:
        # Name the brand and the two specific gaps. The first version of this
        # paragraph was identical, word for word, for a two-person pottery and
        # for a widely-cited software project - which reads as a template
        # rather than a diagnosis, and discredits everything under it.
        missing = []
        if "no-org-schema" in causes:
            missing.append("no page carries Organization or LocalBusiness markup")
        if "no-entity-definition" in causes:
            missing.append("no page states in one sentence what it is")
        if breadth is not None:
            missing.append(plural(breadth, "off-site profile is linked",
                                  "off-site profiles are linked"))
        return ("A machine can reach {} and read it. What it cannot do is establish who the "
                "brand is: {}. Nothing on the site says it in a form a machine reads, and "
                "nothing off the site corroborates it, so an assistant asked about this "
                "category has no fact about this brand it can safely repeat. Most of what "
                "follows is a consequence of that.".format(
                    brand_label or "this site", "; ".join(missing) or "the identity is undeclared"))

    if counts["high"] >= 3:
        return ("The site is reachable, but {} high-severity problems mean a machine fetching "
                "it struggles to work out who this brand is or to find a fact worth "
                "quoting.".format(counts["high"]))
    if counts["high"]:
        return ("Access and delivery are sound - a machine can fetch these pages and read "
                "them. {} back how confidently the brand can be described and "
                "cited.".format(plural(counts["high"], "high-severity problem then holds",
                                       "high-severity problems then hold")))
    if counts["medium"]:
        return ("No blocking problems. {} worth fixing to improve how often and how "
                "accurately the brand gets quoted.".format(
                    plural(counts["medium"], "medium-severity item is",
                           "medium-severity items are")))
    return ("No blocking or significant problems were found. The proactive recommendations below "
            "are where the remaining upside is.")


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------

SEVERITY_HEADINGS = [
    ("critical", "Critical", "Machines cannot get in or cannot read the page. Fix these first."),
    ("high", "High", "Significant loss of visibility, accuracy or visitors."),
    ("medium", "Medium", "Real improvements, worth scheduling."),
    ("low", "Low", "Hygiene. Cheap to fix, small individual gain."),
    ("info", "For information", "Observations that are not defects, recorded so you know they were checked."),
]


def render_markdown(report):
    lines = []
    add = lines.append

    add("# AI readiness audit: {}".format(report["site"]))
    add("")
    add("Audited {} · {} · {} · {}".format(
        report["audited_at"],
        _plural(report["crawl"]["pages_crawled"], "page crawled", "pages crawled"),
        _plural(report["summary"]["total_findings"], "finding", "findings"),
        _plural(len(report["recommendations"]), "recommendation", "recommendations")))
    add("")
    add("## The short version")
    add("")
    add(report["summary"]["verdict"])
    add("")
    add("| Severity | Count |")
    add("| --- | --- |")
    for key, label, _ in SEVERITY_HEADINGS:
        add("| {} | {} |".format(label, report["summary"].get(key, 0)))
    add("")

    by_id = {f["id"]: f for f in report["findings"]}
    if report["start_here"]:
        add("## Start here")
        add("")
        add("Three fixes, ranked by how much they change relative to the work involved.")
        add("")
        # A reader who sees a `high` finding below, absent from this list,
        # reasonably concludes the report contradicts itself. It does not:
        # rank is severity x reach / effort, so a high-severity problem on one
        # page of forty can sit below a medium one on all forty. Said here
        # because the alternative is the reader inferring a bug.
        severe = [by_id[i]["id"] for i in report["start_here"]]
        omitted = [f for f in report["findings"]
                   if f["severity"] in ("critical", "high") and f["id"] not in severe]
        if omitted:
            # Name the reason rather than offering the reader a choice of two.
            # The score is severity x reach / effort and we computed it, so we
            # know which term pushed the finding down.
            explained = []
            for f in omitted[:3]:
                reason = ("it affects {} of the {} pages crawled".format(
                    f["affected_page_count"], report["crawl"]["pages_crawled"])
                    if f.get("affected_page_count") else "it needs development time")
                if f["suggested_action"]["effort"] == "high":
                    reason = "it needs several days of development time"
                explained.append("{} ({}, {})".format(f["id"], f["severity"], reason))
            add("These are ranked by how much each changes relative to the work involved, not "
                "by severity alone. {} rank{} lower for that reason and {} listed in full "
                "below.".format(
                    "; ".join(explained), "s" if len(explained) == 1 else "",
                    "is" if len(explained) == 1 else "are"))
            add("")
        for position, finding_id in enumerate(report["start_here"], start=1):
            finding = by_id[finding_id]
            action = finding["suggested_action"]
            add("{}. **{}** ({}) — {}".format(position, finding["title"], finding["id"],
                                              action["summary"]))
            add("   Who does it: {}. Roughly: {}.".format(
                action["owner"], EFFORT_TIME[action["effort"]]))
        add("")

    for key, label, blurb in SEVERITY_HEADINGS:
        group = [f for f in report["findings"] if f["severity"] == key]
        if not group:
            continue
        group.sort(key=lambda f: (-f["suggested_action"]["priority_score"], f["id"]))
        add("## {} findings".format(label))
        add("")
        add("_{}_".format(blurb))
        add("")
        for finding in group:
            add(_render_finding(finding))
        add("")

    if report["recommendations"]:
        add("## Beyond the defects")
        add("")
        add("These are not problems with the site. They are things that would raise the chance "
            "of being found, quoted and trusted, and they are included only where the audit saw "
            "the condition that makes them relevant.")
        add("")
        for rec in report["recommendations"]:
            add("### {}".format(rec["title"]))
            add("")
            add(rec["summary"])
            add("")
            add("**Why this works.** {} — {}".format(
                MECHANISMS[rec["mechanism"]].rstrip("."), rec["why_this_works"]))
            add("")
            add("**How to do it** — {}, roughly {}:".format(rec["owner"], EFFORT_TIME[rec["effort"]]))
            for step in rec["how_to_do_it"]:
                add("- {}".format(step))
            if rec.get("snippet"):
                add("")
                add("```")
                add(rec["snippet"])
                add("```")
            add("")

    citations = report.get("citation_simulation") or []
    if citations:
        add("## What an assistant would quote")
        add("")
        add("For each key page, the sentence an assistant fetching it would most likely lift. "
            '"Nothing quotable" means the page contains no self-contained sentence stating a fact.')
        add("")
        add("| Page | Type | Likely quote |")
        add("| --- | --- | --- |")
        for citation in citations:
            quote = citation["likely_citation"]
            add("| {} | {} | {} |".format(
                citation["url"], citation["page_type"],
                _escape_cell(quote) if quote else "**Nothing quotable** — " + citation["why"]))
        add("")

    add("## Appendix: what was checked")
    add("")
    # The arithmetic, rather than a promise that the arithmetic works.
    #
    # This paragraph used to end "every check that ran appears in exactly one
    # place in this appendix", which is not true and cannot be: a check named
    # by a finding appears in the finding, above the appendix. A reader who
    # counted the three lists on a hospital site got 67 against a stated 68
    # and had no way to tell whether a check had gone missing or the sentence
    # was loose. Printing where all of them went lets them add it up.
    named_by_finding = len({f.get("check") for f in report["findings"] if f.get("check")})
    add("{} checks were run across {} skills: {} named by a finding above, {} that did not "
        "apply, {} that ran and found nothing wrong, and {} that contributed to a finding "
        "without being named by one. The audit made {} extra read-only requests beyond the "
        "crawl, used the user agent `{}`, and rendered in `{}` mode.".format(
            len(report["checks_run"]), len({c["skill"] for c in report["checks_run"]}),
            named_by_finding, len(report["not_applicable"]),
            len(report.get("checks_passed") or []),
            len(report.get("checks_that_found_something") or []),
            report["crawl"]["extra_requests_made"], report["crawl"]["user_agent"],
            report["crawl"]["render_mode"]))
    add("")
    if report["crawl"]["notes"]:
        for note in report["crawl"]["notes"]:
            add("- Note: {}".format(note))
        add("")

    if report["not_applicable"]:
        add("### Checks that did not apply, and why")
        add("")
        add("A check that stays quiet on purpose is as important as one that fires. These were "
            "run and found nothing to report:")
        add("")
        for item in report["not_applicable"]:
            add("- **{}** ({}): {}".format(item["check"], item.get("skill", ""), item["reason"]))
        add("")

    if report.get("checks_passed"):
        add("### Checks that ran and found nothing wrong")
        add("")
        add("Listed so that silence is never ambiguous. These ran against this site and were "
            "clean - they are not omissions:")
        add("")
        for item in report["checks_passed"]:
            add("- **{}** ({})".format(item["check"], item.get("skill", "")))
        add("")

    if report.get("checks_that_found_something"):
        add("### Checks that contributed to a finding above")
        add("")
        add("Each of these ran in the same step as a finding in this report and is part of "
            "why it was raised. They are listed for completeness: with them, every check "
            "that ran is accounted for in the count at the top of this appendix.")
        add("")
        for item in report["checks_that_found_something"]:
            add("- **{}** ({})".format(item["check"], item.get("skill", "")))
        add("")

    if report.get("unverifiable"):
        add("### Questions this audit could not answer")
        add("")
        add("This crawler was shown very little of the site, so the checks below have nothing "
            "to report either way. They are listed here rather than as defects, because "
            "\"we could not see it\" and \"it is not there\" are different statements and only "
            "one of them is something to fix.")
        add("")
        for item in report["unverifiable"]:
            add("- **{}** - {}.".format(item["title"], item["reason"]))
        add("")

    if report["merged_duplicates"]:
        add("### Observations merged")
        add("")
        for item in report["merged_duplicates"]:
            add("- `{}` from {} was merged into `{}`, which reports the same root cause.".format(
                item["id_hint"], item["skill"], item["merged_into"]))
        add("")

    add("---")
    add("")
    add("Generated by {} v{}. Read-only: this audit made no change to the site and submitted "
        "no forms.".format(report["auditor"]["name"], report["auditor"]["version"]))
    add("")
    return "\n".join(lines)


def _render_finding(finding):
    action = finding["suggested_action"]
    out = []
    out.append("### {} — {}".format(finding["id"], finding["title"]))
    out.append("")
    # The letter alone is an unexplained code, and this document is written for
    # a marketing manager. The expansion lives in report.json and in a
    # reference file, neither of which the person reading report.md has.
    out.append("**Priority: {}** · confidence {} · found by {}{}".format(
        action["priority"], finding["confidence"], finding["detected_by"],
        " and " + ", ".join(finding["also_detected_by"]) if finding.get("also_detected_by") else ""))
    out.append("")
    out.append("*Why this class of problem matters: {}.*".format(
        MECHANISMS[finding["mechanism"]].rstrip(".")))
    out.append("")
    out.append("**What we found.** {}".format(finding["evidence"]))
    out.append("")
    out.append("**Why it matters.** {}".format(action["rationale"]))
    out.append("")
    out.append("**What to do.** {}".format(action["summary"]))
    out.append("")
    out.append("Who does it: {}. Roughly: {}.".format(action["owner"], EFFORT_TIME[action["effort"]]))
    out.append("")
    for step in action["how_to_fix"]:
        out.append("- {}".format(step))
    if action.get("snippet"):
        out.append("")
        if action.get("snippet_warning"):
            out.append("> **{}**".format(action["snippet_warning"]))
            out.append("")
        # The snippet is pre-filled with values found on the site, and on a
        # staging or local origin that means a non-public URL inside code
        # labelled paste-ready. Somebody pastes it into a live template and
        # publishes a localhost logo path, so say so above the block.
        if _non_public_host(action["snippet"]):
            out.append("> **Change the URLs before you use this.** It is filled in with the "
                       "address that was audited, which is not a public one, so the `url`, "
                       "`logo` and any `offers.url` values below point somewhere nobody else "
                       "can reach.")
            out.append("")
        out.append("```")
        out.append(action["snippet"])
        out.append("```")
    if finding["affected_pages"]:
        out.append("")
        out.append("Affected pages ({} total, showing up to 5):".format(finding["affected_page_count"]))
        for url in finding["affected_pages"]:
            out.append("- {}".format(url))
    elif finding.get("example_withheld"):
        out.append("")
        out.append("*No per-page list is shown: {}. The counts above stand; the list did "
                   "not, so it is not printed.*".format(finding["example_withheld"]))
    out.append("")
    return "\n".join(out)


def _escape_cell(text):
    return (text or "").replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------
# Optional HTML rendering
# --------------------------------------------------------------------------

SEVERITY_COLOURS = {"critical": "#b00020", "high": "#c2410c", "medium": "#a16207",
                    "low": "#3f6212", "info": "#334155"}


def render_html(report):
    def esc(value):
        return html.escape(str(value or ""))

    parts = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
             "<title>AI readiness audit: {}</title>".format(esc(report["site"])),
             "<style>",
             "body{font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif;max-width:52rem;",
             "margin:2rem auto;padding:0 1rem;color:#111;background:#fff}",
             "h1,h2,h3{line-height:1.25} code,pre{font-family:ui-monospace,Menlo,Consolas,monospace}",
             "pre{background:#f6f7f9;padding:.75rem;overflow-x:auto;border-radius:6px;font-size:.85em}",
             ".badge{display:inline-block;padding:.1rem .5rem;border-radius:999px;color:#fff;",
             "font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.03em}",
             ".finding{border:1px solid #e5e7eb;border-radius:8px;padding:1rem 1.25rem;margin:1rem 0}",
             ".meta{color:#555;font-size:.85rem} table{border-collapse:collapse;width:100%}",
             "td,th{border-bottom:1px solid #e5e7eb;padding:.4rem .5rem;text-align:left;font-size:.9rem}",
             "@media (prefers-color-scheme:dark){body{background:#0b0e14;color:#e6e6e6}",
             "pre{background:#161b22}.finding{border-color:#2a2f3a}td,th{border-color:#2a2f3a}",
             ".meta{color:#9aa4b2}}",
             "</style></head><body>"]
    parts.append("<h1>AI readiness audit: {}</h1>".format(esc(report["site"])))
    parts.append('<p class="meta">Audited {} · {} pages crawled · {} findings</p>'.format(
        esc(report["audited_at"]), report["crawl"]["pages_crawled"],
        report["summary"]["total_findings"]))
    parts.append("<p>{}</p>".format(esc(report["summary"]["verdict"])))

    by_id = {f["id"]: f for f in report["findings"]}
    if report["start_here"]:
        parts.append("<h2>Start here</h2><ol>")
        for finding_id in report["start_here"]:
            finding = by_id[finding_id]
            parts.append("<li><strong>{}</strong> — {}</li>".format(
                esc(finding["title"]), esc(finding["suggested_action"]["summary"])))
        parts.append("</ol>")

    for key, label, _ in SEVERITY_HEADINGS:
        group = [f for f in report["findings"] if f["severity"] == key]
        if not group:
            continue
        parts.append("<h2>{} findings</h2>".format(esc(label)))
        for finding in sorted(group, key=lambda f: -f["suggested_action"]["priority_score"]):
            action = finding["suggested_action"]
            parts.append('<div class="finding">')
            parts.append('<h3><span class="badge" style="background:{}">{}</span> {} — {}</h3>'.format(
                SEVERITY_COLOURS[key], esc(key), esc(finding["id"]), esc(finding["title"])))
            parts.append('<p class="meta">Priority: {} · confidence {} · {} · {}</p>'.format(
                esc(action["priority"]), esc(finding["confidence"]),
                esc(action["owner"]), esc(EFFORT_TIME[action["effort"]])))
            parts.append('<p class="meta"><em>Why this class of problem matters: {}.</em></p>'
                         .format(esc(MECHANISMS[finding["mechanism"]].rstrip("."))))
            parts.append("<p><strong>What we found.</strong> {}</p>".format(esc(finding["evidence"])))
            parts.append("<p><strong>Why it matters.</strong> {}</p>".format(esc(action["rationale"])))
            parts.append("<p><strong>What to do.</strong> {}</p><ul>".format(esc(action["summary"])))
            for step in action["how_to_fix"]:
                parts.append("<li>{}</li>".format(esc(step)))
            parts.append("</ul>")
            if action.get("snippet"):
                parts.append("<pre><code>{}</code></pre>".format(esc(action["snippet"])))
            parts.append("</div>")

    if report["recommendations"]:
        parts.append("<h2>Beyond the defects</h2>")
        for rec in report["recommendations"]:
            parts.append('<div class="finding"><h3>{}</h3><p>{}</p>'.format(
                esc(rec["title"]), esc(rec["summary"])))
            parts.append("<p><strong>Why this works.</strong> {} — {}</p><ul>".format(
                esc(MECHANISMS[rec["mechanism"]].rstrip(".")), esc(rec["why_this_works"])))
            for step in rec["how_to_do_it"]:
                parts.append("<li>{}</li>".format(esc(step)))
            parts.append("</ul></div>")

    parts.append("<h2>Appendix</h2><p class=\"meta\">{} checks run; {} did not apply.</p>".format(
        len(report["checks_run"]), len(report["not_applicable"])))
    parts.append("</body></html>")
    return "".join(parts)


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--findings", nargs="+", required=True)
    parser.add_argument("--out-json", default="report.json")
    parser.add_argument("--out-md", default="report.md")
    parser.add_argument("--out-html", help="also write an HTML report to this path")
    parser.add_argument("--audited-at", help="override the report timestamp (used by tests)")
    args = parser.parse_args(argv)

    snapshot = load_snapshot(args.snapshot)
    results = load_skill_results(args.findings)
    report = compose(snapshot, results, audited_at=args.audited_at)

    write_json(args.out_json, report)
    with open(args.out_md, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    if args.out_html:
        with open(args.out_html, "w", encoding="utf-8") as handle:
            handle.write(render_html(report))

    summary = report["summary"]
    print("composed {} finding(s) [{} critical, {} high, {} medium, {} low, {} info] and {} "
          "recommendation(s) -> {}".format(
              summary["total_findings"], summary["critical"], summary["high"],
              summary["medium"], summary["low"], summary["info"],
              len(report["recommendations"]), args.out_json), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
