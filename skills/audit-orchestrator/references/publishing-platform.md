# What the site is built with, and where its template is

Every markup fix this marketplace emits used to end in the same sentence: *"add the snippet
to the site-wide template."* It appeared about forty times across four real reports, and it
is a template instruction nobody can follow, because it names no file, no screen and no
person.

Three of those four sites were one hosted storefront platform. The crawl had fetched,
parsed and quoted that platform's CDN hostname on every page of three of them, and the
platform's name appears in the four reports exactly once — inside a URL the tool quoted back
in its own snippet.

The evidence was in `snapshot.json` the whole time. Nothing had ever been asked to read it.

---

## The two halves of the failure

**Where.** "The site-wide template" is a description of a thing, not a location. On a hosted
store it is `layout/theme.liquid` behind four menu clicks; on a hosted site builder it is a
box in a settings screen and there is no file at all; on a static-site generator it is a file
in a git repository. One sentence cannot be right about all three, and the generic one is
right about none.

**Who.** Everything typed `low` effort / `developer` owner — Organization properties, Article
and FAQPage blocks, BreadcrumbList, `og:image`, JSON-LD escaping — is seven to nine of every
twelve to fourteen findings in a report. Across 2,795 lines of real report output the phrases
"your theme", "your developer" and "whoever built the site" appear **zero** times. For a shop
owner with no developer, "low effort, developer" is not an hour of work. It is a person they
do not have.

---

## The rule that outranks coverage

**A wrong platform name is far worse than none.** Telling somebody on a hosted site builder
to open `theme.liquid` costs the reader's trust in every other finding in the report, and
buys less than the generic sentence it replaced.

So certainty is the design and the platform list is the detail:

| Confidence | What a finding may print |
|---|---|
| `high` | the platform's own menu wording and its own file names, flat |
| `medium` | the name, hedged out loud, followed by what to do if the guess is wrong |
| `low` | nothing about the platform at all — the fallback sentence, which is not the leftover one |

---

## Signal families, and what each one proves

Nothing reads prose and nothing reads `<a href>`. A page that says a platform's name, links
to it, or shows its badge is a page mentioning a company. A page that fetches its stylesheet
from that company's CDN, or whose origin sets that company's cookie, is a page that company
rendered. This repository has been bitten repeatedly by a word on a page being read as a
fact about the site — most recently a "Powered by" footer line counted as the brand's own
off-site profile.

| Family | What it is | What it proves |
|---|---|---|
| `generator` | `<meta name="generator">`, plus the `server`, `x-powered-by` and `x-generator` header values | the software naming itself |
| `asset-host` | a hostname the page fetches a script, stylesheet, image or frame from | a brand cannot get its assets served from a platform's CDN without being on the platform |
| `asset-path` | the leading path segments of those subresources — `/cdn/shop`, `/wp-content`, `/_next/static` | a shape the platform's own build produces |
| `markup-attribute` | a `data-` attribute *name* on `<html>` or `<body>` | the renderer stamped the root element. Names only; the values are per-site ids |
| `framework-marker` | the mount point and state blob `_spa_shell` already found | the framework naming its own container |
| `response-header` | a header *name* only that platform sends | the origin, not the document |
| `cookie-name` | the name of a cookie the origin set. Names only, never values — a value is a session | the origin, not the document |

The last three are **self-declaring**. An embedded third-party widget can add a script host,
a path shape, a root attribute and a framework marker to somebody else's page. It cannot make
that page's own origin send a header or set a cookie. That is what separates a shop from a
blog with a buy button glued into its footer, and it is why the confidence rule below asks
for one of those three before it will name anything.

---

## The confidence rule

Signals are tallied per platform across every crawled page that carries a `platform_signals`
record. `families` is how many of the seven fired anywhere; `reach` is the share of read
pages carrying at least one.

```
ambiguity  a runner-up with as many families as the winner -> name nothing,
           and put both names in the evidence. A headless storefront and a
           blog bolted onto a shop both land here, and both consoles are
           the wrong console to send that reader to.

high       families >= 2  AND  one of them self-declaring  AND  reach >= 0.5
medium     families >= 2  OR   (self-declaring AND reach >= 0.5)
low        anything else, and `.name` is cleared

a runner-up holding two families of its own caps the winner at `medium`
```

`reach >= 0.5` is the site-wide test: a platform's own template is on every page it renders,
an embedded widget is usually on one section.

Worked outcomes:

| Site | Families | Answer |
|---|---|---|
| hosted store | asset-host, asset-path, response-header, cookie-name | `high` — names the theme file |
| hosted site builder | generator, asset-host, markup-attribute | `high` — names the settings screen |
| blogging platform, generator tag only | generator, on every page | `medium` — hedged, with the fallback attached |
| blog with one storefront buy button | asset-host only | `low` — names nothing |
| plain HTML | none | `low` — names nothing |

---

## The API

In `skills/audit-orchestrator/scripts/audit_common.py`, vendored into all seven skills.

```python
from audit_common import (
    publishing_platform, PublishingPlatform, PLATFORM_SIGNATURES,
    where_the_template_is, who_edits_the_template, template_change_steps,
    ADMIN_SCREEN, SOURCE_FILE, DEFAULT_TEMPLATE_SLOT,
)

platform = publishing_platform(snapshot)   # always an object, never None
```

`PublishingPlatform` fields and gates:

| Member | Type | Meaning |
|---|---|---|
| `.name` | `str` | `"Shopify"`, or `""` when nothing may be named |
| `.confidence` | `str` | `"high"` / `"medium"` / `"low"` |
| `.reach` | `str` | `ADMIN_SCREEN` (a screen you log into) or `SOURCE_FILE` (a file that ships with a release), `""` when unnamed |
| `.signals` | `tuple[str]` | the families that fired, in table order |
| `.evidence` | `tuple[str]` | one printable phrase per family — `'the pages load assets from "cdn.…"'` |
| `.pages` | `int` | pages carrying at least one signal |
| `.named` | `bool` | `high` only. **May a finding print this platform's own vocabulary?** |
| `.determined` | `bool` | `high` or `medium`. Enough to say the name out loud, hedged |
| `bool(platform)` | `bool` | the same as `.named`, so `if platform:` means "may I name it" |
| `.is_certainly(*names)` | `bool` | asserts. `platform.is_certainly("Shopify")` |
| `.might_be(*names)` | `bool` | gates. **True for an undetermined site**, so gating on it removes nothing |
| `.why()` | `str` | the evidence joined, for a finding's prose |

The three sentence writers each accept a `PublishingPlatform` **or** a snapshot **or**
`None`, so a check that resolved the platform once does not pay for it again and a check
holding only the snapshot does not have to know that.

```python
where_the_template_is(platform) -> str
    # "On Shopify that template is `layout/theme.liquid`, reached through
    #  Online Store -> Themes -> the ... button on your live theme -> Edit code."
    # Says where the template IS, never what to put in it: the slot inside the
    # template belongs to the caller, because og:image and BreadcrumbList do
    # not go in the same place.

who_edits_the_template(platform) -> str
    # "This is a settings screen in the Shopify admin rather than a code
    #  release, so whoever holds the Shopify login can make it - often the
    #  person who runs the site day to day. If that is not you, it is the
    #  agency or freelancer who set the site up."

template_change_steps(platform, what="the block below",
                      where=DEFAULT_TEMPLATE_SLOT) -> tuple[str, str]
    # The two how_to_fix steps every site-wide markup fix ends with.
```

Example call, spliced onto a check's own steps:

```python
platform = publishing_platform(snapshot)
result.add(make_finding(
    id_hint="no-organization-schema",
    ...,
    how_to_fix=[
        "Fill the Organization block below in with your own details.",
        *template_change_steps(platform, "that block"),
    ],
))
```

`template_change_steps` drops the `</head>` half of its wording on a platform whose admin
has a dedicated head box, because that screen never shows the reader a `</head>` and sending
them to look for one is the same class of unfollowable instruction this replaces.

---

## What each kind of reader is now told

**Recognised, `high`, admin screen.** The click path in that product's own menu wording, the
file when there is one, and: *this is a settings screen rather than a code release, so
whoever holds the login can make it — often the person who runs the site day to day. If that
is not you, it is the agency or freelancer who set the site up.*

**Recognised, `high`, source file.** The file that wraps every page, and: *this ships with a
release and needs whoever deploys the site. If nobody in-house does that, it is the agency,
freelancer or contractor who built it — the name on your web-development or hosting invoices
is usually the right person to ask.*

**Recognised, `medium`.** The same sentence, prefixed with *"looks like … though not
certainly"*, the evidence that said so, and the fallback attached to the end: *"if that
screen or file is not there, it is not X: …"*

**Unrecognised — the common case on the open web, and it gets the better sentence:**

> This audit could not tell what this site is built with, so it cannot name the file.
> Whatever produces the pages, one template produces the top of every one of them: the part
> that holds the `<title>` tag and ends with `</head>`. Search the site's theme or source
> files for the text `</head>` — the file that contains it and is used by every page is the
> one to change, and changing it once covers the whole site.
>
> If you do not know who edits your pages, ask whoever built or maintains the site: an
> agency, a freelancer, or failing both your hosting provider's support, who can at least
> tell you which system the site runs on. That is the first thing any developer you hire
> will ask you.

That is actionable knowing only that somebody, somewhere, edits your pages, which is the
one thing every reader of this report knows.

---

## Adding a row

`PLATFORM_SIGNATURES` is a tuple of `_Signature`. Naming a platform whose signature the code
must recognise is the same thing this file already does for bot managers, CAPTCHA vendors,
media hosts and consent tools: a product we detect, never an example site, and never keyed
to any brand being audited.

Hosts are held as **labels** — the word somebody chose — and never as addresses, exactly as
`_PUBLIC_LABELS` and `_REGISTRY_HOST_LABELS` are held. That is what lets one row match a
platform's marketing address, its CDN and its per-shop subdomains at once, and it is also
what keeps `tests/test_marketplace.py::test_no_real_domain_names_anywhere` satisfied.

A row whose click path might be wrong is worse than no row. If the menu wording is not
certain, leave `admin_path` empty and give the row a `file` instead; if neither is certain,
do not add the row — the fallback above is already better than a wrong instruction.

---

## Where the evidence is recorded

`skills/audit-orchestrator/scripts/page_extract.py::_platform_signals`, written to
`page["platform_signals"]` during the one shared crawl. One capped `select` over the
document's subresource elements, plus values the caller has already parsed — the meta tags,
the response headers, and the `spa_shell` reading. Nothing is re-parsed and no element is
walked twice, because this runs on every page of every crawl inside a five-minute budget.

Cookie **names** are recorded and cookie values never are. A name is a fact about the
platform; a value is a session belonging to whoever this audit fetched as.

One known gap: `crawl.py` re-extracts a page after rendering it and passes the already-
filtered header dictionary, so the two header-derived families are absent on those pages.
Detection is a tally across the whole crawl, so a handful of re-rendered pages cannot change
the answer — but a site where every page is re-rendered will lose its `response-header` and
`cookie-name` evidence and can fall from `high` to `medium`, which is a hedge rather than a
falsehood.
