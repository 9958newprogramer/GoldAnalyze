"""Show miss, exact reuse, and semantic-candidate behavior with an isolated database."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from time import perf_counter

from app.bootstrap import build_services
from app.config import Settings


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="aurumlab-memory-") as directory:
        services = build_services(
            Settings(
                app_database_path=str(Path(directory) / "demo.db"),
                router_llm_api_key=None,
                llm_api_key=None,
            )
        )
        questions = [
            "黄金1小时K，20日均线上穿60日均线做多。",
            "请回测黄金1小时K线，20/60小时均线交叉策略。",
            "请回测黄金1小时K线，21/61小时均线交叉策略。",
        ]
        labels = ["first_miss", "equivalent_exact_hit", "different_semantic_candidate"]
        output = []
        for label, question in zip(labels, questions, strict=True):
            started = perf_counter()
            run = await services.agent.run(question)
            output.append(
                {
                    "case": label,
                    "run_id": run.run_id,
                    "cache_status": run.cache_status,
                    "source_run_id": run.cache.source_run_id if run.cache else None,
                    "similarity_score": run.cache.similarity_score if run.cache else None,
                    "tool_executions": sum(
                        item.get("phase") == "execution" for item in run.tool_audit
                    ),
                    "latency_ms": round((perf_counter() - started) * 1_000, 2),
                }
            )
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
