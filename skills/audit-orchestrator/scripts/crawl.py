#!/usr/bin/env python3
"""Fetch a bounded, deterministic sample of a site and write snapshot.json.

One crawl feeds all six sub-skills. The budget is fixed (seed 42, 60 pages,
240 s wall clock) so the same site produces the same page set on every run,
which is what makes the findings reproducible.

Usage:
    python crawl.py https://example.com --out snapshot.json
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zlib
from collections import defaultdict, deque
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit_common import (  # noqa: E402
    DEEP_TYPES, MAX_DEPTH, MAX_PAGES, MAX_RESPONSE_BYTES, MAX_SITEMAP_SAMPLE,
    REFUSED_STATUS,
    REQUEST_DELAY,
    REQUEST_TIMEOUT,
    SEED, USER_AGENT, WALL_CLOCK_BUDGET, FetchError, Fetcher, detect_page_type,
    detect_site_language, eprint, is_forbidden_path, normalise_url, origin_of,
    response_text, same_site,
    site_label, strip_www, truncate, write_json,
)

from page_extract import extract_page  # noqa: E402
from robots_parser import (  # noqa: E402
    blocks_entire_site, is_disallowed, parse_robots, path_of,
)

SNAPSHOT_SCHEMA_VERSION = 1

# The first two bytes of every gzip stream.
GZIP_MAGIC = bytes([0x1F, 0x8B])

# Longer than this and a separator-free <title> is a sentence, not a name.
TITLE_AS_BRAND_MAX = 40

# Where a <title> splits into "Brand" and "the rest of the sentence".
#
# The first version required whitespace on both sides of the separator, and
# that is not how titles are punctuated. A real shop's homepage title reads
# "Zingerman's: Online Shopping for Food and Gifts" - colon tight against the
# word, space after it, which is ordinary English. The split found nothing, the
# whole 47-character title was rejected as too long to be a name, the domain
# won, and the brand became "Zingermans". Every generated fix in that report -
# the definition sentence, the Organization snippet's `name` field, the sample
# llms.txt - then told the owner to publish their own shop's name without its
# apostrophe, in a report whose whole subject is making the brand's identity
# unambiguous. Following the advice would have made the site worse.
#
# So a colon, pipe or bullet may sit tight against the word before it and must
# be followed by space. The dashes still need space on both sides: a tight
# hyphen belongs to the word ("e-commerce", "Jean-Paul"). The trailing-space
# requirement is what keeps a clock time like "9:00 to 5:00" in one piece.
TITLE_SEPARATOR = re.compile(r"\s*[|:·•]\s+|\s[-–—]\s")

# A meta refresh with a delay this short is a redirect, not a courtesy pause on a
# page someone is meant to read. Longer ones stay where they are.
META_REFRESH_MAX_DELAY = 5

def dedup_key(url):
    """Identity of a page for crawl purposes, ignoring the `www.` prefix.

    Without this the crawler fetches example.com/ and www.example.com/ as two
    pages and then reports them as duplicate titles. A genuine split between
    the two hostnames is still reported separately as host-inconsistency.
    """
    normalised = normalise_url(url)
    if not normalised:
        return None
    scheme, _, rest = normalised.partition("://")
    host, slash, path = rest.partition("/")
    return "{}://{}{}{}".format(scheme, strip_www(host.lower()), slash, path)


# Non-HTML endings we never queue: they cost a request and carry no prose.
SKIP_EXTENSIONS = re.compile(
    r"\.(jpe?g|png|gif|webp|avif|svg|ico|css|js|mjs|woff2?|ttf|eot|zip|gz|tar|"
    r"mp4|mp3|wav|mov|avi|dmg|exe|pkg|rss|atom|json|txt|csv|xlsx?|docx?|pptx?|pdf)(\?|$)",
    re.I,
)


def normalise_target(raw):
    """Accept `example.com`, `https://example.com/path`, or anything between."""
    raw = (raw or "").strip()
    if not raw:
        raise SystemExit("a target URL is required")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlparse(raw)
    if not parts.netloc:
        raise SystemExit("could not parse a hostname from {!r}".format(raw))
    origin = origin_of(raw)
    seed_url = normalise_url(raw) or origin + "/"
    return origin, seed_url


# --------------------------------------------------------------------------
# robots.txt and sitemaps
# --------------------------------------------------------------------------

def fetch_robots(fetcher, origin):
    url = origin.rstrip("/") + "/robots.txt"
    record = {"url": url, "fetched": False, "status": None, "raw": "",
              "groups": [], "sitemaps": [], "errors": [], "fetch_error": None}
    try:
        response = fetcher.get(url)
    except FetchError as exc:
        record["fetch_error"] = str(exc)
        return record
    record["fetched"] = True
    record["status"] = response.status_code
    if response.status_code != 200:
        return record
    text = response_text(response)
    # A robots.txt served as HTML is a soft-404 the site owner probably did not
    # intend; treating it as rules would invent blocks that do not exist.
    if "<html" in text[:600].lower():
        record["errors"].append("robots.txt returned HTML, not plain text; treated as absent")
        record["raw"] = truncate(text, 500)
        return record
    record["raw"] = text[:20000]
    parsed = parse_robots(text)
    record["groups"] = parsed["groups"]
    record["sitemaps"] = [normalise_url(s, origin) or s for s in parsed["sitemaps"]]
    record["errors"] = parsed["errors"]
    return record


def _tag(element):
    return element.tag.split("}")[-1].lower()


def fetch_sitemaps(fetcher, origin, robots_record, deadline):
    """Fetch sitemaps referenced in robots plus the conventional location.

    Follows one level of sitemap index, capped at three children, because the
    goal is a representative URL sample, not a full inventory.
    """
    queue = list(dict.fromkeys(robots_record.get("sitemaps") or []))
    default_url = origin.rstrip("/") + "/sitemap.xml"
    referenced_in_robots = bool(queue)
    if default_url not in queue:
        queue.append(default_url)

    results = []
    seen = set()
    index_children_fetched = 0
    while queue and len(results) < 5 and time.monotonic() < deadline:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        record = {"url": url, "status": None, "urls": [], "is_index": False,
                  "parse_error": None, "child_sitemaps": [],
                  "referenced_in_robots": url in (robots_record.get("sitemaps") or [])}
        try:
            response = fetcher.get(url)
        except FetchError as exc:
            record["parse_error"] = "fetch failed: {}".format(exc)
            results.append(record)
            continue
        record["status"] = response.status_code
        if response.status_code != 200:
            results.append(record)
            continue
        payload = response.content
        # A .xml.gz sitemap is not a broken sitemap. The sitemaps.org protocol
        # names gzip explicitly and large sites use it as a matter of course.
        # Without this, a national government's sitemap of 706 URLs was
        # reported as "does not parse", quoting the Python parser choking on
        # gzip magic bytes, and ranked second in what to fix first.
        if payload[:2] == GZIP_MAGIC or url.endswith(".gz"):
            try:
                payload = gzip.decompress(payload)
                record["gzipped"] = True
            except (OSError, EOFError, zlib.error) as exc:
                record["parse_error"] = "gzip could not be read: {}".format(exc)
                results.append(record)
                continue
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            record["parse_error"] = "XML parse error: {}".format(exc)
            results.append(record)
            continue

        if _tag(root) == "sitemapindex":
            record["is_index"] = True
            for child in root:
                loc = child.find("./{*}loc")
                if loc is not None and (loc.text or "").strip():
                    child_url = normalise_url(loc.text.strip(), origin)
                    if child_url:
                        record["child_sitemaps"].append(child_url)
            for child_url in record["child_sitemaps"][:3]:
                if index_children_fetched < 3:
                    queue.append(child_url)
                    index_children_fetched += 1
        else:
            for entry in root:
                loc = entry.find("./{*}loc")
                if loc is None or not (loc.text or "").strip():
                    continue
                lastmod = entry.find("./{*}lastmod")
                record["urls"].append({
                    "loc": loc.text.strip(),
                    "lastmod": (lastmod.text or "").strip() if lastmod is not None else "",
                })
                if len(record["urls"]) >= 5000:
                    break
        results.append(record)

    for record in results:
        record["url_count"] = len(record["urls"])
        record["lastmod_count"] = sum(1 for u in record["urls"] if u["lastmod"])
    return results, referenced_in_robots


# --------------------------------------------------------------------------
# Page selection
# --------------------------------------------------------------------------

def guess_type_from_url(url):
    """Cheap type guess used only to spread the sitemap sample across types."""
    return detect_page_type(url, {"text": "", "headings": {}, "jsonld_types": [], "title": ""})


def sample_sitemap_urls(sitemaps, origin, limit=MAX_SITEMAP_SAMPLE, seed=SEED):
    """Pick up to `limit` sitemap URLs spread across page types.

    Round-robin over types first, so a 4,000-URL blog sitemap cannot crowd out
    the one pricing page - coverage of *kinds* of page beats coverage of count.
    """
    import random

    pool = []
    seen = set()
    for record in sitemaps:
        for entry in record["urls"]:
            url = normalise_url(entry["loc"], origin)
            if not url or url in seen or not same_site(url, origin):
                continue
            if is_forbidden_path(url) or SKIP_EXTENSIONS.search(url):
                continue
            seen.add(url)
            pool.append(url)
    if not pool:
        return []

    buckets = defaultdict(list)
    for url in sorted(pool):
        buckets[guess_type_from_url(url)].append(url)

    rng = random.Random(seed)
    for urls in buckets.values():
        rng.shuffle(urls)

    picked = []
    order = sorted(buckets.keys())
    while len(picked) < limit and any(buckets[t] for t in order):
        for page_type in order:
            if not buckets[page_type]:
                continue
            picked.append(buckets[page_type].pop())
            if len(picked) >= limit:
                break
    return sorted(picked)


def crawlable(url, origin, robots, respect_robots):
    if not same_site(url, origin):
        return False, "off-site"
    if is_forbidden_path(url):
        return False, "state-changing or private path"
    if SKIP_EXTENSIONS.search(url):
        return False, "non-HTML asset"
    if respect_robots and robots.get("groups") and is_disallowed(robots, USER_AGENT, path_of(url)):
        return False, "disallowed by robots.txt"
    return True, ""


# --------------------------------------------------------------------------
# Crawl
# --------------------------------------------------------------------------

def fetch_page(fetcher, url, depth, source, origin):
    """Fetch one URL and build its snapshot record, or an error record."""
    try:
        response = fetcher.get(url)
    except FetchError as exc:
        return {
            "url": url, "final_url": url, "status": None, "error": str(exc),
            "depth": depth, "source": source, "redirect_chain": [],
            "page_type": guess_type_from_url(url), "links": {"internal": [], "external": []},
            "text": "", "text_len": 0, "html_len": 0,
        }

    chain = [r.url for r in response.history]
    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    headers = {k.lower(): v for k, v in response.headers.items()}

    # A redirect that leaves the origin leaves the audit. A standards body's
    # site keeps a `/go/<id>` route that answers 302 into a different
    # organisation's document tracker; following it filed seventeen of that
    # other organisation's pages as pages of the audited site - 28% of the
    # crawl budget - and then took the brand name from one of them. The report
    # opened "A machine can reach <the other organisation> and read it" under a
    # heading naming the audited site, and asked the audited site to publish a
    # sentence defining a company it has nothing to do with.
    #
    # The redirect itself is worth recording: an internal link that leaves the
    # site is a fact about this site. Its destination is not.
    if not same_site(response.url, origin):
        return {
            "url": url, "final_url": response.url, "status": response.status_code,
            "depth": depth, "source": source, "redirect_chain": chain,
            "skipped": "redirects off this origin",
            "offsite_redirect": True,
            "page_type": "other", "links": {"internal": [], "external": []},
            "text": "", "text_len": 0, "html_len": 0, "headers": headers,
        }

    if content_type and "html" not in content_type and "xml" not in content_type:
        return {
            "url": url, "final_url": response.url, "status": response.status_code,
            "depth": depth, "source": source, "redirect_chain": chain,
            "content_type": content_type, "skipped": "non-HTML content type",
            "page_type": "other", "links": {"internal": [], "external": []},
            "text": "", "text_len": 0, "html_len": len(response.content or b""),
            "truncated": bool(getattr(response, "truncated", False)),
            "headers": headers,
        }

    html = response_text(response)
    record = extract_page(
        url=url, final_url=response.url, status=response.status_code, headers=headers,
        html=html, redirect_chain=chain, elapsed_ms=getattr(response, "elapsed_ms", 0),
        depth=depth, source=source, origin=origin,
    )
    # A page we stopped reading at the size cap is a limit of ours. Recorded
    # here so the crawl notes can say so, rather than letting a half-read
    # document look like a site that omitted the second half.
    record["truncated"] = bool(getattr(response, "truncated", False))
    return record


# Words a title carries around the brand rather than as part of it.
_NOT_A_BRAND = frozenset({"our", "the", "my", "your", "this", "welcome", "home"})

TITLE_BOILERPLATE_LEAD = ("welcome to ", "the official ", "official ")
TITLE_BOILERPLATE_TAIL = (
    " home page", " homepage", " home", " official site", " official website",
    " website", " web site", " site", " online", " uk", " usa",
)


def _is_title_boilerplate(part):
    """True for a title half that is only scaffolding: "Home", "Official Site"."""
    value = (part or "").strip().lower().strip(".")
    if not value:
        return True
    if value in _NOT_A_BRAND:
        return True
    for tail in TITLE_BOILERPLATE_TAIL:
        if value == tail.strip():
            return True
    return value in ("home page", "homepage", "official site", "official website",
                     "welcome", "start", "start page", "index", "main page")


def _strip_title_boilerplate(title):
    """"SQLite Home Page" -> "SQLite". Returns "" if nothing is left."""
    value = (title or "").strip()
    lowered = value.lower()
    for lead in TITLE_BOILERPLATE_LEAD:
        if lowered.startswith(lead):
            value = value[len(lead):].strip()
            lowered = value.lower()
            break
    changed = True
    while changed:
        changed = False
        for tail in TITLE_BOILERPLATE_TAIL:
            if lowered.endswith(tail) and len(value) > len(tail) + 1:
                value = value[: -len(tail)].strip(" -–—:|,")
                lowered = value.lower()
                changed = True
                break
    # Refuse to strip a name down to nothing meaningful. "SQLite Home Page"
    # losing "Home Page" is the point; "Our Site" losing "Site" and becoming
    # "Our" is the same rule eating the brand.
    if len(value) < 4 or value.lower() in _NOT_A_BRAND:
        return title.strip()
    return value


def _site_spelling(token, home):
    """The site's own spelling of a name the domain can only approximate.

    A hostname cannot carry an apostrophe, an ampersand or a space, so the
    domain token is a flattened version of a name the site writes properly on
    its own homepage: `zingermans` for "Zingerman's", `benjerry` for "Ben &
    Jerry's". Falling back to the domain is reasonable; publishing the
    flattened spelling back to the owner as the name they should put in their
    structured data is not, because the report's own subject is making the
    brand's identity unambiguous.

    So when the domain is all we have, the homepage is checked for a run of up
    to four words that flattens to the same letters, and the site's spelling
    wins. Four words is enough for "Ben & Jerry's" and short enough that a
    coincidental match across a whole sentence is not a risk; the four-letter
    floor keeps short domains from matching anything.
    """
    key = re.sub(r"[^a-z0-9]", "", (token or "").lower())
    if len(key) < 4 or not home:
        return None
    for text in (home.get("title") or "", home.get("h1") or ""):
        words = str(text).split()
        for start in range(len(words)):
            for end in range(start + 1, min(start + 4, len(words)) + 1):
                phrase = " ".join(words[start:end])
                if re.sub(r"[^a-z0-9]", "", phrase.lower()) == key:
                    trimmed = phrase.strip(" ,.:;|-")
                    if trimmed and trimmed.lower() != token.lower():
                        return trimmed
    return None


def detect_brand(pages, origin):
    """Work out the brand name and, separately, how the site declares it.

    Two lists come out of this, and keeping them apart matters:

    `authoritative_variants` are names the site *asserts* are its identity -
    Organization `name` and `alternateName`, and og:site_name. Disagreement
    between these is a real inconsistency worth reporting.

    `fallback_candidates` are names guessed from the <title> or the domain when
    nothing authoritative exists. A title is usually "Brand | Tagline", so
    guesses drawn from it are frequently taglines, and comparing them would
    manufacture a naming-inconsistency finding on almost every site.
    """
    home = next((p for p in pages if p.get("page_type") == "home" and p.get("status") == 200), None)
    authoritative = []
    fallback = []
    alternates = set()

    def clean(value):
        value = re.sub(r"\s+", " ", str(value or "")).strip().strip("|-–—·•").strip()
        return value if 1 < len(value) <= 80 else ""

    home_url = (home or {}).get("url")
    declared_on = {}

    for page in pages:
        # Where a declaration was found decides how much it is worth. One
        # national charity declared no identity at all on its homepage, its
        # about page or its contact page, and declared an Organization `name`
        # on exactly one of sixty crawled pages: a store listing for a
        # three-ring binder, naming the training subsidiary that runs the
        # shop. That name became the brand for the whole report, drove two of
        # the three "start here" items, and headed the generated llms.txt - so
        # the file telling assistants what the organisation is would have
        # named a merchandise subsidiary. A declaration on a deep page is
        # still evidence; it is just worth less than the homepage's own title.
        on_home = page.get("url") == home_url and home_url is not None
        suffix = "" if on_home else "-off-home"
        for node in page.get("jsonld") or []:
            types = node.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if not any(str(t).lower() in ("organization", "localbusiness", "corporation", "store",
                                          "restaurant", "ngo", "educationalorganization",
                                          "professionalservice", "onlinestore")
                       for t in types):
                continue
            name = clean(node.get("name"))
            if name:
                authoritative.append(
                    {"name": name, "source": "jsonld:Organization" + suffix})
                declared_on.setdefault(name, set()).add(page.get("url"))
            alternate = node.get("alternateName")
            for value in ([alternate] if isinstance(alternate, str) else (alternate or [])):
                cleaned = clean(value)
                if cleaned:
                    alternates.add(cleaned)
        site_name = clean(page.get("og", {}).get("og:site_name"))
        if site_name:
            # Plenty of sites set og:site_name to the whole page title,
            # separator and all: "Die Bundesregierung informiert | Startseite"
            # became the brand, and then appeared inside generated fix text as
            # if it were a company name.
            pieces = [clean(x) for x in TITLE_SEPARATOR.split(site_name)]
            pieces = [x for x in pieces if x]
            if len(pieces) > 1:
                site_name = min(pieces, key=len)
            authoritative.append({"name": site_name, "source": "og:site_name" + suffix})
            declared_on.setdefault(site_name, set()).add(page.get("url"))

    if home:
        title = home.get("title") or ""
        parts = [clean(p) for p in TITLE_SEPARATOR.split(title)]
        parts = [p for p in parts if p]
        if len(parts) > 1:
            # The brand is conventionally the shorter end of "Brand | Tagline",
            # but "Brand | Home" and "Brand | Official Site" are also ordinary
            # titles, and there the shorter end is the boilerplate. Drop the
            # ends that are nothing but boilerplate first, and only fall back
            # to length when that leaves nothing to choose between.
            ends = [parts[0], parts[-1]]
            named = [p for p in ends if not _is_title_boilerplate(p)]
            ordered = sorted(named or ends, key=len)
            for index, value in enumerate(ordered):
                fallback.append({"name": value, "source": "title-part-{}".format(index)})
        elif parts:
            # A title with no separator is often the brand plus boilerplate:
            # "SQLite Home Page", "Welcome to Acme", "Acme - Official Site".
            # Taking it whole made the brand name "SQLite Home Page", which then
            # broke the definition check downstream: the homepage says "SQLite
            # is a C-language library that implements ...", and the check was
            # looking for a sentence starting "SQLite Home Page is". One bad
            # name produced a false "no page states what the brand is" on a
            # site whose first sentence states exactly that.
            stripped = _strip_title_boilerplate(parts[0])
            if stripped and stripped != parts[0]:
                fallback.append({"name": stripped, "source": "title-part-0"})
            # A long title with no separator is a sentence, not a name. One
            # site's became "The GNU Operating System and the Free Software
            # Movement", and the definition check then hunted the homepage for
            # a sentence beginning with all 54 characters of it - while "GNU is
            # an operating system that is free software" sat in the second
            # line. The domain is a better guess than a headline.
            if len(parts[0]) <= TITLE_AS_BRAND_MAX:
                fallback.append({"name": parts[0], "source": "title"})
            else:
                fallback.append({"name": parts[0], "source": "title-long"})

    host = urlparse(origin).netloc.lower()
    host_brand = re.sub(r"^www\.", "", host).split(".")[0].replace("-", " ").strip()
    if host_brand and not re.match(r"^\d+$", host_brand):
        fallback.append({"name": host_brand.title(), "source": "domain"})

    priority = {"jsonld:Organization": 0, "og:site_name": 1,
                "title-part-0": 2, "title-part-1": 3, "title": 4,
                # A declaration found on one deep page and nowhere near the
                # homepage ranks below the homepage's own title. It is still
                # evidence, and it still beats the domain.
                "jsonld:Organization-off-home": 4.4, "og:site_name-off-home": 4.6,
                "domain": 5,
                # Below the domain: a headline is not a brand name.
                "title-long": 6}
    ranked = sorted(authoritative + fallback,
                    key=lambda c: (priority.get(c["source"], 9), c["name"].lower()))
    chosen = ranked[0] if ranked else {"name": host_brand.title() or origin, "source": "domain"}

    # The domain is a last resort, and a last resort should still spell the
    # name the way the site does.
    if chosen["source"] == "domain":
        spelled = _site_spelling(host_brand, home)
        if spelled:
            chosen = {"name": spelled, "source": "domain-as-the-site-writes-it"}

    return {
        "name": chosen["name"],
        "source": chosen["source"],
        "authoritative_variants": sorted({c["name"] for c in authoritative}),
        "authoritative_sources": sorted({c["source"] for c in authoritative}),
        "alternate_names": sorted(alternates),
        "fallback_candidates": sorted({c["name"] for c in fallback}),
        # Which pages asserted each name. Without this a naming-inconsistency
        # finding lists no pages at all, and a finding with no pages is scored
        # as affecting the whole site - so a disagreement visible on one page
        # out of sixty outscored a critical crawler block on the same report.
        "declared_on": {name: sorted(urls) for name, urls in sorted(declared_on.items())},
        "pages_seen": len(pages),
        "host": host,
        "domain_token": host_brand,
    }


def _resolve_scheme(fetcher, origin, seed_url, target):
    """Fall back to http when a bare hostname was assumed to be https.

    `example.com` becomes `https://example.com`, which is the right default and
    is what the README documents. A site still served only over http then
    returned nothing at all - no pages, no findings, no explanation - when the
    useful answer was already in our vocabulary: `insecure-transport`. So the
    fallback runs only when the scheme was ours to assume, and only when https
    fails to connect rather than answering with an error.
    """
    if "://" in (target or "") or not origin.startswith("https://"):
        return origin, seed_url, False
    if fetcher.try_get(origin.rstrip("/") + "/", method="HEAD") is not None:
        return origin, seed_url, False
    downgraded = origin.replace("https://", "http://", 1)
    if fetcher.try_get(downgraded.rstrip("/") + "/", method="HEAD") is None:
        return origin, seed_url, False
    return downgraded, seed_url.replace("https://", "http://", 1), True


def _follow_meta_refresh(fetcher, record, depth, source, origin, notes):
    """Audit the page a meta refresh points at, not the stub that points there.

    A homepage answering with 216 bytes and `<meta http-equiv="refresh">` is a
    real and common pattern - language selection, legacy URLs, static hosts
    without redirect rules. Auditing the stub produced four confident findings
    about a page nobody has ever seen: no heading, no navigation, no call to
    action, no facts. All true of the stub. None true of the site.

    Followed only when the site is telling every visitor to go immediately
    (a short delay) and the destination is its own. The stub is kept in the
    record as `meta_refresh_from`, because a consumer that does not follow it
    sees what we first saw, and crawl-access-audit reports that.
    """
    refresh = (record.get("meta_refresh") or {}) if record.get("status") == 200 else {}
    target = refresh.get("url")
    if not target or refresh.get("delay", 99) > META_REFRESH_MAX_DELAY:
        return record
    if not same_site(target, origin) or normalise_url(target) == normalise_url(record["url"]):
        return record

    followed = fetch_page(fetcher, target, depth, source, origin)
    if followed.get("status") != 200:
        return record
    followed["meta_refresh_from"] = {
        "url": record["url"],
        "delay": refresh.get("delay"),
        "html_len": record.get("html_len"),
    }
    followed["redirect_chain"] = list(record.get("redirect_chain") or []) + [record["url"]]
    notes.append(
        "{} answers with a meta-refresh redirect to {}; the destination was audited "
        "instead of the stub".format(record["url"], target))
    return followed


def crawl(target, out_path, max_pages=MAX_PAGES, budget_s=WALL_CLOCK_BUDGET,
          respect_robots=True, delay=REQUEST_DELAY, render="auto"):
    origin, seed_url = normalise_target(target)
    started = time.monotonic()
    deadline = started + budget_s
    fetcher = Fetcher(delay=delay, timeout=REQUEST_TIMEOUT, deadline=deadline)

    notes = []
    origin, seed_url, downgraded = _resolve_scheme(fetcher, origin, seed_url, target)
    if downgraded:
        notes.append(
            "https did not answer, so the site was audited over http. That is itself a "
            "finding and is reported as one; the alternative was to return nothing."
        )
    robots = fetch_robots(fetcher, origin)
    sitemaps, sitemap_in_robots = fetch_sitemaps(fetcher, origin, robots, deadline)

    llms_txt = {"url": origin.rstrip("/") + "/llms.txt", "present": False, "status": None}
    probe = fetcher.try_get(llms_txt["url"], allow_redirects=False)
    if probe is not None:
        llms_txt["status"] = probe.status_code
        llms_txt["present"] = probe.status_code == 200 and "<html" not in response_text(probe)[:400].lower()

    # If our own polite user agent is shut out of the homepage, stop. Auditing
    # a site that has asked us not to read it would break the read-only,
    # robots-respecting contract this marketplace advertises.
    home_url = origin.rstrip("/") + "/"
    audit_blocked = bool(
        respect_robots and robots.get("groups")
        and is_disallowed(robots, USER_AGENT, "/")
    )

    pages = []
    queue = deque()
    queued = set()
    fetched_final = set()
    skipped = []

    if audit_blocked:
        notes.append(
            "robots.txt disallows this auditor at /, so no pages were fetched. "
            "The access findings below are derived from robots.txt alone."
        )
    else:
        for url in (home_url, seed_url):
            url = normalise_url(url)
            if url and dedup_key(url) not in queued:
                queue.append((url, 0, "homepage" if url == home_url else "seed"))
                queued.add(dedup_key(url))

        sitemap_sample = sample_sitemap_urls(sitemaps, origin)
        for url in sitemap_sample:
            ok, reason = crawlable(url, origin, robots, respect_robots)
            if not ok:
                skipped.append({"url": url, "reason": reason})
                continue
            if dedup_key(url) not in queued:
                queue.append((url, 1, "sitemap"))
                queued.add(dedup_key(url))

        budget_exhausted = False
        while queue and len(pages) < max_pages:
            if time.monotonic() >= deadline:
                budget_exhausted = True
                notes.append("wall-clock budget of {:.0f}s reached; crawl stopped cleanly".format(budget_s))
                break
            url, depth, source = queue.popleft()
            record = fetch_page(fetcher, url, depth, source, origin)
            record = _follow_meta_refresh(fetcher, record, depth, source, origin, notes)
            # Two queued URLs that redirect to the same destination are one
            # page; keeping both would double-count every finding on it.
            landed = normalise_url(record.get("final_url") or url) or url
            if landed != url and landed in fetched_final:
                skipped.append({"url": url, "reason": "redirects to an already-crawled page"})
                continue
            fetched_final.add(landed)
            # Whatever the root landed on is the homepage. A site whose `/`
            # redirects to `/en/`, or answers with a meta refresh to
            # `/home.html`, still has a homepage - but page-type detection only
            # ever calls the path `/` home, so the real one was classified
            # `other` and dropped from every content check. Common enough on
            # multilingual and statically hosted sites to matter.
            if source == "homepage" and record.get("status") == 200                     and record.get("page_type") != "home":
                record["page_type"] = "home"
                record["page_type_source"] = "reached from the site root"
            pages.append(record)

            if record.get("status") != 200 or depth >= MAX_DEPTH:
                continue
            # Breadth-first expansion from in-page links, sorted for determinism.
            for link in sorted(
                {l["url"] for l in (record.get("links", {}).get("internal") or [])}
            ):
                if len(queued) >= max_pages * 3:
                    break
                if dedup_key(link) in queued:
                    continue
                ok, reason = crawlable(link, origin, robots, respect_robots)
                if not ok:
                    if reason == "disallowed by robots.txt":
                        skipped.append({"url": link, "reason": reason})
                    continue
                queued.add(dedup_key(link))
                queue.append((link, depth + 1, "bfs"))

    home_record = next((p for p in pages if p["url"] == home_url), None)
    if home_record is not None and home_record.get("status") is None and not audit_blocked:
        # One retry for the homepage only: a single transient failure should not
        # turn into a "site is down" verdict.
        retry = fetch_page(fetcher, home_url, 0, "homepage-retry", origin)
        if retry.get("status") is not None:
            pages[pages.index(home_record)] = retry
            home_record = retry
        else:
            notes.append("homepage unreachable after two attempts: {}".format(retry.get("error")))

    _mark_edge_refusals(pages, notes)
    head_supported = _probe_head_support(fetcher, home_url, home_record, notes)

    oversized = [p["url"] for p in pages if p.get("truncated")]
    if oversized:
        notes.append(
            "{} page(s) exceeded the {:,}-byte read cap and were analysed up to that point: "
            "{}. Anything the audit says about what those pages lack is limited to the part "
            "it read".format(len(oversized), MAX_RESPONSE_BYTES, ", ".join(sorted(oversized)[:3])))

    render_mode = "static"
    if render:
        # The rendered pass shares the crawl's wall-clock budget rather than
        # adding to it. Before it ran by default that distinction did not
        # matter; now it does, because an unclamped 60s pass on top of a
        # 240s crawl would put a slow site past the five minutes the audit
        # promises. If the crawl has already spent the budget there is nothing
        # left to spend and the pass says so instead of running.
        render_mode = _render_pass(pages, notes, deadline,
                                   required=(render is True))

    pages.sort(key=lambda p: (0 if p.get("page_type") == "home" else 1, p["url"]))
    elapsed = time.monotonic() - started

    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "site": site_label(origin),
        "origin": origin,
        "seed_url": seed_url,
        "brand": detect_brand(pages, origin),
        # Detected once, here, so six skills cannot reach six different answers
        # about what language the site is in.
        "site_language": detect_site_language(pages),
        "crawl": {
            "pages_crawled": len(pages),
            "pages_ok": sum(1 for p in pages if p.get("status") == 200),
            "budget_exhausted": bool(queue) or time.monotonic() >= deadline,
            "wall_clock_budget_s": budget_s,
            "elapsed_s": round(elapsed, 2),
            "max_pages": max_pages,
            "seed": SEED,
            "user_agent": USER_AGENT,
            "render_mode": render_mode,
            # Whether this site answers HEAD the same way it answers GET.
            # Measured once here rather than rediscovered by three checks,
            # because it is a property of the site and this crawl is the only
            # place allowed to establish those. `None` means it could not be
            # determined and the checks say so rather than guessing.
            "head_supported": head_supported,
            "requests_made": fetcher.count,
            "respect_robots": respect_robots,
            "audit_blocked_by_robots": audit_blocked,
            "notes": notes,
            "skipped": skipped[:30],
        },
        "robots": robots,
        "sitemaps": sitemaps,
        "sitemap_referenced_in_robots": sitemap_in_robots,
        "llms_txt": llms_txt,
        "pages": pages,
    }
    write_json(out_path, snapshot)
    return snapshot


# A real error page is branded and long. An edge refusing a crawler answers in
# a few bytes, identically, many times over.
EDGE_REFUSAL_MAX_BYTES = 400
EDGE_REFUSAL_MIN_REPEATS = 3


def _mark_edge_refusals(pages, notes):
    """Non-200s that are one edge saying no, not many pages being deleted.

    A retailer's crawl returned thirteen URLs with a ten-byte body reading
    "Not found", served by a static edge - for every user agent, including a
    browser's. Its genuine missing-page response is a 300 KB branded page under
    a different status. The thirteen were live shop categories: one of them was
    named as the canonical by a 962 KB page in the same crawl.

    Reported as thirteen dead pages, they produced three high-severity findings
    and all three "start here" slots, and the recommended fix was to delete
    live categories from the sitemap.

    Identical tiny bodies repeated across several URLs are one refusal.
    """
    groups = {}
    for page in pages:
        status = page.get("status")
        if status is None or status == 200:
            continue
        length = page.get("html_len", 0)
        if length > EDGE_REFUSAL_MAX_BYTES:
            continue
        groups.setdefault((status, length), []).append(page)

    for (status, _length), group in sorted(groups.items()):
        if len(group) < EDGE_REFUSAL_MIN_REPEATS:
            continue
        for page in group:
            page["edge_refusal"] = True
        notes.append(
            "{} URLs answered HTTP {} with an identical body of under {} bytes. That is one "
            "edge refusing this crawler, not {} deleted pages, so they are reported as "
            "unverified rather than as broken links".format(
                len(group), status, EDGE_REFUSAL_MAX_BYTES, len(group)))


def _probe_head_support(fetcher, home_url, home_record, notes):
    """One HEAD to the homepage, to learn whether HEAD means anything here.

    Three checks - broken internal links, sitemap URLs and canonical targets -
    confirm a URL exists with HEAD and used to file any non-200 as dead.
    Measured across eight major retail and banking homepages, three answer 403
    to HEAD and 200 to GET, so on those sites every probed link was a false
    positive waiting to happen.

    Probing per link would double the request count. Probing once does not: a
    site either honours HEAD or it does not, and the homepage has already been
    fetched with GET so there is a baseline to compare against. One request.
    """
    if home_record is None or home_record.get("status") != 200:
        return None
    response = fetcher.try_get(home_url, method="HEAD")
    if response is None:
        return None
    if response.status_code in REFUSED_STATUS:
        notes.append(
            "the site answers HEAD requests with HTTP {} while answering GET with 200, so link "
            "targets the crawl did not reach could not be verified; they are reported as "
            "unchecked rather than as broken".format(response.status_code))
        return False
    return 200 <= response.status_code < 400


# The rendered pass. It runs by default when Playwright is importable and is
# skipped, with a note, when it is not.
#
# It used to be opt-in, which meant that on a judge's machine it would almost
# certainly never run and the JavaScript-shell finding would always be the
# inferred version rather than the measured one. It costs about 8 seconds
# against five pages, inside a run that has 140 seconds of its budget spare, so
# there was nothing left to protect by keeping it off. `--no-render` turns it
# off; `--render` demands it and says so in the notes when it cannot happen.
#
# Every number below exists to keep the pass inside the same five-minute budget
# the static audit promises: a browser waiting for an idle state a page never
# reaches will happily spend the whole of it.
RENDER_PAGES = 5             # a sample, not a second crawl
RENDER_BUDGET_SECONDS = 60   # hard ceiling for the whole pass
RENDER_GOTO_MS = 15000       # per page, to first paint
RENDER_IDLE_MS = 4000        # best-effort wait for the network to settle
RENDER_SETTLE_MS = 500       # a moment for hydration to write to the DOM
RENDER_MIN_SECONDS = 10      # below this there is no time to render even one page honestly


def _render_targets(pages):
    """The pages to render: a spread across page types, not the first five.

    This was `pages[:RENDER_PAGES]`. The crawl is breadth-first from the
    homepage, so those five were the homepage plus the first four top-nav
    pages - which on a hybrid site are exactly the pages rendered on the
    server. The JavaScript shells this pass exists to measure live on product,
    article and location detail pages one level down, and could never be in
    the sample. A site-wide share was being computed from a sample chosen by
    position, which is the one thing a sample must not be.

    Deterministic: every ordering here is by page type then URL, so two runs
    against the same site render the same five pages.
    """
    ok = [p for p in pages if p.get("status") == 200]
    if not ok:
        return []

    chosen, chosen_urls, seen_types = [], set(), set()

    def take(page):
        chosen.append(page)
        chosen_urls.add(page["url"])
        seen_types.add(page.get("page_type"))

    home = next((p for p in ok if p.get("page_type") == "home"), None)
    if home is not None:
        take(home)

    # One page of each type we have not seen yet, deepest types first: a
    # product or article page tells us more about rendering than another
    # listing page does.
    def type_rank(page):
        ptype = page.get("page_type")
        return (0 if ptype in DEEP_TYPES else 1, ptype or "", page["url"])

    for page in sorted(ok, key=type_rank):
        if len(chosen) >= RENDER_PAGES:
            break
        if page["url"] in chosen_urls or page.get("page_type") in seen_types:
            continue
        take(page)

    # Still short - a small site with two page types - so fill by URL order.
    for page in sorted(ok, key=lambda p: p["url"]):
        if len(chosen) >= RENDER_PAGES:
            break
        if page["url"] not in chosen_urls:
            take(page)

    return chosen[:RENDER_PAGES]


def _render_pass(pages, notes, crawl_deadline=None, required=False):
    """Playwright pass: measure how much text JavaScript adds.

    Absence of Playwright is a property of the auditing machine, never a
    finding about the site. `required` only changes how loudly that is said:
    someone who typed `--render` asked for this and should be told it did not
    happen, whereas on a default run it is ordinary.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        notes.append(
            "--render was requested but Playwright is not installed; "
            "run `pip install playwright && playwright install chromium`. "
            "Static-only pass; this is not a site defect"
            if required else
            "Playwright is not installed, so the JavaScript gap is inferred from the static "
            "HTML rather than measured. Install it for the measured version; this is a "
            "property of the auditing machine, not a defect in the site")
        return "static"

    targets = _render_targets(pages)
    if not targets:
        return "static"

    deadline = time.monotonic() + RENDER_BUDGET_SECONDS
    if crawl_deadline is not None:
        deadline = min(deadline, crawl_deadline)
    remaining = deadline - time.monotonic()
    if remaining < RENDER_MIN_SECONDS:
        notes.append(
            "the crawl used its wall-clock budget, leaving under {}s for the rendered pass, so "
            "the JavaScript gap is inferred rather than measured".format(RENDER_MIN_SECONDS))
        return "static"
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch()
            context = browser.new_context(user_agent=USER_AGENT)
            for page_record in targets:
                if time.monotonic() > deadline:
                    notes.append("rendered pass stopped at the {}s budget; {} page(s) measured"
                                 .format(RENDER_BUDGET_SECONDS, targets.index(page_record)))
                    break
                tab = context.new_page()
                try:
                    # Not `networkidle`. Playwright discourages it and a page
                    # with analytics polling or an open socket never reaches it,
                    # so a five-page pass took over two minutes against a local
                    # fixture. Wait for the document, give hydration a bounded
                    # moment, then read whatever is there.
                    tab.goto(page_record["final_url"],
                             wait_until="domcontentloaded", timeout=RENDER_GOTO_MS)
                    try:
                        tab.wait_for_load_state("networkidle", timeout=RENDER_IDLE_MS)
                    except Exception:  # noqa: BLE001 - never going idle is normal
                        pass
                    tab.wait_for_timeout(RENDER_SETTLE_MS)
                    rendered = tab.evaluate("document.body ? document.body.innerText : ''")
                    page_record["rendered_text_len"] = len(re.sub(r"\s+", " ", rendered).strip())
                except Exception as exc:  # noqa: BLE001 - a render failure is not a site defect
                    page_record["render_error"] = truncate(str(exc), 200)
                finally:
                    tab.close()
            browser.close()
    except Exception as exc:  # noqa: BLE001
        notes.append("Playwright pass failed ({}); falling back to static analysis".format(truncate(str(exc), 120)))
        return "static"
    return "rendered"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Crawl a site into snapshot.json (read-only).")
    parser.add_argument("target", help="site URL or bare hostname")
    parser.add_argument("--out", default="snapshot.json", help="snapshot output path")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--budget", type=float, default=WALL_CLOCK_BUDGET,
                        help="wall-clock seconds for the crawl")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY,
                        help="seconds between requests")
    parser.add_argument("--render", action="store_true",
                        help="require the Playwright pass and say so if it cannot run")
    parser.add_argument("--no-render", action="store_true",
                        help="skip the Playwright pass even if Playwright is installed")
    parser.add_argument("--ignore-robots", action="store_true",
                        help=argparse.SUPPRESS)  # test fixtures only
    args = parser.parse_args(argv)

    snapshot = crawl(
        args.target, args.out, max_pages=args.max_pages, budget_s=args.budget,
        respect_robots=not args.ignore_robots, delay=args.delay,
        render=False if args.no_render else (True if args.render else "auto"),
    )
    crawl_info = snapshot["crawl"]
    eprint("crawled {} page{} ({} ok) in {}s -> {}".format(
        crawl_info["pages_crawled"], "" if crawl_info["pages_crawled"] == 1 else "s",
        crawl_info["pages_ok"], crawl_info["elapsed_s"], args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
