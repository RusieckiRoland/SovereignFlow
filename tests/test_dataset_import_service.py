from __future__ import annotations

from dataclasses import replace

import pytest

from sovereignflow.application import DatasetImportService
from sovereignflow.domain import (
    DatasetImportRequest,
    DatasetImportRun,
    DatasetImportStatus,
    DocumentChunk,
    DomainProfile,
    GraphDirection,
    GraphNodeRef,
    GraphRelationship,
    GraphTraversalProfile,
    IngestionCommand,
    IngestionJob,
    IngestionJobStatus,
    PolicyViolationError,
    RetrievalProfile,
    SearchMode,
)


def profile() -> DomainProfile:
    return DomainProfile(
        name="neutral",
        description="",
        collection="Neutral",
        tenant_id="tenant-a",
        prompt_name="answer",
        allow_external_model=False,
        retrieval=RetrievalProfile(SearchMode.HYBRID, 5, 1000),
        graph=GraphTraversalProfile(True, 2, 10, GraphDirection.BOTH),
        allowed_acl_labels=("public",),
        max_classification_level=1,
    )


def command(source_id: str, *, domain: str = "neutral") -> IngestionCommand:
    chunk_id = f"{source_id}-chunk"
    return IngestionCommand(
        idempotency_key=f"import:{source_id}:v1",
        domain=domain,
        tenant_id="tenant-a",
        source_id=source_id,
        source_version="v1",
        chunks=(
            DocumentChunk(
                chunk_id,
                domain,
                "tenant-a",
                source_id,
                "text",
                acl_labels=("public",),
                classification_level=1,
            ),
        ),
    )


class Reader:
    def __init__(self, commands, relationships=(), deletions=()) -> None:
        self.commands = commands
        self.relationships = relationships
        self.deleted = deletions
        self.prepared = []

    def prepare(self, *, domain, tenant_id):
        self.prepared.append((domain, tenant_id))
        return DatasetImportRequest(
            "import-1",
            domain,
            tenant_id,
            "a" * 64,
            len(self.commands),
            len(self.commands),
            sum(len(item.relationships) for item in self.relationships),
            len(self.deleted),
        )

    def source_commands(self):
        return iter(self.commands)

    def relationship_commands(self):
        return iter(self.relationships)

    def deletions(self):
        return iter(self.deleted)


class Repository:
    def __init__(self, *, completed=False, error=None) -> None:
        self.completed = completed
        self.error = error
        self.calls = []
        self.run = None

    def stage(self, ingestion_command, *, payload_hash):
        self.calls.append(("stage", ingestion_command))
        return IngestionJob(
            "job",
            payload_hash,
            IngestionJobStatus.STAGED,
            ingestion_command,
        )

    def mark_indexing(self, job_id):
        self.calls.append(("indexing", job_id))

    def mark_indexed(self, job_id):
        if self.error:
            raise self.error
        self.calls.append(("indexed", job_id))

    def mark_failed(self, job_id, *, error_code, error_message):
        self.calls.append(("source_failed", error_code, error_message))

    def start_import(self, request):
        self.calls.append(("start", request))
        status = DatasetImportStatus.COMPLETED if self.completed else DatasetImportStatus.STAGING
        self.run = DatasetImportRun(
            request.import_id,
            request.domain,
            request.tenant_id,
            request.dataset_hash,
            status,
            request.source_count,
            request.chunk_count,
            request.relationship_count,
            request.deletion_count,
        )
        return self.run

    def update_import(self, import_id, **values):
        self.calls.append(("update", values["status"]))
        self.run = replace(
            self.run,
            status=values["status"],
            indexed_sources=values["indexed_sources"],
            published_relationships=values["published_relationships"],
            deleted_sources=values["deleted_sources"],
        )

    def load_import(self, import_id, *, tenant_id):
        self.calls.append(("load_import", import_id, tenant_id))
        return self.run

    def fail_import(self, import_id, *, error_code, error_message):
        self.calls.append(("import_failed", error_code, error_message))

    def replace_relationships(self, ingestion_command):
        self.calls.append(("relationships", ingestion_command))

    def delete_source(self, **values):
        self.calls.append(("delete", values))

    def consistency_counts(self, **values):
        self.calls.append(("counts", values))
        return {
            "active_sources": 2,
            "active_chunks": 3,
            "active_relationships": 1,
        }


class Index:
    def __init__(self, count=3) -> None:
        self.calls = []
        self.count_value = count

    def replace_source(self, job, *, collection_name):
        self.calls.append(("replace", collection_name, job.command))

    def delete_source(self, **values):
        self.calls.append(("delete", values))

    def count(self, **values):
        self.calls.append(("count", values))
        return self.count_value


def service(repository=None, index=None):
    return DatasetImportService(
        domain=profile(),
        repository=repository or Repository(),
        vector_index=index or Index(),
    )


def test_dataset_import_executes_all_phases_and_is_observable() -> None:
    first = command("source-a")
    second = command("source-b")
    relationship = replace(
        first,
        relationships=(
            GraphRelationship(
                GraphNodeRef("source-a", "source-a-chunk"),
                GraphNodeRef("source-b", "source-b-chunk"),
                "references",
            ),
        ),
    )
    repository = Repository()
    index = Index()
    reader = Reader((first, second), (relationship,), ("source-b",))

    result = service(repository, index).execute(reader)

    assert result.status == DatasetImportStatus.COMPLETED
    assert result.indexed_sources == 2
    assert result.published_relationships == 1
    assert result.deleted_sources == 1
    assert reader.prepared == [("neutral", "tenant-a")]
    staged = [item[1] for item in repository.calls if item[0] == "stage"]
    assert all(not item.relationships for item in staged)
    assert any(item[0] == "relationships" for item in repository.calls)
    assert any(item[0] == "delete" for item in repository.calls)
    assert any(item[0] == "delete" for item in index.calls)


def test_completed_import_is_returned_without_work() -> None:
    repository = Repository(completed=True)
    reader = Reader((command("source-a"),))

    result = service(repository).execute(reader)

    assert result.status == DatasetImportStatus.COMPLETED
    assert [item[0] for item in repository.calls] == ["start"]


def test_import_failure_is_recorded_and_rethrown() -> None:
    repository = Repository(error=RuntimeError("secret"))

    with pytest.raises(RuntimeError, match="secret"):
        service(repository).execute(Reader((command("source-a"),)))

    assert ("import_failed", "internal_error", "Unhandled dataset import failure") in (
        repository.calls
    )


def test_import_rejects_domain_boundary_and_records_policy_error() -> None:
    repository = Repository()

    with pytest.raises(PolicyViolationError):
        service(repository).execute(Reader((command("source-a", domain="other"),)))

    assert any(item[:2] == ("import_failed", "policy_violation") for item in repository.calls)


def test_status_and_consistency_report() -> None:
    repository = Repository(completed=True)
    repository.start_import(
        DatasetImportRequest("import-1", "neutral", "tenant-a", "a" * 64, 1, 1, 0, 0)
    )
    index = Index(count=3)
    selected = service(repository, index)

    assert selected.status("import-1").import_id == "import-1"
    report = selected.consistency()

    assert report.consistent is True
    assert report.active_sources == 2
    assert report.active_relationships == 1
