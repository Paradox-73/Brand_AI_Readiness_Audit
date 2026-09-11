#!/usr/bin/env python3
"""freshness-corroboration-audit: is the brand current, agreed-upon and unambiguous? (mechanism D)

Reads snapshot.json, writes findings JSON. Makes at most 16 extra read-only
requests: up to three to Wikidata's public API (a search for the declared name,
a second for the shorter form when the site writes one, then one lookup asking
which of the results names this site as its own website), one HEAD per
off-site profile link it is able to verify, after that platform's robots.txt,
and up to two re-reads of an RSS or Atom feed this crawl already fetched, made
only when the site is about to be reported as no longer publishing.

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
    brand_key, confirm_dead, comparison_key, counts_as_off_site_profile,
    deadline_from_budget, example_urls, Fetcher,
    FetchError, host_name_forms, is_listing_page, is_multi_location,
    jsonld_type_names, language_of,
    letter_runs, load_snapshot, LOCAL_BUSINESS, name_forms, names_are_one_name,
    no_network_reason, not_a_telephone_number, ONLINE_SELLER, ORG_IDENTITY_TYPES,
    ORGANISATION, pages_of, pct, PERSONAL_OR_ACADEMIC, plural,
    primary_subtag, PROJECT, PUBLIC_BODY, PUBLICATION, site_kind,
    PROFILE_GONE_STATUS, profile_is_opaque, profile_names_brand, profiles_naming_brand,
    response_text, sentences, sitemap_scope, sitemap_total_phrase, SkillResult,
    speaks_about_itself, strip_www, third_party_allows, truncate, VERIFIABLE_PROFILE_PLATFORMS,
    VISITABLE_JSONLD_TYPES
)

# The one reading this skill shares with the extractor rather than repeating:
# which of the hosts a site loads its code from is the platform it is built on.
# Both this file and `page_extract` decide whether an address the site declares
# is an account or the platform underneath it, and two copies of that reasoning
# would drift the moment either was corrected.
from page_extract import subresource_domains  # noqa: E402
# The hosts the extractor names as profile platforms, read for the same reason:
# an address on one of them is an account by where it lives, and an address
# anywhere else has to say so in its path. See `_is_coverage_not_an_account`.
from page_extract import SOCIAL_PLATFORMS  # noqa: E402

SKILL = "freshness-corroboration-audit"

# Thresholds and their reasons.
STALE_MONTHS = 18            # 18 months is the point at which "recent" claims stop being defensible
STALE_SHARE = 0.7            # most of the library being old is a programme problem, not one old post
# The second condition SKILL.md states, and the one that separates a deep
# archive from an abandoned one. A blog that has run for ten years always has
# most of its posts past 18 months; that is arithmetic, not a defect.
STILL_PUBLISHING_MONTHS = 12
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

# One robots.txt for Wikidata, read before anything is asked of it, then at
# most three lookups where that file allows them - a search for the declared
# name, a second for the shorter form when the site writes one, then one batch
# lookup of which item claims this host as its official website - six for the
# profile links we are
# able to verify, one per platform in VERIFIABLE_PROFILE_PLATFORMS, and one
# robots.txt per distinct platform host before any of those six is sent.
# Without the last six the profile probes were skipped part-way through on
# every brand that publishes more than three profile links, because the budget
# ran out on the robots fetches themselves.
MAX_EXTRA_REQUESTS = 16

# `[0-9]`, not `\d`. `\d` matches Arabic-Indic digits and `int()` accepts them,
# so an Arabic page's Hijri date parsed as the year 1445 - and the staleness
# arithmetic then reported an article as 7,000 months old, quoting a 6th-century
# date back at the reader as evidence.
_ISO_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")

# The numeric form most of the Americas writes. Without it `3/15/2024` parsed as
# nothing, while `page_extract` recorded it as a visible date - so the freshness
# check skipped with "no article carries a parsable date, which is reported
# separately as a missing date signal" and the date-signal check passed, because
# a date *was* seen. Two checks, one gap, and neither said so.
_SLASH_RE = re.compile(r"\b([0-9]{1,2})/([0-9]{1,2})/((?:19|20)[0-9]{2})\b")

# A date outside this range is a parse artefact, not a publication date.
_EARLIEST_PLAUSIBLE_YEAR = 1990
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def parse_date(value, now=None):
    """Parse the date formats sites actually publish. Returns a date or None.

    A date in the future, or before the web existed, is rejected. Both were
    accepted, and `months_between` floors at zero - so a sitemap `lastmod` of
    2035 read as nought months old and suppressed a staleness finding, while
    the evidence line printed the 2035 date beside it.
    """
    parsed = _parse_date_raw(value)
    if parsed is None:
        return None
    if parsed.year < _EARLIEST_PLAUSIBLE_YEAR:
        return None
    horizon = (now or dt.datetime.now(dt.timezone.utc).date())
    if parsed > dt.date(horizon.year + 1, 1, 1):
        return None
    return parsed


def _parse_date_raw(value):
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
    match = _SLASH_RE.search(text)
    if match:
        first, second = int(match.group(1)), int(match.group(2))
        # Which number is the month is genuinely ambiguous between the two
        # conventions, and at the month granularity every threshold here uses,
        # it does not matter - unless one of them is over 12, which settles it.
        month, day = (second, first) if first > 12 else (first, second)
        try:
            return dt.date(int(match.group(3)), month, day)
        except ValueError:
            return None
    return None


def newest_date(page, now=None):
    """The most recent date signal on a page, machine-readable ones first.

    `non_gregorian` is read too, and it has to be read separately because
    `parse_date` cannot help with it: `28 สิงหาคม 2569` is a Thai Buddhist-era
    date and `令和8年4月6日` is a Japanese era date, and neither is a string any
    date library resolves. The extractor converts them where the page's own
    script or declared language justifies the conversion, and records the
    result as `gregorian_year`.

    A year alone, without a month or a day. That is all the conversion
    establishes, so the date taken from it is the first of January - which is
    the earliest the page can be, and therefore the reading that cannot
    overstate how fresh the page is. Reported this way, a Thai page dated 2569
    is eight months older than it really is and never newer; the alternative,
    guessing a month, would have this audit call a page current on a date
    nobody published.

    A date after `now` is not when the page was published. It is when
    something the page announces will happen: a national library's event page
    carries the festival's own date, five weeks after the audit, in both its
    markup and its text, and the report printed "the newest article is dated
    2026-10-20 (0 month(s) old)" - an age the arithmetic floored at zero
    because the page is, by that reading, from the future. A page cannot have
    been written after the day it was read, so a later date is set aside and
    the newest date the page could have been published on is what remains.
    One day of slack, because the reference date is a calendar day in one
    time zone and a page stamped in another can already be on the next.
    """
    dates = page.get("dates") or {}
    ceiling = (now + dt.timedelta(days=1)) if now else None
    candidates = []
    for value in (dates.get("machine_readable") or []) + (dates.get("visible") or []):
        parsed = parse_date(value, now)
        if parsed:
            candidates.append(parsed)
    for reading in dates.get("non_gregorian") or []:
        year = reading.get("gregorian_year") if isinstance(reading, dict) else None
        if isinstance(year, int) and 1000 <= year <= 9999:
            candidates.append(dt.date(year, 1, 1))
    if ceiling is not None:
        candidates = [d for d in candidates if d <= ceiling]
    return max(candidates) if candidates else None


def months_between(earlier, later):
    """Whole months elapsed, never rounded up.

    Counting calendar-month boundaries alone reported a 22.2-month gap as
    "23 months old", which is a number the reader can check against a visible
    date and find wrong. The day of the month decides the last one.
    """
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    return max(0, months)


def run(snapshot, now=None, allow_network=True, time_budget=None):
    result = SkillResult(SKILL)
    now = now or dt.datetime.now(dt.timezone.utc).date()
    pages = pages_of(snapshot, content_only=True)
    brand = snapshot.get("brand") or {}

    if not pages:
        for name in ("content-freshness", "date-signals-present", "footer-copyright-year",
                     # `off-site-profile-breadth`, which is what the check is
                     # called where it is registered. This list said
                     # `authoritative-profiles`, a name no code uses, so on a
                     # fully blocked site the real check appeared in neither
                     # the findings nor the not-applicable list - and two rows
                     # of `references/round2-failure-modes.md` pointed at it.
                     "stale-year-references", "off-site-profile-breadth", "entity-ambiguity",
                     "profile-links-resolve", "fact-consistency-across-pages",
                     # The two the list left out, found by walking the file's
                     # own `result.check(...)` calls with a parser rather than
                     # reading them off. They are decided in
                     # `_check_sitemap_lastmod`, which the early return above
                     # never reaches, so on a site where no content page
                     # answered 200 they appeared in `checks_run[]`,
                     # `not_applicable[]` and `findings[]` alike - nowhere.
                     #
                     # Exactly the defect the note above this list records for
                     # `off-site-profile-breadth`, which is why it is worth
                     # saying twice: a list of names kept by hand beside the
                     # code that registers them drifts, and the drift is
                     # invisible because the symptom is a check going missing
                     # rather than a check going wrong.
                     "sitemap-lastmod-coverage", "sitemap-lastmod-recency"):
            result.skip(name, "no content pages returned HTTP 200")
        return result

    # Built before the first check rather than after the last one, because the
    # freshness check needs it: on the path where it is about to report a site
    # as abandoned it reads the site's own feed, which is the document that
    # settles the question. That costs at most two of the sixteen requests and
    # only on that path, so the Wikidata calls below still have their three.
    fetcher = None
    if allow_network:
        try:
            fetcher = Fetcher(max_requests=MAX_EXTRA_REQUESTS,
                              deadline=deadline_from_budget(time_budget))
        except FetchError:
            fetcher = None

    _check_article_freshness(result, snapshot, pages, now, fetcher)
    _check_date_signals(result, pages, snapshot)
    _check_copyright_year(result, pages, now, brand.get("name") or "",
                          brand.get("host") or urlparse(snapshot.get("origin") or "").netloc)
    _check_stale_year_references(result, pages, now, brand.get("name") or "")
    # Every readable page, not the content pages. An account is linked from the
    # site's chrome, and a software project's own repository account sits in
    # the header of its download page, which the content filter sets aside - so
    # the report led with "No off-site profile is linked" about a project whose
    # every download page links its account.
    profiles = _check_authoritative_profiles(result, snapshot, pages_of(snapshot))
    _check_sitemap_lastmod(result, snapshot, now)
    _check_fact_consistency(result, snapshot, pages)

    # Entity ambiguity first. Both share one ten-request ceiling, and `Fetcher`
    # charges a retry against it too, so six slow profile HEADs could spend
    # twelve - and the Wikidata call then failed with "request budget
    # exhausted", which `try_get` swallows into the reason "the Wikidata search
    # API could not be reached; this is an audit-environment limitation". That
    # is a statement about Wikidata, and it was false. The two Wikidata
    # requests are the ones with no substitute, so they go first.
    _check_entity_ambiguity(result, snapshot, pages, brand, profiles, fetcher, allow_network,
                            time_budget=time_budget)
    _check_profile_links_resolve(result, profiles, fetcher, allow_network,
                                 snapshot=snapshot, time_budget=time_budget)
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

# How many dated sitemap entries a section needs before its dates can overrule
# the handful of articles the crawl happened to read.
SITEMAP_CONTRADICTION_MIN = 5

# A feed the crawl already fetched is the site's own list of what it published
# and when, and it settles the question this check asks. The crawl records the
# file and never reads it: a feed is not a page, so it is stored with a status,
# a content type and an empty body, and it was counted as one of the sixty
# pages while the four oldest posts on the blog decided the finding.
#
# A cloud host was told its articles "have not been updated in over a year",
# with the newest dated 51 months back, in a run whose own snapshot holds the
# URL of an Atom feed whose two newest entries are dated the day before the
# audit and the day of it.
_FEED_URL_RE = re.compile(
    r"(?:^|/)(?:feeds?|rss|atom)(?:[./]|$)"
    r"|(?:feed|rss|atom|index)\.(?:xml|rss|atom)$", re.I)

# The element every entry date arrives in, across both feed formats: Atom
# writes `updated` and `published`, RSS writes `pubDate` and `dc:date`, and
# `lastBuildDate` is the channel's own stamp.
_FEED_DATE_RE = re.compile(
    r"<(?:updated|published|pubDate|dc:date|lastBuildDate)\b[^>]*>\s*([^<]{4,80})<", re.I)

# One feed answers the question. Two, because a site may split its library into
# a blog feed and a news feed, and reading the wrong one alone proves nothing.
FEEDS_READ_MAX = 2

# How recent a feed entry has to be before it contradicts a stale sample. The
# same twelve months the sitemap veto uses, for the same reason: this is the
# line between "the sample missed the recent posts" and "the section really did
# stop".
FEED_CONTRADICTION_MONTHS = 12


def _feed_documents(snapshot):
    """Feed URLs this crawl fetched and stored without reading.

    Only URLs that were already fetched at 200, so re-reading one asks the site
    for nothing it has not already been asked for and stays inside the
    robots.txt decision the crawl already honoured.
    """
    sitemaps = {record.get("url") for record in snapshot.get("sitemaps") or []}
    found = []
    for page in snapshot.get("pages") or []:
        url = page.get("url") or ""
        if page.get("status") != 200 or not url or url in sitemaps:
            continue
        content_type = (page.get("content_type") or "").lower()
        if not any(marker in content_type for marker in ("xml", "rss", "atom")):
            continue
        if not _FEED_URL_RE.search(urlparse(url).path or ""):
            continue
        found.append(url)

    # What the pages themselves declare, which is how a feed is meant to be
    # found. Matching on the filename missed `/blog.rss` outright - the pattern
    # wanted the stem to be `feed`, `rss`, `atom` or `index` - and the crawl
    # had never fetched it anyway, because it is declared as
    # `<link rel="alternate">` in the head and linked from nowhere. A school
    # whose newest post was four months old was reported as a library that had
    # stopped, by a check that has a guard for exactly that and could not
    # reach the evidence.
    declared = []
    for page in snapshot.get("pages") or []:
        for url in page.get("feed_urls") or []:
            if url and url not in sitemaps and url not in declared:
                declared.append(url)
    return sorted(set(found) | set(declared))


def _newest_feed_entry(fetcher, url, now):
    """The newest entry date in one feed document, or None if it cannot be read."""
    if fetcher is None:
        return None
    response = fetcher.try_get(url)
    if response is None or getattr(response, "status_code", None) != 200:
        return None
    dates = [parse_date(match.group(1), now)
             for match in _FEED_DATE_RE.finditer(response_text(response))]
    dates = [d for d in dates if d]
    return max(dates) if dates else None


def _feed_contradicts_sample(snapshot, sampled, now, fetcher):
    """Ask the site's own feed whether this stale sample is the whole library.

    Returns `(skip_reason, unread_feed_urls)`. The reason is None when there is
    no feed, when the feed agrees that the library is old, or when the feed
    could not be read - and in that last case the URLs come back so the finding
    can name what it did not manage to check instead of asserting past it.
    """
    feeds = _feed_documents(snapshot)
    if not feeds:
        return None, []
    unread = []
    for url in feeds[:FEEDS_READ_MAX]:
        newest = _newest_feed_entry(fetcher, url, now)
        if newest is None:
            unread.append(url)
            continue
        age = months_between(newest, now)
        if age > FEED_CONTRADICTION_MONTHS:
            continue
        # "0 months old" is not how anybody says "last week".
        age_phrase = "under a month old" if age == 0 else plural(age, "month") + " old"
        reason = ("the {} this crawl reached are old, but the site's own feed at {} lists an "
                  "entry dated {} ({}). The crawl sampled a few articles; the site's index "
                  "of all of them says it is still publishing, so no staleness is asserted "
                  "here".format(plural(len(sampled), "dated article"), url,
                                newest.isoformat(), age_phrase))
        return reason, []
    return None, unread


def _sitemap_contradicts_sample(snapshot, sampled, now):
    """Why the sitemap says these sampled articles are not the whole library.

    a cloud host was told "the articles reached by this crawl have not been updated
    in over a year" on a sample of four posts, with the rationale that a
    library which stopped two years ago loses its comparisons. The sitemap in
    the very same snapshot lists 204 entries under `/blog`, one of them
    modified three months before the run. The crawl reads a handful of
    articles; the site's own index of all of them was already in hand and said
    the opposite.

    Returns the skip reason, or None when the sitemap does not contradict the
    sample.
    """
    sections = {_section_of(p["url"]) for p in sampled}
    sections.discard("")
    if not sections:
        return None
    dates, total = [], 0
    for record in snapshot.get("sitemaps") or []:
        for entry in record.get("urls") or []:
            if _section_of(entry.get("loc") or "") not in sections:
                continue
            total += 1
            parsed = parse_date(entry.get("lastmod"), now)
            if parsed:
                dates.append(parsed)
    if len(dates) < SITEMAP_CONTRADICTION_MIN or total <= len(sampled):
        return None
    newest = max(dates)
    if months_between(newest, now) > 12:
        return None
    recent = sum(1 for d in dates if months_between(d, now) <= STALE_MONTHS)
    return ("the {} this crawl reached are old, but the sitemap in this same snapshot lists "
            "{} under {}, of which {} carry a <lastmod> within {} months and the newest is "
            "{} ({} old). The sample is not representative of the section, so no staleness "
            "is asserted here".format(
                plural(len(sampled), "dated article"), plural(total, "entry", "entries"),
                ", ".join(sorted(sections)), recent, STALE_MONTHS,
                newest.isoformat(), plural(months_between(newest, now), "month")))


def _check_article_freshness(result, snapshot, pages, now, fetcher=None):
    result.check("content-freshness")
    # An index is not an item, here either. A blog index typed as an article
    # dates itself from the newest post it lists, so counting it inflates the
    # sample with a page that has no publication date of its own and whose age
    # is a copy of a date already counted.
    articles = [p for p in pages if p["page_type"] == "article" and not is_listing_page(p)
                and not _lists_its_own_section(p)]
    if len(articles) < 3:
        result.skip("content-freshness",
                    "fewer than 3 article pages were crawled, so there is no publishing "
                    "cadence to assess")
        return

    # `now` passed through, so an event date later than the audit is not read
    # as the newest article. See `newest_date`.
    dated = [(p, newest_date(p, now)) for p in articles]
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
    result.signal("stale_article_share", round(share, 2))

    # Both conditions, which is what SKILL.md states and what anyone working
    # through it by hand would apply. The share alone fired this finding on a
    # programming school that had published four months earlier, under the
    # title "most of the articles are over 18 months old" - true of every blog
    # with a back catalogue, and not a defect in any of them. Only the age of
    # the newest article distinguishes a deep archive from an abandoned one.
    #
    # The share still decides what the skip says. Filing a 93%-stale archive
    # under "nothing to report" is what removed this condition before, so the
    # skip states the share and says which condition it failed rather than
    # implying the library is current.
    age = months_between(newest, now)
    if share < STALE_SHARE:
        result.skip("content-freshness",
                    "the newest article is dated {} ({} month(s) old) and {}% of dated articles "
                    "are within {} months".format(
                        newest.isoformat(), age, round(100 * (1 - share)), STALE_MONTHS))
        return
    if age <= STILL_PUBLISHING_MONTHS:
        result.skip("content-freshness",
                    "{}% of dated articles are older than {} months, but the newest is dated {} "
                    "({} month(s) old), so this is a deep archive on a site that is still "
                    "publishing rather than a library that has stopped".format(
                        pct(len(stale), len(dated)), STALE_MONTHS, newest.isoformat(), age))
        return

    contradiction = _sitemap_contradicts_sample(snapshot, [p for p, _ in dated], now)
    if contradiction:
        result.skip("content-freshness", contradiction)
        return

    # The site's own feed, asked before the site is told it has stopped
    # publishing. Only reached on the path where the finding is about to fire,
    # so an ordinary run spends nothing on it.
    feed_reason, unread_feeds = _feed_contradicts_sample(
        snapshot, [p for p, _ in dated], now, fetcher)
    if feed_reason:
        result.skip("content-freshness", feed_reason)
        return
    result.signal("feeds_unread", unread_feeds)

    # Reached only where both conditions hold, so the newest article is itself
    # over a year old and the title may say so. The softer wording that used to
    # sit here, for a site whose newest post was four months old, now belongs to
    # the skip above: that site is still publishing and has nothing to fix.
    result.add(
        id_hint="published-content-has-gone-stale",
        # Scoped to what was crawled. On a large site the audit sees a sample,
        # and claiming the whole site has stalled would overstate the evidence.
        title="The articles reached by this crawl have not been updated in over a year",
        # A feed that exists and could not be read is a hole in the evidence,
        # not a detail. It is the document that would settle whether this
        # sample is the library, so the claim drops to medium and says which
        # file went unread rather than asserting past it.
        severity="medium",
        confidence=("medium" if unread_feeds else "high"),
        evidence="{} of {} dated article(s) ({}%) are older than {} months, and the newest is "
                 "{} ({} months old). Examples: {}.{}".format(
                     len(stale), len(dated), pct(len(stale), len(dated)), STALE_MONTHS,
                     newest.isoformat(), months_between(newest, now),
                     ", ".join(example_urls([p["url"] for p, _ in stale])),
                     "" if not unread_feeds else
                     " The site publishes a feed at {}, which this run could not read, so the "
                     "sample above may not be the whole library.".format(unread_feeds[0])),
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
                  "library that stopped over a year ago loses those comparisons by default, "
                  "whatever the quality of the writing.",
        affected_pages=[p["url"] for p, _ in stale],
    )


# How many pages under its own address a page has to link to before it is the
# index of that section. Five: a post links to two or three siblings in a
# "read next" strip, and a blog's front page links to every post it lists.
_SECTION_INDEX_MIN_CHILDREN = 5


def _lists_its_own_section(page):
    """Does this page link to the pages filed under its own address?

    A shop's blog front page, `/blogs/<name>`, links to twelve posts at
    `/blogs/<name>/<post>`, and was typed an article, reported as a page "where
    a date is expected" and handed a fix to add a published date under its
    headline. `is_listing_page` reads a page's headings and its type; this
    reads its links, which is where a blog's front page says what it is. A
    page whose links reach five or more distinct pages one level or more below
    its own path is that section's index, and its dates, if any, are its posts'.
    """
    path = (urlparse(page.get("url") or "").path or "/").rstrip("/")
    if not path:
        return False
    children = set()
    for link in ((page.get("links") or {}).get("internal") or []):
        target = urlparse((link or {}).get("url") or "").path or ""
        if target.startswith(path + "/") and target.rstrip("/") != path:
            children.add(target.rstrip("/"))
    return len(children) >= _SECTION_INDEX_MIN_CHILDREN


def _section_of(url):
    """The path a page sits in: `/changelog/x` -> `/changelog`."""
    path = urlparse(url).path or "/"
    return path.rsplit("/", 1)[0] if path.count("/") >= 2 else ""


def _page_key(url):
    """One spelling of a URL, so a sitemap and a crawled page can be compared.

    Host without `www.`, path without its trailing slash. A sitemap listing the
    bare host against a crawl that followed the `www.` host shares not one
    string with it, and every comparison between the two then comes out empty.
    """
    parts = urlparse(url or "")
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return "{}{}".format(host, parts.path.rstrip("/") or "/")


def _sitemap_lastmod_by_page(snapshot):
    """{page key: lastmod string} for every sitemap entry that carries one.

    The one freshness signal that does not live on the page. Read so that "this
    page carries no date at all" is not said about a page whose modification
    date the site publishes somewhere a crawler reads.
    """
    out = {}
    for record in (snapshot or {}).get("sitemaps") or []:
        for entry in record.get("urls") or []:
            value = (entry.get("lastmod") or "").strip()
            key = _page_key(entry.get("loc") or "")
            if value and key:
                out[key] = value
    return out


# What a page is for when it is for doing something rather than reading
# something: getting there, signing in, signing up, booking. Written as the
# labels sites put on those pages - a menu entry, the last step of a title's
# breadcrumb, the page's own heading - in the languages this audit has met,
# and matched against a whole label, so "Access to Information Act" is not a
# directions page because it opens with a word on this list.
_UTILITY_PAGE_LABELS = (
    # Getting there.
    "map", "maps", "site map", "directions", "getting here", "how to get here",
    "how to find us", "find us", "visit us", "location map", "parking", "subway",
    "metro", "by train", "by bus", "public transport", "access map",
    "오시는 길", "찾아오시는 길", "지하철", "버스", "주차", "교통", "교통안내",
    "アクセス", "交通", "交通案内", "地図", "アクセスマップ",
    "交通指南", "地图", "路线", "交通路线",
    "plan d'accès", "itinéraire", "venir", "anfahrt", "lageplan", "cómo llegar", "mapa",
    # Signing in, signing up, booking and paying.
    "login", "log in", "sign in", "sign up", "register", "registration", "create account",
    "my account", "my page", "join", "membership", "become a member", "reservation",
    "reservations", "book", "booking", "book a visit", "book tickets", "checkout", "cart",
    "basket", "search", "search results",
    "로그인", "회원가입", "예약", "예약하기", "마이페이지",
    "ログイン", "会員登録", "新規登録", "予約", "マイページ",
    "登录", "注册", "预约", "预订",
    "connexion", "inscription", "réservation", "anmelden", "registrieren", "reservierung",
    "iniciar sesión", "registro", "reserva",
    # Describing an app, a service or a facility the visitor uses. A museum's
    # page introducing its exhibition-guide app - titled "...>전시 해설 안내>
    # 전시안내앱" - sat in a dated section and was counted among the pages
    # "where a date is expected". It says what the app does and where to get
    # it; it was never written on a date and no reader asks when.
    "app", "apps", "mobile app", "mobile apps", "our app", "the app", "audio guide",
    "facilities", "amenities", "visitor facilities", "wi-fi", "wifi", "lockers",
    "cloakroom", "accessibility",
    "앱", "어플", "애플리케이션", "편의시설", "시설 안내",
    "アプリ", "施設案内", "館内施設",
    "应用", "應用", "设施", "設施",
)
_UTILITY_LABEL_KEYS = frozenset(" ".join(label.casefold().split())
                                for label in _UTILITY_PAGE_LABELS)

# The same labels as the last word of a compound. Korean, Japanese and Chinese
# build "exhibition-guide app" as one word ending in the word for app, so
# `전시안내앱` is `앱` with the thing it guides in front of it. Only the scripts
# that write compounds without spaces; "Snapp" is not an app page.
_UTILITY_LABEL_ENDINGS = ("앱", "어플", "애플리케이션", "편의시설", "アプリ", "施設案内",
                          "应用", "應用")

# The same pages, as the words an address spells them with.
# `/contents/membership.do?schM=public_signup` and `/...?menuId=subway-map`
# name what the page does in the URL even where the page says it in another
# script. A whole token, so `/sitemap-news/` is not a map.
_UTILITY_URL_TOKENS = frozenset({
    "map", "maps", "directions", "signup", "signin", "login", "logon", "register",
    "registration", "checkout", "cart", "basket", "reservation", "reservations",
    "booking", "subway", "parking",
})

# A form is what the page is for when it asks for this many things at once. A
# search box asks for one and a newsletter box for one or two; a sign-up or a
# booking form asks for a name, an address, a date and more.
_A_FORM_PAGE_FIELDS = 4


def _label_is_a_utility(label):
    """Is this label - a heading, a breadcrumb step - the name of a utility page?

    A whole label, or a label that only adds a verb ending to one: Korean and
    Japanese write "make a reservation" as the noun plus a short ending, and
    `예약하기` is `예약` with two syllables after it.
    """
    key = " ".join(str(label or "").casefold().split())
    if not key:
        return False
    if key in _UTILITY_LABEL_KEYS:
        return True
    if any(key.endswith(ending) for ending in _UTILITY_LABEL_ENDINGS):
        return True
    return any(key.startswith(word) and len(key) - len(word) <= 2
               for word in _UTILITY_LABEL_KEYS
               if word and not word.isascii())


def _a_page_for_doing_something_else(page):
    """Is this a form, a map, a directions page or another utility page?

    Such a page is not a dated thing, and no amount of writing makes it one. A
    museum's sign-up form and its "by subway" page sat under the same path as
    its dated notices, and both were counted among the pages "where a date is
    expected" - with a fix to add a published-and-updated line to a
    membership form. What a page is for is read from three places the page
    itself fills in: the words of its address, the last step of the
    breadcrumb its title carries and its own headings, and a form on it that
    is neither a search box nor a newsletter sign-up.
    """
    parts = urlparse(page.get("url") or "")
    tokens = set(re.split(r"[^a-z0-9]+", "{} {}".format(parts.path, parts.query).lower()))
    if tokens & _UTILITY_URL_TOKENS:
        return True
    for form in page.get("forms") or []:
        if not isinstance(form, dict):
            continue
        if form.get("is_search") or form.get("is_newsletter") or form.get("submits_off_site"):
            continue
        if (form.get("field_count") or 0) >= _A_FORM_PAGE_FIELDS:
            return True
    title = str(page.get("title") or "")
    steps = [s for s in re.split(r"\s*(?:>|›|»|\||/| - | – | — )\s*", title) if s.strip()]
    labels = steps[-2:] if len(steps) > 1 else []
    # The first `h2` and no other. A recipe's "Directions" is a heading
    # halfway down a dated post, and it is never the first thing the post says.
    headings = page.get("headings") or {}
    labels += list(headings.get("h1") or []) + list(headings.get("h2") or [])[:1]
    return any(_label_is_a_utility(label) for label in labels)


# The last path segment of a page that says who the site is: its story, its
# history, its mission. Such a page is the about page, and it is not written
# on a date.
_ABOUT_THE_SITE_SEGMENTS = frozenset({
    "story", "our-story", "brand-story", "about", "about-us", "who-we-are", "our-history",
    "history", "mission", "our-mission", "heritage", "our-heritage", "company",
})


def _a_page_about_the_site(page):
    """Is this page the site's own story or about page, by its address or its type?"""
    if page.get("page_type") == "about":
        return True
    segments = [s for s in urlparse(page.get("url") or "").path.lower().split("/") if s]
    return bool(segments) and re.sub(r"\.(?:html?|php|aspx?)$", "", segments[-1]) \
        in _ABOUT_THE_SITE_SEGMENTS


