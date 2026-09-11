# Enhance CLI

A Python 3.9+ standard-library client for the bundled Enhance OpenAPI 12.25.8 schema. It discovers all **482 operations**, including **201 GETs**. Discovery is not a claim that every operation can be authenticated or executed: cookie-only operations and inconsistent schema contracts are reported explicitly.

Keep `enhance.py` with `assets/openapi.json` and `assets/freshness.json`. Paths are resolved beside the script, not against the current directory. There are no runtime packages to install. Renaming the containing directory or placing this layout under `scripts/` is supported.

**Client scope guards prevent mistakes; they do not make a root token safe. Use an Enhance principal whose organisation/site grants are enforced by the server.** Neither the `SiteAccess` role on an organisation token nor a website ID in this configuration proves server-enforced website restriction. The CLI does not enroll principals, infer token/member equivalence, invent website grants, or treat a website JWT as a general-purpose bearer token.

## Discovery

```sh
python3 enhance.py ops
python3 enhance.py ops --search wordpress --method GET
python3 enhance.py ops --tag websites
python3 enhance.py --pretty describe getWebsites
python3 enhance.py describe createAccessToken
```

`ops` includes total/matched counts, method/tag counts, exact operation IDs, routes, summaries and tags. Search is a case-insensitive substring over ID, route, summary and tags. Method filters use uppercase names. Discovery and description never read credentials or contact the API.

Aliases are conveniences, not extra capabilities:

| Alias | Exact operationId |
|---|---|
| `version` | `orchdVersion` |
| `status` | `orchdStatus` |
| `websites` | `getWebsites` |
| `customers` | `getOrgCustomers` |

`describe` expands local references, combines inherited and operation parameters, retains descriptions and request/response schemas, reports effective security and security scheme definitions, and explains apply/output gates and known schema issues. Route/input spelling is not repaired.

## Profiles and credentials

Pass global options **before** the subcommand:

```sh
python3 enhance.py --config site.json call getWebsite
```

Example `site.json`; every identifier and hostname here is synthetic:

```json
{
  "base_url": "https://panel.example.invalid/api",
  "scope": "site",
  "org_id": "11111111-1111-4111-8111-111111111111",
  "website_id": "22222222-2222-4222-8222-222222222222",
  "domain_ids": ["44444444-4444-4444-8444-444444444444"],
  "token_env": "ENHANCE_SITE_TOKEN"
}
```

`base_url` is the approved API prefix; the schema contains no server URL to infer it from. Only HTTPS is allowed in ordinary use. Userinfo, query strings, fragments, control characters, encoded traversal and ambiguous authorities are rejected. There is no SSL-disable switch. A hidden test-only option permits HTTP on literal loopback IPs; it does not allow `localhost` or other DNS names.

Scopes:

- **admin**: permits schema-catalog routes, subject to input/auth/media checks and the apply/output gates. Optional configured locators still bind exact matching parameter names.
- **organisation**: requires `org_id`, binds every declared org locator and blocks cluster/root routes and ambiguous unanchored ownership. Website routes under that org may use an explicit `website_id` parameter; setting `website_id` in the profile further binds it. A website-only route without an org path requires a configured website ID. Descendant recursion and the master-only `showDeleted` query override are blocked.
- **site**: requires both IDs. Named website parameters must match the configured site; an organisation-wide route cannot be made site-scoped by putting a website ID in its body or query. Global listing/creation and root/server routes are blocked. Domain-only routes require an explicit matching `domain_ids` entry; domain parameters beneath an already bound website route use that route's site anchor. Additional domain locators in body/query still require the allowlist.

The guard uses **named schema parameters**, never the position of an ID in a path. In particular, a backup server ID cannot stand in for the website ID. Cross-org/site body and query locators, ambiguous nested owner containers, and unanchored app/login/member operations fail closed. Some legitimate workflows therefore need a separately reviewed profile or provider action. Root-only wording in the current schema also blocks `setWebsiteBackupsDisabledStatus` and `getWebsiteDomainMapping` in restricted profiles.

