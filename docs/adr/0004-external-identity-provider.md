# ADR-0004: External Identity Provider via OIDC/JWT

**Status:** Accepted  
**Date:** 2026-06-13

## Context

RAG systems serving multiple tenants must authenticate users and carry per-request security context: tenant ID, roles, ACL labels, clearance level. Implementing user management, password storage, token issuance, and session handling inside SovereignFlow would make it a user management system in addition to a RAG engine — outside its responsibility boundary.

## Decision

SovereignFlow does not manage users or issue tokens. All authentication is delegated to an external OIDC provider (Keycloak in the reference deployment). The application validates JWT access tokens on every request and extracts security context from configured claims.

Claim names are configurable per deployment:
```yaml
identity_provider:
  tenant_claim: tenant_id
  roles_claim: roles
  clearance_claim: clearance_label
  acl_claim: acl_labels
```

JWKS keys are cached locally with a configurable TTL to avoid a remote call on every request.

## Consequences

**Positive:**
- SovereignFlow has no user database and no password handling — entire attack surface of credential management is eliminated.
- Any OIDC-compliant provider (Keycloak, Auth0, Azure AD, Okta) can be used without code changes.
- Multi-tenancy is handled by the identity provider — different tenants get different tokens with different claims.
- Token rotation and key rollover are managed externally; the application picks up new JWKS keys automatically within TTL.

**Negative:**
- The application is unavailable if the identity provider is unavailable (JWKS fetch fails on cold start or cache expiry).
- Local development requires a running Keycloak instance or a token stub.
