"""Triggering and inspecting an analysis through the API — job queue, DB persistence and progress all
wired together. The queue runs in "eager" mode (see app/jobs/queue.py) so the POST that triggers an
analysis only returns once it has finished, keeping these tests deterministic with no polling or sleeps.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.schemas import Plan, Proposals, Summary
from app.api.main import build_app
from app.engine.catalog import Catalog
from app.settings import Settings
from tests.api_seed import seed_document
from tests.builders import load_fixture
from tests.fake_gateway import FakeGateway


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def document_id(database_url: str) -> str:
    return seed_document(database_url, load_fixture("annual_report_manufacturing"))


def test_rules_only_analysis_runs_end_to_end_and_persists_findings(
    database_url: str, document_id: str, catalog: Catalog
) -> None:
    settings = Settings(database_url=database_url)
    app = build_app(settings, catalog=catalog, kb=None, gateway=None, eager_jobs=True)
    with TestClient(app) as client:
        triggered = client.post(f"/documents/{document_id}/analyses")
        assert triggered.status_code == 202, triggered.text
        analysis_id = triggered.json()["id"]
        assert triggered.json()["status"] == "succeeded"  # eager mode: finished before the response returns

        detail = client.get(f"/analyses/{analysis_id}").json()
        assert detail["report"]["provenance"]["mode"] == "rules_only"
        assert detail["report"]["findings"], "the manufacturing fixture is expected to fire rules"

        findings = client.get(f"/analyses/{analysis_id}/findings").json()
        assert findings and all(f["origin"] == "rule" for f in findings)
        assert {f["finding_id"] for f in findings} == {f["id"] for f in detail["report"]["findings"]}


def test_agent_mode_analysis_uses_the_scripted_gateway(
    database_url: str, document_id: str, catalog: Catalog
) -> None:
    gateway = FakeGateway(
        script={
            "plan": [Plan(company_context="", enquiries=[], immaterial_rules=[])],
            "compose": [
                Summary(
                    headline="Nothing further to add beyond the rule library",
                    overall_risk="low",
                    executive_summary="No agent-authored findings this run; see the fired rules.",
                    strengths=[],
                    concerns=[],
                )
            ],
            "propose": [Proposals(rules=[])],
        }
    )
    settings = Settings(database_url=database_url)
    app = build_app(settings, catalog=catalog, kb=None, gateway=gateway, eager_jobs=True)
    with TestClient(app) as client:
        triggered = client.post(f"/documents/{document_id}/analyses")
        assert triggered.json()["status"] == "succeeded", triggered.json()
        analysis_id = triggered.json()["id"]

        report = client.get(f"/analyses/{analysis_id}").json()["report"]
        assert report["provenance"]["mode"] == "agent"
        assert report["headline"] == "Nothing further to add beyond the rule library"
        # the rules the (empty) plan didn't cover still surface as rule-origin findings
        assert report["findings"]


def _app(database_url: str, catalog: Catalog):
    settings = Settings(database_url=database_url)
    return build_app(settings, catalog=catalog, kb=None, gateway=None, eager_jobs=True)


def test_trigger_analysis_for_unknown_document_is_404(database_url: str, catalog: Catalog) -> None:
    with TestClient(_app(database_url, catalog)) as client:
        assert client.post("/documents/does-not-exist/analyses").status_code == 404


def test_get_unknown_analysis_is_404(database_url: str, catalog: Catalog) -> None:
    with TestClient(_app(database_url, catalog)) as client:
        assert client.get("/analyses/does-not-exist").status_code == 404
