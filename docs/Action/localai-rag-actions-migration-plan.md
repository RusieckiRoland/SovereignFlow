# LocalAI-RAG Actions Migration Plan for SovereignFlow

## Purpose

This document defines how SovereignFlow will migrate the useful action model from
LocalAI-RAG without importing code-domain assumptions.

The migration rule is strict:

1. document the action contract first;
2. implement the action only after the contract is documented;
3. preserve the clean architecture boundary;
4. keep SovereignFlow domain-neutral;
5. reject hidden fallbacks, implicit behavior, and hard-coded prompts.
6. remove defective legacy behavior before introducing the replacement.

## No Legacy Fallback Rule

There is a complete ban on fallback execution paths to the existing defective
action code.

If an existing SovereignFlow action violates the target action contract, the
migration must not preserve it as a compatibility path, fallback branch, hidden
default, or temporary runtime option.

The required order is:

1. document the correct action contract;
2. identify the defective existing behavior;
3. remove the defective implementation path;
4. remove or rewrite tests that assert the defective behavior;
5. implement the new contract cleanly;
6. add new tests for the new contract;
7. run full validation.

For example, the current `call_model` implementation must not keep a fallback to
`domain.prompt_name` and hard-coded prompt assembly after the YAML-driven
`prompt_key` / `user_parts` contract is introduced.

The old tests that verify hard-coded prompt assembly must be deleted or rewritten
to verify the new YAML-driven behavior. Keeping both behaviors would hide design
debt and make future action migration ambiguous.

The current SovereignFlow implementation works as a proof of architecture, but
its action contract is still too narrow. In particular, `call_model` currently
loads the system prompt from `domain.prompt_name` and builds the user prompt in
Python code. That is not compatible with the LocalAI-RAG action model.

In LocalAI-RAG, action behavior is driven by the pipeline step YAML. For example,
`call_model` reads `prompt_key`, `user_parts`, optional model parameters, and the
next transition from the step configuration. SovereignFlow must move in that
direction before additional actions are migrated.

## Sources Reviewed

The migration plan is based on the LocalAI-RAG documentation and implementation:

- `LocalAI-RAG/docs/actions/call_model_action.md`
- `LocalAI-RAG/docs/actions/search_nodes_action.md`
- `LocalAI-RAG/docs/actions/fetch_node_texts_action.md`
- `LocalAI-RAG/docs/actions/manage_context_budget_action.md`
- `LocalAI-RAG/docs/actions/set_variables_action.md`
- `LocalAI-RAG/docs/actions/prefix_router_action.md`
- `LocalAI-RAG/docs/actions/json_decision_router_action.md`
- `LocalAI-RAG/docs/actions/load_conversation_history_action.md`
- `LocalAI-RAG/docs/actions/loop_guard_action.md`
- `LocalAI-RAG/docs/actions/repeat_query_guard_action.md`
- `LocalAI-RAG/docs/actions/finalize_action.md`
- `LocalAI-RAG/code_query_engine/pipeline/actions/base_action.py`
- `LocalAI-RAG/code_query_engine/pipeline/actions/call_model.py`
- `LocalAI-RAG/code_query_engine/pipeline/action_registry.py`
- `LocalAI-RAG/code_query_engine/pipeline/engine.py`
- `LocalAI-RAG/code_query_engine/pipeline/state.py`

## Core Rule From LocalAI-RAG

An action is not only an action name.

An action is:

```text
step id
+ action name
+ action version
+ step-local configuration
+ state inputs
+ state outputs
+ routing contract
+ trace/audit contract
```

The YAML step must be allowed to configure the action.

For `call_model`, the correct shape is closer to:

```yaml
- id: call_model_answer
  action: call_model
  action_version: "1.0"
  prompt_key: "general/answer_v1"
  user_parts:
    evidence:
      source: evidence
      template: "### Evidence:\n{}\n\n"
    user_question:
      source: normalized_query
      template: "### User:\n{}\n\n"
  max_output_tokens: 512
  temperature: 0.0
  next: finalize
```

The pipeline owns how the prompt is assembled. The domain profile may select
which pipelines are available, but it must not hide the prompt contract inside
application code.

## What Must Not Be Migrated Directly

Some LocalAI-RAG actions are valuable, but their current implementation is tied
to code analysis. These parts must be generalized before migration:

| LocalAI-RAG concept | SovereignFlow decision |
|---|---|
| repository, branch, snapshot | Replace with neutral `tenant_id`, `domain`, `source_id`, `source_version`. |
| Roslyn symbols and SQL objects | Replace with neutral document chunks and graph nodes. |
| dependency-tree naming | Generalize as graph traversal. |
| node IDs tied to code indexer | Use `chunk_id`, `source_id`, and graph relationships. |
| code-oriented prompts | Do not migrate as defaults; only use as examples. |
| language-specific compaction rules for SQL/.NET | Move only after they become neutral content policies. |
| command links for code UI | Keep out until a neutral command contract exists. |
| translation best-effort fallback | Do not migrate as fallback; only as explicit policy. |

