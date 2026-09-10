"""Tests use the official local schema and explicitly SYNTHETIC fixtures only."""
import contextlib
import datetime
import http.server
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
import enhance as e

class CatalogTests(unittest.TestCase):
    def test_official_catalog_and_filters(self):
        c = e.Catalog()
        self.assertEqual(c.list_ops()['total'], 482)
        got = c.list_ops(method='GET')
        self.assertEqual(got['matched'], 201)
        self.assertEqual(len(got['operations']), 201)
        self.assertEqual(c.operations['orchdVersion']['path'], '/version')
        self.assertEqual(c.list_ops(search='orchdVersion')['matched'], 1)
        self.assertTrue(c.list_ops(tag='websites')['matched'] > 0)

# Every UUID/token/domain below is a literal SYNTHETIC fixture, never a live credential.
ORG = '11111111-1111-4111-8111-111111111111'
SITE = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'
DOMAIN = '44444444-4444-4444-8444-444444444444'

class ConstructionTests(unittest.TestCase):
    def test_org_request_inherited_refs_and_query_array(self):
        c = e.Catalog()
        p = e.Profile({'base_url':'https://example.invalid/api', 'scope':'organisation', 'org_id':ORG, 'server_id':OTHER})
        r = e.prepare(c, p, 'getWebsites', {'offset':0,'limit':20,'roles':['application','backup'],'servers':[OTHER]})
        self.assertEqual(r.method, 'GET')
        self.assertIn('/api/orgs/' + ORG + '/websites?', r.url)
        self.assertIn('offset=0', r.url)
        self.assertIn('roles=application%2Cbackup', r.url)
        self.assertEqual(r.params['org_id'], ORG)
        with self.assertRaises(e.CLIError):
            e.prepare(c,p,'getWebsites',{'org_id':OTHER})

    def test_site_rejects_orgwide_and_unanchored(self):
        c = e.Catalog()
        p = e.Profile({'base_url':'https://example.invalid/api','scope':'site','org_id':ORG,'website_id':SITE})
        for name in ['getWebsites', 'getServers', 'getLogin']:
            with self.subTest(name=name), self.assertRaises(e.CLIError):
                e.prepare(c,p,name)
        r = e.prepare(c,p,'getWebsite')
        self.assertIn('/websites/' + SITE, r.url)
        with self.assertRaises(e.CLIError):
            e.prepare(c,p,'getWebsite',{'website_id':OTHER})

TOKEN = 'SYNTHETIC-test-bearer-not-a-real-credential'

@contextlib.contextmanager
def local_server(responses):
    class Handler(http.server.BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get('Content-Length','0')))
            self.server.records.append((self.command,self.path,dict(self.headers),body))
            status, headers, payload = self.server.responses.pop(0) if self.server.responses else (500,{},b'SYNTHETIC unexpected request')
            self.send_response(status)
            for key,value in headers.items():
                self.send_header(key,value)
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError):
                pass
        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_request
        def log_message(self,*args):
            pass  # Do not record any credentials or URLs, even synthetic ones.
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.records=[]
    server.responses=list(responses)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        p=e.Profile({'base_url':'http://127.0.0.1:'+str(server.server_port)+'/api','scope':'admin','org_id':ORG,'website_id':SITE},_allow_loopback=True)
        p.token=TOKEN
        yield p,server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()

