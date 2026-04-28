import logging
from typing import Dict, Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from holmes.config import Config
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import PrerequisiteCacheMode, ToolsetTag
from holmes.core.tools_utils.tool_executor import ToolExecutor

admin_app = FastAPI()

_CONFIG: Optional[Config] = None
_DAL: Optional[SupabaseDal] = None


def _require_init() -> Tuple[Config, SupabaseDal]:
    """Return (_CONFIG, _DAL) or raise 503 if init_admin_app hasn't run."""
    if _CONFIG is None or _DAL is None:
        raise HTTPException(status_code=503, detail="Admin app not initialized")
    return _CONFIG, _DAL


class ReloadResponse(BaseModel):
    status: str
    component: str
    detail: str = ""
    counts: Dict[str, int] = {}


def init_admin_app(main_app: FastAPI, config: Config, dal: SupabaseDal) -> None:
    """Register the admin sub-app on *main_app* under ``/api/admin``."""
    global _CONFIG, _DAL
    _CONFIG = config
    _DAL = dal
    main_app.mount("/api/admin", admin_app)


def _build_toolset_counts(executor: ToolExecutor) -> Dict[str, int]:
    """Return total, enabled, and runbook counts from a ToolExecutor."""
    total = len(executor.toolsets)
    enabled = len(executor.enabled_toolsets)
    runbook_count = 0
    for t in executor.enabled_toolsets:
        if t.name == "runbook" and t.tools:
            runbook_count = len(getattr(t.tools[0], "available_runbooks", []))
            break
    return {"toolsets_total": total, "toolsets_enabled": enabled, "runbooks": runbook_count}


def _reload_and_rebuild_toolsets() -> ToolExecutor:
    """Re-read the config file and rebuild the tool executor."""
    config, _ = _require_init()
    config.reload_toolsets()
    return config.create_tool_executor(
        dal=_DAL,
        toolset_tag_filter=[ToolsetTag.CORE, ToolsetTag.CLUSTER],
        enable_all_toolsets_possible=False,
        prerequisite_cache=PrerequisiteCacheMode.DISABLED,
        reuse_executor=True,
    )


@admin_app.post("/reload/toolsets", response_model=ReloadResponse)
def reload_toolsets() -> ReloadResponse:
    """Reload toolset configuration from disk."""
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
    """Reload the LLM model registry from disk."""
    try:
        config, _ = _require_init()
        result = config.reload_models()
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
    """Reload both toolsets and models in one call."""
    try:
        config, _ = _require_init()
        executor = _reload_and_rebuild_toolsets()
        model_result = config.reload_models()

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
