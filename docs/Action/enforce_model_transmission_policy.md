# `enforce_model_transmission_policy` Action

`enforce_model_transmission_policy` checks whether retrieved context may be sent
to the configured model.

This action must run after retrieval, graph expansion, and context budgeting,
but before `call_model`.

It is intentionally fail-closed. It does not silently downgrade to another
model and does not fall back to a local provider unless a future explicit
`select_model` contract implements that behavior.

## YAML Contract

```yaml
- id: enforce_model_transmission_policy
  action: enforce_model_transmission_policy
  action_version: "1.0"
  restricted_acl_labels:
    - restricted
  max_external_classification_level: null
  next: call_model
```

## Fields

| Field | Required | Description |
|---|---:|---|
| `restricted_acl_labels` | yes | ACL labels that must never be transmitted to an external model. Empty list is allowed only as an explicit policy. |
| `max_external_classification_level` | yes | Highest classification allowed for external transmission, or `null` for no explicit classification ceiling. |

## Behavior

- Local models are allowed after recording a safe policy decision.
- External models are blocked when the authenticated user cannot transmit to
  external models.
- External models are blocked when any retrieved or graph-expanded chunk has a
  restricted ACL label.
- External models are blocked when any retrieved or graph-expanded chunk exceeds
  the configured external classification ceiling.
- `call_model` must refuse to run if this policy has not already allowed the
  call.

## Diagnostics

Diagnostics expose only safe metadata:

- model scope;
- allow/block decision;
- reason code;
- checked chunk IDs;
- blocked chunk IDs;
- blocked ACL labels.

No document text is logged by this action.
