# The access checks, one section each

`SKILL.md` names the questions in reading order. This file holds the measurement: what each
check reads, where it declines, and the real report behind every exclusion.

Three companion files carry the vocabularies these checks read:
`references/ai-crawler-user-agents.md` (which agent does what, and who operates it),
`references/robots-path-vocabulary.md` (which paths are content and which are plumbing), and
`references/second-client-comparison.md` (what a two-client refusal can and cannot claim).

---

## The register: twenty-seven check ids, and what each one settles

These are the exact strings that appear in `checks_run[]` and `not_applicable[]` in every
report, so a reader can tick this list off against a real run. `SKILL.md` groups them into
fourteen questions; this is the ungrouped set. Every finding carries `mechanism="A"`.

**The order below is the order they appear in the report. It is not the order they run in,
and that difference is deliberate.** `bot-manager-user-agent-comparison` executes **first**,
before anything else in this skill, because three later checks may not call a refusal a
defect until it has answered. On one hardware manufacturer the comparison established that
two named search crawlers are answered 200 while this audit is answered 403, and said so in
the appendix — while the same report led with a critical finding saying a bot manager serves
crawlers a verification page instead of the site. Reading the appendix and the headline
together, the report contradicted itself. The comparison now runs first and the challenge
check reads its answer.

The whole skill spends at most `MAX_EXTRA_REQUESTS = 30` network requests.

