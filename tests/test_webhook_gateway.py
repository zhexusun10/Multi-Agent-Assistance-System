import unittest

import httpx

from api.webhook_gateway import app, graph_client


class WebhookGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []

        def handle(request):
            self.requests.append(request)
            if request.url.path == "/webhooks/whatsapp" and request.method == "GET":
                return httpx.Response(200, text=request.url.params["hub.challenge"])
            return httpx.Response(403, json={"code": "WEBHOOK_AUTH"})

        self.transport = httpx.MockTransport(handle)
        app.dependency_overrides[graph_client] = lambda: httpx.AsyncClient(
            base_url="http://graph", transport=self.transport)
        self.client = httpx.AsyncClient(base_url="http://gateway", transport=httpx.ASGITransport(app=app))

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()

    async def test_exact_raw_bytes_signature_and_rejection_are_preserved(self):
        raw = b'{ "entry": [] }\n'
        response = await self.client.post("/api/webhooks/whatsapp", content=raw,
                                          headers={"x-hub-signature-256": "sha256=test", "Authorization": "Bearer must-not-forward"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.requests[0].content, raw)
        self.assertEqual(self.requests[0].headers["x-hub-signature-256"], "sha256=test")
        self.assertNotIn("authorization", self.requests[0].headers)

    async def test_challenge_query_and_plain_text_response(self):
        response = await self.client.get("/api/webhooks/whatsapp", params={
            "hub.mode": "subscribe", "hub.verify_token": "test-only", "hub.challenge": "9876"})
        self.assertEqual(response.text, "9876")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.requests[0].url.params["hub.verify_token"], "test-only")

    async def test_telegram_secret_is_forwarded(self):
        await self.client.post("/api/webhooks/telegram", json={"update_id": 1},
                               headers={"x-telegram-bot-api-secret-token": "test-only"})
        self.assertEqual(self.requests[0].headers["x-telegram-bot-api-secret-token"], "test-only")

    async def test_business_routes_docs_and_unknown_providers_are_not_exposed(self):
        for path in ["/", "/docs", "/openapi.json", "/api/state", "/api/sessions", "/api/webhooks/unknown", "/api/webhooks/telegram"]:
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 404, path)
        self.assertEqual(self.requests, [])

    async def test_oversized_body_is_rejected_before_upstream(self):
        response = await self.client.post("/api/webhooks/whatsapp", content=b"x" * 65537)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.requests, [])

    async def test_graph_unavailable_returns_retryable_503(self):
        def fail(request):
            raise httpx.ConnectError("test upstream unavailable", request=request)
        self.transport = httpx.MockTransport(fail)
        response = await self.client.post("/api/webhooks/whatsapp", content=b"{}")
        self.assertEqual(response.status_code, 503)