# How many of the crawled pages have to print one date before it is the
# template's rather than any page's. Four in five, and never fewer than three
# pages: two articles published on the same day are not a clock.
_CHROME_DATE_SHARE = 0.8
_CHROME_DATE_MIN_PAGES = 3


def _dates_on_most_pages(pages):
    """The visible dates printed on most of these pages, as one string each.

    A site whose header shows today's date puts that one date on every page,
    and each page then counted as dated - an article with no date of its own
    among them - because a date appears on it. The extractor already sets
    aside a date beside a copyright sign or inside the footer; a date repeated
    across the crawl is the same furniture, and only the whole crawl can see
    that.
    """
    if len(pages) < _CHROME_DATE_MIN_PAGES:
        return frozenset()
    counts = {}
    for page in pages:
        for value in set((page.get("dates") or {}).get("visible") or []):
            counts[value] = counts.get(value, 0) + 1
    return frozenset(value for value, seen in counts.items()
                     if seen >= _CHROME_DATE_SHARE * len(pages))


def _carries_its_own_date(page, chrome=frozenset()):
    """Does this page carry a date that is its own, not the template's?"""
    dates = page.get("dates") or {}
    if not dates.get("has_any"):
        return False
    if not chrome:
        return True
    if (dates.get("jsonld_date_published") or dates.get("jsonld_date_modified")
            or dates.get("machine_readable")):
        return True
    visible = set(dates.get("visible") or [])
    return not visible or bool(visible - chrome)


def _check_date_signals(result, pages, snapshot=None):
    """No date at all is a distinct problem from having an old date.

    `snapshot` is optional so a caller holding only a page list can still run
    this. Passing it adds the sitemap `<lastmod>` reading, which is the one
    date source that lives outside the page.
    """
    result.check("date-signals-present")
    # Which pages are supposed to carry a date, without asking whether they
    # do - that question is the check itself.
    #
    # Dated posts whose URLs the type table does not recognise are typed
    # `other`, and this list came back empty on a site the crawl had just read
    # twelve of them from: `/changelog/...` and `/now/...`, each with a
    # headline and a publication date. The two freshness checks then skipped
    # with the reason "no article or press pages were crawled".
    #
    # A section is what settles it. If any page under `/changelog/` is dated,
    # `/changelog/` is a dated section, and a page there without a date is
    # missing one - which is exactly what this check is for, and is not
    # circular, because the page being judged is not the page that established
    # the section.
    # A section counts as dated when most of it is dated, not when one page in
    # it happens to print a date in prose. A law firm's eight staff bios sat
    # under `/people/`; one bio mentions "3 October 2011" in a sentence, and
    # the other seven were then reported as pages "where one is expected"
    # carrying no date - with a fix telling the firm to put "last updated"
    # lines on its lawyers.
    #
    # An index is not an item. A page whose job is to point at other pages
    # carries their dates or none, and neither says anything about when the
    # index itself was written: a blog hub linking three category listings was
    # counted as one of "13 pages where one is expected", and the fix handed
    # to the owner read "add a visible line under the headline: Published
    # <date>, last updated <date>" - a line that belongs on a post and on
    # nothing that lists posts. Listings are dropped from both sides of the
    # arithmetic: they cannot be judged undated, and their borrowed dates
    # cannot be what makes a section count as dated either.
    items = [p for p in pages if not is_listing_page(p) and not _lists_its_own_section(p)]
    listing_count = len(pages) - len(items)
    result.signal("listing_pages_not_expected_to_carry_a_date", listing_count)
    # A date the template prints on every page - a clock in the header, today's
    # date beside the menu - is the day the page was served, not a date the
    # page was written on. See `_dates_on_most_pages`.
    chrome = _dates_on_most_pages(pages)
    result.signal("dates_repeated_across_the_site", sorted(chrome))

    section_pages, section_dated = {}, {}
    for page in items:
        section = _section_of(page["url"])
        if not section:
            continue
        section_pages[section] = section_pages.get(section, 0) + 1
        if _carries_its_own_date(page, chrome):
            section_dated[section] = section_dated.get(section, 0) + 1
    # Two dated pages, and at least half the section. One was enough, and
    # `has_any` is true for any full date printed anywhere in the body - so a
    # law firm's eight staff bios became "a dated section" because one bio
    # contains the sentence "joined the firm on 3 October 2011", and the other
    # seven were reported as pages "where a date is expected" carrying none.
    # The fix told the firm to put "last updated" lines on its lawyers.
    #
    # A majority is what makes the inference sound: the section is dated
    # because the site dates it, not because one page mentioned a date.
    dated_sections = {
        section for section, total in section_pages.items()
        if section_dated.get(section, 0) >= 2
        and section_dated.get(section, 0) >= 0.5 * total
    }
    # A section being dated says its pages are dated things, and a sign-up
    # form or a directions page filed under the same path is not one. Only the
    # section inference is narrowed: a page the crawl typed as an article or a
    # press release is asked for a date whatever else is on it.
    utility = [p for p in items
               if p["page_type"] not in ("article", "press")
               and _section_of(p["url"]) in dated_sections
               and _a_page_for_doing_something_else(p)]
    result.signal("utility_pages_not_expected_to_carry_a_date", len(utility))
    utility_urls = {p.get("url") for p in utility}
    # A brand's story page is its about page, whatever the page typer called it.
    # A fashion retailer's `/<region>/story` pages were typed as articles by
    # their address and graded as "undated articles" - the page that says who
    # the company is, told to add a published date. See `_a_page_about_the_site`.
    about_pages = [p for p in items if _a_page_about_the_site(p)]
    result.signal("about_pages_not_expected_to_carry_a_date", len(about_pages))
    about_urls = {p.get("url") for p in about_pages}
    expect_dates = [p for p in items
                    if p.get("url") not in about_urls
                    and (p["page_type"] in ("article", "press")
                         or (_section_of(p["url"]) in dated_sections
                             and p.get("url") not in utility_urls))]
    if not expect_dates:
        result.skip("date-signals-present",
                    "no article or press page was crawled, and no section of the site holds "
                    "dated pages; dates are not expected on "
                    "evergreen pages such as pricing or contact{}".format(
                        "" if not listing_count else
                        ". {} that list other pages were left out: an index carries the dates "
                        "of the items on it, or none, and neither is a date of its "
                        "own".format(plural(listing_count, "crawled page", "crawled pages"))))
        result.signal("no_date_signals", False)
        return

    # The sitemap is the second reading, and it is the only date source that
    # does not live on the page. A publisher whose template prints no date but
    # whose sitemap gives every entry a real <lastmod> was told its pages
    # "carry no date at all", when a crawler had a modification date for every
    # one of them. That page still needs a visible date for a human, so the
    # finding stays - but it is a smaller claim than the one being made, and
    # the pages that have neither are named separately.
    # `has_sitemap` decides whether the sitemap can be named as a source at
    # all. A caller that passes no snapshot has not consulted one, and saying
    # it did would be the invented corroboration this field exists to stop.
    has_sitemap = bool((snapshot or {}).get("sitemaps"))
    lastmod = _sitemap_lastmod_by_page(snapshot)
    undated = [p for p in expect_dates if not _carries_its_own_date(p, chrome)]
    result.signal("no_date_signals", len(undated) == len(expect_dates))
    dated_only_in_sitemap = [p for p in undated if _page_key(p.get("url")) in lastmod]
    # The machine-readable half of what `has_any` combines, read apart. A page
    # can carry <time datetime="PT0S"> - a video player's duration - and have
    # no date signal at all, and "no <time> element" is a false sentence about
    # that page even though the finding itself is right.
    with_unusable_value = [p for p in undated
                           if (p.get("dates") or {}).get("machine_readable")]

    listing_note = ("" if not listing_count else
                    " {} that list other pages are not counted here: an index carries the "
                    "dates of the items on it, or none, and neither is a date of its "
                    "own.".format(plural(listing_count, "crawled page", "crawled pages")))
    if utility:
        listing_note += (" {} in dated sections {} a form, a map, directions or a sign-in "
                         "or booking page rather than something written on a date, so {} "
                         "not counted: {}.".format(
                             plural(len(utility), "page"), "is" if len(utility) == 1 else "are",
                             "it is" if len(utility) == 1 else "they are",
                             ", ".join(example_urls([p["url"] for p in utility]))))

    if not undated:
        result.skip("date-signals-present",
                    "every page where a date is expected - articles, press pages, and pages "
                    "in sections whose other pages are dated - carries at least one." +
                    listing_note)
        return

    result.add(
        id_hint="content-pages-carry-no-date",
        # Not "no date at all". On plenty of these sites the sitemap dates the
        # same page, and a title saying otherwise is contradicted two lines
        # later by this finding's own evidence.
        title="{} of {} show no date on the page".format(
            len(undated), plural(len(expect_dates), "page where one is expected",
                                 "pages where one is expected")),
        severity="medium", confidence="high",
        evidence="Pages carrying no date a reader or a machine could use - nothing in the "
                 "visible text, and no usable datePublished, dateModified or <time> value in "
                 "the markup: {}.{}{}".format(
                     ", ".join(example_urls([p["url"] for p in undated])),
                     " On {} of them a date property is present but holds something that is "
                     "not a date, such as a media duration.".format(len(with_unusable_value))
                     if with_unusable_value else "",
                     " {} of them do have a <lastmod> date in the sitemap, which a crawler "
                     "reads and a visitor never sees.".format(len(dated_only_in_sitemap))
                     if dated_only_in_sitemap else "") + listing_note,
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
        checked=("dates printed in the visible text of each page",
                 "<time> elements and datePublished/dateModified in the page markup")
                + (("<lastmod> dates for those pages in the site's sitemap",)
                   if has_sitemap and not dated_only_in_sitemap else ()),
    )


