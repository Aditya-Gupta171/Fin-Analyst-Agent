"""Listing candidate rules and recording a human approve/reject decision on one.

Producing a genuine agent-proposed candidate requires an anomaly or an uncovered finding to trigger the
rule-proposer node (see app/agent/graph.py:_propose) — orthogonal to what this endpoint does with a
candidate once it exists, and already covered by the proposer's own tests in test_agent_graph.py. So the
candidate here is seeded directly (tests/api_seed.py), keeping this test focused on the API/DB surface.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import build_app
from app.engine.catalog import Catalog
from app.settings import Settings
from tests.api_seed import seed_analysis, seed_document, seed_rule_candidate
from tests.builders import load_fixture


@pytest.fixture
def client_and_candidate(tmp_path: Path, catalog: Catalog) -> Iterator[tuple[TestClient, str]]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    document_id = seed_document(database_url, load_fixture("annual_report_manufacturing"))
    analysis_id = seed_analysis(database_url, document_id)
    candidate_id = seed_rule_candidate(database_url, analysis_id)
    settings = Settings(database_url=database_url)
    app = build_app(settings, catalog=catalog, kb=None, gateway=None, eager_jobs=True)
    with TestClient(app) as client:
        yield client, candidate_id


def test_list_candidates_returns_the_seeded_one(client_and_candidate: tuple[TestClient, str]) -> None:
    client, candidate_id = client_and_candidate
    response = client.get("/rules/candidates")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == candidate_id
    assert body[0]["status"] == "candidate"
    assert body[0]["definition"]["when"] == "revenue_from_operations > 0"


def test_list_candidates_filters_by_status(client_and_candidate: tuple[TestClient, str]) -> None:
    client, _ = client_and_candidate
    assert client.get("/rules/candidates", params={"status_filter": "approved"}).json() == []
    assert len(client.get("/rules/candidates", params={"status_filter": "candidate"}).json()) == 1


def test_approving_a_candidate_records_the_decision(client_and_candidate: tuple[TestClient, str]) -> None:
    client, candidate_id = client_and_candidate
    response = client.post(
        f"/rules/candidates/{candidate_id}/decision",
        json={"decision": "approve", "decided_by": "lead_analyst"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["decided_by"] == "lead_analyst"
    assert body["decided_at"] is not None


def test_rejecting_an_unknown_candidate_is_404(client_and_candidate: tuple[TestClient, str]) -> None:
    client, _ = client_and_candidate
    response = client.post("/rules/candidates/does-not-exist/decision", json={"decision": "reject"})
    assert response.status_code == 404
