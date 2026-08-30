"""One-thing-at-a-time tests: break exactly one property, expect exactly one finding.

Why this exists
---------------
Every other test we have compares the audit against sites we chose. That proves
the audit does what we tuned it to do on those sites; it cannot prove a check
detects the thing it claims to detect. A check that fires because a page is
long, and happens to correlate with the defect on our sample, passes a fixture
test and fails in the wild. We found nine of those by hand.

This suite removes the site from the question. `good-site` audits clean - zero
findings. Each mutation below takes a copy of it, breaks one named property,
and the test asserts two things:

    1. the root cause that property maps to now fires   (the check detects it)
    2. nothing else fires                               (the check is specific)

The second assertion is the one that matters. A check whose finding appears
only when its own cause is introduced is measuring that cause. A check that
lights up whenever the page changes shape is measuring something else, and
`also` below is where that gets admitted in writing rather than discovered on
a judge's laptop.

Anything listed in `also` is a knock-on we have looked at and accepted - real
consequences of the same edit, not tolerated noise. Keep those lists short. A
mutation needing a long `also` list is telling you the checks are entangled.
"""

from __future__ import annotations

import json
import os
import re

# --------------------------------------------------------------------------
# Edit helpers. Every mutation works on a throwaway copy of the fixture.
# --------------------------------------------------------------------------

def html_files(site):
    """Every HTML file in the site, as absolute paths."""
    found = []
    for directory, _, names in os.walk(site):
        for name in sorted(names):
            if name.endswith(".html"):
                found.append(os.path.join(directory, name))
    return sorted(found)


def edit(site, relative, transform):
    """Apply `transform` to one file's text."""
    path = os.path.join(site, relative.replace("/", os.sep))
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(transform(text))


def edit_all(site, transform, skip=()):
    """Apply `transform` to every HTML file except the named ones."""
    skipped = {os.path.join(site, name.replace("/", os.sep)) for name in skip}
    for path in html_files(site):
        if path in skipped:
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(transform(text))


def rules(site, payload):
    """Write the fixture server's `_rules.json`, merging with anything present."""
    path = os.path.join(site, "_rules.json")
    current = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            current = json.load(handle)
    for key, value in payload.items():
        if isinstance(value, dict):
            current.setdefault(key, {}).update(value)
        else:
            current.setdefault(key, []).extend(value)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(current, handle)


def add_file(site, relative, body):
    path = os.path.join(site, relative.replace("/", os.sep))
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)


def drop(pattern, flags=re.S | re.I):
    """A transform that deletes every match of `pattern`."""
    compiled = re.compile(pattern, flags)
    return lambda text: compiled.sub("", text)


def swap(pattern, replacement, flags=re.S | re.I):
    compiled = re.compile(pattern, flags)
    return lambda text: compiled.sub(replacement, text)


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------
#
# Each entry:
#   name      what property is being broken, in words
#   expect    root causes that MUST appear once it is broken
#   also      knock-ons we have reviewed and accept
#   exclusive False when the edit genuinely damages the page in several ways
#             at once (emptying a page really does remove its facts as well as
#             its markup). Those assert detection only, not specificity.
#   apply     the edit itself

MUTATIONS = []


def mutation(name, expect, apply, also=(), exclusive=True):
    MUTATIONS.append({
        "name": name,
        "expect": set(expect),
        "also": set(also),
        "exclusive": exclusive,
        "apply": apply,
    })


# --- Gate A: can a crawler get in at all -----------------------------------

mutation(
    "robots.txt disallows the answer-engine crawlers",
    expect={"robots-block"},
    apply=lambda site: edit(
        site, "robots.txt",
        lambda text: "User-agent: GPTBot\nDisallow: /\n\n"
                     "User-agent: PerplexityBot\nDisallow: /\n\n"
                     "User-agent: ClaudeBot\nDisallow: /\n\n" + text),
)

