#!/usr/bin/env python3
"""Fetch a bounded, deterministic sample of a site and write snapshot.json.

One crawl feeds all six sub-skills. The budget is fixed (seed 42, 60 pages,
240 s wall clock) and so is the order the addresses are worked through, so the
same site produces the same page set on every run, which is what makes the
findings reproducible.

How many of those addresses get read is fixed before the first request goes
out: the `--max-pages` ceiling, lowered only by a `Crawl-delay` the site
publishes in robots.txt. No duration this run measures is allowed to decide
it. Running this tool twice against one unchanged site used to give two
different reports - 11 findings then 13, and 53 pages then 60 - because the
count used to be computed from the measured cost of a page; the comment above
`PAGE_COST_RUNGS` shows the arithmetic that produced 53 and 60.

The wall clock is a backstop and nothing more. Where it, a rate limiter or a
failed connection did choose part of what was read,
`crawl.page_plan.deterministic` is false, `not_reproducible_because` says why
in a sentence, and `truncated_at` names the address the crawl stopped before.
`crawl.budget_exhausted` means the wall clock ran out and nothing else;
`crawl.stopped_early` is the separate fact that pages of the site were left
unread, which is what a check holding back an absence claim needs.

Usage:
    python crawl.py https://example.com --out snapshot.json
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zlib
from collections import defaultdict, deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit_common import (  # noqa: E402
    CHALLENGE_GIVE_UP_AFTER, CHALLENGE_GIVE_UP_SHARE,
    HTML_PAGE_MARKERS, HTML_SNIFF_CHARS, response_is_an_html_page,
    DEEP_TYPES, MAX_DEPTH, MAX_PAGES, MAX_RESPONSE_BYTES, MAX_SITEMAP_SAMPLE,
    REFUSED_STATUS,
    REQUEST_DELAY,
    REQUEST_TIMEOUT,
    ORG_IDENTITY_TYPES,
    SEED, USER_AGENT, WALL_CLOCK_BUDGET, FetchError, Fetcher, comparison_key,
    undecoded_body,
    detect_page_type, regional_homepage,
    detect_site_language, eprint, is_forbidden_path, is_multi_location,
    jsonld_type_names, LEADING_ARTICLE_RE, name_the_site_spells,
    normalise_url, origin_of,
    page_identity, the_brand_slot_is_empty,
    response_text, same_site,
    site_label, strip_www, truncate, VISITABLE_JSONLD_TYPES, word_count,
    write_json,
)

from page_extract import extract_page, recount_readability  # noqa: E402
from robots_parser import (  # noqa: E402
    group_for, is_disallowed, parse_robots, path_of, paths_under_route_words,
)

# How much of an agent-readable file is kept in the snapshot. Enough to read
# the links and the headings out of a real `/llms.txt`, which is a page or two
# of text, and not enough for `/llms-full.txt` - a whole site flattened - to
# dominate the file every skill loads.
AGENT_FILE_TEXT_MAX = 20000

SNAPSHOT_SCHEMA_VERSION = 1

# The one definition of "is this response an HTML page rather than the file
# that was asked for" lives in `audit_common` and is imported above, along
# with the size of the window it sniffs and the markers it looks for. It was
# written twice - here and in `crawl-access-audit/scripts/check.py` - byte for
# byte, and the copy here said so in its own docstring, which is how this
# repository records a defect it has not fixed yet. Two spellings of one rule
# is the drift argued against everywhere else in this codebase.
#
# The names stay importable from this module because
# `tests/test_a_200_is_not_a_file.py` reads them from here.


def write_json_atomically(path, obj):
    """`write_json`, but a reader never sees a half-written file.

    The snapshot is the only thing this crawl produces, and it is now written
    more than once - see `_write_checkpoint` - so a write that is interrupted
    has to leave the previous one intact rather than truncate it. Written to a
    sibling file and renamed over the target; `os.replace` is one filesystem
    operation on every platform this runs on, so the file at `path` is always
    either the last complete snapshot or this one.
    """
    partial = path + ".partial"
    write_json(partial, obj)
    os.replace(partial, path)


# How often the crawl leaves what it has on disk, and why it is not every page.
#
# Measured on this tree, on a sixty-page snapshot whose pages carry 40 KB of
# text each - 2.2 MB of JSON, the size a snapshot of a real retail site
# reaches. One checkpoint costs 0.053 s: 0.031 to serialise and write, and
# 0.022 to re-derive the brand name and the site language from the pages.
#
# Every page would spend 3.2 s of a 115-second budget and rewrite 132 MB over
# one crawl. Every fifth page spends 0.6 s, rewrites 26 MB, and caps at four
# pages what a crawl stopped at the reserve boundary loses - which is not worth
# the other 2.6 s of crawling.
CHECKPOINT_EVERY_PAGES = 5

# What a snapshot says about itself when it was written mid-crawl.
#
# It is appended to `crawl.notes`, which the report already prints one bullet
# per line in its appendix, so a reader of the report learns it from the same
# document that describes the pages rather than from a console they never saw.
CHECKPOINT_NOTE = (
    "this snapshot was written while the crawl was still running and is the "
    "record it had reached, so it is only ever read when the crawl was stopped "
    "before it finished. Site-wide headers and footers have not been stripped "
    "from these pages, no rendered pass has run and whether this host answers "
    "HEAD was never measured, so word counts and anything that depends on "
    "JavaScript are less reliable here than in a crawl that ran to the end."
)

# The first two bytes of every gzip stream.
GZIP_MAGIC = bytes([0x1F, 0x8B])

# Longer than this and a separator-free <title> is a sentence, not a name.
TITLE_AS_BRAND_MAX = 40


def _as_declared(candidate):
    """The string the site published for this candidate.

    Identical to `candidate["name"]` for everything except an `og:site_name`
    the reader split, where the name is one half and the declaration is the
    whole value. Every field that says "the site asserts this" is built from
    this rather than from `name`; see the `published` note in `detect_brand`.
    """
    return candidate.get("published") or candidate["name"]


def _confined_to_one_directory(candidate, declared_on, pages, home_url):
    """Was this name declared only under a single top-level directory?

    True only when the site has more than one such directory, so a site that
    keeps everything under `/en/` is not stripped of its own name. The
    homepage is excluded from the test: a name declared there speaks for the
    site by definition.
    """
    if not str(candidate.get("source") or "").endswith("-off-home"):
        return False
    # Keyed on what the site published, because that is what `declared_on` is
    # keyed on - a split `og:site_name` records the whole value there and
    # carries the half in `name`, and looking the half up finds nothing, which
    # reads as "declared nowhere" and lets the candidate through.
    urls = [u for u in (declared_on.get(_as_declared(candidate)) or ()) if u]
    if not urls or (home_url and home_url in urls):
        return False
    declaring = {_first_segment(u) for u in urls}
    if len(declaring) != 1 or "" in declaring:
        return False
    # A directory, not a page. `/about.html` has a first segment like anything
    # else, and reading it as a directory made every name declared on exactly
    # one top-level page into that page's own - which dropped three of the
    # four names a naming-consistency test asserts on a site that spells
    # itself three different ways, leaving one variant and nothing to compare
    # it against. A section has pages beneath it, or is addressed at the
    # prefix itself with a trailing slash.
    if not any(_is_a_directory(u) for u in urls):
        return False
    crawled = {_first_segment(p.get("url") or "") for p in pages}
    crawled.discard("")
    return len(crawled) > 1


def _is_a_directory(url):
    path = urlparse(url).path or "/"
    return len([p for p in path.split("/") if p]) >= 2 or path.endswith("/")


def _first_segment(url):
    parts = [p for p in (urlparse(url).path or "/").split("/") if p]
    return parts[0].lower() if parts else ""


def _without_a_borrowed_article(candidate, domain_token):
    """Drop a leading "The" that the site's own domain does not carry.

    Only for a name read off a title, and only when an article-free form of it
    spells the domain. A brand whose real name begins with "The" keeps it,
    because its domain does too.
    """
    if not str(candidate.get("source") or "").startswith("title"):
        return candidate
    token = comparison_key(domain_token)
    if not token:
        return candidate
    # Only the leading article, and only when what is left spells the domain.
    #
    # `name_forms(domain_token=...)` also derives a head form - the rule that
    # turns "Walmgate Indian Restaurants" into "Walmgate" - and reaching for
    # the whole family here cut "Example Relief" on `example.test` down to
    # "Example". That rule earns its place where a site never writes its full
    # legal name; it has no business shortening a name a title states in full.
    without = LEADING_ARTICLE_RE.sub("", candidate["name"]).strip()
    if without and without != candidate["name"] and comparison_key(without) == token:
        return dict(candidate, name=without)
    return candidate


def _names_the_address(candidate, host):
    """Does this candidate brand name contain the site's own hostname?

    A page whose title is built from the address it sits at - "Sitemap for
    www.example.edu", "Index of /~user", "www.example.edu - Home" - is
    describing itself to a machine, and every one of those strings is a page
    label rather than an organisation's name.
    """
    bare = re.sub(r"^www\.", "", (host or "").lower())
    text = (candidate or "").lower()
    if not bare or not text:
        return False
    return bare in text or ("www." + bare) in text

# The longest a single piece of an `og:site_name` can be and still be part of
# the name rather than a tagline attached to it. Splitting unconditionally cut
# a charity's own "charity: shelter" down to "water", which then became the brand
# for a whole report and sent a Wikidata search for the word.
SITE_NAME_PIECE_MAX = 20

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
# The ideographic space is a separator too. A Japanese digital library
# titles its homepage "<Japanese name>　<Latin name>" - one name written
# twice, once in each script, joined by U+3000 - and with no separator
# recognised the whole bilingual string became the entity name: the
# Organization snippet's `name`, the string the definition check hunted
# for, and the term sent to a Wikidata search that then returned nothing
# for an organisation that has an item.
# The fullwidth vertical bar is the same separator written in a fullwidth
# font, which is what a title composed on a Japanese or Chinese keyboard uses
# where an English one types `|`. Without it a title of the form
# "Brand｜Tagline" had no separator this file recognised, so the whole string
# was one candidate name and the tagline travelled through the report as the
# organisation's name. It gets its own branch rather than joining the class
# above, because that branch requires a space after the separator to keep a
# clock time like "9:00 to 5:00" in one piece - and a fullwidth bar is written
# tight against both words, while nothing else in a title is ever a fullwidth
# bar, so no such guard is needed for it.
TITLE_SEPARATOR = re.compile(r"\s*[|:·•]\s+|\s*｜\s*|\s[-–—]\s|　+")

# The same separators without the colon, for `og:site_name` only.
#
# A `<title>` is a sentence built around a name, and a colon in one is nearly
# always the join: "Frostvane: the fast DataFrame library". The tag whose only
# job is to state the site's name is the opposite case - a colon in it is
# punctuation inside the name. A leather workshop declares "The Bicyclist:
# Handmade Leather Goods" in `og:site_name` on all 58 of its pages, and
# splitting it produced a bare "The Bicyclist" that appears on no page of the
# site, filed under `declared_by` as if the site had asserted it. Two
# high-severity findings then reported the site's markup as contradicting its
# own pages, which agree exactly.
#
# `audit_common._TITLE_SEPARATOR_RE` reaches the same answer from the other
# direction - "charity: shelter" is one name - so this is not a new rule, it
# is the rule that file already applies, now applied here too.
SITE_NAME_SEPARATOR = re.compile(r"\s*[|·•]\s+|\s*｜\s*|\s[-–—]\s|　+")

# A meta refresh with a delay this short is a redirect, not a courtesy pause on a
# page someone is meant to read. Longer ones stay where they are.
META_REFRESH_MAX_DELAY = 5


# --------------------------------------------------------------------------
# A link a visitor can follow
#
# One family of hosted site builder injects an empty anchor into every page it
# serves, pointing at an asset route that answers 404 to anything but its own
# script:
#
#     <a rel="noindex noopener nofollow noarchive" href="/getjsassets"
#        target="_blank" style="display: none;" aria-hidden="true"
#        tabindex="-1"></a>
#
# The crawl followed it, spent a page slot on it, and recorded a 404. On a
# two-page site that one artefact became "33.3% of crawled pages do not return
# HTTP 200" and a second, separate finding telling the owner that "a broken
# link is a visitor stopped mid-journey" - about an anchor no visitor can see,
# reach or click. Two of the three items that report opened with were the same
# injected trap, and it also poisoned the denominator of every share the report
# computed.
#
# The four signals on that anchor are not equal evidence, and only two of them
# are evidence at all:
#
#   style="display:none"  a script can undo it between the response and the
#                         visitor, and one usually does: every collapsed menu,
#                         tab panel and accordion on the web ships this way and
#                         their links are real links.
#   rel="nofollow"        a request about how a search engine should weigh the
#                         destination, not a statement about who can see the
#                         anchor. Sites nofollow their own login link.
#   rel="noindex"         not a link relation any specification defines, and
#                         what it gestures at is the destination rather than
#                         this anchor.
#   aria-hidden="true"    the element is removed from the accessibility tree,
#     with tabindex="-1"  so a screen reader never announces it, *and* removed
#                         from the keyboard focus order, so a keyboard never
#                         lands on it. Together they are the page stating in
#                         two independent ways that this element is not for a
#                         person. Neither is undone by CSS, and a script that
#                         reveals a hidden menu changes `display`, not these.
#
# So the rule is that pair, and nothing else: `aria-hidden="true"` and
# `tabindex="-1"` on the same anchor. One definition, applied in the crawler
# (which does not queue such a link) and in `engagement-audit` (which does not
# report one as a broken link a visitor walked into).
#
# Both halves are read off the anchor's own open tag. `aria-hidden` is
# inherited down the accessibility tree and `tabindex` is not, so demanding
# both on one element needs no walk up the document - which also means a
# missing close tag somewhere above cannot widen this rule into swallowing a
# real link.
_ANCHOR_OPEN_TAG_RE = re.compile(r"<a\b[^>]*>", re.I)
_TAG_ATTRIBUTE_RE = re.compile(
    r"""([A-Za-z_:][-.:\w]*)\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+)""")


def anchors_no_visitor_can_reach(html, base):
    """Addresses this page offers a person nowhere on it.

    Read from the delivered HTML rather than from the extracted link list,
    because the snapshot records an anchor's `rel` and not the two attributes
    that settle this. A regex over anchor open tags rather than a second parse
    of the whole document: the two attributes sit on the `<a>` tag itself,
    where nesting cannot confuse a scan, and re-parsing every page a second
    time would come out of the crawl's wall-clock budget for nothing.

    A URL that some other anchor on the same page offers plainly is not in the
    result. The question is whether anything on the page leads a visitor
    there, not what one anchor did - the same rule the broken-link check
    already applies to a destination held in a fragment.
    """
    hidden, plain = set(), set()
    for tag in _ANCHOR_OPEN_TAG_RE.findall(html or ""):
        attrs = {}
        for name, value in _TAG_ATTRIBUTE_RE.findall(tag):
            attrs[name.lower()] = value[1:-1] if value[:1] in ("\"", "'") else value
        href = (attrs.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = normalise_url(href, base)
        if not url:
            continue
        if (attrs.get("aria-hidden", "").strip().lower() == "true"
                and attrs.get("tabindex", "").strip() == "-1"):
            hidden.add(url)
        else:
            plain.add(url)
    return hidden - plain


def _mark_links_no_visitor_can_reach(record, html, base):
    """Flag the link entries whose anchors are all closed to a person.

    The key is written whether or not anything matched, so a check downstream
    can tell "this crawl looked and found none" from "this crawl predates the
    question" rather than reading an absent key as an empty answer.
    """
    links = record.get("links")
    if not isinstance(links, dict):
        return
    unreachable = anchors_no_visitor_can_reach(html, base)
    marked = set()
    for bucket in ("internal", "external"):
        for link in links.get(bucket) or []:
            if isinstance(link, dict) and link.get("url") in unreachable:
                link["unreachable_by_a_visitor"] = True
                marked.add(link["url"])
    links["targets_no_visitor_can_reach"] = sorted(marked)


def dedup_key(url):
    """Identity of a page for crawl purposes.

    Without this the crawler fetches example.com/ and www.example.com/ as two
    pages and then reports them as duplicate titles. A genuine split between
    the two hostnames is still reported separately as host-inconsistency.

    This used to be `page_identity` minus the navigation-source parameters,
    and the difference cost budget rather than accuracy. A hosted store links
    one destination from three menu items, tagging each with the menu it was
    clicked from, so the crawl queued three addresses that differ only in that
    tag and read the same page three times. Thirteen of sixty page slots on
    that crawl went to duplicates, and the report then said 25 of 60 pages
    share a title. Counting is repaired downstream; the slots are only
    recoverable here, before the request goes out.

    A trailing slash is not the site telling two pages apart either. A home
    decor shop links `/home-visit` from one menu and `/home-visit/` from
    another; both answered 200 with the same page, both were read, and every
    count over the crawl carried that page twice. So the key drops a final
    slash from any path but the root, and the first of the two spellings to
    be queued is the one read - its response is the one kept. A site where
    the two spellings really are different pages is the rare case, and it
    costs one page of sample rather than a doubled finding.
    """
    key = page_identity(url)
    if not key:
        return key
    address, question, query = key.partition("?")
    scheme, _, rest = address.partition("://")
    host, slash, path = rest.partition("/")
    if path.endswith("/"):
        path = path.rstrip("/")
    return "{}://{}{}{}{}{}".format(scheme, host, slash, path, question, query)


# Non-HTML endings we never queue: they cost a request and carry no prose.
#
# This list is the cheap half of a two-part rule, and it is deliberately not
# the half correctness rests on. An ending can only ever *save* a request; it
# can never be complete, because the next site names its files something
# nobody wrote down here. The complete half is the content type, read off the
# response in `fetch_page` before the body is pulled - see `_read_body` on
# `PageFetcher`. So the division of labour is:
#
#   ending on this list      no request at all
#   anything else            one request, headers only unless it is HTML
#
# A HEAD would cost the same one request as reading the headers of the GET and
# would then need a second request for the body of a page that is a page, so
# HEAD is never the cheaper test and is not used.
#
# The list is written by family rather than by the one member that has burned
# us, because that is what stops the same gap reopening. It was a list of
# single names, and a static site publishing its releases as `.tar.bz2` with a
# `.patch` beside them matched none of them: `gz` and `tar` were present,
# `bz2` was not. Nine such responses were downloaded in full - 17.8 MB of
# somebody else's bandwidth - and each one then took a slot from a page that
# had prose on it. So: every compression suffix, not `gz`; every package
# format, not `pkg`; patches and signatures and checksums, which a source
# project publishes beside every release.
SKIP_EXTENSIONS = re.compile(
    r"\.("
    # images
    r"jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|heic|jxl|psd|ai|eps"
    # stylesheets, scripts, fonts, build output
    r"|css|js|mjs|cjs|map|wasm|woff2?|ttf|otf|eot"
    # archives and every compression suffix that follows a `.tar`
    r"|zip|tar|gz|tgz|bz2|tbz2?|xz|txz|lz|lzma|zst|zstd|7z|rar|iso|img"
    # packages a project publishes for download
    r"|deb|rpm|dmg|exe|msi|pkg|apk|jar|war|whl|egg|gem|nupkg|appimage|snap"
    # source deltas, signatures and checksums, published beside a release
    r"|patch|diff|asc|sig|gpg|pgp|md5|sha1|sha256|sha512|crc"
    # audio and video
    r"|mp3|mp4|m4a|m4v|wav|aac|flac|opus|og[gav]|mov|avi|wmv|flv|mkv|webm"
    # documents and data
    r"|pdf|rss|atom|json|jsonld|txt|csv|tsv|xlsx?|docx?|pptx?|odt|ods|odp|rtf"
    r"|epub|mobi|djvu|ps|dvi|yaml|yml|toml|sql|sqlite|db|dat|bin"
    r")(\?|$)",
    re.I,
)

# The `skipped` reason, and the flag beside it, for a response whose own
# content type proved it was never a page. `compose_report` reads the flag to
# keep such a URL out of the readable-page denominator: a release archive that
# answered 200 is not a page this crawler was refused, and counting it as one
# is what once suppressed eight true findings into the unverifiable list.
NOT_A_PAGE_REASON = "non-HTML content type"

# Path segments that address the signed-in visitor rather than the public.
#
# `is_forbidden_path` already refuses `/account`, `/my-account` and `/login`,
# and it is the read-only guard, so it lists the spellings it knows. It does
# not know `/mypage`, which is the same route with the hyphen left out and is
# how a large number of shops name it. One crawl reached `/store/mypage/change`
# and got 401 - harmless in itself, GET only - but the record then sat in the
# snapshot as a URL this site "refused", and the user-agent comparison picked
# it as the one URL to probe under seven crawler names. Every name got 401,
# because every name gets 401 from a page that wants a password, so the check
# that exists to find an edge treating crawlers differently measured nothing
# at all. A page needing credentials this audit will never have is not a page
# to spend a request on and not a page to compare responses against.
PRIVATE_AREA_RE = re.compile(
    r"(?:^|/)my[-_]?(?:page|pages|account|accounts|profile|orders?|"
    r"settings|details|data|list|lists|library)(?:[./]|$)",
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


def _robots_with_paths_seen(robots, sitemaps, pages, skipped):
    """The robots record, carrying the addresses a route-word rule reaches past.

    `Disallow: /home` is excused as the front page's second address, and a
    robots.txt rule is a prefix, so it also closes `/home-decor`. Only the
    crawl knows whether the site has such an address - in its sitemap, in a
    link, among the addresses skipped because robots.txt closes them - so it
    is written here and read by `substantive_disallows`. See
    `paths_under_route_words`.
    """
    if not isinstance(robots, dict) or not robots.get("groups"):
        return robots
    urls = [entry.get("url") for entry in skipped or () if entry.get("url")]
    for record in sitemaps or ():
        urls.extend(u.get("loc") for u in record.get("urls") or () if u.get("loc"))
    for page in pages or ():
        urls.append(page.get("url"))
        urls.extend(l.get("url") for l in (page.get("links") or {}).get("internal") or ())
    return dict(robots, paths_under_route_words=paths_under_route_words(
        robots, [u for u in urls if u]))


def _tag(element):
    return element.tag.split("}")[-1].lower()


# How many children of a sitemap index to fetch, and how many sitemap records
# to keep in all. Five children rather than three because a site that splits
# its sitemap by language has one per language, and reading one of twelve made
# the sample as lopsided as the total was wrong. The ceiling stays low on
# purpose: the deadline is checked every iteration, and a full inventory of a
# 32,000-URL sitemap is not what any check here needs.
SITEMAP_INDEX_CHILDREN = 5
SITEMAP_RECORDS = 8

# How many language codes a sitemap record keeps, and how many alternates of
# its one example entry. Enough to name every edition of a site with one per
# country; a record is not the place for the whole matrix.
SITEMAP_HREFLANG_CODES = 30


def _sitemap_alternates(entry, base_url):
    """`[(hreflang, href), ...]` for one sitemap `<url>` entry.

    The sitemap form of `hreflang`: `<xhtml:link rel="alternate"
    hreflang="en-gb" href="..."/>` inside the `<url>`, namespaced by the
    XHTML namespace. Read by local name so a sitemap declaring the namespace
    under another prefix is read the same way.
    """
    out = []
    for link in entry.findall("./{*}link"):
        rel = str(link.get("rel") or "").strip().lower()
        code = str(link.get("hreflang") or "").strip()
        href = str(link.get("href") or "").strip()
        if rel != "alternate" or not code or not href:
            continue
        out.append((code.lower(), normalise_url(href, base_url) or href))
    return out


def fetch_sitemaps(fetcher, origin, robots_record, deadline):
    """Fetch sitemaps referenced in robots plus the conventional location.

    Follows one level of sitemap index, capped at `SITEMAP_INDEX_CHILDREN`,
    because the goal is a representative URL sample, not a full inventory.

    Every record keeps the full child list whether or not the child was
    fetched, so `sitemap_scope` can say how much of the index the totals
    actually cover. Reporting a partial sum as the site's total was worth
    three wrong numbers in one validation pass.
    """
    queue = list(dict.fromkeys(robots_record.get("sitemaps") or []))
    referenced_in_robots = bool(queue)
    # Two conventional locations, not one. `/sitemap_index.xml` is what Yoast
    # writes, which makes it the default on a large share of WordPress sites,
    # and a site that publishes only that one was reported as having no sitemap
    # at all - measured on a hardware manufacturer whose index sits there and
    # names four child sitemaps. Both are tried; a site with neither costs one
    # extra request and gets a more accurate answer.
    default_url = origin.rstrip("/") + "/sitemap.xml"
    if default_url not in queue:
        queue.append(default_url)
    # `/sitemap_index.xml` is the other conventional location - it is what
    # Yoast writes, which makes it the default on a large share of WordPress
    # sites, and a site publishing only that one was reported as having no
    # sitemap at all. Tried only when nothing else produced one, because on a
    # host that is refusing connections every speculative fetch costs the
    # retry budget twice over: adding it unconditionally spent the whole
    # 20-second budget before the homepage was ever requested, and the report
    # came back with no pages and no critical finding about a dead site.
    fallback_url = origin.rstrip("/") + "/sitemap_index.xml"

    results = []
    seen = set()
    index_children_fetched = 0
    while queue and len(results) < SITEMAP_RECORDS and time.monotonic() < deadline:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        record = {"url": url, "status": None, "urls": [], "is_index": False,
                  "parse_error": None, "child_sitemaps": [],
                  "referenced_in_robots": url in (robots_record.get("sitemaps") or []),
                  # Present on every record this crawl reads, zero included, so
                  # a check can tell "read, and none declared" from a snapshot
                  # taken before the sitemap's alternates were read at all.
                  "hreflang_url_count": 0, "hreflang_codes": [],
                  "hreflang_example": None}
        try:
            response = fetcher.get(url)
        except FetchError as exc:
            record["parse_error"] = "fetch failed: {}".format(exc)
            # A sitemap that could not be reached, as distinct from one that
            # answered something unusable. The sitemaps decide the candidate
            # URLs, so a run that failed to reach one starts its page loop from
            # a different list than a run that reached it - which is a page set
            # this run cannot promise to repeat. `crawl` reads this flag into
            # `page_plan.not_reproducible_because`; a 404 or an unparsable file
            # is not it, because those answer the same way every time.
            record["fetch_failed"] = True
            results.append(record)
            continue
        record["status"] = response.status_code
        # What the server said it sent, and how much of it, on every record.
        #
        # A temple's server answers every unknown path with its homepage.
        # `/sitemap.xml` returned 200, `text/html`, 1.8 MB - the homepage byte
        # for byte - the XML parser choked on it, and the report published "an
        # XML sitemap is published but does not parse ... line 25, column 104"
        # at high confidence in the confident tier, with a fix that is work on
        # a file that does not exist. It was the only false positive to reach
        # the confident tier across six sites in that validation pass.
        #
        # The check that reads these records can only tell that shape from a
        # genuinely malformed sitemap by probing for a path nobody published,
        # which costs a request and is unavailable on a run with no budget
        # left. The server's own `Content-Type` settles it outright and costs
        # nothing, because this response is already in memory - so it is
        # recorded here rather than re-established downstream. The length is
        # recorded with it: a "sitemap" the size of a homepage is the same
        # evidence read a second way, and a check that has neither has to guess.
        record["content_type"] = (
            (getattr(response, "headers", None) or {}).get("content-type") or "")
        record["body_bytes"] = len(response.content or b"")
        record["looks_like_html"] = (response.status_code == 200
                                     and response_is_an_html_page(response))
        if response.status_code != 200:
            results.append(record)
            continue
        payload = response.content
        # A .xml.gz sitemap is not a broken sitemap. The sitemaps.org protocol
        # names gzip explicitly and large sites use it as a matter of course.
        # Without this, a national government's sitemap of 706 URLs was
        # reported as "does not parse", quoting the Python parser choking on
        # gzip magic bytes, and ranked second in what to fix first.
        #
        # Tested on the magic bytes only. `url.endswith(".gz")` was also
        # accepted, and that is wrong twice over: `iter_content` already
        # decodes `Content-Encoding: gzip`, so a server sending
        # `sitemap.xml.gz` with that header hands back plain XML, and calling
        # `gzip.decompress` on plain XML raises `BadGzipFile` - reported as
        # "gzip could not be read" about a sitemap that is correct.
        if payload[:2] == GZIP_MAGIC:
            try:
                # Bounded, because deflate reaches about 1000:1 and nothing
                # else limits this. Five megabytes of zeros - the most the
                # fetcher will read - expands to roughly five gigabytes, and
                # `MemoryError` is not among the exceptions caught below, so
                # the whole audit would have died with no report.
                decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
                payload = decompressor.decompress(payload, MAX_RESPONSE_BYTES)
                record["gzipped"] = True
                if decompressor.unconsumed_tail:
                    record["truncated"] = True
            except (OSError, EOFError, zlib.error) as exc:
                record["parse_error"] = "gzip could not be read: {}".format(exc)
                results.append(record)
                continue
        # An internal entity declaration in a sitemap is never legitimate and
        # is the one shape that can make the parser expand a few kilobytes into
        # gigabytes. Python's own documentation names `xml.etree` as vulnerable
        # to it. Refusing the file costs a sitemap nobody publishes; parsing it
        # can cost the run.
        if b"<!ENTITY" in payload[:8192].upper():
            record["parse_error"] = ("the file declares XML entities, which a sitemap never "
                                     "needs and which this audit will not expand")
            results.append(record)
            continue
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            # Our cap, not their file. A national museum publishes a valid
            # 10.5 MB sitemap; the read cap stops at 5 MB, the parser then
            # fails on an unclosed tag at exactly that offset, and the report
            # told the museum to spend an hour fixing XML that is not broken.
            # A limit this audit imposed on itself is never a defect in the
            # site, and the same rule already governs blocked requests.
            if getattr(response, "truncated", False):
                record["truncated"] = True
                record["parse_error"] = None
                record["unchecked_reason"] = (
                    "the file is larger than the {:,}-byte cap this audit reads to, so it was "
                    "cut off mid-document and could not be parsed. That is a limit of this "
                    "audit, not a fault in the sitemap".format(MAX_RESPONSE_BYTES))
            else:
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
            for child_url in record["child_sitemaps"][:SITEMAP_INDEX_CHILDREN]:
                # On this origin only. A `<sitemap><loc>` naming a third party
                # would have had the audit fetch up to five files of up to
                # 5 MB each from a host whose robots.txt it never read - which
                # is the one guarantee this marketplace makes about itself.
                if not same_site(child_url, origin):
                    continue
                if index_children_fetched < SITEMAP_INDEX_CHILDREN:
                    queue.append(child_url)
                    index_children_fetched += 1
        else:
            for entry in root:
                loc = entry.find("./{*}loc")
                if loc is None or not (loc.text or "").strip():
                    continue
                lastmod = entry.find("./{*}lastmod")
                # The sitemaps.org protocol requires `<loc>` to begin with the
                # protocol, and Google discards a sitemap whose entries do not.
                # Measured on a live SaaS marketing site: every one of its 174
                # entries reads `<loc>/pricing</loc>`. Stored verbatim, those
                # values reached `normalise_url(entry["loc"])` with no base -
                # which returns None - and a HEAD probe of a bare path, so two
                # checks silently did nothing and neither said so. Resolved
                # here so the rest of the audit sees URLs, and counted so the
                # spec violation can be reported as the defect it is.
                raw_loc = loc.text.strip()
                resolved = normalise_url(raw_loc, url) or raw_loc
                if not re.match(r"^[a-z][a-z0-9+.-]*://", raw_loc, re.I):
                    record["relative_locs"] = record.get("relative_locs", 0) + 1
                record["urls"].append({
                    "loc": resolved,
                    "lastmod": (lastmod.text or "").strip() if lastmod is not None else "",
                })
                # The language editions of this address, where the sitemap
                # names them. A furniture retailer with one storefront per
                # country declares `<xhtml:link rel="alternate" hreflang=...>`
                # for every edition on every entry - a method search engines
                # read as fully as the `<link>` in a page's head - and the
                # crawl had downloaded that very file while the `hreflang`
                # check reported the editions linked to nothing. Counted per
                # record, with the codes and one example, rather than copied
                # onto five thousand entries.
                alternates = _sitemap_alternates(entry, url)
                if alternates:
                    record["hreflang_url_count"] += 1
                    record["hreflang_codes"] = sorted(
                        set(record["hreflang_codes"]) | {code for code, _href in alternates}
                    )[:SITEMAP_HREFLANG_CODES]
                    if not record["hreflang_example"]:
                        record["hreflang_example"] = {
                            "loc": resolved,
                            "alternates": [list(pair) for pair in
                                           alternates[:SITEMAP_HREFLANG_CODES]]}
                if len(record["urls"]) >= 5000:
                    # Said, not inferred from the round number: a national
                    # library's sitemap holds 44,909 addresses and the report
                    # called the first 5,000 the site's total.
                    record["url_cap_reached"] = True
                    break
        results.append(record)
        # Only when the host is answering. A site that answered 404 at
        # `/sitemap.xml` may still publish an index; a host that refused the
        # connection will refuse this one too, and on a dead origin every
        # speculative fetch costs the retry budget twice.
        if (not queue and fallback_url and fallback_url not in seen
                and any(r["status"] is not None for r in results)
                and not any(r["urls"] or r["child_sitemaps"] for r in results)):
            queue.append(fallback_url)
            fallback_url = None

    # The other place the clock can choose what gets read. The sitemaps decide
    # the candidate URLs, so a deadline that cuts this loop changes the page
    # set as surely as one that cuts the page loop - and this loop runs before
    # a single page has been fetched, so nothing downstream would notice. Left
    # on the fetcher rather than added to the return value, which two callers
    # and a schema already depend on; `crawl` reads it into
    # `crawl.page_plan.deterministic`, which is the one claim it would falsify.
    if queue and len(results) < SITEMAP_RECORDS and time.monotonic() >= deadline:
        fetcher.sitemaps_cut_by_clock = True

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


# Routes a CDN serves on the site's hostname that the site never wrote.
#
# `/cdn-cgi/l/email-protection` is the decoder for Cloudflare's email
# obfuscation: it is the `href` of every obfuscated `mailto:` on a page, the
# script rewrites it into the real address in the browser, and it answers 404
# to a plain GET by design. A one-page site with three fetched URLs had one of
# them be this, and the report opened - highest severity, first item under
# "Start here" - with "1 of 3 fetched URLs returned a non-200 status (1x404)".
# A 33% failure rate on a site with nothing wrong with it, over a link the
# owner did not write, cannot fix, and would break their own email addresses
# by removing.
#
# Everything under `/cdn-cgi/` is the same kind of thing: the CDN's own
# machinery, mounted on the site's hostname. None of it is a page.
CDN_MACHINERY_RE = re.compile(r"(?:^|/)cdn-cgi(?:/|$)", re.I)


def crawlable(url, origin, robots, respect_robots):
    if not same_site(url, origin):
        return False, "off-site"
    if is_forbidden_path(url) or PRIVATE_AREA_RE.search(urlparse(url).path or "/"):
        return False, "state-changing or private path"
    if SKIP_EXTENSIONS.search(url):
        return False, "non-HTML asset"
    if CDN_MACHINERY_RE.search(urlparse(url).path or "/"):
        return False, "a CDN's own route, not a page the site published"
    if respect_robots and robots.get("groups") and is_disallowed(robots, USER_AGENT, path_of(url)):
        return False, "disallowed by robots.txt"
    return True, ""


# --------------------------------------------------------------------------
# Crawl
# --------------------------------------------------------------------------

# How many records in all the crawl will hold, as a multiple of `max_pages`.
#
# A response the content type proved was not a page costs no page slot, which
# is the point of the two-part rule above - but it still costs one request and
# one record, and a host that answers `application/octet-stream` at every
# address must not be able to turn that into an unbounded crawl. Two: a run
# where more than half of everything reached is not a page has already
# established the only fact worth establishing about it.
NON_PAGE_RECORD_HEADROOM = 2

# Path segments that hold one thing each. A URL one level under one of these
# is the thing itself; the segment on its own is the index of them.
#
# `guess_type_from_url` cannot answer this. It runs the full classifier over
# an empty document, and the product branch demotes anything with no price on
# it - which an unfetched URL always is - so every product address guesses as
# a category. That is the right answer for spreading a sitemap sample across
# types and the wrong one here.
#
# `collections`, `category` and `catalog` are deliberately absent: on the
# hosted store platforms those hold a listing per entry, not an item.
ITEM_CONTAINER_SEGMENTS = frozenset({
    "product", "products", "item", "items", "sku", "dp", "p",
    "article", "articles", "post", "posts", "story", "stories",
    "news", "blog", "blogs", "recipe", "recipes", "event", "events",
    "location", "locations", "store", "stores", "branch", "branches",
    "service", "services", "course", "courses", "book", "books",
})


def looks_like_a_detail_url(url):
    """Is this URL one thing rather than a list of things?

    Decided on the shape of the path, which is all that is known before the
    page is fetched: a segment that holds one item each, then a name under it.
    A deeper path under such a segment counts too, because that is how a blog
    writes `/blogs/<blog>/<post>`.
    """
    parts = [p for p in (urlparse(url).path or "/").split("/") if p]
    if len(parts) < 2:
        return False
    return any(part.lower() in ITEM_CONTAINER_SEGMENTS for part in parts[:-1])


def crawl_kind(url):
    """The kind of page this URL is, judged before it is fetched.

    The classifier is asked first, because it is the same question
    `sample_sitemap_urls` asks of a sitemap and the two samples should not
    disagree about what kinds a site has. It cannot answer one thing on its
    own: run over an empty document, every item address under a listing
    segment comes back as the listing type - the reason
    `ITEM_CONTAINER_SEGMENTS` exists - so a shop's shelves and the things on
    them would share a kind and the shelves would crowd out the things. The
    two signals together are the kind.
    """
    kind = guess_type_from_url(url)
    return kind + "/item" if looks_like_a_detail_url(url) else kind


class KindSpreadQueue:
    """A breadth-first queue that serves each kind of page in rotation.

    A crawl sample has to cover the kinds of page a site has. Plain
    breadth-first order does not: a shop's homepage links to thirty-odd
    shelves, so 34 of a 43-page sample were shelves and one was an item, and
    every question the audit asks about an item was answered about one of
    them.

    Two queues - listings in one, items in the other, half the budget each -
    fixed that and broke the other half of the same rule. On a footwear
    brand's site the two between them took all 41 slots: 18 shelves, 19 items,
    the homepage and two others, and not one of the nine `about`, `contact`
    and policy pages linked from the homepage. The report then said the site
    never states what the brand is and never states when it was founded, with
    both facts sitting in the first sentence of a page the crawler had seen
    linked and chosen not to fetch. Reserving the budget for the deepest pages
    is the same mistake as letting the shallowest ones have it.

    So there are no buckets to divide by hand. Each kind of page gets a turn,
    the kind that has had the fewest turns goes next, and a kind that runs out
    of URLs stops holding a place. Within a kind the order is still
    first-in-first-out, so the breadth-first shape survives, and the arrival
    number breaking ties makes the whole order deterministic: the same site
    produces the same sample on every run, which is what the fixed seed and
    the fixed budget are for.

    This is the rule `sample_sitemap_urls` already applies to a sitemap, for
    the reason its docstring gives - coverage of kinds beats coverage of
    count - applied to the queue the rest of the crawl fills.
    """

    def __init__(self):
        self._waiting = {}
        self._taken = defaultdict(int)
        self._arrivals = 0

    def append(self, entry):
        """Queue `(url, depth, source)` under the kind of page it is."""
        self._waiting.setdefault(crawl_kind(entry[0]), deque()).append(
            (self._arrivals, entry))
        self._arrivals += 1

    def __len__(self):
        return sum(len(waiting) for waiting in self._waiting.values())

    def popleft(self):
        """The oldest URL of whichever kind has been served least."""
        kind = min((k for k, waiting in self._waiting.items() if waiting),
                   key=lambda k: (self._taken[k], self._waiting[k][0][0]))
        self._taken[kind] += 1
        return self._waiting[kind].popleft()[1]

    def peek(self):
        """The URL `popleft` would serve next, or None when nothing is waiting.

        Read without serving it, so a crawl the wall clock cut short can name
        the address it stopped before. Two runs that were cut at different
        points are then comparable by a reader, which is the least a run that
        cannot be reproduced owes one.
        """
        waiting = [k for k, entries in self._waiting.items() if entries]
        if not waiting:
            return None
        kind = min(waiting, key=lambda k: (self._taken[k], self._waiting[k][0][0]))
        return self._waiting[kind][0][1][0]

    def kinds_waiting(self):
        """The kinds still holding URLs, for a caller that wants to say so."""
        return sorted(k for k, waiting in self._waiting.items() if waiting)


def _served_a_document(response):
    """Did the server answer with the thing asked for, rather than refuse it?

    Only a 2xx may be judged by its content type. An edge that refuses this
    crawler with a JSON or plain-text error body is refusing a page, and
    reading that as "this URL was never a page" would drop it out of the
    readable-page denominator - which is the count that exists to notice
    exactly that refusal. It would also throw away the short, repeated body
    that `EDGE_REFUSAL_MAX_BYTES` identifies an edge refusal by.
    """
    status = getattr(response, "status_code", None)
    return status is not None and 200 <= status < 300


def _declared_length(headers):
    """`Content-Length` as an int, or None when the server did not say."""
    try:
        return int(str(headers.get("content-length")).strip())
    except (TypeError, ValueError):
        return None


def looks_like_html(content_type):
    """Is this response a page? The one test, used before and after the body.

    An absent content type counts as HTML. A server that declares nothing is
    almost always serving a page, and old static hosts do it often enough that
    refusing those would cost real pages.

    `application/xhtml+xml` counts and every other XML does not, which is the
    rule `fetch_page` has always applied; it lives here now so that the
    decision made before the body arrives and the decision made after it
    cannot drift apart.
    """
    value = (content_type or "").split(";")[0].strip().lower()
    return not value or "html" in value


# The status that means "you are asking too fast", as distinct from every
# other refusal, which means "you may not ask". 403 is a decision about who is
# asking and slowing down cannot change it; 503 is the server saying it is not
# serving at all, and is already read as that by `NOT_SERVING_STATUS`.
RATE_LIMIT_STATUS = frozenset({429})

# The slowest this crawl will go under a rate limiter, in seconds per request.
#
# A limiter that asks for longer than this is asking for more than the whole
# five-minute audit has, so waiting is not obedience, it is just running out
# the clock at the site's request and returning nothing. Past this the crawl
# stops and the report says the site rate limited it - which is the true and
# useful thing to tell an owner, and is what the run that produced 45 429s in
# 59 URLs should have said instead of withholding nine real findings.
RATE_LIMIT_MAX_DELAY = 30.0


def retry_after_seconds(value):
    """`Retry-After` as a number of seconds, or None if it said nothing usable.

    The header is defined as either a count of seconds or an HTTP-date, and
    real edges send both. A date is turned into the seconds between now and
    then, and a date already in the past means "you may ask again", which is
    zero rather than a negative wait.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, seconds)


