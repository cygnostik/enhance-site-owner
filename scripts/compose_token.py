#!/usr/bin/env python3
"""Assemble a complete Enhance bearer into a new private file; no network."""
import argparse
import json
import re
import sys
import enhance as e

UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'


def compose(response):
    if not isinstance(response, dict):
        raise e.CLIError('Expected an access-token creation response object.')
    identifier, secret = response.get('id'), response.get('unencryptedToken')
    if not isinstance(identifier, str) or not re.fullmatch(UUID, identifier):
        raise e.CLIError('Token response requires an exact hyphenated UUID id.')
    if not isinstance(secret, str) or not secret or len(secret) > 8192 or not re.fullmatch(r'[!-~]+', secret):
        raise e.CLIError('Token response requires nonempty, whitespace-free ASCII secret material.')
    prefix = identifier + '_'
    if secret.startswith(prefix):
        if len(secret) == len(prefix):
            raise e.CLIError('Complete token is missing its secret component.')
        return secret
    if re.match(UUID + '_', secret):
        raise e.CLIError('Complete token prefix conflicts with response id.')
    return prefix + secret


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--response', required=True, help='Private createAccessToken JSON response file')
    parser.add_argument('--output', required=True, help='New private file for the complete bearer value')
    args = parser.parse_args()
    try:
        token = compose(e.load_json(args.response, limit=65536))
        with e.reserve_output(args.output) as output:
            output.write((token + '\n').encode('ascii'))
        print(json.dumps({'output_written': True, 'format': 'token-id_secret'}))
        return 0
    except (e.CLIError, OSError):
        print(json.dumps({'error': 'Cannot compose token: check response fields and use a new writable output file. Secret values are withheld.'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
