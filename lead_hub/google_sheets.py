from __future__ import annotations

import os
import random
import time
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from lead_hub.config import ConfigError, ProjectConfig
from lead_hub.headers import column_to_a1, find_header
from lead_hub.models import SheetData


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TRANSIENT_CODES = {429, 500, 502, 503, 504}


def quote_sheet_name(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


class GoogleSheetsGateway:
    def __init__(self, max_retries: int = 4):
        self.max_retries = max_retries
        self._services: dict[str, Any] = {}

    @staticmethod
    def _required_env(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            raise ConfigError(f"Environment variable {name} is empty or missing")
        return value

    def _service(self, credentials_env: str) -> Any:
        credentials_path = self._required_env(credentials_env)
        if credentials_path not in self._services:
            credentials = service_account.Credentials.from_service_account_file(
                credentials_path,
                scopes=SCOPES,
            )
            self._services[credentials_path] = build(
                "sheets", "v4", credentials=credentials, cache_discovery=False
            )
        return self._services[credentials_path]

    def _execute(self, request: Any, action: str) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            try:
                return request.execute()
            except HttpError as error:
                status = getattr(error.resp, "status", None)
                if status not in TRANSIENT_CODES or attempt >= self.max_retries:
                    raise RuntimeError(f"Google Sheets request failed during {action}") from error
                delay = min(30.0, 2**attempt + random.random())
                time.sleep(delay)
        raise RuntimeError(f"Google Sheets request failed during {action}")

    def _sheet_name(self, service: Any, spreadsheet_id: str, project: ProjectConfig) -> str:
        if project.google.sheet_name:
            return project.google.sheet_name
        if project.google.sheet_name_env:
            configured = os.getenv(project.google.sheet_name_env, "").strip()
            if configured:
                return configured
        request = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties.title",
        )
        result = self._execute(request, "get_first_sheet")
        sheets = result.get("sheets") or []
        if not sheets:
            raise RuntimeError("Spreadsheet has no sheets")
        return str(sheets[0]["properties"]["title"])

    def read_project(self, project: ProjectConfig, *, create_result_column: bool) -> SheetData:
        spreadsheet_id = self._required_env(project.google.spreadsheet_id_env)
        service = self._service(project.google.credentials_file_env)
        sheet_name = self._sheet_name(service, spreadsheet_id, project)
        request = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=quote_sheet_name(sheet_name),
        )
        result = self._execute(request, f"read:{project.code}")
        values = result.get("values") or []
        headers = [str(value) for value in (values[0] if values else [])]
        rows = [[str(value) for value in row] for row in values[1:]] if values else []

        result_index = find_header(headers, [project.google.result_header, "Статус Б24"])
        if result_index is None:
            result_index = len(headers)
            if create_result_column:
                cell = f"{column_to_a1(result_index)}1"
                request = service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"{quote_sheet_name(sheet_name)}!{cell}",
                    valueInputOption="RAW",
                    body={"values": [[project.google.result_header]]},
                )
                self._execute(request, f"create_result_column:{project.code}")
            headers.append(project.google.result_header)

        return SheetData(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            headers=headers,
            rows=rows,
            result_column_index=result_index,
        )

    def write_result(self, project: ProjectConfig, sheet: SheetData, row_number: int, value: str) -> None:
        service = self._service(project.google.credentials_file_env)
        cell = f"{column_to_a1(sheet.result_column_index)}{row_number}"
        request = service.spreadsheets().values().update(
            spreadsheetId=sheet.spreadsheet_id,
            range=f"{quote_sheet_name(sheet.sheet_name)}!{cell}",
            valueInputOption="RAW",
            body={"values": [[value]]},
        )
        self._execute(request, f"write_result:{project.code}")