class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c=e.Catalog()

    def test_redirect_refused_without_second_request_or_url_leak(self):
        for status in (301,302,303,307,308):
            with self.subTest(status=status), local_server([(status,{'Location':'/api/secret?token='+TOKEN},b'')]) as (p,s):
                r=e.prepare(self.c,p,'getServers')
                with self.assertRaises(e.CLIError) as err:
                    e.execute(r,p)
                self.assertEqual(err.exception.code,'redirect')
                self.assertNotIn(TOKEN,str(err.exception))
                self.assertEqual(len(s.records),1)

    def test_success_recursively_redacts_exact_token_and_credentials(self):
        payload={'items':[{'id':'synthetic','password':'not-real','detail':'prefix '+TOKEN,'nested':{'apiKey':'not-real'}}], 'total':7}
        with local_server([(200,{'Content-Type':'application/json'},json.dumps(payload).encode())]) as (p,s):
            result=e.execute(e.prepare(self.c,p,'getServers'),p)
            text=json.dumps(result)
            self.assertNotIn(TOKEN,text)
            self.assertNotIn('not-real',text)
            self.assertEqual(result['data']['total'],7)
            self.assertEqual(s.records[0][2]['Authorization'],'Bearer '+TOKEN)

    def test_unauthorized_json_string_is_not_echoed(self):
        with local_server([(401,{'Content-Type':'application/json'},json.dumps('error '+TOKEN+' password=not-real').encode())]) as (p,s):
            with self.assertRaises(e.CLIError) as err:
                e.execute(e.prepare(self.c,p,'getServers'),p)
            self.assertEqual(err.exception.status,401)
            self.assertNotIn(TOKEN,str(err.exception))
            self.assertNotIn('not-real',str(err.exception))

    def test_mutation_is_local_by_default(self):
        with local_server([]) as (p,s):
            r=e.prepare(self.c,p,'deleteWebsite')
            result=e.execute(r,p)
            self.assertTrue(result['dry_run'])
            self.assertFalse(result['sent'])
            self.assertEqual(s.records,[])

    def test_plain_text_version_exception(self):
        with local_server([(200,{'Content-Type':'text/plain'},b'12.25.8')]) as (p,s):
            result=e.execute(e.prepare(self.c,p,'orchdVersion'),p)
            self.assertEqual(result['data'],'12.25.8')

    def test_identical_response_duplicate_keys_are_accepted(self):
        # Synthetic minimal form of the observed vendor duplicate phpVersion.
        payload=b'{"phpVersion":"8.4","phpVersion":"8.4"}'
        with local_server([(200,{'Content-Type':'application/json'},payload)]) as (p,s):
            result=e.execute(e.prepare(self.c,p,'getWebsite'),p)
            self.assertEqual(result['data'],{'phpVersion':'8.4'})

    def test_conflicting_response_duplicates_remain_rejected(self):
        payloads=[b'{"x":"one","x":"two"}',b'{"x":true,"x":1}',
                  b'{"x":{"n":true},"x":{"n":1}}']
        for payload in payloads:
            with self.subTest(payload=payload), local_server([(200,{'Content-Type':'application/json'},payload)]) as (p,s):
                with self.assertRaises(e.CLIError):
                    e.execute(e.prepare(self.c,p,'getWebsite'),p)
        # Even identical duplicate keys are still invalid in local inputs.
        with self.assertRaises(e.CLIError):
            e.parse_json('{"phpVersion":"8.4","phpVersion":"8.4"}')

    def test_multipart_integration_and_explicit_apply(self):
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp, local_server([(200,{},b'')]) as (p,s):
            image=Path(tmp)/'synthetic.png'
            image.write_bytes(b'\x89PNG\r\n\x1a\nSYNTHETIC-NOT-A-VALID-IMAGE')
            r=e.prepare(self.c,p,'setOrgAvatar',files={'avatar':str(image)})
            out=e.execute(r,p,apply=True)
            self.assertEqual(out['status'],200)
            self.assertIn(b'name="avatar"; filename="upload.png"',s.records[0][3])
            self.assertIn(b'SYNTHETIC-NOT-A-VALID-IMAGE',s.records[0][3])
            self.assertTrue(s.records[0][2]['Content-Type'].startswith('multipart/form-data; boundary='))

    def test_batch_preflight_and_partial_errors(self):
        with local_server([(200,{'Content-Type':'application/json'},b'{"items":[],"total":0}'),(403,{},b'SYNTHETIC SECRET')]) as (p,s):
            with self.assertRaises(e.CLIError):
                e.batch(self.c,p,[{'operationId':'getServers'},{'operationId':'deleteWebsite'}])
            self.assertEqual(s.records,[])
            out=e.batch(self.c,p,[{'operationId':'getServers'},{'operationId':'getWebsites'}])
            self.assertEqual((out['requested'],out['succeeded'],out['failed']),(2,1,1))
            self.assertEqual(len(out['results']),2)
            self.assertNotIn('SYNTHETIC SECRET',json.dumps(out))

class NegativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c=e.Catalog()

    def admin(self):
        return e.Profile({'base_url':'https://example.invalid/api','scope':'admin','org_id':ORG,'website_id':SITE})

    def site(self,domains=None):
        return e.Profile({'base_url':'https://example.invalid/api','scope':'site','org_id':ORG,'website_id':SITE,'domain_ids':domains or []})

    def test_type_enum_and_unknown_parameter_negatives(self):
        for params in ({'offset':True},{'limit':'3'},{'isSuspended':'false'},{'roles':'application'},
                       {'roles':['unlisted-role']},{'servers':['not-a-uuid']},{'org_id':'{'+ORG+'}'},
                       {'sortBy':'not-in-enum'},{'recursive':True},{'unexpected':TOKEN}):
            with self.subTest(params=params),self.assertRaises(e.CLIError):
                e.prepare(self.c,self.admin(),'getWebsites',params)

    def test_path_injection_and_deep_encoding_chains(self):
        for name in ('..','.', 'a/b','a\\b','a?secret=x','a#x','a\nX-Test: x','%2e%2e','%252e%252e',
                     '%252froot','%255croot','%250aInjected','%FF','%','%25'*20):
            with self.subTest(name=name),self.assertRaises(e.CLIError):
                e.prepare(self.c,self.admin(),'getSetting',{'name':name})
        r=e.prepare(self.c,self.admin(),'getSetting',{'name':'literal @ ü'})
        self.assertTrue(r.url.endswith('/literal%20%40%20%C3%BC'))

    def test_query_newline_injection_and_safe_encoding(self):
        with self.assertRaises(e.CLIError):
            e.prepare(self.c,self.admin(),'getWebsites',{'search':'x\r\nAuthorization: injected'})
        r=e.prepare(self.c,self.admin(),'getWebsites',{'search':'name&limit=100?token=not-real'})
        from urllib.parse import parse_qs,urlsplit
        self.assertEqual(parse_qs(urlsplit(r.url).query),{'search':['name&limit=100?token=not-real']})

    def test_base_url_injection_https_and_no_loopback_dns(self):
        for url in ('http://example.invalid/api','https://name:password@example.invalid/api','https://example.invalid/api?q=x',
                    'https://example.invalid/api#x','https://example.invalid/api?','https://example.invalid/api#',
                    'https://example.invalid/api/%252e%252e','https://example.invalid/api/%252f','https://example.invalid/\napi',
                    'https://example.invalid\\@evil.invalid/','https://example.invalid:0/api','https://example.invalid:99999/api'):
            with self.subTest(url=url),self.assertRaises(e.CLIError):
                e.Profile({'base_url':url,'scope':'admin'})
        with self.assertRaises(e.CLIError):
            e.Profile({'base_url':'http://localhost/api','scope':'admin'},_allow_loopback=True)
        self.assertEqual(e.Profile({'base_url':'http://127.0.0.1/api','scope':'admin'},_allow_loopback=True).base_url,'http://127.0.0.1/api')

    def test_domain_only_explicit_allowlist_and_site_anchor(self):
        names=[n for n,op in self.c.operations.items() if op['path']=='/v2/domains/{domain_id}/modsec_status' and op['method']=='GET']
        self.assertEqual(len(names),1)
        with self.assertRaises(e.CLIError):
            e.prepare(self.c,self.site(),names[0],{'domain_id':DOMAIN})
        r=e.prepare(self.c,self.site([DOMAIN]),names[0],{'domain_id':DOMAIN})
        self.assertIn(DOMAIN,r.url)
        with self.assertRaises(e.CLIError):
            e.prepare(self.c,self.site([DOMAIN]),names[0],{'domain_id':OTHER})

    def test_body_scope_mismatch_and_ambiguous_nested_owner(self):
        for body in ({'orgId':OTHER},{'websiteId':OTHER},{'targetWebsiteId':OTHER},{'domainId':OTHER},
                     {'nested':{'org':{'id':OTHER}}},{'websites':[OTHER]},{'owner':{'id':OTHER}},
                     {'target':{'website':OTHER}},{'serverId':OTHER}):
            with self.subTest(body=body),self.assertRaises(e.CLIError):
                e.prepare(self.c,self.site(),'updateWebsite',body=body)

    def test_root_only_and_no_body_anchor_escape(self):
        for name in ('getServers','getSettings','getLogin','getWebsites','createWebsite','setWebsiteBackupsDisabledStatus'):
            with self.subTest(name=name),self.assertRaises(e.CLIError):
                e.prepare(self.c,self.site(),name,body={'websiteId':SITE})
        for name in e.SAFE_METADATA:
            r=e.prepare(self.c,self.site(),name)
            self.assertEqual(r.method,'GET')
        org=e.Profile({'base_url':'https://example.invalid/api','scope':'organisation','org_id':ORG})
        self.assertIn(SITE,e.prepare(self.c,org,'getWebsite',{'website_id':SITE}).url)
        with self.assertRaises(e.CLIError):
            e.prepare(self.c,org,'getWebsites',{'recursion':'infinite'})

    def test_auth_cookie_only_and_authorization_override(self):
        for name in ('verify2FA','resendPin'):
            with self.subTest(name=name),self.assertRaises(e.CLIError) as err:
                e.prepare(self.c,self.admin(),name)
            self.assertEqual(err.exception.code,'unsupported')
        with self.assertRaises(e.CLIError):
            e.prepare(self.c,self.admin(),'GetEmailPublicIp',{'Authorization':TOKEN,'Address':'synthetic@example.invalid','Password':'SYNTHETIC'})
        with self.assertRaises(e.CLIError):
            e.cli_params(self.c,'GetEmailPublicIp',['Password='+TOKEN],None)

    def test_json_schema_subset_and_closed_properties(self):
        schema={'type':'object','required':['id','enabled'],'additionalProperties':False,
                'properties':{'id':{'type':'string','format':'uuid'},'enabled':{'type':'boolean'},
                              'count':{'type':'integer','minimum':0},'mode':{'type':'string','enum':['safe']}}}
        e.validate({'id':ORG,'enabled':False,'count':0,'mode':'safe'},schema,self.c)
        for data in ({'id':ORG},{'id':ORG,'enabled':False,'surprise':TOKEN},{'id':ORG,'enabled':0},
                     {'id':ORG,'enabled':False,'count':-1},{'id':ORG,'enabled':False,'count':True},
                     {'id':ORG,'enabled':False,'mode':'unsafe'}):
            with self.subTest(data=data),self.assertRaises(e.CLIError):
                e.validate(data,schema,self.c)
        e.validate({'x':1},{'allOf':[{'type':'object','required':['x']},{'type':'object','properties':{'x':{'type':'integer'}}}]},self.c)
        e.validate(1,{'oneOf':[{'type':'integer'},{'type':'string'}]},self.c)
        with self.assertRaises(e.CLIError):
            e.validate(True,{'oneOf':[{'type':'integer'},{'type':'string'}]},self.c)

    def test_body_required_content_restrictions_and_json_types(self):
        with self.assertRaises(e.CLIError):
            e.prepare(self.c,self.admin(),'createWebsite')
        with self.assertRaises(e.CLIError):
            e.prepare(self.c,self.admin(),'createWebsite',body={})
        with self.assertRaises(e.CLIError):
            e.prepare(self.c,self.admin(),'createWebsite',body={'domain':'synthetic.example.invalid','subscriptionId':True})
        r=e.prepare(self.c,self.admin(),'createWebsite',body={'domain':'synthetic.example.invalid'})
        self.assertEqual(json.loads(r.body),{'domain':'synthetic.example.invalid'})
        self.assertEqual(r.headers['Content-Type'],'application/json')
        with self.assertRaises(e.CLIError):
            e.prepare(self.c,self.admin(),'getServers',body={})

    def test_request_files_gzip_and_multipart_property_validation(self):
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp:
            path=Path(tmp)/'synthetic.tar.gz'
            path.write_bytes(b'\x1f\x8bSYNTHETIC')
            r=e.prepare(self.c,self.admin(),'uploadWebsiteBackup',raw_file=path)
            self.assertEqual(r.body,path.read_bytes())
            self.assertEqual(r.headers['Content-Type'],'application/gzip')
            with self.assertRaises(e.CLIError):
                e.prepare(self.c,self.admin(),'setOrgAvatar',files={'unknown':str(path)})
            with self.assertRaises(e.CLIError):
                e.prepare(self.c,self.admin(),'setOrgAvatar',body={'avatar':'not-a-file'})
            path.write_bytes(b'not gzip')
            with self.assertRaises(e.CLIError):
                e.prepare(self.c,self.admin(),'uploadWebsiteBackup',raw_file=path)

    def test_credential_file_env_sources_and_permissions(self):
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp:
            tmp=Path(tmp)
            f=tmp/'synthetic.env'
            f.write_text('# SYNTHETIC credentials only\nexport EXAMPLE_TOKEN="'+TOKEN+'"\n')
            f.chmod(0o600)
            p=e.Profile({'base_url':'https://example.invalid/api','scope':'admin','token_env_file':'synthetic.env','token_env_key':'EXAMPLE_TOKEN'},tmp)
            self.assertEqual(p.read_token(),TOKEN)
            f.chmod(0o644)
            with self.assertRaises(e.CLIError):
                e.private_text(f)
            f.chmod(0o600)
            f.write_text(TOKEN+'\n')
            p=e.Profile({'base_url':'https://example.invalid','scope':'admin','token_file':str(f)})
            self.assertEqual(p.read_token(),TOKEN)
            link=tmp/'link'
            link.symlink_to(f)
            with self.assertRaises(e.CLIError):
                e.private_text(link)
            with mock.patch.dict(os.environ,{'SYNTHETIC_ENV':TOKEN}):
                p=e.Profile({'base_url':'https://example.invalid','scope':'admin','token_env':'SYNTHETIC_ENV'})
                self.assertEqual(p.read_token(),TOKEN)
            with mock.patch.dict(os.environ,{'SYNTHETIC_ENV':TOKEN+'\nInjected'}):
                p=e.Profile({'base_url':'https://example.invalid','scope':'admin','token_env':'SYNTHETIC_ENV'})
                with self.assertRaises(e.CLIError):
                    p.read_token()
            with self.assertRaises(e.CLIError):
                e.Profile({'base_url':'https://example.invalid','scope':'admin','token_env':'ONE','token_file':'two'})

    def test_exact_token_redaction_inside_error_like_nested_json(self):
        data={'message':json.dumps({'password':'SYNTHETIC-HIDDEN','detail':TOKEN}),TOKEN:'prefix '+TOKEN,
              'settings':[{'name':'smtp_password','value':'SYNTHETIC-HIDDEN'}], 'pem':'-----BEGIN PRIVATE KEY-----\nSYNTHETIC-HIDDEN'}
        text=json.dumps(e.redact(data,TOKEN))
        self.assertNotIn(TOKEN,text)
        self.assertNotIn('SYNTHETIC-HIDDEN',text)

    def test_external_refs_rejected_and_path_parameter_override(self):
        doc={'openapi':'3.0.3','info':{'version':'SYNTHETIC'},'paths':{'/test/{id}':{'parameters':[{'$ref':'#/components/parameters/Id'}],
              'get':{'operationId':'syntheticGet','parameters':[{'name':'id','in':'path','required':True,'schema':{'type':'integer'}}],'responses':{'200':{'description':'SYNTHETIC'}}}}},
              'components':{'parameters':{'Id':{'name':'id','in':'path','required':True,'schema':{'type':'string'}}}}}
        c=e.Catalog(document=doc)
        self.assertEqual(c.describe('syntheticGet')['parameters'][0]['schema']['type'],'integer')
        self.assertTrue(e.prepare(c,self.admin(),'syntheticGet',{'id':3}).url.endswith('/test/3'))
        for ref in ('https://example.invalid/spec.json#/x','file:///private/spec','../spec.json#/x'):
            bad=json.loads(json.dumps(doc))
            bad['paths']['/test/{id}']['parameters'][0]['$ref']=ref
            with self.assertRaises(e.CLIError):
                e.Catalog(document=bad)

    def test_public_security_never_sends_bearer(self):
        doc={'openapi':'3.0.3','info':{'version':'SYNTHETIC'},'paths':{'/public':{'get':{'operationId':'syntheticPublic','security':[],
             'responses':{'200':{'description':'SYNTHETIC','content':{'application/json':{'schema':{'type':'object'}}}}}}}}}
        c=e.Catalog(document=doc)
        with local_server([(200,{'Content-Type':'application/json'},b'{}')]) as (p,s):
            e.execute(e.prepare(c,p,'syntheticPublic'),p)
            self.assertNotIn('Authorization',s.records[0][2])

    def test_private_output_exclusive_and_sensitive_preflight(self):
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp,local_server([(200,{'Content-Type':'application/json'},json.dumps('SYNTHETIC LOG '+TOKEN).encode())]) as (p,s):
            path=Path(tmp)/'new-private-output'
            r=e.prepare(self.c,p,'getWebsitePhpErrorLog')
            self.assertTrue(e.execute(r,p)['dry_run'])
            self.assertEqual(s.records,[])
            with self.assertRaises(e.CLIError):
                e.execute(r,p,apply=True)
            self.assertEqual(s.records,[])
            result=e.execute(r,p,apply=True,output=path)
            self.assertTrue(result['output_written'])
            self.assertIn(TOKEN,path.read_text())  # Explicit raw output intentionally contains secrets.
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with self.assertRaises(e.CLIError):
                e.execute(r,p,apply=True,output=path)
            self.assertEqual(len(s.records),1)

    def test_binary_download_output_and_size_cap(self):
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp,local_server([(200,{'Content-Type':'application/gzip'},b'\x1f\x8bSYNTHETIC')]) as (p,s):
            r=e.prepare(self.c,p,'downloadWebsiteBackup')
            out=e.execute(r,p,apply=True,output=Path(tmp)/'new.gz')
            self.assertEqual(out['status'],200)
        with local_server([(200,{'Content-Type':'application/json'},b'{"x":"too big"}')]) as (p,s):
            with self.assertRaises(e.CLIError) as err:
                e.execute(e.prepare(self.c,p,'getServers'),p,max_bytes=2)
            self.assertEqual(err.exception.code,'size')

    def test_actionable_status_errors_no_mutation_retries(self):
        for status in (401,403,404,429,500):
            with self.subTest(status=status),local_server([(status,{},b'SYNTHETIC-RAW-ERROR')]) as (p,s):
                with self.assertRaises(e.CLIError) as err:
                    e.execute(e.prepare(self.c,p,'deleteWebsite'),p,apply=True)
                self.assertEqual(err.exception.status,status)
                self.assertNotIn('SYNTHETIC-RAW-ERROR',str(err.exception))
                self.assertEqual(len(s.records),1)

    def test_batch_preflight_late_invalid_types_and_views(self):
        bad_entries=[{'operationId':'getWebsites','params':{'limit':True}},
                     {'operationId':'getWebsites','params':{'unknown':TOKEN}},
                     {'operationId':'getWebsitePhpErrorLog'},
                     {'operationId':'getServers','view':{'select':'not-a-list'}},
                     {'operationId':'getServers','output':'not-allowed'}]
        for bad in bad_entries:
            with self.subTest(bad=bad),local_server([]) as (p,s):
                with self.assertRaises(e.CLIError):
                    e.batch(self.c,p,[{'operationId':'getServers'},bad])
                self.assertEqual(s.records,[])
        with self.assertRaises(e.CLIError):
            e.batch(self.c,self.admin(),[{'operationId':'getServers'}]*51)

    def test_batch_header_auth_prerequisite_all_before_network(self):
        with local_server([]) as (p,s):
            p.token=None
            with self.assertRaises(e.CLIError):
                e.batch(self.c,p,[{'operationId':'orchdVersion'},{'operationId':'GetEmailPublicIp','params':{'Address':'synthetic@example.invalid','Password':'SYNTHETIC'}}])
            self.assertEqual(s.records,[])

    def test_unexpected_non_json_text_never_printed(self):
        with local_server([(200,{'Content-Type':'text/plain'},b'SYNTHETIC-RAW-LOG')]) as (p,s):
            with self.assertRaises(e.CLIError) as err:
                e.execute(e.prepare(self.c,p,'getServers'),p)
            self.assertNotIn('SYNTHETIC-RAW-LOG',str(err.exception))
        with local_server([(200,{'Content-Type':'application/json'},json.dumps('password=SYNTHETIC-RAW').encode())]) as (p,s):
            with self.assertRaises(e.CLIError) as err:
                e.execute(e.prepare(self.c,p,'getServers'),p)
            self.assertNotIn('SYNTHETIC-RAW',str(err.exception))

