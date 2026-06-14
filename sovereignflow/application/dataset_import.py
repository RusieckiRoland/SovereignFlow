from __future__ import annotations

from dataclasses import replace

from sovereignflow.domain import (
    DatasetConsistencyReport,
    DatasetImportRun,
    DatasetImportStatus,
    DomainProfile,
    IngestionCommand,
    PolicyViolationError,
    SovereignFlowError,
)

from .ingestion import DocumentIngestionService
from .ports import DatasetReaderPort, IngestionRepositoryPort, VectorIndexPort


class DatasetImportService:
    def __init__(
        self,
        *,
        domain: DomainProfile,
        repository: IngestionRepositoryPort,
        vector_index: VectorIndexPort,
    ) -> None:
        self._domain = domain
        self._repository = repository
        self._vector_index = vector_index
        self._documents = DocumentIngestionService(
            domain=domain,
            repository=repository,
            vector_index=vector_index,
        )

    def execute(self, reader: DatasetReaderPort) -> DatasetImportRun:
        request = reader.prepare(
            domain=self._domain.name,
            tenant_id=self._domain.tenant_id,
        )
        run = self._repository.start_import(request)
        if run.status == DatasetImportStatus.COMPLETED:
            return run
        indexed_sources = 0
        published_relationships = 0
        deleted_sources = 0
        try:
            for command in reader.source_commands():
                self._verify_boundary(command)
                self._documents.ingest(replace(command, relationships=()))
                indexed_sources += 1
                self._repository.update_import(
                    request.import_id,
                    status=DatasetImportStatus.STAGING,
                    indexed_sources=indexed_sources,
                    published_relationships=published_relationships,
                    deleted_sources=deleted_sources,
                )
            self._repository.update_import(
                request.import_id,
                status=DatasetImportStatus.RELATING,
                indexed_sources=indexed_sources,
                published_relationships=published_relationships,
                deleted_sources=deleted_sources,
            )
            for command in reader.relationship_commands():
                self._verify_boundary(command)
                self._repository.replace_relationships(command)
                published_relationships += len(command.relationships)
                self._repository.update_import(
                    request.import_id,
                    status=DatasetImportStatus.RELATING,
                    indexed_sources=indexed_sources,
                    published_relationships=published_relationships,
                    deleted_sources=deleted_sources,
                )
            self._repository.update_import(
                request.import_id,
                status=DatasetImportStatus.DELETING,
                indexed_sources=indexed_sources,
                published_relationships=published_relationships,
                deleted_sources=deleted_sources,
            )
            for source_id in reader.deletions():
                self._vector_index.delete_source(
                    collection_name=self._domain.collection,
                    domain=self._domain.name,
                    tenant_id=self._domain.tenant_id,
                    source_id=source_id,
                )
                self._repository.delete_source(
                    domain=self._domain.name,
                    tenant_id=self._domain.tenant_id,
                    source_id=source_id,
                )
                deleted_sources += 1
                self._repository.update_import(
                    request.import_id,
                    status=DatasetImportStatus.DELETING,
                    indexed_sources=indexed_sources,
                    published_relationships=published_relationships,
                    deleted_sources=deleted_sources,
                )
            self._repository.update_import(
                request.import_id,
                status=DatasetImportStatus.COMPLETED,
                indexed_sources=indexed_sources,
                published_relationships=published_relationships,
                deleted_sources=deleted_sources,
            )
        except Exception as exc:
            error_code = exc.code if isinstance(exc, SovereignFlowError) else "internal_error"
            error_message = (
                exc.safe_message
                if isinstance(exc, SovereignFlowError)
                else "Unhandled dataset import failure"
            )
            self._repository.fail_import(
                request.import_id,
                error_code=error_code,
                error_message=error_message,
            )
            raise
        return self._repository.load_import(
            request.import_id,
            tenant_id=request.tenant_id,
        )

    def status(self, import_id: str) -> DatasetImportRun:
        return self._repository.load_import(
            import_id,
            tenant_id=self._domain.tenant_id,
        )

    def consistency(self) -> DatasetConsistencyReport:
        counts = self._repository.consistency_counts(
            domain=self._domain.name,
            tenant_id=self._domain.tenant_id,
        )
        indexed_chunks = self._vector_index.count(
            collection_name=self._domain.collection,
            domain=self._domain.name,
            tenant_id=self._domain.tenant_id,
        )
        return DatasetConsistencyReport(
            domain=self._domain.name,
            tenant_id=self._domain.tenant_id,
            active_sources=counts["active_sources"],
            active_chunks=counts["active_chunks"],
            indexed_chunks=indexed_chunks,
            active_relationships=counts["active_relationships"],
        )

    def _verify_boundary(self, command: IngestionCommand) -> None:
        if command.domain != self._domain.name or command.tenant_id != self._domain.tenant_id:
            raise PolicyViolationError("Dataset import crossed a domain or tenant boundary")
