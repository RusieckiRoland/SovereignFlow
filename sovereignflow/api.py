from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Flask, jsonify, request

from .models import QueryRequest
from .rag import RAGService


def create_app(services: Mapping[str, RAGService]) -> Flask:
    app = Flask(__name__)
    registry = dict(services)

    @app.get("/health")
    def health() -> Any:
        return jsonify({"ok": True, "domains": sorted(registry)})

    @app.post("/v1/query")
    def query() -> Any:
        payload = request.get_json(silent=True) or {}
        domain = str(payload.get("domain") or "").strip()
        service = registry.get(domain)
        if service is None:
            return jsonify({"ok": False, "error": f"Unknown domain: {domain}"}), 404

        try:
            result = service.query(
                QueryRequest(
                    query=str(payload.get("query") or ""),
                    domain=domain,
                    session_id=str(payload.get("session_id") or ""),
                    tenant_id=str(payload.get("tenant_id") or "default"),
                    user_id=str(payload.get("user_id")) if payload.get("user_id") else None,
                    locale=str(payload.get("locale") or "en"),
                    filters=dict(payload.get("filters") or {}),
                    allowed_acl_labels=tuple(payload.get("allowed_acl_labels") or ()),
                    max_classification_level=payload.get("max_classification_level"),
                )
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        return jsonify(
            {
                "ok": True,
                "answer": result.answer,
                "domain": result.domain,
                "session_id": result.session_id,
                "citations": [
                    {
                        "source_id": citation.source_id,
                        "chunk_id": citation.chunk_id,
                        "source_uri": citation.source_uri,
                        "score": citation.score,
                        "metadata": citation.metadata,
                    }
                    for citation in result.citations
                ],
                "pipeline_trace": list(result.pipeline_trace),
            }
        )

    return app