## Target Action Documentation Folder

All action documentation for SovereignFlow lives under:

```text
docs/Action/
```

Before implementing or changing an action, add or update a document named:

```text
docs/Action/<action_name>.md
```

Each action document must include:

1. purpose;
2. domain-neutrality decision;
3. YAML schema;
4. required state inputs;
5. state outputs;
6. routing behavior;
7. fail-fast validation rules;
8. security rules;
9. observability/audit fields;
10. unit tests;
11. integration tests;
12. examples.

## Five-Step Migration Plan

## Step 1 — Action Contract Foundation

### Goal

Make SovereignFlow capable of supporting LocalAI-RAG-style actions without yet
migrating every action.

### What to migrate

From LocalAI-RAG:

- the principle that each action receives the full step definition;
- action-local YAML configuration;
- fail-fast validation of step configuration;
- structured action identity;
- action-level trace inputs and outputs;
- routing by returned next step or configured transition.

### What to implement in SovereignFlow

1. Extend `PipelineStepDefinition` with immutable `config` / `raw` action
   settings.
2. Change the YAML parser so unknown step fields are preserved as action config
   instead of being rejected globally.
3. Keep globally reserved step fields:

   ```text
   id, action, action_version, next, routes, end
   ```

   Everything else belongs to the action config.
4. Change the action protocol from:

   ```python
   execute(context)
   ```

   to:

   ```python
   execute(step, context)
   ```

5. Move action-specific validation into action validators or action classes.
6. Keep the pipeline validator responsible for graph correctness, step reachability,
   version matching, and required/provided state contracts.
7. Add a reusable action documentation template in `docs/Action/action-template.md`.

### What must work after Step 1

- Existing `direct`, `graph`, and `strict` pipelines still execute.
- Step-local config is parsed, checksummed, and available to actions.
- Unknown fields are rejected only by the action that owns them.
- Pipeline checksums include action config.
- Invalid action config fails before runtime where possible.

### Tests

Unit tests:

- parser preserves action config;
- reserved fields cannot be duplicated or malformed;
- checksums change when action config changes;
- engine passes the current step to actions;
- action validator errors are stable and safe.

Integration tests:

- current pipelines continue to execute;
- audit records include the same trace as before;
- invalid pipeline YAML fails at startup.

Coverage target:

- 100% statement and branch coverage for changed production code.

## Step 2 — YAML-Driven `call_model`

### Goal

Replace the hard-coded SovereignFlow `call_model` behavior with a YAML-driven
contract aligned with LocalAI-RAG.

### What to migrate

From LocalAI-RAG `call_model`:

- `prompt_key` as the system prompt selector;
- system prompt file resolution under the configured prompts root;
- `user_parts` ordered assembly;
- templates containing `{}`;
- source fields read from pipeline state/context;
- optional generation parameters;
- fail-fast input validation;
- clear separation between system prompt and user content.

### What to adapt for SovereignFlow

LocalAI-RAG uses `PipelineState`. SovereignFlow currently uses `PipelineContext`.
The action should expose a safe source resolver that supports only explicitly
allowed context fields and methods, for example:

```text
normalized_query
evidence
citations_text
context_chunk_ids
retrieval_trace_summary
```

It must not allow arbitrary attribute access or private fields.

### Target YAML

```yaml
- id: call_model
  action: call_model
  action_version: "1.0"
  prompt_key: "general/answer"
  user_parts:
    user_question:
      source: normalized_query
      template: "### User question:\n{}\n\n"
    evidence:
      source: evidence
      template: "### Evidence:\n{}\n\n"
  max_output_tokens: 700
  temperature: 0.0
  next: finalize
```

### What must change

- `domain.prompt_name` must stop being the primary prompt selector.
- no runtime fallback to `domain.prompt_name` is allowed after this step starts;
  the legacy path must be removed rather than kept as compatibility behavior.
- `CallModelAction` must not hard-code the user prompt structure.
- The prompt hash must be computed from the loaded `prompt_key` prompt.
- Diagnostics must include `prompt_key`, prompt hash, selected model, and safe
  user part metadata, not full sensitive content unless diagnostics policy allows it.

### What must work after Step 2

- `direct`, `graph`, and `strict` pipelines define their prompt contract in YAML.
- Different `call_model` steps can use different prompts in the same pipeline.
- A missing `prompt_key` fails fast.
- Invalid `user_parts` fails fast.
- Existing demo still runs after pipeline YAML is updated.

