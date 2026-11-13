"""FastAPI Localhost Backend for the Interactive Production-Readiness Workbench."""

from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path
from typing import Any

# Ensure standalone `prod_ready` skill scripts are importable when running `web` directly from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILL_SCRIPTS_DIR = _REPO_ROOT / "skills" / "agent-production-readiness" / "scripts"
if _SKILL_SCRIPTS_DIR.is_dir() and str(_SKILL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS_DIR))

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from prod_ready.checklist import CustomParams
from prod_ready.patcher import (
    apply_patches,
    create_zip_bundle,
    generate_html_report,
    generate_readiness_report,
    preview_patches,
    reset_project_to_baseline,
)
from prod_ready.scanner import scan_project
from web.runtime_inspector import (
    DEFAULT_PROJECT_ID,
    DEFAULT_REGION,
    get_sample_runtime_json,
    inspect_deployed_agent,
    inspect_pasted_json,
    list_deployed_agents,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _find_skill_dir() -> Path | None:
    """Locates the standalone `agent-production-readiness` skill directory."""
    candidates: list[Path] = [
        _REPO_ROOT / "skills" / "agent-production-readiness",
        Path.cwd() / "skills" / "agent-production-readiness",
        Path("/app/skills/agent-production-readiness"),
    ]
    for cand in candidates:
        if (cand / "SKILL.md").is_file():
            return cand
    return None


class PatchRequest(BaseModel):
    target: str = "auto"
    items: list[str] = Field(default_factory=lambda: ["ALL"])
    custom_params: dict[str, Any] = Field(default_factory=dict)


class PasteJsonRequest(BaseModel):
    raw_json: str
    project_id: str = ""
    region: str = "us-central1"


def create_workbench_app(project_dir: str | Path) -> FastAPI:
    root = Path(project_dir).resolve()
    app = FastAPI(
        title="Production-Readiness Workbench",
        description="Localhost Interactive Checklist, Diff Preview & In-Place Hardening for Google Cloud AI Agents",
    )
    app.state.project_dir = root

    # Discover sibling sample projects if available so users can switch scenarios in the UI
    samples_parent = root.parent if root.parent.name == "samples" else (root / "samples")
    app.state.samples_parent = samples_parent if samples_parent.is_dir() else None

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html_file = STATIC_DIR / "index.html"
        return HTMLResponse(html_file.read_text(encoding="utf-8"))

    @app.get("/lab", response_class=HTMLResponse)
    async def hands_on_lab() -> HTMLResponse:
        lab_file = STATIC_DIR / "hands-on-lab.html"
        return HTMLResponse(lab_file.read_text(encoding="utf-8"))

    @app.get("/api/skill/download")
    async def api_skill_download() -> Response:
        skill_dir = _find_skill_dir()
        if not skill_dir:
            return Response(content=b"Skill directory not found", status_code=404)

        buf = io.BytesIO()
        skip_names = {"__pycache__", ".DS_Store", ".pytest_cache"}
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for item in sorted(skill_dir.rglob("*")):
                if any(part in skip_names for part in item.parts) or item.suffix == ".pyc":
                    continue
                arcname = Path("agent-production-readiness") / item.relative_to(skill_dir)
                tar.add(item, arcname=str(arcname), recursive=False)
        return Response(
            content=buf.getvalue(),
            media_type="application/gzip",
            headers={
                "Content-Disposition": 'attachment; filename="agent-production-readiness.tar.gz"'
            },
        )

    @app.get("/api/runtime/agents")
    async def api_runtime_agents(
        project_id: str = Query(default=DEFAULT_PROJECT_ID),
        region: str = Query(default=DEFAULT_REGION),
    ) -> dict[str, Any]:
        return list_deployed_agents(project_id=project_id, region=region)

    @app.get("/api/runtime/inspect")
    async def api_runtime_inspect(
        service_name: str = Query(...),
        runtime_type: str = Query(default="cloud_run"),
        project_id: str = Query(default=DEFAULT_PROJECT_ID),
        region: str = Query(default=DEFAULT_REGION),
    ) -> dict[str, Any]:
        return inspect_deployed_agent(
            project_id=project_id,
            region=region,
            service_name=service_name,
            runtime_type=runtime_type,
        )

    @app.post("/api/runtime/inspect-json")
    async def api_runtime_inspect_json(req: PasteJsonRequest) -> dict[str, Any]:
        try:
            return inspect_pasted_json(
                raw_json=req.raw_json,
                project_id_hint=req.project_id,
                region_hint=req.region,
            )
        except Exception as exc:
            return {"count": 0, "results": [], "error": str(exc)}

    @app.get("/api/runtime/sample-json")
    async def api_runtime_sample_json(
        runtime_type: str = Query(default="cloud_run"),
    ) -> dict[str, Any]:
        return get_sample_runtime_json(runtime_type=runtime_type)

    @app.get("/api/projects")
    async def api_projects() -> dict[str, Any]:
        available: list[dict[str, str]] = []
        if app.state.samples_parent and app.state.samples_parent.is_dir():
            for child in sorted(app.state.samples_parent.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    available.append({"name": child.name, "path": str(child.resolve())})
        if not available:
            available.append({"name": app.state.project_dir.name, "path": str(app.state.project_dir)})
        return {
            "active_name": app.state.project_dir.name,
            "active_path": str(app.state.project_dir),
            "projects": available,
        }

    @app.post("/api/select-project")
    async def api_select_project(name: str = Query(...)) -> dict[str, Any]:
        if app.state.samples_parent and (app.state.samples_parent / name).is_dir():
            app.state.project_dir = (app.state.samples_parent / name).resolve()
        return {
            "active_name": app.state.project_dir.name,
            "active_path": str(app.state.project_dir),
        }

    @app.get("/api/scan")
    async def api_scan(target: str = Query(default="auto")) -> dict[str, Any]:
        report = scan_project(app.state.project_dir, target_override=target)
        return report.to_dict()

    @app.post("/api/diff")
    async def api_diff(req: PatchRequest) -> dict[str, Any]:
        params = CustomParams.from_dict(req.custom_params)
        patches = preview_patches(
            app.state.project_dir,
            target_override=req.target,
            selected_items=req.items,
            custom_params=params,
        )
        return {
            "project_dir": str(app.state.project_dir),
            "target": req.target,
            "patch_count": len(patches),
            "patches": [p.to_dict() for p in patches],
        }

    @app.post("/api/apply")
    async def api_apply(req: PatchRequest) -> dict[str, Any]:
        params = CustomParams.from_dict(req.custom_params)
        return apply_patches(
            app.state.project_dir,
            target_override=req.target,
            selected_items=req.items,
            custom_params=params,
        )

    @app.get("/api/export/report", response_class=PlainTextResponse)
    async def api_export_report(target: str = Query(default="auto")) -> Response:
        md = generate_readiness_report(app.state.project_dir, target_override=target)
        return Response(
            content=md.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="READINESS_REPORT.md"'
            },
        )

    @app.get("/api/export/html", response_class=HTMLResponse)
    async def api_export_html(
        target: str = Query(default="auto"),
        download: bool = Query(default=False),
    ) -> Response:
        html_doc = generate_html_report(app.state.project_dir, target_override=target)
        headers = {}
        if download:
            headers["Content-Disposition"] = 'attachment; filename="READINESS_REPORT.html"'
        return Response(
            content=html_doc.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers=headers,
        )

    @app.post("/api/reset")
    async def api_reset(target: str = Query(default="auto")) -> dict[str, Any]:
        return reset_project_to_baseline(app.state.project_dir, target_override=target)

    @app.post("/api/export/zip")
    async def api_export_zip(req: PatchRequest) -> Response:
        params = CustomParams.from_dict(req.custom_params)
        zip_bytes = create_zip_bundle(
            app.state.project_dir,
            target_override=req.target,
            selected_items=req.items,
            custom_params=params,
        )
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="production-ready-bundle.zip"'
            },
        )

    return app
