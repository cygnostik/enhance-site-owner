#!/usr/bin/env python3
"""Convert a reviewed local Enhance schema to a NEW JSON candidate.

JSON needs only stdlib; YAML additionally needs the optional PyYAML parser.
This helper makes no network calls and never installs or activates anything.
"""
import argparse
import json
import math
import os
from pathlib import Path
import stat
import sys

METHODS = {'get','post','put','patch','delete','head','options','trace'}
MAX_INPUT = 16 * 1024 * 1024

def inspect_schema(doc):
    if not isinstance(doc, dict) or not str(doc.get('openapi','')).startswith('3.'):
        raise ValueError('Expected an OpenAPI 3 document')
    if not isinstance(doc.get('info'),dict) or not doc['info'].get('version'):
        raise ValueError('Schema version missing')
    if not isinstance(doc.get('paths'),dict) or not doc['paths']:
        raise ValueError('No API paths in candidate')
    identifiers = set()
    for route, item in doc['paths'].items():
        if not isinstance(route,str) or not route.startswith('/') or route.startswith('//') or not isinstance(item,dict):
            raise ValueError('Invalid API path entry')
        for method, operation in item.items():
            if method not in METHODS: continue
            if not isinstance(operation,dict): raise ValueError('Invalid API operation')
            operation_id=operation.get('operationId')
            if not isinstance(operation_id,str) or not operation_id or operation_id in identifiers:
                raise ValueError('Missing or duplicate operationId')
            identifiers.add(operation_id)
    visited=set()
    def refs(value):
        if isinstance(value,(dict,list)):
            if id(value) in visited: return
            visited.add(id(value))
        if isinstance(value,dict):
            if any(type(key) is not str for key in value):
                raise ValueError('Schema mapping keys must be strings; identifiers are not coerced')
            ref=value.get('$ref')
            if ref is not None and (not isinstance(ref,str) or not ref.startswith('#/')):
                raise ValueError('External or invalid schema reference; review separately')
            for child in value.values(): refs(child)
        elif isinstance(value,list):
            for child in value: refs(child)
    refs(doc)
    if not identifiers: raise ValueError('No API operations in candidate')
    return {'version':doc['info']['version'],'paths':len(doc['paths']),'operations':len(identifiers)}

def parse_source(text):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate source mapping key')
            result[key]=value
        return result
    def finite_number(value):
        result=float(value)
        if not math.isfinite(result):raise ValueError('Non-finite source number')
        return result
    def invalid_constant(value):
        raise ValueError('Non-finite source number')
    try:
        return json.loads(text,object_pairs_hook=unique,parse_float=finite_number,parse_constant=invalid_constant)
    except json.JSONDecodeError:
        try: import yaml
        except ImportError as exc:
            raise ValueError('YAML conversion needs optional PyYAML in the maintainer environment; runtime calls do not. No package was installed.') from exc
        class UniqueLoader(yaml.SafeLoader):
            pass
        def mapping(loader,node):
            loader.flatten_mapping(node)
            return unique((loader.construct_object(k,deep=True),loader.construct_object(v,deep=True)) for k,v in node.value)
        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)
        try:return yaml.load(text,Loader=UniqueLoader)
        except (yaml.YAMLError,TypeError) as exc:raise ValueError('Invalid YAML source') from exc


def prepare(source, destination):
    source=Path(source); destination=Path(destination)
    flags=os.O_RDONLY|getattr(os,'O_NONBLOCK',0)
    fd=os.open(source,flags)
    with os.fdopen(fd,'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):raise ValueError('Source must be a regular file')
        raw=f.read(MAX_INPUT+1)
    if len(raw)>MAX_INPUT:raise ValueError('Source exceeds 16 MiB input limit')
    doc=parse_source(raw.decode('utf-8'))
    summary=inspect_schema(doc)
    try: payload=(json.dumps(doc,indent=2,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8')
    except (ValueError,TypeError,RecursionError) as exc: raise ValueError('Candidate must be finite JSON-compatible data') from exc
    inspect_schema(parse_source(payload))
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL
    if hasattr(os,'O_NOFOLLOW'): flags|=os.O_NOFOLLOW
    fd=os.open(destination,flags,0o600)
    with os.fdopen(fd,'wb') as f: f.write(payload)
    return dict(summary,candidate=str(destination),activated=False)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('candidate',type=Path)
    args=parser.parse_args()
    try:
        result=prepare(args.source,args.candidate)
    except (OSError,ValueError,RecursionError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)}),file=sys.stderr)
        return 1
    print(json.dumps(result,indent=2))
    return 0

if __name__=='__main__': sys.exit(main())
