"""Process-wide ADK Session, Memory Bank & Artifact services shared by all serving surfaces.

[C2.1 & C2.2] Default Architecture — Agent Platform Managed Sessions & Memory Bank:
- Session Store : `VertexAiSessionService` via `GOOGLE_CLOUD_AGENT_ENGINE_ID` (Zero-Ops, auto TTL).
- Memory Bank   : `VertexAiMemoryBankService` via `GOOGLE_CLOUD_AGENT_ENGINE_ID` (automatic cross-session fact extraction).
- Artifacts     : `GcsArtifactService` via `LOGS_BUCKET_NAME`.

Note (Self-Hosted Option):
  If your workload strictly requires private-VPC-only relational storage or sub-ms custom caching,
  you may optionally replace this with Self-Hosted Cloud SQL (`postgresql+asyncpg://`) or Memorystore Redis.
"""

from __future__ import annotations

import functools
import os

from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.cli.service_registry import get_service_registry

SESSION_SERVICE_URI = "shared://session"
MEMORY_SERVICE_URI = "shared://memory"
ARTIFACT_SERVICE_URI = "shared://artifact"


def _agent_engine_location() -> str:
    """Resolves regional location for Agent Platform Session & Memory Bank (never 'global')."""
    loc = (
        os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
        or os.environ.get("GOOGLE_CLOUD_REGION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION")
        or "us-central1"
    )
    return "us-central1" if loc == "global" else loc


@functools.cache
def get_session_service():
    """Returns Agent Platform Managed `VertexAiSessionService`, with InMemory fallback for local dev."""
    agent_engine_id = os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID", "").strip()
    if agent_engine_id:
        from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService

        return VertexAiSessionService(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=_agent_engine_location(),
            agent_engine_id=agent_engine_id,
        )

    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    return InMemorySessionService()


@functools.cache
def get_memory_service():
    """Returns Agent Platform Managed `VertexAiMemoryBankService`, with InMemory fallback for local dev."""
    agent_engine_id = os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID", "").strip()
    if agent_engine_id:
        from google.adk.memory import VertexAiMemoryBankService

        return VertexAiMemoryBankService(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=_agent_engine_location(),
            agent_engine_id=agent_engine_id,
        )

    from google.adk.memory import InMemoryMemoryService

    return InMemoryMemoryService()


@functools.cache
def get_artifact_service():
    """Process-wide artifact service: GCS when LOGS_BUCKET_NAME is set, else in-memory."""
    if bucket := os.environ.get("LOGS_BUCKET_NAME"):
        return GcsArtifactService(bucket_name=bucket)
    return InMemoryArtifactService()


_registry = get_service_registry()
_registry.register_session_service("shared", lambda uri, **kw: get_session_service())
if hasattr(_registry, "register_memory_service"):
    _registry.register_memory_service("shared", lambda uri, **kw: get_memory_service())
_registry.register_artifact_service("shared", lambda uri, **kw: get_artifact_service())