mutation(
    "a bot manager returns 403 to a named AI crawler",
    expect={"bot-manager-block"},
    apply=lambda site: rules(site, {"block_user_agents": ["GPTBot"]}),
)

mutation(
    "the sitemap is gone",
    expect={"sitemap-missing"},
    apply=lambda site: (
        os.remove(os.path.join(site, "sitemap.xml")),
        edit(site, "robots.txt", drop(r"Sitemap:.*")),
    ),
)

mutation(
    "a page linked from the navigation returns 404",
    # `non-200` covers pages the crawl was told exist; `sitemap-broken` covers
    # the sitemap entry; `broken-links` covers the link that points at it. All
    # three are true here and each carries a different fix.
    expect={"sitemap-broken", "broken-links"},
    apply=lambda site: rules(site, {"status": {"/faq.html": 404}}),
)

mutation(
    "a page is served with X-Robots-Tag: noindex",
    expect={"noindex"},
    apply=lambda site: rules(
        site, {"headers": {"/pricing.html": {"X-Robots-Tag": "noindex"}}}),
)

mutation(
    "canonical tags point at a page that does not exist",
    expect={"canonical-broken"},
    apply=lambda site: edit_all(
        site, swap(r'<link rel="canonical" href="[^"]*">',
                   '<link rel="canonical" href="{{BASE}}/nowhere.html">')),
)


# --- Gate A/C: can a machine read what comes back --------------------------

mutation(
    "content pages ship an empty div and a bundle instead of HTML",
    expect={"js-shell"},
    exclusive=False,
    apply=lambda site: edit_all(
        site,
        swap(r"<body>.*</body>",
             '<body><div id="root"></div>'
             '<script src="{{BASE}}/assets/app.bundle.js"></script></body>'),
        skip=("index.html",)),
)

mutation(
    "the facts on a page exist only inside an image",
    expect={"image-locked-facts"},
    exclusive=False,
    apply=lambda site: edit_all(
        site,
        swap(r"<main>.*</main>",
             '<main><h1>Pricing</h1>'
             '<img src="{{BASE}}/assets/prices.png" alt="" width="900" height="700">'
             '<img src="{{BASE}}/assets/specs.png" alt="" width="900" height="700">'
             '</main>'),
        skip=("index.html",)),
)

PDF_STUB = os.linesep.join([
    "%PDF-1.4",
    "1 0 obj<</Type/Catalog>>endobj",
    "trailer<</Root 1 0 R>>",
    "%%EOF",
])

mutation(
    "the substance is in linked PDFs rather than on the page",
    expect={"pdf-locked-facts"},
    apply=lambda site: (
        [add_file(site, "docs/{}.pdf".format(n), PDF_STUB)
         for n in ("datasheet", "pricing", "security")],
        edit_all(
            site,
            swap(r"</main>",
             '<p>Full details are in the '
             '<a href="{{BASE}}/docs/datasheet.pdf">datasheet (PDF)</a>, the '
             '<a href="{{BASE}}/docs/pricing.pdf">price list (PDF)</a> and the '
             '<a href="{{BASE}}/docs/security.pdf">security overview (PDF)</a>.'
             '</p></main>')),
    ),
)

mutation(
    "the page body is an iframe",
    expect={"iframe-content"},
    exclusive=False,
    apply=lambda site: edit_all(
        site,
        swap(r"<main>.*</main>",
             '<main><iframe src="https://embed.example.invalid/app" '
             'width="1200" height="900" title="Application"></iframe></main>'),
        skip=("index.html",)),
)


# --- Gate C: is anything machine-readable ---------------------------------

mutation(
    "all JSON-LD is removed",
    expect={"no-org-schema"},
    also={"no-article-schema", "no-breadcrumb-markup", "no-faq-schema", "no-website-schema",
          "no-product-schema"},
    apply=lambda site: edit_all(
        site, drop(r'<script type="application/ld\+json">.*?</script>')),
)

