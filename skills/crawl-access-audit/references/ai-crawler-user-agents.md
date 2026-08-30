# AI crawler user agents

The user agents `crawl-access-audit` checks in robots.txt, split by **what blocking them
actually costs**. That split is the point of this file: most audits report "you block AI
crawlers" as one undifferentiated problem, when the two groups represent completely
different decisions.

> **Operators change these.** User-agent tokens are added, renamed and split as products
> evolve, and an operator can change what a token is used for without announcement. Treat
> the groupings below as accurate at the time of writing and worth re-checking against each
> operator's published documentation before acting on a block.

---

## Group 1: answer crawlers — blocking these costs citations

These fetch live pages to build or support an answer a user is reading right now. A block
here removes the brand from answers directly, which is a discoverability defect.

| User agent | Operator | Notes |
|---|---|---|
| `OAI-SearchBot` | OpenAI | Search index for ChatGPT search. The clearest citation-affecting agent OpenAI publishes. |
| `ChatGPT-User` | OpenAI | User-triggered fetch — someone asked and ChatGPT is retrieving the page now. |
| `GPTBot` | OpenAI | Documented primarily as a training crawler, but OpenAI states it also affects whether content can surface. Grouped here because the citation risk is real; see the note below. |
| `ClaudeBot` | Anthropic | General crawler. |
| `Claude-User` | Anthropic | User-triggered fetch. |
| `Claude-SearchBot` | Anthropic | Search indexing. |
| `anthropic-ai` | Anthropic | Legacy token; still seen in robots.txt files. |
| `PerplexityBot` | Perplexity | Indexing for a product built around citing sources. |
| `Perplexity-User` | Perplexity | User-triggered fetch. |
| `Applebot` | Apple | Powers Siri and Spotlight. Distinct from `Applebot-Extended`. |
| `Amazonbot` | Amazon | Powers Alexa answers. |
| `Bytespider` | ByteDance | Widely reported as aggressive; blocking is often a bandwidth decision. |
| `Meta-ExternalAgent` | Meta | Crawler for Meta AI products. |
| `MistralAI-User` | Mistral | User-triggered fetch. |
| `DuckAssistBot` | DuckDuckGo | Powers DuckAssist answers. |
| `YouBot` | You.com | Indexing for a citing answer engine. |
| `cohere-ai` | Cohere | Retrieval. |

### The GPTBot judgement call

`GPTBot` is genuinely ambiguous and worth being explicit about. OpenAI documents it mainly
as the crawler whose content may be used to improve models — which is a training role — while
`OAI-SearchBot` and `ChatGPT-User` are the retrieval-side agents.

It sits in group 1 here because the *cost of being wrong* is asymmetric. Misclassifying it as
training-only would mean staying silent about a block that may suppress the brand, which is
the failure mode this audit exists to prevent. Misclassifying it the other way produces a
`high` finding whose fix — "decide which of these you want quoting your pages" — is
reasonable advice either way.

The finding text never claims GPTBot is purely a search crawler, and this file is cited from
it. Treated as an assumption throughout, not as a measured fact.

---

## Group 2: training crawlers — blocking these is a rights decision

These collect corpora for model training rather than fetching pages to answer a live
question. Blocking them is a legitimate choice about content rights, made by plenty of
publishers on purpose.

| User agent | Operator | Notes |
|---|---|---|
| `Google-Extended` | Google | **Controls Gemini training only.** It does not affect Google Search indexing or AI Overviews eligibility — that is `Googlebot`. This is the most commonly misunderstood token in the list. |
| `Applebot-Extended` | Apple | Training opt-out. Does **not** affect Siri or Spotlight, which follow `Applebot`. |
| `CCBot` | Common Crawl | Feeds an open dataset many models train on. |
| `Meta-ExternalFetcher` | Meta | Distinct from `Meta-ExternalAgent`. |
| `omgilibot` | Webz.io | Data licensing. |
| `Webzio-Extended` | Webz.io | Training opt-out. |
| `Diffbot` | Diffbot | Knowledge-graph extraction. |
| `Timpibot` | Timpi | Index building. |
| `PanguBot` | Huawei | Training. |
| `ImagesiftBot` | ImageSift | Image dataset collection. |

A block on this group is reported as `info` severity: recorded so the owner can confirm it is
intentional, never counted as a defect.

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
