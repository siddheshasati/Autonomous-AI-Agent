"""Smoke tests for the assignment requirements.

Run after installing requirements:
    python smoke_test.py
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from main import app


TEST_CASES = [
    {
        "name": "standard business request",
        "request": "Write a project proposal for migrating our on-prem data warehouse to the cloud",
        "expected_type": "project proposal",
        "expected_sections": {"Executive Summary", "Objectives", "Next Steps"},
    },
    {
        "name": "complex ambiguous request",
        "request": (
            "We need something for the new AI feature launch, leadership wants it by Friday "
            "but also wants it to cover both technical rollout and the go-to-market side, "
            "not sure who owns what yet"
        ),
        "expected_type": "cross-functional launch plan",
        "expected_sections": {"Technical Rollout Plan", "Go-To-Market Plan", "Ownership & Open Decisions"},
    },
    {
        "name": "meeting notes summary",
        "request": """Summarize the meeting notes below and provide:
1. Key Decisions
2. Action Items
3. Next Steps

Meeting Notes:
- Product launch approved for August.
- Marketing campaign starts July 15.
- Engineering to complete testing by July 20.""",
        "expected_type": "meeting summary",
        "expected_sections": {"Key Decisions", "Action Items", "Next Steps"},
    },
]


def assert_agent_case(client: TestClient, test_case: dict) -> None:
    response = client.post("/agent", json={"request": test_case["request"]})
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["document_type"] == test_case["expected_type"], data
    assert data["download_url"].startswith("/agent/"), data

    section_names = {section["heading"] for section in data["sections"]}
    missing_sections = test_case["expected_sections"] - section_names
    assert not missing_sections, f"Missing sections for {test_case['name']}: {missing_sections}"

    download = client.get(data["download_url"])
    assert download.status_code == 200, download.text
    assert download.content.startswith(b"PK"), "DOCX download is not a zip/docx file"

    print(f"{test_case['name']}: {data['message']} download={len(download.content)} bytes")


def main() -> None:
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200, health.text
    print("health:", health.json())

    for test_case in TEST_CASES:
        assert_agent_case(client, test_case)

    generated_dir = os.path.join(os.path.dirname(__file__), "generated_documents")
    print("generated_documents:", generated_dir)
    print("smoke test passed")


if __name__ == "__main__":
    main()