Optional `server_id` is a UUID binding for an exact server parameter. `server_context` is an object mapping exact server locator names to UUIDs, for example `{"appServerId":"33333333-3333-4333-8333-333333333333"}`. Restricted requests cannot use additional server locators outside the configured IDs. Configuration does not prove actual server/site ownership.

Choose **at most one** credential source:

```json
{"token_env": "ENHANCE_TOKEN"}
```

```json
{"token_file": "private/token.txt"}
```

```json
{"token_env_file": "private/runtime.env", "token_env_key": "ENHANCE_TOKEN"}
```

These are source-field fragments to include in a complete profile, not standalone profiles. File paths are relative to the config file, unless absolute. On POSIX, credential files must be regular, owned by the current user, and have no group/other permissions (`chmod 600`). Leaf symlinks are refused. Environment files are parsed literally: optional `export`, matching single/double quotes, no shell execution or interpolation. Missing/duplicate keys and invalid tokens fail without printing values. Use a private directory/appropriate ACLs on Windows; POSIX modes do not replace NTFS access controls.

Provide the environment variable through your approved secret manager/session. There is **no token-value command option**. Dry-runs do not load token sources; ordinary bearer calls require one. Body secrets belong in private body files, and sensitive schema parameters use `--params-file`, not `--param`.

A named-profile file may use `{"profiles":{"site":{...},"owner":{...}},"default_profile":"site"}`. Select with `--config profiles.json --profile owner`. Unknown configuration fields and multiple token sources are rejected.

## Complete bearer credential

Supply `<token-id>_<secret>` in your configured token source. `createAccessToken.unencryptedToken` alone is only the secret component. Use [the composition helper](authentication.md) on a private creation response. A 403 can be malformed authentication rather than an incorrect role.

## Calls, typed parameters and mutation gates

```sh
python3 enhance.py --config site.json call getWebsite
python3 enhance.py --config owner.json call getWebsites --param offset=0 --param limit=20 --count
python3 enhance.py --config owner.json call getWebsites --param 'roles=["application","backup"]'
python3 enhance.py --config site.json call updateWebsite --body-file change.json
```

The last command only constructs a **local dry-run**. Add `--apply` only after reviewing the target, schema, consequences and rollback. **Every non-GET requires `--apply`**, including DELETE, restore and credential operations. `--dry-run` forces local construction even for an ordinary GET. Dry-run output contains the route template, parameter names, media and byte count, not credential values, body content or a secret-bearing URL.

`--param NAME=VALUE` uses exact, case-sensitive schema names. Strings are literal; integer/number/boolean/array parameters use JSON syntax. Query arrays honor `style`/`explode`: `getWebsites` `roles` and `servers` use a comma-separated value because `explode=false`; ordinary form arrays use repeated query keys. All data is URL-encoded. An explicit empty array is distinct from an omitted query parameter. Configured org/site/server IDs are filled only where those parameters exist and cannot be overridden.

Use `--params-file params.json` for a typed JSON object, including any required sensitive headers such as `Password`. `Authorization` cannot be supplied this way; it is managed by the configured credential source. Duplicate parameters, unknown names, missing required values, UUID normalization, boolean-as-integer and invalid enums are rejected. Path values are checked through repeated percent-decoding for traversal/separator/newline injection, then the original value is encoded.

The bundled `getWebsites` parameter is **`recursion`**, with the values in `describe`, not `recursive`. Some endpoint prose refers to `recursive`, but this client follows the actual parameter definitions. Do not silently substitute a flag observed in an older client or another route.

### GETs that require explicit approval

The reviewed operation-ID risk table includes OTP/session/SSO creation, `downloadSql`, `getWordpressInstallations` (discovery inserts database metadata), `scanImportMigrations`, `downloadWebsiteBackup`, both domain TLS certificate reads (which return `key`), and remote import-domain pulls. Additional conservative rules flag login/token/key/log/download and unstructured-text responses. `flush=true`, `refreshCache=true` and `shouldRedirect=true` also require `--apply`.

