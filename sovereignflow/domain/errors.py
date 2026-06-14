from __future__ import annotations


class SovereignFlowError(Exception):
    code = "sovereignflow_error"
    http_status = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.safe_message = message


class ValidationError(SovereignFlowError):
    code = "validation_error"
    http_status = 400


class PolicyViolationError(SovereignFlowError):
    code = "policy_violation"
    http_status = 403


class DomainNotFoundError(SovereignFlowError):
    code = "domain_not_found"
    http_status = 404


class ProviderProtocolError(SovereignFlowError):
    code = "provider_protocol_error"
    http_status = 502


class DependencyUnavailableError(SovereignFlowError):
    code = "dependency_unavailable"
    http_status = 503


class ConfigurationError(SovereignFlowError):
    code = "configuration_error"
    http_status = 500


class PipelineDefinitionError(ConfigurationError):
    code = "pipeline_definition_error"


class PipelineExecutionError(SovereignFlowError):
    code = "pipeline_execution_error"
    http_status = 500


class ConflictError(SovereignFlowError):
    code = "conflict"
    http_status = 409


class IngestionError(SovereignFlowError):
    code = "ingestion_error"
    http_status = 500
