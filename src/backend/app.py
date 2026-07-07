"""Review API.

The reviewer is the only actor who can change a case's status, and the only
statuses are approved and dismissed. There is no endpoint that files a report,
notifies an exchange, or acts on an account; approving a case marks the memo
as reviewed and nothing more. That is the whole point of the design.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.pipeline import Case, build_cases, default_session, persist
from services.settings import Settings

_cases: dict[str, Case] = {}
_settings = Settings()
_STATIC = Path(__file__).resolve().parent.parent.parent / "frontend"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    events, ticks = default_session()
    for case in build_cases(events, ticks, _settings):
        _cases[case.case_id] = case
    persist(list(_cases.values()), _settings)
    yield
    _cases.clear()


app = FastAPI(title="trade-surveillance review", version="0.1.0", lifespan=_lifespan)


class ReviewAction(BaseModel):
    action: str = Field(pattern="^(approve|dismiss)$")
    notes: str = ""
    edited_narrative: str | None = None


@app.get("/api/cases")
def list_cases(status: str | None = None) -> list[dict]:
    cases = [c for c in _cases.values() if status is None or c.status == status]
    return [
        {
            "case_id": c.case_id,
            "pattern": c.hit.pattern,
            "account_id": c.hit.account_id,
            "instrument": c.hit.instrument,
            "score": c.hit.score,
            "recommendation": c.memo.recommendation,
            "status": c.status,
            "generated_by": c.generated_by,
        }
        for c in sorted(_cases.values() if status is None else cases,
                        key=lambda c: c.case_id)
        if status is None or c.status == status
    ]


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    case = _cases.get(case_id)
    if case is None:
        raise HTTPException(404, f"unknown case {case_id}")
    return case.to_dict()


@app.post("/api/cases/{case_id}/review")
def review_case(case_id: str, action: ReviewAction) -> dict:
    case = _cases.get(case_id)
    if case is None:
        raise HTTPException(404, f"unknown case {case_id}")
    if case.status != "pending":
        raise HTTPException(409, f"case {case_id} already reviewed")
    if action.edited_narrative is not None:
        case.memo = case.memo.model_copy(update={"narrative": action.edited_narrative})
    case.status = "approved" if action.action == "approve" else "dismissed"
    case.reviewer_notes = action.notes
    case.review_history.append(
        {"action": action.action, "notes": action.notes,
         "edited": action.edited_narrative is not None}
    )
    persist(list(_cases.values()), _settings)
    return {"case_id": case_id, "status": case.status}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/app.js")
def app_js() -> FileResponse:
    return FileResponse(_STATIC / "app.js")