mutation(
    "the JSON-LD block is malformed",
    expect={"invalid-jsonld"},
    also={"no-org-schema", "no-article-schema", "no-breadcrumb-markup", "no-faq-schema",
          "no-website-schema", "no-product-schema"},
    apply=lambda site: edit_all(
        site, swap(r'(<script type="application/ld\+json">\s*\{)',
                   r'\1 "broken": ,')),
)

mutation(
    "Article markup is stripped from the posts",
    expect={"no-article-schema"},
    apply=lambda site: edit_all(
        site, drop(r'<script type="application/ld\+json">\s*\{[^<]*?"@type":\s*"Article".*?</script>')),
)

mutation(
    "BreadcrumbList markup is stripped but the visible trail stays",
    expect={"no-breadcrumb-markup"},
    apply=lambda site: edit_all(
        site, drop(r'<script type="application/ld\+json">\s*\{[^<]*?"@type":\s*"BreadcrumbList".*?</script>')),
)

mutation(
    "every meta description is removed",
    expect={"meta-hygiene"},
    apply=lambda site: edit_all(
        site, drop(r'<meta name="description"[^>]*>')),
)


# --- Gate C: is there a sentence worth quoting -----------------------------

mutation(
    "the homepage never says what the company is",
    expect={"no-entity-definition"},
    # Every copular sentence about the brand has to go, under any name the site
    # uses for itself - not just the one on the homepage. The fixture also says
    # "Brightpath is built for retail operations teams at ...", which is a
    # perfectly good definition and the check is right to accept it.
    apply=lambda site: edit_all(
        site,
        swap(r"<p>\s*(?:Brightpath(?: Analytics)?) (?:is|are|was|were) [^<]*</p>",
             "<p>We believe the future belongs to those who move first. They "
             "know it. It is why they choose us, and why they stay.</p>")),
)

mutation(
    "every section opens with warm-up prose instead of the answer",
    expect={"fluff-first"},
    apply=lambda site: edit_all(
        site,
        swap(r"(<h2>[^<]*</h2>\s*)<p>",
             r"\1<p>In a fast-moving landscape, it has never been more "
             r"important to think carefully about this. Many organisations "
             r"struggle here, and we understand why. ")),
)

mutation(
    "no page states a price in plain text",
    expect={"missing-core-fact"},
    apply=lambda site: edit_all(site, swap(r"\$[\d,]+", "market rate")),
)

mutation(
    "the brand spells its own name three different ways",
    expect={"name-inconsistency"},
    # The N in NAP is the name. A site that spells itself three ways is
    # genuinely inconsistent on contact identity too.
    also={"nap-inconsistency"},
    apply=lambda site: (
        edit(site, "about.html", swap(r"Brightpath Analytics", "BrightPath Analytica")),
        edit(site, "pricing.html", swap(r"Brightpath Analytics", "Bright Path Analytics")),
        edit(site, "faq.html", swap(r"Brightpath Analytics", "Brightpath Analitics")),
    ),
)

mutation(
    "no page has an H1",
    expect={"heading-structure"},
    # Removing every H1 removes the homepage's, so the homepage really does
    # stop orienting anyone.
    also={"no-orientation"},
    apply=lambda site: edit_all(site, swap(r"<h1>(.*?)</h1>", r'<p class="t">\1</p>')),
)


# --- Mechanism D: is it current, corroborated and unambiguous --------------

def _age_dates(text):
    """Move every date back to 2019 without touching URLs that contain a year."""
    text = re.sub(r"(?<![-/\w])(20[2-9][0-9])(?=-[0-9]{2}-[0-9]{2})", "2019", text)
    text = re.sub(r"(?<![-/\w.])(20[2-9][0-9])(?![-/\w.])", "2019", text)
    return text


mutation(
    "every date on the site is seven years old",
    expect={"stale-content"},
    also={"no-date-signal"},
    apply=lambda site: (
        edit_all(site, _age_dates),
        edit(site, "sitemap.xml", _age_dates),
    ),
)


