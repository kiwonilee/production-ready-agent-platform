"""[C6.1] ADK BasePlugin for Per-Turn LLM Loop Prevention & Token Spend Caps.

Tracks call counts strictly per `invocation_id` (resets automatically on every new user turn)
and enforces `max_output_tokens` directly on each `LlmRequest.config`.
"""

from __future__ import annotations

import os
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types


class CostTokenGuardrailPlugin(BasePlugin):
    """Prevents runaway agent loops per user turn and caps max_output_tokens."""

    def __init__(
        self,
        max_llm_calls_per_turn: int | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        super().__init__(name="cost_token_guardrail")
        self.max_llm_calls = max_llm_calls_per_turn or int(
            os.environ.get("MAX_LLM_CALLS_PER_TURN", "10")
        )
        self.max_output_tokens = max_output_tokens or int(
            os.environ.get("MAX_OUTPUT_TOKENS_PER_TURN", "2048")
        )
        self._turn_counts: dict[str, int] = {}

    async def on_user_message_callback(
        self,
        *,
        invocation_context: Any,
        user_message: types.Content,
    ) -> types.Content | None:
        inv_id = getattr(invocation_context, "invocation_id", None)
        if inv_id:
            self._turn_counts[inv_id] = 0
        return None

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        inv_ctx = getattr(callback_context, "_invocation_context", None)
        inv_id = getattr(inv_ctx, "invocation_id", "default_turn")

        # Keep counter strictly scoped to the active user turn (never locks out future turns!)
        current_calls = self._turn_counts.get(inv_id, 0) + 1
        self._turn_counts[inv_id] = current_calls
        if len(self._turn_counts) > 1000:
            self._turn_counts.clear()
            self._turn_counts[inv_id] = current_calls

        if current_calls > self.max_llm_calls:
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(
                            text=f"FinOps 가드레일: 단일 요청 내 최대 모델 호출 한도({self.max_llm_calls}회)에 도달하여 루프를 안전 종료합니다."
                        )
                    ],
                )
            )

        # Enforce max_output_tokens cap directly on LlmRequest config
        if llm_request.config is None:
            llm_request.config = types.GenerateContentConfig(
                max_output_tokens=self.max_output_tokens
            )
        elif not llm_request.config.max_output_tokens or llm_request.config.max_output_tokens > self.max_output_tokens:
            llm_request.config.max_output_tokens = self.max_output_tokens

        return None
