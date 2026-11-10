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
        # An add may have succeeded before a timeout; recover by origin on the next run.
        retries = 0 if method.endswith(".add") else self.max_retries
        for attempt in range(retries + 1):
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
                if transient and attempt < retries:
                    time.sleep(min(30.0, 2**attempt + random.random()))
                    continue
                if not response.ok:
                    raise RuntimeError(f"Bitrix {method} failed with HTTP {response.status_code}")
                if data.get("error"):
                    raise RuntimeError(f"Bitrix {method} failed: {data.get('error')}")
                return data.get("result")
            except requests.RequestException:
                if attempt >= retries:
                    raise RuntimeError(f"Bitrix {method} network request failed") from None
                time.sleep(min(30.0, 2**attempt + random.random()))
            except ValueError:
                raise RuntimeError(f"Bitrix {method} returned invalid JSON") from None
        raise RuntimeError(f"Bitrix {method} failed")

    @staticmethod
    def lead_url(project: ProjectConfig, lead_id: str, entity: str | None = None) -> str:
        webhook = BitrixGateway._webhook(project)
        parsed = urlsplit(webhook)
        return f"{parsed.scheme}://{parsed.netloc}/crm/{entity or project.bitrix.entity}/details/{lead_id}/"

    def find_by_origin(self, project: ProjectConfig, origin_id: str) -> CreatedLead | None:
        entities = ("deal", "lead") if project.bitrix.entity == "deal" else ("lead",)
        for entity in entities:
            entity_id = self._find_origin_id(project, entity, origin_id)
            if entity_id:
                return CreatedLead(entity_id, self.lead_url(project, entity_id, entity), recovered=True)
        return None

    def _find_origin_id(self, project: ProjectConfig, entity: str, origin_id: str) -> str | None:
        result = self._call(
            project,
            f"crm.{entity}.list",
            {
                "filter": {
                    "ORIGINATOR_ID": project.bitrix.originator_id,
                    "ORIGIN_ID": origin_id,
                },
                "select": ["ID"],
                "order": {"ID": "ASC"},
            },
        )
        if not isinstance(result, list):
            raise RuntimeError(f"Bitrix crm.{entity}.list returned an invalid result")
        if not result:
            return None
        lead_id = str(result[0].get("ID") or "").strip()
        if not lead_id:
            raise RuntimeError(f"Bitrix crm.{entity}.list returned no ID")
        return lead_id

    def _contact_id(self, project: ProjectConfig, fields: dict[str, Any], origin_id: str) -> str:
        existing = self._find_origin_id(project, "contact", origin_id)
        if existing:
            return existing
        phones = fields.get("PHONE") or []
        if not phones or not all(item.get("VALUE") for item in phones):
            raise ConfigError("Deal delivery requires a contact phone")
        duplicates = self._call(project, "crm.duplicate.findbycomm", {
            "entity_type": "CONTACT", "type": "PHONE",
            "values": [item["VALUE"] for item in phones],
        })
        if not isinstance(duplicates, dict) and duplicates != []:
            raise RuntimeError("Bitrix duplicate search returned an invalid result")
        contacts = duplicates.get("CONTACT", []) if isinstance(duplicates, dict) else []
        if contacts:
            return str(min(contacts, key=int))
        result = self._call(project, "crm.contact.add", {"fields": {
            "NAME": fields["TITLE"],
            "PHONE": phones,
            "ASSIGNED_BY_ID": fields["ASSIGNED_BY_ID"],
            "SOURCE_ID": fields.get("SOURCE_ID", ""),
            "ORIGINATOR_ID": project.bitrix.originator_id,
            "ORIGIN_ID": origin_id,
        }})
        if not result:
            raise RuntimeError("Bitrix crm.contact.add returned no contact ID")
        return str(result)

    def create(self, project: ProjectConfig, fields: dict[str, Any], origin_id: str) -> CreatedLead:
        payload_fields = dict(fields)
        payload_fields["ORIGINATOR_ID"] = project.bitrix.originator_id
        payload_fields["ORIGIN_ID"] = origin_id
        if project.bitrix.entity == "deal":
            payload_fields["CONTACT_IDS"] = [self._contact_id(project, fields, origin_id)]
            payload_fields.pop("PHONE", None)
        method = f"crm.{project.bitrix.entity}.add"
        result = self._call(project, method, {"fields": payload_fields})
        lead_id = str(result or "").strip()
        if not lead_id:
            raise RuntimeError(f"Bitrix {method} returned no ID")
        return CreatedLead(lead_id, self.lead_url(project, lead_id))
