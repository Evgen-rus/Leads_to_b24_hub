from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_hub.config import ConfigError, load_project, load_projects, missing_environment


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_all_expected_projects_load(self) -> None:
        projects = load_projects(ROOT / "config")
        self.assertEqual(
            {project.code for project in projects},
            {"metalwork", "werus", "vkc", "prolog", "hirehub", "supermet", "tochkap", "praktikm"},
        )
        self.assertTrue(all(project.enabled for project in projects))

    def test_optional_sheet_name_env_is_not_required(self) -> None:
        project = next(item for item in load_projects(ROOT / "config") if item.code == "praktikm")
        with patch.dict(os.environ, {}, clear=True):
            missing = missing_environment(project)
        self.assertNotIn("PRAKTIKM_GOOGLE_SHEET_NAME", missing)
        self.assertIn("PRAKTIKM_GOOGLE_SHEET_ID", missing)

    def test_invalid_project_code_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.yaml"
            path.write_text(
                """
code: Bad Code
enabled: true
google:
  spreadsheet_id_env: SHEET
bitrix:
  webhook_env: WEBHOOK
  fields: {TITLE: test}
assignment:
  members: [{id: '1'}]
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_project(path)


if __name__ == "__main__":
    unittest.main()