# A copyright notice and the years in it. The same opener the extractor uses,
# kept here because the extractor stores the years and throws the line away,
# and whose line it is cannot be decided from a bare number.
_COPYRIGHT_NOTICE_RE = re.compile(r"(?:©|\(c\)|copyright)\s*(?:(?:19|20)\d{2}\s*"
                                  r"[-–—,]\s*)*((?:19|20)\d{2})", re.I)

# How much of the line after the year is read when asking whose notice it is.
# Long enough to reach the name a credit gives, short enough not to swallow
# the next paragraph.
#
# It can still swallow some of it. Every block on the page arrives here joined
# by a single space - `visible_text` collapses all whitespace and no block
# boundary survives - so a notice with prose after it and no full stop between
# them cannot be cut at the right place. The residual error runs one way: a
# bare "(c) 2019" followed by ordinary copy can read as a notice naming
# somebody, and the staleness finding is then not made. That is the direction
# to be wrong in. The finding is `low` severity, and the error in the other
# direction - a publisher's, a photographer's or a supplier's line reported as
# the audited brand's - is the one this audit has made on every batch of sites.
_NOTICE_TAIL_CHARS = 80

# Where a notice ends when the text gives any sign of it: a sentence boundary,
# a separator a footer uses between its items, or the start of a second
# notice.
_NOTICE_END_RE = re.compile(r"[\r\n|·•]|(?<=[.!?])\s|©|\bcopyright\b", re.I)

# Words a copyright line uses about itself rather than about a party. A notice
# made only of these names nobody, and a notice that names nobody is the
# site's own - which is the ordinary shape of a footer, "(c) 2019 All rights
# reserved", and must keep firing.
_NOTICE_BOILERPLATE = frozenset({
    "all", "rights", "reserved", "right", "copyright", "inc", "ltd", "llc",
    "plc", "gmbh", "srl", "bv", "co", "company", "limited", "incorporated",
    "the", "and", "of", "by", "or", "present", "since",
    # What a notice that names nobody says next. Generic words, not names, and
    # they are what a bare footer line runs into when the page's blocks arrive
    # joined by a space.
    "unless", "otherwise", "stated", "noted", "content", "contents",
    "material", "site", "website", "page", "pages", "text", "images",
    "reproduced", "permission", "terms", "privacy", "policy", "cookies",
    # The same words in the other scripts this file can meet. They are
    # dictionary words, not anybody's name.
    "все", "права",
    "защищены",
    "alle", "rechte", "vorbehalten", "tous", "droits", "reserves",
    "todos", "los", "derechos", "reservados",
})

# Two, not one. A one-word remainder is most often the site's own name in a
# spelling this audit did not recognise - a trading name, an abbreviation, the
# name without its suffix - and silencing the finding on that would cost more
# than it saves.
_NOTICE_OTHER_PARTY_WORDS = 2

# How close to the end of the page's text a notice has to sit before this
# check may call it a footer. A site-wide footer is the last thing on the
# page; a rights credit for material the page is reproducing sits in the
# middle of it, and calling that "the footer" told an owner to edit a
# template that has nothing to do with it.
_FOOTER_TAIL_SHARE = 0.2
_FOOTER_TAIL_MIN_CHARS = 400


# Footers that list every year rather than a range: "(c) 2002 2003 2004 ...".
# The same run the extractor reads, so a notice's newest year is the same
# number here as it is there.
_YEAR_RUN_RE = re.compile(r"(?:\s*[-–—,]?\s*(?:19|20)\d{2})+")


def _copyright_notices(page):
    """Every copyright line in this page's text, with its years and position.

    Returns `(years, line, at_the_end)` for each. Empty when the page carries
    no text to read or states no notice in it, which is a different answer
    from "the notice is somebody else's": the caller falls back to the
    extractor's years there, because the extractor reads a wider region of the
    page than the snapshot stores, and a site-wide footer often sits outside
    the region a check can re-read.

    The whole visible text, not the main region, because that is where a
    footer is and where the extractor found these years in the first place.
    """
    text = page.get("text") or page.get("body_text") or ""
    if not text:
        return []
    length = len(text)
    tail_starts = length - max(int(length * _FOOTER_TAIL_SHARE), _FOOTER_TAIL_MIN_CHARS)
    out = []
    for match in _COPYRIGHT_NOTICE_RE.finditer(text):
        years = [int(match.group(1))]
        run = _YEAR_RUN_RE.match(text[match.end():match.end() + 160])
        if run:
            years.extend(int(y) for y in re.findall(r"(?:19|20)\d{2}", run.group(0)))
        opener, tail = text[match.start():match.end()], text[match.end():]
        tail = tail[:_NOTICE_TAIL_CHARS]
        line = (opener + _NOTICE_END_RE.split(tail)[0]).strip()
        out.append((tuple(sorted(set(years))), line, match.start() >= tail_starts))
    return out


def _notice_names_the_site(line, brand_name="", host=""):
    """Does this copyright line name the audited site, by its name or its address?

    The name alone was the only test, and the name this audit arrives with can
    be the whole of the homepage's `<title>` - "The Programming Language
    <Name>" - which no copyright line ever repeats. A project's own notice,
    "(c) 1994-2026 <name>.org, <University>", was then read as crediting
    somebody else, left out, and a newspaper's line reproduced on the
    project's press page was reported as the site's stale footer instead.

    A notice that writes the site's own address, or the word its address is
    built from, is the site's notice: that is how a great many sites sign
    their footers, and it is the one spelling of the name the site cannot have
    got wrong.
    """
    if speaks_about_itself(line, brand_name):
        return True
    host = strip_www(str(host or "").lower())
    if not host or not line:
        return False
    lowered = line.lower()
    if host in lowered:
        return True
    words = {w.lower() for w in letter_runs(line, 2)}
    return any(label in words for label in host_name_forms(host))


def _notice_parties(line, brand_name="", host=""):
    """The words this notice names somebody with, beyond its years and boilerplate."""
    tail = _COPYRIGHT_NOTICE_RE.sub(" ", line or "")
    return [w for w in letter_runs(tail, 2) if w.lower() not in _NOTICE_BOILERPLATE]


# How many different parties a page's notices have to credit before the page is
# a collection of other people's material. Two: a site's own footer carries one
# notice, and a page reprinting press clippings carries one per clipping.
_REPRODUCED_MATERIAL_PARTIES = 2

# The words a page puts in front of material it is reproducing.
_REPRODUCED_FROM_RE = re.compile(
    r"\b(?:reprint(?:ed)?\s+from|reproduced\s+(?:from|by\s+permission)|courtesy\s+of"
    r"|republished\s+from|originally\s+published\s+in)\b", re.I)
_REPRODUCED_FROM_WINDOW = 160


def _reproduces_other_material(page, notices, brand_name="", host=""):
    """Is this a page of reproduced clippings, whose notices credit their publishers?

    A project's press page reprints thirty newspaper and magazine articles,
    each followed by that publication's notice - "Copyright (c) 2015 <paper>.
    <All rights reserved in Portuguese>." A one-word remainder is let through
    as possibly the site's own name in a spelling this audit does not know
    (see `_NOTICE_OTHER_PARTY_WORDS`), so the newest clipping's year was
    reported as the site's own footer year, eleven years stale, on a site whose
    own notice reads 2026.

    Two readings, either of which settles it: the notices on the page credit
    two or more different parties, or the page introduces a notice as the
    credit of something reprinted. On such a page only a notice that names the
    site is the site's.
    """
    parties = set()
    for _years, line, _at_end in notices:
        if _notice_names_the_site(line, brand_name, host):
            continue
        named = tuple(w.lower() for w in _notice_parties(line, brand_name, host))
        if named:
            parties.add(named)
    if len(parties) >= _REPRODUCED_MATERIAL_PARTIES:
        return True
    text = page.get("text") or page.get("body_text") or ""
    for match in _COPYRIGHT_NOTICE_RE.finditer(text):
        before = text[max(0, match.start() - _REPRODUCED_FROM_WINDOW):match.start()]
        if _REPRODUCED_FROM_RE.search(before):
            return True
    return False


def _notice_names_another_party(line, brand_name="", host=""):
    """Does this copyright line credit somebody other than the audited site?

    The class this belongs to has produced a wrong finding on every batch of
    sites: a
    partner's, a supplier's or a rights-holder's fact read as the brand's. A
    library reproducing a publisher's text carries that publisher's notice
    mid-page - "(c) 2000 text supplied by <publisher>" - and the site's own
    footer may carry no notice at all, so the publisher's year was reported as
    the site's stale footer.

    `speaks_about_itself` answers it wherever the line names the brand or
    speaks in the first person. Where it does not, the test is whether the
    line names anyone at all: words beyond the year and the boilerplate. This
    is deliberately script-neutral - it counts words, it does not read them -
    because the sites this gets wrong are the ones this audit cannot read.

    `host` adds the site's own address to what counts as naming the site. See
    `_notice_names_the_site`.
    """
    if _notice_names_the_site(line, brand_name, host):
        return False
    return len(_notice_parties(line, brand_name, host)) >= _NOTICE_OTHER_PARTY_WORDS


