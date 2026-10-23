"""The same-origin proxy forwards only the intended authenticated card operations."""
import unittest
from unittest.mock import patch
import httpx
from api.main import app


class RefinementProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_delete_forwards_auth_and_only_the_exact_route(self):
        calls=[]
        client_class=httpx.AsyncClient
        def handle(request):
            calls.append(request)
            return httpx.Response(200,json={'status':'deleted'})
        def upstream(*args,**kwargs):
            return client_class(transport=httpx.MockTransport(handle),base_url='http://graph')
        async with client_class(transport=httpx.ASGITransport(app=app),base_url='http://api') as client:
            with patch('api.main.httpx.AsyncClient',side_effect=upstream):
                response=await client.delete('/api/sessions/00000000-0000-4000-8000-000000000001',headers={'Authorization':'Bearer local-owner'})
                self.assertEqual(response.status_code,200)
                self.assertEqual((await client.delete('/api/actions/anything')).status_code,404)
                self.assertEqual((await client.delete('/api/sessions/not-uuid')).status_code,404)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].method,'DELETE')
        self.assertEqual(calls[0].headers['authorization'],'Bearer local-owner')

    async def test_refinement_posts_keep_identity_and_errors_without_calling_models(self):
        calls=[]
        client_class=httpx.AsyncClient
        def handle(request):
            calls.append(request)
            return httpx.Response(409 if request.url.path.endswith('/submit') else 200,json={'detail':'stale'} if request.url.path.endswith('/submit') else {'status':'dismissed'})
        def upstream(*args,**kwargs):
            return client_class(transport=httpx.MockTransport(handle),base_url='http://graph')
        async with client_class(transport=httpx.ASGITransport(app=app),base_url='http://api') as client:
            with patch('api.main.httpx.AsyncClient',side_effect=upstream):
                path='/api/refinements/00000000-0000-4000-8000-000000000001'
                response=await client.post(path+'/submit',json={'session_id':'session-one','answers':[]},headers={'Authorization':'Bearer isolated-test'})
                self.assertEqual(response.status_code,409)
                response=await client.post(path+'/dismiss',json={'session_id':'session-one'},headers={'Authorization':'Bearer isolated-test'})
                self.assertEqual(response.status_code,200)
                self.assertEqual((await client.get(path+'/submit')).status_code,404)
                self.assertEqual((await client.post('/api/refinements/not-an-id/submit',json={})).status_code,404)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0].headers['authorization'],'Bearer isolated-test')
        self.assertIn(b'session-one',calls[0].content)