mutation(
    "nothing on the site carries a date",
    expect={"no-date-signal"},
    # datePublished and dateModified are required Article properties, so
    # stripping them really does leave the markup incomplete.
    also={"missing-schema-props"},
    apply=lambda site: (
        edit_all(site, drop(r"<time[^>]*>.*?</time>")),
        edit_all(site, drop(r'"date(Published|Modified)":\s*"[^"]*",')),
        edit(site, "sitemap.xml", drop(r"<lastmod>.*?</lastmod>")),
    ),
)

mutation(
    "the brand links to one off-site profile instead of seven",
    expect={"weak-corroboration"},
    apply=lambda site: (
        edit_all(site, swap(r'"sameAs":\s*\[.*?\]',
                            '"sameAs": ["https://x.com/brightpath_example"]')),
        edit_all(site, drop(
            r'<a href="https://(www\.)?(linkedin|github|youtube|instagram|wikidata|crunchbase)[^"]*">[^<]*</a>')),
    ),
)

# --- Engagement: does the visitor who lands here stay ----------------------

mutation(
    "the homepage says Welcome and offers no next step",
    expect={"no-orientation"},
    apply=lambda site: edit(
        site, "index.html",
        lambda text: swap(r"<h1>.*?</h1>", "<h1>Welcome</h1>")(
            swap(r'<p><a href="\{\{BASE\}\}/pricing\.html" class="btn">.*?</p>', "")(text))),
)

mutation(
    "content pages have no onward link in the body",
    expect={"dead-end"},
    apply=lambda site: edit_all(
        site,
        lambda text: re.sub(
            r"<main>.*?</main>",
            lambda match: re.sub(r'<a href="[^"]*">(.*?)</a>', r"\1",
                                 match.group(0), flags=re.S),
            text, flags=re.S),
        skip=("index.html",)),
)

mutation(
    "in-body links point at pages that do not exist",
    expect={"broken-links"},
    apply=lambda site: edit_all(
        site, swap(r'href="\{\{BASE\}\}/pricing\.html"',
                   'href="{{BASE}}/plans-and-pricing.html"')),
)

mutation(
    "the visible breadcrumb trail is removed",
    expect={"no-breadcrumbs"},
    # The same links survive as an ordinary paragraph, so this removes the
    # trail and nothing else. Deleting the whole <nav> also removed two links
    # from the main content, which is a different defect.
    apply=lambda site: edit_all(
        site,
        swap(r'<nav aria-label="Breadcrumb"[^>]*>(.*?)</nav>', r'<p>\1</p>')),
)

mutation(
    "titles describe something other than the page",
    expect={"title-body-drift"},
    apply=lambda site: [
        edit(site, os.path.relpath(path, site).replace(os.sep, "/"),
             swap(r"<title>.*?</title>",
                  "<title>Depot rota, sheet {}</title>".format(index)))
        for index, path in enumerate(html_files(site))
    ],
)

mutation(
    "the enquiry form asks for fourteen things",
    expect={"form-friction"},
    apply=lambda site: edit(
        site, "contact.html",
        swap(r"</form>",
             "".join('<label for="x{n}">Field {n}</label>'
                     '<input id="x{n}" name="x{n}" required>'.format(n=n)
                     for n in range(1, 12)) + "</form>")),
)

mutation(
    "a modal covers the page on arrival",
    expect={"intrusive-interstitial"},
    apply=lambda site: edit_all(
        site,
        swap(r"<body>",
             '<body><div class="modal-overlay" role="dialog" aria-modal="true">'
             '<h2>Before you go</h2><p>Join 4,000 retail operators.</p>'
             '<button>Close</button></div>')),
)


# --- Cases added after a coverage pass over the root-cause vocabulary -------
#
# Each root cause the marketplace can report needs a test that makes it report
# it. Without that, a check can quietly stop working - or, as `thin-html` did,
# be declared and documented while never having been implemented at all.

