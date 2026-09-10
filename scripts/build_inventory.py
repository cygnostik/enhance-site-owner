"""Offline request construction coverage with literal SYNTHETIC data only.

This constructs requests but NEVER executes a transport or contacts Enhance.
"""
import collections
import json
import tempfile
from pathlib import Path
import enhance as e

SYNTHETIC_UUID='11111111-1111-4111-8111-111111111111'


def sample(catalog,schema,depth=0):
    schema=catalog.resolve(schema)
    if depth>40:
        raise e.CLIError('Synthetic generator nesting limit.')
    if 'enum' in schema:
        return schema['enum'][0]
    if 'oneOf' in schema:
        return sample(catalog,schema['oneOf'][0],depth+1)
    if 'allOf' in schema:
        values=[sample(catalog,part,depth+1) for part in schema['allOf']]
        if all(isinstance(x,dict) for x in values):
            result={}
            for value in values:
                result.update(value)
            return result
        return values[0]
    kind=schema.get('type')
    if kind=='object' or 'properties' in schema:
        return {key:sample(catalog,schema.get('properties',{}).get(key,{}),depth+1) for key in schema.get('required',[])}
    if kind=='array':
        return [sample(catalog,schema.get('items',{}),depth+1)]
    if kind=='boolean':
        return False
    if kind=='integer':
        return max(1,schema.get('minimum',1))
    if kind=='number':
        return max(1,schema.get('minimum',1))
    if kind=='string':
        return {'uuid':SYNTHETIC_UUID,'date':'2026-01-01','date-time':'2026-01-01T00:00:00Z',
                'ip':'192.0.2.1','ipv4':'192.0.2.1','ipv6':'2001:db8::1','email':'synthetic@example.invalid',
                'hostname':'synthetic.example.invalid','domain':'synthetic.example.invalid'}.get(schema.get('format'),'SYNTHETIC')
    return {}  # Unconstrained JSON schema: explicitly synthetic empty object.


def construction_inventory(catalog=None):
    c=catalog or e.Catalog()
    profile=e.Profile({'base_url':'https://example.invalid/api','scope':'admin'})
    rows=[]
    with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp:
        png=Path(tmp)/'synthetic.png'
        png.write_bytes(b'\x89PNG\r\n\x1a\nSYNTHETIC-NOT-A-VALID-IMAGE')
        gzip=Path(tmp)/'synthetic.tar.gz'
        gzip.write_bytes(b'\x1f\x8bSYNTHETIC-NOT-A-VALID-ARCHIVE')
        for name,op in sorted(c.operations.items()):
            row={'operationId':name,'method':op['method'],'path':op['path'],'auth_support':e.auth_support(op)}
            try:
                c.describe(name)
                params={p['name']:sample(c,p.get('schema',{})) for p in op['parameters'] if p['name'].lower()!='authorization'}
                content=c.resolve(op.get('requestBody',{})).get('content',{})
                body=e.UNSET
                files=None
                raw=None
                if 'application/json' in content:
                    body=sample(c,content['application/json'].get('schema',{}))
                elif 'multipart/form-data' in content:
                    props=c.resolve(content['multipart/form-data'].get('schema',{})).get('properties',{})
                    files={k:str(png) for k,v in props.items() if c.resolve(v).get('format')=='binary'}
                    body={k:sample(c,v) for k,v in props.items() if k not in files}
                elif 'application/gzip' in content:
                    raw=str(gzip)
                r=e.prepare(c,profile,name,params,body,files,raw)
                row.update(constructed=True,body_media=list(content),body_bytes=len(r.body or b''),guidance=r.guidance)
            except e.CLIError as err:
                row.update(constructed=False,code=err.code,reason=str(err))
            rows.append(row)
    return {'fixture_notice':'ALL request inputs are SYNTHETIC. This only constructs requests, never sends them.',
            'schema_version':c.version,'total':len(rows),'constructed':sum(r['constructed'] for r in rows),
            'not_constructed':sum(not r['constructed'] for r in rows),
            'method_counts':dict(collections.Counter(op['method'] for op in c.operations.values())),
            'auth_counts':dict(collections.Counter(e.auth_support(op) for op in c.operations.values())),
            'media_counts':dict(collections.Counter(t for op in c.operations.values() for t in c.resolve(op.get('requestBody',{})).get('content',{}))),
            'operations':rows}


if __name__=='__main__':
    result=construction_inventory()
    (e.ROOT/'evidence').mkdir(parents=True, exist_ok=True)
    (e.ROOT/'evidence'/'operation-coverage.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='operations'},indent=2))
    print(json.dumps([row for row in result['operations'] if not row['constructed']],indent=2))