Sensitive/log/download operations additionally require a **new** `--output` file:

```sh
python3 enhance.py --config site.json call getWebsitePhpErrorLog --apply --output private-php-log.json
python3 enhance.py --config site.json call getWebsiteDomainSslCert --param domain_id=44444444-4444-4444-8444-444444444444 --apply --output private-tls.json
```

All redirects, including same-host redirects, are refused. This intentionally prevents automatic completion of redirect-only SSO endpoints. No redirect target or raw upstream error body is printed.

### Uploads

```sh
python3 enhance.py --config owner.json call setOrgAvatar --file avatar=logo.png
python3 enhance.py --config site.json call uploadWebsiteBackup --raw-file approved-backup.tar.gz
```

Both examples are local dry-runs; add `--apply` only for an approved change/restore. Multipart binary properties use repeated `--file FIELD=PATH`; nonbinary multipart properties use a JSON object in `--body-file`. Only schema properties/encoding types are accepted. Multipart headers use a safe generated filename, not the original path. Raw gzip uploads preserve file bytes and check only the gzip signature, **not** archive validity, contents or restore safety.

Requests/responses are buffered with a default 16 MiB limit, adjustable through `--max-bytes` up to 128 MiB. This is deliberately smaller than some server-documented upload limits, including 100 GB imports. The client is not a large-archive streaming uploader. Unsupported media/encodings are explicit errors.

## JSON output and client-side views

Normal output is compact JSON; `--pretty` is a global option. Default responses recursively redact credential-bearing keys (including `key`), name/value settings that identify secrets, embedded serialized JSON and the exact supplied bearer token. Detection of arbitrary unknown secrets in free-form text cannot be guaranteed; known sensitive text is output-only.

`--output NEW_FILE` preserves the **raw, potentially secret response bytes** instead of printing them. It creates an exclusive mode-0600 file on POSIX before sending; it never overwrites an existing file or symlink. Transport failures leave an empty reserved file. It cannot be combined with client-side filters/projection/counting. Keep output outside public directories and treat it as confidential.

Documented JSON responses are decoded as JSON. `orchdVersion` also accepts a strict plain-text version such as `12.25.8`, because deployments can return plain text despite the schema. JSON-string `orchdStatus` is supported. Other unexpected plain text/binary responses are withheld and require explicit private output, not guessed JSON or printed logs.

Client-side JSON pointers use RFC 6901 (`/id`, `/status`, `/nested/name`):

```sh
python3 enhance.py --config owner.json call getWebsites --param limit=20 --where '/status="active"' --select /id --select /domain
python3 enhance.py --config owner.json call getWebsites --param limit=20 --count
```

Select fields that actually exist in the response schema; missing pointers error rather than silently dropping data. The default array is `/items`, or the root if it is an array; `--items /some/array` selects another one. `--where` is exact typed equality, not an expression language. Filtering/projection runs **after redaction**. Views report `returned_count`, `matched_count`, selected `items` unless count-only, and **all outer response metadata**, preserving `total`, nested totals and continuation fields. They do not turn one page's row count into the server total.

### Pagination

No endpoint is auto-paginated. For `getWebsites`, `offset` is the described starting offset and `limit` is the described maximum number of returned items. Request each page explicitly and inspect the returned `items` and `total`. No page-number parameter, guessed continuation token, page-size increment or assumption that a short page exhausts results is implemented. A total is preserved, not treated as proof of a complete collection.

## Batch reads

```json
[
  {"operationId":"orchdVersion"},
  {"operationId":"getWebsites","params":{"offset":0,"limit":20},"view":{"count":true}},
  {"operationId":"getOrgCustomers","view":{"select":["/id"]}}
]
```

```sh
python3 enhance.py --config owner.json batch reads.json
```

Plans are explicit arrays of **1–50** entries. Only exact `operationId`, `params` and `view` keys are accepted; no aliases, bodies, writes, apply flags, output files, expressions or inferred pagination. **Every entry and credential prerequisite is preflighted before the first network request.** Mutations and flagged GETs are blocked, even if the operator would supply `--apply` elsewhere.

