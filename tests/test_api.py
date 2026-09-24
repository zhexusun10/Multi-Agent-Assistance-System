import unittest

import httpx

from api.main import app, get_graph_client


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            if request.url.path == "/run":
                return httpx.Response(200, json={
                    "answer": "done",
                    "agents": [{"agent": "a", "prompt": "task", "output": "result"}],
                })
            return httpx.Response(404)

        self.graph_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="http://graph"
        )

        async def override_client():
            yield self.graph_client

        app.dependency_overrides[get_graph_client] = override_client
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
        self.assertEqual(response.json(), {
            "answer": "done",
            "agents": [{"agent": "a", "prompt": "task", "output": "result"}],
        })

    async def test_blank_query_is_rejected(self):
        response = await self.api_client.post("/api/query", json={"query": "   "})
        self.assertEqual(response.status_code, 422)

    async def test_health_checks_graph_service(self):
        response = await self.api_client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
