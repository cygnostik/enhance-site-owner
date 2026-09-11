# Complete API token format

Organisation access tokens use `Authorization: Bearer <token-id>_<secret>`. The value includes both the token UUID and its secret, separated by one underscore. The CLI adds `Bearer` for you: your credential file contains only the complete value.

The `createAccessToken` response returns `id` and `unencryptedToken` separately. The latter is the secret component, not a complete API key. Sending it alone can produce HTTP 403 with `{"code":"unauthorized"}` even for a correctly scoped token. Check composition before changing roles, rotating keys or declaring a token revoked.

## From a private creation response

Through `terminal`, from the repository/skill root:

```text
python3 scripts/compose_token.py --response /private/token-response.json --output /private/enhance.token
```

The helper joins the fields, preserves an already complete matching value, rejects conflicting IDs and writes a new private file without printing the credential. It never contacts Enhance, changes permissions or overwrites an existing file. Use a private destination directory; on Windows restrict its ACL as well as the file's.

Set `token_file` in your private profile to the output path. Relative paths resolve from the profile file, not your current directory.

## If your provider sent a complete key

Save the entire value as supplied; do not split it or run a second prefix step. No separate secret is needed. Do not paste it into command arguments, repositories or diagnostic output.

## Verify scope

Check an ordinary read for an authorised website with the actual customer credential. Customer-organisation `SuperAdmin` means administration within that customer account, not provider administration; future services added to the same customer can also fall within its scope. A local website guard is additional accident prevention, not server-side scope enforcement. Have the provider verify denied access directly at Enhance.