class PageFetcher(Fetcher):
    """`Fetcher`, plus the ability to stop a response before its body.

    The non-HTML test in `fetch_page` was correct and 2 MB too late: it runs
    on a response whose body has already been pulled, so a crawl that reached
    nine release archives and a patch file threw away 17.8 MB it had already
    paid for. `Fetcher.get` asks the session with `stream=True` and reads the
    body in `_read_body`, which is the single point between the headers
    arriving and the bytes being pulled, so that is where the decision goes.

    Scoped to page requests, through `get_page`, and to nothing else. The same
    fetcher also collects robots.txt, sitemaps and `/llms.txt`, which are meant
    to be plain text or XML; an html-only rule reaching those would empty every
    one of them.

    This overrides a method of a class defined in `audit_common`, which this
    file does not own. `tests/test_only_pages_count_as_pages.py` asserts the
    hook still exists, so a rename over there fails the build here rather than
    quietly going back to downloading archives.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._html_only = False
        # What the site said about this crawl's own request rate. Read by
        # `crawl` into `snapshot["crawl"]["rate_limit"]`.
        self.rate_limited_urls = []
        self.retry_after_s = None
        self.rate_limit_gave_up = False
        self.slowed_to = None

    def get(self, url, **kwargs):
        """`Fetcher.get`, and then honour a rate limiter that answered.

        A Malaysian grocer answered 429 to 45 of its 59 URLs. Nothing here
        read `Retry-After` and nothing backed off, so the crawl kept asking at
        the same rate, and the report then treated the throttling this crawl
        had caused as grounds to withhold nine findings that were true. A 429
        is the site saying "slower", which is an instruction with a number
        attached and the same kind of thing as `Crawl-delay`.
        """
        response = super().get(url, **kwargs)
        if getattr(response, "status_code", None) in RATE_LIMIT_STATUS:
            self._back_off(url, response)
        return response

    def _back_off(self, url, response):
        """Slow down by what the site asked for, or by doubling if it did not.

        Bounded twice. `RATE_LIMIT_MAX_DELAY` is the slowest this crawl will
        ever go, because a limiter asking for an hour is asking for more than
        the whole audit has. The deadline is the harder bound: a wait that
        runs past it buys nothing - the crawl would be stopped before the
        request went out - so the crawl gives up on the site instead and says
        so. Reading fewer pages honestly beats overrunning the budget.
        """
        self.rate_limited_urls.append(url)
        asked = retry_after_seconds(response.headers.get("retry-after"))
        if asked is not None:
            self.retry_after_s = max(self.retry_after_s or 0, asked)
        wait = asked if asked is not None else max(self.delay, REQUEST_DELAY) * 2
        left = None if self.deadline is None else self.deadline - time.monotonic()
        if left is not None and wait >= left:
            self.rate_limit_gave_up = True
            return
        self.delay = min(max(self.delay, wait), RATE_LIMIT_MAX_DELAY)
        self.slowed_to = self.delay

    def get_page(self, url, **kwargs):
        """GET a URL that is meant to be a page. Its body is read only if it is."""
        self._html_only = True
        try:
            return self.get(url, **kwargs)
        finally:
            self._html_only = False

    def _read_body(self, response, method, started):
        # `_send_urllib` reads its own body and never reaches here, so the
        # second transport still downloads what it asks for. Nothing in this
        # file puts a page request on that transport.
        if self._html_only and _served_a_document(response) and not looks_like_html(
                response.headers.get("content-type")):
            response.close()
            response._content = b""
            response._content_consumed = True
            response.truncated = False
            response.body_declined = True
            return
        super()._read_body(response, method, started)


def fetch_page(fetcher, url, depth, source, origin):
    """Fetch one URL and build its snapshot record, or an error record."""
    # `get_page` where the fetcher has it. A stub fetcher in a test has only
    # `get`, and the fallback changes nothing but whether the body of a
    # non-page arrives before it is discarded.
    get_page = getattr(fetcher, "get_page", None) or fetcher.get
    try:
        response = get_page(url)
    except FetchError as exc:
        return {
            "url": url, "final_url": url, "status": None, "error": str(exc),
            "depth": depth, "source": source, "redirect_chain": [],
            "page_type": guess_type_from_url(url), "links": {"internal": [], "external": []},
            "text": "", "text_len": 0, "html_len": 0,
        }

    chain = [r.url for r in response.history]
    # The status of each hop as well as its address. A chain of addresses says
    # a redirect happened and not which kind: a 307 tells a crawler the old
    # address is still the real one and a 301 tells it the address has moved,
    # and the transport check has to be able to say which an owner has.
    hops = [r.status_code for r in response.history]
    content_type =(response.headers.get("content-type") or "").split(";")[0].strip().lower()
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
            "redirect_statuses": hops,
            "skipped": "redirects off this origin",
            "offsite_redirect": True,
            "page_type": "other", "links": {"internal": [], "external": []},
            "text": "", "text_len": 0, "html_len": 0, "headers": headers,
        }

    # `application/xhtml+xml` is HTML and stays; `application/rss+xml` and
    # every other XML document is not a page anybody reads. XML used to be let
    # through wholesale, and a news site's RSS feed was crawled as a page: it
    # has no `<h1>` and no `<html lang>` because it has no `<html>` element at
    # all, so the report told the newsroom to add both to a document where
    # neither exists. Sitemaps are XML too, and they are fetched by the sitemap
    # step rather than crawled as pages, so nothing is lost.
    if not looks_like_html(content_type):
        return {
            "url": url, "final_url": response.url, "status": response.status_code,
            "depth": depth, "source": source, "redirect_chain": chain,
            "redirect_statuses": hops,
            "content_type": content_type, "skipped": NOT_A_PAGE_REASON,
            # Not "a page we could not read" - a thing that was never a page.
            # The difference decides a denominator: a release archive that
            # answered 200 sat in the count of URLs the crawl "reached but
            # could not read", pushed the readable share under the bar that
            # holds back absence claims, and suppressed eight findings that
            # were true and checkable from the seven pages actually read.
            #
            # Only a 2xx earns the flag. A refusal that happens to carry a
            # JSON body is a URL this crawler was refused a page at, and it
            # belongs in that denominator, which exists to notice refusals.
            "not_a_page": _served_a_document(response),
            "page_type": "other", "links": {"internal": [], "external": []},
            "text": "", "text_len": 0, "html_len": len(response.content or b""),
            # What the server said it was about to send, when the body was
            # declined before it arrived. `html_len` is 0 on that path because
            # nothing was downloaded, which is the point.
            "declared_bytes": _declared_length(headers),
            "body_declined": bool(getattr(response, "body_declined", False)),
            "truncated": bool(getattr(response, "truncated", False)),
            "headers": headers,
        }

    # A body this client could not decompress is not a page. Recorded the same
    # way a non-HTML content type is: skipped, with the reason named, so every
    # check downstream leaves it out of its denominators instead of reading
    # compressed bytes as a page with no title, no links and no text.
    encoding = undecoded_body(response)
    if encoding:
        return {
            "url": url, "final_url": response.url, "status": response.status_code,
            "depth": depth, "source": source, "redirect_chain": chain,
            "redirect_statuses": hops,
            "content_type": content_type,
            "skipped": "the response arrived {} and this audit could not "
                       "decompress it".format(
                           "as Brotli (Content-Encoding: br)" if encoding == "br"
                           else "with Content-Encoding: " + encoding),
            "undecoded_encoding": encoding,
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
    # When this response was read, twice over: the server's own `Date` header
    # and this machine's clock. A page generated per request sends
    # `Last-Modified` equal to the moment it answered, and without either
    # time in the record nothing downstream could tell that from a real
    # modification date - the sitemap snippet printed the audit's own date as
    # `<lastmod>` on twelve pages of a documentation site, directly above the
    # advice "Do not set <lastmod> to today's date". The page record keeps
    # only a short list of headers, and `date` was not on it.
    if headers.get("date"):
        record.setdefault("headers", {})["date"] = headers["date"]
    record["fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record["redirect_statuses"] = hops
    # A page we stopped reading at the size cap is a limit of ours. Recorded
    # here so the crawl notes can say so, rather than letting a half-read
    # document look like a site that omitted the second half.
    record["truncated"] = bool(getattr(response, "truncated", False))
    # The same fact under a name no check can misread. On a Vietnamese shop a
    # guide page ran past the cap, the crawl's own note said so, and checks
    # still reported what the page lacked - its missing site chrome, its title
    # words absent from the text - about a document read only as far as
    # 5,000,000 bytes. `truncated` is also a word sitemaps and other records
    # use, so the page-level flag checks skip on is spelt out.
    if record["truncated"]:
        record["truncated_at_read_cap"] = True
    # What the server actually sent, in bytes. `html_len` counts characters of
    # the decoded string, and the page-weight check compares it against a
    # constant named in bytes - so a 2.4 MB page in Japanese, Hindi or Arabic
    # is about 800,000 characters in UTF-8 and could never trip a two-megabyte
    # threshold, while an equivalent ASCII page did. The check was
    # systematically blind on exactly the sites least like ours.
    record["html_bytes"] = len(response.content or b"")
    # Which of this page's anchors lead nowhere a person can go. Marked here,
    # once, on the record every downstream reader shares, so the crawler's
    # decision not to queue such a link and the engagement skill's decision
    # not to report it as broken rest on the same reading of the same HTML.
    _mark_links_no_visitor_can_reach(record, html, response.url)
    return record


# Words a title carries around the brand rather than as part of it.
_NOT_A_BRAND = frozenset({"our", "the", "my", "your", "this", "welcome", "home"})

TITLE_BOILERPLATE_LEAD = ("welcome to ", "the official ", "official ")
TITLE_BOILERPLATE_TAIL = (
    " home page", " homepage", " home", " official site", " official website",
    " website", " web site", " site", " online", " uk", " usa",
)


# The half of "Brand - Region" that is the region. Many international
# organisations name their national arm this way, and the region is never the
# brand.
_REGION_SUFFIXES = frozenset({
    "usa", "u.s.a.", "us", "u.s.", "uk", "u.k.", "gb", "eu", "ca", "au", "nz",
    "in", "canada", "australia", "new zealand", "ireland", "scotland", "wales",
    "united states", "united kingdom", "great britain", "america",
    "north america", "south america", "latin america", "europe", "emea",
    "apac", "asia", "africa", "middle east", "global", "international",
    "worldwide", "deutschland", "france", "espana", "italia", "nederland",
    "sverige", "norge", "danmark", "suomi", "polska", "brasil", "mexico",
    "en", "en-us", "en-gb", "de", "fr", "es", "it", "nl", "pt", "ja", "zh",
})


def _is_region_suffix(part):
    """True for "USA", "EMEA", "Deutschland" - a place, not a brand."""
    value = (part or "").strip().lower().strip(".")
    return value in _REGION_SUFFIXES or value.replace(".", "") in _REGION_SUFFIXES


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
    # "Home", in the languages the crawl actually meets. The comment on the
    # og:site_name split says a German government site's "Die Bundesregierung
    # informiert | Startseite" was fixed by splitting it - and splitting it and
    # then taking the shorter half returns "Startseite", which is German for
    # "home page". The bug the comment describes had never been fixed, only
    # moved. Everything structural in this file is language-independent; this
    # list is the one place a word has to be recognised, so it names them.
    return value in ("home page", "homepage", "official site", "official website",
                     "welcome", "start", "start page", "index", "main page",
                     "startseite", "hauptseite", "willkommen",
                     "accueil", "page d'accueil", "bienvenue",
                     "inicio", "pagina de inicio", "portada", "bienvenido",
                     "pagina iniziale", "benvenuto",
                     "homepagina", "welkom", "hem", "forside", "hjem",
                     "etusivu", "strona glowna", "glowna", "principal")


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


# A title that describes the thing before it names it: "The Programming
# Language Frostvane", "The Official Site of Acme". A language's documentation
# site titles its homepage exactly like that, with no separator and no
# boilerplate tail, and the whole 28-character sentence became the brand - in
# the report, in the paste-ready Organization `name` and in the definition
# the report asked the project to publish - for a project that signs every
# page of its own site with its one-word name.
#
# The name is only ever the end of such a title, and only once two things
# agree: the site's own address or its own copyright line spells that end, and
# what comes before it is description rather than part of the name. "Friends
# of Example" on `example.test` keeps its whole name, because "Friends of" is
# how the organisation is called and not a description of it; "The Official
# Site of Example" loses the words that describe the page.
_LEAD_PREPOSITIONS = frozenset({"of", "to", "for", "by", "at", "from"})
_LEAD_BOILERPLATE_WORDS = frozenset({
    "official", "site", "website", "home", "homepage", "page", "welcome"})

# Where a site signs its own pages: "Copyright © 1994–2026 Example.org,
# Example University." The holder is what follows the mark and the years, up to
# the end of that sentence.
_COPYRIGHT_MARK_RE = re.compile(
    r"(?:©|\(c\)|\bcopyright\b)(?:\s*(?:©|\(c\)))?\s*"
    r"(?:\d{4}(?:\s*[-–—]\s*\d{4})?[\s,]*)*", re.I)
_COPYRIGHT_HOLDER_END_RE = re.compile(r"\.\s|[;|©]|\ball rights\b", re.I)


def _is_a_descriptive_lead(lead):
    """True for "The Programming Language" and "The Official Site of"."""
    words = [w.lower() for w in re.findall(r"[^\W\d_]+", lead or "", re.UNICODE)]
    if not words:
        return False
    # "Friends of", "Town of", "University of" are how a name begins. Only a
    # lead that describes the page itself is dropped in front of a preposition.
    if words[-1] in _LEAD_PREPOSITIONS:
        return bool(_LEAD_BOILERPLATE_WORDS & set(words))
    # An article and at least two words of description. "The Home Depot" and
    # "The Guardian" are names whose article is part of them, and a lead of
    # one or two words is how those read.
    return words[0] == "the" and len(words) >= 3


def _names_the_site_signs_with(pages, home_url, domain_token):
    """Comparison keys of the names a site signs itself with.

    Its own address, and the holder named in its own copyright line. A line on
    one deep page is a reprint's credit as often as the site's own, so a
    holder counts once it appears on two pages or on the homepage.
    """
    keys = set()
    token = comparison_key(domain_token or "")
    if token:
        keys.add(token)
    seen = {}
    for page in pages or []:
        texts = {str(page.get("body_text") or ""), str(page.get("text") or "")}
        found = set()
        for text in texts:
            for match in _COPYRIGHT_MARK_RE.finditer(text):
                holder = _COPYRIGHT_HOLDER_END_RE.split(
                    text[match.end():match.end() + 80], maxsplit=1)[0]
                for piece in holder.split(","):
                    # "Example.org" is signed with its address; the name is
                    # the part before the suffix.
                    piece = re.sub(r"\.[a-z]{2,6}$", "", piece.strip(), flags=re.I)
                    key = comparison_key(piece)
                    if key:
                        found.add(key)
        for key in found:
            seen.setdefault(key, set()).add(page.get("url"))
    for key, urls in seen.items():
        if len(urls) >= 2 or (home_url and home_url in urls):
            keys.add(key)
    return keys


def _name_after_a_descriptive_lead(title, own_names):
    """"The Programming Language Frostvane" -> "Frostvane", or "".

    The shortest ending of the title that the site signs itself with, where
    everything before it is description. See the block above.
    """
    words = (title or "").split()
    for cut in range(len(words) - 1, 0, -1):
        tail = " ".join(words[cut:]).strip(" ,:;-–—")
        if (tail and comparison_key(tail) in own_names
                and _is_a_descriptive_lead(" ".join(words[:cut]))):
            return tail
    return ""


def _site_spelling(token, home):
    """The site's own spelling of a name the domain can only approximate.

    A hostname cannot carry an apostrophe, an ampersand or a space, so the
    domain token is a flattened version of a name the site writes properly on
    its own homepage: `zingermans` for "Zingerman's", `benjerry` for "Ben &
    Jerry's". Falling back to the domain is reasonable; publishing the
    flattened spelling back to the owner as the name they should put in their
    structured data is not, because the report's own subject is making the
    brand's identity unambiguous.

    The reading itself is `audit_common.name_the_site_spells`, which is where
    it now lives so that the crawl and the six skills that match names cannot
    answer differently about one site. This is the homepage-shaped wrapper
    around it: it says which two strings on the homepage count as the site
    writing its own name, and turns the shared function's "" into the `None`
    this file's caller reads.
    """
    if not home:
        return None
    spelled = name_the_site_spells(
        token, [home.get("title") or "", home.get("h1") or ""])
    return spelled or None


# --------------------------------------------------------------------------
# Other spellings of the same name
#
# One name is what this function used to produce, and one name is what every
# matcher downstream then had to satisfy. On a design studio's site it
# produced "<City> Premier Interior Designer" from the title and
# "<studioname>" from the bare domain label, and never tried the label split
# into the two words it is made of. The homepage's first sentence is
# "<Studio Name> is a full-service, client-focused design firm specialising in
# residential projects" - a definition of exactly the kind the report then said
# the site does not contain, missed because the name the matcher was given was
# the run-together label with no space in it. The same missing alias put the
# wrong name into the paste-ready markup on that site and sent an entity
# lookup after a string nothing is called.
#
# So the output is a list of candidate forms, each carrying how far it can be
# trusted, and the chosen name is untouched. A wrong alias is worse than no
# alias: a definition check handed one would accept a sentence about something
# else and report a pass the site has not earned. Every rule below therefore
# has to be corroborated by the site's own words before a candidate is offered
# at all.

# Prefixes a public body writes in front of the place it governs. The place is
# how everyone writes the body's name in a sentence, and the site writes both,
# so the short form is a real alias rather than a guess.
_CIVIC_PREFIX_RE = re.compile(
    r"^(?:the\s+)?(?:town|city|village|borough|county|district|township|"
    r"municipality|parish|commune|province)\s+of\s+", re.I)

# How many words a run-together domain label may be split into, and how short
# a piece may be.
#
# Two words is the ordinary case and three covers a name with a short middle
# word in it. Past that the number of ways to cut a string grows far faster
# than the evidence for any one of them, and the rule below - accept only when
# exactly one split works - would start rejecting real names for the wrong
# reason. The three-letter floor is what keeps a stray "in", "of" or "co" from
# being treated as a word of the name.
DOMAIN_SPLIT_MAX_WORDS = 3
DOMAIN_SPLIT_MIN_WORD = 3


def _words_the_site_writes(home):
    """{lowercase word: the site's own spelling of it} from its homepage.

    The vocabulary a domain split has to be justified against. Taken from the
    homepage's own words rather than from a dictionary, because the question is
    not whether a string is a word of some language - it is whether this site
    writes that word about itself. A dictionary would split a label into two
    words the site has never used; this cannot.

    The body text is read as well as the title and the headings, and it is the
    half that matters most: a studio whose title is its tagline writes its own
    name in the first sentence of its copy and nowhere in its markup, and a
    vocabulary built from headings alone had nothing to split its domain
    against. A larger vocabulary makes more labels ambiguous rather than more
    labels wrong - the caller declines a label that can be cut two ways - so
    reading more of the page fails safe.
    """
    out = {}
    if not home:
        return out
    texts = [str(home.get("title") or "")]
    headings = home.get("headings") or {}
    if isinstance(headings, dict):
        for level in ("h1", "h2", "h3"):
            value = headings.get(level)
            texts.extend([value] if isinstance(value, str) else [str(v) for v in (value or [])])
    texts.append(str((home.get("og") or {}).get("og:site_name") or ""))
    texts.append(str(home.get("body_text") or ""))
    for text in texts:
        for word in re.findall(r"[^\W\d_]+", text, re.UNICODE):
            if len(word) < DOMAIN_SPLIT_MIN_WORD:
                continue
            low = word.lower()
            # The site's own capitalisation wins over a later lower-case use of
            # the same word, so the alias is spelled the way the site spells it
            # rather than the way a sentence happened to.
            if low not in out or (word[:1].isupper() and not out[low][:1].isupper()):
                out[low] = word
    return out


def _domain_split_into_words(token, vocabulary):
    """The domain label rewritten as the words the site itself uses, or "".

    A hostname cannot carry a space, so a two-word name reaches this file as
    one run of letters. Splitting it needs no dictionary: the pieces have to be
    words the homepage already writes about itself, which is a far stronger
    test than being words of some language.

    Accepted only when exactly one split works. Where two do, the letters are
    genuinely ambiguous and there is no evidence here to choose between them -
    and offering the wrong one is worse than offering none, because a matcher
    given it would accept a sentence about a different subject.
    """
    key = re.sub(r"[^a-z0-9]", "", (token or "").lower())
    if len(key) < DOMAIN_SPLIT_MIN_WORD * 2 or not vocabulary:
        return ""
    found = []

    def walk(rest, parts):
        if len(found) > 1:
            return
        if not rest:
            if len(parts) > 1:
                found.append(list(parts))
            return
        if len(parts) >= DOMAIN_SPLIT_MAX_WORDS:
            return
        for end in range(DOMAIN_SPLIT_MIN_WORD, len(rest) + 1):
            piece = rest[:end]
            if piece in vocabulary:
                parts.append(vocabulary[piece])
                walk(rest[end:], parts)
                parts.pop()

    walk(key, [])
    return " ".join(found[0]) if len(found) == 1 else ""


def _name_candidates(chosen_name, home, domain_token):
    """Other forms of this site's name, each with how far it can be trusted.

    Offered alongside the chosen name, never instead of it. `confidence` is
    what a matcher gates on: "high" means two independent facts had to agree
    before the form was produced, "medium" means one did.
    """
    out = []
    split = _domain_split_into_words(domain_token, _words_the_site_writes(home))
    if split and split.lower() != (chosen_name or "").lower():
        out.append({
            "name": split, "confidence": "high",
            "source": "the domain label split into words the site's own "
                      "headings and title spell out",
        })
    without_prefix = _CIVIC_PREFIX_RE.sub("", chosen_name or "").strip()
    if without_prefix and without_prefix != (chosen_name or "").strip():
        out.append({
            "name": without_prefix, "confidence": "medium",
            "source": "the name with the civic prefix removed, which is the "
                      "place the body is named for",
        })
    return out


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
    branches = is_multi_location({"pages": pages})

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
            # The shared reader, not a hand-rolled copy of it. The copy split
            # on `/` only, so a site declaring `schema:Organization` - correct,
            # and accepted by Google - matched nothing and its declared name
            # was never read; and `for t in 5` ended the crawl with a
            # TypeError on a site with a numeric `@type`.
            names = jsonld_type_names(node.get("@type"))
            if not names & (ORG_IDENTITY_TYPES | VISITABLE_JSONLD_TYPES):
                continue
            # A branch name is not a spelling of the brand. A restaurant group
            # declares one node per restaurant, named for where it is - "Citi
            # Field, NYC", "Dubai, Mall of the Emirates" - and the naming check
            # then reported the brand as "written 13 different ways" and advised
            # picking one and using it everywhere. There is nothing to fix: the
            # site is naming thirteen restaurants.
            if branches and names & VISITABLE_JSONLD_TYPES:
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
        raw_site_name = page.get("og", {}).get("og:site_name")
        site_name = clean(raw_site_name)
        # A template whose brand slot came out empty. Every page of one site
        # carries `<meta property="og:site_name" content="- Empowering
        # communities">`: the theme writes `"<brand> - <tagline>"` and the
        # brand is missing, so what is left is the tagline with the separator
        # still in front of it. `clean` strips that separator as stray
        # punctuation, and the tagline was then adopted as the company's name -
        # reaching the verdict, four fix templates, a Wikidata lookup and a
        # paste-ready `Organization` snippet.
        #
        # Tested on the raw value, before `clean` runs, because the leading
        # separator is the entire evidence and cleaning removes it. Only the
        # leading position: `"Acme -"` has lost its tagline rather than its
        # name, and rejecting that would throw a good name away.
        if site_name and the_brand_slot_is_empty(raw_site_name):
            site_name = ""
        if site_name:
            # Plenty of sites set og:site_name to the whole page title,
            # separator and all: "Die Bundesregierung informiert | Startseite"
            # became the brand, and then appeared inside generated fix text as
            # if it were a company name.
            #
            # Shortest-wins is the wrong rule on its own. An international
            # charity sets og:site_name to "Doctors Without Borders - USA", so
            # the shortest piece is "USA" - and that became the brand for the
            # whole report, the subject of two findings about a name collision
            # that does not exist, and the `name` field of a paste-ready
            # Organization snippet. A report about making a brand's identity
            # unambiguous told a medical charity its own name was "USA".
            #
            # A national or regional suffix is not the brand, and neither is
            # boilerplate. Drop both first; shortest-wins is only the tiebreak
            # between two halves that are both real.
            # But a separator inside `og:site_name` is not automatically a
            # title. It is a name with punctuation in it far more often than
            # not, and splitting unconditionally cut a charity's own
            # "charity: shelter" down to "water" - which became the brand for the
            # whole report, sent a Wikidata search for the word "water", and
            # produced a high-severity finding that "4 other entities share the
            # name". The name is on the page, in the tag whose entire job is to
            # state it.
            #
            # So the split needs a reason: either the value is too long to be a
            # name at all - the case the rule was written for, a page title
            # pasted into the field - or one of its halves is boilerplate or a
            # region suffix, which is the evidence that the other half is the
            # name. "charity: shelter" is neither. "Die Bundesregierung
            # informiert | Startseite" is 43 characters. "Doctors Without
            # Borders - USA" ends in a region.
            #
            # And the colon is not one of the separators this splits on. A
            # leather workshop sets `og:site_name` to "The Bicyclist: Handmade
            # Leather Goods" on all 58 of its pages and declares the identical
            # string as its Organization `name`. The second piece is 22
            # characters, two over `SITE_NAME_PIECE_MAX`, so the value was cut
            # and "The Bicyclist" was filed under `declared_by` as something
            # the site had asserted - a string that appears on no page of it.
            # Two high-severity findings then told the site its markup
            # contradicted its own pages, which agree exactly.
            #
            # `audit_common._TITLE_SEPARATOR_RE` had already reached this
            # answer for the same reason - "charity: shelter" is one name -
            # and two files disagreeing about whether a colon separates is the
            # drift this codebase argues against everywhere. A colon inside
            # the tag whose whole job is to state the site's name is
            # punctuation in that name far more often than it is a separator;
            # the shapes this split exists for are written with a bar or a
            # spaced dash.
            published = site_name
            pieces = [clean(x) for x in SITE_NAME_SEPARATOR.split(site_name)]
            pieces = [x for x in pieces if x]
            named = [x for x in pieces
                     if not _is_title_boilerplate(x) and not _is_region_suffix(x)]
            # What separates a name from a title is how long its parts are.
            # A name's pieces are words - "charity", "water", "Doctors Without
            # Borders". A tagline is a clause: "Wheel-thrown stoneware from
            # York", "Online Shopping for Food and Gifts", "Expert testing,
            # reviews and advice". A boilerplate or region piece is the other
            # kind of evidence, and either is enough.
            looks_like_a_title = (any(len(p) > SITE_NAME_PIECE_MAX for p in pieces)
                                  or len(named) < len(pieces))
            if len(pieces) > 1 and looks_like_a_title:
                site_name = min(named or pieces, key=len)
            # `name` is the reading - which half of the value to call the site
            # by. `published` is the string the site actually put in the tag,
            # and it is what every "the site declared this" field below is
            # built from. Recording the half under `declared_by` is the
            # extractor inventing an assertion nobody made, and it is what
            # produced the finding above: one declared name became two rivals,
            # neither pair of which any page carries.
            authoritative.append({"name": site_name, "source": "og:site_name" + suffix,
                                  "published": published})
            declared_on.setdefault(published, set()).add(page.get("url"))

    host = urlparse(origin).netloc.lower()
    domain_token = re.sub(r"^www\.", "", host).split(".")[0]
    # Read before the title is, because the title is where it is needed: which
    # piece of "Brand | Tagline", and which ending of a title with no
    # separator, is the name the site signs itself with.
    own_names = _names_the_site_signs_with(pages, home_url, domain_token)

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
            # A piece the site also signs itself with - its address, its
            # copyright line - is the name whatever its length. Shortest-wins
            # is the tiebreak between pieces with no such evidence, and it
            # hands the name to a short tagline on "Eat Well | Example's".
            ordered = sorted(named or ends,
                             key=lambda p: (comparison_key(p) not in own_names, len(p)))
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
            after_lead = _name_after_a_descriptive_lead(parts[0], own_names)
            if after_lead:
                fallback.append({"name": after_lead, "source": "title-part-0"})
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

    # "The Postfix Home Page" loses its boilerplate and keeps its article, so a
    # mail server was addressed throughout its report as "The Postfix": in the
    # paste-ready Organization `name`, in the definition the report told it to
    # publish, and in the Wikidata search, which found nothing under that
    # string for a project that has an item. `name_forms` already derives the
    # article-free form; nothing asked it for one. The domain is what justifies
    # the choice - a site whose address is `the-something.test` keeps its
    # article, and one whose address spells the name without it does not.
    fallback = [_without_a_borrowed_article(c, domain_token) for c in fallback]
    # A candidate that spells out the site's own address is labelling the page,
    # not naming the brand. A university department's legacy host has no
    # Organization markup and no `og:site_name` on its homepage, its
    # `<title>` is "Sitemap for www.example.edu", and that string became the
    # brand for the whole report: the identity snippet offered the department
    # `"name": "Sitemap for www.example.edu"`, the definition check hunted
    # every page for a sentence beginning with it, and the suggested H1 was
    # the same string. A name is not a name if it contains the hostname it is
    # supposedly the name of.
    fallback = [f for f in fallback if not _names_the_address(f["name"], host)]
    # A name declared only under one directory of a multi-directory site is
    # that directory's, not the site's.
    #
    # A university department's legacy host gives every academic a directory
    # and no identity markup of its own. One person's page sets `og:site_name`
    # to their own name, that was the only such declaration anywhere, and it
    # outranked the domain - so the whole report addressed the department by
    # one professor's name, offered it as the `name` of a paste-ready
    # Organization block, and suggested that string as the site's H1. This is
    # the same rule the naming-consistency check applies one level down: a
    # section names itself, and only the site names the site.
    authoritative = [c for c in authoritative
                     if not _confined_to_one_directory(c, declared_on, pages, home_url)]
    fallback = [c for c in fallback
                if not _confined_to_one_directory(c, declared_on, pages, home_url)]
    host_brand = domain_token.replace("-", " ").strip()
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

    # Other forms of the same name, derived rather than declared. See the block
    # above `_name_candidates`: the chosen name is not touched, and only the
    # corroborated ones join the guesses the rest of the audit already reads.
    candidates = _name_candidates(chosen["name"], home, host_brand)
    guessed = {c["name"] for c in fallback}
    guessed.update(c["name"] for c in candidates if c["confidence"] == "high")

    return {
        "name": chosen["name"],
        "source": chosen["source"],
        # The strings the site published, never the halves this function read
        # out of them. See the `published` note in the og:site_name branch.
        "authoritative_variants": sorted({_as_declared(c) for c in authoritative}),
        "authoritative_sources": sorted({c["source"] for c in authoritative}),
        "alternate_names": sorted(alternates),
        "fallback_candidates": sorted(guessed),
        # Kept apart from `fallback_candidates` as well as folded into it,
        # because a matcher that wants to know how far a form can be trusted
        # cannot read that off a flat list of strings. `alternate_names` is
        # deliberately not where these go: that field means "names the site
        # declares as its own", and a derived spelling is not a declaration.
        "name_candidates": candidates,
        # Which pages asserted each name. Without this a naming-inconsistency
        # finding lists no pages at all, and a finding with no pages is scored
        # as affecting the whole site - so a disagreement visible on one page
        # out of sixty outscored a critical crawler block on the same report.
        "declared_on": {name: sorted(urls) for name, urls in sorted(declared_on.items())},
        # Which source declared each name, not just which sources were used.
        # Without it the naming finding could say "sources read: Organization
        # JSON-LD and og:site_name" and then quote two spellings without being
        # able to say which came from where - and on one site neither named
        # source had produced the second spelling at all.
        "declared_by": {
            name: sorted({c["source"] for c in authoritative if _as_declared(c) == name})
            for name in sorted({_as_declared(c) for c in authoritative})},
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


# Labels a registry uses to group registrations rather than to name a
# registrant. Nobody buys `co` in `example.co.uk`: `example` is the name and
# `co.uk` is where names of that shape are sold. These are registry
# conventions rather than a list of countries - every label here is used
# under many different suffixes, and none of them is ever a company's name.
_REGISTRY_GROUPING_LABELS = frozenset({
    "co", "com", "net", "org", "edu", "gov", "mil", "int", "ac", "sch",
    "or", "ne", "go", "gob", "gouv", "nom", "biz", "info", "web", "ltd", "plc",
})


def registrable_name(host):
    """The name a registrant bought: `example` in `www.example.co.uk`.

    No public-suffix list ships with this tool, and downloading one in the
    middle of a crawl would put a third-party dependency inside a read-only
    audit, so the suffix is peeled off by shape: the last label always belongs
    to a registry, and a grouping label beneath it does too. What is left on
    the right is the name.

    Only ever asked one question - are these two hostnames the same name at a
    different address - and the way it can be wrong is by peeling one label
    too few, which makes it answer "no" and leaves a redirect unfollowed. That
    is the safe direction to be wrong in: an audit that stops and says so can
    be re-run against the right address, an audit of another company's website
    published under this company's name cannot be taken back.
    """
    host = strip_www((host or "").lower())
    # An address is not a name. `192.0.2.1` and `192.0.2.9` share every label
    # shape there is and belong to nobody in common, and an IPv6 literal has
    # no labels at all, so both are compared whole or not at all.
    if not host or ":" in host or host.replace(".", "").isdigit():
        return host
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return ".".join(labels)
    labels = labels[:-1]
    while len(labels) > 1 and labels[-1] in _REGISTRY_GROUPING_LABELS:
        labels.pop()
    return labels[-1]


def _same_registered_name(origin_a, origin_b):
    """Are these two origins the same registered name at different addresses?"""
    name = registrable_name(urlparse(origin_a).hostname or "")
    return bool(name) and name == registrable_name(urlparse(origin_b).hostname or "")


def _settle_origin(fetcher, origin, seed_url, notes):
    """Find the host that actually serves the address this audit was given.

    Returns `(origin, seed_url, off_site_redirect_record_or_None)`.

    Three different things hide behind a redirect of the seed URL and they
    need three different answers.

    A move inside the same host family - the bare domain to `www`, or back -
    is one site with two names. A retailer redirects `/` from the bare domain
    to `www` and answers 404 for `/robots.txt` there, so robots.txt and the
    sitemap were both read from a host that has neither and the report's top
    finding was "No XML sitemap is available" about a site with a valid
    sitemap index one hop away. Adopt the host that answers, quietly.

    A move to a different hostname carrying the same registered name is the
    site saying where it lives. Trading under one name from a country or
    regional domain, with the older domain redirecting to it, is the ordinary
    arrangement for a brand that sells in more than one market - and refusing
    it produced a crawl of exactly one page, no content read at all, 64 of 72
    checks marked not-applicable, and a headline verdict of "no blocking or
    significant problems were found" about a site this audit had never seen.
    Adopt the destination, and record the move so that no reader can mistake
    it for the address they asked about.

    A move to a different name is somebody else's site, refused here for the
    reason `fetch_page` refuses one mid-crawl: a standards body's `/go/` route
    filed seventeen of another organisation's pages as this site's own and
    then took the brand name from one of them.

    So the line between following and refusing is the registered name, not the
    hostname and not the suffix: the same name at another address is a site
    that moved, a different name is a different owner. And the line between
    this redirect and one met later in the crawl is that this one leaves from
    the address the audit was pointed at - the only URL whose destination the
    person who asked for the audit has vouched for. A `Location` header found
    on page forty vouches for nothing.
    """
    requested_url = seed_url or (origin.rstrip("/") + "/")
    response = fetcher.try_get(requested_url, method="HEAD")
    final = getattr(response, "url", None) if response is not None else None
    # A host that refuses HEAD has told us nothing about where a GET lands.
    # One retailer measured for this answers 403 to every HEAD and 200 to
    # every GET, and on a host like that the probe reports the seed staying
    # where it is - which is exactly how a domain that redirects to another
    # of its owner's would go back to being read as a single unreadable page.
    # The second request is spent only when HEAD was refused and only when it
    # also showed no move, so an ordinary site never pays for it.
    status = getattr(response, "status_code", None)
    if status is not None and status >= 400 and (
            not final or origin_of(final) == origin):
        retried = fetcher.try_get(requested_url)
        if retried is not None:
            response = retried
            final = getattr(response, "url", None) or final
    if not final:
        return origin, seed_url, None
    settled = origin_of(final)
    if not settled or settled == origin:
        return origin, seed_url, None
    settled_seed = normalise_url(final) or (settled.rstrip("/") + "/")

    if same_site(settled, origin):
        # The same host under a different name or scheme. Moving is only worth
        # it when the two spell the same host: a redirect that changes nothing
        # but the port is not a site telling us where it lives.
        if strip_www(urlparse(settled).netloc.lower()) != \
                strip_www(urlparse(origin).netloc.lower()):
            return origin, seed_url, None
        notes.append(
            "The homepage redirects to {}, so robots.txt, the sitemap and every page "
            "were read from there. Auditing the host that answers is the only way to "
            "see what the site actually serves.".format(settled))
        return settled, settled_seed, None

    # Off this origin entirely: the case every reader of the report has to be
    # told about, whichever way it is decided.
    record = {
        "requested_origin": origin,
        "requested_url": requested_url,
        "final_url": final,
        "final_origin": settled,
        "status": getattr(response, "status_code", None),
        "same_registered_name": _same_registered_name(origin, settled),
        "followed": False,
        "reason": "",
    }
    requested_host = site_label(origin)
    settled_host = site_label(settled)
    if not record["same_registered_name"]:
        record["reason"] = (
            "The address this audit was asked about, {}, answers with a redirect to {}, "
            "which is a different site under a different registered name. Following it "
            "would have described somebody else's pages under this heading, so it was "
            "not followed and no page of {} was read: this audit cannot say whether {} "
            "is ready for AI assistants or not.".format(
                requested_url, final, requested_host, requested_host))
        return origin, seed_url, record
    # A sign-in or account address is never read, and a redirect is the way
    # this audit reached one before: a `/calendar` route answering 302 to
    # `/login` was audited as a page. An apex domain pointing at a customer
    # portal is the same thing one hop earlier, and the destination being the
    # right company's does not make it a page anyone should be audited on.
    if is_forbidden_path(settled_seed):
        record["reason"] = (
            "The address this audit was asked about, {}, answers with a redirect to {}, "
            "which is a sign-in or account address. This audit never reads those, so no "
            "page of {} was read.".format(requested_url, final, requested_host))
        return origin, seed_url, record
    record["followed"] = True
    record["reason"] = (
        "The address this audit was asked about, {}, answers with a redirect to {}: a "
        "different hostname carrying the same registered name, which is the site saying "
        "where it lives. {} was audited instead - robots.txt, the sitemap, the brand name "
        "and every page below come from there. Nothing below is a measurement of "
        "{}.".format(requested_url, final, settled_host, requested_host))
    return settled, settled_seed, record


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


# ---------------------------------------------------------------------------
# How many pages the crawl reads, and why no clock and no stopwatch decides it
# ---------------------------------------------------------------------------
#
# Two separate pairs of runs of this tool against one unchanged site each gave
# two different reports. The first pair gave 11 findings and then 13. The
# second gave 53 pages / 16 findings and then 60 pages / 19, on the same
# machine with nothing else running. Two people running the same audit got two
# different reports and could not tell which findings moved.
#
# The second pair of numbers names the cause exactly. This file used to plan
# its page count as `budget x 0.75 / rung(measured cost of a page)`, over a
# ladder of rungs doubling from 0.25s. Against the 142s crawl budget that is:
#
#     rung 1.0s -> int(106.5 / 1.0) = 106, capped at the 60-page ceiling -> 60
#     rung 2.0s -> int(106.5 / 2.0) =  53                                -> 53
#
# 53 and 60 are not two readings of a site. They are the two sides of one rung
# boundary, and the site sat on it: the cost was the median of the first five
# pages, which on that site came out just under a second on one run and just
# over on the other. Both runs then finished with 40 seconds of the budget
# unspent, which is why neither was ever cut by the clock.
#
# So the ladder did not remove the defect it was written for, it moved it. A
# page count computed from a measured duration is a page count computed from
# the network, and rounding a measured duration only widens the band inside
# which two runs agree - it cannot remove the boundary, and a real site can
# sit on one. That is the whole finding, and it is why nothing below measures
# how long a page took.
#
# What decides the count now is declared before the crawl starts:
#
#   --max-pages          the ceiling, a constant (`MAX_PAGES`)
#   Crawl-delay          when robots.txt asks for a wait longer than the
#                        default, the count the budget can pay for at that
#                        wait. The wait is published by the site, so it reads
#                        the same on every run - it is not a measurement.
#
# The sequence those pages are taken from was already fixed: `KindSpreadQueue`
# serves a determined order, `sample_sitemap_urls` samples under a fixed seed,
# and in-page links are expanded sorted. A page set is a prefix of that
# sequence, and the length of the prefix is now declared, so two runs of one
# unchanged site read the same pages.
#
# The wall clock remains, as a backstop and nothing else. Where it fires the
# crawl really did read a set of pages this minute's network chose, and the
# snapshot says so rather than leaving two reports to be found side by side:
# `crawl.page_plan.deterministic` is false, `not_reproducible_because` gives
# the reason in a sentence, and `truncated_at` names the address it stopped
# before so two truncated runs can be compared.
#
# What this costs, stated plainly rather than left to be discovered: a site
# slower than `budget / max_pages` a page used to have its sample cut to a
# planned size and finish inside the budget; it now reads until the clock stops
# it and is marked non-reproducible. That is a real loss on a slow site, and it
# buys reproducibility - on a site the budget can pay for, which is every site
# either pair of runs measured, the answer repeats exactly.

# Seconds per page, doubling. Used on a `Crawl-delay` a site publishes, never
# on a duration this crawl measured: rounding the declared wait up gives the
# response itself room inside the budget, so the count stays payable without
# the clock having to catch it.
PAGE_COST_RUNGS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)

# The share of the wall-clock budget the page loop may plan to spend. The rest
# pays for robots.txt, the sitemaps, the two agent files, the homepage retry,
# the HEAD probe and the rendered pass, which all come out of the same budget.
# Measured on the fixtures, that preamble is a second or two; a quarter of the
# budget is slack, and slack is what stops the backstop firing.
PAGE_BUDGET_SHARE = 0.75


def cost_rung(seconds):
    """Round a seconds-per-page figure up onto `PAGE_COST_RUNGS`.

    Up, never down, so a count cannot promise more pages than the budget pays
    for. Anything slower than the last rung is treated as the last rung; a site
    costing more than 32s a page has a budget for one or two pages either way.
    """
    for rung in PAGE_COST_RUNGS:
        if seconds <= rung:
            return rung
    return PAGE_COST_RUNGS[-1]


def pages_a_budget_buys(budget_s, cost_s, ceiling):
    """How many pages a budget pays for at a stated cost per page.

    A pure function of its three arguments - no clock is read - and every
    caller passes a `cost_s` the site declared rather than one this crawl
    timed, which is what makes the answer the same on two runs.
    """
    return max(1, min(int(ceiling),
                      int((budget_s * PAGE_BUDGET_SHARE) / cost_rung(cost_s))))


def crawl(target, out_path, max_pages=MAX_PAGES, budget_s=WALL_CLOCK_BUDGET,
          respect_robots=True, delay=REQUEST_DELAY, render="auto"):
    origin, seed_url = normalise_target(target)
    # The address as it was given, kept before anything can move it. A crawl
    # can change scheme and can change host, and after either the snapshot had
    # no record of what was asked for.
    requested_origin, requested_seed_url = origin, seed_url
    started = time.monotonic()
    deadline = started + budget_s
    # What set the number of pages this crawl was allowed to read. Both values
    # are known before a page is fetched, which is the property the whole of
    # `PAGE_COST_RUNGS`' comment is about: neither is a duration this run
    # timed, so neither can differ between two runs of one unchanged site.
    page_cap = max_pages
    page_cap_source = "the --max-pages ceiling"
    # Why this run's page set may not repeat, in sentences, empty on a crawl
    # that read the pages it set out to read. `page_plan.deterministic` is this
    # list being empty. Declared here rather than beside the page loop because
    # the sitemaps decide the candidate URLs and are fetched before it, so a
    # sitemap this run failed to reach has already changed the page set by the
    # time the loop starts.
    not_reproducible = []
    fetcher = PageFetcher(delay=delay, timeout=REQUEST_TIMEOUT, deadline=deadline)

    notes = []
    origin, seed_url, downgraded = _resolve_scheme(fetcher, origin, seed_url, target)
    if downgraded:
        notes.append(
            "https did not answer, so the site was audited over http. That is itself a "
            "finding and is reported as one; the alternative was to return nothing."
        )
    robots = fetch_robots(fetcher, origin)
    # One HEAD, spent whenever this host answered anything at all.
    #
    # It used to be spent only when robots.txt came back as something other
    # than 200, on the reasoning that a host serving robots.txt is the host
    # serving the site. That is not true of a domain that redirects: an apex
    # that answers 301 to a country domain hands over `/robots.txt` along with
    # everything else, so robots.txt looked healthy, the homepage was recorded
    # as "redirects off this origin", and the audit read nothing at all.
    #
    # Which host serves the site is also the question that has to be settled
    # before robots.txt can be obeyed rather than merely fetched - the rules
    # that bind this crawl are the ones published by the host it is about to
    # read. A host that did not answer robots.txt is still never probed: on a
    # dead origin every speculative request costs the retry budget twice, and
    # that is what once spent a whole twenty-second budget before the homepage
    # was ever requested.
    origin_redirect = None
    if robots.get("status") is not None:
        settled, settled_seed, origin_redirect = _settle_origin(
            fetcher, origin, seed_url, notes)
        if origin_redirect is not None:
            notes.append(origin_redirect["reason"])
        if settled != origin:
            origin, seed_url = settled, settled_seed
            # Everything derived from the origin is re-derived here rather
            # than carried over: robots.txt and the sitemaps below, the
            # `/llms.txt` addresses, `home_url`, every `same_site` comparison
            # in `crawlable`, the brand name `detect_brand` reads off the
            # host, and the `site` and `origin` fields of the snapshot. The
            # first version of this re-based the crawl and kept the old
            # robots.txt, which is a set of rules from a host no page was
            # read from.
            robots = fetch_robots(fetcher, origin)
    # A `Crawl-delay` the site sets is an instruction, and this crawler parsed
    # it, merged it strictest-wins across groups, printed it in a report
    # sentence, and then ignored it - every request went out at the fixed half
    # second. Honouring it is the whole difference between reading robots.txt
    # and obeying it. Bounded, because a site asking for 30 seconds a page
    # would otherwise spend the entire budget on two pages, and the note says
    # so rather than silently capping.
    asked = (group_for(robots, USER_AGENT) or {}).get("crawl_delay")
    if asked and asked > delay:
        fetcher.delay = float(asked)
        # In full, and the sample shrinks to fit. Capping the delay at 5s was
        # this audit spending someone else's bandwidth at twice the rate they
        # asked for, to hit its own deadline - on a site whose robots.txt says
        # `Crawl-delay: 10`, which is the site declining to be read that fast.
        # "Respect robots.txt" is a hard constraint, and `Crawl-delay` is part
        # of robots.txt. What gives instead is how many pages get read, and the
        # report says so rather than quietly reading fewer.
        # From the budget, not from what is left of it. `(deadline - now)`
        # made this number depend on how long robots.txt and the sitemaps had
        # taken to arrive, so a site asking for `Crawl-delay: 7` could be
        # sampled at 27 pages on one run and 26 on the next - the same defect
        # as the measured page count, a hundred lines earlier in the run.
        affordable = pages_a_budget_buys(budget_s, float(asked), max_pages)
        if affordable < max_pages:
            max_pages = affordable
            page_cap = affordable
            page_cap_source = (
                "the Crawl-delay of {:.0f}s robots.txt asks for, divided into the "
                "budget".format(float(asked)))
            notes.append(
                "robots.txt asks for {:.0f}s between requests. That is honoured in full, so "
                "this crawl could read at most {} page(s) inside its budget instead of the "
                "usual {}: the sample is smaller because the site asked for a slower "
                "crawl.".format(float(asked), affordable, MAX_PAGES))
        else:
            notes.append(
                "robots.txt asks for {:.0f}s between requests, which this crawl "
                "honoured.".format(float(asked)))

    sitemaps, sitemap_in_robots = fetch_sitemaps(fetcher, origin, robots, deadline)
    # Everything the page set depends on that this run did not get to see the
    # same way a second run would. Both of these happen before a page is
    # fetched, and both change which addresses are candidates, so a crawl that
    # hit either is not reproducible however cleanly its page loop then ran.
    if getattr(fetcher, "sitemaps_cut_by_clock", False):
        not_reproducible.append(
            "the wall clock stopped the sitemap fetch before every sitemap had been read, "
            "so the list of candidate addresses is shorter than it would be on a run that "
            "read them all")
    for record in sitemaps:
        if record.get("fetch_failed"):
            not_reproducible.append(
                "{} could not be reached on this run, so the addresses it lists were not "
                "candidates".format(record["url"]))

    # Both addresses, because the report recommends both. It asked the owner to
    # publish `/llms.txt` and `/llms-full.txt` while only ever looking for the
    # first, so the recommendation could not know whether half of it was
    # already done.
    #
    # Every other fetch in this crawl passes through `crawlable`, which asks
    # robots.txt first. These did not, so a site that disallows them was
    # fetched anyway - a small breach of the one promise the marketplace makes
    # about its own behaviour, and the easiest one to check.
    agent_files = {}
    for path in ("/llms.txt", "/llms-full.txt"):
        record = {"url": origin.rstrip("/") + path, "present": False, "status": None}
        # The second address is only worth a request when the first got an
        # answer. On a host that is refusing connections every speculative
        # fetch costs the retry budget twice, and adding one unconditionally
        # spent a dead site's whole twenty seconds before the homepage was
        # ever requested - the report came back with no pages and no critical
        # finding about a site that is down. The same lesson the sitemap index
        # probe learned.
        first = agent_files.get("/llms.txt")
        if first is not None and first.get("status") is None:
            record["unchecked_reason"] = (
                "this host did not answer /llms.txt, so a second address was not requested")
        elif (respect_robots and robots.get("groups")
                and is_disallowed(robots, USER_AGENT, path)):
            record["unchecked_reason"] = (
                "robots.txt disallows {} for this auditor".format(path))
        else:
            probe = fetcher.try_get(record["url"], allow_redirects=False)
            if probe is not None:
                body = response_text(probe)
                record["status"] = probe.status_code
                # What the server said it sent, kept whatever this crawl
                # decides, so a check reading this record can see the evidence
                # rather than only the verdict.
                record["content_type"] = (
                    (getattr(probe, "headers", None) or {}).get("content-type") or "")
                record["body_bytes"] = len(probe.content or b"")
                # A 200 is not a file. This address answering with the site's
                # homepage is the commonest shape there is - a server with a
                # catch-all route hands the same page back for every path it
                # does not recognise - and the old test read 400 characters
                # looking for `<html` and consulted the `Content-Type` not at
                # all. So a temple whose server answers every unknown path with
                # a 1.8 MB `text/html` homepage was recorded as publishing
                # `/llms.txt`, and the report went on to read that homepage as
                # the file's contents.
                record["present"] = (probe.status_code == 200
                                     and not response_is_an_html_page(probe))
                # The file itself, not only that it exists. `/llms.txt` is a
                # site telling machines what it is and where its things live,
                # so an account named there is declared by the site as its own
                # - the strongest kind of evidence there is. The crawl fetched
                # the file, read a few hundred characters of it to decide
                # whether it was HTML, and threw the rest away; a report then
                # carried, as its only high-severity finding, "the site links
                # to no off-site profile at all" about a site whose
                # `/llms.txt` names its public repository.
                #
                # Kept only when the file is really a file - which is what the
                # test above now decides properly - and capped: this is
                # evidence to read, not a copy of the site. A homepage kept
                # here would be read as the site's own declaration of its
                # accounts, which is the strongest evidence this audit has.
                if record["present"]:
                    record["text"] = body[:AGENT_FILE_TEXT_MAX]
        agent_files[path] = record
    llms_txt = agent_files["/llms.txt"]
    llms_full_txt = agent_files["/llms-full.txt"]

    # If our own polite user agent is shut out of the homepage, stop. Auditing
    # a site that has asked us not to read it would break the read-only,
    # robots-respecting contract this marketplace advertises.
    home_url = origin.rstrip("/") + "/"
    audit_blocked = bool(
        respect_robots and robots.get("groups")
        and is_disallowed(robots, USER_AGENT, "/")
    )

    pages = []
    # One queue, which serves each kind of page in turn. See `KindSpreadQueue`
    # for what a sample that covers only the deepest kinds costs.
    queue = KindSpreadQueue()
    queued = set()
    fetched_final = set()
    # Canonical identity -> the address of the one page read for that group.
    canonical_groups = {}
    skipped = []
    # Records that could have been pages, which is what `max_pages` bounds.
    page_slots_used = 0
    # Set only when the wall clock ran out - the one thing the name says, and
    # nothing else. It used to be written as `budget_exhausted or queue`, so
    # every crawl that reached its page ceiling with addresses still waiting -
    # which is every crawl of a site larger than sixty pages - published
    # `budget_exhausted: true`: two runs both reported it against a 142s
    # budget they never spent. Whatever
    # else ends a crawl now has its own name in `stopped_by`, and the fact both
    # readers downstream actually want - that the crawl stopped with pages of
    # this site still unread - is `stopped_early`.
    budget_exhausted = False
    # Which of the exits below ended the page loop, set by that exit rather
    # than inferred afterwards from what the queue looks like now.
    stopped_by = "nothing left to fetch"
    truncated_at = None
    # Everything the phases after the page loop establish, at the value a
    # snapshot written before they ran must carry. `head_supported` is `None`
    # rather than `False` because the three link-verification checks read
    # `None` as "not determined" and report their targets as unchecked, which
    # is what a crawl stopped mid-loop actually knows.
    render_mode = "static"
    head_supported = None
    site_not_serving = None

    # The snapshot, built wherever the crawl has got to.
    #
    # This used to be a dictionary literal at the end of the function, reached
    # only by a crawl that finished, so a crawl stopped by its parent left
    # either no file or half of one - and the orchestrator's handling of a
    # partial crawl, which reads whatever pages were fetched and says the
    # crawl was cut short, could almost never fire.
    #
    # That mattered once the run started reserving wall clock for the six
    # sub-skills ahead of the crawl rather than after it. The reservation is
    # only real if the crawl can be stopped at the boundary, and stopping it
    # was throwing the whole run away. Now it is not: `_write_checkpoint`
    # leaves a readable snapshot on disk as the pages arrive, so a crawl cut
    # at the boundary costs pages and no skill at all.
    #
    # A checkpoint is honest about being one. The pages in it have not been
    # through `_strip_sitewide_boilerplate`, no rendered pass has run and HEAD
    # support is unmeasured, and `CHECKPOINT_NOTE` says so in the same list of
    # crawl notes the report already prints in its appendix.

    def _snapshot_now(checkpoint=False):
        """The whole snapshot, from wherever the enclosing crawl has got to.

        `checkpoint` marks a snapshot written before the phases after the page
        loop had run, which is the only state in which it is ever read.
        """
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "site": site_label(origin),
            "origin": origin,
            "seed_url": seed_url,
            # What was asked for, next to what was audited. `site` and `origin`
            # name the host the pages below actually came from, which is the only
            # honest thing for them to name once a redirect has moved the crawl -
            # and it leaves nothing saying what the person typed. These two do,
            # on every snapshot, so a report can always show both.
            "requested_origin": requested_origin,
            "requested_seed_url": requested_seed_url,
            # Null unless the address this audit was pointed at redirected off its
            # own origin. When it is set, `followed` says which happened:
            #   true  - the destination carried the same registered name, the
            #           crawl moved to it, and everything in this snapshot
            #           describes `final_origin` rather than `requested_origin`.
            #   false - the destination was somebody else's site or a sign-in
            #           address, nothing was followed, and no page of the
            #           requested origin was read. A snapshot in that state
            #           carries no evidence about the site and cannot support a
            #           verdict of any kind, clean one included.
            # `reason` is a plain sentence saying which, already present in
            # `crawl.notes`.
            "origin_redirect": origin_redirect,
            # Null unless every address this crawl reached answered with a status
            # that speaks for the site rather than for the address - see
            # `NOT_SERVING_STATUS`. When it is set:
            #   status              the status every reached address answered
            #   meaning             what that status says, in plain words
            #   addresses           the addresses that answered it
            #   addresses_answered  how many answered at all, so a reader can see
            #                       how much the claim rests on
            #   reason              a plain sentence, already in `crawl.notes`
            # A snapshot in that state holds no evidence about what the site
            # publishes, and this is the largest true thing in it: a report has
            # nothing to say about headings, markup or wording on a site that is
            # not serving any, and leading with anything else describes a site that
            # is not there.
            "site_not_serving": site_not_serving,
            "brand": detect_brand(pages, origin),
            # Detected once, here, so six skills cannot reach six different answers
            # about what language the site is in.
            "site_language": detect_site_language(pages),
            "crawl": {
                "pages_crawled": len(pages),
                "pages_ok": sum(1 for p in pages if p.get("status") == 200),
                # Pages whose text this audit actually has. `pages_ok` counts a
                # 200 whatever came back with it, and a record carrying `skipped`
                # has no text, no links and no headings: an off-origin redirect,
                # a PDF, a body this client could not decompress. A domain that
                # redirects to another site scored `pages_ok` of 1 on a crawl that
                # read nothing, and the report called that a clean bill of health.
                # This is the count to gate a verdict on.
                "pages_read": sum(1 for p in pages
                                  if p.get("status") == 200 and not p.get("skipped")),
                # The flag the loop actually set, not a guess made afterwards.
                # `bool(queue) or now >= deadline` was read after the render pass,
                # whose own deadline can run past this one - so a crawl that
                # finished comfortably reported as budget-exhausted, and
                # `engagement-audit` reads this to decide whether to judge orphan
                # pages at all. Hitting the page ceiling is a different fact and
                # now has its own name.
                # Set by the loop that stopped, not read from the clock
                # afterwards. `now >= deadline` was evaluated after the render
                # pass, whose own deadline can run past this one, so a crawl that
                # finished comfortably reported as exhausted - and
                # `engagement-audit` reads this to decide whether it may judge
                # orphan pages at all. Either limit stopping the crawl early is
                # the thing downstream cares about; which one it was is recorded
                # separately.
                # True when the wall clock ran out, and at no other time. A
                # crawl that read every page its ceiling allows and left the
                # rest of a large site unvisited has not exhausted its budget,
                # and saying it has is what put "budget_exhausted: true"
                # against a 142s budget with 40s of it unspent on both of two
                # runs compared side by side.
                "budget_exhausted": budget_exhausted,
                # The crawl stopped with addresses of this site still queued -
                # whatever stopped it. This is the fact a check needs before it
                # can say "no page of this site does X", and it is what
                # `budget_exhausted` was being used for. Two readers still ask
                # the old key: `engagement-audit` decides whether it may judge
                # orphan pages, and `fact-extractability-audit` decides how
                # large a site the sample is a share of. Both want this one.
                "stopped_early": bool(queue),
                "stopped_by": stopped_by,
                "wall_clock_budget_s": budget_s,
                "elapsed_s": round(time.monotonic() - started, 2),
                "max_pages": max_pages,
                # Whether this run's page set can be reproduced, and what decided
                # its size. Anyone who runs this tool twice on one site is
                # entitled to the same findings both times; where that cannot be
                # promised, this says so in the snapshot rather than leaving two
                # different reports to be discovered side by side.
                #
                #   page_cap        how many pages this crawl was allowed to
                #                   read, and `cap_source` what set that - the
                #                   ceiling, or a `Crawl-delay` the site
                #                   publishes. Both are known before the first
                #                   request, so both read the same on a second
                #                   run. Nothing here is timed.
                #   deterministic   whether this page set repeats. False only
                #                   when something outside the crawl's own plan
                #                   chose part of it - the wall clock, a rate
                #                   limiter, a connection that failed.
                #   not_reproducible_because
                #                   why, in sentences, so a reader comparing two
                #                   reports can see which one moved and what
                #                   moved it. Empty when `deterministic`.
                #   truncated_at    the address the crawl stopped before, so two
                #                   truncated runs can be compared
                "page_plan": {
                    "page_cap": page_cap,
                    "cap_source": page_cap_source,
                    "deterministic": not not_reproducible,
                    "not_reproducible_because": list(not_reproducible),
                    "truncated_at": truncated_at,
                },
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
                # What the site said about this crawl's request rate, and what
                # this crawl did about it. Written always, so a check can tell
                # "nothing was rate limited" from "this snapshot predates the
                # question" rather than reading a missing key as a clean run.
                #
                # `urls` is the addresses that answered 429, `retry_after_s` the
                # longest wait the site asked for in a `Retry-After` header,
                # `slowed_to_s` the seconds per request this crawl backed off to,
                # and `gave_up` whether the wait asked for was longer than the run
                # had left. A crawl that was throttled read less of the site than
                # it asked to, which is a fact about this run rather than about
                # the site, and the checks that hold back absence claims need to
                # be able to say which.
                "rate_limit": {
                    "urls": sorted(set(fetcher.rate_limited_urls))[:30],
                    "count": len(fetcher.rate_limited_urls),
                    "retry_after_s": fetcher.retry_after_s,
                    "slowed_to_s": fetcher.slowed_to,
                    "gave_up": fetcher.rate_limit_gave_up,
                },
                "notes": notes + ([CHECKPOINT_NOTE] if checkpoint else []),
                "skipped": skipped[:30],
            },
            "robots": _robots_with_paths_seen(robots, sitemaps, pages, skipped),
            "sitemaps": sitemaps,
            "sitemap_referenced_in_robots": sitemap_in_robots,
            "llms_txt": llms_txt,
            "llms_full_txt": llms_full_txt,
            "pages": pages,
        }

    def _write_checkpoint():
        """Leave what the crawl has so far on disk, replacing the last one.

        Written through a temporary file and one rename: a kill that lands
        inside a write would otherwise leave half a JSON document where a
        readable snapshot had been, which is worse than leaving none.
        """
        write_json_atomically(out_path, _snapshot_now(checkpoint=True))

    if audit_blocked:
        notes.append(
            "robots.txt disallows this auditor at /, so no pages were fetched. "
            "The access findings below are derived from robots.txt alone."
        )
    else:
        for url in (home_url, seed_url):
            url = normalise_url(url)
            if not url or dedup_key(url) in queued:
                continue
            # Checked, like every other URL. `audit_blocked` above only catches
            # `Disallow: /`, so a site disallowing `/en/` while the audit was
            # pointed at `https://example.com/en/` had that page fetched in
            # defiance of the file - the one URL a user is most likely to
            # hand us, and the one path most likely to be listed.
            ok, reason = crawlable(url, origin, robots, respect_robots)
            if not ok and url != home_url:
                skipped.append({"url": url, "reason": reason})
                continue
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

        # The ceiling is on pages, so only a page may spend from it.
        #
        # `len(pages) < max_pages` counted every record, and a record for a
        # response that was never a page is still a record. On one crawl of
        # sixteen URLs, nine were release archives and a patch file: they took
        # eight of the sixteen slots, and eight pages with prose on them were
        # never requested. The second bound is a backstop, not a budget - a
        # host that answers a non-page at every address would otherwise be
        # bounded only by the wall clock.
        while (queue
               and page_slots_used < max_pages
               and len(pages) < max_pages * NON_PAGE_RECORD_HEADROOM):
            if time.monotonic() >= deadline:
                # The backstop, and the only exit here that a second run of
                # this site can disagree with. Reaching it means this site is
                # slower than `budget / max_pages` a page, so where the clock
                # fell decided part of what was read - which is not a property
                # of the site. It is recorded rather than merely noted, and the
                # address the crawl stopped before is named, so two runs that
                # were both cut can be compared line by line.
                budget_exhausted = True
                stopped_by = "wall clock"
                truncated_at = queue.peek()
                # Pages spend from the ceiling; every other record does not.
                # Counting records printed "ran out after 95 pages, before the
                # crawl reached the 60-page count it was allowed" about a museum
                # whose 95 records held 52 pages - a sentence that contradicts
                # itself, and a `--max-pages 95` that would change nothing.
                fetched_note = ("" if len(pages) == page_slots_used else
                                " ({} addresses fetched in all, counting files and "
                                "redirects)".format(len(pages)))
                not_reproducible.append(
                    "the wall clock, not the page ceiling, stopped this crawl: it read {} of "
                    "the {} page(s) it was allowed{}{}".format(
                        page_slots_used, page_cap, fetched_note,
                        ", stopping before {}".format(truncated_at) if truncated_at else ""))
                notes.append(
                    "the wall-clock budget of {:.0f}s ran out after {} of the {} pages it was "
                    "allowed{}. Where the clock falls "
                    "depends on how fast the network was, so another audit of this site may "
                    "read a different number of pages and report a slightly different set of "
                    "findings{}. Run it again with a larger --budget, or with --max-pages {}, "
                    "for a page set that repeats.".format(
                        budget_s, page_slots_used, page_cap, fetched_note,
                        "; the next address in the crawl's fixed order was {}".format(
                            truncated_at) if truncated_at else "",
                        max(1, page_slots_used)))
                break
            # A rate limiter asking for longer than this run has left. Waiting
            # it out returns nothing; carrying on at the old rate is what
            # produced 45 429s across 59 URLs on one site, and the report then
            # withheld nine true findings because of throttling this crawl had
            # caused. Stopping here keeps whatever was read before the limiter
            # engaged, and the note says what happened.
            if fetcher.rate_limit_gave_up:
                stopped_by = "rate limit"
                # Recorded for the same reason as the clock exit above: whether
                # the wait the limiter asked for was longer than the run had
                # left depends on when in the run it was asked, so this crawl
                # stopped somewhere only this run's timing chose. Not
                # `budget_exhausted`: the budget was not spent, the site asked
                # for more of it than was left.
                truncated_at = queue.peek()
                not_reproducible.append(
                    "the site rate limited this crawl and asked for a wait longer than the "
                    "run had left, so where it stopped was chosen by when the limiter "
                    "engaged")
                notes.append(
                    "the site answered HTTP 429 (too many requests) and asked for {} before "
                    "the next one, which is longer than this run had left. The crawl stopped "
                    "there rather than overrun its budget, so the pages below are what was "
                    "read before the rate limiter engaged.".format(
                        "{:.0f}s".format(fetcher.retry_after_s)
                        if fetcher.retry_after_s else "a further wait"))
                break
            # Re-tested between the fetch and the meta-refresh follow, because
            # one wedged request can cost REQUEST_TOTAL_TIMEOUT plus
            # REQUEST_TIMEOUT and the deadline was only ever read at the top of
            # the loop.
            url, depth, source = queue.popleft()
            record = fetch_page(fetcher, url, depth, source, origin)
            record = _follow_meta_refresh(fetcher, record, depth, source, origin, notes)
            # A page this crawl could not reach at all - a refused connection,
            # a socket that never answered - is the second way one run's page
            # set can differ from another's, and the quiet one. The record
            # carries no links, so every page reachable only through it is
            # never queued; a run where the same request succeeded expands from
            # it and reads more of the site. Nothing can make a failed
            # connection repeat, so it is named here instead.
            if record.get("error") and not record.get("status"):
                not_reproducible.append(
                    "{} could not be reached on this run ({}), so any page linked only from "
                    "it was never queued".format(url, truncate(record["error"], 80)))
            # Where a redirect landed, not only where it was aimed. `crawlable`
            # refuses to queue a sign-in path; nothing checked the destination,
            # so a school's `/calendar` - a 302 to `/login` - was read, audited
            # and named in a finding telling the owner to set a heading on a
            # page they do not control. The audit's promise is that it analyses
            # nothing under `/login` or `/account`, and a redirect is how it
            # got there.
            landed_url = record.get("final_url") or record.get("url") or ""
            if landed_url != url and is_forbidden_path(landed_url):
                skipped.append({"url": url,
                                "reason": "redirects to a sign-in or account page"})
                continue
            # Two queued URLs that redirect to the same destination are one
            # page; keeping both would double-count every finding on it.
            landed = normalise_url(record.get("final_url") or url) or url
            # `landed != url` was part of this condition, and it let the other
            # half of every redirect pair through. A shop's `/` redirects to
            # `/us/eng`, so `/` was recorded under the landing URL; `/us/eng`
            # was then fetched in its own right, did not redirect, and so
            # never reached the check at all. The same page appeared twice,
            # was reported as a duplicate title against itself, and inflated
            # the counts in the autoplay and page-weight findings. The
            # question is only ever "have we already read what is at this
            # address", and where the request started does not change it.
            if landed in fetched_final:
                skipped.append({"url": url, "reason": "redirects to an already-crawled page"})
                continue
            # Pages that name the same canonical are one page, and the crawl
            # reads one of them. A retailer's cookie-preferences links
            # produced `/?ketch_show=preferences` and a second variant of it,
            # both byte-identical to the homepage and both canonicalling back
            # to it; the report counted three pages sharing a title, spent 5%
            # of a sixty-page budget on one page, and inflated the denominator
            # of every site-wide share.
            #
            # The rule used to be "skip a page whose canonical the crawl has
            # already read", which missed the case it most needed to catch: a
            # store's three menu links landed on three tagged addresses that
            # each named a bare address none of them was, so nothing matched
            # an already-read page and all three were read. Grouping by the
            # canonical they declare catches that, and it cannot hide a crawl
            # the way "skip anything naming a canonical" would - the first
            # member of every group is always read, so a site that
            # mass-declares a canonical nobody fetched still gets one page per
            # group and the canonical check downstream still fires on all of
            # them.
            #
            # Which member survives is settled downstream by
            # `fold_declared_duplicates`, which prefers the canonical address
            # itself when the crawl holds it. Here the only question is how
            # many requests a group is worth, and the answer is one.
            #
            # `normalise_url("", origin)` returns the origin, so an empty
            # canonical has to be rejected before it is resolved - otherwise
            # every page that declares none reads as a duplicate of the
            # homepage, and a five-page fixture crawls one page.
            declared_canonical = (record.get("canonical") or "").strip()
            canonical = normalise_url(declared_canonical, origin) if declared_canonical else ""
            group = dedup_key(canonical) if canonical else None
            if group and group != dedup_key(landed):
                held = canonical_groups.get(group)
                if held is not None:
                    skipped.append({"url": url,
                                    "reason": "names {} as its canonical, the page "
                                              "already read as {}".format(canonical, held)})
                    continue
                canonical_groups[group] = landed
            elif dedup_key(landed) is not None:
                # A page read at the address others name. It closes its own
                # group, so a variant declaring it later is skipped whichever
                # of them the crawl reached first.
                canonical_groups.setdefault(dedup_key(landed), landed)
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
            if not record.get("not_a_page"):
                page_slots_used += 1
            # What this crawl has, on disk, before the next request can hang.
            #
            # The orchestrator now stops the crawl at the boundary of the wall
            # clock it reserved for the six sub-skills, instead of letting the
            # crawl hold the clock and the skills take whatever survived it.
            # That trade only pays if being stopped costs pages rather than
            # costing the run. Measured on a site answering in six seconds a
            # page: the crawl was told 171 s and ran 174, holding 29 pages it
            # had read, and every one of them lived in this process. Stopping
            # it there without this write would have ended the audit with
            # "nothing to report on" - a worse outcome than the missing skill
            # the reserve exists to prevent.
            if len(pages) % CHECKPOINT_EVERY_PAGES == 0:
                _write_checkpoint()

            # Stop once a bot manager is answering for most of the site.
            # Continuing cannot find anything: the next page is the same
            # screen, and the finding worth making - that this site refuses the
            # crawler - is already made by the first few.
            challenged = sum(1 for seen in pages if seen.get("challenge"))
            if (len(pages) >= CHALLENGE_GIVE_UP_AFTER
                    and challenged >= len(pages) * CHALLENGE_GIVE_UP_SHARE):
                stopped_by = "bot-manager challenge"
                notes.append(
                    "{} of the first {} pages answered with a bot-manager verification screen "
                    "instead of content, so the crawl stopped: what is behind them is "
                    "unverified, and reading more copies of the same screen would only add "
                    "them to every count.".format(challenged, len(pages)))
                break

            if record.get("status") != 200 or depth >= MAX_DEPTH:
                continue
            # Where the page itself says it continues, before its links. A
            # one-page site answered `/` with an empty document carrying
            # eighteen `<link rel="alternate" hreflang>` tags and a script
            # setting `window.location` to its English edition; with no anchor
            # to follow the crawl read that one empty page and audited it as
            # the site. The alternates and a script's fixed same-site target
            # are queued like links - same site, robots.txt respected, inside
            # the same page ceiling - and the target keeps the source name
            # `script-redirect` so a check can say the page got there by script.
            continuations = [(alt.get("url"), "hreflang")
                             for alt in record.get("hreflang") or []]
            if record.get("script_redirect"):
                continuations.insert(0, (record["script_redirect"].get("url"),
                                         "script-redirect"))
            for link, via in continuations:
                if not link or not same_site(link, origin) or len(queue) >= max_pages * 3:
                    continue
                if dedup_key(link) in queued:
                    continue
                ok, reason = crawlable(link, origin, robots, respect_robots)
                if not ok:
                    if reason == "disallowed by robots.txt":
                        skipped.append({"url": link, "reason": reason})
                    continue
                queued.add(dedup_key(link))
                queue.append((link, depth + 1, via))
            # Breadth-first expansion from in-page links, sorted for
            # determinism. An anchor the page has closed to people - see
            # `anchors_no_visitor_can_reach` - is not a page of this site and
            # is not queued: following one spent a page slot on a hosted site
            # builder's injected asset route, recorded its 404, and turned one
            # artefact into "33.3% of crawled pages do not return HTTP 200" on
            # a site with two pages.
            for link in sorted(
                {l["url"] for l in (record.get("links", {}).get("internal") or [])
                 if not l.get("unreachable_by_a_visitor")}
            ):
                # The bound is on what is still waiting, not on everything ever
                # seen. `queued` is cumulative and never shrinks, so on a
                # link-dense site the homepage alone filled the 180-entry
                # ceiling and every page after it broke on its first link and
                # contributed nothing. Depth 2 was then unreachable: the whole
                # sixty-page sample came from one or two pages' link sets, and
                # the page-distribution figure this module's own budget comment
                # quotes - 1 at depth 0, 29 at depth 1, 30 at depth 2 - could
                # not happen. A retailer's product pages, all linked from
                # category pages the crawl reached but never expanded, were
                # simply never seen, and the report described the site.
                if len(queue) >= max_pages * 3:
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

        # The ordinary end of a large crawl, named rather than inferred. The
        # loop can also fall out on the record backstop - a host answering a
        # non-page at every address - and that is a different fact about the
        # run, so it gets a different word.
        if queue and stopped_by == "nothing left to fetch":
            stopped_by = ("page ceiling" if page_slots_used >= max_pages
                          else "record ceiling")

    # By `source`, not by URL. A homepage that answers with a meta refresh is
    # recorded under the destination it points at, so matching on the address
    # we requested found nothing on exactly those sites - and the two things
    # that depend on this record then went unanswered: the one homepage retry
    # never happened, and `head_supported` came out `None`, which makes the
    # three link-verification checks report every unreached target as unchecked
    # rather than confirming it.
    home_record = next(
        (p for p in pages if p.get("source") in ("homepage", "homepage-retry")),
        None)
    if home_record is None:
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

    # A seed redirect the probe above never saw. That probe is one request and
    # a host can answer it differently from the GET that follows; this record
    # is the GET, which is the ground truth about where the address the audit
    # was pointed at actually goes. If it left the origin then no page of the
    # site was read, and the snapshot says so here rather than leaving a
    # reader to infer it from an empty page list - which is the inference that
    # produced "no blocking or significant problems were found" about a site
    # nothing had been read from.
    if origin_redirect is None and home_record is not None \
            and home_record.get("offsite_redirect"):
        landed = home_record.get("final_url") or ""
        origin_redirect = {
            "requested_origin": origin,
            "requested_url": home_record.get("url") or home_url,
            "final_url": landed,
            "final_origin": origin_of(landed) if landed else "",
            "status": home_record.get("status"),
            "same_registered_name": bool(landed) and _same_registered_name(
                origin, origin_of(landed)),
            "followed": False,
            "reason": (
                "The address this audit was asked about, {}, answers with a redirect "
                "to {}, which is off its own origin. It was not followed, so no page "
                "of {} was read and this audit cannot say whether {} is ready for AI "
                "assistants or not.".format(
                    home_record.get("url") or home_url, landed or "another site",
                    site_label(origin), site_label(origin))),
        }
        notes.append(origin_redirect["reason"])

    _mark_edge_refusals(pages, notes)

    # Read after every request this crawl was going to make has been made, so
    # that "every address answered the same way" is a statement about the whole
    # crawl rather than about the first few responses.
    site_not_serving = _detect_site_not_serving(
        robots, sitemaps, agent_files, pages, origin)
    if site_not_serving is not None:
        notes.append(site_not_serving["reason"])

    oversized = [p["url"] for p in pages if p.get("truncated")]
    if oversized:
        notes.append(
            "{} page(s) exceeded the {:,}-byte read cap and were analysed up to that point: "
            "{}. Anything the audit says about what those pages lack is limited to the part "
            "it read".format(len(oversized), MAX_RESPONSE_BYTES, ", ".join(sorted(oversized)[:3])))

    # Once more before the longest thing left. Everything above this line is
    # network work the fetcher's own deadline already refuses past the budget;
    # the rendered pass below drives a browser, whose page loads are bounded by
    # their own timeouts and not by this crawl's clock. A checkpoint here is
    # also the only one a crawl of fewer than CHECKPOINT_EVERY_PAGES pages
    # gets, so a four-page site is not left with no checkpoint at all.
    _write_checkpoint()

    if render:
        # The rendered pass shares the crawl's wall-clock budget rather than
        # adding to it. Before it ran by default that distinction did not
        # matter; now it does, because an unclamped 60s pass on top of a
        # 240s crawl would put a slow site past the five minutes the audit
        # promises. If the crawl has already spent the budget there is nothing
        # left to spend and the pass says so instead of running.
        render_mode = _render_pass(pages, notes, origin, deadline,
                                   required=(render is True),
                                   not_reproducible=not_reproducible)

    # Boilerplate is stripped after the rendered pass, not before it. A page
    # the pass re-read from the rendered DOM arrives with its chrome intact,
    # and stripping earlier left that one page carrying a header and footer
    # every other page had had removed - so its word counts, its "what an
    # assistant would quote" line and its title-drift comparison were all
    # measured against a different document from its neighbours'.
    _strip_sitewide_boilerplate(pages, notes)
    head_supported = _probe_head_support(fetcher, home_url, home_record, notes)

    # Said once, at the end, with the total. Saying it per response would put
    # 45 identical lines in a 30-line notes list on the site that produced the
    # measurement, and the fact a reader needs is that the crawl was throttled
    # and read less because of it - not which request was throttled first.
    if fetcher.rate_limited_urls and not fetcher.rate_limit_gave_up:
        notes.append(
            "{} request(s) were answered HTTP 429 (too many requests), so the crawl slowed "
            "to {:g}s between requests for the rest of the run{}. Fewer pages were read as "
            "a result, and that is a limit this crawl met rather than a defect in the "
            "site.".format(
                len(fetcher.rate_limited_urls), fetcher.slowed_to or fetcher.delay,
                ", which is what the site's Retry-After header asked for"
                if fetcher.retry_after_s else ""))

    _type_regional_homepages(pages, home_record)
    pages.sort(key=lambda p: (0 if p.get("page_type") == "home" else 1, p["url"]))

    snapshot = _snapshot_now()
    write_json_atomically(out_path, snapshot)
    return snapshot


def _type_regional_homepages(pages, root):
    """Call each region's storefront a homepage, once the root has been read.

    A retailer whose `/` is a country picker keeps its real homepages at
    `/sg`, `/au`, `/uk`, and each was typed by the markup it carries - `faq`,
    on that site - so every check that reads "the homepage" read the picker.
    Page typing sees one page at a time and cannot know what the root links
    to, so it is settled here, after the crawl, from the root's own links.
    See `audit_common.regional_homepage` for the rule.
    """
    if not root or root.get("status") != 200:
        return
    try:
        root_path = urlparse(root.get("final_url") or root.get("url") or "").path
    except ValueError:
        return
    if root_path.strip("/"):
        return          # the root landed on a storefront, which is already home
    root_links = (root.get("links") or {}).get("internal") or []
    for page in pages:
        if page is root or page.get("status") != 200 or page.get("page_type") == "home":
            continue
        address = page.get("final_url") or page.get("url") or ""
        if regional_homepage(address, page.get("lang"), root_links):
            page["page_type"] = "home"
            page["page_type_source"] = (
                "a region's storefront: the root page links to it among other regions, "
                "and the page declares lang=\"{}\"".format(page.get("lang")))


# A real error page is branded and long. An edge refusing a crawler answers in
# a few bytes, identically, many times over.
EDGE_REFUSAL_MAX_BYTES = 400
EDGE_REFUSAL_MIN_REPEATS = 3


# Statuses that answer for the site rather than for the address asked for.
#
# The distinction is the whole point. A 404 says "no page is filed here", which
# is a fact about one address and leaves every other address open. Each status
# below says something about the service itself, so a site that answers one of
# them at every address it was asked for is not a site with missing pages - it
# is a site that is not serving, and that is the largest true thing an audit
# can say about it.
#
# What is deliberately absent:
#
#   404             the one status that is about the address, not the site.
#   REFUSED_STATUS  401, 403, 405, 406 and 429 are about who is asking. The
#                   crawl-access checks compare user agents and re-probe to
#                   decide whether an edge is refusing this crawler in
#                   particular, and that is a different story with a different
#                   fix; asserting "the site is not serving" over the top of it
#                   would be wrong on every bot-managed site that serves people.
#   500, 502, 504   a request that failed on the way through. 503 is the one
#                   5xx that is a statement rather than an accident: the server
#                   saying it is not taking requests.
#
# Measured on a hosted store whose owner had switched it off. Every address
# answered 402 with a two-word page; the sitemap answered 402 with a two-word
# XML error; robots.txt answered 200 with `Disallow: /`. The crawl recorded the
# 402 in the snapshot and nothing read it, so the entire report was one finding
# telling the owner to remove a `Disallow: /` line - a change that would have
# altered nothing, because there was no shop behind it to crawl.
NOT_SERVING_STATUS = {
    402: "the server is withholding the site pending payment",
    410: "the server states that what was asked for is permanently gone",
    451: "the server is withholding the site for legal reasons",
    503: "the server states that it is not currently serving requests",
}


def _detect_site_not_serving(robots, sitemaps, agent_files, pages, origin):
    """Is the site declining to serve at all, rather than missing some pages?

    Returns the snapshot's `site_not_serving` record, or None.

    The test is uniformity plus meaning: every address this crawl reached
    answered, all of them answered the same status, and that status is one
    that speaks for the service (`NOT_SERVING_STATUS`). One address answering
    410 among ninety-nine that answer 200 is a deleted page and fails the
    first half; ninety-nine addresses answering 403 fail the second.

    robots.txt is read for evidence and left out of the uniformity test. It is
    not a page of the site, it is the file that says what a crawler may fetch,
    and a switched-off store still serves it - which is exactly the shape that
    hid this: robots.txt answered 200, so "every address answered the same
    thing" would have been false about a site where every address that holds
    content answered 402.
    """
    answers = []
    for record in sitemaps:
        answers.append((record.get("url") or "", record.get("status")))
    for record in (agent_files or {}).values():
        answers.append((record.get("url") or "", record.get("status")))
    for page in pages:
        answers.append((page.get("url") or "", page.get("status")))

    answered = [(url, status) for url, status in answers if status is not None]
    if not answered:
        # Nothing answered at all. That is a host that is down or unreachable,
        # which the crawl notes already say and which this record must not be
        # allowed to restate as a deliberate refusal.
        return None
    statuses = {status for _, status in answered}
    if len(statuses) != 1:
        return None
    status = statuses.pop()
    meaning = NOT_SERVING_STATUS.get(status)
    if not meaning:
        return None

    addresses = sorted({url for url, _ in answered if url})
    if (robots or {}).get("status") == status and robots.get("url"):
        addresses = sorted(set(addresses) | {robots["url"]})
    reason = (
        "Every one of the {} address(es) this audit reached on {} answered HTTP {}: {}. "
        "That is the site declining to serve, not a page that is missing, so nothing "
        "below is a measurement of what {} publishes, and no change to robots.txt, to a "
        "sitemap or to any page would alter it while the site is answering this way."
        .format(len(answered), site_label(origin), status, meaning, site_label(origin)))
    return {
        "status": status,
        "meaning": meaning,
        # Every address that answered this way, so a report can show its
        # working rather than asserting a site-wide fact from one response.
        "addresses": addresses[:20],
        "addresses_answered": len(answered),
        "reason": reason,
    }


# Text that appears on this share of the crawled pages is the site's furniture,
# whatever element it happens to live in.
#
# Every selector-based attempt at this failed the same way: the list is a list
# of the shapes we have already been burned by, and the next site uses a shape
# nobody wrote down. Stripping `header`, `nav`, `footer` and `aside` missed a
# cosmetics retailer's `<div class="promo-bar__text">`, so four locales' worth
# of free-shipping thresholds were read as the prices on a product page and the
# page's own correct price was reported as contradicting them. Adding
# announcement-bar classes to the list fixed that site and would not have fixed
# the next one.
#
# Repetition is the property that actually defines chrome, and it does not
# depend on knowing any site's markup conventions. A paragraph on 90% of a
# site's pages is not what any of those pages is about.
#
# 0.5 with a floor of 4 pages: a two-column footer, a cookie line and a
# promotional banner all sit at or near 1.0, while the most-repeated genuine
# content block measured across the crawls behind these numbers - a shipping
# blurb repeated on one retailer's product pages - sat at 0.34. Nothing
# measured falls between. The floor exists because on a three-page crawl every
# block is "on most pages" and the rule would eat the site.
BOILERPLATE_PAGE_SHARE = 0.5
BOILERPLATE_MIN_PAGES = 4
# Below this a repeated string is a label, not a passage: "Read more", "Home",
# a price. Stripping those would empty the tables that legitimately repeat
# short cells, and they are too short to be quoted as a fact anyway.
BOILERPLATE_MIN_CHARS = 40


def _strip_sitewide_boilerplate(pages, notes):
    """Remove text that repeats across the site from each page's own content.

    Runs after the crawl because it needs every page to see the repetition.
    Rewrites `paragraphs`, `sections` and `body_text` in place; `text` keeps
    the whole page, so nothing is lost, only reattributed.
    """
    readable = [p for p in pages if p.get("status") == 200 and not p.get("skipped")]
    if len(readable) < BOILERPLATE_MIN_PAGES:
        return

    counts = defaultdict(set)
    for page in readable:
        for block in _content_blocks(page):
            counts[block].add(page["url"])

    threshold = max(BOILERPLATE_MIN_PAGES, int(len(readable) * BOILERPLATE_PAGE_SHARE))
    boilerplate = {block for block, urls in counts.items() if len(urls) >= threshold}
    if not boilerplate:
        # `_content_blocks` stashed the raw text on every page so it could be
        # matched here. On the path where nothing repeats, this returned before
        # the loop that clears it, and up to a hundred duplicated blocks per
        # page were serialised into snapshot.json under a private key and then
        # reloaded by all six sub-skills.
        for page in readable:
            page.pop("_raw_blocks", None)
        return

    for page in readable:
        page["paragraphs"] = [p for p in (page.get("paragraphs") or [])
                              if _block_key(p) not in boilerplate]
        page["sections"] = [s for s in (page.get("sections") or [])
                            if _block_key(s.get("first_paragraph")) not in boilerplate]
        body = page.get("body_text") or ""
        removed = [b for b in (page.get("_raw_blocks") or []) if _block_key(b) in boilerplate]
        for raw in removed:
            body = body.replace(raw, " ")
        page.pop("_raw_blocks", None)
        body = re.sub(r"\s+", " ", body).strip()
        page["body_text"] = body
        page["body_text_len"] = len(body)
        # Everything derived from the body has to be derived again, or the
        # snapshot carries two incompatible descriptions of one page. A page
        # cut to 50 characters of its own content still reported 160 words, and
        # `cta.offset` was a character position into a string that had been
        # replaced - so `within_first_1500`, which the homepage-orientation
        # finding reads, was measuring nothing.
        page["word_count"] = word_count(body)
        page["above_fold_text"] = truncate(body, 1500)
        # The readability block is derived from the body too, and was the one
        # derived thing this pass left alone. A page's sentence lengths were
        # therefore measured with its account menu and category nav still in
        # the text: one crawl's longest "sentence" was 307 words of menu
        # labels, ten of the page's fourteen "sentences" were furniture, and
        # the finding read "47% long, average 67.6 words" on a run whose own
        # notes say twelve blocks of site furniture had been identified. Four
        # findings read these numbers.
        page["readability"] = recount_readability(page.get("readability"), body)
        cta = page.get("cta")
        if isinstance(cta, dict) and cta.get("found"):
            label = (cta.get("text") or "").strip()
            offset = body.lower().find(label.lower()) if label else -1
            if offset < 0:
                # The call to action was in a block this pass removed, which
                # means it is site furniture rather than this page's own
                # invitation. Say so rather than keeping a stale number.
                cta["offset"] = None
                cta["within_first_1500"] = False
                cta["in_repeated_chrome"] = True
            else:
                cta["offset"] = offset
                cta["within_first_1500"] = offset < 1500

    notes.append(
        "{} block(s) of text appear on at least {} of the {} readable pages and were treated "
        "as site furniture rather than as any one page's content".format(
            len(boilerplate), threshold, len(readable)))


def _content_blocks(page):
    """The text blocks on this page that repetition could mark as furniture."""
    raw = []
    for paragraph in page.get("paragraphs") or []:
        raw.append(paragraph)
    for section in page.get("sections") or []:
        if section.get("first_paragraph"):
            raw.append(section["first_paragraph"])
    page["_raw_blocks"] = raw
    return {key for key in (_block_key(value) for value in raw) if key}


def _block_key(value):
    """A repeated block, normalised so spacing and case cannot hide it."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    return text if len(text) >= BOILERPLATE_MIN_CHARS else ""


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
        # Bytes, matching the constant's name. A 400-character threshold and a
        # 400-byte threshold are the same thing only in ASCII.
        length = page.get("html_bytes") or page.get("html_len") or 0
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
# It used to be opt-in, which meant that for anyone not told about the flag it
# would almost certainly never run and the JavaScript-shell finding would always be the
# inferred version rather than the measured one. It costs about 8 seconds
# against five pages, inside a run that has 140 seconds of its budget spare, so
# there was nothing left to protect by keeping it off. `--no-render` turns it
# off; `--render` demands it and says so in the notes when it cannot happen.
#
# Every number below exists to keep the pass inside the same five-minute budget
# the static audit promises: a browser waiting for an idle state a page never
# reaches will happily spend the whole of it.
RENDER_PAGES = 5             # a sample, not a second crawl
# 110, not 75. A client-rendered site is the case the pass exists for, and 75
# seconds covered ten pages of a software vendor's twenty-eight - leaving the
# emptiest seven unread and three findings raised against them. The pass is
# still bounded by the crawl's own deadline, so this cannot push a run past
# the five minutes: measured, that site finished in 204s of 300.
RENDER_BUDGET_SECONDS = 110  # hard ceiling for the whole pass
RENDER_GOTO_MS = 12000       # per page, to first paint
RENDER_IDLE_MS = 3000        # best-effort wait for the network to settle
RENDER_MIN_SECONDS = 10      # below this there is no time to render even one page honestly

# How long to wait for hydration to finish writing to the DOM.
#
# This was a flat 500 ms, and 500 ms is not enough for a commerce site built
# on a JavaScript framework. Re-running the same browser against one such shop
# with about eleven seconds instead of the four and a half it was given
# returned 6,500 to 10,300 characters of real text, real titles and real
# headings on pages the audit had just called empty shells - and the report's
# first recommendation was to spend "several days of development time"
# re-architecting rendering on a site that renders correctly. The tool gave up
# before the page finished, then described what it saw as a property of the
# site.
#
# A flat, longer wait would spend the whole budget on pages that were ready
# immediately. So the text is polled instead, and the wait ends as soon as it
# stops growing: a static page settles in one round, and a slow one gets the
# time it needs up to the ceiling.
RENDER_POLL_MS = 500          # how often to re-read the rendered text
RENDER_SETTLE_MAX_MS = 9000   # stop waiting for growth after this
RENDER_SETTLE_STABLE = 2      # unchanged this many polls in a row means settled


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
    # A 200 is not enough: a record carrying `skipped` answered 200 and is a
    # release archive, a PDF or a redirect off this origin. Handing one to a
    # browser spends up to `RENDER_GOTO_MS` plus the settle wait on a document
    # that has no DOM to settle, out of a pass with five slots in it.
    ok = [p for p in pages if p.get("status") == 200 and not p.get("skipped")]
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


def _settled_text(tab):
    """Read the rendered text once it stops growing. Returns (text, ms waited).

    Polling beats a fixed pause in both directions: a server-rendered page is
    stable on the first read and costs one poll, while a page still hydrating
    keeps its time up to the ceiling instead of being measured half-built and
    reported as empty.
    """
    read = "document.body ? document.body.innerText : ''"
    text = tab.evaluate(read)
    waited, stable = 0, 0
    while waited < RENDER_SETTLE_MAX_MS and stable < RENDER_SETTLE_STABLE:
        tab.wait_for_timeout(RENDER_POLL_MS)
        waited += RENDER_POLL_MS
        current = tab.evaluate(read)
        stable = stable + 1 if len(current) == len(text) else 0
        text = current
    return text, waited


# How much richer the rendered document has to be before it replaces the
# static one. Two and a half times, with a floor, so an ordinary server-rendered
# page whose scripts add a cookie bar is left alone and only a genuine shell is
# re-read.
RENDER_ADOPT_RATIO = 2.5
RENDER_ADOPT_MIN_CHARS = 400


def _adopt_rendered_dom(page_record, tab, origin):
    """Re-read a page from the rendered DOM when the delivered HTML was a shell.

    The pass used to record `rendered_text_len` and throw the document away.
    Measured on a software vendor's homepage: the delivered HTML holds 60
    characters, the rendered DOM holds 6,606, and the snapshot kept the 60.
    Every downstream check then read a page with no H1, no navigation, no call
    to action and nothing quotable, and the report carried four findings that
    were all false of the page a visitor sees - while the JavaScript finding
    two entries above it quoted the 6,606 figure it had just measured.

    The static measurements are kept under their own names, because the
    JavaScript-shell finding is about the gap between them and is still true.
    """
    rendered_len = page_record.get("rendered_text_len") or 0
    static_len = page_record.get("text_len") or 0
    if rendered_len < RENDER_ADOPT_MIN_CHARS:
        return False
    if rendered_len < max(static_len * RENDER_ADOPT_RATIO, RENDER_ADOPT_MIN_CHARS):
        return False
    try:
        html = tab.content()
    except Exception:  # noqa: BLE001 - a DOM we cannot read is not a site defect
        return False
    if not html:
        return False
    fresh = extract_page(
        url=page_record["url"], final_url=page_record.get("final_url") or page_record["url"],
        status=page_record.get("status"), headers=page_record.get("headers") or {},
        html=html, redirect_chain=list(page_record.get("redirect_chain") or []),
        elapsed_ms=page_record.get("elapsed_ms", 0),
        depth=page_record.get("depth", 0), source=page_record.get("source", ""),
        origin=origin,
    )
    # What the server sent, kept so the gap can still be reported honestly.
    # The JavaScript-shell finding is about the difference between these
    # numbers and the rendered ones, and it is still true of the site; reading
    # the rendered document for both halves would compare it against itself and
    # report a gap of zero on exactly the pages that have the largest one.
    static = {
        "static_text_len": static_len,
        "static_view": {
            "text_len": static_len,
            "body_text_len": page_record.get("body_text_len"),
            "html_len": page_record.get("html_len"),
            "spa_shell": page_record.get("spa_shell"),
            "scripts": page_record.get("scripts"),
            "noscript_text": page_record.get("noscript_text"),
        },
        "content_from": "rendered",
    }
    # Crawl bookkeeping belongs to the fetch, not to the document.
    for key in ("truncated", "truncated_at_read_cap", "html_bytes", "edge_refusal",
                "meta_refresh_from",
                "rendered_text_len", "render_settle_ms", "error", "skipped",
                "_raw_blocks"):
        if key in page_record:
            fresh[key] = page_record[key]
    page_record.clear()
    page_record.update(fresh)
    page_record.update(static)
    return True


def _keep_the_browser_on_this_origin(context, origin):
    """Let the page render; do not let it call anybody else.

    The audit sends GET and HEAD to one origin, whose robots.txt it has read.
    A browser driven at that same page does not: it loads fonts, scripts,
    images and iframes from hosts nobody asked, runs their JavaScript to
    network idle, and analytics libraries then send their hits by POST or
    `sendBeacon`. The audit issues no POST; the browser it drove did, to third
    parties, outside the request counts and the half-second spacing this
    marketplace promises.

    Same-origin requests are what rendering needs - the page's own bundle, its
    own stylesheet, its own data calls. Everything else is aborted, which also
    makes the pass faster and its measurement of the site's own text cleaner.
    """
    host = urlparse(origin).netloc.lower()

    def gate(route, request):
        try:
            target = urlparse(request.url)
        except ValueError:
            return route.abort()
        if target.scheme in ("data", "blob"):
            return route.continue_()
        if strip_www(target.netloc.lower()) == strip_www(host):
            return route.continue_()
        return route.abort()

    try:
        context.route("**/*", gate)
    except Exception:  # noqa: BLE001 - an unroutable context is not a site defect
        pass


def _render_pass(pages, notes, origin, crawl_deadline=None, required=False,
                 not_reproducible=None):
    """Playwright pass: measure how much text JavaScript adds.

    Absence of Playwright is a property of the auditing machine, never a
    finding about the site. `required` only changes how loudly that is said:
    someone who typed `--render` asked for this and should be told it did not
    happen, whereas on a default run it is ordinary.

    This pass replaces a page's text with the browser's, so which pages it
    reaches decides what every later check reads about them - which makes its
    page set part of the same promise the crawl's is. The set it works through
    is fixed (`_render_targets`, then the emptiest pages first) and it works
    through the whole of it; only the clock can cut it short, and when it does
    the reason goes in `not_reproducible` beside the crawl's own.
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
    adopted = []
    measured = 0
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch()
            context = browser.new_context(user_agent=USER_AGENT)
            _keep_the_browser_on_this_origin(context, origin)
            for page_record in _render_queue(pages, targets, adopted):
                if time.monotonic() > deadline:
                    # The one exit that is not the end of the queue, and the
                    # only thing here a second run can disagree with. It used
                    # to sit behind a page count planned from the measured cost
                    # of a render - the same arithmetic the crawl loop used to
                    # plan its own page count, with the same defect: a duration
                    # this run timed, rounded onto a ladder, deciding how much
                    # of the site the report is about. The queue is the plan
                    # now, and where the clock cuts it the snapshot says so
                    # rather than the pass claiming a set it chose.
                    notes.append(
                        "the rendered pass ran out of its {}s budget after {} page(s), so which "
                        "pages were read from the browser depends on how fast this run was. "
                        "The static reading of the rest still stands"
                        .format(RENDER_BUDGET_SECONDS, measured))
                    if not_reproducible is not None:
                        not_reproducible.append(
                            "the rendered pass was cut by the clock after {} page(s), so which "
                            "pages this report reads from the browser rather than from the "
                            "delivered HTML was decided by how fast this run was".format(
                                measured))
                    break
                measured += 1
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
                    rendered, waited_ms = _settled_text(tab)
                    page_record["rendered_text_len"] = len(re.sub(r"\s+", " ", rendered).strip())
                    page_record["render_settle_ms"] = waited_ms
                    if _adopt_rendered_dom(page_record, tab, origin):
                        adopted.append(page_record["url"])
                except Exception as exc:  # noqa: BLE001 - a render failure is not a site defect
                    page_record["render_error"] = truncate(str(exc), 200)
                finally:
                    tab.close()
            browser.close()
    except Exception as exc:  # noqa: BLE001
        notes.append("Playwright pass failed ({}); falling back to static analysis".format(truncate(str(exc), 120)))
        return "static"
    # Which thin pages the browser never reached. A check that grades a page
    # for a missing heading, description or viewport tag has to know whether it
    # is looking at the delivered stub or at the document a visitor sees, and
    # on a client-rendered site those are different pages.
    for page_record in pages:
        # `render_skipped` means "this may be a shell with content behind
        # JavaScript, so do not assert an absence about it". A release archive
        # carries no text because it is not a document, and marking it would
        # buy it that protection while telling the reader the browser missed a
        # page there was never a page at.
        if (page_record.get("status") == 200
                and not page_record.get("skipped")
                and (page_record.get("text_len") or 0) < RENDER_SHELL_TEXT
                and page_record.get("rendered_text_len") is None):
            page_record["render_skipped"] = True
    if adopted:
        notes.append(
            "{} of the {} page(s) rendered delivered almost no text until JavaScript ran, and "
            "were re-read from the browser's document so the rest of this audit judges the page "
            "a visitor sees. The delivered length is kept and is what the JavaScript finding "
            "reports".format(len(adopted), measured))
    return "rendered"


# Rendering costs seconds per page, so the sample is small. On a site where
# every page is a client-rendered shell a five-page sample leaves the other
# fifty-five in the snapshot as empty containers, and five skills then report a
# site with no headings, no navigation and nothing to quote. When the sample
# shows the shell is the site's whole delivery model, keep rendering until the
# budget runs out. The deadline still bounds it; nothing here can overrun.
RENDER_SHELL_TEXT = 300      # under this, the delivered page has nothing to read
RENDER_EXTEND_AFTER = 2      # adopted pages that make it the site's model, not one page


def _render_queue(pages, targets, adopted):
    """The sample first, then every other shell page while the budget lasts."""
    for page_record in targets:
        yield page_record
    if len(adopted) < RENDER_EXTEND_AFTER:
        return
    seen = {p["url"] for p in targets}
    rest = [p for p in pages
            if p.get("status") == 200
            and p["url"] not in seen
            and (p.get("text_len") or 0) < RENDER_SHELL_TEXT]
    # Emptiest first. Ordering by page type rendered ten of a software vendor's
    # twenty-eight pages and left the seven emptiest - 45 to 65 characters
    # each - unread, and three findings were then raised against exactly those
    # seven: no H1, no meta description, no viewport. The pages with the least
    # in them are the ones the browser has most to add to.
    for page_record in sorted(rest, key=lambda p: ((p.get("text_len") or 0), p["url"])):
        yield page_record


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
    # Visible in `--help`, not hidden. A guardrail sold as "enforced in code,
    # not promised in prose" cannot have an off-switch the prose does not
    # mention. It exists because the test fixtures serve their own robots.txt
    # from an ephemeral port, and `run_audit.py` never passes it.
    parser.add_argument("--ignore-robots", action="store_true",
                        help="test fixtures only: skip the robots.txt check. "
                             "Never used by run_audit.py and never for a real site.")
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