Each preflighted request is attempted at most once. Responses/errors are aggregated with exact `requested`, `succeeded`, `failed` counts and one result per plan entry. `--max-bytes` is the aggregate response-body budget, including bytes consumed before parsing, projection or transport failures in batch mode; if a size limit is hit, remaining entries receive explicit not-sent errors. Data-dependent view/response errors cannot be known at preflight and are reported per entry.

Exit codes: `0` success or local dry-run; `1` batch completed with failures; `2` validation/auth/transport/output error; `130` interrupted. HTTP 401/403/404/429 have specific guidance. Error bodies, URL values, token values and raw logs are not echoed. There are no automatic mutation retries or privilege escalation. A failed/timed-out mutation can have an unknown outcome: verify the exact resource before retrying.

## Freshness and reviewed updates

```sh
python3 enhance.py refresh-status
python3 enhance.py schema-diff reviewed-candidate.json
```

`assets/freshness.json` stores the retrieved date (`2026-09-10`) and schema version, not a precomputed age. Age is computed from dates in UTC at use. At **30 days or older**, ordinary CLI invocations emit one advisory to stderr; a batch emits it once, not per request. Future dates and mismatched version metadata are flagged too.

`schema-diff` compares added/removed/changed operation IDs, exact paths/methods, resolved parameters, request/response content, effective security arrays and security scheme definitions, descriptions and tags. Referenced component changes are attributed to affected operations. It does not overwrite active files, fetch a candidate, install a schedule, update permissions or change the risk table. Fetch candidates separately through a reviewed process; examine semantic/access changes before replacing a tested bundle.

## Explicit limits and schema defects

The validator implements an OpenAPI subset: types, required fields, enum, literal UUID, finite numbers, numeric and length bounds, arrays, closed properties, additional-property schemas, `allOf`, `oneOf` and `anyOf`. It is **not full JSON Schema validation**. It does not enforce every format (`email`, domain/path/date/semver and others), regex patterns, discriminator semantics or server/business rules. Open objects remain open as the schema specifies. Server-side ownership, permissions, package restrictions, archive validity and actual effects must be verified separately.

Strict construction currently succeeds for **476 operations using synthetic offline inputs**. Six are explicitly blocked:

| Operation | Reason |
|---|---|
| `verify2FA`, `resendPin` | Require `sessionCookie`; this bearer client does not implement cookie sessions. |
| `uploadSql` | Route names `websiteId`/`db_id` conflict with declared `website_id`/`db_name`. No mapping is guessed. Its multipart schema is discoverable, but the route cannot safely be constructed. |
| `updateEmailAutoresponder`, `deleteEmailAutoresponder` | The route contains `autoresponder_id` without a matching parameter declaration. |
| `updateServerRole` | The five `oneOf` request alternatives are identical; strict oneOf cannot select exactly one. |

There are 351 operations declaring a bearer alternative, 2 cookie-only operations, and 129 with no security declaration. **Omitted security does not prove public access**: the client sends the configured bearer if available but makes no promise about server auth requirements. Explicit public security in a schema suppresses bearer sending. Additional documented email-client headers may still be required. Arbitrary auth scheme combinations, cookie parameters, unsupported query/path serialization and unexpected media fail explicitly.

For token creation, use the described `roles` array, not an invented `permissionLevel` field. The API's `NewAccessToken` has no `siteAccesses`; no token ID is assumed to be a member ID. This CLI does not establish token grant ceilings, derived JWT lifetime/compatibility or minimum live permissions.

From the repository/skill root, run `python3 -m unittest discover -s scripts -p 'test_*.py' -v`. Tests use the official local schema plus labelled synthetic fixtures and loopback servers. `python3 scripts/build_inventory.py` constructs every operation without network access and creates `scripts/evidence/operation-coverage.json`; this generated report stays out of Git. Live read-only authorization checks are separate from these tests.
