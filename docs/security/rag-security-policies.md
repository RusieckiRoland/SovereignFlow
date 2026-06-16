# SovereignFlow RAG Security Policies

## Purpose

This document defines the security policy model for SovereignFlow as a neutral RAG backend.

The goal is to make authorization, retrieval filtering, graph traversal, and model transmission explicit, testable, and independent of any single business domain.

SovereignFlow must never rely on hidden fallbacks, implicit trust, or user-supplied security context.

## 1. Pipeline Access Policy

Concrete pipelines are exposed to users through the identity and access model.

The flow is:

```text
Identity Provider user
-> Identity Provider claims/groups
-> SovereignFlow application groups
-> granted capabilities
-> available pipelines
```

A user must see and execute only the pipelines assigned to their resolved application groups.

Security rules:

- pipeline access is derived from the authenticated identity provider token;
- request bodies must not grant or override pipeline access;
- identity provider groups are mapped to application groups;
- application groups are mapped to capabilities;
- capabilities expose concrete domain and pipeline combinations;
- unauthorized pipelines must not appear in the catalog;
- unauthorized pipelines must not be executable even if a user guesses their identifier.

## 2. Retrieval ACL Policy

Each data record may define ACL labels, for example:

```json
["finance", "developers", "analyst"]
```

ACL labels use OR semantics.

A user can read a record when:

```text
record ACL is empty
OR
user ACL labels intersect record ACL labels
```

Examples:

| User ACL labels | Record ACL labels | Visible |
|---|---:|---:|
| `[]` | `[]` | yes |
| `[]` | `["finance"]` | no |
| `["finance"]` | `[]` | yes |
| `["finance"]` | `["finance"]` | yes |
| `["finance"]` | `["finance", "developers"]` | yes |
| `["finance"]` | `["developers"]` | no |
| `["finance"]` | `["developers", "analyst"]` | no |

Security rules:

- ACL checks must be applied during retrieval;
- ACL checks must also be applied to graph-expanded nodes;
- records with empty ACL are public within the tenant and domain boundary;
- ACL labels do not replace the configured security model;
- ACL labels are additive filters, not a source of user permissions.

## 3. Security Model Policy

SovereignFlow must support one selected security model per protected dataset or domain profile:

```yaml
security_model:
  kind: none
```

Supported values:

- `none`
- `clearance_level`
- `classification_labels`

Only one model is active for a given protected scope.

## 3.1 `none`

No clearance or classification-label checks are applied.

ACL, tenant, domain, pipeline, graph traversal, and model-transmission policies still apply.

## 3.2 `clearance_level`

The configuration defines a label universe and numeric clearance values.

Example:

```yaml
security_model:
  kind: clearance_level
  levels:
    PUBLIC: 0
    LIMITE: 10
    EU_RESTRICTED: 20
    EU_CONFIDENTIAL: 30
    EU_SECRET: 40
    EU_TOP_SECRET: 50
```

A user can read a document when:

```text
user clearance value >= document clearance value
```

Security rules:

- every protected document clearance label must exist in the configured `levels`;
- every user clearance label must exist in the configured `levels`;
- unknown clearance labels are configuration or ingestion errors;
- numeric comparison must use configured values only;
- request bodies must not provide clearance values.

## 3.3 `classification_labels`

The configuration defines the allowed classification-label universe.

The examples below use normalized technical identifiers inspired by real dissemination-control markings. They are not a complete or authoritative classification taxonomy.

Example:

```yaml
security_model:
  kind: classification_labels
  labels_universe_subset:
    - US_NOFORN
    - US_ORCON
    - US_REL_TO_FVEY
    - NATO_ATOMAL
    - EU_LIMITE
```

A user can read a document when:

```text
document classification labels are a subset of user classification labels
```

Example:

```text
document: ["US_NOFORN", "US_ORCON"]
user:     ["US_NOFORN", "US_ORCON", "US_REL_TO_FVEY"]
result:   allowed
```

```text
document: ["US_NOFORN", "US_ORCON"]
user:     ["US_NOFORN"]
result:   denied
```

Security rules:

- every document classification label must be listed in `labels_universe_subset`;
- every user classification label must be listed in `labels_universe_subset`;
- unknown labels are configuration, identity, or ingestion errors;
- classification labels use AND/subset semantics;
- classification labels do not use ACL OR semantics.

## 4. Model Server Access Policy

Model servers must have explicit trust and security permissions.

SovereignFlow distinguishes between:

- local model servers with higher trust;
- external model servers with lower trust.

The system must define an explicit list of model servers that may be used by pipelines.

Prefer an enum-like field over a boolean flag:

```yaml
model_servers:
  - id: local-secure
    trust_boundary: internal
    provider: openai-compatible
    base_url: http://localhost:11434/v1
    security_profile:
      security_model:
        kind: clearance_level
      clearance_level: EU_TOP_SECRET
    security_reroute_server_id: null

  - id: external-standard
    trust_boundary: external
    provider: openai
    security_profile:
      security_model:
        kind: clearance_level
      clearance_level: PUBLIC
    security_reroute_server_id: local-secure
```

`trust_boundary: internal | external` is preferred over `internal: true` because it is explicit, readable, and leaves room for future values such as `partner`, `regulated`, or `air_gapped` without changing the schema shape.

A model server may receive retrieval context only when the server security permissions are equal to or higher than the combined permissions required by all retrieved context sent to that server.

The model server security requirement is computed from the active `security_model`, not from ACL labels.

ACL labels are an access filter for deciding which records a user can retrieve. ACL labels must not be used to determine the security level of a model server.