class DiagnosticsTests(unittest.TestCase):
    def test_projection_count_filter_retains_total(self):
        original={'items':[{'id':1,'status':'active'},{'id':2,'status':'inactive'}],'total':15,'nested':{'total':20},'has_more':True}
        result=e.project(original,{'filter':{'/status':'active'},'select':['/id']})
        self.assertEqual(result['items'],[{'/id':1}])
        self.assertEqual((result['returned_count'],result['matched_count']),(2,1))
        self.assertEqual(result['response_metadata']['total'],15)
        self.assertEqual(result['response_metadata']['nested']['total'],20)
        self.assertTrue(result['response_metadata']['has_more'])
        self.assertEqual(len(original['items']),2)
        counted=e.project(original,{'count':True})
        self.assertNotIn('items',counted)
        self.assertEqual(counted['returned_count'],2)
        with self.assertRaises(e.CLIError):
            e.project(original,{'select':['/missing']})

    def test_freshness_date_only_boundary(self):
        c=e.Catalog()
        for day,due in ((29,False),(30,True),(31,True)):
            result=e.refresh_status(c,today=datetime.date(2026,9,10)+datetime.timedelta(days=day))
            self.assertEqual(result['age_days'],day)
            self.assertEqual(result['review_due'],due)
        self.assertTrue(e.refresh_status(c,today=datetime.date(2026,9,9))['future_date'])

    def test_schema_diff_add_remove_param_content_security(self):
        c=e.Catalog()
        doc=json.loads(json.dumps(c.document))
        del doc['paths']['/status']['get']
        doc['paths']['/synthetic']={'get':{'operationId':'syntheticAdded','responses':{'200':{'description':'SYNTHETIC'}}}}
        doc['components']['parameters']['Limit']['schema']['minimum']=0
        doc['paths']['/version']['get']['security']=[{'bearerAuth':[]}]
        doc['paths']['/orgs/{org_id}/avatar']['put']['requestBody']['content']['application/x-synthetic']={'schema':{'type':'string'}}
        diff=e.schema_diff(c,e.Catalog(document=doc))
        self.assertEqual(diff['added'],['syntheticAdded'])
        self.assertEqual(diff['removed'],['orchdStatus'])
        by_id={x['operationId']:x for x in diff['changed']}
        self.assertIn('security',by_id['orchdVersion']['changed_fields'])
        self.assertIn('parameters',by_id['getWebsites']['changed_fields'])
        self.assertIn('query:limit',by_id['getWebsites']['parameters']['changed'])
        self.assertIn('requestBody',by_id['setOrgAvatar']['changed_fields'])
        self.assertEqual(e.schema_diff(c,c)['changed_count'],0)

    def test_full_schema_inventory_and_all_media_construction(self):
        import build_inventory
        result=build_inventory.construction_inventory()
        self.assertEqual(result['total'],482)
        self.assertEqual(result['method_counts']['GET'],201)
        self.assertEqual(result['media_counts'],{'application/json':179,'multipart/form-data':9,'application/gzip':1})
        bad={x['operationId'] for x in result['operations'] if not x['constructed']}
        self.assertEqual(bad,{'verify2FA','resendPin','uploadSql','updateEmailAutoresponder','deleteEmailAutoresponder','updateServerRole'})
        self.assertEqual(result['constructed'],476)

