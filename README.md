# Enhance site-owner workbench

Manage your Enhance-hosted websites with a reusable agent skill and local CLI. The workbench covers domains, DNS/TLS, PHP, databases, email, cron, WordPress, persistent applications and backups.

## Get started

Use Python 3.9+ with PyYAML available for the one-time schema setup. Normal CLI calls use only the standard library.

```sh
git clone https://github.com/cygnostik/enhance-site-owner.git
cd enhance-site-owner
python3 scripts/setup_schema.py
python3 scripts/enhance.py ops --search dns
python3 scripts/enhance.py describe getWebsite
```

Follow [first installation](references/first-install.md) to configure your provider-issued customer credentials in a private profile. Then:

```sh
python3 scripts/enhance.py --config /path/to/private-profile.json call getWebsite
python3 scripts/enhance.py --config /path/to/private-profile.json batch templates/site-read-plan.json
```

[SKILL.md](SKILL.md) is the agent entrypoint. Keep this directory as a standalone workbench or install it in your agent's skills directory. No administrator skill or running helper service is needed.

## Using the client

- `ops` searches the catalog; `describe` explains an operation's exact inputs.
- `call` uses typed parameters and private request files. Changes are planned locally until you explicitly add `--apply`.
- `batch` combines ordinary read operations and returns concise structured results.
- `refresh-status` checks whether the API references need review after 30 days. It does not schedule a background updater.

See [CLI usage](references/cli-usage.md), [site operations](references/site-operations.md), [permissions](references/permissions.md) and [verification scope](references/verification.md).

Your credential's Enhance permissions determine access. A site profile adds a local guard; it does not reduce an administrator token's authority. Use customer-level credentials and have your provider verify the allowed and denied scope at Enhance.

## API credentials

Use the complete `token-id_secret` value, not the secret alone. [Authentication instructions](references/authentication.md) include the private-output composition helper. See [changes in 0.1.1](CHANGELOG.md).

## Tests

After schema setup:

```sh
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```

The official API schema is fetched directly from its publisher, not committed here. See [rights and upstream material](NOTICE.md).
