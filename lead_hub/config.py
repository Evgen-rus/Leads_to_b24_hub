from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when a project configuration is incomplete or inconsistent."""


@dataclass(frozen=True)
class GoogleConfig:
    spreadsheet_id_env: str
    credentials_file_env: str = "GOOGLE_CREDENTIALS_FILE"
    sheet_name: str | None = None
    sheet_name_env: str | None = None
    result_header: str = "Статус_Б24"
    columns: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class BitrixConfig:
    webhook_env: str
    fields: dict[str, Any]
    originator_id: str = "leads_to_b24_hub"
    entity: str = "lead"


@dataclass(frozen=True)
class AssignmentMember:
    weight: int = 1
    user_id: str | None = None
    user_id_env: str | None = None
    default: str | None = None

    def resolve(self) -> str:
        if self.user_id:
            return self.user_id
        if self.user_id_env:
            value = os.getenv(self.user_id_env, self.default or "").strip()
            if value:
                return value
            raise ConfigError(f"Environment variable {self.user_id_env} is empty or missing")
        raise ConfigError("Assignment member requires id or env")


@dataclass(frozen=True)
class AssignmentConfig:
    members: tuple[AssignmentMember, ...]

    def resolved_cycle(self) -> tuple[str, ...]:
        cycle: list[str] = []
        for member in self.members:
            if member.weight <= 0:
                raise ConfigError("Assignment weight must be positive")
            cycle.extend([member.resolve()] * member.weight)
        if not cycle:
            raise ConfigError("Assignment cycle cannot be empty")
        return tuple(cycle)


@dataclass(frozen=True)
class ProjectConfig:
    code: str
    name: str
    enabled: bool
    google: GoogleConfig
    bitrix: BitrixConfig
    assignment: AssignmentConfig
    path: Path

    def required_environment(self) -> set[str]:
        names = {
            self.google.spreadsheet_id_env,
            self.google.credentials_file_env,
            self.bitrix.webhook_env,
        }
        for member in self.assignment.members:
            if member.user_id_env and not member.default:
                names.add(member.user_id_env)
        for rule in self.bitrix.fields.values():
            if isinstance(rule, dict) and rule.get("env") and not rule.get("default"):
                names.add(str(rule["env"]))
        return names


DEFAULT_COLUMNS = {
    "source_id": ["ID"],
    "event_dt": ["Дата"],
    "phone": ["Номера", "Телефон"],
    "channel": ["Канал"],
    "source_lead": ["Источник"],
}


def _as_string_list(value: Any, *, key: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ConfigError(f"google.columns.{key} must be a string or list of strings")


def _load_member(raw: Any) -> AssignmentMember:
    if not isinstance(raw, dict):
        raise ConfigError("Each assignment member must be a mapping")
    return AssignmentMember(
        weight=int(raw.get("weight", 1)),
        user_id=str(raw["id"]).strip() if raw.get("id") is not None else None,
        user_id_env=str(raw["env"]).strip() if raw.get("env") else None,
        default=str(raw["default"]).strip() if raw.get("default") is not None else None,
    )


def load_project(path: Path) -> ProjectConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"Cannot read {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")

    code = str(raw.get("code") or "").strip()
    if not re.fullmatch(r"[a-z0-9_-]+", code):
        raise ConfigError(f"Invalid project code in {path}: {code!r}")

    google_raw = raw.get("google")
    bitrix_raw = raw.get("bitrix")
    assignment_raw = raw.get("assignment")
    if not isinstance(google_raw, dict) or not isinstance(bitrix_raw, dict):
        raise ConfigError(f"{path} requires google and bitrix mappings")
    if not isinstance(assignment_raw, dict) or not isinstance(assignment_raw.get("members"), list):
        raise ConfigError(f"{path} requires assignment.members")

    columns = dict(DEFAULT_COLUMNS)
    for key, value in (google_raw.get("columns") or {}).items():
        columns[str(key)] = _as_string_list(value, key=str(key))

    fields = bitrix_raw.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ConfigError(f"{path} requires bitrix.fields")

    entity = bitrix_raw.get("entity", "lead")
    if entity not in {"lead", "deal"}:
        raise ConfigError(f"{path}: bitrix.entity must be lead or deal")
    if entity == "deal":
        if not fields.get("CATEGORY_ID") or not fields.get("STAGE_ID") or "STATUS_ID" in fields:
            raise ConfigError(f"{path}: deals require CATEGORY_ID and STAGE_ID, without STATUS_ID")

    project = ProjectConfig(
        code=code,
        name=str(raw.get("name") or code),
        enabled=bool(raw.get("enabled", False)),
        google=GoogleConfig(
            spreadsheet_id_env=str(google_raw.get("spreadsheet_id_env") or "").strip(),
            credentials_file_env=str(
                google_raw.get("credentials_file_env") or "GOOGLE_CREDENTIALS_FILE"
            ).strip(),
            sheet_name=str(google_raw["sheet_name"]).strip() if google_raw.get("sheet_name") else None,
            sheet_name_env=(
                str(google_raw["sheet_name_env"]).strip()
                if google_raw.get("sheet_name_env")
                else None
            ),
            result_header=str(google_raw.get("result_header") or "Статус_Б24").strip(),
            columns=columns,
        ),
        bitrix=BitrixConfig(
            webhook_env=str(bitrix_raw.get("webhook_env") or "").strip(),
            fields=fields,
            originator_id=str(bitrix_raw.get("originator_id") or "leads_to_b24_hub").strip(),
            entity=entity,
        ),
        assignment=AssignmentConfig(tuple(_load_member(item) for item in assignment_raw["members"])),
        path=path,
    )

    if not project.google.spreadsheet_id_env or not project.bitrix.webhook_env:
        raise ConfigError(f"{path} requires spreadsheet_id_env and webhook_env")
    return project


def load_projects(config_dir: Path) -> list[ProjectConfig]:
    paths = sorted([*config_dir.glob("*.yaml"), *config_dir.glob("*.yml")])
    projects = [load_project(path) for path in paths]
    codes = [project.code for project in projects]
    if len(codes) != len(set(codes)):
        raise ConfigError("Project codes must be unique")
    return projects


def missing_environment(project: ProjectConfig) -> list[str]:
    return sorted(name for name in project.required_environment() if not os.getenv(name, "").strip())