| Check id | The question | What it reads | Threshold, and where it came from | Ceiling |
|---|---|---|---|---|
| `bot-manager-user-agent-comparison` | Does the edge answer an AI crawler's name differently from this audit's own? | one comparison URL fetched under up to two answer-crawler names from **different operators**, a re-baseline, a confirmation, then the same names again through a **second HTTP client** | `BOT_PROBE_AGENTS = 2` — bot rules are written per vendor, and probing whichever name sat first in a list turned a real block into a clean pass the moment the list was reordered. `SECOND_TRANSPORT_AGENTS = 4` — covers every operator whose search agent a refusal costs a citation with. `CHALLENGE_HTML_CEILING = 250_000` — a vendor interstitial is about 60 KB and the largest measured is under 120 KB | **high**, at **medium** confidence, and only where the confirming response is itself blocking — a challenge body, or 401/403/405/406/429/5xx. A difference that is not blocking-shaped is **medium**. The second-client refusal path is fixed at **high**, also at medium confidence. Never critical: every probe leaves from this audit's address, not the crawler's published ranges, and a CDN verifying the address behind a crawler's name refuses exactly this request while serving the real crawler — see `second-client-comparison.md` |
| `bot-manager-challenge-page` | Was a verification page served instead of the content? | the challenge marker on each page, the vendor its forms post to, whether the record is wired into the site by its links, the comparison's verdict above, and, where nothing names itself, the shape of the body | `SCRIPT_WALL_MIN_BYTES = 5000`, `SCRIPT_WALL_SCRIPT_SHARE = 0.9` and `SCRIPT_WALL_TEXT_MAX = 20`, in `audit_common.py` — **measured**: a museum answered every request with 101,079 bytes, 101,000 of them inline script, with no title, no text and no link; an application shell carries a title and a mount point and is not this, and a redirect stub falls under the byte floor. `CHALLENGE_INTERNAL_LINK_FLOOR = 3` — three rather than one, because a challenge screen may still link the vendor serving it, while a page carrying so much as a navigation bar clears three several times over. `SITE_WIDE_CHALLENGE_SHARE = 50` — above this the wall stands across the site rather than intercepting some requests to it. `UNCONFIRMED_CHALLENGE_SHARE = 10` — **measured**: a municipal site's snapshot recorded one path of sixty-one challenged, and re-fetching that URL returned 200 with 51,856 bytes of content and no vendor marker | **critical** at half the fetched URLs; **high** below; **medium** where it is unconfirmed and under a tenth of the crawl. A shape seen at one URL whose byte count no other address repeats is at **medium** confidence |
| `robots-txt-reachable` | Did the rules file answer, and was what answered a rules file? | the status, the fetch error, and whether an HTML page came back at that address | none numeric | **high** — and only for a **5xx**, which Google and others read as "disallow everything" for up to thirty days. A file that simply never answered is **medium**; lines crawlers will ignore are **low** |
| `robots-txt-rules` | Could the rules be read at all? | the same record, before any rule is evaluated | none | declines only. It exists so that "no rules to evaluate, which means nothing is disallowed" is never printed about a file that answered 403 |
| `robots-blocks-all-crawlers` | `Disallow: /` under `*` with no carve-out? | the `*` group | none — binary | **critical** |
| `robots-blocks-ai-answer-crawlers` | Is a search-index or live-fetch agent shut out, wholly or from a section? | two questions of every agent: is it disallowed from `/`, and does a group **naming** it close a section no `*` rule closes | two or more agents blocked is high, one is medium — reasoned, not measured. `SECTION_BLOCK_HIGH_SHARE = 0.10` **and** `SECTION_BLOCK_HIGH_PAGES = 2` together: a rule closing a prefix holding a tenth of the crawled pages has been shown by those pages to close a section, and two pages as well as a tenth because on a nine-page crawl one page clears a tenth on its own and one page is not a section | **high** |
| `robots-blocks-ai-training-crawlers` | Is a training crawler or opt-out token blocked? | the same two shapes, run against the training roster | none affects severity | **info**, and nothing higher, ever. Blocking a training crawler is a rights choice, not a discoverability defect, and this is the only `info` ceiling in the skill |
| `robots-blocks-content-paths` | Are whole top-level sections closed to everybody? | `Disallow` rules in the `*` group only, filtered to single segments with no wildcard | **two** closed sections, because a single closed section is usually deliberate and firing on one put this check on most of the web | **medium**, at **medium** confidence — this audit does not fetch what robots.txt closes, so what is behind those paths is unverified and the finding says so |
| `robots-agent-groups-unambiguous` | Are one agent's rules stated in two places? | groups sharing a token | none | **low** |
| `robots-crawl-delay` | Does the delay starve a compliant crawler? | `Crawl-delay` in the `*` group, against the pages this run actually read | `CRAWL_DELAY_STARVES_S = 10` — **measured**: a sixty-page site costs ten minutes at 10s, half an hour at 30s, three quarters of an hour at 45s; one brand's `Crawl-delay: 45` meant this audit read 0 pages in 186 seconds. `CRAWL_DELAY_SEVERE_S = 30` — half a minute per page is under 200 pages a day for a crawler that never stops | **high** |
| `sitemap-present` | Is an XML sitemap published? | the sitemap records, both conventional locations, a post-crawl probe of `/sitemap_index.xml`, and the raw `Sitemap:` line in robots.txt | `SITEMAP_ABSENT_STATUS` is `{404, 410}` and nothing else. `/sitemap_index.xml` is probed because it is what one popular plugin writes and a manufacturer whose index sat there was reported as having no sitemap at all | **medium**, carrying `checked[]` — every independent source consulted |
| `sitemap-parses` | Is what answered actually a sitemap? | the parse error, and the recorded content type before any request is spent | none numeric | **medium**, **low** where only an orphan sitemap is involved. Where nothing settled HTML from XML, it reports **unchecked**: an XML parser gives the same error for a sitemap missing a tag and for 1.8 MB of HTML |
| `sitemap-urls-resolve` | Do the advertised addresses exist? | statuses the crawl already collected, then up to eight HEAD probes with each dead verdict re-asked | `SITEMAP_PROBE_LIMIT = 8`; 25% dead raises it to high — that 25 is chosen, not measured | **high** |
| `sitemap-excludes-private-paths` | Does it spend a crawler's budget on one visitor's session? | each `loc`, classified on whole path segments | `PRIVATE_PATH_HIGH_SHARE = 10` percent — above it the sitemap is mostly pointing crawlers at addresses no crawler can use, which is a different size of problem from four stray entries in a catalogue of hundreds | **medium** |
| `unknown-paths-return-200` | What does a 200 prove on this origin? | one GET of `/brand-ai-readiness-audit-no-such-page` — **fixed rather than random, so two runs ask the same question and can be compared** | none | **medium**. Its answer settles `sitemap-present`, `sitemap-parses`, `sitemap-urls-resolve` and `canonical-targets`, which is why it runs before all four |
| `homepage-reachable` | Did the front door answer 200? | the homepage record, found by requested address, then answering address, then page type | none | **critical** — dropping to **info** where the comparison established the refusal is of this audit only |
| `non-200-rate` | How much did the origin refuse? | fetched URLs, minus those the edge refused with one identical short body | 10% to fire, 25% for high — both chosen, not measured. **Under three fetched URLs it declines whatever the percentage**, because a rate computed from one blocked request restates the homepage finding rather than adding to it | **high** |
| `origin-response-time` | Slow is not down | the median server response across pages that answered 200 | `SLOW_ORIGIN_MEDIAN_MS` and `VERY_SLOW_ORIGIN_MEDIAN_MS`, in `audit_common.py` | **high**, under its own `slow-origin` rather than a non-200 |
| `redirect-chain-length` | How far is the published address from the page? | the recorded redirect chain | more than two hops. Chosen, not measured | **low** — the only fixed-low ceiling here |
| `meta-refresh-redirects` | A redirect only a renderer can follow | `meta_refresh_from` on pages that answered | none | **high** where the homepage is among them, **medium** otherwise |
| `noindex-on-content-pages` | Is a page the site publishes to be read fetched and then thrown away? | **every** robots meta tag, per-crawler meta tags, and the `X-Robots-Tag` header — not the first one found, because a consumer applies the most restrictive directive it sees — over **every page the crawl read, with no filter on page type**, plus the pages set aside for carrying the directive | no numeric threshold. The population is narrowed by what each page carries, not by what it was typed: a soft 404, a search-result address, a page whose canonical names another, a cart/checkout/account/wishlist address, a tag, category, author, dated or paginated listing, an address carrying a paging, sorting or filtering parameter, `noindex, follow` on a listing, and an address the crawl set aside as something other than a page are each **named and left alone**. Those are the arrangements a site makes on purpose; an earlier version told a site to strip `noindex` off its tag archives, which is advice that makes the site worse | **high**. It refuses to claim a `noindex` is a mistake anywhere except on a page the site plainly publishes to be read |
| `canonical-targets` | Does the canonical point somewhere real and on-domain? | the declared canonical, probed where the crawl did not already know the answer | `CANONICAL_PROBE_LIMIT = 3` — **measured across 13 real sites**: the median site declares zero canonical targets the crawl did not already fetch, and the worst declared one, so three has never been the binding constraint | **high** off-domain, **medium** at a non-200 |
| `canonical-host-consistency` | Is one site served under two names? | the canonical hosts with the default port stripped; failing that, paths that answered under two hostnames with the same title and the same body length; failing that, where no path was read under both names, pages the crawl read on both the bare domain and its `www.` form, each answering 200 where it was asked, with no canonical naming the other; and when the crawl only ever reached one name, one GET of the homepage under the other (robots.txt respected, this skill's own budget, skipped wherever a canonical already names the real address) | none | **medium** |
| `https-transport` | Plain HTTP? | the origin scheme and where every page landed; on an HTTPS site, internal links written `http://` that the crawl followed and saw redirect to `https://`, with the hop's status where the crawl recorded it (`internal-links-use-http`) | none | **medium** for a site served over HTTP; **low** for `http://` internal links |
| `robots-rules-are-path-shaped` | Is every `Allow`/`Disallow` value a URL path at all? | each rule value in every group of the parsed file | `_NOT_IN_A_PATH_RE` — the characters `(){};<>"'` and a backtick, plus a value that begins with neither `/` nor `*`. Deliberately **not** `=`, `?`, `&`, `%`, `*` or `$`: `Disallow: /*?sort=` and `Disallow: /*.php$` are correct rules a large share of real sites publish. `ROBOTS_MOSTLY_BROKEN_SHARE = 50` percent — chosen, not measured: one broken rule in forty is a typo, half of them is a build step writing over the file | **medium**, **high** where most of the file's rules are unreadable. Zero requests — the file is already in the snapshot. A live file carried `Disallow: /g,mt=RegExp(`, visible to anyone reading it by hand, and nothing here said a word about it |
| `hreflang-between-language-editions` | Does a site serving several language editions link them to each other? | `audit_common.locale_editions` over the crawled pages, then one re-read of the shallowest page of each of the first two editions, searched for the attribute name `hreflang` in the markup and in the `Link:` header. Where fewer than two path editions exist, the links to other hostnames on the same registered domain whose first label is a language code; then the crawled pages' own `hreflang`, the sitemap's, and one re-read of the front page. Pages the crawl stopped reading at its size cap are left out | `HREFLANG_PROBE_LIMIT = 2` — two editions with no link between them is the whole finding, and a third address adds a name to a sentence rather than changing it. A leading path segment is an edition only where the page's own `lang` agrees with it or where the crawl met more than one such prefix, which is `locale_editions`' own rule and is not loosened here. `HOST_EDITION_MIN_HOSTS = 2` and `HOST_EDITION_PAGE_SHARE = 0.5` — a hostname edition counts only where at least two such hostnames are each linked from at least half the crawled pages that carry links, the way a language picker is; one `fr.` subdomain is as likely a regional office as an edition, and two-letter labels that name a service (`my.`, `go.`, `qa.`, `ai.`) are not read as languages. Reported where a documentation site linked `ja.`, `fr.` and other language hostnames from every page with no `hreflang` anywhere | **medium**, under `canonical-broken`: several addresses hold one thing and nothing declares how they relate. It refuses to claim absence beyond the pages it read, and says so — an `hreflang` declared only in a sitemap's `xhtml:link` would not have been seen. Costs **nothing at all** on a site with one edition |
| `llms-txt-presence` | What answered at the agent-file addresses? | `/llms.txt`, then any other agent file robots.txt names itself | `AGENT_FILE_PROBE_LIMIT = 2` | declines only. **Absence is never a finding here** — it feeds a recommendation, and a file this audit never requested is reported as unknown rather than absent |

---

## 1. Was a verification page served instead of the content?

A bot manager that answers 403 announces itself in the status line. The one that answers 2xx
does not: measured on real commercial homepages, one returns HTTP 202 with zero characters of
readable text and another returns 200 with thirty-two, both carrying a vendor's challenge
script.

Detect the vendor by its own scaffolding — AWS WAF, Akamai Bot Manager, Cloudflare, DataDome,
PerimeterX, Imperva or Distil — or by the mitigation header it sets; failing both, by a CAPTCHA
widget in the body, which names the widget and never the bot manager. Gate phrase-matching on
under 800 characters of text, so an article *about* bot management is never mistaken for one.

Where nothing names itself, read the shape. A 2xx body of at least 5,000 bytes that is 90% or
more inline script, with no `<title>` text, no `<a href>`, no application mount point
(`id="app"`, `root`, `__next`) and at most 20 characters of visible text is a check the browser
must run before it is let through. A museum answered every request that way — 101,079 bytes,
one inline script, under a `Server` header naming a product no table here knew — and the report
called its homepage an empty 200. The finding states what was measured: the byte counts, any
other address answering with the same number of bytes, and the `Server` header as sent. One URL
whose byte count nothing else repeats is **medium** confidence; the same bytes at a second
address is the wall.

**Two fixes.** A product named has a console: name it, and say what to do if that account is not
the reader's. Nothing named means no console to send anyone to — a shop on a hosted platform has
no login for one — so print the curl loop as the evidence and a support ticket to whoever serves
the site. Never assert the reader can change a rule not shown to be theirs.

Challenged pages are excluded from every content check: a challenge is the crawler's reception,
not the site, and without that the audit files four confident findings about a homepage that is
fine.

---

## 2. Is robots.txt resolvable?

Fetch it. A 404 means no restrictions and is not a defect. A 5xx *is* a defect and a non-obvious
one: major crawlers treat a persistent 5xx on robots.txt as "disallow everything" for weeks. An
unreachable file is a **medium** finding.

Malformed lines are **low**: rules above the first `User-agent` line apply to nobody, so the
policy the site believes it has is not the one crawlers see.

---

## 3. Does robots.txt shut everyone out?

`Disallow: /` under `User-agent: *` with no `Allow` carve-out is **critical**, full stop.

---

## 4. Which AI crawlers are named and blocked?

Split them into two groups, because they are not the same decision. Four roles, from
`references/ai-crawler-user-agents.md`:

- **Search index agents** index pages so the brand can be linked in an answer (`OAI-SearchBot`,
  `Claude-SearchBot`, `PerplexityBot`, `Applebot`).
- **Live-fetch agents** fetch one page because a person just asked about it (`ChatGPT-User`,
  `Claude-User`, `Perplexity-User`, `Meta-ExternalFetcher`). Blocking either of these two groups
  removes the brand from answers. Two or more blocked is **high**; one is **medium**.
- **Training crawlers** collect corpora (`GPTBot`, `ClaudeBot`, `Amazonbot`, `CCBot`,
  `Meta-ExternalAgent`). Blocking them is a legitimate rights choice, not a discoverability
  defect. Report as `info` so it never inflates the counts.
- **Opt-out tokens** are not crawlers at all (`Google-Extended`, `Applebot-Extended`). They fetch
  nothing, so blocking one cannot cost a citation. `Google-Extended` is not `Googlebot` and does
  not affect Google Search.

**Never tell an owner to allow a training crawler in order to be cited.** The first version of
this skill had `GPTBot`, `ClaudeBot`, `Amazonbot`, `Bytespider` and `Meta-ExternalAgent` in the
answer group and told owners, at high severity and rank two of "Start here", to allow-list them so
their pages could be quoted. All five are training crawlers by their operators' own published
descriptions; following that advice would have reopened a training opt-out for no citation gain.
When reporting a training block, name the same operators' search and live-fetch agents instead,
and confirm those are open.

**Ask two questions of every agent, not one.** Is it disallowed from `/`? And does a group
*naming* it close a real section that no `User-agent: *` rule closes? A robots.txt whose single
group named eleven of these crawlers and carried `Disallow: /store/` was called clean by the first
question and excused by check 5 — "no `User-agent: *` group" — on a shop, where that path is every
product and price it sells.

Three things are not that second finding, and each boundary is an existing check rather than a
threshold: a section inside a whole-site block (reported once, up there); a path that is only
admin, cart, search, account, assets, staging or a query filter (check 5's vocabulary tells a
section from plumbing); and a path `*` closes too, which is policy for Googlebot as well and
belongs to check 5.

**Severity follows what is behind the path, not how many paths there are.** Count how many pages
the crawl already fetched sit behind the rule and print the number: two or more of them **and** a
tenth of the crawl is **high**, fewer is **medium**, and a path the crawl never reached is
**medium** at medium confidence because the size of the loss was not measured. The training half
of a mixed group stays `info` in a finding of its own, and the remedy splits the one group in two
so the opt-out survives the fix.

**The fix opens the paths named and nothing else** — one `Allow:` line per reported path, spelt as
the `Disallow:` line spells it and added to the existing group rather than replacing it, never a
bare `Allow: /`, which reopens every other path the group closes. See
`references/robots-path-vocabulary.md`.

**When nothing is found, say what was tested.** A quiet check is listed by bare name under "checks
that ran and found nothing wrong", and a bare name carries no qualification — which is how a test
for one shape of block certified the other shape as clean. Decline instead, naming both questions
and how many agents each was asked of.

Every agent in the report is named with its operator and that operator's own statement of what it
does. `references/ai-crawler-user-agents.md` carries the URL of the page each statement came from
and the date it was read; eleven of the twenty-nine agents have no published statement at all, and
both the table and the report say their role is inferred from observed behaviour. Do not assert a
role you cannot source.

---

## 5. Which paths are disallowed?

Ignore admin, cart, checkout, account, login and search paths — blocking those is correct. Ignore
files that exist for machines rather than readers (`/apple-app-site-association`, `/humans.txt`,
`/ads.txt`, `/.well-known/`), framework and build directories (`/App_Themes`, `/_next/`, `/bin`),
and error and maintenance pages. Two shop reports opened with "robots.txt disallows N paths that
look like real content" and named an 89-byte Apple deep-linking manifest as the first thing to fix.

Ignore wildcard rules too: they usually filter query strings. Ignore the signed-in account screens
(`/dashboard`, `/settings`), any rule naming a file by its extension rather than a page
(`/me.json`, `/strings.xml`) and a version-control metadata directory (`/RCS/`, `/.git`);
`references/robots-path-vocabulary.md` gives the boundary each is matched on and why a lower-case
`/cvs` is not one. Ignore, too, the same routes written in the site's own language
(`/tai-khoan`, `/warenkorb`, `/carrito`), a second address for the front page (`/home`,
`/homepage`), and the code directories a publishing platform's own robots.txt closes where the
file is that stock file; the vocabulary file lists the languages and the platforms.

Report only disallow rules covering what looks like real content — and say in the finding that you
did **not** fetch those paths, because robots.txt disallows them and this audit respects that, so
what is behind them is unverified. Name all three exclusions you applied, not one of them; a reader
comparing the report against their own robots.txt will otherwise find rules missing for a reason
you never gave.

**This check reads the `User-agent: *` group and nothing else**, so its decline must point at check
4: "robots.txt names specific crawlers and has no `User-agent: *` group" is true and reads to a
non-expert as "no path is closed".

---

## 5b. Are one agent's rules stated in two places?

A shop's own `User-agent: *` block, then a plugin's with a bare `Disallow:`. RFC 9309 combines
records with the same token and the major crawlers do; others take the first matching block, or the
last. Nothing in the file decides, so the owner cannot know which rules are in force. **Low**, one
edit to fix. Identical repeated blocks change no behaviour and are not reported.

---

## 6. Sitemaps

Referenced from robots, or present at `/sitemap.xml` or `/sitemap_index.xml`? Both conventional
locations are tried: the second is what Yoast writes, so it is the default on a large share of
WordPress sites, and a manufacturer whose index sits there was reported as having no sitemap at all.

Distinguish the three answers rather than merging them — 404 means the file is absent, 403 means the
request was refused and nothing was learned, and no request at all means the same. A path that
answers 200 with an HTML page is a file that was never written, not a sitemap that needs fixing; the
same treatment robots.txt already gets.

Does it parse? Do the URLs it advertises resolve? **`<lastmod>` coverage is not checked here** —
`freshness-corroboration-audit` owns date signals and reports it; registering it in both places put
one check under two skills and made the README's count and the report's count disagree.

Prefer statuses the crawl already collected; probe at most 8 more with HEAD, and only where the site
answers HEAD honestly — a 403, 405 or 429 is recorded as *unchecked*, never as dead, because a
refusal is a fact about the crawler's reception and not about the page. A sitemap full of dead URLs
teaches crawlers the sitemap is unreliable.

**A sitemap must not list addresses that differ for every visitor** — `/cart/`, `/checkout/`,
`/my-account/`, `/wishlist/`. Each spends a crawler's budget on one visitor's session, and a shop
platform normally sends those pages `noindex`, contradicting the sitemap entry. This audit's
read-only guard already refuses to fetch three of the four by name: read them out of the sitemap and
say so. Whole path segments only — `/wishlist-ideas/` is an article.

---

## 6b. Unknown paths, and what a 200 proves

One GET of a path that cannot exist, **run before the sitemap, canonical and sitemap-entry checks**,
because its answer settles all of them.

A 200 means every wrong URL here looks like a real page — and that no status code on this origin is
evidence a file exists. A sitemap location answering 200 is then the catch-all route,
"`/sitemap.xml` does not parse" is an XML parser reading a homepage, and "every sitemap entry
resolves" is a statement about nothing: say so in those checks.

Where that request could not be made and nothing else established what arrived, **report the parse
failure as unchecked, never as a broken sitemap** — an XML parser gives the same kind of error for a
sitemap missing a tag and for 1.8 MB of HTML. **Medium.**

---

## 7. Bot manager probe

robots.txt permission means nothing if the edge returns a challenge page. Fetch the homepage with
the user agent of an answer crawler robots.txt does *not* disallow, and compare the status against
the crawl's baseline.

If it matches, try **one more agent from a different operator** before concluding the edge treats
crawlers no differently: bot rules are written per agent and per vendor, and probing whichever name
sat first in a list turned a real WAF block into a clean pass the moment the list was reordered.

If a status differs, fetch that agent once more to confirm; a single differing response is more often
a rate limiter than a policy. Two matching blocks is **high**, at medium confidence, and the finding
says which agents answered normally, so the reader knows the rule is per-agent. It is not critical
because the probe cannot see the address half of the rule: it sends the crawler's name from this
audit's address, and a CDN that verifies a crawler's address by reverse DNS or the operator's
published list refuses that request and serves the real crawler. The fix therefore starts with the
CDN's verified-bot settings and firewall log, not with an allow rule. Skip the probe entirely if robots.txt
already disallows every answer crawler, because the robots finding covers it. Maximum four requests:
two probe names, one re-baseline, one confirmation.

**A comparison whose arms share a confound settles nothing.** Both arms went through the same
`requests` session, so exactly one conclusion was available: a name answered differently from the
baseline, seconds apart, means the rule reads the name. The reverse never followed — a uniform 403 is
equally well explained by an edge scoring this client and never reading the name — and asserting one
from the status code produced a Critical "do first" step that was wrong half the time.

---

## 7b. Ask again through a second HTTP client

`Fetcher` re-runs the comparison through `urllib.request`: a different TLS handshake and header set,
no new dependency, the same read-only guard, and the client now held constant across every name.

Spent only where a refusal is already on the table — four answer-crawler names and one control — so a
site that answers this crawler and the probe names alike pays nothing here.

Where names differ that is the answer: **high**, `bot-manager-block`, naming which agents were refused
and which were served, and saying robots.txt names the refused ones nowhere. Where both clients are
refused under every name it is still undecided and must say so. The four outcomes, what each may claim,
and the measurements behind them are in `references/second-client-comparison.md`.

Where the cause is still undetermined, the first fix step is the measurement this skill cannot make: a
loop from the reader's own machine printing one status per crawler name, which changes the network too.
Never open with a list of every answer crawler.

**This is not the training-crawler finding and must never merge with it.** A training crawler disallowed
in robots.txt is a rights decision made on purpose, in a file the owner can read, and stays at `info`
with "this is often deliberate". An answer crawler refused at the edge and named nowhere in robots.txt
costs the citation.

---

## 8. Slow is not down

Take the median server response across the pages that answered 200. Over 3 s is **medium**, over 10 s is
**high**, and it is its own root cause (`slow-origin`) rather than a non-200.

One site answered every page in ten to forty seconds; enough requests timed out that the report called
it unreachable, with outage wording and a fix reading "read the server log for the failing request", on
a site that was up and serving correct HTML. Where a fetch does fail on a slow origin, say so in the
same sentence.

No measurement is not a fast one: `--no-network` and a fully blocked site both produce none, and both
decline the check.

---

## 9. Status and indexability across the crawled pages

Every check here must reach an outcome: one registered and then answered by neither a finding nor a
decline is printed as clean, which is what `homepage-reachable` and `non-200-rate` did on a crawl that
fetched nothing. Where the snapshot carries `site_not_serving` the decline cites its reason, and the
homepage is found by requested address, then answering address, then page type — see
`references/robots-path-vocabulary.md`.

**Homepage non-200** is **critical**. A **non-200 rate** at or above 10% is a systemic problem — **high**
at 25% or more, **medium** below that; under 10%, stay quiet, and under three fetched URLs stay quiet
whatever the percentage, because a rate computed from one blocked request restates the homepage finding
rather than adding to it.

**Redirect chains** longer than two hops are **low**.

**`noindex`** in a robots meta tag, a per-crawler meta tag such as `<meta name="googlebot">`, or an
`X-Robots-Tag` header on a content page is **high**: the page is fetched and then thrown away. Read
every robots meta tag on the page, not the first: a search engine applies the most restrictive directive
it finds, so a theme that writes `index,follow` into the head and a plugin that appends `noindex` further
down leave the page unindexed — unless the page's own title says it is missing ("Page Not Found",
"Nothing To See Here"), in which case `noindex` is the correct thing for the site to do and telling the
owner to remove it would put a broken page into the index. Say in the exemption line that you found one
and why you left it alone.

