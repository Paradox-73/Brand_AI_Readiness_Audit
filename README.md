# Brand AI-Readiness Audit
### An Agent Skill Marketplace · Adobe University Hackathon 2026 — Round 3

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Agent Skills](https://img.shields.io/badge/Agent%20Skills-agentskills.io-6E56CF?style=flat-square)
![Skills](https://img.shields.io/badge/Skills-7%20(1%20entrypoint)-0F9D58?style=flat-square)
![Tests](https://img.shields.io/badge/Tests-598%20passing-2EA043?style=flat-square)
![Read Only](https://img.shields.io/badge/Mode-Read--only-FF6F00?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-4169E1?style=flat-square)

Point it at any website. It reports why AI assistants cannot find, read, trust or correctly
describe that brand, and why visitors who do arrive leave again — with the evidence for each
problem and the specific fix.

> **Read-only.** GET and HEAD only, robots.txt respected, 0.5 s between requests. The audit
> never signs in, submits a form, or touches `/cart`, `/checkout`, `/login` or `/admin`. It
> recommends changes; it never makes them. Enforced in code, not promised in prose.

---

## What each skill does

Seven skills. One is the entrypoint; the other six are a **sequence of gates**, and each only
matters if the one before it passed.

| Skill | The question it answers | Gate |
|---|---|---|
| **`audit-orchestrator`** | *Entrypoint.* Crawls once, runs the six, merges and prioritises | — |
| `crawl-access-audit` | Can a crawler get in at all? robots.txt, bot managers, dead pages, `noindex`, redirect chains, canonicals, transport | Let in |
| `render-readability-audit` | Is there anything to read in what comes back? JavaScript shells, text locked in images or PDFs, uncrawlable pagination | Can read |
| `structured-data-audit` | Are the facts labelled, or must a machine guess? JSON-LD presence, validity, agreement with the page, the right type per page type | Machine-readable |
| `fact-extractability-audit` | Is there a sentence worth quoting? A one-line definition of the brand, prices and contact routes in plain text, sentences short enough to excerpt | Quotable |
| `freshness-corroboration-audit` | Is it current, and does anywhere else agree? Dates, staleness, off-site profile breadth, name collisions | Trusted |
| `engagement-audit` | Does the person who clicks through stay? Orientation, dead ends, orphans, broken links, breadcrumbs, interstitials, forms | Visitor stays |

Between them the six run **71 checks**, drawing on a catalogue of **77 distinct findings**
and **55 named root causes**. A single audit reports only the ones its evidence supports —
a typical small site produces ten to twenty.

Six is the number of gates there are. A test fails the build if any two skills ever claim the
same root cause, so the separation is enforced rather than asserted.

---

## How the entrypoint composes them

**One crawl, six readers.**

```
              audit-orchestrator
                       |
              crawls the site ONCE
                       |
                 snapshot.json          <- every fact about every page
                       |
   +--------+--------+--------+--------+--------+--------+
   |        |        |        |        |        |        |
 crawl-  render-  structured- fact-  freshness- engagement
 access  readab.    data    extract.  corrob.    audit
   |        |        |        |        |        |
   +--------+--------+--------+--------+--------+
                       |
              compose_report.py
        merge, deduplicate, score, prioritise
                       |
              report.md  +  report.json
```

The six sub-skills **never fetch the site themselves**. They read fields off `snapshot.json`.
That is what makes this one crawl rather than six.

**Measured, not estimated.** The crawl is capped at 60 page requests. Four sub-skills then make
a bounded number of extra read-only requests — link and sitemap targets the crawl did not
reach, and one Wikidata lookup — declared in each skill's `compatibility` line and capped at 40,
16, 10 and 2. On two large public sites the totals were **117 and 103 requests, in 223 s and
195 s** of the 300 allowed; a small site finishes in about 20 s. `--no-network` reduces it to
the 60.

`compose_report.py` merges the six result files. It deduplicates **across** skills — while
deliberately keeping several findings from one skill that share a cause, because those are
separate fixes — sets stable IDs, scores priority, simulates what an assistant could quote,
and adds the proactive recommendations whose conditions are present.

Priority is a formula, not a judgement:

```
priority = severity weight x reach / effort
```

**Reach** is the share of crawled pages affected, floored at 0.25; a finding with no listed
pages affects the whole site and gets 1.0. **Effort** divides: low 1.0, medium 1.5, high 2.0.
Severity weights run critical 4, high 3, medium 2, low 1, info 0. The three highest become the
report's **Start here** section — so a one-line robots.txt correction that unblocks a whole
site outranks a rewrite that fixes one page.

---

## What a finding contains

| Part | Example |
|---|---|
| **Evidence** | "0 of 12 product pages contain schema.org markup" — counts and sampled URLs, never adjectives |
| **Mechanism** | Which lettered mechanism (A–G) the fix repairs |
| **Fix** | Concrete steps, plus paste-ready code where code is the answer |
| **Owner** | developer / content owner / marketing |
| **Effort** | low / medium / high |

Two properties worth naming.

**Silence is explained.** Every check is accounted for in the report appendix. The ones that
declined say why — *"No product detail pages were detected on this site, so Product and Offer
markup is not expected."* The ones that ran and were clean are listed as having passed, so a
reader can see that robots.txt was fetched and was fine rather than wondering whether it was
looked at. A check never simply goes missing.

**A deliberate choice is not a fault.** A site blocking AI *training* crawlers is reported as
information, not a problem. It is a rights decision, and calling it a defect would impose a
view the brand never asked for.

Suggested actions go beyond defects: the recommendation catalogue fires on what a site is
missing, not only on what is broken.

---

## Running it

> **There is nothing to install.** `marketplace.json` at the root is the contest's own
> manifest convention, not a package format — the brief notes that agentskills.io defines the
> single-skill `SKILL.md` format and does not define a multi-skill one. So there is no
> `/plugin marketplace add` step and no `.claude-plugin/` directory. Each of the seven folders
> is independently valid against the agentskills.io spec; the manifest names which one is the
> entrypoint. Run it as below, or point an agent at `skills/audit-orchestrator/SKILL.md` and
> let it follow the procedure there.

If `requests`, `beautifulsoup4` and `lxml` are already installed, skip the virtual
environment and go straight to the last two commands.

```bash
# macOS / Linux
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
```powershell
# Windows PowerShell. The first line is needed because Windows blocks
# script execution by default; it applies to this shell only.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Rather not change the policy? In `cmd.exe` activate with
`.venv\Scripts\activate.bat`; in Git Bash, `source .venv/Scripts/activate`.

Then run it:

```bash
# macOS / Linux
python3 skills/audit-orchestrator/scripts/validate_marketplace.py
python3 run_audit.py example.com --out-dir ./out
```
```powershell
# Windows
python skills\audit-orchestrator\scripts\validate_marketplace.py
python run_audit.py example.com --out-dir .\out
```

`out/report.md` is the one to read. `out/report.json` is the fixed-schema machine version,
specified in `skills/audit-orchestrator/references/report-schema.json`.

| Flag | Default | Effect |
|---|---|---|
| `--max-pages` | 60 | Page ceiling |
| `--budget` | 240 | Wall-clock seconds |
| `--render` | on when Playwright is installed | Compare static HTML against a browser-rendered DOM. `--no-render` turns it off |
| `--no-network` | off | Snapshot only; no extra probes |
| `--format html` | md | Also write `report.html` |

The tests, if you want them:

```bash
pip install pytest
python3 -m pytest -q                        # 598 tests, about 16 minutes
python3 -m pytest -q -m "not mutation"      # 541 tests, about 4 minutes
```

> **Windows path limit.** Do not unzip into a deep directory — `pip` cannot install `lxml` if
> the total path exceeds 260 characters. Use something short like `C:\audit`.

---

## Where the depth is

This README is deliberately short. Everything below lives where a skill can read it.

| Read | For |
|---|---|
| `skills/audit-orchestrator/references/cited-vs-uncited-study.md` | **The field research.** 36 matched-pair sites, then three holdout samples of 24, 32 and 40 more, each predicted in writing before it was run. Results and failures both |
| `skills/audit-orchestrator/references/how-the-checks-were-built.md` | The three methods behind the checks, and what each one caught |
| `skills/audit-orchestrator/references/mechanism-model.md` | The seven mechanisms every check traces back to |
| `skills/audit-orchestrator/references/severity-and-priority.md` | The scoring formula, worked |
| `skills/*/SKILL.md` | Each skill's checks, in prose an agent can follow without Python |
| `tests/mutations.py` | The shortest honest description of what every check means |
| `evals/README.md` | Three prompts a judge is likely to type, and what should happen |

**One number, if you read nothing else.** Brands assistants name link to **7.2** other places
about themselves; comparable competitors they ignore link **4.6**. The audit also confirms
those profile links still resolve, on the six platforms that answer that question honestly.
That separated in all six categories studied — the only measure that did — and it is the
cheapest fix here. You cannot become famous this quarter; you can claim six profiles this week
and put the same sentence on all of them.

*What that number is and is not:* our own study of 29 crawlable sites in six categories, matched within category. Whether an assistant "names" a brand was our prominence judgement, not a systematic query of any assistant, so this is the strongest association we found and not a proven cause. The study says so at greater length, including the measures that did **not** separate the two groups.

---

## Limits, stated rather than found

- **A response is read to 5 MB and no further.** Above that the page is analysed up to the
  cap, recorded as truncated, and the report says so. The heaviest real homepage we
  measured was 1.38 MB, so nothing ordinary comes near it.
- **On a site that refuses HEAD, unreached link targets are unchecked.** Three of eight
  major commercial sites answer 403 to HEAD and 200 to GET; verifying those links would mean
  fetching each in full. The report says how many went unchecked, not that they are broken.
- **Sixty pages is still a sample** on a large site; the report says how many it read, and
  every finding describes only those pages. Sixty was measured: thirty pages found 16
  problems in 84 s, sixty found 19 in 99 s, a hundred added one for another 72 s.
- **The prose checks are English.** The definition pattern, the answer-first test, sentence
  length and the call-to-action verbs all assume it. The crawl detects the site language once
  and those checks **decline** on a site in another language rather than guessing. Everything
  structural still runs.
- **Engagement is read from markup, not analytics.** It finds structural reasons a visitor
  would leave. It cannot tell you that one did.
- **Some sites refuse us, and we do not work around it.** Completing our HTTP headers
  changed nothing on zero of fifteen blocked sites, and getting in would mean wearing a
  browser's identity. A block is a finding.
- **Name conflicts are checked against Wikidata only.** That says how many organisations share
  a name, not which one an assistant currently prefers.
- **Only six platforms can be asked whether a profile exists** — LinkedIn, X, YouTube,
  GitHub, Wikipedia and Wikidata answer 404 for one that is not there. Instagram, TikTok,
  Medium, Pinterest and Threads answer 200 for anything, and Crunchbase, Yelp, Glassdoor and
  Trustpilot refuse every crawler; those nine are reported as unchecked.
- **Without Playwright the JavaScript gap is inferred, not measured**, so the homepage-shell
  finding drops to medium confidence. With Playwright — used automatically when installed —
  it is measured, and a homepage whose text JavaScript recovers is high rather than critical.
  Both verdicts are correct; the difference is what was known.
- **A robots.txt block stops the run.** The verdict explains the empty result instead of
  presenting it as a clean bill of health.
- **A refused request is unchecked, never an absence**, and a path robots.txt disallows is
  never fetched — so "this disallow looks like real content" is a judgement about the path,
  not a measurement of what is behind it. Both findings say so.
- **The JavaScript gap is measured on five pages.** A finding naming thirty extrapolates
  from those five, says how many were rendered, and drops to medium when the rendered ones
  recovered their text.

---

## Licence

MIT. See `LICENSE`.
