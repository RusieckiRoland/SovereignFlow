# SovereignFlow Demo — RCIDG Regulation Dataset

This demo domain verifies that SovereignFlow works correctly across all its core capabilities.
The dataset is a fictional regulation: **Regulation on Critical Infrastructure Data Governance (RCIDG)**.

## Dataset Structure

15 articles covering the full lifecycle of a data governance regulation:

| Article | Topic | ACL Labels |
|---------|-------|------------|
| Art. 1  | General Provisions | public |
| Art. 2  | Definitions | public |
| Art. 3  | Operator Obligations | public |
| Art. 4  | Classification Levels | public |
| Art. 5  | Risk Assessment | internal, compliance |
| Art. 6  | Data Governance Register | internal, compliance, legal |
| Art. 7  | Access Control | internal, compliance, security |
| Art. 8  | Classified Data Handling | restricted, security |
| Art. 9  | Third-Party Processors | internal, compliance, legal |
| Art. 10 | Incident Response | restricted, security, compliance |
| Art. 11 | Retention and Disposal | internal, compliance, legal |
| Art. 12 | Enforcement and Penalties | public |
| Art. 13 | Appeals Procedure | public |
| Art. 14 | Technical Security Standards | restricted, security, technical |
| Art. 15 | Commencement | public |

The knowledge graph in `relationships.json` contains 36 directed `references` edges between articles,
forming a realistic cross-reference structure (e.g., Art. 10 → Art. 3, Art. 6, Art. 4).

---

## Ingestion

```bash
python demo/scripts/ingest.py --url http://localhost:8000 --token <jwt>
```

---

## What This Proves

### 1. Semantic Search
**Pipeline**: `strict` (search_mode: semantic)

Query: *"What encryption algorithm is required for classified data at rest?"*

Expected: Art. 14 (AES-256) retrieved. Graph expansion pulls Art. 8 (classified handling) and Art. 7 (technical controls) as related nodes.

### 2. BM25 Keyword Search
**Pipeline**: `direct` (search_mode: bm25)

Query: *"administrative fine turnover percentage"*

Expected: Art. 12 retrieved via exact keyword match ("annual turnover", "administrative fine").
Art. 14 not retrieved (keyword miss) — proves BM25 behaves differently from semantic search.

### 3. Hybrid Search
**Pipeline**: `default` (search_mode: hybrid)

Query: *"What are the timelines for reporting a data breach?"*

Expected: Art. 10 (incident response timelines table) + Art. 3 (notification obligation in 3.4).
Hybrid combines semantic relevance (data breach = incident) with keyword hit (72 hours, notification).

### 4. Graph Expansion
**Pipeline**: `graph` (expand_graph enabled, depth: 2, relationship_types: references)

Query: *"What controls apply to classified data?"*

Expected seed: Art. 8 (classified handling). Graph expands outward:
- depth 1: Art. 4 (classification levels), Art. 7 (access control)
- depth 2: Art. 3 (obligations referencing Art. 7), Art. 14 (technical standards referencing Art. 8)

This proves the traversal follows `references` edges and respects `max_depth`.

### 5. ACL Filtering
**Scenario A**: User with `acl_labels: [public]`

Query: *"What penalties apply to operators who fail to notify an incident?"*

Expected: Art. 12 (public, enforcement) retrieved. Art. 10 (restricted, incident response)
NOT retrieved — ACL filter blocks it.

**Scenario B**: User with `acl_labels: [public, restricted, security]`

Same query. Expected: Art. 10 AND Art. 12 both retrieved. Graph expansion now also reaches
Art. 14 (restricted, technical standards).

This proves ACL labels are enforced at the retrieval boundary and that Weaviate does not
return chunks the user's token does not grant access to.

### 6. Security Policy — Clearance Level
Ingest a variant of Art. 8 with `clearance_label: SECRET` and configure the domain with
`security_model: kind: clearance_level`. Users without SECRET clearance in their JWT claim
must not receive Art. 8 in any retrieval result.

### 7. Pipeline Composition — Evidence Guard
**Pipeline**: `strict`

Query: *"What is the capital of France?"*

Expected: No relevant evidence retrieved. The `require_evidence` action returns a failure
before `call_model` executes. The model is never called. Proves the pipeline can enforce
"no hallucination without grounding".

### 8. Pipeline Composition — Retry Loop
**Pipeline**: custom pipeline with `repeat_query_guard` or `loop_guard`

Build a pipeline where: retrieve → evaluate evidence quality → if insufficient, widen search
and retrieve again → then call model. This proves pipelines can branch conditionally and loop.

### 9. External vs Internal Model Policy
Configure two model servers:
```yaml
model_servers:
  - id: internal-model
    trust_boundary: internal
  - id: external-model
    trust_boundary: external
```

Ingest Art. 14 with `classification_labels: [RESTRICTED]`. Configure the domain security model
to block external transmission for RESTRICTED documents. Run the pipeline with
`selected_model_server_id: external-model`. Expected: `enforce_model_transmission_policy`
blocks the call before the model is reached.

### 10. Conversation Memory
**Pipeline**: `conversation`

Run a multi-turn conversation:
1. "What obligations does an operator have?" → retrieves Art. 3
2. "What is the fine for failing to comply with those obligations?" → no need to re-specify
   "obligations"; history context carries forward, retrieves Art. 12

This proves the conversation pipeline loads history and provides it to the model.

---

## Domain Config

`config/domains/regulations.yaml` — configures:
- ACL enabled, default labels: `[public]`
- Hybrid retrieval, top_k: 5
- Graph enabled, depth: 2, relationship_types: references

Modify `allowed_labels` in the domain config or pass `acl_labels` in the JWT token to test
different ACL scenarios.
