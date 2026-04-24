import logging
from typing import Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from holmes.config import Config
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import PrerequisiteCacheMode, ToolsetTag
from holmes.core.tools_utils.tool_executor import ToolExecutor

admin_app = FastAPI()

_CONFIG: Config
_DAL: SupabaseDal


class ReloadResponse(BaseModel):
    status: str
    component: str
    detail: str = ""
    counts: Dict[str, int] = {}


def init_admin_app(main_app: FastAPI, config: Config, dal: SupabaseDal) -> None:
    # TODO: Add authentication (API key header or similar) before exposing in production.
    global _CONFIG, _DAL
    _CONFIG = config
    _DAL = dal
    main_app.mount("/api/admin", admin_app)


def _build_toolset_counts(executor: ToolExecutor) -> Dict[str, int]:
    toolsets = executor.toolsets
    total = len(toolsets)
    enabled = sum(1 for t in toolsets if t.enabled)
    runbook_count = 0
    for t in toolsets:
        if t.name == "runbook" and t.tools:
            runbook_count = len(getattr(t.tools[0], "available_runbooks", []))
            break
    return {"toolsets_total": total, "toolsets_enabled": enabled, "runbooks": runbook_count}


def _reload_and_rebuild_toolsets() -> ToolExecutor:
    _CONFIG.reload_toolsets()
    return _CONFIG.create_tool_executor(
        dal=_DAL,
        toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLUSTER],
        enable_all_toolsets_possible=False,
        prerequisite_cache=PrerequisiteCacheMode.DISABLED,
        reuse_executor=True,
    )


@admin_app.post("/reload/toolsets", response_model=ReloadResponse)
def reload_toolsets() -> ReloadResponse:
    try:
        executor = _reload_and_rebuild_toolsets()
        counts = _build_toolset_counts(executor)
        return ReloadResponse(
            status="ok",
            component="toolsets",
            detail=f"{counts['toolsets_total']} toolsets loaded, {counts['toolsets_enabled']} enabled, {counts['runbooks']} runbooks",
            counts=counts,
        )
    except Exception as e:
        logging.error("Failed to reload toolsets", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@admin_app.post("/reload/models", response_model=ReloadResponse)
def reload_models() -> ReloadResponse:
    try:
        result = _CONFIG.reload_models()
        model_count = result.get("models_loaded", 0)
        return ReloadResponse(
            status="ok",
            component="models",
            detail=f"{model_count} models loaded",
            counts={"models_loaded": model_count},
        )
    except Exception as e:
        logging.error("Failed to reload models", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@admin_app.post("/reload", response_model=ReloadResponse)
def reload_all() -> ReloadResponse:
    try:
        executor = _reload_and_rebuild_toolsets()
        model_result = _CONFIG.reload_models()

        counts = _build_toolset_counts(executor)
        counts["models_loaded"] = model_result.get("models_loaded", 0)
        return ReloadResponse(
            status="ok",
            component="all",
            detail=f"{counts['toolsets_total']} toolsets ({counts['toolsets_enabled']} enabled), {counts['models_loaded']} models",
            counts=counts,
        )
    except Exception as e:
        logging.error("Failed to reload all config", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
