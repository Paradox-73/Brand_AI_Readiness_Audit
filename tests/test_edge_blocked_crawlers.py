# -*- coding: utf-8 -*-
"""An edge that refuses some crawler names and serves others, seen past one client.

The comparison that decides whether a bot rule reads the crawler name held the
HTTP client fixed and moved the name. That answers the question in one
direction only. When every probe came back refused, two rules fitted equally
well and had opposite fixes:

    the edge refuses every crawler name         -> allow the names
    the edge refuses this HTTP client, whatever  -> no name rule helps at all
    name it carries

Both produce an identical table when the table is built with one client, so
the check declined - correctly, and at the cost of the most consequential
finding an access audit can make. Measured on a museum with one request per
name: two answer-crawler names were refused at the edge and three were served,
robots.txt named neither of the refused two, and the rule lived in a bot
manager nobody could read by opening a file on the site.

So `Fetcher` has a second HTTP client and the comparison is re-run through it,
with that client held constant across every name. What that changes, and what
it must never claim, is what this file pins:

  the guarantee    the second client passes the same gate as the first -
                   forbidden paths, GET or HEAD, the budget, the clock
  the finding      only where names actually differed, naming which were
                   refused and which were served, and neither more nor fewer
  the silence      a uniform refusal through both clients is still undecided,
                   and a site that refuses nobody pays for none of this
"""

from __future__ import annotations

import http.server
import importlib.util
import os
import sys
import threading

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    Fetcher, FetchError, PRIMARY_TRANSPORT, SECOND_TRANSPORT, SkillResult, USER_AGENT,
)


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
             "ca_for_edge_blocked_crawlers")


# --------------------------------------------------------------------------
# The read-only guarantee is about method and path, not about the library
# --------------------------------------------------------------------------

def test_the_second_transport_refuses_the_same_paths_as_the_first():
    """A second way to send bytes is not a second set of rules.

    The guard that keeps this audit from changing anything lives in one
    function. Routing a new client around it - even to answer a question worth
    answering - would move the promise from enforced code into a comment.
    """
    fetcher = Fetcher(delay=0)
    for path in ("/cart", "/checkout", "/wp-login.php", "/my-account/orders"):
        url = "http://127.0.0.1:1/" + path.lstrip("/")
        for transport in (PRIMARY_TRANSPORT, SECOND_TRANSPORT):
            with pytest.raises(FetchError) as refusal:
                fetcher.get(url, transport=transport)
            assert "state-changing or private path" in str(refusal.value)
    # And nothing was spent finding that out, by either client.
    assert fetcher.count == 0


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_the_second_transport_sends_nothing_but_get_and_head(method):
    fetcher = Fetcher(delay=0)
    with pytest.raises(FetchError) as refused:
        fetcher.get("http://127.0.0.1:1/", method=method, transport=SECOND_TRANSPORT)
    assert "only GET and HEAD are permitted" in str(refused.value)


def test_the_second_transport_spends_the_same_request_budget():
    """One ceiling over both clients, or the ceiling is not a ceiling.

    A budget the second client did not count against would let a check spend
    twice what its SKILL.md advertises while every counter in the run still
    read below the limit.
    """
    fetcher = Fetcher(max_requests=1, delay=0)
    fetcher.count = 1
    with pytest.raises(FetchError) as spent:
        fetcher.get("http://127.0.0.1:1/", transport=SECOND_TRANSPORT)
    assert "request budget exhausted" in str(spent.value)


def test_the_second_transport_stops_when_the_wall_clock_is_gone():
    fetcher = Fetcher(delay=0, deadline=0.0)
    with pytest.raises(FetchError) as spent:
        fetcher.get("http://127.0.0.1:1/", transport=SECOND_TRANSPORT)
    assert "wall-clock budget exhausted" in str(spent.value)


def test_an_unknown_transport_is_refused_rather_than_guessed_at():
    fetcher = Fetcher(delay=0)
    with pytest.raises(FetchError) as unknown:
        fetcher.get("http://127.0.0.1:1/", transport="curl")
    assert "unknown transport" in str(unknown.value)
    assert fetcher.count == 0


# --------------------------------------------------------------------------
# An edge that reads the client as well as the name
#
# The server below is the museum's shape, reduced to the two rules that
# produced it. It refuses a client by a header only the primary client sends,
# and it refuses two crawler names whatever client sends them. Through one
# client those two rules are indistinguishable; through two they are not.
# --------------------------------------------------------------------------

