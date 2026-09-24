import unittest
import json

import httpx

from api.main import app, get_graph_client


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.graph_requests = []
        self.event_requests = []
        self.runtime_requests = []

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
                        'event: done\ndata: {"type":"done","session_id":"session-one","answer":"done","spawned_agents":["a"]}\n\n'
                    ),
                )
            if request.url.path == "/events":
                self.event_requests.append((dict(request.url.params), request.headers.get("last-event-id")))
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content='id: 2\nevent: runtime_answer\ndata: {"type":"runtime_answer","answer":"follow-up"}\n\n',
                )
            if request.url.path == "/runtime/events":
                self.runtime_requests.append(json.loads(request.content))
                return httpx.Response(202, json={"status": "accepted", "session_id": "session-one"})
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
        self.assertIn('"spawned_agents":["a"]', response.text)

    async def test_events_relay_cursor_and_runtime_answer(self):
        response = await self.api_client.get(
            "/api/events", params={"session_id": "session-one", "after": 1}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.event_requests, [({"session_id": "session-one", "after": "1"}, None)])
        self.assertIn("event: runtime_answer", response.text)

    async def test_runtime_event_is_forwarded(self):
        response = await self.api_client.post("/api/runtime/events", json={
            "session_id": "session-one", "name": "build_finished", "payload": {"ok": True}
        })
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.runtime_requests, [{
            "session_id": "session-one", "name": "build_finished", "payload": {"ok": True}
        }])

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
