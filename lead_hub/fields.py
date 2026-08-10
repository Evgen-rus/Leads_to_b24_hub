from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from lead_hub.config import ConfigError, ProjectConfig


MSK_TZ = ZoneInfo("Europe/Moscow")


class StrictFormatDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        raise ConfigError(f"Template variable {key!r} is missing")


def _resolve_rule(rule: Any, context: dict[str, str]) -> Any:
    if not isinstance(rule, dict):
        return rule
    if "value" in rule:
        return rule["value"]
    if "env" in rule:
        name = str(rule["env"])
        value = os.getenv(name, str(rule.get("default") or "")).strip()
        if not value:
            raise ConfigError(f"Environment variable {name} is empty or missing")
        return value
    if "from" in rule:
        return context.get(str(rule["from"]), "")
    if "template" in rule:
        return str(rule["template"]).format_map(StrictFormatDict(context))
    if rule.get("phone") is True:
        phone = context.get("phone", "")
        return [{"VALUE": phone, "VALUE_TYPE": str(rule.get("value_type") or "MOBILE")}]
    raise ConfigError(f"Unsupported Bitrix field rule: {rule}")


def build_bitrix_fields(
    project: ProjectConfig,
    context: dict[str, str],
    assigned_by_id: str,
) -> dict[str, Any]:
    now = datetime.now(MSK_TZ)
    template_context = dict(context)
    template_context.update(
        {
            "project_code": project.code,
            "date": now.strftime("%Y-%m-%d"),
            "date_ddmmyyyy": now.strftime("%d%m%Y"),
        }
    )

    fields: dict[str, Any] = {}
    for name, rule in project.bitrix.fields.items():
        value = _resolve_rule(rule, template_context)
        omit_if_empty = isinstance(rule, dict) and bool(rule.get("omit_if_empty", False))
        if omit_if_empty and (value is None or value == "" or value == []):
            continue
        fields[str(name)] = value
    fields["ASSIGNED_BY_ID"] = assigned_by_id
    return fields
