# API access and customer-agent permissions

Source baseline: Enhance orchd OpenAPI 12.25.8, retrieved 2026-09-10. These are documented contracts and operating rules; deployed credential authority must be verified separately.

## Credential scope

`createAccessToken` creates rights within an organisation. Its `NewAccessToken` schema requires `roles` and declares `tokenExpires`, `friendlyName`, `allowedIps` and `ipRestricted`; it does not declare `permissionLevel`, `readOnly`, a website ID or per-operation scopes.[1]

The role enum is exactly `Owner`, `SuperAdmin`, `Business`, `SiteAccess`, `Support`, `Sysadmin`. Do not treat this as a total ordering or infer a read-only role. Customer and provider/master credentials must be separate.[1]

| Intended authority | Appropriate starting point |
|---|---|
| Hosting provider/cluster | Provider-owned token in the master organisation with the required operator role; admin profile on the operator's system. |
| All permitted resources of a normal customer | Dedicated token in that customer organisation with the required role; organisation profile. Inspect whether the org is a reseller or has additional sites. |
| One website within a shared organisation | A documented `SiteAccess` login member can receive selected `siteAccesses`. Equivalent organisation-token binding is not documented; obtain a supported, tested token procedure before relying on it. |
| Website files/processes | Separately authorised website SSH/SFTP identity; not the provider's root account. |

The member/invitation schemas have `siteAccesses`; token create/update schemas do not. An access-token ID must not be assumed to be a `member_id` accepted by `updateMember`. `getSiteAccessToken` returns an encrypted website JWT, but the schema does not define its general orchd bearer compatibility, lifetime or revocation behavior.[1]

A local `scope: site` allowlist catches wrong-target calls through this client. It does not reduce the authority of a token, prevent a same-user process reading its credential file, or establish isolation from other sites in the same org. Never distribute a master/reseller token as a workaround. A dedicated non-reseller customer org containing one site is a practical org boundary, not an immutable single-site token.

## Enrolment and validation

1. Identify the actual customer org, intended site set and package. Record whether org-wide access is acceptable; do not infer it from a single domain in the request.
2. Inspect the relevant operation contracts and role/feature gates. Use an existing approved credential if available. Ask the responsible operator for missing credentials rather than minting or escalating silently.
3. For approved token creation, set role, name, expiry and IP settings deliberately. `allowedIps: []` is documented as allowing all IPs; don't assume an interactive admin-login lockdown covers API tokens.[1]
4. Capture the returned secret privately. Token listing returns metadata, not the declared `unencryptedToken` field from creation. Keep the token outside webroots and skill archives.[1]
5. Execute a harmless approved own-org/own-site read. Verify the intended resource ID, not just HTTP success. Then, within an agreed sandbox, test denied sibling-site/customer/server requests **directly against Enhance**, independently of client-side refusal. Use known non-sensitive targets and no destructive requests.
6. Record the exact credential role/scope, target version, allowed/denied operation IDs and date. A failing local guard, 404 alone, or a mocked SuperAdmin token is not server-side isolation evidence.
7. On rotation, approve and create replacement, verify it, switch the deployment, revoke the old token and verify the old token no longer authorises the harmless read. Do not infer that this closes existing SSH sessions, keys, cookies or derived JWTs.

## Read is an effect category, not an HTTP verb

Keep these operations out of automatic inventory/retries. Their definitions describe access creation, work, secret data or writes:[1]

- `getOrgMemberLogin`, `createOtpSession`, `ssoToRoundcube`, `getPhpMyAdminSSOUrl`, `getPhpMyAdminWebsiteSSOUrl`, `getWordpressUserSsoUrl`, `openclawSso`: access/session/SSO capabilities.
- `downloadSql`, `downloadWebsiteBackup`: data export and workload, not metadata.
- `getWordpressInstallations`: scans and inserts installation records.
- `scanImportMigrations`: scans files and adds importer records.
- `getWebsiteDomainSslCert`, `getWebsiteMailDomainSslCert`: responses include certificate `key` material.
- Subscription-bandwidth requests with `refreshCache: true`: an explicit refresh action.

Logs and wp-config can contain secrets even when no token-like property name appears. Sensitive/non-JSON exports need an explicit private output path; do not dump their content into chat. A local dry-run must not contact the operation.

## Errors and completion

- **401:** inspect the credential source, expiry and auth mode privately. No password guessing, SSO creation or broader-token fallback.
- **403:** check the specific role, website grant, package and target. Stop rather than treating it as a network fault.
- **404:** verify IDs, parent relationships and version; do not claim that it proves absence or isolation.
- **400/409:** inspect the literal schema and read current state before retrying a create.
- **Timeout after a write:** outcome is unknown until readback/status reconciliation. Do not blindly replay.
- **2xx:** inspect returned per-component/per-target results. `backupWebsite` explicitly permits completely failed backup metadata with HTTP 201; migration scheduling can be partial.[1]

## Sources

[1] https://apidocs.enhance.com/spec/oas3-api.yaml
