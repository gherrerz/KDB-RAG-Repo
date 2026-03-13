"""Compara latencia sintética pre/post entre HEAD y el árbol de trabajo actual."""

from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
from textwrap import dedent

BENCH_CODE = dedent(
    r'''
    import json
    from pathlib import Path
    from time import perf_counter, sleep

    def stats(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)
        if not ordered:
            return {"mean_ms": 0.0, "p95_ms": 0.0}
        p95_index = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
        return {
            "mean_ms": round(sum(ordered) / len(ordered), 2),
            "p95_ms": round(ordered[p95_index], 2),
        }


    def bench_run_query(module_query: bool, iterations: int = 14) -> dict[str, float]:
        import coderag.api.query_service as qs
        from coderag.core.models import RetrievalChunk

        original_hybrid = qs.hybrid_search
        original_rerank = qs.rerank
        original_expand = qs.expand_with_graph
        original_discover = qs._discover_repo_modules
        original_is_module = qs._is_module_query
        original_assemble = qs.assemble_context
        original_answer_client = qs.AnswerClient

        class _Client:
            enabled = False

        def fake_hybrid(repo_id: str, query: str, top_n: int):
            sleep(0.06)
            return [
                RetrievalChunk(
                    id=f"id-{idx}",
                    text="sample",
                    score=1.0,
                    metadata={"path": f"src/f{idx}.py", "start_line": 1, "end_line": 2},
                )
                for idx in range(max(1, min(top_n, 20)))
            ]

        def fake_rerank(chunks, top_k: int):
            sleep(0.02)
            return chunks[: max(1, min(top_k, len(chunks)))]

        def fake_expand(chunks):
            sleep(0.08)
            return [{"seed": "id-1", "labels": ["Symbol"], "props": {"name": "x"}}]

        def fake_discover(repo_id: str):
            sleep(0.07)
            return ["core", "api"]

        def fake_assemble(chunks, graph_records, max_tokens: int):
            return "ctx"

        try:
            qs.hybrid_search = fake_hybrid
            qs.rerank = fake_rerank
            qs.expand_with_graph = fake_expand
            qs._discover_repo_modules = fake_discover
            qs._is_module_query = (lambda q: module_query)
            qs.assemble_context = fake_assemble
            qs.AnswerClient = _Client

            latencies = []
            for _ in range(iterations):
                started = perf_counter()
                qs.run_query(
                    repo_id="repo-bench",
                    query="list modules" if module_query else "what is auth",
                    top_n=60,
                    top_k=15,
                )
                latencies.append((perf_counter() - started) * 1000.0)
            return stats(latencies)
        finally:
            qs.hybrid_search = original_hybrid
            qs.rerank = original_rerank
            qs.expand_with_graph = original_expand
            qs._discover_repo_modules = original_discover
            qs._is_module_query = original_is_module
            qs.assemble_context = original_assemble
            qs.AnswerClient = original_answer_client


    def bench_hybrid_search(iterations: int = 14) -> dict[str, float]:
        import coderag.retrieval.hybrid_search as hs
        from coderag.core.models import RetrievalChunk

        original_embedder = hs.EmbeddingClient
        original_chroma = hs.ChromaIndex
        original_bm25 = hs.GLOBAL_BM25

        class FakeEmbeddingClient:
            def __init__(self):
                self.client = object()

            def embed_texts(self, texts):
                return [[0.1] * 4 for _ in texts]

        class FakeChromaIndex:
            def query(self, collection_name, query_embedding, top_n, where=None):
                sleep(0.05)
                return {
                    "ids": [[f"{collection_name}-1"]],
                    "documents": [["doc"]],
                    "metadatas": [[{"path": "src/a.py", "start_line": 1, "end_line": 2}]],
                    "distances": [[0.2]],
                }

        class FakeBM25:
            def query(self, repo_id, text, top_n=50):
                sleep(0.04)
                return [
                    {
                        "id": "bm25-1",
                        "text": "doc",
                        "score": 1.0,
                        "metadata": {"path": "src/b.py", "start_line": 1, "end_line": 2},
                    }
                ]

        try:
            hs.EmbeddingClient = FakeEmbeddingClient
            hs.ChromaIndex = FakeChromaIndex
            hs.GLOBAL_BM25 = FakeBM25()

            latencies = []
            for _ in range(iterations):
                started = perf_counter()
                hs.hybrid_search(repo_id="repo-bench", query="auth", top_n=20)
                latencies.append((perf_counter() - started) * 1000.0)
            return stats(latencies)
        finally:
            hs.EmbeddingClient = original_embedder
            hs.ChromaIndex = original_chroma
            hs.GLOBAL_BM25 = original_bm25


    def bench_storage_preflight(iterations: int = 12) -> dict[str, float]:
        import coderag.core.storage_health as sh

        class FakeSettings:
            health_check_strict = True
            health_check_timeout_seconds = 5.0
            health_check_ttl_seconds = 0.0
            health_check_openai = True
            health_check_redis = True
            workspace_path = Path("./storage/workspace")
            redis_url = "redis://localhost:6379/0"
            openai_api_key = "x"
            neo4j_uri = "bolt://127.0.0.1:7687"
            neo4j_user = "neo4j"
            neo4j_password = "password"

        original_get_settings = sh.get_settings
        original_workspace = sh._check_workspace
        original_metadata = sh._check_metadata_sqlite
        original_chroma = sh._check_chroma
        original_neo4j = sh._check_neo4j
        original_bm25 = sh._check_bm25
        original_openai = sh._check_openai
        original_redis = sh._check_redis

        def sleepy(name):
            def fn(*args, **kwargs):
                sleep(0.05)
                return {"name": name, "ok": True}
            return fn

        try:
            sh.get_settings = lambda: FakeSettings()
            sh._check_workspace = sleepy("workspace")
            sh._check_metadata_sqlite = sleepy("metadata")
            sh._check_chroma = sleepy("chroma")
            sh._check_neo4j = sleepy("neo4j")
            sh._check_bm25 = sleepy("bm25")
            sh._check_openai = sleepy("openai")
            sh._check_redis = sleepy("redis")
            sh._CACHE.clear()

            latencies = []
            for _ in range(iterations):
                started = perf_counter()
                sh.run_storage_preflight(context="query", repo_id="repo-bench", force=True)
                latencies.append((perf_counter() - started) * 1000.0)
            return stats(latencies)
        finally:
            sh.get_settings = original_get_settings
            sh._check_workspace = original_workspace
            sh._check_metadata_sqlite = original_metadata
            sh._check_chroma = original_chroma
            sh._check_neo4j = original_neo4j
            sh._check_bm25 = original_bm25
            sh._check_openai = original_openai
            sh._check_redis = original_redis


    output = {
        "run_query_general": bench_run_query(module_query=False),
        "run_query_module": bench_run_query(module_query=True),
        "hybrid_search": bench_hybrid_search(),
        "storage_preflight": bench_storage_preflight(),
    }
    print(json.dumps(output))
    '''
)


