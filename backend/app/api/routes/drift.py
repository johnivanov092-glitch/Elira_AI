"""API for the server drift detector.

GET  /api/drift/status    — last verified live facts (+ previous/changed).
POST /api/drift/reconcile — probe now, update, return facts + any drifts.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.application.drift import runtime as drift_runtime

router = APIRouter(prefix="/api/drift", tags=["drift"])


@router.get("/status")
def drift_status() -> dict:
    return drift_runtime.get_status()


@router.post("/reconcile")
def drift_reconcile() -> dict:
    return drift_runtime.reconcile()
