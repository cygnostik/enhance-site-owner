"""Parent regressions from independent review; synthetic loopback only."""
import copy
import unittest
import enhance as e
from test_enhance import local_server

class ReviewCliTests(unittest.TestCase):
    def test_failed_views_still_consume_batch_byte_budget(self):
        raw=b'{"items":[{"id":"synthetic"}],"total":1}'
        with local_server([(200,{'Content-Type':'application/json'},raw)]*3) as (profile,server):
            result=e.batch(e.Catalog(),profile,[{'operationId':'getServers','view':{'select':['/absent']}}]*3,max_bytes=len(raw))
            self.assertEqual(len(server.records),1)
            self.assertEqual(result['failed'],3)
            self.assertEqual(result['results'][0]['error']['code'],'view')
            self.assertEqual(result['results'][1]['error']['code'],'size')

    def test_invalid_json_still_consumes_batch_byte_budget(self):
        raw=b'{invalid json}'
        with local_server([(200,{'Content-Type':'application/json'},raw)]*2) as (profile,server):
            result=e.batch(e.Catalog(),profile,[{'operationId':'getServers'}]*2,max_bytes=len(raw))
            self.assertEqual(len(server.records),1)
            self.assertEqual(result['results'][1]['error']['code'],'size')

    def test_operation_deprecation_is_visible_in_schema_diff(self):
        old=e.Catalog(); changed=copy.deepcopy(old.document)
        changed['paths']['/version']['get']['deprecated']=True
        diff=e.schema_diff(old,e.Catalog(document=changed))
        self.assertEqual(diff['changed_count'],1)
        self.assertIn('deprecated',diff['changed'][0]['changed_fields'])

if __name__=='__main__':unittest.main()
