"""LangGraph orchestration engine (``orchestration.engine: langgraph``).

Wraps the same plan → execute → synthesize steps as the native engine in a LangGraph
``StateGraph``. Authorization stays in the tool registry, so swapping engines never
changes security behaviour. Requires ``pip install 'webmind[langgraph]'``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from webmind.core.errors import ProviderNotInstalledError
from webmind.core.types import ChatResponse
from webmind.intelligence.orchestrator import NativeOrchestrator, TurnState

if TYPE_CHECKING:
    from webmind.runtime import WebMindRuntime


class _GraphState(TypedDict, total=False):
    turn: TurnState
    response: ChatResponse


class LangGraphOrchestrator(NativeOrchestrator):
    def __init__(self, runtime: WebMindRuntime) -> None:
        super().__init__(runtime)
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise ProviderNotInstalledError("langgraph orchestration", "langgraph") from exc

        async def plan(state: _GraphState) -> dict[str, Any]:
            turn = state["turn"]
            turn.plan = await self.make_plan(turn)
            return {"turn": turn}

        async def execute(state: _GraphState) -> dict[str, Any]:
            await self.execute(state["turn"])
            return {"turn": state["turn"]}

        async def synthesize(state: _GraphState) -> dict[str, Any]:
            return {"response": await self.synthesize(state["turn"])}

        def route(state: _GraphState) -> str:
            p = state["turn"].plan
            return "synthesize" if p is None or p.intent == "write_attempt" or not p.calls else "execute"

        g = StateGraph(_GraphState)
        g.add_node("plan", plan)
        g.add_node("execute", execute)
        g.add_node("synthesize", synthesize)
        g.add_edge(START, "plan")
        g.add_conditional_edges("plan", route, {"execute": "execute", "synthesize": "synthesize"})
        g.add_edge("execute", "synthesize")
        g.add_edge("synthesize", END)
        self.graph = g.compile()

    async def chat(self, principal, question, conversation_id=None):  # noqa: ANN001
        turn = self.begin(principal, question, conversation_id)
        result = await self.graph.ainvoke({"turn": turn})
        return result["response"]