mutation(
    "images carry no alt text",
    expect={"alt-missing"},
    apply=lambda site: edit_all(site, drop(r' alt="[^"]*"')),
)


mutation(
    "sentences run past thirty words",
    expect={"long-sentences"},
    # The fact still comes first, so this isolates sentence length from
    # answer-first structure.
    # Confined to pages that carry no price, date or identity sentence, so the
    # edit changes sentence length and nothing else about the site.
    apply=lambda site: edit_all(
        site,
        swap(r"<p>.*?</p>",
             "<p>At 06:00 each day Brightpath returns 1 replenishment quantity per SKU for every warehouse "
             "in the account each morning at six, drawing on at least twelve months of "
             "point-of-sale history together with inbound purchase orders, supplier "
             "lead-time variance and any promotional calendar the operations team has "
             "loaded, so that the number a planner sees already accounts for every "
             "input that would otherwise have to be reconciled by hand. The same nightly "
             "run also recalculates safety stock for every location using the trailing "
             "variance of demand over the last thirteen weeks, adjusted for the service "
             "level the account has configured and for any supplier whose lead time has "
             "drifted outside the band the planner set when the feed was first connected."
             "</p>"),
        skip=("index.html", "about.html", "contact.html", "pricing.html",
              "press.html", "privacy.html", "blog/index.html",
              "blog/safety-stock-2026.html", "blog/forecast-accuracy-metrics.html")),
)


mutation(
    "a page ships a megabyte of inline payload",
    expect={"page-weight"},
    apply=lambda site: edit_all(
        site,
        swap(r"</body>",
             '<script>var padding = "' + ("0123456789" * 200000) + '";</script></body>')),
)

mutation(
    "the JSON-LD name is not the name on the page",
    expect={"schema-text-mismatch"},
    # Both are correct downstream readings. The declared identity is now a name
    # no page uses, so the site is inconsistent about who it is, and there is no
    # sentence anywhere defining the entity it claims to be.
    also={"name-inconsistency", "no-entity-definition"},
    apply=lambda site: edit_all(
        site, swap(r'"name": "Brightpath Analytics"', '"name": "Northwind Freight Systems"')),
)

mutation(
    "content pages keep their shape but lose their words",
    expect={"thin-html"},
    exclusive=False,
    apply=lambda site: edit_all(
        site,
        lambda text: re.sub(r"<p>.*?</p>", "<p>Details.</p>", text, flags=re.S),
        skip=("index.html",)),
)


mutation(
    "a page is reached through three redirects",
    expect={"redirect-chain"},
    apply=lambda site: (
        rules(site, {"redirects": {
            "/services.html": "/hop-one.html",
            "/hop-one.html": "/hop-two.html",
            "/hop-two.html": "/platform.html",
        }}),
        # Links and sitemap keep pointing at the old URL, which is the whole
        # point: the crawler follows the chain the way a real consumer would.
        os.rename(os.path.join(site, "services.html"),
                  os.path.join(site, "platform.html")),
    ),
)


mutation(
    "some templates ship without the site header and footer",
    expect={"inconsistent-chrome"},
    # Missing chrome is not a dead end: the page has links, they are the wrong
    # ones. Separate finding, separate template to fix.
    also={"no-breadcrumbs"},
    apply=lambda site: edit_all(
        site,
        lambda text: re.sub(r"<header>.*?</header>|<footer>.*?</footer>", "", text, flags=re.S),
        skip=("index.html", "about.html", "contact.html")),
)


mutation(
    "the site declares two different telephone numbers for itself",
    expect={"nap-inconsistency"},
    # Organization markup, not a tel: link. A site may publish a sales line and
    # a support line; it may not declare two primary numbers for itself.
    apply=lambda site: (
        edit(site, "contact.html", swap(r'"telephone": "\+1 503 555 0142"',
                                        '"telephone": "+1 503 555 0199"')),
        edit(site, "about.html", swap(r'"telephone": "\+1 503 555 0142"',
                                      '"telephone": "+1 503 555 0177"')),
    ),
)