**Canonical tags** pointing off-domain are **high**; pointing at a non-200 URL is **medium**.

**`<meta http-equiv="refresh">`** instead of an HTTP status is **high** when the homepage is among them,
**medium** otherwise, under its own root cause (`meta-refresh`): a meta refresh is a redirect only for
something that renders HTML, so a server-side crawler reads the stub and stops. The crawl follows it so
the rest of the report describes the real page, and the stub is reported separately, because a consumer
that does not follow it sees what we first saw.

---

## 10. Transport and hostnames

Plain HTTP is **medium**, skipped for localhost and bare IP origins. Canonical tags split across `www`
and non-`www` are **medium**, because two hostnames serving one site split every signal that would
otherwise reinforce it.

**A default port is not a hostname.** `:443` on https and `:80` on http name the port the scheme already
implies, and a server may spell one out in a `Location` header. A publisher's site redirects its bare
domain to `https://www.example.test:443/`, and the audit kept that spelling: 36 pages whose canonicals
are relative were reported as declaring "a canonical URL on a different domain", with an example pointing
at itself, and the host-split finding listed `www.example.test` and `www.example.test:443` as its two
hostnames. `normalise_url` and `strip_www` in the shared library drop a default port now, and this check
groups canonicals on the port-free `netloc` — `www.` kept, because that split is real.

