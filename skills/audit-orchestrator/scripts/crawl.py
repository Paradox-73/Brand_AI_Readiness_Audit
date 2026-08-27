#!/usr/bin/env python3
"""Fetch a bounded, deterministic sample of a site and write snapshot.json.

One crawl feeds all six sub-skills. The budget is fixed (seed 42, 30 pages,
240 s wall clock) so the same site produces the same page set on every run,
which is what makes the findings reproducible.

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
from collections import defaultdict, deque
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit_common import (  # noqa: E402
    MAX_DEPTH, MAX_PAGES, MAX_SITEMAP_SAMPLE, REQUEST_DELAY, REQUEST_TIMEOUT,
    SEED, USER_AGENT, WALL_CLOCK_BUDGET, FetchError, Fetcher, detect_page_type,
    eprint, is_forbidden_path, normalise_url, origin_of, same_site, site_label,
    truncate, write_json,
)
from page_extract import extract_page  # noqa: E402
from robots_parser import (  # noqa: E402
    blocks_entire_site, is_disallowed, parse_robots, path_of,
)

SNAPSHOT_SCHEMA_VERSION = 1

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
    text = response.text or ""
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
        try:
            root = ET.fromstring(response.content)
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

    if content_type and "html" not in content_type and "xml" not in content_type:
        return {
            "url": url, "final_url": response.url, "status": response.status_code,
            "depth": depth, "source": source, "redirect_chain": chain,
            "content_type": content_type, "skipped": "non-HTML content type",
            "page_type": "other", "links": {"internal": [], "external": []},
            "text": "", "text_len": 0, "html_len": len(response.content or b""),
            "headers": headers,
        }

    html = response.text or ""
    record = extract_page(
        url=url, final_url=response.url, status=response.status_code, headers=headers,
        html=html, redirect_chain=chain, elapsed_ms=getattr(response, "elapsed_ms", 0),
        depth=depth, source=source, origin=origin,
    )
    return record


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

    for page in pages:
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
                authoritative.append({"name": name, "source": "jsonld:Organization"})
            alternate = node.get("alternateName")
            for value in ([alternate] if isinstance(alternate, str) else (alternate or [])):
                cleaned = clean(value)
                if cleaned:
                    alternates.add(cleaned)
        site_name = clean(page.get("og", {}).get("og:site_name"))
        if site_name:
            authoritative.append({"name": site_name, "source": "og:site_name"})

    if home:
        title = home.get("title") or ""
        parts = [clean(p) for p in re.split(r"\s[|\-–—:·•]\s", title)]
        parts = [p for p in parts if p]
        if len(parts) > 1:
            # The brand is conventionally the shorter end of "Brand | Tagline".
            ordered = sorted([parts[0], parts[-1]], key=len)
            for index, value in enumerate(ordered):
                fallback.append({"name": value, "source": "title-part-{}".format(index)})
        elif parts:
            fallback.append({"name": parts[0], "source": "title"})

    host = urlparse(origin).netloc.lower()
    host_brand = re.sub(r"^www\.", "", host).split(".")[0].replace("-", " ").strip()
    if host_brand and not re.match(r"^\d+$", host_brand):
        fallback.append({"name": host_brand.title(), "source": "domain"})

    priority = {"jsonld:Organization": 0, "og:site_name": 1,
                "title-part-0": 2, "title-part-1": 3, "title": 4, "domain": 5}
    ranked = sorted(authoritative + fallback,
                    key=lambda c: (priority.get(c["source"], 9), c["name"].lower()))
    chosen = ranked[0] if ranked else {"name": host_brand.title() or origin, "source": "domain"}

    return {
        "name": chosen["name"],
        "source": chosen["source"],
        "authoritative_variants": sorted({c["name"] for c in authoritative}),
        "authoritative_sources": sorted({c["source"] for c in authoritative}),
        "alternate_names": sorted(alternates),
        "fallback_candidates": sorted({c["name"] for c in fallback}),
        "host": host,
        "domain_token": host_brand,
    }


def crawl(target, out_path, max_pages=MAX_PAGES, budget_s=WALL_CLOCK_BUDGET,
          respect_robots=True, delay=REQUEST_DELAY, render=False):
    origin, seed_url = normalise_target(target)
    started = time.monotonic()
    deadline = started + budget_s
    fetcher = Fetcher(delay=delay, timeout=REQUEST_TIMEOUT, deadline=deadline)

    notes = []
    robots = fetch_robots(fetcher, origin)
    sitemaps, sitemap_in_robots = fetch_sitemaps(fetcher, origin, robots, deadline)

    llms_txt = {"url": origin.rstrip("/") + "/llms.txt", "present": False, "status": None}
    probe = fetcher.try_get(llms_txt["url"], allow_redirects=False)
    if probe is not None:
        llms_txt["status"] = probe.status_code
        llms_txt["present"] = probe.status_code == 200 and "<html" not in (probe.text or "")[:400].lower()

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
            if url and url not in queued:
                queue.append((url, 0, "homepage" if url == home_url else "seed"))
                queued.add(url)

        sitemap_sample = sample_sitemap_urls(sitemaps, origin)
        for url in sitemap_sample:
            ok, reason = crawlable(url, origin, robots, respect_robots)
            if not ok:
                skipped.append({"url": url, "reason": reason})
                continue
            if url not in queued:
                queue.append((url, 1, "sitemap"))
                queued.add(url)

        budget_exhausted = False
        while queue and len(pages) < max_pages:
            if time.monotonic() >= deadline:
                budget_exhausted = True
                notes.append("wall-clock budget of {:.0f}s reached; crawl stopped cleanly".format(budget_s))
                break
            url, depth, source = queue.popleft()
            record = fetch_page(fetcher, url, depth, source, origin)
            # Two queued URLs that redirect to the same destination are one
            # page; keeping both would double-count every finding on it.
            landed = normalise_url(record.get("final_url") or url) or url
            if landed != url and landed in fetched_final:
                skipped.append({"url": url, "reason": "redirects to an already-crawled page"})
                continue
            fetched_final.add(landed)
            pages.append(record)

            if record.get("status") != 200 or depth >= MAX_DEPTH:
                continue
            # Breadth-first expansion from in-page links, sorted for determinism.
            for link in sorted(
                {l["url"] for l in (record.get("links", {}).get("internal") or [])}
            ):
                if len(queued) >= max_pages * 3:
                    break
                if link in queued:
                    continue
                ok, reason = crawlable(link, origin, robots, respect_robots)
                if not ok:
                    if reason == "disallowed by robots.txt":
                        skipped.append({"url": link, "reason": reason})
                    continue
                queued.add(link)
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

    render_mode = "static"
    if render:
        render_mode = _render_pass(pages, notes)

    pages.sort(key=lambda p: (0 if p.get("page_type") == "home" else 1, p["url"]))
    elapsed = time.monotonic() - started

    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "site": site_label(origin),
        "origin": origin,
        "seed_url": seed_url,
        "brand": detect_brand(pages, origin),
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


def _render_pass(pages, notes):
    """Optional Playwright pass: measure how much text JavaScript adds.

    Absence of Playwright is a property of the auditing machine, never a
    finding about the site.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        notes.append("Playwright not installed; static-only pass (this is not a site defect)")
        return "static"

    targets = [p for p in pages if p.get("status") == 200][:5]
    if not targets:
        return "static"
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch()
            context = browser.new_context(user_agent=USER_AGENT)
            for page_record in targets:
                tab = context.new_page()
                try:
                    tab.goto(page_record["final_url"], wait_until="networkidle", timeout=15000)
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
                        help="add a Playwright pass if Playwright is installed")
    parser.add_argument("--ignore-robots", action="store_true",
                        help=argparse.SUPPRESS)  # test fixtures only
    args = parser.parse_args(argv)

    snapshot = crawl(
        args.target, args.out, max_pages=args.max_pages, budget_s=args.budget,
        respect_robots=not args.ignore_robots, delay=args.delay, render=args.render,
    )
    crawl_info = snapshot["crawl"]
    eprint("crawled {} pages ({} ok) in {}s -> {}".format(
        crawl_info["pages_crawled"], crawl_info["pages_ok"], crawl_info["elapsed_s"], args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
