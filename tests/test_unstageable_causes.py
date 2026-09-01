"""The two root causes the fixture server cannot stage, proved without it.

`tests/test_coverage.py` guarantees that every defect this marketplace can
report has a test that makes it report it. Two causes sat outside that
guarantee on an exemption list, and re-reading the exemptions found that
neither reason survived contact with the code:

  insecure-transport   The recorded reason was "needs a TLS certificate and an
                       https origin". It does not. The check reads one string -
                       whether the snapshot's origin starts with `http://` -
                       and skips loopback hosts. What the fixture server cannot
                       do is be a non-loopback host, which is a different fact
                       and is trivial to supply directly.

  entity-ambiguity     The recorded reason was "needs a real Wikidata name
                       collision". It needs a *response shaped like* one. The
                       check hands a fetcher to the Wikidata search API and
                       reads the JSON, so a stub answers it with entirely
                       invented labels and no real organisation goes anywhere
                       near the test corpus.

Both now sit in PROVED_BY_UNIT_TEST instead, and the exemption list is empty.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ORCH = os.path.join(_ROOT, "skills", "audit-orchestrator", "scripts")
sys.path.insert(0, _ORCH)

from audit_common import ROOT_CAUSES, SkillResult  # noqa: E402


def _load(skill):
    scripts = os.path.join(_ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "{}_check".format(skill.replace("-", "_")), os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


access = _load("crawl-access-audit")
freshness = _load("freshness-corroboration-audit")


def _causes(result):
    return {f["root_cause"] for f in result.findings}


# --------------------------------------------------------------------------
# insecure-transport
# --------------------------------------------------------------------------

def _snapshot(origin):
    page = {"url": origin.rstrip("/") + "/", "final_url": origin.rstrip("/") + "/",
            "status": 200, "page_type": "home", "html_len": 900, "text_len": 400,
            "links": {"internal": [], "external": []}, "jsonld": [], "headers": {}}
    return {"origin": origin, "site": "audited site", "pages": [page],
            "crawl": {"head_supported": True}}


def test_a_site_served_over_http_is_reported():
    snapshot = _snapshot("http://an-invented-host.test")
    result = SkillResult("crawl-access-audit")
    access._check_transport_and_hosts(result, snapshot, snapshot["pages"])
    assert "insecure-transport" in _causes(result)


def test_the_same_site_over_https_is_not_reported():
    snapshot = _snapshot("https://an-invented-host.test")
    result = SkillResult("crawl-access-audit")
    access._check_transport_and_hosts(result, snapshot, snapshot["pages"])
    assert "insecure-transport" not in _causes(result)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "192.168.1.10"])
def test_a_loopback_or_ip_host_is_a_deployment_detail_not_a_defect(host):
    """Why the fixture server could never stage this: it is one of these."""
    snapshot = _snapshot("http://{}:8000".format(host))
    result = SkillResult("crawl-access-audit")
    access._check_transport_and_hosts(result, snapshot, snapshot["pages"])
    assert "insecure-transport" not in _causes(result)
    assert any("local or IP-addressed host" in s["reason"] for s in result.not_applicable)


def test_the_evidence_names_the_origin_it_read():
    snapshot = _snapshot("http://an-invented-host.test")
    result = SkillResult("crawl-access-audit")
    access._check_transport_and_hosts(result, snapshot, snapshot["pages"])
    finding = next(f for f in result.findings if f["root_cause"] == "insecure-transport")
    assert "http://an-invented-host.test" in finding["evidence"]


# --------------------------------------------------------------------------
# entity-ambiguity
# --------------------------------------------------------------------------

class StubResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class StubWikidata:
    """Answers the Wikidata search API with whatever the test invented."""

    def __init__(self, labels, description="an invented thing"):
        self.payload = {"search": [
            {"id": "Q{}".format(900000 + i), "label": label, "description": description,
             "aliases": []}
            for i, label in enumerate(labels)]}
        self.calls = []
        self.count = 0

    def try_get(self, url, **kwargs):
        self.calls.append(url)
        self.count += 1
        return StubResponse(self.payload)


BRAND = "Norhaven Tessellate"


def _ambiguity(labels, profiles=None, pages=None):
    result = SkillResult("freshness-corroboration-audit")
    fetcher = StubWikidata(labels)
    freshness._check_entity_ambiguity(
        result, {"origin": "https://an-invented-host.test"}, pages or [],
        {"name": BRAND}, profiles or {}, fetcher, True)
    return result, fetcher


def test_a_name_shared_with_other_entities_is_reported():
    result, _ = _ambiguity([BRAND, BRAND, BRAND])
    assert "entity-ambiguity" in _causes(result)


def test_the_root_cause_is_in_the_shared_vocabulary():
    result, _ = _ambiguity([BRAND, BRAND])
    finding = next(f for f in result.findings if f["root_cause"] == "entity-ambiguity")
    assert finding["root_cause"] in ROOT_CAUSES


def test_a_unique_name_is_not_reported():
    result, _ = _ambiguity([BRAND])
    assert "entity-ambiguity" not in _causes(result)
    assert any("no obvious name collision" in s["reason"] for s in result.not_applicable)


def test_a_prefix_match_is_not_a_collision():
    """Wikidata matches by prefix, so a brand's own sub-brands come back too."""
    result, _ = _ambiguity([BRAND, BRAND + " One Login", BRAND + " Verify"])
    assert "entity-ambiguity" not in _causes(result)


def test_a_site_that_links_wikidata_has_already_disambiguated_itself():
    result, _ = _ambiguity(
        [BRAND, BRAND, BRAND],
        profiles={"Wikidata": "https://www.wikidata.org/wiki/Q900000"})
    assert "entity-ambiguity" not in _causes(result)
    assert any("already disambiguates itself" in s["reason"] for s in result.not_applicable)


def test_four_or_more_collisions_is_high_severity():
    few, _ = _ambiguity([BRAND, BRAND])
    many, _ = _ambiguity([BRAND] * 5)
    assert next(f for f in few.findings
                if f["root_cause"] == "entity-ambiguity")["severity"] == "medium"
    assert next(f for f in many.findings
                if f["root_cause"] == "entity-ambiguity")["severity"] == "high"


def test_only_one_lookup_is_made():
    _, fetcher = _ambiguity([BRAND, BRAND])
    assert fetcher.count == 1


def test_no_network_means_no_lookup_and_a_stated_reason():
    result = SkillResult("freshness-corroboration-audit")
    freshness._check_entity_ambiguity(
        result, {"origin": "https://an-invented-host.test"}, [],
        {"name": BRAND}, {}, None, False)
    assert "entity-ambiguity" not in _causes(result)
    assert any("disabled for this run" in s["reason"] for s in result.not_applicable)
