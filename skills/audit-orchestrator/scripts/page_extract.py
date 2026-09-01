"""Turn one fetched HTML response into the page record stored in snapshot.json.

Extraction happens exactly once, here, during the single shared crawl. The six
sub-skills then read fields off the record instead of each re-parsing the same
HTML - that is what makes the marketplace one crawl rather than six.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from audit_common import (
    detect_challenge, detect_page_type, find_prices, main_text, make_soup,
    normalise_url, same_site, sentences, truncate, visible_text, word_count,
)

# Root containers frameworks mount into. An empty one means the delivered HTML
# is a shell and the content only exists after JavaScript runs.
SPA_ROOT_SELECTORS = (
    "#root", "#app", "#__next", "#__nuxt", "#gatsby-focus-wrapper",
    "[data-reactroot]", "[data-server-rendered]", "[ng-app]", "#ember-app",
    "#svelte", "#q-app",
)

STATE_BLOB_PATTERNS = (
    "__NEXT_DATA__", "__NUXT__", "__INITIAL_STATE__", "__APOLLO_STATE__",
    "__REDUX_STATE__", "__remixContext", "self.__next_f",
)

COOKIE_BANNER_HINTS = (
    "cookie-banner", "cookie-consent", "cookiebanner", "cookieconsent",
    "gdpr-banner", "consent-manager", "onetrust", "cookiebot", "cky-consent",
    "truste-banner", "privacy-banner", "cc-banner", "cmp-container",
)

MODAL_HINTS = (
    "newsletter-modal", "popup-overlay", "exit-intent", "interstitial",
    "subscribe-modal", "signup-modal", "welcome-mat",
)

VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "wistia", "loom.com", "brightcove")

# The extractor recorded video and never looked at audio, so a podcast episode
# page - the clearest case there is of content a machine cannot read without a
# transcript - was invisible to the transcript check.
AUDIO_HOSTS = (
    "soundcloud.com", "spotify.com/embed", "podbean.com", "buzzsprout.com",
    "libsyn.com", "megaphone.fm", "simplecast.com", "acast.com", "anchor.fm",
    "captivate.fm", "transistor.fm", "art19.com", "omnystudio.com", "audioboom.com",
)

TRANSCRIPT_HINTS = ("transcript", "captions", "subtitle", "read the transcript", "full text")

SOCIAL_PLATFORMS = {
    "linkedin.com": "LinkedIn",
    "wikipedia.org": "Wikipedia",
    "wikidata.org": "Wikidata",
    "crunchbase.com": "Crunchbase",
    "github.com": "GitHub",
    "twitter.com": "X",
    "x.com": "X",
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "youtube.com": "YouTube",
    "tiktok.com": "TikTok",
    "g.page": "Google Business",
    "goo.gl/maps": "Google Business",
    "maps.google": "Google Business",
    "yelp.com": "Yelp",
    "glassdoor": "Glassdoor",
    "trustpilot.com": "Trustpilot",
    "medium.com": "Medium",
    "pinterest.com": "Pinterest",
    "threads.net": "Threads",
    "bsky.app": "Bluesky",
}

# Ordinal suffixes are allowed on the day: "3rd November 2025" is how a great
# many real sites write a date, and requiring a bare digit reported those pages
# as carrying no date at all.
DATE_TEXT_RE = re.compile(
    r"\b(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+"
    r"\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
    r"\s+(?:19|20)\d{2}"
    r"|(?:19|20)\d{2}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/(?:19|20)\d{2})\b",
    re.I,
)
COPYRIGHT_YEAR_RE = re.compile(r"(?:©|\(c\)|copyright)\s*(?:\d{4}\s*[-–—]\s*)?((?:19|20)\d{2})", re.I)
# Only phrases that assert the copy is *current* as of a year. A bare "in 2019"
# is usually a founding date - history, not a staleness claim - and matching it
# turned every about page into a false positive.
AS_OF_YEAR_RE = re.compile(
    r"\b(?:as of|current as of|accurate as of|correct as of|last updated|updated (?:in|on|for)"
    r"|valid (?:for|until|through)|figures? for|data (?:from|for)|prices? (?:as of|for)"
    r"|edition|guide for|report for)\s+(?:\w+\s+){0,2}((?:19|20)\d{2})\b", re.I)

# `\b` before a leading zero used to leave it behind: "01904 555 812" came out
# as "1904 555 812", a different number, which then failed to match the same
# number written elsewhere on the site.
PHONE_RE = re.compile(
    r"(?<![\d/])"
    r"(?:\+\d{1,3}[\s.-]?)?"
    r"(?:\(0?\d{2,5}\)|0?\d{2,5})"
    r"[\s.-]?\d{3,4}[\s.-]?\d{2,4}"
    r"(?![\d/])")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")

# Alphanumeric formats first. A bare five- or six-digit run is also what a
# phone number looks like, and searching left to right on "…YO1 9TT. 01904 555
# 812." with the US branch first returned the area code as the postcode. The
# numeric branches now refuse to match anything sitting inside a longer run of
# digits and spaces, which is what a phone number is.
POSTCODE_HINT_RE = re.compile(
    r"\b(?:[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}"       # UK
    r"|[A-Z]\d[A-Z]\s*\d[A-Z]\d"                    # CA
    r"|(?<!\d)\d{5}(?:-\d{4})?(?![\s.-]?\d{3})(?!\d)"   # US, not a phone's area code
    r"|(?<!\d)\d{6}(?![\s.-]?\d{3})(?!\d))\b"           # IN / SG, likewise
)

# The original required a street-type word, so "41 Walmgate, York" matched
# nothing - and neither does most of the UK, where the street type is often
# part of the name (gate, row, mews, close) or absent entirely. The second
# branch accepts a number followed by capitalised words when a postcode follows
# close behind, which is what an address looks like when the word "Street" is
# not in it.
STREET_HINT_RE = re.compile(
    r"\b\d+[A-Za-z]?\s+[\w.'-]+(?:\s+[\w.'-]+){0,3}\s+"
    r"(?:street|st\.?|road|rd\.?|avenue|ave\.?|lane|ln\.?|drive|dr\.?|boulevard|blvd\.?|"
    r"way|court|ct\.?|place|pl\.?|suite|ste\.?|floor|marg|nagar|colony|gate|row|mews|"
    r"close|terrace|crescent|parade|walk|green|hill|square|sq\.?|parkway|pkwy\.?|"
    r"highway|hwy\.?|circle|cir\.?|trail|loop|alley|plaza|park|gardens|grove)\b"
    r"|\b\d+[A-Za-z]?\s+[A-Z][\w.'-]+(?:,?\s+[A-Z][\w.'-]+){0,3}"
    r"(?=[,\s]+(?:[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}|[A-Z]\d[A-Z]\s*\d[A-Z]\d|\d{5}))",
    re.I,
)

# A call to action is a link that asks the visitor to do something next.
# Matching a fixed list of marketing phrases missed most real ones ("Browse the
# range", "Speak to the trade counter"), so this matches on the shape instead:
# an imperative opening verb, or markup that styles the link as a button.
CTA_VERBS = frozenset("""
get start begin book buy shop browse view see request contact call order try
download subscribe join apply explore discover find learn read watch schedule
reserve sign register talk speak ask enquire inquire compare choose select plan
build create send email message visit check claim take open configure estimate
quote hire arrange play listen search play tour
""".split())

# Not a position. Sorts a call to action whose label is not in the body copy
# (an aria-label, or text inside an image) behind every located one.
UNLOCATED_CTA_OFFSET = 10 ** 6

CTA_MARKUP_RE = re.compile(r"\b(btn|button|cta|call-to-action|primary-action)\b", re.I)

NEWSLETTER_HINTS = ("newsletter", "subscribe", "mailing list", "email updates", "join our list")

GENERIC_H1 = {
    "welcome", "home", "homepage", "welcome to our website", "hello",
    "untitled", "index", "welcome!", "our website", "site",
}


# --------------------------------------------------------------------------

def extract_page(url, final_url, status, headers, html, redirect_chain, elapsed_ms,
                 depth, source, origin):
    """Build the snapshot record for one page."""
    soup = make_soup(html)
    body = soup.body or soup

    page_text = visible_text(soup)
    body_text = main_text(soup)
    jsonld, jsonld_errors = _extract_jsonld(soup)
    jsonld_types = _jsonld_types(jsonld)
    headings = _headings(soup)

    meta = _meta_tags(soup)
    og = {k: v for k, v in meta.items() if k.startswith("og:")}
    twitter = {k: v for k, v in meta.items() if k.startswith("twitter:")}

    links = _links(soup, final_url, origin)
    scripts = _scripts(soup, final_url)
    images = _images(soup, final_url)

    record = {
        "url": url,
        "final_url": final_url,
        "status": status,
        "redirect_chain": redirect_chain,
        "elapsed_ms": elapsed_ms,
        "depth": depth,
        "source": source,
        "content_type": (headers.get("content-type") or "").split(";")[0].strip().lower(),
        "headers": {
            k: v for k, v in headers.items()
            if k.lower() in ("content-type", "x-robots-tag", "last-modified", "server",
                             "cache-control", "content-encoding", "cf-cache-status")
        },
        "x_robots_tag": headers.get("x-robots-tag", ""),
        "html_len": len(html or ""),
        "title": _text_or_empty(soup.title),
        "meta_description": meta.get("description", ""),
        "meta_robots": meta.get("robots", ""),
        "canonical": _canonical(soup, final_url),
        "meta_refresh": _meta_refresh(soup, final_url),
        "lang": (soup.html.get("lang") if soup.html else "") or "",
        "has_viewport": "viewport" in meta,
        "og": og,
        "twitter": twitter,
        "headings": headings,
        "heading_sequence": _heading_sequence(soup),
        "sections": _sections(soup),
        "jsonld": jsonld,
        "jsonld_errors": jsonld_errors,
        "jsonld_types": jsonld_types,
        "microdata_count": len(soup.select("[itemscope]")),
        "rdfa_count": len(soup.select("[typeof], [vocab], [property]")),
        "text": page_text,
        "text_len": len(page_text),
        "body_text": body_text,
        "body_text_len": len(body_text),
        "word_count": word_count(body_text),
        "above_fold_text": truncate(body_text, 1500),
        "paragraphs": _paragraphs(soup),
        "links": links,
        "scripts": scripts,
        "images": images,
        "forms": _forms(soup),
        "iframes": _iframes(soup, final_url),
        "video": _video(soup, page_text, final_url),
        "pdf_links": _pdf_links(soup, final_url),
        "spa_shell": _spa_shell(soup, html, page_text),
        "challenge": detect_challenge(html, page_text),
        "noscript_text": truncate(" ".join(n.get_text(" ") for n in soup.select("noscript")), 400),
        "breadcrumb": _breadcrumb(soup, jsonld_types),
        "has_search": _has_search(soup),
        "cta": _cta(soup, body_text, origin, final_url),
        "dates": _dates(soup, page_text, jsonld),
        "contact_facts": _contact_facts(page_text, soup),
        "prices": find_prices(body_text),
        "social_profiles": _social_profiles(links["external"], jsonld),
        "interstitial": _interstitial(soup),
        "newsletter_signup": _newsletter(soup, page_text),
        "readability": _readability(body_text, soup),
        "chrome_signature": _chrome_signature(soup, origin, final_url),
    }

    record["page_type"] = detect_page_type(final_url, {
        "text": body_text,
        "headings": headings,
        "jsonld_types": jsonld_types,
        "title": record["title"],
    })
    return record


# --------------------------------------------------------------------------
# Field extractors
# --------------------------------------------------------------------------

def _text_or_empty(node):
    return re.sub(r"\s+", " ", node.get_text(" ")).strip() if node else ""


def _meta_tags(soup):
    out = {}
    for tag in soup.find_all("meta"):
        key = (tag.get("name") or tag.get("property") or tag.get("http-equiv") or "").strip().lower()
        if not key:
            continue
        out.setdefault(key, (tag.get("content") or "").strip())
    return out


_META_REFRESH_RE = re.compile(r"^\s*(\d+)\s*;\s*url\s*=\s*(.+?)\s*$", re.I)


def _meta_refresh(soup, base):
    """A client-side redirect: `<meta http-equiv="refresh" content="0; url=...">`.

    Worth recording for two reasons. A server-side crawler that does not follow
    it audits a stub - one real site answered its homepage with 216 bytes and a
    refresh, and the audit dutifully reported that it had no heading, no
    navigation and no call to action, all of which were true of the stub and
    none of which were true of the site. And a consumer that does not follow it
    sees exactly what we saw, so it is a finding in its own right.
    """
    tag = soup.find("meta", attrs={"http-equiv": re.compile(r"^refresh$", re.I)})
    if not tag or not tag.get("content"):
        return None
    match = _META_REFRESH_RE.match(tag["content"])
    if not match:
        return None
    target = normalise_url(match.group(2).strip("'\""), base)
    if not target:
        return None
    return {"delay": int(match.group(1)), "url": target}


def _canonical(soup, base):
    link = soup.find("link", rel=lambda v: v and "canonical" in [x.lower() for x in (v if isinstance(v, list) else [v])])
    if not link or not link.get("href"):
        return ""
    return normalise_url(link["href"], base) or ""


def _headings(soup):
    out = {}
    for level in ("h1", "h2", "h3"):
        out[level] = [_text_or_empty(h) for h in soup.find_all(level) if _text_or_empty(h)]
    return out


def _heading_sequence(soup):
    """Ordered `(level, text)` pairs, used to spot skipped levels."""
    seq = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = _text_or_empty(tag)
        if text:
            seq.append([int(tag.name[1]), truncate(text, 120)])
    return seq


# Headings that introduce a list of links rather than an answer. Judging these
# for "does it open with a concrete value" would penalise good practice: a
# related-links block is supposed to be links.
NAVIGATIONAL_HEADING_RE = re.compile(
    r"^\s*(?:related|read next|readnext|see also|further reading|keep reading|more (?:from|on|about)?"
    r"|where to go next|what to read next|next steps?|explore|learn more|useful links?"
    r"|quick links?|elsewhere|other pages?|browse|in this section|on this page|contents?"
    r"|table of contents|share|follow us|categories|tags|archive)\b", re.I)


def _sections(soup):
    """For each H2, the first paragraph beneath it plus how link-heavy it is.

    fact-extractability uses this to ask whether a section opens with a
    concrete value or with warm-up prose. `navigational` marks the sections
    where that question does not apply.
    """
    out = []
    for heading in soup.find_all("h2"):
        title = _text_or_empty(heading)
        if not title:
            continue
        first = ""
        section_text = []
        link_text = []
        for sibling in heading.next_elements:
            name = getattr(sibling, "name", None)
            if name in ("h1", "h2"):
                break
            if name == "a":
                link_text.append(_text_or_empty(sibling))
            if name in ("p", "li", "dd", "td"):
                text = _text_or_empty(sibling)
                section_text.append(text)
                if not first and len(text) >= 40:
                    first = text
        body_len = len(" ".join(section_text))
        link_len = len(" ".join(link_text))
        link_ratio = round(link_len / float(body_len), 2) if body_len else 0.0
        out.append({
            "heading": truncate(title, 160),
            "first_paragraph": truncate(first, 500),
            "link_ratio": link_ratio,
            "navigational": bool(NAVIGATIONAL_HEADING_RE.match(title)) or link_ratio > 0.7,
        })
        if len(out) >= 40:
            break
    return out


def _extract_jsonld(soup):
    """Parse every `application/ld+json` block, recording parse failures.

    A block that fails to parse is worse than a missing one: the site believes
    it has structured data and no consumer can read it.
    """
    blocks, errors = [], []
    for index, tag in enumerate(soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)})):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            errors.append({"index": index, "error": "empty <script type=application/ld+json> block"})
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append({
                "index": index,
                "error": "{} (line {}, col {})".format(exc.msg, exc.lineno, exc.colno),
                "excerpt": truncate(raw, 160),
            })
            continue
        for node in _flatten_jsonld(data):
            blocks.append(node)
    return blocks, errors


def _flatten_jsonld(data):
    """Flatten `@graph` containers and top-level arrays into a node list."""
    out = []
    if isinstance(data, list):
        for item in data:
            out.extend(_flatten_jsonld(item))
    elif isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            for item in data["@graph"]:
                out.extend(_flatten_jsonld(item))
            leftovers = {k: v for k, v in data.items() if k != "@graph"}
            if len(leftovers) > 1:
                out.append(leftovers)
        else:
            out.append(data)
    return out


def _jsonld_types(blocks):
    types = set()
    for node in blocks:
        value = node.get("@type")
        if isinstance(value, str):
            types.add(value.split("/")[-1])
        elif isinstance(value, list):
            types.update(str(v).split("/")[-1] for v in value)
    return sorted(types)


def _paragraphs(soup):
    out = []
    for tag in soup.find_all("p"):
        text = _text_or_empty(tag)
        if len(text) >= 40:
            out.append(truncate(text, 600))
        if len(out) >= 60:
            break
    return out


MAIN_REGION_SELECTOR = "main, article, [role=main], #main, #content"


def main_region(soup):
    """The page's own content, excluding site-wide chrome.

    One definition, used by both the link count and the CTA scan. When they
    disagreed, a footer link counted as a page's call to action and the
    dead-end check could never fire on any site with a footer.
    """
    return soup.select_one(MAIN_REGION_SELECTOR) or soup.body or soup


# Semantic navigation first. Plenty of real sites - including ones that predate
# `<nav>` and plenty that simply never adopted it - put their menu in a div with
# a class saying so, and we reported those as having no navigation at all: an
# eight-item menu in `<div class="menu mainmenu">` was read as "the primary
# navigation has 0 items", at high severity, on a site anyone can see has one.
NAV_SELECTOR = (
    "nav, header nav, [role=navigation], "
    "[class*=mainmenu], [class*=main-menu], [class*=main-nav], [class*=mainnav], "
    "[class*=navbar], [class*=nav-menu], [class*=menu-main], [class*=site-nav], "
    "[class*=primary-nav], [class*=topnav], [class*=top-nav], "
    "[id*=mainmenu], [id*=main-menu], [id*=main-nav], [id*=navbar], [id*=site-nav]"
)

# Minimum links before an unlabelled block counts as navigation. Below this it
# is a group of related links, not a menu.
NAV_SHAPE_MIN_LINKS = 3


def _nav_by_shape(soup):
    """Blocks that behave like a menu on sites that never say `nav`.

    Only consulted when nothing semantic matched, and only near the top of the
    document, so a footer link list or a body paragraph full of links is not
    mistaken for the primary navigation.
    """
    body = soup.body or soup
    candidates = []
    for node in body.find_all(["ul", "div", "header"], recursive=True)[:60]:
        anchors = node.find_all("a", href=True)
        if len(anchors) < NAV_SHAPE_MIN_LINKS:
            continue
        # A menu is mostly short labels, not sentences.
        labels = [_text_or_empty(a) for a in anchors]
        if not labels or sum(len(l) for l in labels) / float(len(labels)) > 40:
            continue
        if any(len(l) > 80 for l in labels):
            continue
        candidates.append((len(anchors), node))
        if len(candidates) >= 3:
            break
    return [node for _count, node in candidates[:1]]


def _links(soup, base, origin):
    internal, external, nav, footer = [], [], [], []
    main_region_node = main_region(soup)
    nav_nodes = soup.select(NAV_SELECTOR)
    if not nav_nodes:
        nav_nodes = _nav_by_shape(soup)
    footer_nodes = soup.select("footer, [role=contentinfo]")

    def collect(container, bucket):
        for anchor in container.find_all("a", href=True):
            url = normalise_url(anchor["href"], base)
            if url:
                bucket.append({"url": url, "text": truncate(_text_or_empty(anchor), 120)})

    for node in nav_nodes:
        collect(node, nav)
    for node in footer_nodes:
        collect(node, footer)

    main_links = []
    collect(main_region_node, main_links)

    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = normalise_url(href, base)
        if not url or url in seen:
            continue
        seen.add(url)
        entry = {"url": url, "text": truncate(_text_or_empty(anchor), 120),
                 "nofollow": "nofollow" in (anchor.get("rel") or [])}
        if same_site(url, origin):
            internal.append(entry)
        else:
            external.append(entry)

    main_internal = sorted({l["url"] for l in main_links if same_site(l["url"], origin)})
    return {
        "internal": internal[:300],
        "external": external[:150],
        "nav": _dedupe_links(nav)[:40],
        "footer": _dedupe_links(footer)[:60],
        "main_internal_count": len(main_internal),
        "main_internal_sample": main_internal[:10],
        "internal_count": len(internal),
        "external_count": len(external),
        "mailto_count": len(soup.select('a[href^="mailto:"]')),
        "tel_count": len(soup.select('a[href^="tel:"]')),
    }


def _dedupe_links(links):
    seen, out = set(), []
    for link in links:
        if link["url"] in seen:
            continue
        seen.add(link["url"])
        out.append(link)
    return out


def _scripts(soup, base):
    external, inline_bytes, hosts = [], 0, set()
    host = urlparse(base).netloc.lower()
    for tag in soup.find_all("script"):
        src = tag.get("src")
        if src:
            url = normalise_url(src, base)
            if url:
                external.append(url)
                script_host = urlparse(url).netloc.lower()
                if script_host and script_host != host:
                    hosts.add(script_host)
        else:
            inline_bytes += len(tag.string or tag.get_text() or "")
    style_bytes = sum(len(t.string or "") for t in soup.find_all("style"))
    return {
        "count": len(soup.find_all("script")),
        "external_count": len(external),
        "external_sample": sorted(set(external))[:10],
        "inline_bytes": inline_bytes,
        "style_bytes": style_bytes,
        "third_party_hosts": sorted(hosts),
        "third_party_host_count": len(hosts),
    }


def _images(soup, base):
    all_images = soup.find_all("img")
    missing_alt, content_images = [], 0
    for tag in all_images:
        src = normalise_url(tag.get("src") or tag.get("data-src") or "", base) or ""
        alt = tag.get("alt")
        # A decorative image is correctly marked `alt=""`; only a *missing*
        # alt attribute hides content from a machine.
        if alt is None:
            missing_alt.append(src)
        width = _int_attr(tag.get("width"))
        height = _int_attr(tag.get("height"))
        if (width and height and width * height >= 120000) or _is_hero(tag):
            content_images += 1
    return {
        "count": len(all_images),
        "missing_alt_count": len(missing_alt),
        "missing_alt_sample": [u for u in missing_alt if u][:5],
        "large_image_count": content_images,
        "svg_text_nodes": len(soup.select("svg text")),
        "canvas_count": len(soup.find_all("canvas")),
    }


def _int_attr(value):
    try:
        return int(str(value).strip().replace("px", ""))
    except (TypeError, ValueError):
        return 0


def _is_hero(tag):
    marker = " ".join(filter(None, [
        " ".join(tag.get("class") or []), tag.get("id") or "", tag.get("src") or "",
    ])).lower()
    return any(word in marker for word in ("hero", "banner", "masthead", "cover", "splash"))


def _forms(soup):
    out = []
    for form in soup.find_all("form"):
        inputs = form.find_all(["input", "select", "textarea"])
        visible = [i for i in inputs if (i.get("type") or "text").lower()
                   not in ("hidden", "submit", "button", "image", "reset")]
        required = [i for i in visible if i.has_attr("required")]
        text = _text_or_empty(form).lower()
        out.append({
            "action": (form.get("action") or "").strip(),
            "method": (form.get("method") or "get").lower(),
            "field_count": len(visible),
            "required_count": len(required),
            "is_search": _form_is_search(form, text),
            "is_newsletter": any(hint in text for hint in NEWSLETTER_HINTS),
        })
        if len(out) >= 15:
            break
    return out


def _form_is_search(form, text):
    if form.get("role") == "search":
        return True
    if form.select('input[type="search"]'):
        return True
    action = (form.get("action") or "").lower()
    return "search" in action or "/s/" in action or "search" in text[:60]


def _iframes(soup, base):
    out = []
    for tag in soup.find_all("iframe"):
        src = normalise_url(tag.get("src") or "", base) or (tag.get("src") or "")
        out.append({
            "src": truncate(src, 200),
            "title": truncate(tag.get("title") or "", 120),
            "is_video": any(host in src.lower() for host in VIDEO_HOSTS),
            "is_audio": any(host in src.lower() for host in AUDIO_HOSTS),
            "is_map": "map" in src.lower(),
        })
        if len(out) >= 12:
            break
    return out


def _video(soup, text, base):
    """Audio and video on the page, and whether a transcript sits beside it.

    Kept under one key because the transcript question is identical for both: a
    machine reading this page can quote the words only if the words are written
    down somewhere. `audio_count` exists because an episode page carrying a
    player and a two-line summary is the canonical instance of that problem and
    counting only `<video>` missed every one of them.
    """
    videos = soup.find_all("video")
    audios = soup.find_all("audio")
    frames = _iframes(soup, base)
    video_embeds = [f for f in frames if f["is_video"]]
    audio_embeds = [f for f in frames if f.get("is_audio")]
    lower = text.lower()
    return {
        "native_count": len(videos),
        "embed_count": len(video_embeds),
        "audio_count": len(audios) + len(audio_embeds),
        "autoplay_count": len([v for v in videos if v.has_attr("autoplay")]),
        "track_count": len(soup.select("video track, audio track")),
        "transcript_nearby": any(hint in lower for hint in TRANSCRIPT_HINTS),
    }


def _pdf_links(soup, base):
    out = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not re.search(r"\.pdf(\?|$)", href, re.I):
            continue
        out.append({
            "url": normalise_url(href, base) or href,
            "text": truncate(_text_or_empty(anchor), 140),
        })
        if len(out) >= 20:
            break
    return out


def _spa_shell(soup, html, page_text):
    """Signals that the delivered HTML is a container, not content."""
    root = None
    root_text_len = None
    for selector in SPA_ROOT_SELECTORS:
        node = soup.select_one(selector)
        if node is not None:
            root = selector
            root_text_len = len(re.sub(r"\s+", " ", node.get_text(" ")).strip())
            break
    blobs = [name for name in STATE_BLOB_PATTERNS if name in (html or "")]
    noscript = " ".join(n.get_text(" ") for n in soup.select("noscript")).lower()
    return {
        "root_selector": root,
        "root_text_len": root_text_len,
        "state_blobs": blobs,
        "state_blob_bytes": sum(len(m) for m in re.findall(r"__NEXT_DATA__.{0,200000}?</script>", html or "", re.S)),
        "noscript_demands_js": bool(re.search(r"enable\s+javascript|requires?\s+javascript|javascript.{0,20}(is\s+)?(required|disabled)", noscript)),
        "visible_text_len": len(page_text),
    }


def _breadcrumb(soup, jsonld_types):
    """Whether this page shows a trail, marks one up, or both.

    Kept apart deliberately. A visitor who landed mid-journey needs the visible
    trail; a machine needs the markup. Treating JSON-LD as proof of a visible
    breadcrumb made the visible-trail check impossible to fail.
    """
    visible = bool(soup.select(
        '[class*="breadcrumb"], [id*="breadcrumb"], nav[aria-label*="readcrumb"]'))
    markup = "BreadcrumbList" in jsonld_types or bool(
        soup.select('[itemtype*="BreadcrumbList"]'))
    return {"visible": visible, "markup": markup, "any": visible or markup}


def _has_search(soup):
    if soup.select('input[type="search"], form[role="search"], [role="searchbox"]'):
        return True
    return any(f["is_search"] for f in _forms(soup))


def _cta(soup, body_text, origin, base):
    """The first call-to-action link and where it sits in the body text.

    Position matters: a CTA 4,000 characters down is not orientation.
    """
    lowered = body_text.lower()
    first_offset = None
    first_text = ""
    first_url = ""
    in_main = False
    first_located = False
    main_anchors = {id(a) for a in main_region(soup).find_all("a", href=True)}
    for anchor in soup.find_all("a", href=True):
        text = _text_or_empty(anchor)
        low = text.lower().strip()
        if not low or len(low) > 60:
            continue
        first_word = re.sub(r"[^a-z]", "", low.split()[0]) if low.split() else ""
        marker = " ".join(filter(None, [
            " ".join(anchor.get("class") or []), anchor.get("id") or "",
            anchor.get("role") or "",
        ]))
        is_cta = first_word in CTA_VERBS or bool(CTA_MARKUP_RE.search(marker))
        if is_cta:
            offset = lowered.find(low[:40])
            located = offset >= 0
            if not located:
                # Sorts last without pretending to be a position. Anything
                # printing this number must check `offset_known` first.
                offset = UNLOCATED_CTA_OFFSET
            if id(anchor) in main_anchors:
                in_main = True
            if first_offset is None or offset < first_offset:
                first_offset = offset
                first_located = located
                first_text = text
                first_url = normalise_url(anchor["href"], base) or ""
    buttons = len(soup.select('button, [role="button"], input[type="submit"], .btn, .button'))
    return {
        "found": first_offset is not None,
        "text": truncate(first_text, 80),
        "url": first_url,
        "offset": first_offset if first_offset is not None else -1,
        # False when the link exists but its label could not be found in the
        # body copy, so `offset` is a sort key and not a character position.
        "offset_known": bool(first_offset is not None and first_located),
        "within_first_1500": bool(first_offset is not None and first_offset <= 1500),
        # A "Contact" link in the global footer is not this page's next step.
        # Without this distinction the dead-end check was unfirable.
        "in_main": in_main,
        "button_count": buttons,
    }


def _dates(soup, text, jsonld):
    """Every freshness signal a machine could read off this page."""
    machine = []
    for tag in soup.find_all("time"):
        value = tag.get("datetime") or _text_or_empty(tag)
        if value:
            machine.append(value.strip())
    for node in jsonld:
        for key in ("datePublished", "dateModified", "uploadDate", "dateCreated"):
            value = node.get(key)
            if isinstance(value, str) and value:
                machine.append(value.strip())
    visible = [m.group(0) for m in DATE_TEXT_RE.finditer(text)][:10]
    copyright_years = []
    for match in COPYRIGHT_YEAR_RE.finditer(text):
        copyright_years.append(int(match.group(1)))
        # Some footers list every year rather than a range ("© 2002 2003 2004
        # … 2026"). Reading only the anchored year reported the site as 24
        # years stale, so take the whole run that follows.
        tail = text[match.end():match.end() + 160]
        run = re.match(r"(?:\s*[-–—,]?\s*(?:19|20)\d{2})+", tail)
        if run:
            copyright_years.extend(int(y) for y in re.findall(r"(?:19|20)\d{2}", run.group(0)))
    as_of_years = [int(m.group(1)) for m in AS_OF_YEAR_RE.finditer(text)]
    return {
        "machine_readable": sorted(set(machine))[:10],
        "visible": visible,
        "copyright_years": sorted(set(copyright_years)),
        "as_of_years": sorted(set(as_of_years)),
        "has_any": bool(machine or visible),
        "jsonld_date_published": _first_jsonld_date(jsonld, "datePublished"),
        "jsonld_date_modified": _first_jsonld_date(jsonld, "dateModified"),
    }


def _first_jsonld_date(jsonld, key):
    for node in jsonld:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _contact_facts(text, soup):
    """Plain-text facts an assistant would try to quote for "where/how" questions."""
    emails = sorted(set(EMAIL_RE.findall(text)))[:5]
    tel_links = [a.get("href", "")[4:].strip() for a in soup.select('a[href^="tel:"]')][:5]
    phones = tel_links or [
        m.group(0).strip() for m in PHONE_RE.finditer(text)
        if len(re.sub(r"\D", "", m.group(0))) >= 9
    ][:3]
    street = STREET_HINT_RE.search(text)
    postcode = POSTCODE_HINT_RE.search(text)
    return {
        "emails": emails,
        "phones": phones,
        # Numbers the site put in a `tel:` href, so declared rather than
        # inferred. Comparing these between pages is safe; comparing the
        # regex-scraped ones above is not.
        "declared_phones": tel_links,
        "has_email": bool(emails or soup.select('a[href^="mailto:"]')),
        "has_phone": bool(phones),
        "street_hint": truncate(street.group(0), 120) if street else "",
        "postcode_hint": postcode.group(0) if postcode else "",
        "has_address": bool(street and postcode) or bool(soup.select('address, [itemtype*="PostalAddress"]')),
        "has_contact_form": any(
            not f["is_search"] and not f["is_newsletter"] and f["field_count"] >= 2
            for f in _forms(soup)
        ),
    }


# A share button points at a social platform but is not a profile on it.
# Counting `facebook.com/sharer/sharer.php?u=...` as the brand's Facebook page
# inflated corroboration on every site with share widgets.
_SHARE_URL_RE = re.compile(
    r"/(?:sharer|share|shareArticle|intent|share_url|submit)\b"
    r"|share\.php|/intent/(?:tweet|post)|[?&](?:u|url|text|via|mini)=", re.I)

# LinkedIn personal profiles (`/in/<person>`) are people, not the organisation.
# One was being counted as a company's own LinkedIn because a customer story
# linked to an individual.
_PROFILE_PATH_RULES = {
    "LinkedIn": re.compile(r"linkedin\.com/(?:company|school|showcase)/", re.I),
}


def _social_profiles(external_links, jsonld):
    """Authoritative off-site profiles this page points at.

    Only genuine profile URLs count. Share widgets and personal profiles are
    excluded, because both would overstate how well corroborated a brand is.
    """
    found = {}
    candidates = [link["url"] for link in external_links]
    for node in jsonld:
        same_as = node.get("sameAs")
        if isinstance(same_as, str):
            candidates.append(same_as)
        elif isinstance(same_as, list):
            candidates.extend(str(v) for v in same_as)

    for url in candidates:
        low = url.lower()
        if _SHARE_URL_RE.search(low):
            continue
        for domain, platform in SOCIAL_PLATFORMS.items():
            if domain not in low:
                continue
            rule = _PROFILE_PATH_RULES.get(platform)
            if rule and not rule.search(low):
                continue
            found.setdefault(platform, url)
    return dict(sorted(found.items()))


def _is_hidden(node):
    """Markup that says this element is not displayed."""
    if node.get("hidden") is not None:
        return True
    if str(node.get("aria-hidden", "")).lower() == "true":
        return True
    style = (node.get("style") or "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style or "opacity:0" in style


def _interstitial(soup):
    markup = str(soup)[:200000].lower()
    cookie = [hint for hint in COOKIE_BANNER_HINTS if hint in markup]
    modal = [hint for hint in MODAL_HINTS if hint in markup]
    # Substring class matching flagged 18 of 20 pages on a real retail site,
    # because `class="modal-opener"` contains both "modal" and "open". Match
    # whole class tokens, and require the element to be genuinely displayed.
    # Markup that states it is open.
    blocking = bool(soup.select(
        'dialog[open], '
        '[class~="modal"][class~="open"], [class~="modal"][class~="is-open"], '
        '[class~="overlay"][class~="active"], [class~="modal"][class~="show"]'
    ))

    # An `aria-modal` dialog is NOT evidence on its own. Every dialog component
    # in every component library carries `role="dialog" aria-modal="true"` in
    # the DOM while closed, so presence alone reported 22 pages of one real
    # site as covering their content the moment a visitor arrives. It counts
    # only when it is not hidden and something else says it is showing.
    #
    # The previous loop also cleared `blocking` outright when the *last* such
    # node was hidden, discarding a genuine overlay found by the selectors
    # above.
    for node in soup.select('[aria-modal="true"][role="dialog"], [role="dialog"]'):
        if _is_hidden(node):
            continue
        tokens = {c.lower() for c in (node.get("class") or [])}
        identity = " ".join(tokens | {str(node.get("id") or "").lower()})
        if tokens & {"open", "is-open", "active", "show", "shown", "visible", "is-visible"}:
            blocking = True
        # A consent wall or a newsletter interstitial is a different animal
        # from a component-library dialog: it is displayed on first paint by
        # design, which is the whole point of it. Named by its own class, and
        # not hidden, it counts.
        elif any(hint in identity for hint in COOKIE_BANNER_HINTS + MODAL_HINTS):
            blocking = True
    return {
        "cookie_banner_hints": cookie[:5],
        "modal_hints": modal[:5],
        "blocking_overlay": blocking,
    }


def _newsletter(soup, text):
    lower = text.lower()
    if any(f["is_newsletter"] for f in _forms(soup)):
        return True
    if soup.select('input[type="email"]') and any(hint in lower for hint in NEWSLETTER_HINTS):
        return True
    return False


def _readability(body_text, soup):
    sents = sentences(body_text)
    lengths = [word_count(s) for s in sents]
    long_sentences = [n for n in lengths if n > 30]
    paragraphs = [_text_or_empty(p) for p in soup.find_all("p")]
    walls = [p for p in paragraphs if word_count(p) > 200]
    words = word_count(body_text)
    subheadings = len(soup.find_all(["h2", "h3", "h4"]))
    return {
        "sentence_count": len(sents),
        "avg_sentence_words": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
        "long_sentence_count": len(long_sentences),
        "long_sentence_share": round(len(long_sentences) / len(lengths), 3) if lengths else 0.0,
        "wall_of_text_count": len(walls),
        "subheading_count": subheadings,
        "words_per_subheading": round(words / subheadings, 1) if subheadings else float(words),
    }


def _chrome_signature(soup, origin, base):
    """A stable fingerprint of the header/footer nav.

    Pages whose chrome differs from the rest of the site strand a visitor who
    arrived mid-journey with no way back.
    """
    urls = set()
    for node in soup.select("nav, header, footer, [role=navigation], [role=contentinfo]"):
        for anchor in node.find_all("a", href=True):
            url = normalise_url(anchor["href"], base)
            if url and same_site(url, origin):
                urls.add(urlparse(url).path.rstrip("/") or "/")
    return {"nav_paths": sorted(urls)[:40], "nav_path_count": len(urls)}