### Tests

Unit tests:

- loads `prompt_key` from YAML;
- rejects missing prompt;
- rejects prompt path escape;
- assembles `user_parts` in YAML order;
- rejects unknown source;
- rejects template without `{}`;
- passes generation parameters only when configured;
- does not let user input select system prompt.

Integration tests:

- demo query sends YAML-assembled prompt to controlled model endpoint;
- diagnostics report `prompt_key` and prompt hash;
- three pipeline variants can use different prompt keys.

Coverage target:

- 100% statement and branch coverage for changed production code.

## Step 3 — Retrieval, Graph, and Context Actions

### Goal

Split the current monolithic retrieval/context behavior into actions closer to
LocalAI-RAG while keeping SovereignFlow neutral.

### What to migrate

From LocalAI-RAG:

- `search_nodes` as configurable retrieval;
- `expand_dependency_tree` as graph expansion, renamed and generalized;
- `fetch_node_texts` as materialization of retrieved IDs/texts;
- `manage_context_budget` as explicit context budget management.

### SovereignFlow action names

Use neutral names:

| LocalAI-RAG action | SovereignFlow action |
|---|---|
| `search_nodes` | `retrieve` or `search_chunks` |
| `expand_dependency_tree` | `expand_graph` |
| `fetch_node_texts` | `fetch_context_chunks` or keep inside retrieval only if already materialized |
| `manage_context_budget` | `manage_context_budget` |

### Required adaptation

LocalAI-RAG uses code-node IDs and snapshots. SovereignFlow must use:

- `chunk_id`;
- `source_id`;
- `source_version`;
- `domain`;
- `tenant_id`;
- ACL labels;
- classification level;
- graph relationship types.

### Target YAML example

```yaml
- id: retrieve
  action: retrieve
  action_version: "1.0"
  search_mode: hybrid
  top_k: 8
  query_source: normalized_query
  next: expand_graph

- id: expand_graph
  action: expand_graph
  action_version: "1.0"
  max_depth: 2
  max_nodes: 40
  relationship_types: []
  next: manage_context_budget

- id: manage_context_budget
  action: manage_context_budget
  action_version: "1.0"
  max_context_characters: 24000
  source: hits
  target: evidence
  next: call_model
```

### What must work after Step 3

- Retrieval behavior can vary per pipeline step, not only per domain profile.
- Graph expansion can be enabled/disabled per pipeline step.
- Context budget policy is explicit in YAML.
- The same domain can expose multiple retrieval strategies through capabilities.
- No action contains TaricAI, code-analysis, or medical assumptions.

### Tests

Unit tests:

- retrieval reads `search_mode`, `top_k`, and query source from YAML;
- graph action reads depth, direction, and relationship types from YAML;
- context budget truncates deterministically;
- context budget records omitted chunks;
- invalid budgets fail fast;
- ACL and classification boundaries remain enforced after each step.

Integration tests:

- semantic, BM25, and hybrid retrieval work against Weaviate;
- graph expansion works against PostgreSQL;
- context budget affects prompt content and diagnostics;
- graph-expanded restricted data is still visible to later policy checks.

Coverage target:

- 100% statement and branch coverage for changed production code.

## Step 4 — Routing, Guards, and State Transformation Actions

### Goal

Migrate the reusable control-flow actions from LocalAI-RAG without importing
code-specific behavior.

### What to migrate

From LocalAI-RAG:

- `set_variables`;
- `prefix_router`;
- `json_decision_router`;
- `loop_guard`;
- `repeat_query_guard`;
- selected inbox/message concepts only if needed by neutral routing.

### What not to migrate yet

Do not migrate `fork_action`, `merge_action`, `parallel_roads_action`, or
UI command actions until SovereignFlow has a neutral use case and a documented
contract for parallel execution and user-visible commands.

### Required neutralization

Routing actions must operate on neutral context fields, not code-analysis fields.
For example:

```text
last_model_response
normalized_query
retrieval_query
search_mode
context_blocks
evidence
```

No routing action may grant access, change tenant, alter ACL labels, or bypass
model transmission policy.

### What must work after Step 4

- A model can produce a controlled routing decision.
- Prefix-based routing can select predefined branches.
- JSON routing validates and cleans payloads.
- `set_variables` can map safe state fields.
- Loop guards prevent infinite retries.
- Repeat query guard prevents repeated retrieval loops.

### Tests

Unit tests:

- prefix router matches only configured prefixes;
- JSON router accepts only configured decisions;
- JSON router cleans routing keys before downstream use;
- set variables rejects unsupported target fields;
- loop guard denies after configured maximum;
- repeat query guard normalizes repeated queries;
- routing cannot modify security context.

Integration tests:

