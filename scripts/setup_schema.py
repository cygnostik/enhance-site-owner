#!/usr/bin/env python3
"""First install only: obtain the official schema without hosting credentials."""
import argparse
import http.client
import json
import os
from pathlib import Path
import ssl
import sys
import enhance
import prepare_schema

ROOT=Path(__file__).resolve().parent
SOURCE='https://apidocs.enhance.com/spec/oas3-api.yaml'

def install(document,destination,expected_version):
    summary=prepare_schema.inspect_schema(document)
    if summary['version']!=expected_version:
        raise ValueError('Published schema version differs from this release; review the update before installation')
    enhance.Catalog(document=document)
    payload=(json.dumps(document,indent=2,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8')
    if len(payload)>enhance.MAX_SCHEMA_BYTES:raise ValueError('Converted schema exceeds runtime limit')
    enhance.Catalog(document=enhance.parse_json(payload))
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as f:f.write(payload)
    return dict(summary,installed=True,source=SOURCE)

def fetch():
    conn=http.client.HTTPSConnection('apidocs.enhance.com',timeout=30,context=ssl.create_default_context())
    try:
        conn.request('GET','/spec/oas3-api.yaml',headers={'Accept':'application/yaml, text/yaml, text/plain','Accept-Encoding':'identity','User-Agent':'EnhanceSkillSchemaSetup/0.1'})
        response=conn.getresponse()
        if response.status!=200:raise ValueError('Official schema fetch failed; redirects are not followed')
        raw=response.read(prepare_schema.MAX_INPUT+1)
        if len(raw)>prepare_schema.MAX_INPUT:raise ValueError('Official schema exceeds input limit')
        return prepare_schema.parse_source(raw.decode('utf-8'))
    finally:conn.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    destination=ROOT/'assets/openapi.json'
    try:
        if destination.exists():raise ValueError('Schema already installed; use the review/candidate workflow for updates')
        metadata=enhance.load_json(ROOT/'assets/freshness.json')
        result=install(fetch(),destination,metadata['schema_version'])
    except (OSError,ValueError,KeyError,RecursionError,http.client.HTTPException,enhance.CLIError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)}),file=sys.stderr);return 1
    print(json.dumps(result,indent=2));return 0

if __name__=='__main__':sys.exit(main())
