"""[C4.2] ADK BasePlugin for Google Cloud Model Armor API & Tool SSRF/IDOR Guardrails.

1. Calls Google Cloud Model Armor API (`google.cloud.modelarmor_v1` `sanitize_user_prompt` &
   `sanitize_model_response`) when `MODEL_ARMOR_TEMPLATE_ID` is configured, with optional
   Fail-Closed protection (`MODEL_ARMOR_FAIL_CLOSED=true`).
2. Defense-in-depth local prompt-injection & PII redaction patterns for offline/dev testing.
3. `before_tool_callback`: blocks private-IP / metadata server SSRF (`169.254.169.254`) and
   prevents caller `user_id` tampering (IDOR / Confused Deputy protection).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

logger = logging.getLogger(__name__)

_LOCAL_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt\s+override", re.IGNORECASE),
    re.compile(r"reveal\s+your\s+(system\s+)?instructions", re.IGNORECASE),
]
_LOCAL_PII_PATTERNS = [
    re.compile(r"\b\d{6}-[1-4]\d{6}\b"),  # Korean RRN
    re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b"),  # Credit card pattern
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),  # Google API Key
]
_SSRF_PRIVATE_IP_PATTERN = re.compile(
    r"https?://(127\.|10\.|169\.254\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|localhost\b)",
    re.IGNORECASE,
)


class ModelArmorGuardrailPlugin(BasePlugin):
    """Enforces Model Armor input/output screening, PII defense, and tool SSRF/IDOR checks."""

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        template_id: str | None = None,
        fail_closed: bool | None = None,
    ) -> None:
        super().__init__(name="model_armor_guardrail")
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        self.location = location or os.environ.get("MODEL_ARMOR_LOCATION", "us-central1")
        self.template_id = template_id or os.environ.get("MODEL_ARMOR_TEMPLATE_ID", "")
        self.enabled = os.environ.get("MODEL_ARMOR_ENABLED", "true").lower() in ("true", "1")
        self.fail_closed = (
            fail_closed
            if fail_closed is not None
            else os.environ.get("MODEL_ARMOR_FAIL_CLOSED", "false").lower() in ("true", "1")
        )
        self._ma_client: Any = None

    def _get_model_armor_client(self) -> Any:
        if self._ma_client is not None:
            return self._ma_client
        from google.api_core.client_options import ClientOptions
        from google.cloud import modelarmor_v1

        endpoint = f"modelarmor.{self.location}.rep.googleapis.com"
        self._ma_client = modelarmor_v1.ModelArmorClient(
            client_options=ClientOptions(api_endpoint=endpoint)
        )
        return self._ma_client

    def _extract_latest_user_text(self, llm_request: LlmRequest) -> str:
        if not llm_request.contents:
            return ""
        for content in reversed(llm_request.contents):
            if content.role == "user" and content.parts:
                return " ".join(p.text for p in content.parts if getattr(p, "text", None))
        return ""

    def _check_model_armor_api(self, text: str, is_user_prompt: bool = True) -> tuple[bool, str]:
        # 1. Fast local defense-in-depth screening (prompt injection + high-risk PII)
        for pattern in _LOCAL_INJECTION_PATTERNS:
            if pattern.search(text):
                return True, "Matched prompt injection signature"
        for pattern in _LOCAL_PII_PATTERNS:
            if pattern.search(text):
                return True, "Matched sensitive PII/secret pattern"

        # 2. Real Google Cloud Model Armor API screening when template is configured
        if self.project_id and self.template_id:
            try:
                from google.cloud import modelarmor_v1

                client = self._get_model_armor_client()
                template_name = (
                    self.template_id
                    if self.template_id.startswith("projects/")
                    else f"projects/{self.project_id}/locations/{self.location}/templates/{self.template_id}"
                )
                if is_user_prompt:
                    req = modelarmor_v1.SanitizeUserPromptRequest(
                        name=template_name,
                        user_prompt_data=modelarmor_v1.DataItem(text=text),
                    )
                    resp = client.sanitize_user_prompt(request=req)
                else:
                    req = modelarmor_v1.SanitizeModelResponseRequest(
                        name=template_name,
                        model_response_data=modelarmor_v1.DataItem(text=text),
                    )
                    resp = client.sanitize_model_response(request=req)

                state = getattr(resp.sanitization_result, "filter_match_state", None)
                if state and "MATCH_FOUND" in str(state):
                    return True, f"Model Armor API violation ({state})"
            except Exception as exc:
                logger.warning("Model Armor API call failed (fail_closed=%s): %s", self.fail_closed, exc)
                if self.fail_closed:
                    return True, "Model Armor API unavailable (fail-closed)"
        return False, ""

    async def on_user_message_callback(
        self,
        *,
        invocation_context: Any,
        user_message: types.Content,
    ) -> types.Content | None:
        """Sanitizes user input BEFORE it is persisted into Managed Session / Memory Bank."""
        if not self.enabled or not user_message.parts:
            return None
        user_text = " ".join(p.text for p in user_message.parts if getattr(p, "text", None))
        if not user_text:
            return None
        blocked, reason = self._check_model_armor_api(user_text, is_user_prompt=True)
        if blocked:
            logger.warning(
                "Sanitized unsafe user message before session persistence",
                extra={"json_fields": {"event": "guardrail_block", "stage": "session_ingress", "reason": reason}},
            )
            return types.Content(
                role="user",
                parts=[types.Part.from_text(text="[USER_PROMPT_REDACTED_BY_MODEL_ARMOR]")],
            )
        return None

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        if not self.enabled:
            return None
        user_text = self._extract_latest_user_text(llm_request)
        if not user_text:
            return None

        blocked = "[USER_PROMPT_REDACTED_BY_MODEL_ARMOR]" in user_text
        reason = "Redacted at session ingress" if blocked else ""
        if not blocked:
            blocked, reason = self._check_model_armor_api(user_text, is_user_prompt=True)
        if blocked:
            logger.warning(
                "Blocked user prompt via ModelArmorGuardrailPlugin",
                extra={"json_fields": {"event": "guardrail_block", "stage": "input", "reason": reason}},
            )
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(
                            text="보안 정책(Model Armor Guardrail)에 의해 부적절하거나 민감한 요청이 감지되어 차단되었습니다."
                        )
                    ],
                )
            )
        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        if not self.enabled or not llm_response.content or not llm_response.content.parts:
            return None
        model_text = " ".join(
            p.text for p in llm_response.content.parts if getattr(p, "text", None)
        )
        if not model_text:
            return None

        blocked, reason = self._check_model_armor_api(model_text, is_user_prompt=False)
        if blocked:
            logger.warning(
                "Blocked unsafe model response via ModelArmorGuardrailPlugin",
                extra={"json_fields": {"event": "guardrail_block", "stage": "output", "reason": reason}},
            )
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(
                            text="응답에 민감 정보 또는 보안 정책 위반 요소가 포함되어 출력이 마스킹되었습니다."
                        )
                    ],
                )
            )
        return None

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        """[C4.2] Blocks metadata/private-IP SSRF and prevents cross-user IDOR in tool arguments."""
        if not self.enabled:
            return None
        serialized_args = str(tool_args)
        if _SSRF_PRIVATE_IP_PATTERN.search(serialized_args) or "metadata.google.internal" in serialized_args:
            logger.warning(
                "Blocked SSRF attempt in tool arguments",
                extra={"json_fields": {"event": "guardrail_block", "stage": "tool_ssrf", "tool": tool.name}},
            )
            return {"error": f"Security Policy Violation: Unsafe SSRF target rejected for tool '{tool.name}'."}

        # Prevent Confused Deputy / IDOR: reject tool args attempting to impersonate another user_id
        session_user_id = getattr(getattr(tool_context, "_invocation_context", None), "user_id", None)
        if not session_user_id:
            session_obj = getattr(getattr(tool_context, "_invocation_context", None), "session", None)
            session_user_id = getattr(session_obj, "user_id", None)
        arg_user_id = tool_args.get("user_id")
        if session_user_id and arg_user_id and str(arg_user_id) != str(session_user_id):
            logger.warning(
                "Blocked cross-user IDOR attempt in tool arguments",
                extra={"json_fields": {"event": "guardrail_block", "stage": "tool_idor", "tool": tool.name}},
            )
            return {"error": f"Security Policy Violation: Caller identity mismatch rejected for tool '{tool.name}'."}

        return None
