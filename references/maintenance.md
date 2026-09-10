# Updating the Enhance knowledge and CLI

The update check runs when the skill is used. It creates no scheduled task, daemon or background updater.

## Trigger

Run `refresh-status` once at the start of an interaction. A review age of **30 days or more**, a controller/schema version mismatch, a changed operation, or a newly observed API failure triggers review. Report a single short notice such as: “The Enhance API reference is due for review; I’m checking the operations this task uses.” Do not repeatedly notify within the same interaction, and do not block urgent recovery for an unrelated documentation refresh.

A public documentation check is not approval to upgrade the control panel or change a customer's permissions. Never auto-enable new API operations or silently replace executable scripts from a downloaded source.

## Review procedure

1. Read `https://enhance.com/support/release-notes`, `https://apidocs.enhance.com/` and the canonical `https://apidocs.enhance.com/spec/oas3-api.yaml`. Fetch public sources without the customer's bearer token. Check the live controller separately with `orchdVersion`.
2. Preserve the active schema. Use a fresh local candidate file, record the retrieval date/version and convert the vendor YAML to JSON with the reviewed maintainer helper. Runtime calls do not require a YAML package; schema regeneration does. If the optional parser is absent, the agent can still inspect current docs and report the specific refresh gap without installing packages automatically.
3. Use `schema-diff <candidate-json>` to examine operation additions/removals and request/response/security changes. Compare referenced component changes, not just operation IDs or version strings. Read changed descriptions: GETs can gain side effects without changing paths.
4. Recheck `NewAccessToken`, `UpdateAccessToken`, `Role`, member/invite `siteAccesses`, website JWT, SSO, exports, discovery, backup/restore defaults, Node modes and permissions. Keep contradictory docs visible until resolved.
5. Update the operation-effect policy, schema and affected skill procedures together. Run the offline suite plus regressions for changed operations. Use an approved harmless live read to check compatibility; obtain separate approval for a meaningful live-write test.
6. Stage and reproduce both skill copies from the same reviewed runtime source. The customer copy must remain standalone. Exclude private profiles, tokens, customer data, logs and dependency caches from distribution.
7. Record the completed review date only after the evidence and tests are reviewed. Keep the previous release under the operator's retention policy for rollback. Do not stamp freshness merely because a request succeeded or a version string was unchanged.

## Refresh boundaries

- Schema discovery is not permission verification. New paths are not automatically authorised customer actions.
- The client supports the pinned snapshot; the public specification can lag or contradict the installed product. Verify the affected behavior rather than assuming newer text is always correct.
- Updating skills/scripts does not upgrade Enhance, apt packages, PHP, Node or a customer's app.
- Before replacing an installed skill, preserve private configuration separately and do not alter other agent profiles. Re-run only affected regression and integration checks.

## Maintainer command

Through `terminal`, after fetching the canonical YAML to a new local file:

```text
python3 <skill-dir>/scripts/prepare_schema.py <reviewed-source.yaml> <new-candidate.json>
python3 <skill-dir>/scripts/enhance.py schema-diff <new-candidate.json>
```

The converter refuses to overwrite an existing candidate, rejects external references, duplicate/non-string mapping keys and non-finite values, and never activates the candidate. It reads bounded regular files and re-parses serialized JSON before writing. Its YAML parser is an optional maintainer dependency; normal JSON runtime calls have no such dependency.

Keep source identifiers literal: reject key-type collisions rather than coercing keys or choosing a winning definition. Conversion is preliminary validation; require a successful runtime `schema-diff` before adoption, including resolved references and deprecated-only changes. Keep batch stdout on partial failure and count consumed response-body bytes even when parsing or projection fails.
