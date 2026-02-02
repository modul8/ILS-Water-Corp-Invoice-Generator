from __future__ import annotations

from typing import Any, Dict, List, Optional
import requests
import time
from datetime import datetime, timezone


class FieldSyncClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        base = (base_url or "").strip().rstrip("/")
        if base.endswith("/api"):
            base = base[: -len("/api")]
        if base.endswith("/api/index.php"):
            base = base[: -len("/api/index.php")]
        self.base_url = base.rstrip("/")
        self.api_key = (api_key or "").strip()

    def _request(
        self,
        method: str,
        action: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: int = 5,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/api/index.php"
        params = params or {}
        params["action"] = action
        params["key"] = self.api_key
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        }
        data = None
        if json_body is not None:
            if action == "sync_jobs":
                import json as _json
                data = {"jobs_json": _json.dumps(json_body.get("jobs", []))}
            else:
                data = json_body
        redacted_params = dict(params)
        if "key" in redacted_params:
            redacted_params["key"] = "***"
        try:
            r = requests.request(
                method, url, params=params, data=data, headers=headers, timeout=timeout
            )
        except Exception as e:
            return {"ok": False, "error": "request_failed", "text": str(e)[:200]}
        if r.status_code >= 300:
            return {"ok": False, "error": f"http_{r.status_code}", "text": (r.text or "")[:200]}
        try:
            return r.json()
        except Exception:
            return {"ok": False, "error": "bad_json", "text": (r.text or "")[:200]}

    def list_jobs(
        self,
        *,
        module: str = "",
        completed: Optional[bool] = None,
        invoiced: Optional[bool] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": str(limit)}
        if module:
            params["module"] = module
        if completed is not None:
            params["completed"] = "1" if completed else "0"
        if invoiced is not None:
            params["invoiced"] = "1" if invoiced else "0"
        data = self._request("GET", "list", params=params)
        if not isinstance(data, dict) or not data.get("ok"):
            return []
        jobs = data.get("jobs") or []
        return jobs if isinstance(jobs, list) else []

    def changes(self, *, since: str) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"since": since}
        data = self._request("GET", "changes", params=params)
        if not isinstance(data, dict) or not data.get("ok"):
            return []
        jobs = data.get("jobs") or []
        return jobs if isinstance(jobs, list) else []

    @staticmethod
    def utc_now_mysql() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _sync_jobs_get(self, jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
        ok_count = 0
        last_error: Dict[str, Any] = {}
        for j in jobs:
            if not j.get("job_key") or not j.get("module"):
                continue
            params = {
                "job_key": j.get("job_key"),
                "module": j.get("module"),
                "job_type": j.get("job_type", ""),
                "sheet": j.get("sheet", ""),
                "item": j.get("item", ""),
                "work_order": j.get("work_order", ""),
                "po": j.get("po", ""),
                "unit": j.get("unit", ""),
                "qty_default": j.get("qty_default", ""),
                "completed": 1 if j.get("completed") else 0,
                "completed_at": j.get("completed_at", ""),
                "invoiced": 1 if j.get("invoiced") else 0,
                "invoiced_at": j.get("invoiced_at", ""),
                "qty": j.get("qty", ""),
            }
            if "current_work" in j:
                params["current_work"] = 1 if j.get("current_work") else 0
            r = self._request("GET", "upsert", params=params)
            if r.get("ok"):
                ok_count += 1
            else:
                last_error = r
            time.sleep(0.05)
        if ok_count > 0:
            return {"ok": True, "count": ok_count}
        if last_error:
            return last_error
        return {"ok": False, "error": "no_jobs_sent"}

    def sync_jobs(self, jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not jobs:
            return {"ok": True, "count": 0}
        r = self._request("POST", "sync_jobs", json_body={"jobs": jobs})
        if r.get("ok"):
            return r
        if r.get("error", "").startswith("http_") or r.get("error") in {
            "request_failed",
            "bad_json",
        }:
            return self._sync_jobs_get(jobs)
        return r

    def mark_completed(
        self, *, job_key: str, completed: bool, qty: float | int | None, completed_at: str
    ) -> Dict[str, Any]:
        params = {
            "job_key": job_key,
            "completed": 1 if completed else 0,
            "qty": qty if qty is not None else "",
            "completed_at": completed_at or "",
        }
        return self._request("GET", "complete", params=params)

    def set_invoiced(self, *, job_key: str, invoiced: bool, invoiced_at: str) -> Dict[str, Any]:
        params = {
            "job_key": job_key,
            "invoiced": 1 if invoiced else 0,
            "invoiced_at": invoiced_at or "",
        }
        return self._request("GET", "set_invoiced", params=params)