class CliIntegrationTests(unittest.TestCase):
    def invoke(self,args,env=None):
        import subprocess
        return subprocess.run([os.sys.executable,str(e.ROOT/'enhance.py')]+args,cwd=str(e.ROOT),env=env,
                              text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)

    def test_cli_discovery_describe_and_unchanged_schema_diff(self):
        result=self.invoke(['ops','--method','GET','--search','website'])
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout)
        self.assertEqual((data['total'],data['method_counts']['GET']),(482,201))
        result=self.invoke(['describe','getWebsites'])
        desc=json.loads(result.stdout)
        params={p['name']:p for p in desc['parameters']}
        self.assertIn('org_id',params)
        self.assertFalse(params['roles']['explode'])
        self.assertIn('application',params['roles']['schema']['items']['enum'])
        result=self.invoke(['schema-diff',str(e.ROOT/'assets/openapi.json')])
        self.assertEqual(result.returncode,0,result.stderr)
        diff=json.loads(result.stdout)
        self.assertEqual((diff['added_count'],diff['removed_count'],diff['changed_count']),(0,0,0))

    def test_cli_loopback_get_typed_params_and_count(self):
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp,local_server([(200,{'Content-Type':'application/json'},b'{"items":[{"id":"SYNTHETIC"}],"total":12}')]) as (p,s):
            cfg=Path(tmp)/'profile.json'
            cfg.write_text(json.dumps(dict(p.data,token_env='SYNTHETIC_TOKEN')))
            env=dict(os.environ,SYNTHETIC_TOKEN=TOKEN)
            result=self.invoke(['--config',str(cfg),'--_allow-loopback-http','call','getWebsites','--param','limit=5','--param','showAliases=false','--count'],env)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(result.stdout)['data']
            self.assertEqual(data['returned_count'],1)
            self.assertEqual(data['response_metadata']['total'],12)
            self.assertIn('limit=5',s.records[0][1])
            self.assertIn('showAliases=false',s.records[0][1])
            self.assertNotIn(TOKEN,result.stdout+result.stderr)

    def test_cli_mutation_dry_run_does_not_read_missing_token(self):
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp:
            cfg=Path(tmp)/'profile.json'
            cfg.write_text(json.dumps({'base_url':'https://example.invalid/api','scope':'site','org_id':ORG,'website_id':SITE,'token_env':'SYNTHETIC_ABSENT'}))
            env=dict(os.environ)
            env.pop('SYNTHETIC_ABSENT',None)
            result=self.invoke(['--config',str(cfg),'call','deleteWebsite'],env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)['dry_run'])
            result=self.invoke(['--config',str(cfg),'call','deleteWebsite','--timeout','NaN'],env)
            self.assertEqual(result.returncode,2)
            result=self.invoke(['--config',str(cfg),'call','deleteWebsite','--max-bytes','-1'],env)
            self.assertEqual(result.returncode,2)

    def test_cli_bad_args_do_not_echo_token_or_body(self):
        for args in (['--token',TOKEN,'ops'],['call','not-an-operation','--body',TOKEN],['--insecure','ops']):
            result=self.invoke(args)
            self.assertEqual(result.returncode,2)
            self.assertNotIn(TOKEN,result.stdout+result.stderr)
            self.assertNotIn('Traceback',result.stderr)

    def test_cli_batch_exit_partial_and_no_inferred_pagination(self):
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp,local_server([(200,{'Content-Type':'application/json'},b'{"items":[],"total":123}'),(429,{},b'SYNTHETIC')]) as (p,s):
            cfg=Path(tmp)/'profile.json'
            cfg.write_text(json.dumps(dict(p.data,token_env='SYNTHETIC_TOKEN')))
            plan=Path(tmp)/'plan.json'
            plan.write_text(json.dumps([{'operationId':'getServers','view':{'count':True}},{'operationId':'getWebsites','params':{'limit':1}}]))
            result=self.invoke(['--config',str(cfg),'--_allow-loopback-http','batch',str(plan)],dict(os.environ,SYNTHETIC_TOKEN=TOKEN))
            self.assertEqual(result.returncode,1,result.stderr)
            out=json.loads(result.stdout)
            self.assertEqual((out['succeeded'],out['failed']),(1,1))
            self.assertEqual(len(s.records),2)

    def test_refresh_warning_once_per_batch_invocation(self):
        import io
        c=e.Catalog()
        freshness=e.refresh_status(c,today=datetime.date(2026,10,10))
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp,local_server([(200,{'Content-Type':'text/plain'},b'12.25.8'),(200,{'Content-Type':'text/plain'},b'12.25.8')]) as (p,s):
            cfg=Path(tmp)/'profile.json'
            cfg.write_text(json.dumps(p.data))
            plan=Path(tmp)/'plan.json'
            plan.write_text(json.dumps([{'operationId':'orchdVersion'},{'operationId':'orchdVersion'}]))
            stdout,stderr=io.StringIO(),io.StringIO()
            with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr),mock.patch.object(e,'refresh_status',return_value=freshness):
                code=e.main(['--config',str(cfg),'--_allow-loopback-http','batch',str(plan)])
            self.assertEqual(code,0,stderr.getvalue())
            self.assertEqual(stderr.getvalue().count('Advisory:'),1)
            self.assertEqual(json.loads(stdout.getvalue())['succeeded'],2)

    def test_all_reviewed_get_hazards_require_apply_and_reject_batch(self):
        import build_inventory
        c=e.Catalog()
        p=e.Profile({'base_url':'https://example.invalid/api','scope':'admin'})
        operations=['getOrgMemberLogin','createOtpSession','ssoToRoundcube','getPhpMyAdminSSOUrl','getPhpMyAdminWebsiteSSOUrl',
                    'getWordpressUserSsoUrl','openclawSso','downloadSql','getWordpressInstallations','scanImportMigrations',
                    'downloadWebsiteBackup','getWebsiteDomainSslCert','getWebsiteMailDomainSslCert']
        for name in operations:
            with self.subTest(name=name):
                op=c.operation(name)
                params={p['name']:build_inventory.sample(c,p['schema']) for p in op['parameters'] if p.get('required')}
                r=e.prepare(c,p,name,params)
                self.assertTrue(r.guidance['requires_apply'])
                with mock.patch.object(e,'transport',side_effect=AssertionError('No network permitted')):
                    self.assertTrue(e.execute(r,p)['dry_run'])
                    with self.assertRaises(e.CLIError):
                        e.batch(c,p,[{'operationId':name,'params':params}])
        self.assertEqual(e.redact({'key':'SYNTHETIC-PRIVATE-KEY'})['key'],'[REDACTED]')

    def test_schema_auth_scheme_change_is_reported_not_assumed_bearer(self):
        c=e.Catalog()
        doc=json.loads(json.dumps(c.document))
        doc['components']['securitySchemes']['bearerAuth']={'type':'apiKey','in':'header','name':'X-SYNTHETIC'}
        other=e.Catalog(document=doc)
        diff=e.schema_diff(c,other)
        row=next(row for row in diff['changed'] if row['operationId']=='getServers')
        self.assertIn('security_schemes',row['changed_fields'])
        self.assertTrue(e.auth_support(other.operation('getServers')).startswith('unsupported'))

    def test_raw_gzip_local_transport_and_filename_injection(self):
        c=e.Catalog()
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp,local_server([(200,{},b''),(200,{},b'')]) as (p,s):
            archive=Path(tmp)/'synthetic.tar.gz'
            archive.write_bytes(b'\x1f\x8bSYNTHETIC')
            e.execute(e.prepare(c,p,'uploadWebsiteBackup',raw_file=archive),p,apply=True)
            self.assertEqual(s.records[0][3],archive.read_bytes())
            self.assertEqual(s.records[0][2]['Content-Type'],'application/gzip')
            image=Path(tmp)/'bad"\nX-Injected: true.png'
            image.write_bytes(b'SYNTHETIC IMAGE')
            e.execute(e.prepare(c,p,'setOrgAvatar',files={'avatar':str(image)}),p,apply=True)
            headers=s.records[1][3].split(b'\r\n\r\n',1)[0]
            self.assertNotIn(b'X-Injected',headers)
            self.assertIn(b'filename="upload.png"',headers)

    def test_empty_nonobject_params_rejected(self):
        c=e.Catalog()
        p=e.Profile({'base_url':'https://example.invalid','scope':'admin'})
        for params in ([],False,0,''):
            with self.subTest(params=params),self.assertRaises(e.CLIError):
                e.prepare(c,p,'orchdVersion',params)

    def test_named_backup_ids_do_not_allow_site_prefix_confusion(self):
        import build_inventory
        c=e.Catalog()
        p=e.Profile({'base_url':'https://example.invalid/api','scope':'site','org_id':ORG,'website_id':SITE})
        operations=[op for op in c.operations.values() if op['path']=='/backups/{server_id}/{website_id}']
        self.assertTrue(operations)
        for op in operations:
            params={param['name']:build_inventory.sample(c,param['schema']) for param in op['parameters']}
            params.update(server_id=SITE,website_id=OTHER)
            with self.subTest(name=op['operationId']),self.assertRaises(e.CLIError):
                e.prepare(c,p,op['operationId'],params)
        org=e.Profile({'base_url':'https://example.invalid/api','scope':'organisation','org_id':ORG})
        with self.assertRaises(e.CLIError):
            e.prepare(c,org,'getWebsites',{'showDeleted':False})

    def test_cross_origin_redirect_never_contacts_destination(self):
        c=e.Catalog()
        with local_server([]) as (other,target),local_server([(302,{'Location':other.base_url+'/servers?token='+TOKEN},b'')]) as (p,source):
            with self.assertRaises(e.CLIError) as err:
                e.execute(e.prepare(c,p,'getServers'),p)
            self.assertEqual(err.exception.code,'redirect')
            self.assertEqual(target.records,[])
            self.assertEqual(len(source.records),1)
            self.assertNotIn(TOKEN,str(err.exception))

    def test_batch_aggregate_size_exhaustion_explicit_not_sent(self):
        c=e.Catalog()
        raw=b'{"items":[],"total":0}'
        with local_server([(200,{'Content-Type':'application/json'},raw),(200,{'Content-Type':'application/json'},raw)]) as (p,s):
            result=e.batch(c,p,[{'operationId':'getServers'}]*3,max_bytes=len(raw)+1)
            self.assertEqual((result['requested'],result['succeeded'],result['failed']),(3,1,2))
            self.assertEqual(len(s.records),2)
            self.assertIn('not sent',result['results'][2]['error']['message'])

    def test_multipart_nonbinary_fields_and_standalone_sql_schema(self):
        c=e.Catalog()
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp:
            f=Path(tmp)/'synthetic.sql'
            f.write_bytes(b'-- SYNTHETIC SQL fixture; not executed\nSELECT 1;\n')
            media={'schema':{'type':'object','additionalProperties':False,'required':['sql','enabled','metadata'],
                            'properties':{'sql':{'type':'string','format':'binary'},'enabled':{'type':'boolean'},'metadata':{'type':'object'}}}}
            body,ctype=e.build_multipart(c,media,{'enabled':False,'metadata':{'synthetic':True}},{'sql':str(f)},e.DEFAULT_LIMIT)
            self.assertIn(b'name="enabled"',body)
            self.assertIn(b'\r\n\r\nfalse\r\n',body)
            self.assertIn(b'{"synthetic": true}',body)
            self.assertTrue(ctype.startswith('multipart/form-data'))
            official=c.resolve(c.operation('uploadSql')['requestBody'])['content']['multipart/form-data']
            payload,_=e.build_multipart(c,official,{}, {'sql':str(f)},e.DEFAULT_LIMIT)
            self.assertIn(f.read_bytes(),payload)
            self.assertTrue(c.describe('uploadSql')['schema_issues'])

    def test_nonfinite_json_duplicate_keys_and_fifo_input_rejected(self):
        for text in ('{"a":1,"a":2}','NaN','Infinity','1e999','9'*200):
            with self.subTest(text=text),self.assertRaises(e.CLIError):
                e.parse_json(text)
        if hasattr(os,'mkfifo'):
            with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp:
                fifo=Path(tmp)/'synthetic.fifo'
                os.mkfifo(fifo)
                with self.assertRaises(e.CLIError):
                    e.read_bytes(fifo,100)

    def test_unsupported_media_and_parameter_styles_fail_explicitly(self):
        doc={'openapi':'3.0.3','info':{'version':'SYNTHETIC'},'paths':{'/synthetic':{'post':{'operationId':'syntheticUpload',
             'requestBody':{'content':{'application/x-synthetic':{'schema':{'type':'string'}}}},'responses':{'200':{'description':'SYNTHETIC'}}}}}}
        c=e.Catalog(document=doc)
        p=e.Profile({'base_url':'https://example.invalid','scope':'admin'})
        with self.assertRaises(e.CLIError) as err:
            e.prepare(c,p,'syntheticUpload',body='SYNTHETIC')
        self.assertEqual(err.exception.code,'unsupported')
        with self.assertRaises(e.CLIError) as err:
            e.query_pairs({'name':'query','style':'deepObject'},{'x':'SYNTHETIC'})
        self.assertEqual(err.exception.code,'unsupported')

    def test_tls_defaults_and_network_timeout_redaction(self):
        import ssl
        ctx=ssl.create_default_context()
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode,ssl.CERT_REQUIRED)
        c=e.Catalog()
        p=e.Profile({'base_url':'https://example.invalid','scope':'admin'})
        p.token=TOKEN
        request=e.prepare(c,p,'getServers')
        with mock.patch.object(e.http.client.HTTPSConnection,'request',side_effect=e.socket.timeout(TOKEN)):
            with self.assertRaises(e.CLIError) as err:
                e.execute(request,p)
        self.assertEqual(err.exception.code,'timeout')
        self.assertNotIn(TOKEN,str(err.exception))

    def test_standalone_relocation_with_site_packages_disabled(self):
        import shutil
        import subprocess
        with tempfile.TemporaryDirectory(dir=e.ROOT) as tmp:
            script=Path(tmp)/'scripts'
            (script/'assets').mkdir(parents=True)
            shutil.copyfile(e.ROOT/'enhance.py',script/'enhance.py')
            for name in ('openapi.json','freshness.json'):
                shutil.copyfile(e.ROOT/'assets'/name,script/'assets'/name)
            result=subprocess.run([os.sys.executable,'-I','-S',str(script/'enhance.py'),'ops','--method','GET'],
                                  cwd=tmp,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(result.stdout)
            self.assertEqual((data['total'],data['matched']),(482,201))

    def test_unsupported_multipart_text_encoding_is_not_ignored(self):
        c=e.Catalog()
        media={'schema':{'type':'object','properties':{'field':{'type':'string'}}},
               'encoding':{'field':{'contentType':'application/x-synthetic'}}}
        with self.assertRaises(e.CLIError) as err:
            e.build_multipart(c,media,{'field':'SYNTHETIC'},{},1000)
        self.assertEqual(err.exception.code,'unsupported')

if __name__ == '__main__':
    unittest.main()
