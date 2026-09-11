# Reading a refusal with two HTTP clients

The user-agent comparison in `scripts/check.py` decides one thing: when an edge
refuses a request, is it refusing the crawler name, or refusing the client that
sent it? The two have opposite first moves — an allow rule per user agent, or a
conversation with the CDN about bot score, TLS fingerprint, address reputation
and rate limits — and a report that picks the wrong one costs the owner an
afternoon and leaves the site exactly as invisible as it was.

## Why one client could not decide it

Every probe used to leave through the same `requests` session. The client was
therefore held fixed in both arms of the comparison, which makes exactly one
conclusion available:

> A name answered differently from the baseline, from the same client seconds
> apart, so the rule reads the name.

The reverse never followed. When every probe came back refused, two rules fitted
the same table:

| rule | what one client sees | what fixes it |
| --- | --- | --- |
| the edge refuses every crawler name | every probe 403 | allow the names |
| the edge scores this client and refuses it whatever name it carries | every probe 403 | no name rule helps |

Measured on a charity with plain curl, one request per name: three answer-crawler
names came back 403 and four came back 200, while through this skill's client all
seven came back 403. A report reasoning from those uniform 403s published the
reverse of that table and named none of the three agents that were actually
refused.

## What changed

`Fetcher` has a second transport. `SECOND_TRANSPORT` sends through
`urllib.request` from the standard library — a different TLS handshake, a
different default header set and a different header order, and no new
dependency. The comparison is then re-run with that client held constant across
every name, which is the one arrangement that separates the two rules above.

**The read-only guarantee is unchanged, because it is about method and path
rather than about which library writes the socket.** Both transports enter
through `Fetcher.get`, which refuses a forbidden path (with the
`READ_ONLY_API_ALLOWLIST` exception), refuses anything but GET or HEAD, spends a
request from the one budget, waits out the one delay and checks the one wall
clock — all before either client is chosen. There is no second gate to keep in
step with the first because there is no second gate.

## What it costs

Only where a refusal is already on the table: a uniform refusal through the
primary client, or a differential that client has just confirmed. Four
answer-crawler names, one operator each (`SECOND_TRANSPORT_AGENTS`), plus one
control request under this audit's own name — and the control only where the
primary client was itself refused, because that is the only place it decides
anything. Where the primary client was served, the baseline it already holds is
the control.

A site whose edge answers this audit and answers the probe names alike never
reaches this code and pays nothing for it.

## The four outcomes, and what each may claim

| second client saw | verdict | what the report says |
| --- | --- | --- |
| some names refused, others served | `UA_NAMES_DIFFER` | that edge treats these names differently from this address: high severity at medium confidence, `bot-manager-block`, naming which agents were refused and which were served, and saying the address was not the crawlers' own (see below) |
| every name served, this audit's own name served | `UA_CLIENT_SCORED` | no name is being read; the primary client is what is refused, and no crawler allow rule lifts it |
| every name served, this audit's own name refused by both clients | `UA_AUDIT_ONLY` | the edge reads the name and decides in the crawlers' favour — a limit on this run, not a defect in the site |
| every name refused, through both clients | `UA_CLIENT_REFUSED` | still undecided, and it says so: two clients can share an address, and an edge refusing this address refuses both alike |

Where the cause is still undetermined the first fix step is the measurement this
skill cannot make: a loop from the reader's own machine printing one status per
crawler name, which changes the network as well as the client and the name. The
allow rule is written from that output. Never open with a list of every answer
crawler; several of them are usually already being answered.

## What no probe from here can see: the address behind the name

Every request this skill sends leaves from the machine running the audit. A real answer
crawler never does: it sends from its operator's published address ranges, and the large CDNs
check that — by reverse DNS, or against the operator's published list — before treating a
request as the crawler it names. A request claiming `Claude-SearchBot` from any other address
is an impersonator to that rule, and refusing it is the rule working while the real crawler is
served.

So a refused name proves the edge treats that name differently **from this address**. It
cannot separate a rule on the name from a rule verifying the address behind the name, and the
two have opposite fixes: the first needs an allow rule, the second needs nothing. Two shops had
this finding ranked critical and first in "Start here", telling them to allow crawlers their
CDN may have been serving all along.

What the findings do about it:

- `bot-manager-blocks-ai-crawlers` is **high** at most (medium where the response is not
  blocking-shaped), never critical, and at **medium** confidence.
- `edge-refuses-named-answer-crawlers` stays **high**, at **medium** confidence.
- Both evidence lines say the requests came from this audit's address and not the crawlers'.
- Both fixes open with the check the owner has to make first: the CDN's verified-bot
  settings and its firewall log for those names. Only a rule refusing the name whatever
  address sends it is one to change.

## Why the refused names are named, and not merely counted

The list is the fix. A two-name probe reports whichever refused agent it reached
first: on a museum's edge that is `Claude-SearchBot`, and `PerplexityBot` — also
refused, also absent from robots.txt — went unmentioned while `OAI-SearchBot` and
`Applebot` were being answered normally the whole time. Four operators is what it
takes for that pattern to be visible, and the finding prints both lists so an
owner writes one rule rather than half of one.

## This is not the training-crawler finding

A training crawler disallowed in robots.txt is a rights decision the owner made
on purpose, in a file they can open and read, and it stays at `info` with "this
is often deliberate" — see the role table in `ai-crawler-user-agents.md`. An
answer crawler refused at the edge and named nowhere in robots.txt is almost
always a bot category nobody read the membership of, it costs the citation, and
nothing on the site shows it. The two must never converge on one severity or one
fix.