**An absent canonical is what makes the host split a finding, not what makes the check inapplicable.**
Where no canonical is declared, compare the addresses the pages finally answered on: a path reached under
two hostnames, answering with the same title and the same amount of body text, is one document served
twice with nothing saying which copy is real. That is **medium**, and it is this skill's finding.

A report once filed this check as not applicable — "no canonical URLs were declared, so there is no host
split to assess" — on a site serving identical pages on `www` and the bare domain, and the duplication was
then reported by another skill as a duplicate-`<title>` defect with the fix "duplicated titles usually
come from a template that omits the page variable": a template change, on a site whose template is fine.

A path that differs between the two hostnames (a national edition, say) is two pages and is left alone,
and a canonical naming one hostname settles the question and closes the check.

---

## 11. Agent-readable files

Request `/llms.txt` and record what answered: a file, an HTML page served at that address, a redirect, or
a refusal. Then read robots.txt for any other agent file the site names itself (`llms-full.txt`, `ai.txt`,
`agents.md` and the rest of `AGENT_FILE_NAMES`), and request up to `AGENT_FILE_PROBE_LIMIT` of those,
skipping any path robots.txt closes to this auditor.

Absence is never a finding here; it feeds a proactive recommendation instead. A file this audit never
requested is reported as unknown, never as absent — `/llms-full.txt` is not asked for unless robots.txt
names it, and the report says so rather than counting it missing.

