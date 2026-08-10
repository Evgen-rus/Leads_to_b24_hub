from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests

from lead_hub.config import ConfigError, ProjectConfig


TRANSIENT_CODES = {408, 409, 429, 500, 502, 503, 504}
TRANSIENT_ERRORS = {"QUERY_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS", "OPERATION_TIME_LIMIT"}


@dataclass(frozen=True)
class CreatedLead:
    lead_id: str
    lead_url: str
    recovered: bool = False


class BitrixGateway:
    def __init__(self, max_retries: int = 3, timeout: int = 30):
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()

    @staticmethod
    def _webhook(project: ProjectConfig) -> str:
        value = os.getenv(project.bitrix.webhook_env, "").strip().rstrip("/")
        if not value:
            raise ConfigError(
                f"Environment variable {project.bitrix.webhook_env} is empty or missing"
            )
        return value

    def _call(self, project: ProjectConfig, method: str, payload: dict[str, Any]) -> Any:
        webhook = self._webhook(project)
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(
                    f"{webhook}/{method}",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout,
                )
                data = response.json() if response.content else {}
                api_error = str(data.get("error") or "").upper()
                transient = response.status_code in TRANSIENT_CODES or api_error in TRANSIENT_ERRORS
                if transient and attempt < self.max_retries:
                    time.sleep(min(30.0, 2**attempt + random.random()))
                    continue
                if not response.ok:
                    raise RuntimeError(f"Bitrix {method} failed with HTTP {response.status_code}")
                if data.get("error"):
                    raise RuntimeError(f"Bitrix {method} failed: {data.get('error')}")
                return data.get("result")
            except requests.RequestException as error:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Bitrix {method} network request failed") from error
                time.sleep(min(30.0, 2**attempt + random.random()))
        raise RuntimeError(f"Bitrix {method} failed")

    @staticmethod
    def lead_url(project: ProjectConfig, lead_id: str) -> str:
        webhook = BitrixGateway._webhook(project)
        parsed = urlsplit(webhook)
        return f"{parsed.scheme}://{parsed.netloc}/crm/lead/details/{lead_id}/"

    def find_by_origin(self, project: ProjectConfig, origin_id: str) -> CreatedLead | None:
        result = self._call(
            project,
            "crm.lead.list",
            {
                "filter": {
                    "ORIGINATOR_ID": project.bitrix.originator_id,
                    "ORIGIN_ID": origin_id,
                },
                "select": ["ID"],
                "order": {"ID": "ASC"},
            },
        )
        rows = result if isinstance(result, list) else []
        if not rows:
            return None
        lead_id = str(rows[0].get("ID") or "").strip()
        if not lead_id:
            return None
        return CreatedLead(lead_id, self.lead_url(project, lead_id), recovered=True)

    def create(self, project: ProjectConfig, fields: dict[str, Any], origin_id: str) -> CreatedLead:
        payload_fields = dict(fields)
        payload_fields["ORIGINATOR_ID"] = project.bitrix.originator_id
        payload_fields["ORIGIN_ID"] = origin_id
        result = self._call(project, "crm.lead.add", {"fields": payload_fields})
        lead_id = str(result or "").strip()
        if not lead_id:
            raise RuntimeError("Bitrix crm.lead.add returned no lead ID")
        return CreatedLead(lead_id, self.lead_url(project, lead_id))
