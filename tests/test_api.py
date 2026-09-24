import unittest
import json

import httpx

from api.main import app, get_graph_client


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.graph_requests = []

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            if request.url.path == "/run":
                self.graph_requests.append(json.loads(request.content))
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=(
                        'event: session\ndata: {"type":"session","session_id":"session-one"}\n\n'
                        'event: master_answer\ndata: {"type":"master_answer","answer":"done"}\n\n'
                        'event: agent_result\ndata: {"type":"agent_result","result":{"agent":"a","prompt":"task","output":"result"}}\n\n'
                        'event: done\ndata: {"type":"done","session_id":"session-one","answer":"done","agents":[]}\n\n'
                    ),
                )
            return httpx.Response(404)

        self.graph_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="http://graph"
        )

        app.dependency_overrides[get_graph_client] = lambda: self.graph_client
        self.api_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api"
        )

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.api_client.aclose()
        await self.graph_client.aclose()

    async def test_query_proxies_graph_result(self):
        response = await self.api_client.post("/api/query", json={"query": "question"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.graph_requests, [{"query": "question"}])
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("event: master_answer", response.text)
        self.assertIn("event: agent_result", response.text)

    async def test_blank_query_is_rejected(self):
        response = await self.api_client.post("/api/query", json={"query": "   "})
        self.assertEqual(response.status_code, 422)

    async def test_session_id_is_forwarded(self):
        response = await self.api_client.post(
            "/api/query", json={"query": "follow up", "session_id": "session-one"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.graph_requests, [
            {"query": "follow up", "session_id": "session-one"}
        ])

    async def test_blank_session_id_is_rejected(self):
        response = await self.api_client.post(
            "/api/query", json={"query": "question", "session_id": "   "}
        )
        self.assertEqual(response.status_code, 422)

    async def test_health_checks_graph_service(self):
        response = await self.api_client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