---

## The two rules that govern every check above

**A refused request is not an answer.** This is the single easiest way to write something false. If
`sitemap.xml` returns 429, you have not learned that there is no sitemap; you have learned that the edge
refused you. One report said "No XML sitemap is available" as settled fact and recorded `/llms.txt` as a
clean pass, on a site where both requests returned 429 — in the same document that correctly hedged the
identically blocked robots.txt. Report a refusal as **unchecked**, in every check, or not at all.

**A percentage of one page is not a second observation.** A site whose homepage is blocked produces one
fetch. Reporting "the homepage refuses this crawler" and "100% of crawled pages refuse this crawler" as
two findings prices one blocked request twice.

---

## Why the not-applicable reasons are worded as they are

- **robots.txt returns a non-200.** There are no rules, so nothing is disallowed. Recorded as not
  applicable, never as a finding.
- **Every AI answer crawler is already disallowed.** The bot-manager probe is skipped so the audit does
  not send a request it has been asked not to send.
- **No extra requests could be made.** The probe, the sitemap HEAD checks, the missing-path probe and the
  agent-file requests are skipped, and the reason says **which** of the two causes applies. The
  orchestrator appends `--no-network` both when the caller asks for it and when the run has spent its wall
  clock, so the flag alone cannot tell a choice from an exhaustion — a report once told a reader three
  times over that they had passed `--no-network` on a run where they had passed nothing, while the real
  cause, a `Crawl-delay: 10` this audit honoured, appeared nowhere. A run handed under 30 seconds of budget
  reports a spent clock and names what used it.
- **The origin is localhost or a bare IP.** Transport security is a deployment concern there, not a site
  defect.
- **The homepage was never fetched.** There is no baseline for the bot comparison.
