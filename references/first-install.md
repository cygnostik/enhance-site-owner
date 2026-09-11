# First installation

Place this skill in your agent's skills directory or use it as a standalone directory. Keep the private API profile outside the skill and outside served website directories.

## Obtain the API schema once

The customer archive fetches the publisher's official schema at setup rather than redistributing the publisher's complete specification. The source is `https://apidocs.enhance.com/spec/oas3-api.yaml`. The helper uses no hosting credentials, verifies HTTPS, refuses redirects and refuses to overwrite an existing schema. A version different from the release metadata stops installation for review.

The one-time YAML conversion needs PyYAML. Use an existing Python environment that contains it; obtain approval before adding that dependency if none is available. Normal CLI calls use only Python 3.9+ standard-library modules after setup.

Through `terminal`:

```text
python3 <skill-dir>/scripts/setup_schema.py
python3 <skill-dir>/scripts/enhance.py ops --search dns
python3 <skill-dir>/scripts/enhance.py refresh-status
```

If the schema already exists, skip setup. Updates use the separate candidate/review procedure in [maintenance](maintenance.md), not this first-install helper.

## API key format

Use the complete `token-id_secret` value. If starting from an API creation response, use [the private-output helper](authentication.md); do not copy only `unencryptedToken`.

## Configure your access

1. Obtain your provider-issued API base, actual customer org ID, permitted website ID and credential. Use a customer-level credential, not the hosting administrator's token.
2. Copy `templates/site-profile.json` to a private directory and replace each explicit placeholder. `domain_ids` is an optional additional allowlist; add only verified domains belonging to the authorised site. Use `templates/organisation-profile.json` instead when your agent may manage the whole customer organisation.
3. Save the token to the configured private file (0600 on POSIX; restrict its ACL on Windows), or use the supported environment locator. Do not paste the credential into commands, chat or the skill.
4. Verify your own-site read, then have the provider validate denied scope directly at Enhance independently of the CLI guard. A configured `website_id` is not server-side token scoping.

```text
python3 <skill-dir>/scripts/enhance.py --config <private-profile.json> call getWebsite
python3 <skill-dir>/scripts/enhance.py --config <private-profile.json> batch <skill-dir>/templates/site-read-plan.json
```

The batch returns each operation's result and a failure count. On exit 1, retain its structured stdout; it can contain useful successful results and precise failures.

To remove the local workbench, move its directory and unused private profile to your designated Trash. Credential revocation is a separate provider-authorised operation.