- a multi-step pipeline routes between direct answer and retrieval answer;
- invalid model routing output follows explicit `on_other` or fails according to YAML;
- trace shows selected route.

Coverage target:

- 100% statement and branch coverage for changed production code.

## Step 5 — Security, Observability, and Production Action Pack

### Goal

Complete the professional action foundation and make action behavior observable,
auditable, and safe for production domain solutions.

### What to migrate or implement

From LocalAI-RAG:

- structured per-action trace principle;
- action-level input/output summaries;
- visible stages for UI/debugging;
- fail-fast validation culture.

New SovereignFlow-specific actions:

- `enforce_model_transmission_policy`;
- optional `select_model` / `route_model` if local/external routing becomes
  dynamic and policy-driven;
- neutral `finalize` with citations, disclaimers, and safe output shaping.

### Model transmission policy

This is the policy described in Stage 4 of the integration verification plan:

```text
retrieval / graph results contain restricted data
-> external model is blocked
or
-> execution is routed to an explicitly configured local model
```

This policy must run after context construction and before `call_model`.

### What must work after Step 5

- Every built-in action has documentation in `docs/Action/`.
- Every built-in action has a versioned contract.
- Pipeline YAML fully controls action behavior where appropriate.
- Security policies cannot be overridden from pipeline YAML.
- External model transmission is checked against actual retrieved data labels.
- Action traces allow a user to understand what happened without a debugger.
- The web test console can display action trace and policy decisions.

### Tests

Unit tests:

- every built-in action validates its config;
- action docs examples parse successfully;
- action traces are safe and deterministic;
- transmission policy blocks restricted data for external models;
- local routing is allowed only by explicit policy;
- no fallback from local failure to external model.

Integration tests:

- end-to-end public query uses external model;
- restricted query is blocked before external HTTP call;
- restricted query routes to local model only when configured;
- audit records policy decision and selected model;
- UI displays safe policy outcome.

Coverage target:

- 100% statement and branch coverage for changed production code.

## Target Built-In Action Inventory

| Priority | Action | Status | Migration decision |
|---|---|---|---|
| 1 | `call_model` | Must be corrected | Migrate YAML-driven prompt contract. |
| 1 | `retrieve` / `search_chunks` | Existing but simplified | Generalize LocalAI `search_nodes` contract. |
| 1 | `expand_graph` | Existing but simplified | Keep neutral graph semantics. |
| 1 | `manage_context_budget` | Missing | Migrate neutral budget behavior. |
| 1 | `finalize` | Existing but simplified | Keep neutral, add documented output policy. |
| 2 | `set_variables` | Missing | Migrate with allowlisted state fields. |
| 2 | `prefix_router` | Missing | Migrate route-only behavior. |
| 2 | `json_decision_router` | Missing | Migrate with strict schema and safety. |
| 2 | `loop_guard` | Missing | Migrate loop protection. |
| 2 | `repeat_query_guard` | Missing | Migrate neutral anti-repeat behavior. |
| 3 | `load_conversation_history` | Missing | Migrate later with explicit storage contract. |
| 3 | `translate_in_if_needed` | Missing | Migrate only as explicit policy, not fallback. |
| 3 | `translate_out_if_needed` | Missing | Migrate only as explicit policy, not fallback. |
| 4 | `fork_action` / `merge_action` | Missing | Defer until neutral parallel execution contract exists. |
| 4 | `add_command_action` | Missing | Defer until neutral command UI contract exists. |
| 4 | `inbox_dispatcher` | Missing | Defer unless needed for controlled runtime knobs. |

## Immediate Correction Required

Before implementing Stage 4 data-label transmission policy, fix `call_model`.
The current implementation hard-codes prompt assembly and therefore violates the
action contract inherited from LocalAI-RAG.

Minimum correction:

1. add step config to `PipelineStepDefinition`;
2. parse action-specific YAML fields;
3. update `CallModelAction` to require `prompt_key` and `user_parts`;
4. update `direct`, `graph`, and `strict` pipeline YAML files;
5. remove the legacy `domain.prompt_name` runtime fallback and hard-coded prompt
   assembly;
6. remove or rewrite tests that asserted the old prompt behavior;
7. document `docs/Action/call_model.md` before changing the implementation.

## Definition of Done for the Full Action Migration

The migration is complete only when:

- action documentation exists before implementation;
- all migrated actions are domain-neutral;
- every action has stable YAML schema validation;
- every action has unit and integration tests;
- changed production code has 100% statement and branch coverage;
- no action uses hidden fallbacks;
- prompt selection is never controlled by user input;
- model transmission policy is enforced before any external call;
- traces and diagnostics show what each action did;
- existing online demo continues to work with Keycloak, capabilities, and three
  pipeline variants.