def _check_copyright_year(result, pages, now, brand_name="", host=""):
    result.check("footer-copyright-year")
    years = set()
    carriers = []
    in_a_footer = False
    someone_elses = []
    for page in pages:
        # Coerced, because a snapshot written by an older build - or by hand,
        # which the SKILL.md invites - can carry these as strings, and
        # `max({"2019", 2020})` raises while `"2019" >= 2024` raises too.
        page_years = [int(y) for y in ((page.get("dates") or {}).get("copyright_years") or [])
                      if str(y).strip().isdigit()]
        if not page_years:
            continue
        notices = _copyright_notices(page)
        if notices:
            if _reproduces_other_material(page, notices, brand_name, host):
                mine = [n for n in notices if _notice_names_the_site(n[1], brand_name, host)]
            else:
                mine = [n for n in notices
                        if not _notice_names_another_party(n[1], brand_name, host)]
            someone_elses.extend(n[1] for n in notices if n not in mine)
            if not mine:
                continue
            page_years = [year for notice in mine for year in notice[0]]
            in_a_footer = in_a_footer or any(n[2] for n in mine)
        else:
            # No text to read the line out of. The extractor's years are all
            # there is, and they were trusted unconditionally before this.
            in_a_footer = True
        years.update(page_years)
        carriers.append(page["url"])
    if not years:
        if someone_elses:
            result.skip("footer-copyright-year",
                        "the copyright lines found on this site credit another party for "
                        "material the site reproduces, not the site itself, so their years say "
                        "nothing about how current this site is: {}".format(
                            "; ".join('"{}"'.format(truncate(line, 120))
                                      for line in sorted(set(someone_elses))[:3])))
            return
        result.skip("footer-copyright-year", "no copyright year was found in the page text")
        return

    newest = max(years)
    if newest >= now.year - COPYRIGHT_LAG_YEARS:
        result.skip("footer-copyright-year",
                    "the newest copyright year on the site is {}, which is current as of "
                    "{}".format(newest, now.isoformat()))
        return

    # Where it is, not where a footer usually is. The title said "in the
    # footer" about a notice sitting mid-document, and the fix that follows it
    # names a footer template the owner would then edit for nothing.
    where = "in the footer" if in_a_footer else "on the page"
    result.add(
        id_hint="footer-copyright-year-is-stale",
        title="The copyright year {} is {}".format(where, newest),
        severity="low", confidence="high",
        evidence="The most recent copyright year the site states about itself, across {} "
                 "page(s), is {}, {} years behind the reference date {}.{}".format(
                     len(carriers), newest, now.year - newest, now.isoformat(),
                     " Copyright lines crediting another party were left out of this: "
                     "{}.".format("; ".join('"{}"'.format(truncate(line, 120))
                                            for line in sorted(set(someone_elses))[:2]))
                     if someone_elses else ""),
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


# A year in the text is not a currency claim. `dates.as_of_years`, which this
# check used to read on its own, is a deliberately wide net - right for a
# signal, wrong for a finding. It produced four wrong firings on real sites:
#
#   a school's month index (2013)   a month index whose whole body reads
#                                     "Archive April 2026 ... December 2013",
#                                     and the advice was to remove the year
#   a school's code of conduct (2024) "Code of conduct last updated August
#                                     2024", an honest stamp; this audit asks
#                                     for exactly that stamp elsewhere
#   a statistics charity's biodiversity page (1900)  "figures for the year 1900 ...
#                                     from Vaclav Smil (2011)", a citation
#   that database's about page (2014)      "The design used as of 2014 was largely
#                                     created by Lennart Schoors", a credit
#
# So four independent gates now stand between a year and a finding: the phrase
# has to assert present currency, the sentence has to be about the site's own
# subject, the sentence must not be a stamp, a citation or a credit, and the
# page must not be an archive or an index.

# Phrases that assert the copy is current *now*. `last updated <year>` and
# `figures for <year>` are deliberately absent: the first is the dated stamp
# this audit recommends, the second introduces somebody else's number.
_CURRENCY_CLAIM_RE = re.compile(
    r"\b(?:(?:current|accurate|correct|complete|up[- ]to[- ]date)\s+as\s+of"
    r"|as\s+of"
    r"|updated\s+for"
    r"|valid\s+(?:for|until|through)"
    r"|prices?\s+(?:as\s+of|for)"
    r"|(?:guide|report|edition|list|ranking|round-?up)\s+for)"
    r"\s+(?:\w+\s+){0,2}((?:19|20)[0-9]{2})\b", re.I)

# An honest dated stamp, which is the thing the date-signals check asks for.
_UPDATE_STAMP_RE = re.compile(
    r"\b(?:last\s+(?:updated|revised|reviewed|amended|modified|edited)"
    r"|updated\s+(?:on|in)|published\s+(?:on|in)|revision\s+date)\b", re.I)

# Somebody else's figure. A parenthesised year is the giveaway that made
# `figures from Vaclav Smil (2011)` look like a claim about the site.
_CITATION_RE = re.compile(
    r"\((?:19|20)[0-9]{2}\)"
    r"|\b(?:et\s+al|ibid|doi|isbn|vol\.|pp?\.)\b"
    r"|\b(?:figures?|data|estimates?|statistics|numbers)\s+(?:from|by)\b"
    r"|\b(?:according\s+to|cited\s+in|source:|adapted\s+from|based\s+on\s+data)\b", re.I)

# A credit is a historical statement about who did something, not a claim that
# it is still current. This is the a browser-compatibility database design credit exactly.
_CREDIT_RE = re.compile(
    r"\b(?:created|designed|written|built|made|maintained|founded|launched|"
    r"contributed|photographed|illustrated|donated)\s+by\b", re.I)

# What the sentence has to be about for an old year to matter: the site
# itself, or the material the site is publishing. `speaks_about_itself` covers
# the first person and the brand name; this covers "prices as of 2022".
_CURRENCY_SUBJECT_RE = re.compile(
    r"\b(?:this\s+(?:page|guide|list|report|article|document|table|comparison|site)"
    r"|the\s+(?:information|content|figures|data|prices|pricing|rates|fees|list)"
    r"|prices?|pricing|rates|fees|availability|inventory|stock|coverage)\b", re.I)

# An archive or an index is a list of other pages. Every year in it names one
# of them, and deleting those years would delete the navigation.
_ARCHIVE_URL_RE = re.compile(
    r"/(?:archive|archives|back-?issues|all-posts|past|previous|index|sitemap|"
    r"tag|tags|category|categories|topics|authors)(?:/|$)"
    r"|/page/[0-9]+/?$"
    r"|/(?:19|20)[0-9]{2}(?:/[0-9]{1,2}|/[a-z]{3,9})?/?$", re.I)

_ARCHIVE_HEADING_RE = re.compile(r"^\s*(?:archive|archives|all posts|index)\b", re.I)

# Older than this and the year is history, not a lapsed claim. `1900` on a
# biodiversity page and `1917` on a founding story were both reported as copy
# asserting a currency it does not have.
AS_OF_HISTORICAL_YEARS = 15


def _is_archive_or_index(page):
    """A listing of other pages, where every year names an entry.

    The shared detector first. The URL pattern below was this file's own
    partial copy of the same idea and it recognised fewer pages: a category
    listing whose subheadings are the titles it links, a section root, or a
    page marked up as a `CollectionPage` are all indexes, and none of them
    matches the pattern. One list of them, so a page cannot be an index to one
    check in this file and an item to another.
    """
    if is_listing_page(page):
        return True
    if _ARCHIVE_URL_RE.search(urlparse(page.get("url") or "").path or ""):
        return True
    headings = (page.get("headings") or {}).get("h1") or []
    for heading in [page.get("title") or ""] + [str(h) for h in headings]:
        if _ARCHIVE_HEADING_RE.match(heading):
            return True
    return False


# "As of" and a whole calendar date: a figure stamped with the day it was
# measured. A database engine's size page reads "As of 2023-07-04, the size of
# <Name> library is generally less than 1 megabyte", and the advice was to
# "update or remove the year" - which would delete the one thing that makes the
# figure checkable. A figure that says when it was true is a correctly dated
# fact; the currency claim this check is for says "as of 2023" and means now.
_AS_OF_A_FULL_DATE_RE = re.compile(
    r"\bas\s+of\s+(?:(?:19|20)[0-9]{2}-[0-9]{1,2}-[0-9]{1,2}"
    r"|[0-9]{1,2}(?:st|nd|rd|th)?\s+[a-z]{3,9}\.?,?\s+(?:19|20)[0-9]{2}"
    r"|[a-z]{3,9}\.?\s+[0-9]{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)[0-9]{2}"
    r"|[0-9]{1,2}/[0-9]{1,2}/(?:19|20)[0-9]{2})", re.I)


def _a_dated_measurement(sentence, match):
    """Is this "as of" a full date stamped on a figure the sentence gives?

    Both halves. The date has to be a whole day, and the sentence has to carry
    a number other than that date - a size, a count, a share - for the date
    to be dating.
    """
    stamp = _AS_OF_A_FULL_DATE_RE.match(sentence, match.start())
    if not stamp:
        return False
    rest = sentence[:stamp.start()] + " " + sentence[stamp.end():]
    return bool(re.search(r"[0-9]", rest))


def _stale_currency_claim(page, now, brand_name, measured=None):
    """(year, sentence) where this page claims a currency it no longer has.

    `measured`, when a list, collects the dated measurements set aside so the
    caller can say so.
    """
    if _is_archive_or_index(page):
        return None
    text = page.get("body_text") or page.get("text") or ""
    if not text:
        return None
    floor, ceiling = now.year - AS_OF_HISTORICAL_YEARS, now.year - AS_OF_LAG_YEARS
    best = None
    for sentence in sentences(text):
        if (_UPDATE_STAMP_RE.search(sentence) or _CITATION_RE.search(sentence)
                or _CREDIT_RE.search(sentence)):
            continue
        if not (speaks_about_itself(sentence, brand_name)
                or _CURRENCY_SUBJECT_RE.search(sentence)):
            continue
        for match in _CURRENCY_CLAIM_RE.finditer(sentence):
            year = int(match.group(1))
            if not floor <= year <= ceiling:
                continue
            if _a_dated_measurement(sentence, match):
                if measured is not None:
                    measured.append(page.get("url"))
                continue
            if best is None or year > best[0]:
                best = (year, truncate(sentence.strip(), 160))
    return best


def _check_stale_year_references(result, pages, now, brand_name=""):
    result.check("stale-year-references")
    offenders = []
    measured = []
    for page in pages:
        claim = _stale_currency_claim(page, now, brand_name, measured)
        if claim:
            offenders.append((page, claim[0], claim[1]))
    measured = sorted(set(u for u in measured if u))
    result.signal("dated_measurements_not_counted", measured)
    measured_note = (
        " {} a figure stamped \"as of\" a whole calendar date ({}), which dates the figure "
        "rather than claiming it is current; refreshing the figure is worth doing, removing "
        "the date is not".format(
            "1 page gives" if len(measured) == 1 else "{} pages give".format(len(measured)),
            ", ".join(example_urls(measured))) if measured else "")
    if not offenders:
        result.skip("stale-year-references",
                    'no page claims to be current as of a year more than {} years old. Dated '
                    '"last updated" stamps, citations, credits and archive indexes carry years '
                    "without claiming currency, and are not counted.{}".format(
                        AS_OF_LAG_YEARS, measured_note))
        return

    result.add(
        id_hint="copy-references-an-old-year",
        title="{} as current as of an old year".format(
            plural(len(offenders), "page still describes itself",
                   "pages still describe themselves")),
        severity="low", confidence="medium",
        evidence="Sentences claiming the copy is current as of {} or earlier: {}.".format(
            now.year - AS_OF_LAG_YEARS,
            "; ".join('{} references {} in "{}"'.format(p["url"], y, s)
                      for p, y, s in sorted(offenders, key=lambda x: x[0]["url"])[:5])),
        mechanism="D", root_cause="stale-content",
        summary="Update or remove the year references in evergreen copy.",
        how_to_fix=[
            "Review each sentence above and either refresh the facts and the year, or drop the "
            "year from the claim if the copy is meant to stay evergreen.",
            'Avoid relative phrases such as "this year" and "recently"; they age silently.',
            'A "last updated <year>" stamp is not this problem and should stay. This finding is '
            "only about copy that says it is still current as of a year that has passed.",
        ],
        effort="low", owner="content owner",
        rationale="An explicit old year in the text is stronger evidence of "
                  "staleness than a missing date, because the page states it about itself.",
        affected_pages=[p["url"] for p, _y, _s in offenders],
    )


def _check_sitemap_lastmod(result, snapshot, now):
    """Both halves of sitemap dating: how many entries carry a date, and how old they are.

    crawl-access-audit owns whether a sitemap exists and resolves. Whether its
    dates mean anything is mechanism D, so it belongs here. Splitting the two
    across skills left one root cause with two owners.
    """
    result.check("sitemap-lastmod-coverage")
    # `sitemap-lastmod-recency` is registered where it is decided, not here.
    # `SkillResult` treats every check a function registers as one the
    # function's finding might be about, so registering both at the top made
    # the sparse-lastmod finding claim the recency check as fired - and the
    # recency branch below then skipped that same name. One check in two
    # buckets the report presents as exclusive.

    entries = []
    total_urls = 0
    # Counted apart from the parsed ones. "No sitemap entry carries a <lastmod>
    # date" is a false sentence about a sitemap whose every entry carries one
    # in a shape `parse_date` cannot read - a different problem, with a
    # different fix, and telling that publisher to start emitting <lastmod>
    # sends them to look at code that is already doing it.
    written = 0
    for record in snapshot.get("sitemaps") or []:
        for entry in record.get("urls") or []:
            total_urls += 1
            raw = (entry.get("lastmod") or "").strip()
            if raw:
                written += 1
            parsed = parse_date(raw)
            if parsed:
                entries.append(parsed)

    if not total_urls:
        for name in ("sitemap-lastmod-coverage", "sitemap-lastmod-recency"):
            result.skip(name, "no sitemap URLs were found, so there are no dates to assess")
        return

    coverage = len(entries) / float(total_urls)
    result.signal("sitemap_lastmod_coverage", round(coverage, 2))

    if coverage < 0.5:
        # The second reading: whether the pages themselves are dated. It
        # decides which fix is the right one. If the pages carry dates, the
        # generator has a real value and is not writing it out; if nothing on
        # the site carries a date, no modification date exists to publish and
        # the work starts on the pages.
        crawled = pages_of(snapshot)
        dated_pages = sum(1 for p in crawled if (p.get("dates") or {}).get("has_any"))
        unreadable = written - len(entries)
        result.add(
            id_hint="sitemap-lastmod-sparse",
            # "Most" is wrong when it is all of them, and a site publishing
            # 1,116 entries with zero dates was told "most" carry none.
            title=("Sitemap <lastmod> dates are written in a shape crawlers cannot read"
                   if unreadable > len(entries) else
                   "No sitemap entry carries a <lastmod> date" if not written
                   else "Most sitemap entries carry no <lastmod> date"),
            severity="low", confidence="high",
            evidence="{} of {} ({}%) carry a <lastmod> value a crawler can parse.{}{}".format(
                len(entries), sitemap_total_phrase(sitemap_scope(snapshot.get("sitemaps"))),
                pct(len(entries), total_urls),
                " Another {} carry a <lastmod> whose value is not a date any crawler will "
                "read.".format(unreadable) if unreadable else "",
                " The pages themselves are dated on {} of the {} crawled, so the generator has "
                "a real value to write.".format(dated_pages, len(crawled)) if dated_pages else
                " No crawled page carries a date either, so no modification date for this "
                "content exists anywhere a crawler can see."),
            mechanism="D", root_cause="no-date-signal",
            summary="Emit an accurate <lastmod> for every sitemap entry.",
            how_to_fix=[
                "Configure the sitemap generator to write <lastmod> from the page's real "
                "modification date.",
                "Write it as a W3C datetime: 2026-01-31, or 2026-01-31T09:00:00+00:00. Other "
                "shapes are ignored rather than guessed at.",
                "Do not set <lastmod> to today's date on every build. A date that always "
                "changes carries no information, and crawlers learn to ignore it.",
            ],
            effort="low", owner="developer",
            rationale="<lastmod> is how a crawler decides which pages are worth "
                      "re-fetching. Without it, updated pages are re-read on a slow default "
                      "cycle, so your changes take longer to be noticed.",
            checked=("the <lastmod> value on every URL in the site's sitemaps",
                     "dates on the crawled pages those sitemap entries point at"),
        )
    else:
        result.skip("sitemap-lastmod-coverage",
                    "{}% of sitemap entries carry a <lastmod> value".format(
                        pct(len(entries), total_urls)))

    result.check("sitemap-lastmod-recency")
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

# Below this many crawled pages there is no such thing as "linked from only
# one page", so the citation test stays out of the way of a small crawl.
CITATION_MIN_PAGES = 5


def _site_own_profiles(pages, declared, names, host=""):
    """({platform: url}, [urls dropped]) - the accounts the site links as its own.

    `social_profiles` holds one URL per platform per page, and flattening the
    pages with `dict.update` let whichever page came last win. On
    a statistics charity that handed an article's citations to the brand:
    `youtube.com/@altrufisica` and
    `en.wikipedia.org/wiki/History_of_ethanol_fuel_in_Brazil` were recorded as
    the brand's own profiles, and the report then certified "the site links to
    8 distinct off-site profiles" and "all 4 verifiable profile link(s)
    resolve" about two accounts belonging to other people.

    An account is linked from the site's chrome and so appears on page after
    page; a citation appears in the one article that cites it. Where a URL
    appears once and neither names the brand, sits in the site's own `sameAs`,
    nor is an opaque identifier that cannot be read either way, it is a
    citation. That test runs for every platform, not for Wikipedia alone.

    `host` is read for the same reason the declared names are: the labels of
    the site's own address are romanised forms of its name, and on a site whose
    name is not written in the Latin alphabet they are the only forms a handle
    could ever carry. See `host_name_forms`.
    """
    # The name test is only ever an escape from the appearance test below, so
    # widening the names it may use can keep a profile and can never drop one.
    names = [n for n in list(names) + host_name_forms(host) if n]
    counts = {}
    # Where the link sits decides this better than how often it appears. A
    # brand's own account is in the header or the footer; a citation is in the
    # body of the one article that cites it. Counting appearances alone dropped
    # a site's real X account, linked once from its footer and once from its
    # `sameAs`, on a run where the `sameAs` had been broken - and a footer link
    # is exactly the evidence that settles it.
    in_chrome = set()
    for page in pages:
        links = page.get("links") or {}
        for bucket in ("nav", "footer"):
            for link in links.get(bucket) or []:
                url = (link or {}).get("url")
                if url:
                    in_chrome.add(url)
        for platform, url in (page.get("social_profiles") or {}).items():
            if url:
                counts[(platform, url)] = counts.get((platform, url), 0) + 1

    by_platform = {}
    for (platform, url), count in counts.items():
        by_platform.setdefault(platform, []).append((count, url))

    chosen, dropped = {}, []
    total = len(pages)
    for platform, entries in sorted(by_platform.items()):
        claimed = declared.get(platform)
        # The site's own `sameAs` first, then the URL the most pages link, then
        # the shortest, then alphabetically, so two runs of one snapshot cannot
        # pick different accounts for one platform.
        order = sorted(entries,
                       key=lambda e: (0 if e[1] == claimed else 1, -e[0], len(e[1]), e[1]))
        for count, url in order:
            if _looks_like_a_citation(url, count, total, claimed, names, in_chrome):
                dropped.append(url)
                continue
            chosen[platform] = url
            break
    return chosen, sorted(dropped)


# An article's address, not an account's. The last segment of the path is a
# headline: several hyphenated words, very often ending in the publisher's
# numeric story id. A furniture retailer's report counted
# "<magazine>.test/<brand>-nesting-coffee-table-review-37439576" among its own
# off-site profiles - a review, linked "Read more" from three regional press
# pages - and it survived the citation test twice over: the address contains
# the brand's name, and it appears on three pages. A review names the brand
# because it is about the brand, and a press page per region links it once
# each. Neither makes it the brand's account.
#
# Only on a host that is not a profile platform. On one of those, an account
# is an account by where it lives, and a handle is allowed to be long.
_ARTICLE_ID_RE = re.compile(r"-\d{5,}$")
_ARTICLE_SLUG_MIN_WORDS_WITH_ID = 3
_ARTICLE_SLUG_MIN_WORDS = 6
_PAGE_SUFFIX_RE = re.compile(r"\.(?:html?|php|aspx?)$", re.I)


def _on_a_profile_platform(host):
    """Is this host, or a host it sits under, one the extractor names as a platform?"""
    return any(host == known or host.endswith("." + known)
               for known in SOCIAL_PLATFORMS if "/" not in known)


def _is_coverage_not_an_account(url):
    """Is this address an article about the brand rather than the brand's account?"""
    parts = urlparse(url or "")
    host = strip_www(parts.netloc.lower())
    if not host or _on_a_profile_platform(host):
        return False
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return False
    slug = _PAGE_SUFFIX_RE.sub("", segments[-1].lower())
    words = [w for w in slug.split("-") if w and not w.isdigit()]
    if _ARTICLE_ID_RE.search(slug) and len(words) >= _ARTICLE_SLUG_MIN_WORDS_WITH_ID:
        return True
    return len(words) >= _ARTICLE_SLUG_MIN_WORDS


def _looks_like_a_citation(url, pages_linking, page_count, claimed, names,
                           in_chrome=()):
    """Is this a link to somebody else's account rather than the brand's own?"""
    if claimed and url == claimed:
        return False
    # Before the chrome, the name and the count, because none of the three can
    # turn a headline into an account. See `_is_coverage_not_an_account`.
    if _is_coverage_not_an_account(url):
        return True
    if url in in_chrome:
        return False
    if profile_is_opaque(url):
        return False
    if any(profile_names_brand(url, name) for name in names):
        return False
    # A test that cannot pass must not be the thing that rejects.
    #
    # The handle test above compares ASCII against ASCII: `brand_key` keeps
    # letters and digits and drops everything else, so a name written in
    # Japanese, Korean, Arabic, Greek or Cyrillic reduces to nothing and no
    # handle on earth can match it. Where every name the crawl found does
    # that, the only reading left is the appearance test - and the appearance
    # test alone drops every account a site links once from the body of one
    # page, which is what a government publishing a different official account
    # on each of five pages looks like. A high-severity "no off-site profile is
    # linked from the crawled pages" and a verdict reading "nothing off the
    # site corroborates it" followed, about a city government with five.
    if not any(brand_key(name) for name in names):
        return False
    return page_count >= CITATION_MIN_PAGES and pages_linking < 2


# A URL written in a plain-text or Markdown file. Trailing punctuation is
# stripped by the caller, because a link at the end of a sentence in a
# Markdown list picks up the full stop and the closing bracket.
#
# The backtick is here for the same reason as the bracket. A URL written in
# Markdown code formatting ends `...SKILL.md``, and the trailing backtick was
# read as part of the address - so the reading that tells a document from an
# account could not see the file extension, and a shopping platform's
# instruction file was counted as one store's own off-site profile.
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"'`\)\]]+", re.I)

