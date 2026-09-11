# AI crawler user agents

The user agents `crawl-access-audit` checks in robots.txt, and **what blocking each one
actually costs**. That distinction is the point of this file: most audits report "you block
AI crawlers" as one undifferentiated problem, when the groups below represent completely
different decisions.

> **The tables below are generated from the code.** `AI_CRAWLER_AGENTS` in
> `skills/crawl-access-audit/scripts/check.py` is the only place a role is written down, and
> `tests/test_crawler_roles.py` fails the build if this file and that table disagree. An
> earlier version of this file was maintained by hand beside the code and had Meta's two
> crawlers reversed relative to it.

> **Operators change these.** Tokens are added, renamed and split as products evolve, and an
> operator can change what a token is used for without announcement. Each row carries the
> page it came from and the date that page was read. Re-check before acting on a block.

---

<!-- BEGIN GENERATED: agent table, rendered from AI_CRAWLER_AGENTS in check.py -->

## Search index agents (7)

These index pages so the brand can be surfaced and linked in an answer. Blocking one costs citations.

| User agent | Operator | What the operator says it does | Source |
|---|---|---|---|
| `OAI-SearchBot` | OpenAI | surfaces websites in search results in ChatGPT's search features | [operator docs](https://developers.openai.com/api/docs/bots), read 2026-09-03 |
| `Claude-SearchBot` | Anthropic | analyses online content to improve search result quality | [operator docs](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler), read 2026-09-03 |
| `PerplexityBot` | Perplexity | surfaces and links websites in Perplexity search results; explicitly not used for foundation-model training | [operator docs](https://docs.perplexity.ai/guides/bots), read 2026-09-03 |
| `Applebot` | Apple | powers Spotlight, Siri and Safari search | [operator docs](https://support.apple.com/en-us/119829), read 2026-09-03 |
| `Amzn-SearchBot` | Amazon | improves search experiences in Amazon products such as Alexa; does not crawl for generative AI training | [operator docs](https://developer.amazon.com/amazonbot), read 2026-09-03 |
| `DuckAssistBot` | DuckDuckGo | gathers passages that DuckAssist cites in instant answers | [operator docs](https://duckduckgo.com/duckduckgo-help-pages/results/duckassistbot), read 2026-09-03 |
| `YouBot` | You.com | indexes pages for an answer engine that links its sources | **no published statement** - role inferred from behaviour |

## Live-fetch agents (6)

These fetch one page because a person just asked a question about it. Blocking one costs that answer.

| User agent | Operator | What the operator says it does | Source |
|---|---|---|---|
| `ChatGPT-User` | OpenAI | visits a web page when a user asks ChatGPT a question | [operator docs](https://developers.openai.com/api/docs/bots), read 2026-09-03 |
| `Claude-User` | Anthropic | accesses a website when an individual asks Claude a question | [operator docs](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler), read 2026-09-03 |
| `Perplexity-User` | Perplexity | visits a web page to help answer a question a user just asked | [operator docs](https://docs.perplexity.ai/guides/bots), read 2026-09-03 |
| `Amzn-User` | Amazon | supports user actions such as answering an Alexa query that needs current information; does not crawl for generative AI training | [operator docs](https://developer.amazon.com/amazonbot), read 2026-09-03 |
| `Meta-ExternalFetcher` | Meta | fetches individual links at a user's request, including helping AI navigate sites to complete tasks | [operator docs](https://developers.facebook.com/docs/sharing/webmasters/web-crawlers/), read 2026-09-03 |
| `MistralAI-User` | Mistral | fetches a page when a Le Chat user asks about it | **no published statement** - role inferred from behaviour |

## Training crawlers (13)

These collect corpora for model training. Blocking is a rights decision plenty of publishers make on purpose, and this audit reports it at `info` severity, never as a defect.

| User agent | Operator | What the operator says it does | Source |
|---|---|---|---|
| `GPTBot` | OpenAI | crawls content that may be used in training OpenAI's foundation models | [operator docs](https://developers.openai.com/api/docs/bots), read 2026-09-03 |
| `ClaudeBot` | Anthropic | collects web content that could contribute to model training | [operator docs](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler), read 2026-09-03 |
| `anthropic-ai` | Anthropic | legacy token still seen in robots.txt; superseded by the three above and no longer documented | **no published statement** - role inferred from behaviour |
| `Amazonbot` | Amazon | fetches content for Amazon products and services, and may be used to train Amazon AI models | [operator docs](https://developer.amazon.com/amazonbot), read 2026-09-03 |
| `Meta-ExternalAgent` | Meta | crawls for use cases such as training foundation AI models or indexing content directly | [operator docs](https://developers.facebook.com/docs/sharing/webmasters/web-crawlers/), read 2026-09-03 |
| `Bytespider` | ByteDance | collects training data for ByteDance's models; ByteDance publishes no crawler documentation, so this role is inferred from observed behaviour | **no published statement** - role inferred from behaviour |
| `cohere-ai` | Cohere | corpus collection; no published purpose statement | **no published statement** - role inferred from behaviour |
| `CCBot` | Common Crawl | builds the open Common Crawl corpus that many models train on | [operator docs](https://commoncrawl.org/ccbot), read 2026-09-03 |
| `omgilibot` | Webz.io | collects web data for licensing to model builders | **no published statement** - role inferred from behaviour |
| `Diffbot` | Diffbot | extracts pages into a commercial knowledge graph | **no published statement** - role inferred from behaviour |
| `Timpibot` | Timpi | builds a distributed index sold as training data | **no published statement** - role inferred from behaviour |
| `PanguBot` | Huawei | collects training data for the PanGu models | **no published statement** - role inferred from behaviour |
| `ImagesiftBot` | ImageSift | collects images for dataset building | **no published statement** - role inferred from behaviour |

## Opt-out tokens, which are not crawlers (3)

These fetch nothing. The token exists only so a site can express a training opt-out, so blocking one cannot cost a citation.

| User agent | Operator | What the operator says it does | Source |
|---|---|---|---|
| `Applebot-Extended` | Apple | opt-out token for training Apple's foundation models; does not affect Siri or Spotlight, which follow Applebot | [operator docs](https://support.apple.com/en-us/119829), read 2026-09-03 |
| `Google-Extended` | Google | opt-out token for Gemini training and grounding; does not affect Google Search indexing, which follows Googlebot | [operator docs](https://developers.google.com/search/docs/crawling-indexing/google-common-crawlers), read 2026-09-03 |
| `Webzio-Extended` | Webz.io | training opt-out token for Webz.io datasets | **no published statement** - role inferred from behaviour |

<!-- END GENERATED -->

---

## What this replaced, and why the shape changed

The first version of this file had two groups and named them by consequence: "answer
crawlers" and "training crawlers". `GPTBot`, `ClaudeBot`, `Amazonbot`, `Bytespider` and
`Meta-ExternalAgent` were in the first; `Meta-ExternalFetcher` was in the second. All six
were wrong against their operators' own published descriptions, and the finding built on
them told site owners, at high severity, to allow-list the five so their pages could be
cited.

That advice would have reopened a site to training collection its owner had deliberately
opted out of, to fix a citation problem that did not exist. It is the most damaging thing
this audit has ever said about a real site.

Three things changed, and none of them is "the list was corrected":

- **One role became two.** A search index and a live per-question fetch fail differently and
  are worth telling apart, so `search` and `user-fetch` are separate. They are grouped
  together only where the check genuinely does not care.
- **A fourth role exists for tokens that are not crawlers.** `Google-Extended` and
  `Applebot-Extended` fetch nothing. They are opt-out signals. Blocking one cannot cost a
  citation, and describing them as crawlers was the reason `Google-Extended` kept being
  confused with `Googlebot`.
- **Each row carries its source and the date it was read**, and a row with no published
  statement says so in the report rather than presenting an inference as the operator's
  position.

The finding's own sentences are now built from these rows rather than written beside them,
so the report cannot describe an agent as doing something the table does not say.

---

## How the check reads robots.txt

Agent matching follows the Robots Exclusion Protocol as the major crawlers implement it:

- **Longest matching user-agent token wins.** A group naming `ClaudeBot` beats `*` for
  ClaudeBot.
- **`*` applies only when no named group matched.** It is a fallback, not an overlay — a
  named group does not inherit `*`'s rules.
- **Within a group, the longest matching path rule wins**, and `Allow` beats `Disallow` at
  equal length.
- **`*` wildcards and `$` end-anchors are supported** in path patterns.

Two questions are asked of every agent in the tables above, not one:

- **Is the agent disallowed from `/`?** The whole site, shut.
- **Does a group *naming* the agent close a section that no `User-agent: *` rule closes?**
  A named group is not an overlay on `*` — it replaces it — so a rule written inside one is
  a rule no check that reads the `*` group can see. A robots.txt whose single group named
  eleven of the agents above and carried `Disallow: /store/` was reported clean by the first
  question and excused by the check that reads the `*` group, on a shop where that path
  holds every product and every price. Section blocks are reported by role like everything
  else here: the answer and search agents are the defect, the training crawlers and opt-out
  tokens are `info` and get their own finding, and the remedy splits the one group in two so
  a deliberate training opt-out survives the fix.

A section block is priced rather than merely named. The finding says how many of the pages
the crawl already fetched sit behind the rule, because "`/store/` is disallowed" does not
tell an owner whether that is three pages or the catalogue. At least two of them and at least
a tenth of the crawl is high; fewer is medium; a path the crawl never reached is medium at
medium confidence, and says so.

Two consequences worth knowing, both of which the parser handles:

- A group with `Disallow: /` **and** any non-trivial `Allow:` line is not a site-wide block,
  so `blocks_entire_site` returns false and the audit does not report a critical finding.
- Rules appearing before the first `User-agent:` line apply to **nobody**. Some site owners
  believe otherwise. They are recorded as parse errors, because the access policy the site
  believes it has is not the one crawlers see.

## Paths that are never flagged

Disallowing these is normal hygiene, not a discoverability problem, and flagging them would
be the single largest source of false positives in this skill:

`/wp-admin`, `/admin`, `/administrator`, `/cgi-bin`, `/cart`, `/checkout`, `/basket`,
`/account`, `/my-account`, `/customer`, `/user`, `/profile`, `/login`, `/signin`, `/logout`,
`/register`, `/signup`, `/search`, query-string patterns, `/tmp`, `/private`, `/internal`,
`/api`, `/graphql`, `/xmlrpc`, `/wp-includes`, `/wp-json`, `/feed`, `/print`, `/thank-you`,
`/order`, `/preview`, `/draft`.

## The limit of robots.txt

robots.txt states a *policy*. It cannot show you an *enforcement* layer. A CDN or WAF
returning 403 or a challenge page to `ClaudeBot` is invisible in robots.txt and defeats every
other fix on the site.

That is why this skill also performs a live user-agent comparison — one probe, plus one
confirmation, using the first answer crawler robots.txt does *not* disallow, skipped entirely
when they are all disallowed. It is the only place in this marketplace that sends a
user-agent other than its own, and it is documented as such in the orchestrator's safety
section.

### The string a probe sends is the crawler's own

Each probe sends the full user-agent string the operator publishes for that crawler — the
`published_ua` field of `AI_CRAWLER_AGENTS` in `check.py`, copied word for word from the page
each row above cites — followed by this audit's own name in brackets. It used to send the bare token, and an
edge can tell the two apart: one shop's CDN answered `OAI-SearchBot` with the page and refused
the crawler's published `Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible;
OAI-SearchBot/1.3; +https://openai.com/searchbot` with 403, so the report said a crawler was
served that, sending its real string, was not. Anthropic, You.com and Mistral publish only
the token, so for those the token is what is sent; guessing the rest would measure a string
nobody published.

### What that comparison can and cannot conclude

Both arms of it go through the same HTTP client. The name is the only variable that moves,
so the client is a confound shared by both arms, and a comparison whose arms share a
confound cannot settle the question it was built to ask.

One conclusion survives that: **a name answered differently from the baseline, from the same
client seconds apart, means the rule reads the name.** The reverse does not. Uniform
refusals across every name are equally well explained by an edge that scores this client's
TLS and header fingerprint and never looks at the name at all.

Measured on a charity that refused this audit, one request per user agent with plain curl
from the same machine:

| user agent | curl | this skill's client |
|---|---|---|
| `OAI-SearchBot`, `GPTBot`, `Applebot`, `Googlebot` | 200 | 403 |
| this audit's own agent string | 200 | 403 |
| `ClaudeBot`, `Claude-SearchBot`, `PerplexityBot` | 403 | 403 |
| `curl/8.0`, `python-requests/2.32` | 403 | 403 |

That edge reads the name for three of them. This skill's client sees a uniform 403 and can
see nothing else, so the check now reports "this client is refused under every name tried,
and what the edge reads was not determined from here" and makes the loop that produces the
table above the first fix step. Reading the uniform column as "the name does not matter"
published the reverse of the row that mattered.

## Why this crawler does not try harder to get in

Across three samples, 15 sites refused us. We ran the obvious experiment before
accepting that: fetch each of them with a complete, honest header set — a real
`Accept`, `Accept-Language`, `Accept-Encoding`, `Connection`, and
`Upgrade-Insecure-Requests` — and then again on the other host form, with our
own User-Agent unchanged throughout.

**Nothing changed. Zero of 15.** Every one returned the same status. These are
not sites refusing a malformed client; they are sites that have decided which
agents may read them, and the decision holds however politely it is asked.

The headers are in the code anyway, because a request with no `Accept` header is
an incomplete request and we would rather send a correct one. They are not there
in the hope of getting past anything.

Getting into these sites would mean presenting a browser's identity — a
browser's User-Agent, or a browser's TLS fingerprint. That is circumventing an
access decision the site owner made deliberately, and it is out of scope for an
audit that exists to *report* access problems. A block is a finding, not an
obstacle: `bot-manager-block` says which agent was refused, what the response
was, and what the owner would change if the block was not intended.

What the experiment did find is worth more than a workaround. Three of the 15
answered `200` on the next attempt: they had never been blocked at all, just
briefly unreachable, and a single dropped request was marking an entire site
unreadable. The client now retries twice, with backoff, on a dropped connection,
a timeout, a 429 or a 5xx — and never on a 403, because a 403 is an answer. That
correction alone moved the measured block rate meaningfully, and the earlier
figures in the field study overstated it.
