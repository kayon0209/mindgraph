from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import datetime, timezone

from domain.errors import ConflictError, NotFoundError
from domain.models import BadCase, BadCaseUpdate, FeedbackCreate, FeedbackRecord
from infrastructure.database import ProductDatabase, dumps, loads

logger = logging.getLogger("mindgraph.feedback")


class FeedbackService:
    def __init__(self, database: ProductDatabase) -> None:
        self.database = database

    def create_feedback(self, payload: FeedbackCreate) -> FeedbackRecord:
        query = self.database.fetch_one("SELECT * FROM query_logs WHERE request_id=?", (payload.request_id,))
        if not query:
            raise NotFoundError("Request ID does not exist")
        if self.database.fetch_one("SELECT feedback_id FROM feedback WHERE request_id=?", (payload.request_id,)):
            raise ConflictError("Feedback already exists for this request")
        record = FeedbackRecord(feedback_id=str(uuid.uuid4()), **payload.model_dump())
        self.database.execute("INSERT INTO feedback VALUES (?,?,?,?,?,?)", (
            record.feedback_id, record.request_id, record.rating, dumps(record.reason_codes), record.comment, record.created_at.isoformat()))
        if record.rating == "not_helpful":
            now = datetime.now(timezone.utc)
            trace = loads(query["trace_json"], {})
            self.database.execute("INSERT OR IGNORE INTO bad_cases VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                str(uuid.uuid4()), record.request_id, query["question"], query["answer"], dumps(trace.get("final_chunks", [])),
                "unclassified", "new", None, None, now.isoformat(), now.isoformat()))
        logger.info("feedback_created", extra={"request_id": record.request_id, "feedback_id": record.feedback_id, "rating": record.rating})
        return record

    def list_bad_cases(self, status: str | None = None, category: str | None = None) -> list[BadCase]:
        clauses, params = [], []
        if status:
            clauses.append("status=?"); params.append(status)
        if category:
            clauses.append("error_category=?"); params.append(category)
        sql = "SELECT * FROM bad_cases" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY updated_at DESC"  # nosec B608 -- clauses 仅为常量 'status=?'/'error_category=?'
        return [self._bad_case(row) for row in self.database.fetch_all(sql, tuple(params))]

    def get_bad_case(self, bad_case_id: str) -> BadCase:
        row = self.database.fetch_one("SELECT * FROM bad_cases WHERE bad_case_id=?", (bad_case_id,))
        if not row:
            raise NotFoundError("Bad case not found")
        return self._bad_case(row)

    def update_bad_case(self, bad_case_id: str, update: BadCaseUpdate) -> BadCase:
        self.get_bad_case(bad_case_id)
        values = update.model_dump(exclude_none=True)
        if values:
            assignments = ",".join(f"{name}=?" for name in values)  # name 来自 Pydantic 模型字段名（固定 schema）
            params = tuple(values.values()) + (datetime.now(timezone.utc).isoformat(), bad_case_id)
            self.database.execute(f"UPDATE bad_cases SET {assignments},updated_at=? WHERE bad_case_id=?", params)  # nosec B608 -- assignments 由 Pydantic 字段名 + '?' 构成，非用户输入
            logger.info("bad_case_updated", extra={"bad_case_id": bad_case_id, "fields": sorted(values)})
        return self.get_bad_case(bad_case_id)

    def export_bad_cases(self, status: str | None = None, category: str | None = None) -> str:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["bad_case_id", "request_id", "question", "error_category", "status", "reviewer_note", "resolution", "regression_candidate"])
        writer.writeheader()
        for case in self.list_bad_cases(status, category):
            writer.writerow({
                "bad_case_id": case.bad_case_id, "request_id": case.request_id, "question": case.question,
                "error_category": case.error_category, "status": case.status, "reviewer_note": case.reviewer_note,
                "resolution": case.resolution, "regression_candidate": case.status == "resolved",
            })
        return output.getvalue()

    @staticmethod
    def _bad_case(row) -> BadCase:
        return BadCase(
            bad_case_id=row["bad_case_id"], request_id=row["request_id"], question=row["question"], answer=row["answer"],
            retrieved_chunks=loads(row["retrieved_chunks_json"], []), error_category=row["error_category"], status=row["status"],
            reviewer_note=row["reviewer_note"], resolution=row["resolution"], created_at=row["created_at"], updated_at=row["updated_at"])