# Where the body of `/llms.txt` is recorded. `text` is what the crawl writes,
# capped; the other two are read so a snapshot from another build of this
# marketplace still yields its contents rather than being treated as a file
# nobody kept.
_LLMS_TXT_BODY_KEYS = ("text", "body", "content")


def _llms_txt_body(record):
    """The contents of the site's own agent file, when the crawl recorded them."""
    for key in _LLMS_TXT_BODY_KEYS:
        value = (record or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _subresource_domains(pages):
    """Hosts this site loads its code from, across the whole crawl.

    Per page, `scripts.third_party_hosts` is what the extractor recorded; the
    reading of which of them is a platform rather than a profile is
    `subresource_domains`, and it is imported rather than repeated here so the
    two doors into the profile count - a declaration on the page and a
    declaration in the site's own agent file - cannot come apart on what a
    platform is. A store's theme host appears on its product pages and not on
    its policy pages, so this is unioned across the crawl and not read per page.
    """
    hosts = set()
    for page in pages or ():
        hosts.update((page.get("scripts") or {}).get("third_party_hosts") or ())
    return subresource_domains(hosts)


# An address an agent file is telling an agent to go and fetch: a protocol
# specification, an API schema, another site's instruction file. A document is
# something to read, not somewhere a brand keeps an account - and one hosted
# store's agent file named a shopping platform's instruction file twice, both
# times counted as that store's own off-site profile.
#
# File types by name, not "the last segment contains a dot", because handles
# contain dots: an account written `<first>.<second>` on a photo platform is a
# handle and must survive this.
_DOCUMENT_URL_RE = re.compile(
    r"\.(?:md|markdown|txt|json|jsonld|ya?ml|xml|rss|atom|csv|pdf)(?:$|[?#])", re.I)


def _profiles_declared_in_llms_txt(snapshot, pages=()):
    """({host: url}, note) - off-site accounts the site names in its own agent file.

    `/llms.txt` is a file the site publishes about itself, so an off-site link
    in it is the site saying that account is connected to it. That is the same
    kind of assertion `sameAs` makes and the strongest evidence a
    corroboration check can have.

    A JavaScript project's report carried "The site links to no off-site
    profile at all" as its only high-severity finding. This same audit had
    fetched `/llms.txt` and recorded it as present at HTTP 200, and that file
    links the project's public repository - so the one thing the finding said
    was absent was named in a document the audit had already asked for.

    Keyed by hostname, which is what the page extractor already does with a
    `sameAs` entry on a host no platform list recognises: the declaration is
    the evidence, not the hostname.
    """
    record = (snapshot or {}).get("llms_txt") or {}
    if not record.get("present"):
        return {}, ""
    where = record.get("url") or "/llms.txt"
    body = _llms_txt_body(record)
    if not body:
        # The crawl records the body of a file it found, so reaching here means
        # this snapshot does not carry it: an older run, or a file that was
        # served empty. Said as a gap in this run rather than as something the
        # audit never looks at, which is no longer true and would send a reader
        # to argue with the wrong thing.
        return {}, (" The site also publishes {}, a file it writes about itself, which this "
                    "audit fetched (HTTP {}); its contents are not in this snapshot, so an "
                    "account named only in that file is not among the ones counted "
                    "here.".format(where, record.get("status")))

    host = strip_www(urlparse(snapshot.get("origin") or "").netloc.lower())
    subresource = _subresource_domains(pages)
    found, off_site = {}, set()
    for match in _URL_IN_TEXT_RE.finditer(body):
        url = match.group(0).rstrip(".,;:")
        other = strip_www(urlparse(url).netloc.lower())
        if not other or not host or other == host or other.endswith("." + host):
            continue
        # Distinct addresses. An agent file repeats the address it wants an
        # agent to install - four times in the measured case - and counting the
        # repeats told a reader the file named fourteen places when it named
        # six, which is a number the reader can check and find wrong.
        off_site.add(url)
        # This file is not the assertion `sameAs` is. `sameAs` says "these
        # accounts are me" and can say nothing else; a paragraph of prose names
        # whatever its author found useful, and on a hosted store the author is
        # the platform. One such file named the platform's own site, that
        # platform's developer site, a protocol specification, a demonstration
        # store and a shopping assistant's instruction file - five addresses,
        # every one recorded as the brand's own off-site profile, carrying a
        # count of 3 up to 8 and past the threshold on the measure this
        # marketplace calls its strongest.
        #
        # So an address here earns its place the way a link on a page does: it
        # has to be an account rather than a company's front door, on a host
        # this site does not merely load its code from. That costs the one case
        # a declaration alone could carry - an account on a platform that gives
        # each customer a bare subdomain, named nowhere but this file - and
        # that account still counts from `sameAs` or `rel="me"`, which say what
        # they mean.
        if _DOCUMENT_URL_RE.search(urlparse(url).path or ""):
            continue
        if not counts_as_off_site_profile(url, subresource_hosts=subresource):
            continue
        found.setdefault(other, url)
    if not found and off_site:
        return {}, (" {} names {}, but each is the platform this site is built on, a "
                    "document to fetch, or a company's front door rather than an account, "
                    "so the site's own agent file adds nothing here.".format(
                        where, plural(len(off_site), "off-site address",
                                      "off-site addresses")))
    if not found:
        return {}, (" {} names no off-site address, so the site's own agent file adds "
                    "nothing here.".format(where))
    return found, (" {} of these are named in {}, which is the site declaring them about "
                   "itself.".format(len(found), where))


# Which platforms are worth naming, by what kind of thing the site is.
#
# The finding itself does not vary. Breadth is the strongest separator this
# marketplace measured and it is not a fact about shops: a program benefits
# from a repository host and a package index exactly as a shop benefits from a
# photo-sharing platform, and both are somewhere else that says the same name.
# What went wrong was never that the advice fired - it was the platforms it
# named. A national weather service was told to claim a company page on a
# professional network and a profile on a startup-funding database: one exists
# to describe employers, the other to describe investments, and a national
# meteorological agency is neither. It has a Wikidata item, a Wikipedia
# article, an entry in the official register of public bodies and a data
# portal, and every one of those is a place an assistant reads.
#
# Written as what to go and claim, not as brand names where a brand name is not
# the point: "the package index people install it from" is followable by
# somebody who knows their own language's index and stays true when it changes.
_PLATFORMS_TO_CLAIM = (
    (LOCAL_BUSINESS,
     "the mapping service people search in, the review sites your customers already "
     "use, your trade association's directory, and Facebook and Instagram"),
    (ONLINE_SELLER,
     "Instagram, TikTok, YouTube, Pinterest, Facebook, the review site your buyers "
     "check before ordering, and every marketplace you already sell on"),
    # A company or a non-profit: the classifier cannot tell the two apart, so
    # nothing here assumes a trade. "The trade bodies, standards bodies and
    # industry directories you already belong to" went to a network of
    # religious scholarship, which belongs to none of them.
    (ORGANISATION,
     "LinkedIn, YouTube, X, and the registers and directories where organisations "
     "like this one are listed"),
    (PROJECT,
     "the repository host your code lives on, the package index people install it "
     "from, Wikidata, and the forum or chat where your users already talk"),
    (PUBLIC_BODY,
     "Wikidata and Wikipedia, the official register of public bodies that lists you, "
     "your open-data portal, and the channels you already publish notices on"),
    (PERSONAL_OR_ACADEMIC,
     "your researcher identifier, your institution's staff directory, the repository "
     "host your code or data lives on, Wikidata, and LinkedIn"),
    (PUBLICATION,
     "Wikidata and Wikipedia, the indexes and aggregators that list publications, your "
     "serial number record, YouTube, and the platforms your writers already post on"),
)

# The line every site got before any of this existed, and the line an
# unclassified site keeps getting. A wrong guess about what a site is caused
# the failures above, so a classifier that cannot decide has to change nothing.
#
# The platforms are unchanged. The last clause is not: "your industry's
# directories" assumes an industry, and on a site the classifier could not
# place it went to a one-page essay and a central bank alike. The registers
# and directories that list a body like this one exist for every kind of
# body, so that is what the line names.
_PLATFORMS_WHATEVER_THE_SITE_IS = ("LinkedIn, Instagram, YouTube, X, Facebook, and the "
                                   "registers and directories where organisations like "
                                   "this one are listed")

# The step that follows the platform line, in two wordings. The business one
# names a company record and an employer profile, which only a business has;
# it went to a central bank and to a one-page essay, neither of which the
# classifier had determined to be one. Any other site gets the same step
# without the assumption.
_COMPANY_RECORD_STEP = (
    "Claim the company record too - a business database entry and an employer "
    "profile - so an assistant asked who is behind the brand finds an answer that "
    "is not the brand's own site.")
_REGISTER_ENTRY_STEP = (
    "Claim the entries that describe the organisation itself too - in the registers "
    "and directories where organisations like this one are listed - so an assistant "
    "asked who is behind the site finds an answer that is not the site itself.")


# A site that is one document - an essay, a manifesto, a single reference
# page, published in however many languages. A one-page essay that links its
# own source repository in its footer was told to "cover the obvious ones
# first: LinkedIn, Instagram, YouTube, X, Facebook" and to claim a register
# entry "where organisations like this one are listed". An essay has no
# employer page and is listed in no register; the places that say the same
# name about it are where its source lives, a knowledge base, and the forums
# that link it when somebody asks the question it answers.
_PLATFORMS_FOR_ONE_DOCUMENT = (
    "the repository host its source lives on, Wikidata, and the forums, wikis and "
    "communities where people already link to it")


def _how_to_widen_the_footprint(kind, one_document=False):
    """The steps for claiming more profiles, named for this kind of site.

    `one_document` is the site being a single document; see
    `_one_document_site`. It outranks an undetermined kind, and never a kind
    this audit determined, because a business with a one-page site is still a
    business.
    """
    platforms = _PLATFORMS_WHATEVER_THE_SITE_IS
    for name, named_for_that_kind in _PLATFORMS_TO_CLAIM:
        if kind.is_certainly(name):
            platforms = named_for_that_kind
            break
    # A document outranks "a person, a university or a research group": the
    # classifier reaches that kind for a one-page essay because nothing else
    # fits, and a researcher identifier and a staff directory are no more an
    # essay's than an employer page is.
    if one_document and not kind.is_certainly(
            LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION, PUBLIC_BODY, PUBLICATION, PROJECT):
        platforms = _PLATFORMS_FOR_ONE_DOCUMENT
    steps = [
        "Claim the profiles that apply to your category. Breadth is what matters here, not "
        "prestige: the brands assistants name are on more platforms, not better ones.",
        "Cover the obvious ones first: {}.".format(platforms),
    ]
    # `might_be`, not `is_certainly`: this answers True on every site nothing
    # was decided about, so the step survives wherever the classifier is silent
    # and goes quiet only where the site's own structure says a company profile
    # is not a thing this site can have. A program has no employees to review
    # and no funding round to list.
    #
    # Which wording is `is_certainly`: a company record is named only where
    # the site has been determined to be a business. See `_COMPANY_RECORD_STEP`.
    if (kind.might_be(LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION, PUBLICATION)
            and not (one_document and not kind.determined)):
        steps.append(_COMPANY_RECORD_STEP if kind.is_certainly(LOCAL_BUSINESS, ONLINE_SELLER)
                     else _REGISTER_ENTRY_STEP)
    steps += [
        "Use the identical name and the identical one-sentence description on every profile. "
        "Wording that matches exactly is what makes them corroborate rather than merely "
        "coexist.",
        "List every profile URL in the Organization `sameAs` array on your site.",
        "Link back to the site from each profile, so the connection is stated from both ends.",
    ]
    return steps


# Hosts where the first path segment is an account and the second is one of its
# repositories. The list the extractor reads repository owners from.
_REPOSITORY_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org",
                     "sourceforge.net", "gitea.com")


def _own_repository(pages, names, host=""):
    """(platform, url) of a repository the site links whose name is the site's own.

    The extractor counts a repository link as the site's account when the
    *account* segment names the site, or when one account is linked twice.
    A one-page essay's footer reads "Source on GitHub" and links
    `<repository host>/<a person>/<the site's own address>`: the account is the
    author's, the repository is the site, and it is linked once per page - so
    it was never counted, and the report said the site links no off-site
    profile while the site's own source was one click away.

    The repository segment is read here, and it has to be the site's name or
    its whole address - not a word that merely contains it - so a dependency
    the site credits is still somebody else's.
    """
    own = {brand_key(name) for name in names if name}
    bare_host = strip_www(str(host or "").lower())
    if bare_host:
        own.add(brand_key(bare_host))
        own.update(brand_key(label) for label in host_name_forms(bare_host))
    own.discard("")
    if not own:
        return None
    for page in pages:
        links = page.get("links") or {}
        for bucket in ("footer", "nav", "external"):
            for link in links.get(bucket) or []:
                url = link.get("url") if isinstance(link, dict) else link
                parts = urlparse(str(url or ""))
                link_host = strip_www(parts.netloc.lower())
                if link_host not in _REPOSITORY_HOSTS:
                    continue
                segments = [s for s in parts.path.split("/") if s]
                if len(segments) < 2:
                    continue
                if brand_key(re.sub(r"\.git$", "", segments[1])) in own:
                    return (SOCIAL_PLATFORMS.get(link_host) or link_host,
                            "https://{}/{}/{}".format(link_host, segments[0], segments[1]))
    return None


# A path segment that names a language edition: `/de/`, `/pt-br/`, `/fil/`.
_EDITION_SEGMENT_RE = re.compile(r"^[a-z]{2,3}(?:[-_][a-z0-9]{2,4})?$", re.I)

# How many words a page needs before it is a document rather than a stub.
_ONE_DOCUMENT_MIN_WORDS = 150


def _document_key(url):
    """The path of a page with a leading language-edition segment set aside."""
    segments = [s for s in urlparse(str(url or "")).path.split("/") if s]
    if segments and _EDITION_SEGMENT_RE.match(segments[0]):
        segments = segments[1:]
    return "/".join(segments)


