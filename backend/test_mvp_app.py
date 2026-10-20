import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from pydantic import BaseModel

import mvp_app


class MVPTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(mvp_app.app)

    def test_recorded_report(self):
        result=self.client.post('/api/reports',json={'mode':'reviewed_example'})
        self.assertEqual(result.status_code,200)
        body=result.json()
        self.assertEqual(body['status'],'no_compatible_patch')
        self.assertEqual(body['validation']['checks_passed'],19)
        self.assertFalse(body['validation']['patch_tested'])
        self.assertEqual(body['diff'],'')

    def test_cross_origin_and_unknown_host_blocked(self):
        self.assertEqual(self.client.post('/api/reports',json={},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/health',headers={'Host':'evil.example'}).status_code,400)

    def test_body_and_schema_bounds(self):
        self.assertEqual(self.client.post('/api/reports',content='x'*33000,headers={'Content-Type':'application/json'}).status_code,413)
        self.assertEqual(self.client.post('/api/reports',json={'command':'anything'}).status_code,422)

    def test_live_consent_required(self):
        self.assertEqual(self.client.post('/api/reports',json={'mode':'live','issue':'example'}).status_code,422)

    def test_recorded_ai_evidence_no_provider_call(self):
        with patch.object(mvp_app,'clone_repository') as clone:
            result=self.client.post('/api/reports',json={'mode':'gemini_evidence'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['status'],'recorded_ai_repair_verified')
        self.assertTrue(result.json()['validation']['patch_tested'])
        clone.assert_not_called()

    def test_inconsistent_ai_evidence_rejected(self):
        with patch.object(mvp_app,'read_evidence',return_value={'status':'wrong','test_result':{}}):
            result=self.client.post('/api/reports',json={'mode':'gemini_evidence'})
        self.assertEqual(result.status_code,422)

    def test_fresh_baseline_uses_fixed_backend_profile(self):
        with patch.object(mvp_app,'fresh_requests_report',return_value={'mode':'isolated_baseline_run'}) as run:
            result=self.client.post('/api/reports',json={'mode':'requests_baseline'})
        self.assertEqual(result.json()['mode'],'isolated_baseline_run')
        run.assert_called_once_with()

    def live(self,changes):
        class Proposal:
            @classmethod
            def model_validate(cls,value):
                return types.SimpleNamespace(model_dump=lambda:value)
        proposal=dict(summary='Proposed fix',changes=changes,risks=[],tests_to_add=[],needs_more_context=False,confidence='medium')
        fake=types.SimpleNamespace(generate_patch_with_ai=lambda *a:proposal,PatchProposal=Proposal)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'calc.py').write_text('def add(a,b):\n    return a-b\n')
            owner=types.SimpleNamespace(cleanup=lambda:None)
            with patch.dict(sys.modules,{'ai_service':fake}), patch.object(mvp_app,'clone_repository',return_value=(owner,root)), patch.object(mvp_app,'get_bug_relevant_code',return_value=[dict(file='calc.py',code=(root/'calc.py').read_text())]), patch.object(mvp_app.subprocess,'run',return_value=types.SimpleNamespace(stdout=b'a'*40)):
                response=self.client.post('/api/reports',json={'mode':'live','repo_url':'https://github.com/example/repo','issue':'addition wrong','send_source_to_ai':True})
            self.assertEqual((root/'calc.py').read_text(),'def add(a,b):\n    return a-b\n')
            return response

    def test_validated_diff_and_original_unchanged(self):
        result=self.live([dict(file='calc.py',original_code='return a-b',replacement_code='return a+b',explanation='add')])
        self.assertEqual(result.status_code,200,result.text)
        body=result.json()
        self.assertEqual(body['status'],'proposal_validated')
        self.assertIn('+    return a+b',body['diff'])
        self.assertFalse(body['validation']['behavior_tested'])

    def test_invalid_patch_has_no_downloadable_diff(self):
        result=self.live([dict(file='calc.py',original_code='return a-b',replacement_code='return (',explanation='bad')])
        self.assertEqual(result.json()['status'],'proposal_rejected')
        self.assertEqual(result.json()['diff'],'')

    def test_no_change_is_valid_result(self):
        self.assertEqual(self.live([]).json()['status'],'no_change_proposed')

    def test_failure_does_not_echo_provider_details(self):
        with patch.object(mvp_app,'reviewed_report',side_effect=RuntimeError('sensitive internal detail')):
            result=self.client.post('/api/reports',json={})
        self.assertEqual(result.status_code,502)
        self.assertNotIn('sensitive',result.text)


if __name__=='__main__':
    unittest.main()