REFUSED_NAMES = ("Claude-SearchBot", "PerplexityBot")


class _EdgeServer:
    """Serves a page, refusing named crawlers and optionally one client.

    `refuse_names` are turned away whatever client asks. `refuse_primary`
    turns away the client that offers to decode compressed responses, which is
    what the audit's usual client does and the standard-library one does not -
    a header stand-in for the TLS and fingerprint scoring a real bot manager
    does, and enough to reproduce the confound exactly.
    """

    def __init__(self, refuse_names=REFUSED_NAMES, refuse_primary=True,
                 refuse_audit_name=False):
        self.refuse_names = tuple(refuse_names)
        self.refuse_primary = refuse_primary
        self.refuse_audit_name = refuse_audit_name
        self.seen = []

    def __enter__(self):
        edge = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self._serve(head_only=True)

            def do_GET(self):
                self._serve(head_only=False)

            def _serve(self, head_only):
                agent = self.headers.get("User-Agent", "")
                encoding = (self.headers.get("Accept-Encoding") or "").lower()
                # `gzip` in the offer identifies the audit's usual client; the
                # standard-library one asks for `identity`.
                looks_primary = "gzip" in encoding
                edge.seen.append({"user_agent": agent, "primary": looks_primary})
                named = any(name.lower() in agent.lower() for name in edge.refuse_names)
                is_audit_name = not any(
                    name.lower() in agent.lower() for name in CA.ANSWER_CRAWLERS)
                refuse = (named
                          or (edge.refuse_primary and looks_primary)
                          or (edge.refuse_audit_name and is_audit_name))
                body = (b"refused" if refuse
                        else b"<html><head><title>A page</title></head>"
                             b"<body><h1>A page</h1><p>Words on it.</p></body></html>")
                self.send_response(403 if refuse else 200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = "http://127.0.0.1:{}".format(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        return False

    def probes_by(self, primary):
        return [entry for entry in self.seen if entry["primary"] is primary]


def _snapshot(base_url, status=403):
    home = base_url.rstrip("/") + "/"
    return {"origin": base_url, "pages": [{"url": home, "status": status}],
            "robots": {}}


def _run_comparison(server, status=403):
    result = SkillResult("crawl-access-audit")
    fetcher = Fetcher(max_requests=CA.MAX_EXTRA_REQUESTS, delay=0)
    verdict = CA._check_bot_manager(
        result, _snapshot(server.base_url, status), {}, fetcher, True)
    return result, verdict, fetcher


def test_a_uniform_refusal_is_re_asked_through_a_second_client():
    """The finding this whole file exists for.

    Every name refused through the audit's usual client; two names refused and
    two served through another, from the same machine, seconds later. The
    client no longer moves, so the name is what is left to explain it.
    """
    with _EdgeServer() as server:
        result, verdict, _ = _run_comparison(server)

    assert verdict == CA.UA_NAMES_DIFFER
    # The primary client saw one answer and learned nothing from it.
    assert {entry["primary"] for entry in server.probes_by(True)} == {True}

    findings = [f for f in result.findings
                if f["id_hint"] == "edge-refuses-named-answer-crawlers"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["severity"] == "high"
    assert finding["root_cause"] == "bot-manager-block"
    assert finding["mechanism"] == "A"
    assert sorted(result.signals["edge_refuses_crawler_names"]) == sorted(REFUSED_NAMES)
    assert result.signals["edge_serves_crawler_names"]


def test_the_finding_names_the_refused_crawlers_and_only_those():
    """A list is a fix. Naming a served crawler in it sends an owner to write
    an allow rule for a name that was already being answered, which is the
    move that left a charity's three genuinely refused crawlers unnamed."""
    with _EdgeServer() as server:
        result, _, _ = _run_comparison(server)

    finding = next(f for f in result.findings
                   if f["id_hint"] == "edge-refuses-named-answer-crawlers")
    text = finding["evidence"] + " ".join(finding["suggested_action"]["how_to_fix"])
    served = result.signals["edge_serves_crawler_names"]
    for name in REFUSED_NAMES:
        assert name in finding["suggested_action"]["summary"]
        assert name in text
    for name in served:
        assert name not in finding["suggested_action"]["summary"]
    # A crawler that was answered is never described as refused. The evidence
    # names the refused set before the served set, so the clause in front of
    # the served ones may not contain any of them.
    refused_clause = finding["evidence"].split("HTTP 200 with the page to")[0]
    for name in served:
        assert name not in refused_clause


def test_the_finding_says_the_rule_is_not_in_a_file_the_owner_can_read():
    """robots.txt mentions neither refused agent, so an owner reading their own
    robots.txt sees an open site. Saying where the rule actually lives is the
    difference between a finding they can act on and one they cannot."""
    with _EdgeServer() as server:
        result, _, _ = _run_comparison(server)

    finding = next(f for f in result.findings
                   if f["id_hint"] == "edge-refuses-named-answer-crawlers")
    assert "robots.txt names" in finding["evidence"]
    assert "CDN or WAF" in finding["evidence"]


def test_a_refusal_robots_txt_also_states_is_not_called_invisible():
    """The sentence is a claim about robots.txt, so it is read from robots.txt."""
    robots = {"groups": [{"agents": ["claude-searchbot", "perplexitybot"],
                          "allow": [], "disallow": ["/"], "crawl_delay": None}]}
    assert CA._names_robots_does_not_mention(robots, list(REFUSED_NAMES)) == []
    assert CA._names_robots_does_not_mention({}, ["Applebot"]) == ["Applebot"]


def test_both_clients_refused_under_every_name_still_decides_nothing():
    """Two clients are more evidence and are not an answer. They can share an
    address, and an edge refusing this address refuses both alike - so the
    honest report is still that the cause was not established."""
    with _EdgeServer(refuse_names=CA.ANSWER_CRAWLERS, refuse_primary=True,
                     refuse_audit_name=True) as server:
        result, verdict, _ = _run_comparison(server)

    assert verdict == CA.UA_CLIENT_REFUSED
    assert not [f for f in result.findings
                if f["id_hint"] == "edge-refuses-named-answer-crawlers"]
    reason = next(entry["reason"] for entry in result.not_applicable
                  if entry["check"] == "bot-manager-user-agent-comparison")
    assert "could not be determined" in reason
    assert "second" in reason
    assert result.signals["refusal_cause_undetermined"] is True
    # And the wording never converts a uniform refusal into a claim about names.
    assert "not keyed to the user agent" not in reason


def test_a_client_the_edge_refuses_is_not_reported_as_a_crawler_block():
    """Every crawler name and this audit's own name answered through the second
    client. Nothing about a name is being read, and an owner sent to their bot
    allow list would find nothing there to change."""
    with _EdgeServer(refuse_names=(), refuse_primary=True) as server:
        result, verdict, _ = _run_comparison(server)

    assert verdict == CA.UA_CLIENT_SCORED
    assert not [f for f in result.findings
                if f["id_hint"] == "edge-refuses-named-answer-crawlers"]
    steps = " ".join(CA._refusal_steps(CA.UA_CLIENT_SCORED))
    assert "allow rule" in steps.lower()
    assert "Nothing here needs a crawler allow rule" in steps
    note = CA._refusal_cause_note(CA.UA_CLIENT_SCORED)
    assert "refuses is the HTTP client" in note
    assert "no crawler allow rule would lift it" in note


def test_the_second_client_is_a_different_client():
    """If both transports looked identical to the server, the comparison would
    be the same confounded one under a new name."""
    with _EdgeServer(refuse_names=(), refuse_primary=True) as server:
        fetcher = Fetcher(max_requests=6, delay=0)
        primary = fetcher.get(server.base_url + "/", transport=PRIMARY_TRANSPORT)
        second = fetcher.get(server.base_url + "/", transport=SECOND_TRANSPORT)

    assert primary.status_code == 403
    assert second.status_code == 200
    assert b"A page" in second.content
    assert second.headers.get("Content-Type") == "text/html; charset=utf-8"
    # Header lookup ignores case on both, because `response_text` asks for a
    # lowercase name and a server may spell it any way it likes.
    assert second.headers.get("content-type") == second.headers.get("CONTENT-TYPE")


# --------------------------------------------------------------------------
# What it costs
# --------------------------------------------------------------------------

def test_a_site_that_refuses_nobody_pays_for_none_of_this():
    """The probe is a diagnostic for a refusal, not a survey. A site whose edge
    answers this audit and answers the probe names alike never reaches it."""
    with _EdgeServer(refuse_names=(), refuse_primary=False) as server:
        result, verdict, fetcher = _run_comparison(server, status=200)

    assert verdict == CA.UA_UNTESTED
    assert result.signals.get("second_client_probe") is None
    assert not server.probes_by(False), "the second client was used on a clean site"
    assert fetcher.count <= CA.BOT_PROBE_AGENTS
    assert not result.findings


def test_the_probe_is_capped_and_fits_the_declared_budget():
    """The ceiling this skill's SKILL.md advertises has to cover the worst
    case, or the number in the file is decoration."""
    with _EdgeServer() as server:
        result, _, fetcher = _run_comparison(server)

    # Four names and one control request under this audit's own name.
    assert len(server.probes_by(False)) <= CA.SECOND_TRANSPORT_AGENTS + 1
    assert len(result.signals["second_client_probe"]["statuses"]) \
        <= CA.SECOND_TRANSPORT_AGENTS
    assert fetcher.count <= CA.MAX_EXTRA_REQUESTS


def test_the_probe_stops_when_the_budget_is_already_gone():
    """A ceiling reached by an earlier check ends this one rather than
    overrunning it. The check then says nothing, which is what a check with no
    measurement is meant to do."""
    result = SkillResult("crawl-access-audit")
    fetcher = Fetcher(max_requests=1, delay=0)
    fetcher.count = 1
    assert CA._ask_each_name_again(result, fetcher, "http://127.0.0.1:1/", {}) is None
    assert result.signals.get("second_client_probe") is None


def test_a_fetcher_with_no_second_client_is_left_alone():
    """Several checks are handed a stub rather than a `Fetcher`. A stub has no
    second transport, and asking one for a measurement it cannot make would
    turn a missing capability into a claim about a site."""

    class _StubFetcher:
        budget_left = True
        time_left = True

        def try_get(self, url, **kwargs):
            raise AssertionError("a stub must never be asked for a second client")

    result = SkillResult("crawl-access-audit")
    assert CA._ask_each_name_again(result, _StubFetcher(), "http://127.0.0.1:1/", {}) is None


# --------------------------------------------------------------------------
# The training-crawler call is a different call and does not move
# --------------------------------------------------------------------------

def test_blocking_a_training_crawler_is_still_info_and_still_deliberate(audit):
    """Blocking a training corpus is a rights decision an owner made on
    purpose, in a file they can read. Refusing an answer crawler at the edge
    is usually an accident, in a console they have not opened. The two must
    not converge on one severity."""
    result = audit("blocked-site")
    training = [f for f in result.report["findings"]
                if "training" in f["title"].lower()]
    assert training
    assert all(f["severity"] == "info" for f in training)
    assert any("often deliberate" in f["title"] for f in training)

    edge = [f for f in result.report["findings"]
            if f["root_cause"] == "bot-manager-block"
            and "answer-engine" in f["title"]]
    assert edge, "the edge block was not reported"
    assert all(f["severity"] in ("critical", "high") for f in edge)


def test_the_edge_finding_names_every_crawler_the_edge_refuses(audit):
    """Two names refused by that fixture's edge, two probed by the primary
    comparison. Reporting the one the primary comparison happened to reach
    first left the other unnamed, and an owner acting on the finding would
    have allowed half of what was blocked."""
    result = audit("blocked-site")
    signals = result.findings["crawl-access-audit"]["signals"]
    refused = signals.get("edge_refuses_crawler_names") or []
    served = signals.get("edge_serves_crawler_names") or []
    assert len(refused) >= 2
    assert served
    assert not set(refused) & set(served)

    finding = next(f for f in result.report["findings"]
                   if f["root_cause"] == "bot-manager-block")
    for name in refused:
        assert name in finding["evidence"]


# --------------------------------------------------------------------------
# Nothing written here carries a control character
# --------------------------------------------------------------------------

def test_this_marketplace_writes_no_control_characters():
    """A stray control byte in a source string reaches the report, and a
    report is read by people and diffed by machines."""
    for path in (os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
                 os.path.join(SCRIPTS, "audit_common.py"),
                 os.path.abspath(__file__)):
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        stray = sorted({ch for ch in text
                        if ord(ch) < 32 and ch not in ("\n", "\t")}
                       | {ch for ch in text if ord(ch) == 127})
        assert not stray, "{} carries {!r}".format(path, stray)


def test_the_audit_user_agent_is_still_what_the_control_request_sends():
    """The control request is the one that separates a name rule from a client
    rule, so it has to carry this audit's own name and nothing borrowed."""
    # The operator's published string first, because an edge that refuses the
    # real crawler matches on that string and not on the bare token; then this
    # audit's own name, so the request still says who sent it.
    assert CA._ua_string("Applebot").startswith(
        CA.AGENT_BY_TOKEN["applebot"].published_ua + " (")
    assert USER_AGENT in CA._ua_string("Applebot")
