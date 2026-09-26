"""Repository for AIAnalysisJob ORM rows."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from backend.models import AIAnalysisJob
from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)


class AIAnalysisJobRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def find_by_job_id(self, job_id: str) -> AIAnalysisJob | None:
        return self.db.query(AIAnalysisJob).filter(AIAnalysisJob.job_id == job_id).first()

    def create(
        self,
        job_id: str,
        symbol: str,
        timeframe: str,
        template_id: int | None = None,
        template_name: str | None = None,
    ) -> AIAnalysisJob:
        record = AIAnalysisJob(
            job_id=job_id,
            symbol=symbol.upper(),
            timeframe=timeframe,
            template_id=template_id,
            template_name=template_name,
            status="queued",
        )
        self.db.add(record)
        self.db.commit()
        return record

    def update_status(
        self,
        job_id: str,
        status: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        record = self.find_by_job_id(job_id)
        if record is None:
            return
        # A queued job can race with cancellation. Do not let a worker that
        # starts after the cancellation resurrect the terminal record.
        if record.status == "cancelled" and status != "cancelled":
            return
        record.status = status
        if status == "started":
            record.started_at = now_ny()
        elif status in ("finished", "failed"):
            record.completed_at = now_ny()
        if result is not None:
            record.result = json.dumps(result)
        if error is not None:
            record.error = error
        self.db.commit()
