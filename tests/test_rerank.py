"""Rerank contract, failure recovery, chunk identity, and configuration tests."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import chromadb
import httpx

from app import config
from app.kb import rerank, store
from app.tools import kb_tools


def text_hit(chunk_id, doc_id=6):
    return dict(doc_id=doc_id, chunk_id=chunk_id, filename="fixture.pdf", page=7,
                type="text", text=f"Text chunk {chunk_id}", score=0.8)


def image_hit(chunk_id):
    return dict(doc_id=6, chunk_id=chunk_id, filename="fixture.pdf", page=7,
                type="image", caption="Diagram", image_path="kb_images/fixture.png", score=0.7)


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(kb_tools, "RERANK_ENABLED", True))
        for name, value in [("RERANK_TEXT_CANDIDATES", 20), ("RERANK_IMAGE_TOP_K", 2), ("RERANK_FINAL_TOP_K", 6)]:
            self.stack.enter_context(patch.object(kb_tools, name, value))
        self.context = SimpleNamespace(session=SimpleNamespace(id="session-a"))
        self.stack.enter_context(patch.object(kb_tools, "wait_for_pending_ingest", AsyncMock()))
        self.embed = self.stack.enter_context(patch.object(kb_tools, "embed_query", AsyncMock(return_value=[1., 0.])) )
        entries = [dict(id=i, kind="document", status=status, enabled=enabled)
                   for i, status, enabled in [(6, "ready", 1), (7, "ready", 1), (8, "failed", 1), (9, "ready", 0)]]
        self.catalog = self.stack.enter_context(patch.object(kb_tools, "list_catalog_entries", return_value=entries))
        self.texts = [text_hit(i) for i in range(17, 25)]
        self.images = [image_hit(1), image_hit(2)]
        self.query = self.stack.enter_context(patch.object(kb_tools, "query_chunks", side_effect=self.retrieve))
        self.requests = []
        self.handler = self.success
        real_client = httpx.AsyncClient

        async def handle(request):
            self.requests.append(request)
            return await self.handler(request)

        transport = httpx.MockTransport(handle)
        self.stack.enter_context(patch.object(rerank.httpx, "AsyncClient", side_effect=lambda **kw: real_client(transport=transport, **kw)))
        self.stack.enter_context(patch.object(rerank, "get_openrouter_api_key", return_value="test-key"))
        self.stack.enter_context(patch.object(rerank, "OPENROUTER_BASE", "https://openrouter.test/api/v1/"))
        self.stack.enter_context(patch.object(rerank, "RERANK_MODEL", "qwen/qwen3-reranker-8b"))

    def retrieve(self, **kwargs):
        self.assertEqual(kwargs["allowed_doc_ids"], [6])
        return self.images if kwargs.get("chunk_type") == "image" else self.texts

    async def success(self, request):
        body = json.loads(request.content)
        return httpx.Response(200, json={"results": [
            {"index": i, "relevance_score": 1.0 - rank * .1}
            for rank, i in enumerate(reversed(range(len(body["documents"]))))
        ][:body["top_n"]]})

    async def search(self):
        return await kb_tools.search_knowledge_base("retry timeout", self.context, data=[6, 8, 9, 999])

    async def assert_neighbors(self, hit):
        indices = [hit["chunk_id"] - 1, hit["chunk_id"] + 1]
        with patch.object(kb_tools, "get_chunks_by_indices", return_value=[text_hit(i) for i in indices]) as chunks:
            result = await kb_tools.get_chunk(hit["doc_id"], indices, self.context)
        chunks.assert_called_once_with(doc_id=6, chunk_indices=indices)
        self.assertEqual([r["chunk_id"] for r in result["chunks"]], indices)

    async def test_reranks_text_preserves_ids_and_bypasses_images(self):
        result = await self.search()
        self.assertEqual(result["rerank_status"], "applied")
        self.assertEqual([h["chunk_id"] for h in result["results"][:4]], [24, 23, 22, 21])
        self.assertEqual(result["results"][4:], self.images)
        for hit in result["results"][:4]:
            original = next(t for t in self.texts if t["chunk_id"] == hit["chunk_id"])
            self.assertEqual({k: hit[k] for k in original}, original)
            self.assertIn("rerank_score", hit)
        request, = self.requests
        self.assertEqual(str(request.url), "https://openrouter.test/api/v1/rerank")
        self.assertEqual(request.headers["authorization"], "Bearer test-key")
        body = json.loads(request.content)
        self.assertEqual(body, dict(model="qwen/qwen3-reranker-8b", query="retry timeout",
                                    documents=[h["text"] for h in self.texts], top_n=4))
        self.embed.assert_awaited_once()
        self.catalog.assert_called_with(session_id="session-a")
        self.assertEqual([c.kwargs["top_k"] for c in self.query.call_args_list], [20, 2])
        await self.assert_neighbors(result["results"][0])

    async def test_disabled_retains_original_mixed_search(self):
        mixed = [image_hit(1), text_hit(18)]
        self.query.side_effect = None
        self.query.return_value = mixed
        with patch.object(kb_tools, "RERANK_ENABLED", False):
            result = await self.search()
        self.assertEqual(result["results"], mixed)
        self.assertEqual(result["rerank_status"], "disabled")
        self.assertEqual(result["rerank_elapsed_ms"], 0)
        self.query.assert_called_once_with(query_embedding=[1., 0.], allowed_doc_ids=[6], top_k=6)
        self.assertEqual(self.requests, [])

    async def test_cardinality_cases(self):
        for nt, ni in [(0, 0), (0, 2), (1, 0), (1, 2), (8, 0), (8, 1)]:
            with self.subTest(texts=nt, images=ni):
                self.texts = [text_hit(i) for i in range(17, 17 + nt)]
                self.images = [image_hit(i) for i in range(ni)]
                self.requests.clear()
                result = await self.search()
                self.assertEqual(len(result["results"]), min(nt, 6 - ni) + ni)
                self.assertEqual(len(self.requests), int(nt > 1))
                self.assertEqual(result["rerank_status"], "applied" if nt > 1 else "skipped")

    async def test_failures_fall_back_without_retry(self):
        cases = [
            ("429", 429, {}), ("500", 500, {}), ("503", 503, {}),
            ("json", 200, None), ("missing", 200, {}),
            ("incomplete", 200, {"results": [{"index": 0, "relevance_score": .9}]}),
        ]
        for name, index, score in [("duplicate", 0, .5), ("out_of_range", 100, .5),
                                   ("negative", -1, .5), ("bool_index", True, .5),
                                   ("nan", 1, float("nan")), ("inf", 1, float("inf")),
                                   ("string_score", 1, "0.5")]:
            rows = [{"index": i, "relevance_score": .9} for i in range(4)]
            rows[1] = {"index": index, "relevance_score": score}
            cases.append((name, 200, {"results": rows}))
        for name, status, payload in cases:
            with self.subTest(case=name):
                async def handler(request):
                    return httpx.Response(status, content=b"not json") if payload is None else httpx.Response(status, json=payload)
                # json.dumps allows NaN/Infinity to exercise parser validation.
                if name in {"nan", "inf"}:
                    async def handler(request):
                        return httpx.Response(status, content=json.dumps(payload).encode())
                self.handler = handler
                self.requests.clear()
                result = await self.search()
                self.assertEqual(result["rerank_status"], "fallback")
                self.assertEqual(result["results"], self.texts[:4] + self.images)
                self.assertEqual(len(self.requests), 1)
        await self.assert_neighbors(result["results"][0])

    async def test_total_timeout_falls_back(self):
        async def slow(request):
            await asyncio.sleep(.2)
            return await self.success(request)
        self.handler = slow
        with patch.object(rerank, "RERANK_TIMEOUT_SECONDS", .02):
            result = await self.search()
        self.assertEqual(result["rerank_status"], "fallback")
        self.assertEqual(result["results"], self.texts[:4] + self.images)
        self.assertLess(result["rerank_elapsed_ms"], 150)
        self.assertEqual(len(self.requests), 1)

    async def test_no_visible_documents_skips_embedding(self):
        self.catalog.return_value = []
        result = await self.search()
        self.assertEqual(result["results"], [])
        self.embed.assert_not_awaited()
        self.assertEqual(self.requests, [])


class StoreTests(unittest.TestCase):
    def test_real_chroma_type_scope_filter_and_neighbor_ids(self):
        client = chromadb.EphemeralClient()
        collection = client.create_collection("rerank-test", metadata={"hnsw:space": "cosine"})
        try:
            collection.upsert(
                ids=["doc6_t_17", "doc6_t_18", "doc6_t_19", "doc6_i_1", "doc7_t_1"],
                embeddings=[[1., 0.], [.9, .1], [.8, .2], [.95, .05], [1., 0.]],
                documents=["before", "target", "after", "diagram", "other session"],
                metadatas=[dict(doc_id=d, type=t, chunk_index=i, page=7)
                           for d, t, i in [(6,"text",17),(6,"text",18),(6,"text",19),(6,"image",1),(7,"text",1)]],
            )
            with patch.object(store, "_get_collection", return_value=collection):
                texts = store.query_chunks(query_embedding=[1., 0.], allowed_doc_ids=[6], top_k=20, chunk_type="text")
                images = store.query_chunks(query_embedding=[1., 0.], allowed_doc_ids=[6], chunk_type="image")
                self.assertEqual({h["chunk_id"] for h in texts}, {17,18,19})
                self.assertTrue(all(h["doc_id"] == 6 and h["type"] == "text" for h in texts))
                self.assertEqual([h["type"] for h in images], ["image"])
                self.assertEqual([h["chunk_id"] for h in store.get_chunks_by_indices(6,[17,19])], [17,19])
                # Feed a reordered hit into the real tool and real Chroma neighbor lookup.
                reordered = rerank._map_results({"results": [{"index": 1, "relevance_score": .99}]}, texts, 1)
                self.assertEqual(reordered[0]["chunk_id"], 18)
                with patch.object(kb_tools, "list_catalog_entries", return_value=[dict(id=6,kind="document",status="ready",enabled=1)]), patch.object(kb_tools,"wait_for_pending_ingest",AsyncMock()):
                    for hit in [reordered[0], texts[0]]:
                        neighbors = [hit["chunk_id"] - 1, hit["chunk_id"] + 1]
                        result = asyncio.run(kb_tools.get_chunk(hit["doc_id"],neighbors,SimpleNamespace(session=SimpleNamespace(id="test"))))
                        self.assertEqual([r["chunk_id"] for r in result["chunks"]], [i for i in neighbors if i in {17,18,19}])
                self.assertEqual(store.query_chunks(query_embedding=[1.,0.],allowed_doc_ids=[999]), [])
                self.assertEqual(store.query_chunks(query_embedding=[1.,0.],allowed_doc_ids=[6,7],chunk_type="image")[0]["doc_id"],6)
                collection.delete(where={"doc_id": {"$in": [6,7]}})
                self.assertEqual(store.query_chunks(query_embedding=[1.,0.],allowed_doc_ids=[6]), [])
        finally:
            client.delete_collection("rerank-test")


class ConfigTests(unittest.TestCase):
    def test_startup_defaults_and_validation(self):
        program = '''
import os, runpy
from unittest.mock import patch
with patch('dotenv.load_dotenv'):
    values = runpy.run_path('app/config.py')
assert values['RERANK_ENABLED'] is False
assert values['RERANK_MODEL'] == 'qwen/qwen3-reranker-8b'
'''
        env = {k:v for k,v in os.environ.items() if not k.startswith("RERANK_")}
        result = subprocess.run([sys.executable,"-c",program],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        for key, value in [("RERANK_ENABLED","maybe"),("RERANK_TIMEOUT_SECONDS","nan"),
                           ("RERANK_TIMEOUT_SECONDS","inf"),("RERANK_TIMEOUT_SECONDS","abc"),
                           ("RERANK_TIMEOUT_SECONDS","0"),("RERANK_TEXT_CANDIDATES","0"),
                           ("RERANK_FINAL_TOP_K","-1"),("RERANK_IMAGE_TOP_K","-1"),
                           ("RERANK_MODEL","")]:
            with self.subTest(key=key,value=value):
                result = subprocess.run([sys.executable,"-c","import app.config"],env={**env,key:value},capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn(key,result.stderr)

    def test_boolean_and_numeric_settings(self):
        for raw, expected in [("TRUE",True),("1",True),(" Yes ",True),("false",False),("0",False),("NO",False)]:
            with patch.dict(os.environ, {"TEST_RERANK_BOOL": raw}):
                self.assertEqual(config._env_bool("TEST_RERANK_BOOL"), expected)
        self.assertFalse(config._env_bool("MISSING_RERANK_TEST_BOOLEAN"))
        with patch.dict(os.environ, {"TEST_RERANK_BOOL": "sometimes"}):
            with self.assertRaisesRegex(ValueError, "TEST_RERANK_BOOL"):
                config._env_bool("TEST_RERANK_BOOL")
        for value in ["-1", "0", "1.5", "abc"]:
            with patch.dict(os.environ, {"TEST_RERANK_INT": value}):
                with self.assertRaises(ValueError): config._env_int("TEST_RERANK_INT", 20)

    def test_deploy_script_passes_configuration_without_deploying(self):
        with tempfile.TemporaryDirectory() as folder:
            stub = Path(folder)/"gcloud"
            stub.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args=sys.argv[1:]
assert args[:2]==['run','deploy']
p=pathlib.Path(args[args.index('--env-vars-file')+1])
assert p.stat().st_mode & 0o077 == 0
values=json.loads(p.read_text())
assert values['RERANK_ENABLED']=='true'
assert values['RERANK_MODEL']=='qwen/qwen3-reranker-8b'
assert values['RERANK_TIMEOUT_SECONDS']=='8.0'
assert values['RERANK_TEXT_CANDIDATES']=='20'
assert values['RERANK_IMAGE_TOP_K']=='2'
assert values['RERANK_FINAL_TOP_K']=='6'
assert values['OPENROUTER_API_KEY']=='synthetic-key'
pathlib.Path(os.environ['DEPLOY_TEST_CAPTURE']).write_text(str(p))
''')
            stub.chmod(0o700)
            capture=Path(folder)/"capture"
            env={**os.environ,"GCLOUD_BIN":str(stub),"PYTHON_BIN":sys.executable,
                 "DEPLOY_TEST_CAPTURE":str(capture),"OPENROUTER_API_KEY":"synthetic-key",
                 "RERANK_ENABLED":"yes","RERANK_MODEL":"qwen/qwen3-reranker-8b",
                 "RERANK_TIMEOUT_SECONDS":"8","RERANK_TEXT_CANDIDATES":"20",
                 "RERANK_IMAGE_TOP_K":"2","RERANK_FINAL_TOP_K":"6"}
            completed=subprocess.run(["bash","deploy_cloud_run.sh"],env=env,capture_output=True,text=True)
            self.assertEqual(completed.returncode,0,completed.stderr)
            self.assertNotIn("synthetic-key", completed.stdout+completed.stderr)
            self.assertFalse(Path(capture.read_text()).exists())


@unittest.skipUnless(os.getenv("RERANK_SIMULATION_BASE"), "Set RERANK_SIMULATION_BASE for local HTTP simulation")
class SimulationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_success_errors_and_timeout(self):
        texts = [text_hit(i) for i in range(17, 25)]
        images = [image_hit(1), image_hit(2)]
        context = SimpleNamespace(session=SimpleNamespace(id="simulation"))
        def retrieve(**kwargs):
            return images if kwargs.get("chunk_type") == "image" else texts
        with ExitStack() as stack:
            stack.enter_context(patch.object(kb_tools, "RERANK_ENABLED", True))
            stack.enter_context(patch.object(kb_tools, "query_chunks", side_effect=retrieve))
            stack.enter_context(patch.object(kb_tools, "embed_query", AsyncMock(return_value=[1.,0.])))
            stack.enter_context(patch.object(kb_tools, "wait_for_pending_ingest", AsyncMock()))
            stack.enter_context(patch.object(kb_tools, "list_catalog_entries", return_value=[dict(id=6,kind="document",status="ready",enabled=1)]))
            stack.enter_context(patch.object(rerank, "OPENROUTER_BASE", os.environ["RERANK_SIMULATION_BASE"]))
            stack.enter_context(patch.object(rerank, "get_openrouter_api_key", return_value="simulation-key"))
            for query, status in [("test-success","applied"),("test-429","fallback"),("test-503","fallback"),("test-timeout","fallback")]:
                with self.subTest(query=query), patch.object(rerank,"RERANK_TIMEOUT_SECONDS",.1 if query=="test-timeout" else 5):
                    result = await kb_tools.search_knowledge_base(query, context, [6])
                    self.assertEqual(result["rerank_status"],status)
                    expected = list(reversed(texts))[:4] if status=="applied" else texts[:4]
                    self.assertEqual([h["chunk_id"] for h in result["results"][:4]], [h["chunk_id"] for h in expected])
                    self.assertEqual(result["results"][4:],images)


if __name__ == "__main__":
    unittest.main()