When:

```yaml
security_model:
  kind: clearance_level
```

the external model server must declare a `clearance_level` equal to or higher than the highest clearance level present in the retrieval context.

Example:

```text
retrieval context levels: PUBLIC, EU_CONFIDENTIAL, EU_SECRET
required server level:   EU_SECRET
```

When:

```yaml
security_model:
  kind: classification_labels
```

the external model server must declare all classification labels that appear in the retrieval context.

Example:

```text
retrieval context labels: US_NOFORN, US_ORCON
required server labels:   US_NOFORN, US_ORCON
```

The effective retrieval security requirement is computed from:

- clearance level or classification labels present in selected retrieval context;
- tenant and domain boundaries;
- pipeline-specific model-transmission restrictions.

Security rules:

- user permission to see data is not enough to send that data to a model server;
- model server permission must be evaluated independently;
- ACL labels must not determine model server level;
- external model servers may have lower trust than local model servers;
- if a model server does not satisfy the retrieval context requirement, the model call must be blocked or explicitly routed to a permitted local model;
- routing to a local model must be explicit policy, not an automatic fallback;
- failure of a local model must never silently fallback to an external model.

## 4.1 Security Reroute Policy

The system may define an explicit security reroute for a model server.

This must not be called or implemented as a fallback.

A security reroute is a deterministic policy transition used only when the initially selected model server is not permitted to receive the selected retrieval context.

Example:

```yaml
model_servers:
  - id: external-standard
    trust_boundary: external
    security_profile:
      security_model:
        kind: classification_labels
      classification_labels:
        - US_REL_TO_FVEY
    security_reroute_server_id: local-secure

  - id: local-secure
    trust_boundary: internal
    security_profile:
      security_model:
        kind: classification_labels
      classification_labels:
        - US_NOFORN
        - US_ORCON
        - US_REL_TO_FVEY
```

Execution rule:

```text
selected model server is not permitted
-> check explicit security_reroute_server_id
-> if reroute server is permitted, use it
-> otherwise return a business error
```

Security rules:

- reroute must be explicitly configured per model server or per pipeline policy;
- reroute must be evaluated before any model call;
- reroute target must satisfy the same security checks as any other server;
- reroute target must not be selected dynamically by the LLM;
- reroute must be visible in diagnostics and audit;
- missing reroute target means the request is blocked;
- reroute target that also fails security means the request is blocked;
- this mechanism must never hide provider failures, network failures, prompt errors, or runtime errors.

Recommended business error:

```text
model_server_not_permitted_for_context
```

The error means:

```text
The system has retrieved context that the selected model server is not allowed to receive, and no permitted security reroute exists.
```

## 5. Graph Traversal Policy

Graph expansion must use the `TravelPermission = true` principle.

When graph traversal reaches a candidate node, that node can be attached to retrieval only if it satisfies all active security requirements for the authenticated user and request.

The checks include:

- tenant boundary;
- domain boundary;
- ACL labels;
- active security model;
- graph traversal limits;
- relationship-type policy.

If a candidate node does not satisfy the policy:

```text
do not include the node
and
stop traversal through that node
```

Security rules:

- graph traversal must not bypass retrieval security;
- forbidden nodes must not be included as evidence;
- forbidden nodes must not be used as bridge nodes to reach other data;
- traversal must fail closed on unknown labels or unsupported security metadata;
- traversal diagnostics may report blocked counts, but must not leak forbidden content.

## 6. Pipeline External Transmission Ban

A pipeline may explicitly forbid sending retrieved context to model servers marked as external.

This policy is independent from user permissions and model-server clearance.

If a pipeline declares external transmission as forbidden:

```text
external model call is blocked
```

even when:

- the user can read all retrieved records;
- the external model server would otherwise satisfy ACL or classification checks;
- the retrieved data has low classification.

Security rules:

- pipeline-level external transmission ban has priority over model selection;
- users cannot override the ban in the request body;
- hidden fallback to external models is forbidden;
- if local routing is supported, it must be explicitly configured and visible in diagnostics.

## 7. Required Evaluation Order

The recommended evaluation order is:

1. authenticate user through the configured identity provider;
2. map identity provider claims/groups to application groups;
3. resolve available capabilities and pipelines;
4. authorize selected capability/pipeline;
5. execute retrieval with tenant, domain, ACL, and active security model filters;
6. expand graph only through nodes with `TravelPermission = true`;
7. build context only from allowed retrieval and graph nodes;
8. evaluate model-server access against the selected context;
9. apply explicit security reroute if configured and required;
10. apply pipeline-level external transmission policy;
11. call the selected model only if all checks passed;
12. expose safe diagnostics without leaking forbidden content.

## 8. Observability Requirements

Diagnostics and audit logs should expose safe policy outcomes:

- resolved capability and pipeline;
- retrieval mode;
- number of retrieved records;
- number of graph-expanded records;
- context chunk identifiers that were actually sent to the model;
- model scope: `local` or `external`;
- selected model server id;
- final model server id after security reroute, if reroute happened;
- model transmission decision;
- reason code for blocked model transmission;
- blocked chunk identifiers when safe;
- blocked ACL or classification labels when safe;
- no forbidden text content.

## 9. Non-Negotiable Rules

- No hidden fallback from local model failure to external model.
- No request-body override for user permissions.
- No graph traversal through forbidden nodes.
- No external model call before model-transmission policy is evaluated.
- No unknown security labels accepted silently.
- No mixing ACL OR semantics with classification-label AND semantics.
- No domain-specific assumptions inside the neutral SovereignFlow core.