def run_bench(python_exe: str, repo_path: Path) -> dict[str, dict[str, float]]:
    completed = subprocess.run(
        [python_exe, "-c", BENCH_CODE],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    )
    raw = completed.stdout.strip().splitlines()[-1]
    return json.loads(raw)


def pct_delta(pre: float, post: float) -> float:
    if pre == 0:
        return 0.0
    return round(((post - pre) / pre) * 100.0, 2)


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: python scripts/benchmark_compare_pre_post.py <pre_repo_path>")
        return 2

    pre_path = Path(sys.argv[1]).resolve()
    post_path = Path.cwd().resolve()

    python_exe = os.environ.get("PYTHON_BENCH_EXE")
    if not python_exe:
        python_exe = sys.executable

    pre = run_bench(python_exe=python_exe, repo_path=pre_path)
    post = run_bench(python_exe=python_exe, repo_path=post_path)

    report: dict[str, dict[str, float]] = {}
    for key in pre:
        pre_mean = float(pre[key]["mean_ms"])
        post_mean = float(post[key]["mean_ms"])
        report[key] = {
            "pre_mean_ms": round(pre_mean, 2),
            "post_mean_ms": round(post_mean, 2),
            "delta_mean_pct": pct_delta(pre_mean, post_mean),
            "pre_p95_ms": round(float(pre[key]["p95_ms"]), 2),
            "post_p95_ms": round(float(post[key]["p95_ms"]), 2),
            "delta_p95_pct": pct_delta(
                float(pre[key]["p95_ms"]),
                float(post[key]["p95_ms"]),
            ),
        }

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
