#!/usr/bin/env python3
"""freshness-corroboration-audit: is the brand current, agreed-upon and unambiguous? (mechanism D)

Reads snapshot.json, writes findings JSON. Makes at most 4 extra read-only
requests, all to Wikidata's public search API, to count how many distinct
entities share the brand name.

`--now` fixes the reference date so a run is reproducible; it defaults to the
current UTC date.

Usage:
    python check.py --snapshot snapshot.json --out freshness-corroboration-audit.findings.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from urllib.parse import quote

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
    CONTENT_TYPES, SkillResult, load_snapshot, pages_of, pct, sample, truncate,
    FetchError, Fetcher,
)

SKILL = "freshness-corroboration-audit"

# Thresholds and their reasons.
STALE_MONTHS = 18            # 18 months is the point at which "recent" claims stop being defensible
STALE_SHARE = 0.7            # most of the library being old is a programme problem, not one old post
COPYRIGHT_LAG_YEARS = 1      # a footer two calendar years behind reads as an abandoned site
AS_OF_LAG_YEARS = 2          # "as of 2023" written in 2026 actively misinforms
MIN_AUTHORITATIVE_PROFILES = 3  # still used to judge whether a brand has disambiguated itself
WIKIDATA_AMBIGUITY_THRESHOLD = 2

# Off-site profile breadth. These numbers are measured, not chosen: in a
# within-category study of 29 crawlable sites across 6 categories, brands
# assistants name linked a mean of 7.2 distinct off-site profiles and
# comparable brands they do not name linked 4.6. It was the only signal that
# moved the same way in every category. See references/cited-vs-uncited-study.md.
PROFILE_BREADTH_NAMED_MEAN = 7.2
PROFILE_BREADTH_UNNAMED_MEAN = 4.6
PROFILE_BREADTH_GOOD = 6   # at or above this, the footprint is not the problem
PROFILE_BREADTH_THIN = 3   # below this a brand is close to a single unlinked claim

AUTHORITATIVE = ("LinkedIn", "Wikipedia", "Wikidata", "Crunchbase", "GitHub",
                 "Google Business", "Trustpilot", "Yelp", "Glassdoor")

WIKIDATA_API = ("https://www.wikidata.org/w/api.php?action=wbsearchentities"
                "&search={}&language=en&uselang=en&format=json&limit=10&type=item")

MAX_EXTRA_REQUESTS = 4

_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def parse_date(value):
    """Parse the date formats sites actually publish. Returns a date or None."""
    if not value:
        return None
    text = str(value).strip()
    match = _ISO_RE.search(text)
    if match:
        try:
            return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,})\.?\s+((?:19|20)\d{2})\b",
                      text, re.I)
    if match and match.group(2)[:3].lower() in _MONTHS:
        try:
            return dt.date(int(match.group(3)), _MONTHS[match.group(2)[:3].lower()], int(match.group(1)))
        except ValueError:
            return None
    match = re.search(r"\b([A-Za-z]{3,})\.?\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b", text)
    if match and match.group(1)[:3].lower() in _MONTHS:
        try:
            return dt.date(int(match.group(3)), _MONTHS[match.group(1)[:3].lower()], int(match.group(2)))
        except ValueError:
            return None
    return None


def newest_date(page):
    """The most recent date signal on a page, machine-readable ones first."""
    dates = page.get("dates") or {}
    candidates = []
    for value in (dates.get("machine_readable") or []) + (dates.get("visible") or []):
        parsed = parse_date(value)
        if parsed:
            candidates.append(parsed)
    return max(candidates) if candidates else None


def months_between(earlier, later):
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def run(snapshot, now=None, allow_network=True):
    result = SkillResult(SKILL)
    now = now or dt.datetime.now(dt.timezone.utc).date()
    pages = pages_of(snapshot, content_only=True)
    brand = snapshot.get("brand") or {}

    if not pages:
        for name in ("content-freshness", "date-signals-present", "footer-copyright-year",
                     "stale-year-references", "authoritative-profiles", "entity-ambiguity",
                     "fact-consistency-across-pages"):
            result.skip(name, "no content pages returned HTTP 200")
        return result

    _check_article_freshness(result, pages, now)
    _check_date_signals(result, pages)
    _check_copyright_year(result, pages, now)
    _check_stale_year_references(result, pages, now)
    profiles = _check_authoritative_profiles(result, snapshot, pages)
    _check_sitemap_lastmod(result, snapshot, now)
    _check_fact_consistency(result, pages)

    fetcher = None
    if allow_network:
        try:
            fetcher = Fetcher(max_requests=MAX_EXTRA_REQUESTS)
        except FetchError:
            fetcher = None
    _check_entity_ambiguity(result, snapshot, pages, brand, profiles, fetcher, allow_network)
    if fetcher is not None:
        result.extra_requests_made = fetcher.count

    result.signal("press_page_present", any(p["page_type"] == "press" for p in pages))
    result.signal("reference_date", now.isoformat())

    # Site-wide date coverage, recorded as a signal rather than a finding.
    # In our cited-versus-uncited study this was the one hygiene measure that
    # separated the two cohorts: reference sites that assistants quote
    # constantly dated 85% of crawled pages, ordinary brand sites 35%.
    # See references/cited-vs-uncited-study.md.
    dated = sum(1 for p in pages if (p.get("dates") or {}).get("has_any"))
    result.signal("date_coverage", round(dated / float(len(pages)), 2))
    result.signal("dated_page_count", dated)
    return result


# --------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------

def _check_article_freshness(result, pages, now):
    result.check("content-freshness")
    articles = [p for p in pages if p["page_type"] == "article"]
    if len(articles) < 3:
        result.skip("content-freshness",
                    "fewer than 3 article pages were crawled, so there is no publishing "
                    "cadence to assess")
        return

    dated = [(p, newest_date(p)) for p in articles]
    dated = [(p, d) for p, d in dated if d]
    if not dated:
        result.skip("content-freshness",
                    "no article carries a parsable date, which is reported separately as a "
                    "missing date signal rather than as staleness")
        return

    newest = max(d for _, d in dated)
    stale = [(p, d) for p, d in dated if months_between(d, now) > STALE_MONTHS]
    share = len(stale) / float(len(dated))
    result.signal("newest_article_date", newest.isoformat())

    if share < STALE_SHARE or months_between(newest, now) <= 12:
        result.skip("content-freshness",
                    "the newest article is dated {} ({} month(s) old) and {}% of dated articles "
                    "are within {} months".format(
                        newest.isoformat(), months_between(newest, now),
                        round(100 * (1 - share)), STALE_MONTHS))
        return

    result.add(
        id_hint="published-content-has-gone-stale",
        # Scoped to what was crawled. On a large site the audit sees a sample,
        # and claiming the whole site has stalled would overstate the evidence.
        title="The articles reached by this crawl have not been updated in over a year",
        severity="medium", confidence="high",
        evidence="{} of {} dated article(s) ({}%) are older than {} months, and the newest is "
                 "{} ({} months old). Examples: {}.".format(
                     len(stale), len(dated), pct(len(stale), len(dated)), STALE_MONTHS,
                     newest.isoformat(), months_between(newest, now),
                     ", ".join(sample([p["url"] for p, _ in stale], 5))),
        mechanism="D", root_cause="stale-content",
        summary="Publish or refresh content on a regular cadence, and update dateModified when you do.",
        how_to_fix=[
            "Pick the 5 most valuable existing pages and revise them now, updating both the "
            "visible date and the `dateModified` property.",
            "Set a cadence you can actually keep, even if it is one substantive post a quarter.",
            'Only change `dateModified` when the content genuinely changed; refreshing the date '
            "without editing the text is detected and discounted.",
        ],
        effort="high", owner="content owner",
        rationale="Mechanism D: when two sources disagree, the more recent one usually wins. A "
                  "library that stopped two years ago loses those comparisons by default, "
                  "whatever the quality of the writing.",
        affected_pages=[p["url"] for p, _ in stale],
    )


def _check_date_signals(result, pages):
    """No date at all is a distinct problem from having an old date."""
    result.check("date-signals-present")
    expect_dates = [p for p in pages if p["page_type"] in ("article", "press")]
    if not expect_dates:
        result.skip("date-signals-present",
                    "no article or press pages were crawled, and dates are not expected on "
                    "evergreen pages such as pricing or contact")
        result.signal("no_date_signals", False)
        return

    undated = [p for p in expect_dates if not (p.get("dates") or {}).get("has_any")]
    result.signal("no_date_signals", len(undated) == len(expect_dates))

    if not undated:
        result.skip("date-signals-present",
                    "every article and press page carries at least one date signal")
        return

    result.add(
        id_hint="content-pages-carry-no-date",
        title="{} of {} article page(s) carry no date at all".format(len(undated), len(expect_dates)),
        severity="medium", confidence="high",
        evidence="Pages with no <time> element, no datePublished/dateModified property and no "
                 "visible date in the text: {}.".format(
                     ", ".join(sample([p["url"] for p in undated], 5))),
        mechanism="D", root_cause="no-date-signal",
        summary="Show a visible published or updated date on every article, and mirror it in "
                "datePublished/dateModified.",
        how_to_fix=[
            "Add a visible line under the headline: Published <date>, last updated <date>.",
            "Add the same values to the Article JSON-LD as datePublished and dateModified.",
            "Wrap visible dates in <time datetime=\"YYYY-MM-DD\"> so they are unambiguous.",
        ],
        effort="low", owner="developer",
        rationale="Mechanism D: with no date, a consumer cannot tell whether a page is current. "
                  "Faced with an undated page and a dated competitor making the same claim, it "
                  "has an easy reason to prefer the competitor.",
        affected_pages=[p["url"] for p in undated],
    )


def _check_copyright_year(result, pages, now):
    result.check("footer-copyright-year")
    years = set()
    carriers = []
    for page in pages:
        page_years = (page.get("dates") or {}).get("copyright_years") or []
        if page_years:
            years.update(page_years)
            carriers.append(page["url"])
    if not years:
        result.skip("footer-copyright-year", "no copyright year was found in the page text")
        return

    newest = max(years)
    if newest >= now.year - COPYRIGHT_LAG_YEARS:
        result.skip("footer-copyright-year",
                    "the newest copyright year on the site is {}, which is current as of "
                    "{}".format(newest, now.isoformat()))
        return

    result.add(
        id_hint="footer-copyright-year-is-stale",
        title="The copyright year in the footer is {}".format(newest),
        severity="low", confidence="high",
        evidence="The most recent copyright year found across {} page(s) is {}, {} years behind "
                 "the reference date {}.".format(
                     len(carriers), newest, now.year - newest, now.isoformat()),
        mechanism="D", root_cause="stale-content",
        summary="Render the copyright year dynamically so it is always current.",
        how_to_fix=[
            "Replace the hard-coded year in the footer template with the server's current year.",
            "A range such as 2019-{} is fine and communicates longevity.".format(now.year),
        ],
        effort="low", owner="developer",
        rationale="Mechanism D: the footer year is the cheapest liveness signal on a site. A "
                  "stale one suggests nobody has touched the site, which colours how the rest "
                  "of the content is weighed.",
        affected_pages=sorted(carriers),
    )


def _check_stale_year_references(result, pages, now):
    result.check("stale-year-references")
    offenders = []
    for page in pages:
        stale_years = [y for y in ((page.get("dates") or {}).get("as_of_years") or [])
                       if y <= now.year - AS_OF_LAG_YEARS]
        if stale_years:
            offenders.append((page, max(stale_years)))
    if not offenders:
        result.skip("stale-year-references",
                    'no page contains an "as of <year>" or "updated <year>" reference more than '
                    "{} years old".format(AS_OF_LAG_YEARS))
        return

    result.add(
        id_hint="copy-references-an-old-year",
        title="{} page(s) still describe themselves as current as of an old year".format(len(offenders)),
        severity="low", confidence="medium",
        evidence="Pages containing a phrase such as \"as of <year>\" or \"updated <year>\" "
                 "where the year is {} or earlier: {}.".format(
                     now.year - AS_OF_LAG_YEARS,
                     "; ".join("{} (references {})".format(p["url"], y)
                               for p, y in sorted(offenders, key=lambda x: x[0]["url"])[:5])),
        mechanism="D", root_cause="stale-content",
        summary="Update or remove the year references in evergreen copy.",
        how_to_fix=[
            "Review each page and either refresh the facts and the year, or remove the year "
            "reference so the copy stops asserting a currency it does not have.",
            'Avoid relative phrases such as "this year" and "recently"; they age silently.',
        ],
        effort="low", owner="content owner",
        rationale="Mechanism D: an explicit old year in the text is stronger evidence of "
                  "staleness than a missing date, because the page states it about itself.",
        affected_pages=[p["url"] for p, _ in offenders],
    )


def _check_sitemap_lastmod(result, snapshot, now):
    """Both halves of sitemap dating: how many entries carry a date, and how old they are.

    crawl-access-audit owns whether a sitemap exists and resolves. Whether its
    dates mean anything is mechanism D, so it belongs here. Splitting the two
    across skills left one root cause with two owners.
    """
    result.check("sitemap-lastmod-coverage")
    result.check("sitemap-lastmod-recency")

    entries = []
    total_urls = 0
    for record in snapshot.get("sitemaps") or []:
        for entry in record.get("urls") or []:
            total_urls += 1
            parsed = parse_date(entry.get("lastmod"))
            if parsed:
                entries.append(parsed)

    if not total_urls:
        for name in ("sitemap-lastmod-coverage", "sitemap-lastmod-recency"):
            result.skip(name, "no sitemap URLs were found, so there are no dates to assess")
        return

    coverage = len(entries) / float(total_urls)
    result.signal("sitemap_lastmod_coverage", round(coverage, 2))

    if coverage < 0.5:
        result.add(
            id_hint="sitemap-lastmod-sparse",
            title="Most sitemap entries carry no <lastmod> date",
            severity="low", confidence="high",
            evidence="{} of {} sitemap entries ({}%) have a <lastmod> value.".format(
                len(entries), total_urls, pct(len(entries), total_urls)),
            mechanism="D", root_cause="no-date-signal",
            summary="Emit an accurate <lastmod> for every sitemap entry.",
            how_to_fix=[
                "Configure the sitemap generator to write <lastmod> from the page's real "
                "modification date.",
                "Do not set <lastmod> to today's date on every build. A date that always "
                "changes carries no information, and crawlers learn to ignore it.",
            ],
            effort="low", owner="developer",
            rationale="Mechanism D: <lastmod> is how a crawler decides which pages are worth "
                      "re-fetching. Without it, updated pages are re-read on a slow default "
                      "cycle, so your changes take longer to be noticed.",
        )
    else:
        result.skip("sitemap-lastmod-coverage",
                    "{}% of sitemap entries carry a <lastmod> value".format(
                        pct(len(entries), total_urls)))

    if len(entries) < 5:
        result.skip("sitemap-lastmod-recency",
                    "fewer than 5 sitemap entries carry a parsable <lastmod>, so the "
                    "distribution is not meaningful")
        return

    newest = max(entries)
    # median-low rather than statistics.median: averaging two dates is not
    # defined, and on an even-length list the mean would raise.
    median = sorted(entries)[(len(entries) - 1) // 2]
    result.signal("sitemap_newest_lastmod", newest.isoformat())
    if months_between(newest, now) <= 12:
        result.skip("sitemap-lastmod-recency",
                    "the newest sitemap <lastmod> is {} and the median is {}".format(
                        newest.isoformat(), median.isoformat()))
        return

    result.add(
        id_hint="sitemap-shows-no-recent-changes",
        title="No page in the sitemap has been modified in over a year",
        severity="low", confidence="high",
        evidence="Across {} dated sitemap entries the newest <lastmod> is {} and the median is "
                 "{}, against a reference date of {}.".format(
                     len(entries), newest.isoformat(), median.isoformat(), now.isoformat()),
        mechanism="D", root_cause="stale-content",
        summary="Refresh the highest-value pages and let the sitemap dates follow.",
        how_to_fix=[
            "Update the pages that matter most commercially, then regenerate the sitemap.",
            "Confirm the generator writes real modification dates rather than the build date.",
        ],
        effort="medium", owner="content owner",
        rationale="Mechanism D: <lastmod> is the signal crawlers use to decide re-fetch "
                  "frequency. A site where nothing has changed for a year is re-read rarely, so "
                  "any change you do make takes longer to be noticed.",
    )


# --------------------------------------------------------------------------
# Corroboration
# --------------------------------------------------------------------------

def _check_authoritative_profiles(result, snapshot, pages):
    """Off-site profile breadth: the strongest measured signal in this marketplace.

    In our within-category study this was the only measure that moved the same
    way in all six categories. Brands an assistant names linked a median of
    about 7 distinct off-site profiles; comparable competitors it does not name
    linked about 4.6. It held for running shoes, CRMs, coffee, mattresses,
    password managers and standing desks alike.

    An earlier version counted only nine hand-picked "authoritative" platforms
    and fired on 94% of named brands and 100% of unnamed ones - it separated
    nothing, so it carried no information. Breadth across every recognised
    platform is what actually tracks the divide, so that is what is measured.
    See references/cited-vs-uncited-study.md.
    """
    result.check("off-site-profile-breadth")
    profiles = {}
    for page in pages:
        profiles.update(page.get("social_profiles") or {})

    authoritative = {k: v for k, v in profiles.items() if k in AUTHORITATIVE}
    result.signal("profile_platforms", sorted(profiles.keys()))
    result.signal("profile_breadth", len(profiles))
    result.signal("authoritative_profile_count", len(authoritative))
    result.signal("has_wikidata_or_wikipedia",
                  any(k in ("Wikidata", "Wikipedia") for k in profiles))

    if len(profiles) >= PROFILE_BREADTH_GOOD:
        result.skip("off-site-profile-breadth",
                    "the site links to {} distinct off-site profiles ({}), at or above the "
                    "level typical of brands assistants name in our study".format(
                        len(profiles), ", ".join(sorted(profiles))))
        return profiles

    result.add(
        id_hint="thin-off-site-profile-footprint",
        title="The site links to only {} off-site profile{}".format(
            len(profiles), "" if len(profiles) == 1 else "s"),
        severity="medium" if len(profiles) < PROFILE_BREADTH_THIN else "low",
        confidence="high",
        evidence="Distinct off-site profiles linked from the crawled pages or listed in "
                 "`sameAs`: {}. In our within-category study, brands assistants name linked "
                 "a mean of {} and comparable brands they do not name linked {} - the only "
                 "measure that pointed the same way in all six categories tested.".format(
                     ", ".join(sorted(profiles)) or "none",
                     PROFILE_BREADTH_NAMED_MEAN, PROFILE_BREADTH_UNNAMED_MEAN),
        mechanism="D", root_cause="weak-corroboration",
        summary="Claim more of the places that independently confirm the brand exists, and list "
                "every one of them in Organization `sameAs`.",
        how_to_fix=[
            "Claim the profiles that apply to your category. Breadth is what matters here, not "
            "prestige: the brands assistants name are on more platforms, not better ones.",
            "Cover the obvious ones first: LinkedIn, Instagram, YouTube, X, Facebook, and your "
            "industry's directories.",
            "Use the identical name and the identical one-sentence description on every profile. "
            "Wording that matches exactly is what makes them corroborate rather than merely "
            "coexist.",
            "List every profile URL in the Organization `sameAs` array on your site.",
            "Link back to the site from each profile, so the connection is stated from both ends.",
        ],
        effort="medium", owner="marketing",
        rationale="Mechanism D: a fact stated only on the brand's own site is one source's word "
                  "for it. Each additional profile that repeats the same name and description is "
                  "another independent source agreeing. This is the strongest signal we measured, "
                  "and it is almost entirely within a brand's control.",
    )
    return profiles


def _check_entity_ambiguity(result, snapshot, pages, brand, profiles, fetcher, allow_network):
    """Does anything else share this name, and does the site say which one it is?"""
    result.check("entity-ambiguity")
    brand_name = (brand.get("name") or "").strip()
    if not brand_name or len(brand_name) < 3:
        result.skip("entity-ambiguity", "no usable brand name was detected")
        return

    query = '"{}"'.format(brand_name)
    result.signal("suggested_web_search", query)

    if not allow_network or fetcher is None:
        result.skip("entity-ambiguity",
                    "external lookups were disabled for this run, so the number of entities "
                    "sharing this name could not be counted")
        return

    response = fetcher.try_get(WIKIDATA_API.format(quote(brand_name)))
    if response is None:
        result.skip("entity-ambiguity",
                    "the Wikidata search API could not be reached; this is an audit-environment "
                    "limitation, not a site defect")
        return
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        result.skip("entity-ambiguity", "the Wikidata search API returned an unreadable response")
        return

    # Wikidata search matches by prefix, so "GOV.UK" returns "GOV.UK One Login"
    # and "GOV.UK Verify" - the same organisation's own services, not a name
    # collision. Only entities whose label is essentially the brand name count.
    def _key(value):
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    brand_key = _key(brand_name)
    matches = [m for m in (payload.get("search") or [])
               if _key(m.get("label")) == brand_key
               or brand_key in {_key(a) for a in (m.get("aliases") or [])}]
    result.signal("wikidata_match_count", len(matches))
    result.signal("wikidata_matches",
                  [{"id": m.get("id"), "label": m.get("label"),
                    "description": truncate(m.get("description", ""), 90)}
                   for m in matches[:5]])

    # Disambiguation the site already provides.
    alternate_names = []
    has_description = False
    for page in pages:
        for node in page.get("jsonld") or []:
            if node.get("alternateName"):
                alternate_names.append(str(node["alternateName"]))
            if node.get("description"):
                has_description = True
    linked_to_wikidata = any(k in ("Wikidata", "Wikipedia") for k in profiles)
    disambiguated = linked_to_wikidata or (bool(alternate_names) and has_description
                                           and len(profiles) >= MIN_AUTHORITATIVE_PROFILES)

    if len(matches) < WIKIDATA_AMBIGUITY_THRESHOLD:
        result.skip("entity-ambiguity",
                    'Wikidata returns {} entity matching "{}", so there is no obvious name '
                    "collision to resolve".format(len(matches), brand_name))
        return
    if disambiguated:
        result.skip("entity-ambiguity",
                    '{} Wikidata entities share the name "{}", but the site already '
                    "disambiguates itself with a Wikidata/Wikipedia link or an alternateName "
                    "plus description and {} profiles".format(len(matches), brand_name, len(profiles)))
        return

    result.add(
        id_hint="brand-name-collides-with-other-entities",
        title='{} other entities share the name "{}"'.format(len(matches), brand_name),
        severity="high" if len(matches) >= 4 else "medium", confidence="medium",
        evidence='Wikidata returns {} item(s) for "{}": {}. The site declares no Wikidata or '
                 "Wikipedia link{}.".format(
                     len(matches), brand_name,
                     "; ".join("{} ({})".format(m.get("label"), m.get("description") or "no description")
                               for m in matches[:4]),
                     " and no alternateName" if not alternate_names else ""),
        mechanism="D", root_cause="entity-ambiguity",
        summary="Publish the markers that tell a machine which of these entities you are.",
        how_to_fix=[
            "Create a Wikidata item for the company if one does not exist, with the same "
            "one-sentence description you use everywhere else, and link it from Organization "
            "`sameAs`.",
            "Add `alternateName` to Organization markup listing the other ways people write "
            "the name.",
            "Always pair the brand name with a disambiguating phrase in prose: "
            '"{} the &lt;category&gt; company" rather than "{}" alone.'.format(brand_name, brand_name),
            "Fill in the founding year, location and industry in Organization markup; those "
            "three fields are what separate same-named entities.",
        ],
        effort="medium", owner="marketing",
        rationale="Mechanism D: when several things share a name, a system either picks one or "
                  "blends them. Without explicit markers the brand's facts get attributed to "
                  "whichever entity is better documented, which is usually not the smaller one.",
    )


def _check_fact_consistency(result, pages):
    """Contact and boilerplate facts that contradict each other across the site.

    Scope note: JSON-LD name and price mismatches belong to structured-data-audit.
    This check owns telephone, postal address and the description boilerplate.
    """
    result.check("fact-consistency-across-pages")
    conflicts = []

    descriptions = {}
    for page in pages:
        for node in page.get("jsonld") or []:
            types = node.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if not any(str(t).lower() in ("organization", "localbusiness", "corporation",
                                          "store", "restaurant") for t in types):
                continue
            description = str(node.get("description") or "").strip()
            if description:
                descriptions.setdefault(re.sub(r"\s+", " ", description.lower()), []).append(page["url"])

            declared_phone = re.sub(r"\D", "", str(node.get("telephone") or ""))
            if declared_phone and len(declared_phone) >= 7:
                visible = {re.sub(r"\D", "", p) for p in
                           (page.get("contact_facts") or {}).get("phones") or []}
                visible = {v for v in visible if len(v) >= 7}
                if visible and not any(declared_phone.endswith(v[-7:]) or v.endswith(declared_phone[-7:])
                                       for v in visible):
                    conflicts.append((page["url"],
                                      "Organization telephone {} does not match the number(s) "
                                      "shown on the page".format(node.get("telephone"))))

            address = node.get("address")
            if isinstance(address, dict) and address.get("postalCode"):
                declared_postcode = re.sub(r"\s+", "", str(address["postalCode"])).lower()
                visible_postcode = re.sub(
                    r"\s+", "", ((page.get("contact_facts") or {}).get("postcode_hint") or "")).lower()
                if visible_postcode and declared_postcode and visible_postcode != declared_postcode:
                    conflicts.append((page["url"],
                                      "Organization postal code {} does not match the code shown "
                                      "on the page ({})".format(address["postalCode"], visible_postcode)))

    if len(descriptions) > 1:
        pairs = sorted(descriptions.items(), key=lambda kv: kv[0])
        conflicts.append((pairs[0][1][0],
                          "{} different Organization descriptions are published across the site; "
                          "the boilerplate is not one sentence but several".format(len(descriptions))))

    result.signal("distinct_org_descriptions", len(descriptions))

    if not conflicts:
        result.skip("fact-consistency-across-pages",
                    "telephone, postal code and the Organization description are consistent "
                    "wherever they are declared")
        return

    result.add(
        id_hint="facts-contradict-across-pages",
        title="The site contradicts itself on contact details or boilerplate",
        severity="high", confidence="medium",
        evidence="{} conflict(s): {}.".format(
            len(conflicts), "; ".join("{} - {}".format(u, w) for u, w in sorted(conflicts)[:4])),
        mechanism="D", root_cause="nap-inconsistency",
        summary="Agree one canonical version of each fact and publish it from a single source.",
        how_to_fix=[
            "Write one canonical block: legal name, one-sentence description, address, "
            "telephone, email.",
            "Store it once in the CMS or as a shared template partial and include it "
            "everywhere, rather than retyping it per page.",
            "Push the same values to every off-site profile so the whole web agrees.",
            "Where two values are both real (a sales line and a support line), label each "
            "explicitly rather than leaving a machine to guess which is primary.",
        ],
        effort="medium", owner="marketing",
        rationale="Mechanism D: a brand that disagrees with itself is the hardest case for a "
                  "consumer weighing sources. Internal contradictions do more damage than "
                  "silence, because they undermine every other fact on the site too.",
        affected_pages=sorted({u for u, _ in conflicts}),
        snippet="Canonical boilerplate to reuse verbatim on the site, LinkedIn, the press kit "
                "and every directory:\n\n"
                "  <Brand> is a <category> that <does what> for <whom>. Founded in <year> in "
                "<city>, it <one distinguishing fact>.\n"
                "  <Street>, <City>, <Postcode>, <Country>. <Telephone>. <Email>.",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--now", help="reference date as YYYY-MM-DD (defaults to today, UTC)")
    parser.add_argument("--no-network", action="store_true",
                        help="skip the Wikidata entity-ambiguity lookup")
    args = parser.parse_args(argv)

    now = parse_date(args.now) if args.now else None
    if args.now and now is None:
        parser.error("--now must be a date in YYYY-MM-DD form")

    result = run(load_snapshot(args.snapshot), now=now, allow_network=not args.no_network)
    result.write(args.out)
    print("{}: {} finding(s), {} extra request(s)".format(
        SKILL, len(result.findings), result.extra_requests_made), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
