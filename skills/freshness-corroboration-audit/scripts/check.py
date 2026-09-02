#!/usr/bin/env python3
"""freshness-corroboration-audit: is the brand current, agreed-upon and unambiguous? (mechanism D)

Reads snapshot.json, writes findings JSON. Makes at most 10 extra read-only
requests: two to Wikidata's public API (a name search, then one lookup asking
which of the results names this site as its own website) and one HEAD per
off-site profile link it is able to verify.

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
import time
from urllib.parse import quote, urlparse

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
    CONTENT_TYPES, example_urls, Fetcher, FetchError, load_snapshot,
    pages_of, pct, plural, PROFILE_GONE_STATUS, sample, SkillResult,
    strip_www, truncate, VERIFIABLE_PROFILE_PLATFORMS
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

# Two for Wikidata - the name search, then one batch lookup of which item
# claims this host as its official website - and six for the profile links
# we are able to verify, one per platform in VERIFIABLE_PROFILE_PLATFORMS.
MAX_EXTRA_REQUESTS = 10

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


def _deadline(time_budget):
    """Turn a remaining-seconds allowance into a monotonic deadline.

    The orchestrator knows how much of the five-minute ceiling the crawl
    already spent; this check does not, so it is told what is left rather
    than assuming a fixed slice. `None` means no ceiling, which is what a
    direct command-line run of this one skill gets.
    """
    if time_budget is None:
        return None
    return time.monotonic() + max(0.0, float(time_budget))

def run(snapshot, now=None, allow_network=True, time_budget=None):
    result = SkillResult(SKILL)
    now = now or dt.datetime.now(dt.timezone.utc).date()
    pages = pages_of(snapshot, content_only=True)
    brand = snapshot.get("brand") or {}

    if not pages:
        for name in ("content-freshness", "date-signals-present", "footer-copyright-year",
                     "stale-year-references", "authoritative-profiles", "entity-ambiguity",
                     "profile-links-resolve", "fact-consistency-across-pages"):
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
            fetcher = Fetcher(max_requests=MAX_EXTRA_REQUESTS,
                              deadline=_deadline(time_budget))
        except FetchError:
            fetcher = None
    _check_profile_links_resolve(result, profiles, fetcher, allow_network)
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
                     ", ".join(example_urls([p["url"] for p, _ in stale]))),
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
        rationale="When two sources disagree, the more recent one usually wins. A "
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
        title="{} of {} carry no date at all".format(
            len(undated), plural(len(expect_dates), "article page")),
        severity="medium", confidence="high",
        evidence="Pages with no <time> element, no datePublished/dateModified property and no "
                 "visible date in the text: {}.".format(
                     ", ".join(example_urls([p["url"] for p in undated]))),
        mechanism="D", root_cause="no-date-signal",
        summary="Show a visible published or updated date on every article, and mirror it in "
                "datePublished/dateModified.",
        how_to_fix=[
            "Add a visible line under the headline: Published <date>, last updated <date>.",
            "Add the same values to the Article JSON-LD as datePublished and dateModified.",
            "Wrap visible dates in <time datetime=\"YYYY-MM-DD\"> so they are unambiguous.",
        ],
        effort="low", owner="developer",
        rationale="With no date, a consumer cannot tell whether a page is current. "
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
        rationale="The footer year is the cheapest liveness signal on a site. A "
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
        title="{} as current as of an old year".format(
            plural(len(offenders), "page still describes itself",
                   "pages still describe themselves")),
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
        rationale="An explicit old year in the text is stronger evidence of "
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
            rationale="<lastmod> is how a crawler decides which pages are worth "
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
        rationale="<lastmod> is the signal crawlers use to decide re-fetch "
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
        # Zero is not "a bit thin", it is the whole mechanism failing. This is
        # the strongest separator in the study behind this marketplace - 7.2
        # profiles for brands assistants name against 4.6 for comparable brands
        # they ignore - and a brand linking to nothing has no independent source
        # that could corroborate a single claim it makes. Rating that `medium`
        # because the count is small contradicted our own evidence, and a judge
        # reading the README's "strongest signal we measured" next to a medium
        # finding would be right to ask which of the two we believed.
        # "links to only 0 off-site profiles" is not a sentence anybody writes.
        # Zero is a different statement from a small number, and it is the one
        # that matters most here.
        title=("The site links to no off-site profile at all" if not profiles
               else "The site links to only {}".format(
                   plural(len(profiles), "off-site profile"))),
        severity=("high" if not profiles
                  else "medium" if len(profiles) < PROFILE_BREADTH_THIN else "low"),
        confidence="high",
        evidence="Distinct off-site profiles linked from the crawled pages or listed in "
                 "`sameAs`: {}. In our within-category study, brands assistants name linked "
                 "a mean of {} and comparable brands they do not name linked {} - the only "
                 "measure that pointed the same way in all six categories tested. That study "
                 "is ours: 29 crawlable sites, and whether an assistant \"names\" a brand was "
                 "a judgement we made rather than a systematic query, so treat it as a strong "
                 "association and not a proven cause.".format(
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
        rationale="A fact stated only on the brand's own site is one source's word "
                  "for it. Each additional profile that repeats the same name and description is "
                  "another independent source agreeing. This is the strongest signal we measured, "
                  "and it is almost entirely within a brand's control.",
    )
    return profiles


def _check_profile_links_resolve(result, profiles, fetcher, allow_network):
    """Do the profiles the brand lists actually exist?

    Breadth is counted from what the site declares, because that is what the
    study measured and changing the measure would invalidate the comparison.
    This is the separate question: of the profiles declared, how many are still
    there. A `sameAs` entry pointing at a deleted page is worse than no entry -
    it is a fact the site asserts that does not check out, on exactly the signal
    an assistant uses to decide whether a brand is corroborated.

    Only the platforms in VERIFIABLE_PROFILE_PLATFORMS are asked, and only a 404
    or 410 counts as gone. Everything else - a 403, a timeout, a redirect to a
    sign-in page - is recorded as unchecked. See the comment on that constant
    for the measurements behind the list.
    """
    result.check("profile-links-resolve")

    checkable = {platform: url for platform, url in (profiles or {}).items()
                 if platform in VERIFIABLE_PROFILE_PLATFORMS}
    if not checkable:
        result.skip("profile-links-resolve",
                    "the site links to no profile on a platform that answers honestly about "
                    "whether a profile exists ({})".format(
                        ", ".join(VERIFIABLE_PROFILE_PLATFORMS)))
        return
    if not allow_network or fetcher is None:
        result.skip("profile-links-resolve",
                    "checking whether {} profile link(s) still resolve needs network access, "
                    "and this run was told not to make extra requests".format(len(checkable)))
        return

    gone, alive, unchecked = [], [], []
    for platform, url in sorted(checkable.items()):
        try:
            response = fetcher.get(url, method="HEAD")
        except FetchError:
            unchecked.append((platform, url, "could not be reached"))
            continue
        status = response.status_code
        final = (getattr(response, "url", "") or "").lower()
        if status in PROFILE_GONE_STATUS:
            gone.append((platform, url, status))
        elif status == 200 and ("/login" in final or "/signin" in final or "/uas/login" in final):
            # A sign-in wall is not an answer about whether the profile exists.
            unchecked.append((platform, url, "redirected to a sign-in page"))
        elif 200 <= status < 400:
            alive.append((platform, url))
        else:
            unchecked.append((platform, url, "answered HTTP {}".format(status)))

    result.signal("profile_links_checked", len(checkable))
    result.signal("profile_links_alive", len(alive))
    result.signal("profile_links_gone", [p for p, _u, _s in gone])
    result.signal("profile_links_unchecked", [p for p, _u, _r in unchecked])

    if not gone:
        if alive:
            reason = "all {} verifiable profile link(s) resolve ({})".format(
                len(alive), ", ".join(p for p, _u in alive))
        else:
            reason = "none of the profile links could be checked: {}".format(
                "; ".join("{} {}".format(p, r) for p, _u, r in unchecked))
        result.skip("profile-links-resolve", reason)
        return

    evidence = "; ".join(
        "{} at {} returned HTTP {}".format(platform, truncate(url, 90), status)
        for platform, url, status in gone)
    if alive:
        evidence += ". {} other profile link(s) resolved.".format(len(alive))
    if unchecked:
        evidence += ". Not checked: {}.".format(
            ", ".join("{} ({})".format(p, r) for p, _u, r in unchecked))

    result.add(
        id_hint="profile-link-does-not-resolve",
        title="{} of the profile link{} the brand publishes lead nowhere".format(
            len(gone), "" if len(gone) == 1 else "s"),
        severity="medium" if len(gone) > 1 else "low",
        confidence="high",
        evidence=evidence,
        mechanism="D", root_cause="dead-profile-link",
        summary="Point the profile links at pages that exist, or remove them.",
        how_to_fix=[
            "Open each URL above. If the profile moved, correct the link in the page footer "
            "and in the Organization `sameAs` array.",
            "If the profile was closed, delete the entry rather than leaving it. A `sameAs` "
            "target that returns 404 is a claim the site makes that does not check out.",
            "If you want that profile back, recreate it under the same name and the same "
            "one-sentence description as the others, then relink it.",
        ],
        effort="low", owner="marketing",
        rationale="Corroboration works by a machine following the link and finding "
                  "the same brand described the same way at the other end. A link that returns "
                  "404 gives it nothing to agree with, so that profile counts for nothing - and "
                  "the site reads as less maintained than it is.",
        affected_pages=[url for _p, url, _s in gone],
    )


WIKIDATA_ENTITIES_API = ("https://www.wikidata.org/w/api.php?action=wbgetentities"
                         "&ids={}&props=claims&format=json")

# Wikidata's property for "official website".
WIKIDATA_OFFICIAL_SITE = "P856"


def _own_wikidata_entity(matches, snapshot, fetcher):
    """The Wikidata item that IS this site, if one of the matches is.

    One request for every match at once, asking each item which website it
    says is its own, and comparing that to the host being audited. Wikidata's
    search API does not return the property, so without this the brand's own
    entry is indistinguishable from the entities it collides with - and it was
    counted as one of them.
    """
    ids = [m.get("id") for m in matches if m.get("id")][:10]
    if not ids or fetcher is None:
        return None
    response = fetcher.try_get(WIKIDATA_ENTITIES_API.format("|".join(ids)))
    if response is None:
        return None
    try:
        entities = (response.json() or {}).get("entities") or {}
    except (ValueError, json.JSONDecodeError):
        return None

    host = strip_www(urlparse(snapshot.get("origin") or "").netloc.lower())
    if not host:
        return None
    for entity_id in ids:
        claims = ((entities.get(entity_id) or {}).get("claims") or {})
        for claim in claims.get(WIKIDATA_OFFICIAL_SITE) or []:
            value = (((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
            if not isinstance(value, str):
                continue
            if strip_www(urlparse(value).netloc.lower()) == host:
                return entity_id
    return None


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

    # The site's own entity is not one of the others. Wikidata returned five
    # items for one project's name and the report said "5 other entities share
    # the name" - one of the five was the project itself, which is not a
    # collision but the very disambiguation the finding goes on to recommend
    # creating. So the brand is told to go and make a thing it already has,
    # and the count it is alarmed by is one too high.
    own = _own_wikidata_entity(matches, snapshot, fetcher)
    if own is not None:
        matches = [m for m in matches if m.get("id") != own]
        result.signal("wikidata_own_entity", own)
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
        # The title counted every match and the evidence listed four, with
        # nothing saying so, so a reader who counted the examples found the
        # headline overstated by however many ran past the cut.
        evidence='Wikidata returns {} item(s) for "{}": {}{}. The site declares no Wikidata or '
                 "Wikipedia link{}.{}".format(
                     len(matches), brand_name,
                     "; ".join("{} ({})".format(m.get("label"), m.get("description") or "no description")
                               for m in matches[:4]),
                     "; and {} more".format(len(matches) - 4) if len(matches) > 4 else "",
                     " and no alternateName" if not alternate_names else "",
                     " The site's own Wikidata item was found and is not counted among these."
                     if own is not None else ""),
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
        rationale="When several things share a name, a system either picks one or "
                  "blends them. Without explicit markers the brand's facts get attributed to "
                  "whichever entity is better documented, which is usually not the smaller one.",
    )


ORG_IDENTITY_TYPES = frozenset({
    "organization", "localbusiness", "corporation", "store",
    "restaurant", "onlinestore", "ngo", "educationalorganization",
})


def _org_identity(page, field):
    """Values the page declares for the organisation itself, at any nesting depth.

    `telephone` sits on the Organization node; `postalCode` sits inside its
    PostalAddress. Both are read here so the caller only has to name the field.
    """
    found = []
    for node in page.get("jsonld") or []:
        types = node.get("@type")
        types = [types] if isinstance(types, str) else (types or [])
        if not any(str(t).lower() in ORG_IDENTITY_TYPES for t in types):
            continue
        if node.get(field):
            found.append(node[field])
        for key in ("address", "location"):
            value = node.get(key)
            for entry in (value if isinstance(value, list) else [value]):
                if isinstance(entry, dict) and entry.get(field):
                    found.append(entry[field])
    return found


def _check_declared_identity(result, conflicts, pages, label, field, normalise):
    """Record a conflict when the site declares two different values for itself."""
    per_page = {}
    for page in pages:
        values = {normalise(v) for v in _org_identity(page, field)}
        values.discard(None)
        if values:
            per_page[page["url"]] = values
    result.signal("pages_declaring_{}".format(field), len(per_page))
    if len(per_page) < 2 or set.intersection(*per_page.values()):
        return
    distinct = sorted({v for values in per_page.values() for v in values})
    conflicts.append((sorted(per_page)[0],
                      "the Organization markup declares {} different values for the "
                      "site's own {} across {} pages, with none shared by all of "
                      "them".format(len(distinct), label, len(per_page))))


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

    # The comparison above is schema against the page it sits on. The classic
    # NAP problem is different and more common: the visible details disagree
    # from one page to the next, with no schema involved anywhere.
    #
    # The test is not "more than one number exists" - a real site legitimately
    # lists sales, support and regional lines. It is whether *any single value*
    # appears on every page that states one at all. A site with a footer number
    # site-wide passes however many extra numbers its contact page carries; a
    # site whose footer says something different on each template does not.
    # The comparison above is schema against the page it sits on. This one is
    # schema against schema: the identity the site declares for itself on one
    # page against the identity it declares on another.
    #
    # Deliberately narrow. Two earlier and looser versions both failed here. The
    # first scraped digit runs out of page text and reported five telephone
    # numbers on a site publishing one, because any long run of digits looks
    # like a phone number. The second read `tel:` links and reported a site with
    # a sales line, a support line and a returns line as contradicting itself. A
    # site may publish as many numbers as it has departments; what it may not do
    # is declare two different primary numbers for the same organisation.
    _check_declared_identity(
        result, conflicts, pages, "telephone number", "telephone",
        lambda value: re.sub(r"\D", "", str(value))[-7:] if len(
            re.sub(r"\D", "", str(value))) >= 7 else None)
    _check_declared_identity(
        result, conflicts, pages, "postal code", "postalCode",
        lambda value: re.sub(r"\s+", "", str(value)).lower() or None)

    if len(descriptions) > 1:
        pairs = sorted(descriptions.items(), key=lambda kv: kv[0])
        conflicts.append((pairs[0][1][0],
                          "{} different Organization descriptions are published across the site; "
                          "the boilerplate is not one sentence but several".format(len(descriptions))))

    result.signal("distinct_org_descriptions", len(descriptions))

    if not conflicts:
        result.skip("fact-consistency-across-pages",
                    "telephone, postal code and the Organization description are consistent "
                    "wherever they appear, both between pages and against the markup")
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
        rationale="A brand that disagrees with itself is the hardest case for a "
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
    parser.add_argument("--time-budget", type=float,
                        help="seconds of wall clock this check may spend on its own requests; unreached targets are reported as unchecked rather than guessed at")
    parser.add_argument("--now", help="reference date as YYYY-MM-DD (defaults to today, UTC)")
    parser.add_argument("--no-network", action="store_true",
                        help="skip the Wikidata entity-ambiguity lookup")
    args = parser.parse_args(argv)

    now = parse_date(args.now) if args.now else None
    if args.now and now is None:
        parser.error("--now must be a date in YYYY-MM-DD form")

    result = run(load_snapshot(args.snapshot), now=now, allow_network=not args.no_network,
                 time_budget=args.time_budget)
    result.write(args.out)
    print("{}: {} finding(s), {} extra request(s)".format(
        SKILL, len(result.findings), result.extra_requests_made), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