mutation(
    "the brand declares a long formal name and defines itself by the short one",
    # Nothing is broken here. The site states exactly the identity sentence this
    # marketplace asks for; it simply writes "Brightpath is a ..." while its
    # Organization markup carries the full formal name. Requiring the whole
    # declared string made the check unsatisfiable for any organisation with a
    # long formal name, which is most institutions.
    expect=set(),
    apply=lambda site: (
        edit_all(site, swap(r'"name": "Brightpath Analytics"',
                            '"name": "Brightpath Analytics Holdings, Inc."')),
        edit_all(site, swap(r'"og:site_name" content="Brightpath Analytics"',
                            '"og:site_name" content="Brightpath Analytics Holdings, Inc."')),
        edit(site, "index.html",
             swap(r"<p>Brightpath Analytics is a demand-forecasting",
                  "<p>Brightpath is a demand-forecasting")),
    ),
)


# --- The `meta-hygiene` bucket, split into what it was actually reporting ----

mutation(
    "Open Graph tags are missing",
    expect={"open-graph-incomplete"},
    apply=lambda site: edit_all(site, drop(r'<meta property="og:[^>]*>')),
)

mutation(
    "the site has a search box but no WebSite markup describing it",
    expect={"no-website-schema"},
    apply=lambda site: edit_all(
        site, swap(r'\{\s*"@type": "WebSite".*?"query-input"[^}]*\}\s*\}', '{"@type": "Thing"}')),
)

mutation(
    "pages do not declare a language",
    expect={"missing-lang"},
    apply=lambda site: edit_all(site, swap(r'<html lang="en">', "<html>")),
)

mutation(
    "the facts are marked up as microdata rather than JSON-LD",
    expect={"microdata-only"},
    also={"no-org-schema", "no-article-schema", "no-breadcrumb-markup", "no-faq-schema",
          "no-website-schema", "no-product-schema"},
    apply=lambda site: (
        edit_all(site, drop(r'<script type="application/ld\+json">.*?</script>')),
        edit_all(site, swap(
            r"<main>",
            '<main itemscope itemtype="https://schema.org/Organization">'
            '<meta itemprop="name" content="Brightpath Analytics">'
            '<div itemscope itemtype="https://schema.org/PostalAddress">'
            '<meta itemprop="postalCode" content="97204"></div>'
            '<div itemscope itemtype="https://schema.org/ContactPoint">'
            '<meta itemprop="telephone" content="+1 503 555 0142"></div>')),
    ),
)


mutation(
    "an episode page carries a player and two lines of summary",
    expect={"no-transcript"},
    # The canonical case for this check, and one it could not see: the extractor
    # counted <video> and video embeds and never looked at audio, so every
    # podcast episode page on the web was invisible to it.
    exclusive=False,
    apply=lambda site: (
        add_file(site, "episodes/forecasting-after-a-shock.html",
                 '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
                 '<title>Forecasting after a shock | Brightpath Analytics</title>'
                 '<meta name="description" content="Episode 12 of the Brightpath podcast, '
                 'on recalculating safety stock after a demand shock.">'
                 '<link rel="canonical" href="{{BASE}}/episodes/forecasting-after-a-shock.html">'
                 '</head><body><main><h1>Forecasting after a shock</h1>'
                 '<audio controls src="{{BASE}}/assets/episode-12.mp3"></audio>'
                 '<p>Episode 12. Listen above.</p>'
                 '<p><a href="{{BASE}}/index.html">Back to the homepage</a></p>'
                 '</main></body></html>'),
        edit(site, "index.html",
             swap(r"</main>",
                  '<p><a href="{{BASE}}/episodes/forecasting-after-a-shock.html">'
                  'Podcast episode 12</a></p></main>')),
    ),
)
