"""Supplementary tests for cron types and service error paths."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob, CronRunRecord


class TestCronJobFromDict:
    def test_deserializes_with_run_history(self):
        job = CronJob.from_dict({
            "id": "test-job",
            "name": "test",
            "state": {
                "run_history": [
                    {"run_at_ms": 1000, "status": "ok", "duration_ms": 500},
                    {"run_at_ms": 2000, "status": "error", "error": "fail"},
                ],
            },
        })
        assert job.id == "test-job"
        assert len(job.state.run_history) == 2
        assert job.state.run_history[0].status == "ok"
        assert job.state.run_history[1].error == "fail"

    def test_missing_schedule_defaults_to_every(self):
        job = CronJob.from_dict({"id": "x", "name": "x"})
        assert job.schedule.kind == "every"

    def test_handles_already_constructed_records(self):
        record = CronRunRecord(run_at_ms=1000, status="ok")
        job = CronJob.from_dict({
            "id": "t", "name": "t",
            "state": {"run_history": [record]},
        })
        assert len(job.state.run_history) == 1
        assert job.state.run_history[0].run_at_ms == 1000

    def test_empty_run_history(self):
        job = CronJob.from_dict({"id": "t", "name": "t"})
        assert job.state.run_history == []


class TestLoadJobs:
    def test_handles_corrupt_json(self, tmp_path):
        store_path = tmp_path / "cron_store.json"
        store_path.write_text("{invalid json", encoding="utf-8")
        service = CronService(store_path=store_path)
        jobs, version = service._load_jobs()
        assert jobs == []
        assert version == 1

    def test_handles_missing_file(self, tmp_path):
        store_path = tmp_path / "nonexistent.json"
        service = CronService(store_path=store_path)
        jobs, version = service._load_jobs()
        assert jobs == []
        assert version == 1

    def test_loads_valid_store(self, tmp_path):
        store_path = tmp_path / "cron_store.json"
        store_path.write_text(json.dumps({
            "version": 2,
            "jobs": [
                {
                    "id": "j1", "name": "job1",
                    "schedule": {"kind": "every", "everyMs": 60000},
                    "payload": {"message": "hello"},
                    "createdAtMs": 1000,
                },
            ],
        }), encoding="utf-8")
        service = CronService(store_path=store_path)
        jobs, version = service._load_jobs()
        assert version == 2
        assert len(jobs) == 1
        assert jobs[0].id == "j1"
        assert jobs[0].schedule.every_ms == 60000
