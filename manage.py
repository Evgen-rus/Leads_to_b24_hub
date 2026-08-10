from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from lead_hub.config import ConfigError, load_projects, missing_environment


ROOT = Path(__file__).resolve().parent


def set_enabled(path: Path, enabled: bool) -> None:
    text = path.read_text(encoding="utf-8")
    replacement = f"enabled: {'true' if enabled else 'false'}"
    updated, count = re.subn(r"(?m)^enabled:\s*(?:true|false)\s*$", replacement, text, count=1)
    if count != 1:
        raise ConfigError(f"Cannot find a single enabled field in {path}")
    path.write_text(updated, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Leads_to_b24_hub projects")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--environment", action="store_true")
    for command in ("enable", "disable"):
        item = subparsers.add_parser(command)
        item.add_argument("project")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    projects = load_projects(ROOT / "config")
    by_code = {project.code: project for project in projects}

    if args.command == "list":
        for project in projects:
            print(f"{project.code:12} {'enabled' if project.enabled else 'disabled':8} {project.name}")
        return 0
    if args.command == "validate":
        failed = False
        for project in projects:
            missing = missing_environment(project) if args.environment else []
            state = "OK" if not missing else "MISSING: " + ", ".join(missing)
            print(f"{project.code}: {state}")
            failed = failed or bool(missing)
        return 1 if failed else 0

    project = by_code.get(args.project)
    if project is None:
        raise ConfigError(f"Unknown project: {args.project}")
    enabled = args.command == "enable"
    set_enabled(project.path, enabled)
    print(f"{project.code}: {'enabled' if enabled else 'disabled'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        sys.exit(2)