def _one_document_site(pages, every_record=()):
    """Is this site a single document, published in one or more languages?

    `every_record` is the whole crawl, content or not, so a link to an address
    the crawl fetched and set aside - a copy of the page reached through a
    broken relative link - is resolved to the document it served.

    Three conditions, all read off the crawl. Every page with enough words to
    be a document is the same document - one path once its language segment is
    set aside, or the same text word for word. No page links to a page of the
    site that is not an edition of it, so the crawl did not merely stop short
    of the rest. And at least one page has enough words to be a document.

    A one-page essay published in nine languages is the case: nine pages typed
    home or other, the same essay in each, no internal link anywhere.
    """
    bodies, keys, crawled = {}, set(), {}
    substantial = 0
    content = {id(page) for page in pages}
    for page in list(pages) + [p for p in every_record or () if id(p) not in content]:
        body = " ".join((page.get("body_text") or "").split())
        key = _document_key(page.get("final_url") or page.get("url"))
        # A second address serving the same text is the same document, which
        # is what a relative link resolved against an edition's path produces.
        if body and body in bodies:
            key = bodies[body]
        elif body:
            bodies[body] = key
        for address in (page.get("url"), page.get("final_url")):
            if address:
                crawled[str(address).rstrip("/")] = key
        if id(page) not in content:
            continue
        words = page.get("word_count") or len(body.split())
        if words < _ONE_DOCUMENT_MIN_WORDS:
            continue
        substantial += 1
        keys.add(key)
    if not substantial or len(keys) != 1:
        return False
    for page in pages:
        for link in (page.get("links") or {}).get("internal") or []:
            url = str((link.get("url") if isinstance(link, dict) else link) or "")
            if not url:
                continue
            key = crawled.get(url.split("#")[0].rstrip("/"), _document_key(url))
            if key not in keys | {""}:
                return False
    return True


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
    # A URL of the right shape on the right platform is somebody's profile,
    # not necessarily this brand's. Counting shape alone put a contributor's
    # personal GitHub account and a Wikipedia article about a computer-science
    # term into an open-source project's corroboration count, while the
    # project's own org page - linked dozens of times, always as a repository
    # path - was never counted at all.
    brand_name = ((snapshot.get("brand") or {}).get("name") or "")
    # Read once here, and used only to choose which platforms the fix names.
    # The finding fires on every kind of site: breadth separated brands
    # assistants name from brands they ignore in all six categories studied,
    # and nothing in that result is about shops.
    kind = site_kind(snapshot)
    declared = {}
    for page in pages:
        declared.update(page.get("declared_profiles") or {})
    brand = snapshot.get("brand") or {}
    names = [brand_name, brand.get("domain_token")] \
        + list(brand.get("alternate_names") or []) \
        + list(brand.get("authoritative_variants") or []) \
        + list(brand.get("fallback_candidates") or [])
    names = [n for n in names if n]

    candidates, citations = _site_own_profiles(pages, declared, names,
                                               host=brand.get("host") or "")
    result.signal("profiles_dropped_as_citations", citations)

    profiles = profiles_naming_brand(
        candidates, brand_name, declared, name_forms=names[1:])
    unattributed = {k: v for k, v in candidates.items() if k not in profiles}
    result.signal("profiles_not_naming_the_brand", sorted(unattributed))

    # The site's own agent file, which is a declaration and not a link this
    # audit had to attribute to anybody. `setdefault`, so a platform already
    # recognised from the pages keeps the URL those pages gave it.
    from_llms_txt, llms_note = _profiles_declared_in_llms_txt(snapshot, pages)
    for platform, url in sorted(from_llms_txt.items()):
        profiles.setdefault(platform, url)
    result.signal("profiles_declared_in_llms_txt", sorted(from_llms_txt))
    # The site's own source repository, named after the site. See
    # `_own_repository`.
    repository = _own_repository(pages, names, brand.get("host") or "")
    if repository and repository[0] not in profiles:
        profiles[repository[0]] = repository[1]
    result.signal("own_repository", repository[1] if repository else "")
    one_document = _one_document_site(pages_of(snapshot), snapshot.get("pages") or ())
    result.signal("one_document_site", one_document)

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
        # because the count is small contradicted our own evidence, and a reader
        # seeing the README's "strongest signal we measured" next to a medium
        # finding would be right to ask which of the two we believed.
        # "links to only 0 off-site profiles" is not a sentence anybody writes.
        # Zero is a different statement from a small number, and it is the one
        # that matters most here.
        #
        # Scoped to what was read, because a title is the first thing anybody
        # reads and this one was making a larger claim than the evidence
        # beneath it. "The site links to no off-site profile at all" was the
        # only high-severity finding in one project's report, and that
        # project's own `/llms.txt` - fetched by this audit and recorded as
        # present - links its public repository. The evidence line underneath
        # had always named its sources; the title had not, and a title that
        # outruns its evidence is the half a reader quotes back.
        title=("No off-site profile is linked from the crawled pages or declared in sameAs"
               if not profiles
               else "Only {} linked from the crawled pages or declared in sameAs".format(
                   plural(len(profiles), "off-site profile is", "off-site profiles are"))),
        severity=("high" if not profiles
                  else "medium" if len(profiles) < PROFILE_BREADTH_THIN else "low"),
        confidence="high",
        evidence="Distinct off-site profiles linked from the crawled pages or listed in "
                 "`sameAs`: {}. In our within-category study, brands assistants name linked "
                 "a mean of {} and comparable brands they do not name linked {} - the only "
                 "measure that pointed the same way in all six categories tested. That study "
                 "is ours: 29 crawlable sites, and whether an assistant \"names\" a brand was "
                 "a judgement we made rather than a systematic query, so treat it as a strong "
                 "association and not a proven cause.{}".format(
                     ", ".join(sorted(profiles)) or "none",
                     PROFILE_BREADTH_NAMED_MEAN, PROFILE_BREADTH_UNNAMED_MEAN, llms_note),
        mechanism="D", root_cause="weak-corroboration",
        summary="Claim more of the places that independently confirm the brand exists, and list "
                "every one of them in Organization `sameAs`.",
        how_to_fix=_how_to_widen_the_footprint(kind, one_document),
        effort="medium", owner="marketing",
        rationale="A fact stated only on the brand's own site is one source's word "
                  "for it. Each additional profile that repeats the same name and description is "
                  "another independent source agreeing. This is the strongest signal we measured, "
                  "and it is almost entirely within a brand's control.",
        # Three readings, all of them already in the code above. Counting only
        # body links handed a statistics charity an article's outbound
        # citations as its own accounts and certified eight profiles for a
        # brand that has two; counting only `sameAs` loses the real account of
        # every site whose structured data is broken or absent.
        # The real limit here is a vocabulary, not a place, and saying "we
        # looked in the footer" hid that. A browser-compatibility database was
        # told it links one off-site profile: its footer carries a Patreon and
        # two Mastodon accounts on every page, the crawl recorded all three,
        # and the platform list threw them away. A reader could not have
        # worked that out from a sentence about footers.
        # And it says what the code does rather than what the counting was
        # meant to feel like. "Links anywhere on the crawled pages, body,
        # header and footer alike" was the old wording, and the reading it
        # described is not the one that runs: a link seen in the body of a
        # single page is dropped unless something attributes it to this site.
        # That is a defensible rule and it was the one that lost a city
        # government its five official accounts, so a reader has to be able to
        # see it in the sentence and argue with it.
        #
        # The first clause used to say the audit reads "a fixed list of profile
        # platforms", which was true and is not any more: the recogniser now
        # reads the shape of the path rather than the name of the host, because
        # a list of platforms had already been "completed" twice and was still
        # missing every Asian platform - which cost a footwear maker its three
        # storefronts and produced its report's high-severity headline. A
        # sentence describing a method the code no longer uses is worse than no
        # sentence, because a reader who checks it concludes the tool is lying
        # about something it got right.
        checked=("links on the crawled pages whose path names an account: a role "
                 "word the platform itself uses to mean an account follows, then "
                 "a handle, then optionally which view of that account was "
                 "linked. That reading works on any host, including one this "
                 "audit has never seen. A bare name at the root of a host is a "
                 "section of that site rather than an account, and counts only "
                 "where the host is one this audit can name or where the name is "
                 "the audited site's own. A link in a page's header or "
                 "footer is the site's own. A link that appears in the body of "
                 "one page only, on a crawl of {} pages or more, is read as a "
                 "citation of somebody else's account unless the site's sameAs "
                 "claims it, its handle carries a name this site publishes for "
                 "itself, or the platform identifies accounts by number".format(
                     CITATION_MIN_PAGES),
                 'accounts the site declares with rel="me", which is how a '
                 "self-hosted account on any domain can be recognised at all",
                 "the sameAs list in the site's Organization structured data")
                # Named only where it was actually read. Listing a source the
                # run did not open is the invented corroboration this field
                # exists to stop, and the evidence line says instead that the
                # file exists and went unread.
                + (("off-site addresses named in the site's own /llms.txt",)
                   if from_llms_txt else ()),
    )
    return profiles


