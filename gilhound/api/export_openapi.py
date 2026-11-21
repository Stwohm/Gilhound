"""Write the local API schema to openapi.json at the repository root."""

import json
from pathlib import Path
from secrets import token_urlsafe

from gilhound.api.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


def export_openapi(path: Path) -> None:
    schema = create_app(token_urlsafe()).openapi()
    path.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    export_openapi(REPO_ROOT / "openapi.json")
