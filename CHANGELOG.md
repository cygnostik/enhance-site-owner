# Changes

## 0.1.1

- Document the complete organisation bearer format: token ID, underscore, secret.
- Add `scripts/compose_token.py` to assemble API creation responses into new private credential files without printing secrets or duplicating the prefix.
- Clarify HTTP 403 diagnostics: check token composition before changing permissions.
- Add regressions for composition, malformed/duplicate input, private output and overwrite refusal.

## 0.1.0

Initial schema-driven CLI, administrator/site-owner skills, scoped profiles and on-use API refresh guidance.