def _check_profile_links_resolve(result, profiles, fetcher, allow_network,
                                 snapshot=None, time_budget=None):
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
        # Not "told not to make extra requests" unconditionally: see
        # `no_network_reason`. The orchestrator appends the same flag when the
        # caller asks for it and when the run has spent its clock, and naming the
        # flag in the second case tells the reader they made a choice they did not
        # make.
        result.skip("profile-links-resolve",
                    no_network_reason(
                        snapshot, time_budget,
                        flag_reason="checking whether {} profile link(s) still resolve "
                                    "needs network access, and this run was told not to "
                                    "make extra requests".format(len(checkable)),
                        exhausted_reason="no profile link was probed to see whether it "
                                         "still resolves"))
        return

    gone, alive, unchecked = [], [], []
    robots_cache = {}
    for platform, url in sorted(checkable.items()):
        # The platform's own robots.txt decides whether we may ask. LinkedIn
        # and X disallow every automated client at the root, and probing them
        # anyway broke the one guarantee this marketplace makes about itself.
        # Reported as unchecked, with the reason, rather than dropped.
        if not third_party_allows(fetcher, url, robots_cache):
            unchecked.append((platform, url,
                              "that platform's robots.txt disallows automated requests, so "
                              "this audit did not send one"))
            continue
        try:
            response = fetcher.get(url, method="HEAD")
        except FetchError:
            unchecked.append((platform, url, "could not be reached"))
            continue
        status = response.status_code
        final = (getattr(response, "url", "") or "").lower()
        if status in PROFILE_GONE_STATUS:
            # HEAD may say alive on its own; it may not say dead on its own.
            # `confirm_dead` is the marketplace's rule for this and every other
            # link check already uses it - this one did not, so a profile that
            # 404s to an unauthenticated HEAD while serving 200 to a reader was
            # reported as leading nowhere, advising the owner to delete a
            # working `sameAs` entry.
            status = confirm_dead(fetcher, url, status)
        if status in PROFILE_GONE_STATUS:
            gone.append((platform, url, status))
        elif status == 200 and any(
                wall in final for wall in
                ("/login", "/signin", "/uas/login", "/authwall", "/accounts/login",
                 "/checkpoint", "/challenge")):
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
        # "1 of the profile link the brand publishes lead nowhere" - both
        # halves wrong, and for one reason: the count of *dead* links was
        # deciding the number of the noun and the verb, when the noun is the
        # whole set the brand publishes and is always plural, and the verb
        # agrees with the count.
        title="{} of the profile links the brand publishes {} nowhere".format(
            len(gone), "leads" if len(gone) == 1 else "lead"),
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
    """The item that IS this site, the websites the rest declare, and whether asked.

    One request for every match at once, asking each item which website it
    says is its own, and comparing that to the host being audited. Wikidata's
    search API does not return the property, so without this the brand's own
    entry is indistinguishable from the entities it collides with - and it was
    counted as one of them.

    Returns `(entity_id or None, answered, {entity_id: declared website host})`.

    The second value exists because a request that never came back and a
    request that came back naming nobody both produced the same `None`, and
    the report acts on them differently: the recommendation reading this signal
    says "Create a Wikidata item", which is a thing to say when the lookup
    found none and never on the strength of a lookup that failed.

    The third is every host the matched items declare, kept rather than thrown
    away the moment one of them matched. An item of the same name that names a
    *different* website is the strongest evidence of a collision this check can
    obtain - stronger than a shared label, which could be a coincidence of
    spelling - and it was being discarded, so the one site in a batch of runs
    whose name genuinely belongs to a larger organisation was told there was no
    collision to resolve.
    """
    ids = [m.get("id") for m in matches if m.get("id")][:10]
    if not ids or fetcher is None:
        return (None, False, {})
    response = fetcher.try_get(WIKIDATA_ENTITIES_API.format("|".join(ids)))
    if response is None:
        return (None, False, {})
    try:
        entities = (response.json() or {}).get("entities") or {}
    except (ValueError, json.JSONDecodeError):
        return (None, False, {})

    host = strip_www(urlparse(snapshot.get("origin") or "").netloc.lower())
    if not host:
        return (None, False, {})
    own, sites = None, {}
    for entity_id in ids:
        claims = ((entities.get(entity_id) or {}).get("claims") or {})
        for claim in claims.get(WIKIDATA_OFFICIAL_SITE) or []:
            value = (((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
            if not isinstance(value, str):
                continue
            declared = strip_www(urlparse(value).netloc.lower())
            if not declared:
                continue
            sites.setdefault(entity_id, declared)
            if own is None and _same_organisation_host(declared, host):
                own = entity_id
    return (own, True, sites)


# Registry labels that hand out the label to their left rather than keeping it:
# the second level of `co.uk`, `com.au`, `ac.jp`, `gov.br`. Without them the
# registered domain of a site under one of these reads as the registry's own,
# and two unrelated organisations on the same country registry compare as one.
_MULTI_LABEL_REGISTRY_SUFFIXES = frozenset({
    "co", "com", "net", "org", "gov", "edu", "ac", "sch", "mil", "int",
    "ne", "or", "go", "lg", "gob", "gouv", "govt", "nic", "asn", "gen",
    "info", "biz", "web", "in", "id",
})

# The longest tail that can come off a name and still be a domain suffix rather
# than a dropped word. See `_shorter_search_name`.
_TLD_TAIL_MAX = 4


def _registrable_domain(host):
    """`(the domain somebody registered, the label they chose inside it)`.

    `docs.acme-tools.test` -> `("acme-tools.test", "acme-tools")`, and a name
    sitting on a shared platform - `acme.somebodyelse.test` -> the platform's
    own `("somebodyelse.test", "somebodyelse")`, because the label to the left
    of a registered domain was chosen by whoever signed up, not by whoever owns
    the address.
    """
    labels = [label for label in (host or "").split(".") if label]
    if len(labels) < 2:
        return ("", "")
    keep = 3 if (len(labels) > 2 and len(labels[-1]) <= 3
                 and labels[-2] in _MULTI_LABEL_REGISTRY_SUFFIXES) else 2
    return (".".join(labels[-keep:]), labels[-keep])


# Labels that name a section of one organisation's site rather than a tenant
# on somebody else's. Anything two or three characters long joins them without
# being listed: `www`, `m`, `en`, `de`, `uk`, `us`, `api`, `cdn` - the language
# codes, the country codes and the short service names are all in that range,
# and no brand's own label is that short in a way that matters here.
_ORDINARY_SUBDOMAIN_LABELS = frozenset({
    "www2", "www3", "docs", "shop", "store", "blog", "news", "help", "support",
    "mobile", "media", "assets", "static", "images", "email", "mail", "login",
    "secure", "account", "accounts", "portal", "beta", "staging", "preview",
    "search", "events", "careers", "jobs", "press", "learn", "community",
    "forum", "wiki", "status", "developer", "developers", "partners", "info",
})


def _is_an_ordinary_subdomain(label):
    return bool(label) and (len(label) <= 3 or label in _ORDINARY_SUBDOMAIN_LABELS)


def _tenant_label(host, registered_domain):
    """The label immediately left of the registered domain, or `""`.

    On a shared platform this is the label whoever signed up chose, which is
    the only part of the address that says which tenant this is.
    """
    host = (host or "").rstrip(".")
    if not registered_domain or not host.endswith(registered_domain):
        return ""
    prefix = host[: len(host) - len(registered_domain)].rstrip(".")
    return prefix.rsplit(".", 1)[-1] if prefix else ""


def _same_organisation_host(declared, audited):
    """Is the site Wikidata names the site being audited?

    Exact hostname is too strict for any brand with a national site. A Spanish
    manufacturer's own Wikidata item gives its website on the `.com` domain
    while the site under audit is the `.es` one; the item was therefore not
    recognised as the brand's own, was counted among "8 other entities share
    this name", and the report told a company with an article in eight
    Wikipedias to create an item "if one does not exist".

    The version this replaces answered by comparing the leftmost label that is
    not a `www.`-style prefix, and threw away everything to the right of it -
    including which domain was registered and by whom. A subdomain is handed
    out by the platform that owns the address, so an auto-generated
    documentation site for an unofficial third-party client library, hosted at
    `<the library's name>.<a docs platform>.test`, compared equal to the item
    for the service the library talks to, whose declared website is
    `<the same name>.test`. The report then told a small project that a much
    larger organisation's encyclopedia item was its own, and the fix step named
    that item's Q-id as "your Wikidata item".

    So the question is asked of the registered domain, in the two ways one
    organisation's addresses actually differ:

      the same registered domain     `docs.`, `uk.`, `shop.`, `www.` in front
      the same registered label on   a national edition: `.es` beside `.com`
      a different registry

    A shared subdomain label under two different registered domains is neither,
    and answering "same" to it is what produced the wrong entity above.

    One registered domain is also not always one organisation: on a hosting
    platform every tenant sits under the platform's own domain, so
    `<one brand>.<a pages host>.test` and `<another brand>.<the same pages
    host>.test` share a registered domain and share nothing else. Where both
    addresses carry a tenant label of their own and the two differ, they are
    two tenants. `docs.`, `shop.`, `uk.` and `www.` are not tenant labels -
    they are sections of one site - so a pair that differs only by one of
    those still compares as the same organisation.
    """
    if not declared or not audited:
        return False
    if declared == audited:
        return True
    declared_domain, declared_label = _registrable_domain(declared)
    audited_domain, audited_label = _registrable_domain(audited)
    if not declared_domain or not audited_domain:
        return False
    if declared_domain == audited_domain:
        declared_tenant = _tenant_label(declared, declared_domain)
        audited_tenant = _tenant_label(audited, audited_domain)
        if (declared_tenant and audited_tenant
                and declared_tenant != audited_tenant
                and not _is_an_ordinary_subdomain(declared_tenant)
                and not _is_an_ordinary_subdomain(audited_tenant)):
            return False
        return True
    return declared_label == audited_label


def _audited_host_is_a_guest(audited, declared):
    """Could this site be the same organisation, hosted on somebody else's domain?

    The one shape in which the comparison above can still be wrong in the
    generous direction: a brand whose own site sits on a platform subdomain
    that spells its name, while its Wikidata item gives the address on its own
    registered domain. That is a real arrangement, and it is indistinguishable
    from a third party naming its project after somebody else - the address
    cannot say which. So it is not treated as the same organisation, and the
    finding that follows is stated at medium confidence rather than high.
    """
    own_domain, _own_label = _registrable_domain(audited)
    _declared_domain, declared_label = _registrable_domain(declared)
    if not own_domain or not declared_label:
        return False
    prefix = (audited or "")[:-len(own_domain)]
    return declared_label in [label for label in prefix.split(".") if label]


_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.I)

# The administrative class a place's legal name is prefixed with, which is not
# part of the name any register or encyclopedia files it under. A town's
# `og:site_name` was "Town of <place>, <state>", that string matched no
# Wikidata label at all, and the audit printed an instruction to the reader -
# "search the name by hand, without any leading article or legal suffix" -
# while holding an unspent second lookup and the rule needed to run it. The
# same name without the three-letter prefix returns the place's own item, with
# a one-line description, immediately.
#
# Only the administrative classes, and deliberately not the institutional ones.
# "University of <place>" and "Department of <subject>" ARE the name; stripping
# those would search for the place and count the place's item as a collision
# with the university on it.
_LEADING_ADMINISTRATIVE_CLASS_RE = re.compile(
    r"^(?:the\s+)?(?:town|city|village|borough|township|municipality|county|"
    r"parish|commune|ville|comune|ciudad|municipio|ayuntamiento|gemeente|"
    r"kommune|kommun)\s+(?:of|de|del|di|du|des|der|van|von)\s+", re.I)

# The same idea where the language writes no connecting word.
_LEADING_ADMINISTRATIVE_WORD_RE = re.compile(
    r"^(?:stadt|gemeinde|kommune|kaupunki|stad)\s+", re.I)


def _shorter_search_name(brand, primary):
    """The shorter way the site writes its own name, or None if there is one only.

    Searching Wikidata for the long declared name found nothing on two real
    sites while the collision the check exists to report sat one form away:

      a restaurant group's declared name, three words long, returned 0 hits,
      while Wikidata holds three items under its one-word trading name: the
      chain itself, a 1980s film and a sound effect.

      a museum's declared name, with a leading "The", returned 0 hits, while
      two items in two different countries carry the name without it.

    A candidate has to be the same name shortened, not a different name: its
    comparison key must sit inside the declared name's key. That is what keeps
    a tagline out, and it is why the museum's four-letter domain token is
    rejected while the name with the leading article removed is accepted.
    """
    key = comparison_key(primary)
    if not key:
        return None
    candidates = [_LEADING_ARTICLE_RE.sub("", primary).strip()]
    candidates.append(_LEADING_ADMINISTRATIVE_CLASS_RE.sub("", primary).strip())
    candidates.append(_LEADING_ADMINISTRATIVE_WORD_RE.sub("", primary).strip())
    candidates += [str(v) for v in (brand.get("authoritative_variants") or [])]
    candidates += [str(v) for v in (brand.get("alternate_names") or [])]
    # The leading words of the declared name that spell the domain, so a
    # restaurant group's three-word declared name yields the one-word trading
    # name its domain is built from, even when the site never declares that
    # short form anywhere in its markup.
    token = comparison_key(brand.get("domain_token") or "")
    words = primary.split()
    for count in range(1, len(words)):
        if token and comparison_key(" ".join(words[:count])) == token:
            candidates.append(" ".join(words[:count]))
            break

    # A shortening drops words. Anything else is the same single token with
    # punctuation taken off, and searching that is how a cloud host whose name
    # ends in a dot and a two-letter suffix was told "8 other entities share
    # the name" - the eight being the insect order Diptera, the housefly, a
    # country album and a pop single. The suffix-stripped token is the domain,
    # not a name the brand goes by.
    #
    # Counting words was the whole of that rule, and a word count is a Latin
    # assumption. Japanese, Chinese and Thai put no spaces between words, so
    # every candidate on such a site has exactly one "word" and so does the
    # declared name - and the rule rejected all of them. A dental clinic's
    # declared name was its entire page title, three segments and two
    # separators long; the clinic's own name sits inside it and the site
    # publishes that name in its own markup, and this test threw it away before
    # the second lookup could ever be spent on it.
    #
    # So the rule now names what it was really rejecting: a tail too short to
    # be a dropped word is a domain suffix, whatever the script.
    primary_words = len(primary.split())
    best = None
    for value in candidates:
        value = re.sub(r"\s+", " ", value or "").strip()
        candidate_key = comparison_key(value)
        if len(value) < 3 or not candidate_key or candidate_key == key:
            continue
        if candidate_key not in key:
            continue
        if (len(value.split()) >= primary_words
                and len(key) - len(candidate_key) <= _TLD_TAIL_MAX):
            continue
        if best is None or len(value) < len(best):
            best = value
    return best


def _item_carries_one_of(item, names):
    """Is this Wikidata item's label, or one of its aliases, one of these names?

    `names_are_one_name` rather than a comparison of keys built here. This file
    had its own key set, folded through `name_forms` on the searched side only,
    and the marketplace already has one strict matcher written for exactly this
    question - the one that stopped a digital library being credited with the
    encyclopedia article about the printer it is named after. Two matchers for
    one question drift, and the looser of the two is always the one that
    produces the wrong claim.
    """
    labels = [item.get("label")] + list(item.get("aliases") or [])
    return any(names_are_one_name(name, label)
               for name in names if name
               for label in labels if label)


def _covering_matches(names, matches):
    """How many of these Wikidata items carry one of these names."""
    return sum(1 for m in matches if _item_carries_one_of(m, names))


def _wikidata_allows(fetcher, cache):
    """May this audit send its lookups to Wikidata's API, by Wikidata's robots.txt?

    Read through `third_party_allows`, the same reader the profile probes use,
    against the address every lookup here is sent to. A robots file that could
    not be read does not block, which is that reader's rule: an audit may not
    refuse on the strength of its own failure to check. That covers a reply
    with no body to read as well as no reply at all.
    """
    try:
        return third_party_allows(fetcher, WIKIDATA_API.format("robots"), cache)
    except (AttributeError, TypeError, ValueError):
        return True


def _wikidata_search(fetcher, name):
    """The raw search results for one name, or None when the API did not answer."""
    response = fetcher.try_get(WIKIDATA_API.format(quote(name)))
    if response is None:
        return None
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    return payload.get("search") or []


def _category_noun_step(english):
    """The fix step that tells an owner to write a category noun beside the name.

    Two wordings, because an example is only worth printing in a language the
    reader writes in. Neither wording contains the brand name: a step that
    reads as a sentence with the name already in it gets pasted, and the
    category in it was boilerplate.
    """
    if english:
        return ("Write a category noun next to the name every time it appears in prose, so "
                "the name never stands alone. Choose the word a stranger would use for what "
                "you do - \"the ferry operator\", \"the accountancy firm\", \"the county "
                "council\" are that kind of word - and use the same one every time.")
    return ("Write a category noun next to the name every time it appears in prose, so the "
            "name never stands alone. Use the ordinary word a stranger would use for what "
            "you do, in the language this site is written in, and use the same word every "
            "time.")


# The characters a page title is assembled from. Two of these in one string is
# not a name anybody registered anywhere: a dental clinic's declared name was
# its whole title, two separators and three segments long, and one lookup was
# spent on it. The full-width forms are here because the half-width ones are
# not what a Japanese or Chinese title is written with.
_TITLE_SEPARATORS = "|｜/／·・•:：–—【】"
_TITLE_SEPARATOR_RE = re.compile("[{}]".format(re.escape(_TITLE_SEPARATORS)))


def _why_no_second_lookup(brand_name, searched):
    """The sentence explaining what else was tried, for a search that found nothing.

    The budget is two searches. Saying "we found nothing" while one of them is
    unspent, and printing the derivation the reader should perform, is a
    dodge rather than a limit. Either the second was spent - and the reader
    is told on what - or it was not, and the reader is told why not, in terms
    of the string rather than in terms of the reader's homework.
    """
    if len(searched) > 1:
        return (' A shorter form the site itself publishes, "{}", was derived and searched '
                "too, and matched nothing either.".format(searched[1]))
    segments = [s for s in _TITLE_SEPARATOR_RE.split(brand_name) if s.strip()]
    if len(segments) > 1:
        return (" The string is a page title rather than a name: it carries {} separated "
                "segments, and no Wikidata label is written that way. Nothing in the site's "
                "own markup names one of those segments as the organisation, so the second "
                "lookup was not spent guessing which one it is.".format(len(segments)))
    return (" No shorter form could be derived that the site also publishes for itself, so "
            "the second lookup was not spent on a name the site never uses.")


# What kind of thing a Wikidata item is, read from its one-line description.
#
# A university that already has its own Wikidata item was told at high
# severity that "5 other entities share the name" it goes by: a family name, a
# town in another country, an island and a Portuguese dance. None of those is
# a thing an assistant asked about a university answers about instead. A
# collision is two things of one kind - two organisations, two institutions,
# two brands - competing for one label.
#
# Read only once the site's own item has been identified, because until then
# nothing says what kind of thing the site is. An organisation word in the
# description wins over a place word, so "school district in <state>" and
# "football club in <city>" are still counted. A description that says
# neither, or no description at all, is counted: this reading may take an
# item out of the count only when the item says what it is.
_AN_ORGANISATION_KIND_RE = re.compile(
    r"\b(?:universit(?:y|ies)|college|school|institut(?:e|ion)|academy|company|corporation"
    r"|business|brand|organi[sz]ation|foundation|association|society|agency|bank|firm"
    r"|manufacturer|retailer|restaurant|hospital|museum|library|publisher|newspaper"
    r"|magazine|website|software|club|team|party|charity|enterprise|chain|store|shop"
    r"|studio|laboratory|centre|center|government|ministry|council|band|service)\b", re.I)
_ANOTHER_KIND_RE = re.compile(
    r"\b(?:family name|surname|given name|first name|disambiguation page|human settlement"
    r"|town|city|village|hamlet|municipality|commune|county|province|island|river|lake"
    r"|mountain|hill|bay|beach|genus|species|taxon|dance|genre|song|single|album|film"
    r"|novel|painting|asteroid|galaxy|word|chemical compound|protein|gene|language|dish)\b",
    re.I)


def _a_different_kind_of_thing(item):
    """Does this item's description say it is not an organisation of any kind?"""
    description = (item or {}).get("description") or ""
    if not description or _AN_ORGANISATION_KIND_RE.search(description):
        return False
    return bool(_ANOTHER_KIND_RE.search(description))


# Where the name searched for came from, in words a site owner recognises. The
# name is read from the site's own markup, and a university whose markup
# declares its nickname was told about entities sharing the nickname with
# nothing saying that the nickname, and not the full name, was what had been
# looked up.
_NAME_SOURCES_IN_WORDS = (
    ("jsonld", "the site's Organization markup"),
    ("og:site_name", "the site's og:site_name tag"),
    ("title", "the site's page title"),
    ("llms", "the site's llms.txt file"),
)


def _where_the_name_came_from(brand, name):
    """The place on the site this name was read from, or ''."""
    sources = list((brand.get("declared_by") or {}).get(name) or [])
    if not sources and name == brand.get("name"):
        sources = [brand.get("source") or ""]
    for prefix, words in _NAME_SOURCES_IN_WORDS:
        if any(str(source).startswith(prefix) for source in sources):
            return words
    return ""


def _check_entity_ambiguity(result, snapshot, pages, brand, profiles, fetcher, allow_network,
                            time_budget=None):
    """Does anything else share this name, and does the site say which one it is?"""
    result.check("entity-ambiguity")

    # Emitted on every path out of this function, including the ones that never
    # reach Wikidata, because the report's recommendations section reads them
    # and a missing key there reads as "no item".
    #
    # A restaurant group was told to "create a Wikidata item" three sections
    # after this check printed the item's own Q-id and said to link it; a
    # museum was told the same while this check's own skip line said the site
    # already links Wikidata or Wikipedia. Three states, kept apart: True means
    # an item is known to exist, False means the lookup ran and found none, and
    # None means nothing was looked up and nothing should be claimed.
    linked_to_wikidata = any(k in ("Wikidata", "Wikipedia") for k in profiles)
    result.signal("wikidata_own_entity", None)
    result.signal("wikidata_item_exists", True if linked_to_wikidata else None)

    brand_name = (brand.get("name") or "").strip()
    if not brand_name or len(brand_name) < 3:
        result.skip("entity-ambiguity", "no usable brand name was detected")
        return

    query = '"{}"'.format(brand_name)
    result.signal("suggested_web_search", query)

    if not allow_network or fetcher is None:
        # See `no_network_reason`: a spent wall clock and the flag reach this skill
        # as the same argument, and only what is left of the budget separates them.
        result.skip("entity-ambiguity",
                    no_network_reason(
                        snapshot, time_budget,
                        flag_reason="external lookups were disabled for this run, so the "
                                    "number of entities sharing this name could not be "
                                    "counted",
                        exhausted_reason="the number of entities sharing this name was "
                                         "not counted"))
        return

    # Wikidata's own robots.txt decides whether its API may be asked, exactly
    # as each platform's decides whether a profile may be probed. Its file
    # carries `User-agent: *` and `Disallow: /w/`, and `/w/api.php` is the path
    # every lookup here goes to - so asking anyway, with the exception named in
    # the documentation, still broke the one rule this marketplace keeps about
    # other people's servers. Declined, with the reason, and never a finding:
    # nothing about the site was measured.
    if not _wikidata_allows(fetcher, {}):
        result.signal("wikidata_closed_by_robots", True)
        result.skip("entity-ambiguity",
                    "Wikidata's robots.txt closes its API to automated clients, so no lookup "
                    "was made and how many things share this name was not measured")
        return

    shorter = _shorter_search_name(brand, brand_name)
    searched = [brand_name] + ([shorter] if shorter else [])
    result.signal("wikidata_searched_names", searched)

    everything, answered = [], False
    seen_ids = set()
    for name in searched:
        # One extra search at most, and only when the two names really differ,
        # so the Wikidata half of this skill stays within three of the sixteen
        # requests the whole check is allowed.
        found = _wikidata_search(fetcher, name)
        if found is None:
            continue
        answered = True
        for item in found:
            if item.get("id") not in seen_ids:
                seen_ids.add(item.get("id"))
                everything.append(item)
    if not answered:
        result.skip("entity-ambiguity",
                    "the Wikidata search API could not be reached; this is an audit-environment "
                    "limitation, not a site defect")
        return

    # Zero results across every form searched is a fact about the string, not
    # about the world. Wikidata's search matches labels and aliases by prefix,
    # so a name that returns literally nothing - not one item, not even the
    # site's own - has almost always been mis-taken from the page rather than
    # being genuinely unheard of. The name reaching this check is read from the
    # site's own markup and can arrive with a leading article, a tagline half
    # or a legal suffix attached, and none of those appear in a Wikidata label.
    #
    # Reported as "no collision" it is wrong twice over: a mail server whose
    # name is also an ordinary technical term returns five entities under its
    # real name and zero under the form this audit was handed, and the same
    # zero left `wikidata_item_exists` False, which is what the report's
    # recommendation reads before telling the owner to create the item they
    # already have. Both signals stay unset here: nothing was measured.
    if not everything:
        result.signal("wikidata_search_returned_nothing", True)
        # The sentence used to end by telling the reader to "search the name by
        # hand, without any leading article or legal suffix" - an instruction
        # this check can carry out, holding a second lookup it had not spent.
        # That is a dodge rather than a limit, and it happened on two sites out
        # of five: a town whose declared name begins with its legal class
        # returned nothing, and the same name without those three words
        # returns the place's own item at once. The derivation now runs, and
        # what is left to say is what was tried and what could not be.
        result.skip(
            "entity-ambiguity",
            "Wikidata returned no entity at all for {} - not one item, and not even one "
            "naming this site as its own website - so how many things share this name was "
            "not measured. A name that matches no label anywhere is evidence about the name "
            "string this audit was given, which is read from the site's own markup, rather "
            "than about what Wikidata holds.{}".format(
                " or ".join('"{}"'.format(name) for name in searched),
                _why_no_second_lookup(brand_name, searched)))
        return

    # Wikidata search matches by prefix, so a government service brand's name
    # returns the items for that brand's own sign-in and identity services -
    # the same organisation, not a name collision. Only entities whose label is essentially the brand name count.
    # `[^a-z0-9]` is the empty string for a name written in Hangul, Devanagari,
    # Arabic, Greek or Cyrillic - and an empty key equals every other empty
    # key, so the filter that exists to keep only exact name matches kept
    # everything. A national museum in Seoul was told three organisations share
    # its name; the three were its own metro station, its own friends' society
    # and its own directorship, and the labels differ from the museum's name by
    # a suffix that the key had thrown away along with every other character.
    matches = [m for m in everything if _item_carries_one_of(m, searched)]

    # The site's own entity is not one of the others. Wikidata returned five
    # items for one project's name and the report said "5 other entities share
    # the name" - one of the five was the project itself, which is not a
    # collision but the very disambiguation the finding goes on to recommend
    # creating. So the brand is told to go and make a thing it already has,
    # and the count it is alarmed by is one too high.
    #
    # Searched over the *unfiltered* results, which is the whole point. The
    # exact-label filter above exists because Wikidata search matches by
    # prefix; but Wikidata's own convention for resolving the very ambiguity
    # this check reports is to give the item a longer, more specific label. A
    # university department's item is "Department of Geography, University of
    # Cambridge", and its P856 official website is that department's domain -
    # so it was thrown away before the own-entity test could see it, and the
    # report told the department to create an item that already exists and
    # already points at them. The filter that made the count honest was
    # hiding the answer.
    #
    # Name matches first. `_own_wikidata_entity` asks about ten items in one
    # request, and two searches can return twenty, so the ordering decides who
    # fits. The items that share the name are the ones the count is about.
    match_ids = {m.get("id") for m in matches}
    ordered = ([m for m in everything if m.get("id") in match_ids]
               + [m for m in everything if m.get("id") not in match_ids])
    own, own_lookup_answered, declared_sites = _own_wikidata_entity(
        ordered, snapshot, fetcher)
    own_label = ""
    # Three states. `True` means an item is known to exist, `False` that the
    # lookup ran and named none, and `None` that it did not run - a request
    # that failed leaves nothing measured, and the recommendation that reads
    # this signal tells the owner to create the item when it sees `False`.
    result.signal("wikidata_item_exists",
                  True if (own is not None or linked_to_wikidata)
                  else (False if own_lookup_answered else None))
    if not own_lookup_answered and not linked_to_wikidata:
        result.signal("wikidata_own_entity_lookup_failed", True)
    if own is not None:
        matches = [m for m in matches if m.get("id") != own]
        result.signal("wikidata_own_entity", own)
        own_label = next((m.get("label") for m in everything if m.get("id") == own), "")
        result.signal("wikidata_own_entity_label", own_label)
    # Once the site's own item is known, a same-named item of another kind is
    # not a collision. See `_a_different_kind_of_thing`.
    another_kind = []
    if own is not None:
        another_kind = [m for m in matches if _a_different_kind_of_thing(m)]
        matches = [m for m in matches if not _a_different_kind_of_thing(m)]
    result.signal("wikidata_matches_of_another_kind",
                  [{"id": m.get("id"), "label": m.get("label"),
                    "description": truncate(m.get("description", ""), 90)}
                   for m in another_kind[:5]])
    kind_note = ""
    if another_kind:
        kind_note = (" {} more {} the name but {} a different kind of thing from the "
                     "organisation this site is - {} - and {} not counted: an assistant asked "
                     "about an institution does not answer about a surname, a place or a "
                     "genre.".format(
                         len(another_kind), "carries" if len(another_kind) == 1 else "carry",
                         "is" if len(another_kind) == 1 else "are",
                         "; ".join("{} ({})".format(m.get("label"), m.get("description"))
                                   for m in another_kind[:4]),
                         "is" if len(another_kind) == 1 else "are"))
    # Which string was looked up, and where on the site it was read.
    def _name_note(name):
        came_from = _where_the_name_came_from(brand, name)
        also = [s for s in searched if s != name]
        parts = (["the name read from {}".format(came_from)] if came_from else []) + (
            ['"{}" was searched as well'.format(also[0])] if also else [])
        return " ({})".format("; ".join(parts)) if parts else ""
    # Which of the two names the collision is actually on. On a restaurant group's site the
    # declared name is three words long and nothing shares it; three entities
    # share its one-word trading name. A title naming the long form would send
    # the owner looking for a collision that is not there.
    collided = brand_name
    if shorter and _covering_matches([shorter], matches) > _covering_matches(
            [brand_name], matches):
        collided = shorter
    result.signal("wikidata_collision_name", collided)
    result.signal("wikidata_match_count", len(matches))
    result.signal("wikidata_matches",
                  [{"id": m.get("id"), "label": m.get("label"),
                    "description": truncate(m.get("description", ""), 90)}
                   for m in matches[:5]])

    # The items of this name that name a website of their own, and it is not
    # this one. Two same-named items are a count; an item that publishes its
    # own address is a decision, and it is the one this check was made to
    # report. The site whose report was most wrong was an unofficial
    # third-party client library whose only same-named item was the large
    # service it talks to - the service's `P856` names the service's own domain
    # - and the check printed that item as this site's own and concluded there
    # was nothing to resolve. A site whose name belongs to a bigger
    # organisation is precisely the one an assistant will answer about wrongly.
    audited_host = strip_www(urlparse(snapshot.get("origin") or "").netloc.lower())
    confirmed = [m for m in matches if declared_sites.get(m.get("id"))]
    # ... unless this site could be that same organisation hosted on somebody
    # else's domain. The address cannot tell the two apart, so the finding is
    # still made and its confidence is the one that says so.
    borrowed = any(_audited_host_is_a_guest(audited_host, declared_sites[m["id"]])
                   for m in confirmed if m.get("id") in declared_sites)
    result.signal("wikidata_other_entity_sites",
                  [{"id": m.get("id"), "label": m.get("label"),
                    "official_website_host": declared_sites.get(m.get("id"))}
                   for m in confirmed[:5]])

    # Whether this site's own prose is English, which decides whether an
    # English worked example in the fix steps below is worth anything to the
    # person reading them.
    english = bool((language_of(snapshot) or {}).get("prose_checks_apply"))

    # Disambiguation the site already provides.
    alternate_names = []
    has_description = False
    for page in pages:
        for node in page.get("jsonld") or []:
            if node.get("alternateName"):
                alternate_names.append(str(node["alternateName"]))
            if node.get("description"):
                has_description = True
    disambiguated = linked_to_wikidata or (bool(alternate_names) and has_description
                                           and len(profiles) >= MIN_AUTHORITATIVE_PROFILES)

    # Two same-named items, or one that publishes an address of its own which
    # is not this site's. The threshold counts labels, and a label on its own
    # could be a coincidence of spelling - which is why two are wanted before
    # anything is reported. `P856` is not a coincidence: the item states which
    # website it is about, this audit knows which website it read, and where
    # those differ the collision is settled by evidence rather than by count.
    if len(matches) < WIKIDATA_AMBIGUITY_THRESHOLD and not confirmed:
        # The count is of *other* entities, and it was printed as though it
        # were the count of all of them: "Wikidata returns 0 entity matching
        # <the brand> ... the site's own item is Q1688472". Zero matched, and
        # here is the match. Six sites got that sentence, so the clause that
        # names the item now also says what the number counts.
        searched_note = ("" if len(searched) < 2
                         else ' (searched as "{}" and "{}")'.format(*searched[:2]))
        if own is not None:
            # Here `matches` counts entities that are *not* this site, so any
            # count above zero is a same-named thing the sentence has just
            # named. It went on to say "so there is no name collision to
            # resolve" anyway, and a reader who counted what the clause listed
            # found the conclusion contradicted by its own evidence. Below the
            # threshold this check reports on is not the same as nothing found,
            # so the words now follow the count.
            if matches:
                tail = ('{}. That is below the {} this check reports as a collision, so no '
                        "finding is raised; if one of them is confusable with you, an "
                        "`alternateName` and a Wikidata link are worth publishing "
                        "anyway".format(
                            ": " + "; ".join(
                                "{} ({})".format(
                                    m.get("label"), m.get("description") or "no description")
                                for m in matches[:3]),
                            plural(WIKIDATA_AMBIGUITY_THRESHOLD, "entity", "entities")))
            else:
                tail = ", so there is no name collision to resolve"
            reason = ('Wikidata holds this site\'s own item, {}{}, and {} with the name '
                      '"{}"{}{}'.format(
                          own, " ({})".format(own_label) if own_label else "",
                          plural(len(matches), "other entity", "other entities")
                          if matches else "no other entity",
                          collided, searched_note, tail))
        else:
            # No count of *others* is available here: the own-item lookup could
            # not say which of the results is this site, so `matches` is the
            # total and one thing carrying a name is not a collision.
            #
            # `collided`, not the declared name. Where the collision is on a
            # shorter form the site also publishes, naming the long declared
            # string sends the reader looking for a match on a string nothing
            # carries - and on a site whose declared name is its whole page
            # title, that string is the title.
            reason = ('Wikidata returns {} with the name "{}"{}, fewer than the {} this '
                      "check reports as a collision, so there is no obvious name collision "
                      "to resolve".format(
                          plural(len(matches), "entity", "entities"), collided,
                          searched_note,
                          plural(WIKIDATA_AMBIGUITY_THRESHOLD, "entity", "entities")))
        if kind_note:
            reason += "." + kind_note
        result.skip("entity-ambiguity", reason)
        return
    if disambiguated:
        result.skip("entity-ambiguity",
                    '{} share the name "{}", but the site already '
                    "disambiguates itself with a Wikidata/Wikipedia link or an alternateName "
                    "plus description and {} profiles".format(
                        plural(len(matches), "Wikidata entity", "Wikidata entities"),
                        collided, len(profiles)))
        return

    # What each item of this name says its own website is. This sentence is the
    # difference between "something else is spelt the way you are" and "the
    # entry under your name is about a different organisation, and here is its
    # address" - and the second is the one an owner can act on.
    confirmed_note = "".join(
        " {} ({}) gives its own official website as {}, which is not the site audited "
        "here.".format(m.get("id"), m.get("label"),
                       declared_sites.get(m.get("id")))
        for m in confirmed[:3])
    if confirmed and borrowed:
        # Stated, not hidden behind a confidence level a reader never sees.
        confirmed_note += (" This site's own address sits under a different registered "
                           "domain and carries that name as a subdomain label, so it could "
                           "be the same organisation hosted elsewhere; the addresses alone "
                           "cannot decide.")

    result.add(
        id_hint="brand-name-collides-with-other-entities",
        title=('Another entity on Wikidata carries the name "{}"'.format(collided)
               if len(matches) == 1 else
               '{} other entities share the name "{}"'.format(len(matches), collided)),
        severity="high" if len(matches) >= 4 else "medium",
        # An item that names a website of its own, on a domain that is not
        # this one, is read evidence rather than an inference from a spelling.
        confidence="high" if (confirmed and not borrowed) else "medium",
        # The title counted every match and the evidence listed four, with
        # nothing saying so, so a reader who counted the examples found the
        # headline overstated by however many ran past the cut.
        evidence='Wikidata returns {} item(s) for "{}"{}: {}{}.{}{} The site declares no Wikidata or '
                 "Wikipedia link{}.{}".format(
                     len(matches), collided, _name_note(collided),
                     "; ".join("{} ({})".format(m.get("label"), m.get("description") or "no description")
                               for m in matches[:4]),
                     "; and {} more".format(len(matches) - 4) if len(matches) > 4 else "",
                     confirmed_note, kind_note,
                     " and no alternateName" if not alternate_names else "",
                     " The site's own Wikidata item was found and is not counted among these."
                     if own is not None else
                     # The count is of entities that are not this brand, and
                     # the request that establishes which one is this brand did
                     # not come back. Printing the count without that caveat
                     # states as measured a subtraction that never happened.
                     " The lookup asking which of these items names this site as its own "
                     "website did not answer, so if one of them is this brand's own item "
                     "the count above is one too high."
                     if not own_lookup_answered else ""),
        mechanism="D", root_cause="entity-ambiguity",
        summary="Publish the markers that tell a machine which of these entities you are.",
        how_to_fix=[
            ("Your Wikidata item already exists ({}). Link it from Organization `sameAs` - "
             "that link is what tells a machine which of these entities you are, and it is "
             "missing.".format(own) if own is not None else
             # The generic step assumes the reader might already have an item
             # and has not looked. Where an item of this name exists and names
             # somebody else's website, they have looked, and the useful
             # instruction is the one that says so - and says what to put in
             # the new item so a reader can tell the two apart.
             "Create a Wikidata item of your own: the item Wikidata holds under this name "
             "is about a different organisation, so there is nothing to link yet. Give it "
             "the same one-sentence description you use everywhere else, set its official "
             "website to this site, and link it back from Organization `sameAs`."
             if confirmed else
             "Create a Wikidata item for the organisation if one does not exist, with the same "
             "one-sentence description you use everywhere else, and link it from Organization "
             "`sameAs`."),
            "Add `alternateName` to Organization markup listing the other ways people write "
            "the name.",
            # This step used to hand the owner "<brand> the `<category>` company"
            # with their real name filled in and the category left as an angle
            # bracket, so the one thing they were told to publish was a
            # sentence with a hole in it. Substituting the brand into a worked
            # English example instead made it worse: a Japanese university was
            # handed its own name beside the words "the ferry operator", in a
            # language its readers do not use, describing something it is not,
            # and formatted to read as copy to paste.
            #
            # So the step never writes a specimen sentence with the brand in
            # it. It is an instruction about what to write, and the examples
            # are of the category word - the part the owner has to choose -
            # given only where the site's own prose is English, because an
            # English example is no help to anyone writing in another language.
            _category_noun_step(english),
            # "The field it works in", not "industry": a library, a charity and
            # a research group have the first and not the second.
            "Fill in the founding year, the location and the field it works in in "
            "Organization markup; those three facts are what separate same-named entities.",
        ],
        effort="medium", owner="marketing",
        rationale="When several things share a name, a system either picks one or "
                  "blends them. Without explicit markers the brand's facts get attributed to "
                  "whichever entity is better documented, which is usually not the smaller one.",
    )


def _org_identity(page, field, branches=False):
    """Values the page declares for the organisation itself, at any nesting depth.

    `telephone` sits on the Organization node; `postalCode` sits inside its
    PostalAddress. Both are read here so the caller only has to name the field.

    On a multi-location site the branch nodes are skipped: a restaurant's own
    telephone number is not the organisation declaring a second number for
    itself, and reading it as one produced "8 different values for telephone
    across 9 pages" about a chain that was describing nine restaurants.
    """
    found = []
    for node in page.get("jsonld") or []:
        types = node.get("@type")
        types = [types] if isinstance(types, str) else (types or [])
        names = {str(t).split("/")[-1].lower() for t in types}
        if not names & ORG_IDENTITY_TYPES:
            continue
        if branches and names & VISITABLE_JSONLD_TYPES:
            continue
        if node.get(field):
            found.append(node[field])
        for key in ("address", "location"):
            value = node.get(key)
            for entry in (value if isinstance(value, list) else [value]):
                if isinstance(entry, dict) and entry.get(field):
                    found.append(entry[field])
    return found


def _check_declared_identity(result, conflicts, pages, label, field, normalise,
                             branches=False):
    """Record a conflict when the site declares two different values for itself."""
    per_page = {}
    for page in pages:
        values = {normalise(v) for v in _org_identity(page, field, branches)}
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


def _postcode_is_printed(postcode, pages):
    """Does any crawled page print this postal code in its text?

    `postcode_hint` is one code per page, and a page can show several. A
    wallpaper shop's contact page lists its head office and, beneath it, a
    second office in another country; the extractor kept the second office's
    code, and the report said at high severity that the Organization's own
    code "does not match the code shown on the page" - a code printed on that
    same page two lines above the one it was compared with. A code the site
    prints is not contradicted by another code the site also prints, so the
    declared code is looked for in the text of every crawled page before a
    mismatch is called a contradiction.

    Spaces and hyphens inside the code are optional on both sides, so `SW1A
    1AA` and `SW1A1AA`, or `122 008` and `122008`, are one code. Letters and
    digits on either side are not allowed, so a code is never found inside a
    longer number.
    """
    compact = re.sub(r"[\s-]+", "", str(postcode or ""))
    if len(compact) < 3:
        return False
    pattern = re.compile(
        r"(?<![0-9A-Za-z])" + r"[\s-]?".join(re.escape(c) for c in compact) + r"(?![0-9A-Za-z])",
        re.I)
    for page in pages:
        for text in (page.get("body_text"), page.get("text")):
            if text and pattern.search(text):
                return True
    return False


def _is_a_placeholder_number(value):
    """Is this telephone value a template's filler rather than a number?

    Every digit the same - `00000000`, `1111111` - or the digits in counting
    order. No real telephone number is either, and a template that shipped
    with one has published it on every page.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 6:
        return False
    return len(set(digits)) == 1 or digits in "01234567890123456789" \
        or digits in "98765432109876543210"


def _placeholder_telephones(pages):
    """[(page, value)] for every JSON-LD telephone that is a placeholder.

    Every node, nested ones included, because the value that reached a wine
    shop's every page sat in `Organization.contactPoint.telephone`: "00000000",
    passed by this check as consistent - which it was, on every page alike.
    """
    found, seen = [], set()
    for page in pages:
        stack = list(page.get("jsonld") or [])
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            for key, value in node.items():
                if key == "telephone" and isinstance(value, (str, int)) \
                        and _is_a_placeholder_number(value) and page.get("url") not in seen:
                    seen.add(page.get("url"))
                    found.append((page, str(value)))
                elif isinstance(value, (dict, list)):
                    stack.append(value)
    return found


def _report_placeholder_telephones(result, placeholders):
    result.check("fact-consistency-across-pages")
    page, value = placeholders[0]
    result.add(
        id_hint="declared-telephone-is-a-placeholder",
        title="The markup declares a placeholder telephone number on {}".format(
            plural(len(placeholders), "page")),
        severity="medium", confidence="high",
        evidence='Example: {} declares `"telephone": "{}"` in its JSON-LD - not a number '
                 "anyone can call. Seen on {}.".format(page["url"], value,
                                                        plural(len(placeholders), "page")),
        mechanism="D", root_cause="nap-inconsistency",
        summary="Replace the placeholder with the real number, or remove the property.",
        how_to_fix=[
            "Find where the Organization block is written - a theme setting or the template - "
            "and put the number the site prints for reaching it in `telephone`.",
            "Where there is no public number, remove `telephone` rather than leaving a "
            "placeholder: an absent value is read as unknown, a placeholder as the answer.",
        ],
        effort="low", owner="developer",
        rationale="A consumer asked how to reach the company repeats the number the markup "
                  "declares. A row of zeros published as a telephone is a wrong answer with the "
                  "confidence structured data carries.",
        affected_pages=[p["url"] for p, _ in placeholders],
    )


def _check_fact_consistency(result, snapshot, pages):
    """Contact and boilerplate facts that contradict each other across the site.

    Scope note: JSON-LD name and price mismatches belong to structured-data-audit.
    This check owns telephone, postal address and the description boilerplate.

    On a site with branches, most of what this check compares is not the site
    talking about itself. A restaurant group declares one `LocalBusiness` per
    restaurant, each with its own address and telephone, and that is exactly
    right; read as facts about one organisation they look like a site
    contradicting itself dozens of times. This produced a high-severity finding
    ranked second in what to fix first, telling marketing to spend a day
    forcing every location onto "one canonical version" - which would strip the
    real addresses customers use to find their nearest branch.

    So on a multi-location site the branch nodes are left out of the comparison
    and only what the site declares about itself is compared.
    """
    result.check("fact-consistency-across-pages")
    conflicts = []
    branches = is_multi_location(snapshot)
    # A value that is not a number at all, before any two numbers are
    # compared. See `_placeholder_telephones`.
    placeholders = _placeholder_telephones(pages)
    if placeholders:
        _report_placeholder_telephones(result, placeholders)

    descriptions = {}
    for page in pages:
        for node in page.get("jsonld") or []:
            names = jsonld_type_names(node.get("@type"))
            # The same shared list `_org_identity` uses. A local literal of
            # `{"organization", "corporation"}` here disagreed with the list
            # sixty lines above, so on a site marked up as `ProfessionalService`
            # or `MedicalBusiness` the description check below found the
            # organisation node and reported on it while the telephone and
            # postcode checks found nothing at all. One file, two answers about
            # one page.
            if not names & (ORG_IDENTITY_TYPES | VISITABLE_JSONLD_TYPES):
                continue
            # One branch of many is not the site.
            if branches and names & VISITABLE_JSONLD_TYPES:
                continue
            description = str(node.get("description") or "").strip()
            if description:
                # Keyed on letters and digits, and bucketed by the page's own
                # language. Keyed on lowercased whitespace-collapsed text, one
                # sentence written with a curly apostrophe on one page and a
                # straight one on another counted as two boilerplates - a
                # `high` finding telling the owner to "agree one canonical
                # version" of a sentence they already have one of. Worse, a
                # bilingual site's English and German descriptions were read as
                # the same contradiction, and following the fix would delete
                # the translation.
                key = (primary_subtag(page.get("lang")), comparison_key(description))
                descriptions.setdefault(key, []).append(page["url"])

            declared_phone = re.sub(r"\D", "", str(node.get("telephone") or ""))
            if declared_phone and len(declared_phone) >= 7:
                # The same rule the core-facts check uses, from the shared
                # library rather than a second copy. A page carrying both an
                # `Organization.telephone` and a book barcode would otherwise
                # report "the telephone does not match the number shown on the
                # page" against an ISBN.
                visible = {re.sub(r"\D", "", p) for p in
                           ((page.get("contact_facts") or {}).get("phones") or [])
                           if not not_a_telephone_number(p, page.get("body_text") or "")}
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
                # A second office's code beside the first is not a
                # contradiction. See `_postcode_is_printed`.
                if (visible_postcode and declared_postcode
                        and visible_postcode != declared_postcode
                        and not _postcode_is_printed(address["postalCode"], pages)):
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
            re.sub(r"\D", "", str(value))) >= 7 else None,
        branches=branches)
    _check_declared_identity(
        result, conflicts, pages, "postal code", "postalCode",
        lambda value: re.sub(r"\s+", "", str(value)).lower() or None,
        branches=branches)

    # Within one language. Two languages saying the same thing differently is
    # a translation, not a contradiction.
    by_language = {}
    for (language, key), urls in descriptions.items():
        by_language.setdefault(language, {})[key] = urls
    clashing = {lang: keys for lang, keys in by_language.items() if len(keys) > 1}
    if clashing:
        language = sorted(clashing)[0]
        keys = clashing[language]
        pairs = sorted(keys.items(), key=lambda kv: kv[0])
        conflicts.append((pairs[0][1][0],
                          "{} different Organization descriptions are published across the "
                          "site{}; the boilerplate is not one sentence but several".format(
                              len(keys),
                              " in {}".format(language.upper()) if language else "")))

    result.signal("distinct_org_descriptions",
                  max([len(keys) for keys in by_language.values()] or [0]))

    if not conflicts:
        if placeholders:
            return
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
