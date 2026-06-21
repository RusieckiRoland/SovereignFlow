"""
Ingests the RCIDG regulation demo dataset into SovereignFlow.

Usage:
    python demo/scripts/ingest.py --url http://localhost:8000 --token <jwt>

The script reads each article from demo/documents/, splits it into chunks (one per article),
sets the correct ACL labels, and submits an IngestionCommand for each document.
It then publishes the cross-reference graph defined in demo/relationships.json.

Requires: requests
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import requests

DEMO_ROOT = Path(__file__).parent.parent
DOCUMENTS_DIR = DEMO_ROOT / "documents"
RELATIONSHIPS_FILE = DEMO_ROOT / "relationships.json"

DOMAIN = "regulations"
TENANT_ID = "tenant_demo"
SOURCE_VERSION = "v1"


def parse_acl_labels(content: str) -> list[str]:
    match = re.search(r"\*acl_labels:\s*(.+)\*", content)
    if not match:
        return ["public"]
    return [label.strip() for label in match.group(1).split(",")]


def clean_text(content: str) -> str:
    lines = content.splitlines()
    skip = {"*Classification:", "*acl_labels:", "*References:"}
    cleaned = [line for line in lines if not any(line.strip().startswith(p) for p in skip)]
    return "\n".join(cleaned).strip()


def load_documents() -> list[dict]:
    docs = []
    for path in sorted(DOCUMENTS_DIR.glob("art_*.md")):
        content = path.read_text(encoding="utf-8")
        source_id = path.stem
        acl_labels = parse_acl_labels(content)
        text = clean_text(content)
        payload_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        docs.append({
            "source_id": source_id,
            "chunk_id": source_id.split("_", 1)[1],
            "acl_labels": acl_labels,
            "text": text,
            "payload_hash": payload_hash,
            "source_uri": f"rcidg://{source_id}",
        })
    return docs


def ingest_document(base_url: str, token: str, doc: dict) -> None:
    chunk = {
        "chunk_id": doc["chunk_id"],
        "text": doc["text"],
        "source_uri": doc["source_uri"],
        "metadata": {},
        "acl_labels": doc["acl_labels"],
        "clearance_label": None,
        "classification_labels": [],
    }
    payload = {
        "source_id": doc["source_id"],
        "source_version": SOURCE_VERSION,
        "source_uri": doc["source_uri"],
        "idempotency_key": f"{doc['source_id']}:{SOURCE_VERSION}",
        "metadata": {"regulation": "RCIDG"},
        "chunks": [chunk],
        "relationships": [],
    }
    resp = requests.post(
        f"{base_url}/domains/{DOMAIN}/ingestion",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    job = resp.json()
    print(f"  staged {doc['source_id']} → job_id={job['job_id']}")
    index_resp = requests.post(
        f"{base_url}/domains/{DOMAIN}/ingestion/{job['job_id']}/index",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    index_resp.raise_for_status()
    print(f"  indexed {doc['source_id']}")


def publish_relationships(base_url: str, token: str, docs: list[dict]) -> None:
    with RELATIONSHIPS_FILE.open() as f:
        graph_data = json.load(f)

    by_owner: dict[str, list[dict]] = {}
    for edge in graph_data["edges"]:
        owner = edge["from_source"]
        by_owner.setdefault(owner, []).append(edge)

    for source_id, edges in by_owner.items():
        relationships = [
            {
                "from_node": {"source_id": e["from_source"], "chunk_id": e["from_chunk"]},
                "to_node": {"source_id": e["to_source"], "chunk_id": e["to_chunk"]},
                "relationship_type": e["type"],
                "metadata": {},
            }
            for e in edges
        ]
        resp = requests.post(
            f"{base_url}/domains/{DOMAIN}/ingestion/{source_id}/relationships",
            json={"source_version": SOURCE_VERSION, "relationships": relationships},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code == 404:
            print(f"  skip relationships for {source_id} (endpoint not found — check SF version)")
        else:
            resp.raise_for_status()
            print(f"  published relationships for {source_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest RCIDG demo dataset into SovereignFlow")
    parser.add_argument("--url", default="http://localhost:8000", help="SovereignFlow base URL")
    parser.add_argument("--token", required=True, help="JWT access token")
    args = parser.parse_args()

    print("Loading documents...")
    docs = load_documents()
    print(f"Found {len(docs)} articles")

    print("\nIngesting documents...")
    for doc in docs:
        ingest_document(args.url, args.token, doc)

    print("\nPublishing graph relationships...")
    publish_relationships(args.url, args.token, docs)

    print(f"\nDone. {len(docs)} articles ingested into domain '{DOMAIN}'.")


if __name__ == "__main__":
    main()
