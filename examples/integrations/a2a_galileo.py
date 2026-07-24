"""A2A dual-agent → Galileo (orchestrator + researcher), matching Galileo docs pattern.

Real SDKs only — no mock success. Instruments A2A + (optionally) LangChain, starts an
in-process A2A researcher server, and has an orchestrator delegate via ``send_message``.

Usage:
  pip install galileo-a2a 'a2a-sdk[http-server]' 'galileo[otel]' uvicorn httpx
  # Optional LLM path (recommended):
  pip install langchain-openai langgraph opentelemetry-instrumentation-langchain
  export GALILEO_API_KEY=... OPENAI_API_KEY=...
  export GALILEO_PROJECT=rax-galileo-labs GALILEO_LOG_STREAM=a2a-integration
  python examples/integrations/a2a_galileo.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))

from _common import load_keys, project_stream, require_galileo, setup_galileo_otel

RESEARCHER_PORT = int(os.environ.get("A2A_RESEARCHER_PORT", "9867"))


def _agent_card(url: str):
    from a2a.types import AgentCapabilities, AgentCard, AgentSkill

    return AgentCard(
        name="dizzygraph-researcher",
        description="ML infra researcher served over A2A",
        url=url,
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="qa",
                name="Q&A",
                description="Answer short ML infra questions",
                tags=["ml", "infra"],
            )
        ],
    )


def _research_answer(query: str) -> str:
    """Prefer a live LLM; fall back to a deterministic grounded snippet (still real A2A)."""
    if load_keys().get("openai") or os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI

            client = OpenAI()
            resp = client.chat.completions.create(
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {
                        "role": "system",
                        "content": "You are an ML platform engineer. Answer in 2-3 sentences.",
                    },
                    {"role": "user", "content": query},
                ],
                max_tokens=200,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            print(f"WARN: OpenAI researcher failed ({type(exc).__name__}); using local facts")
    q = query.lower()
    if "gradient checkpoint" in q:
        return (
            "Gradient checkpointing trades compute for memory by recomputing activations "
            "in the backward pass instead of storing them all."
        )
    return f"Research note for: {query[:180]}"


async def _run_dual_agent(project: str, stream: str) -> int:
    try:
        from galileo_a2a import A2AInstrumentor
    except ImportError as exc:
        print(
            "ERROR: install galileo-a2a (+ OTel instrumentation):\n"
            "  pip install galileo-a2a a2a-sdk 'galileo[otel]' "
            "opentelemetry-instrumentation uvicorn starlette httpx\n"
            "Note: PyPI galileo-a2a 1.0.0 declares galileo<2; this lab uses galileo>=2.3 — "
            "instrumentor usually still imports; if not, open an upstream issue.\n"
            f"Detail: {exc}"
        )
        return 2

    try:
        import httpx
        import uvicorn
        from a2a.client import ClientConfig, ClientFactory
        from a2a.server.agent_execution import AgentExecutor, RequestContext
        from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
        from a2a.server.events import EventQueue, InMemoryQueueManager
        from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
        from a2a.server.tasks import InMemoryTaskStore
        from a2a.types import (
            Message,
            Role,
            TaskState,
            TaskStatus,
            TaskStatusUpdateEvent,
            TextPart,
        )
        from starlette.applications import Starlette
    except ImportError as exc:
        print(
            "ERROR: A2A dual-agent deps missing.\n"
            "  pip install galileo-a2a 'a2a-sdk[http-server]' uvicorn httpx\n"
            f"Detail: {exc}"
        )
        return 2

    try:
        provider = setup_galileo_otel(project=project, log_stream=stream)
    except ImportError as exc:
        print(f"ERROR: {exc}")
        return 2

    A2AInstrumentor().instrument(tracer_provider=provider, agent_name="dizzygraph-orchestrator")
    try:
        from opentelemetry.instrumentation.langchain import LangchainInstrumentor

        LangchainInstrumentor().instrument(tracer_provider=provider)
        print("langchain instrumentor: on")
    except ImportError:
        print("langchain instrumentor: skipped (optional)")

    url = f"http://127.0.0.1:{RESEARCHER_PORT}"
    card = _agent_card(url)

    class ResearcherExecutor(AgentExecutor):
        async def execute(self, ctx: RequestContext, queue: EventQueue) -> None:
            query = ctx.get_user_input()
            answer = await asyncio.to_thread(_research_answer, query)
            await queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=ctx.task_id,
                    context_id=ctx.context_id,
                    final=True,
                    status=TaskStatus(
                        state=TaskState.completed,
                        message=Message(
                            message_id=str(uuid.uuid4()),
                            role=Role.agent,
                            parts=[TextPart(text=answer)],
                        ),
                    ),
                )
            )

        async def cancel(self, ctx: RequestContext, queue: EventQueue) -> None:
            await queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=ctx.task_id,
                    context_id=ctx.context_id,
                    final=True,
                    status=TaskStatus(state=TaskState.canceled),
                )
            )

    app = Starlette()
    A2AStarletteApplication(
        agent_card=card,
        http_handler=DefaultRequestHandler(
            agent_executor=ResearcherExecutor(),
            task_store=InMemoryTaskStore(),
            queue_manager=InMemoryQueueManager(),
        ),
    ).add_routes_to_app(app)

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=RESEARCHER_PORT, log_level="warning")
    )
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.8)

    user_query = os.environ.get(
        "A2A_QUERY",
        "In two sentences: what is gradient checkpointing?",
    )
    # Orchestrator: plan (trivial) → delegate over A2A → synthesize
    research_query = f"Research factually: {user_query}"

    client = ClientFactory(
        config=ClientConfig(
            streaming=True,
            httpx_client=httpx.AsyncClient(timeout=httpx.Timeout(120)),
        ),
    ).create(card)

    msg = Message(
        message_id=str(uuid.uuid4()),
        role=Role.user,
        parts=[TextPart(text=research_query)],
        context_id="dizzygraph-a2a-session",
    )
    research_text = ""
    async for event in client.send_message(msg):
        if isinstance(event, tuple):
            task = event[0]
            done = (
                task.status
                and task.status.state == TaskState.completed
                and task.status.message
            )
            if done:
                part0 = task.status.message.parts[0]
                research_text = getattr(getattr(part0, "root", part0), "text", "") or getattr(
                    part0, "text", ""
                )
                break

    plan = (
        f"Orchestrator synthesis\n"
        f"Query: {user_query}\n"
        f"Researcher (A2A): {(research_text or '(empty)').strip()[:600]}"
    )
    print(f"galileo-a2a dual-agent → {project}/{stream}")
    print(f"researcher: {url}")
    print("── plan ──")
    print(plan)

    server.should_exit = True
    await server_task
    shutdown = getattr(provider, "shutdown", None) or getattr(provider, "force_flush", None)
    if callable(shutdown):
        try:
            shutdown()
        except TypeError:
            provider.force_flush(5000)

    if not research_text:
        print("ERROR: researcher returned empty response over A2A")
        return 1
    print("OK: orchestrator ↔ researcher A2A handoff traced")
    return 0


def main() -> int:
    err = require_galileo()
    if err:
        return err
    project, stream = project_stream("a2a-integration")
    try:
        return asyncio.run(_run_dual_agent(project, stream))
    except Exception as exc:
        print(f"ERROR: A2A dual-agent failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
