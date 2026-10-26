import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"

APP_DIFF = """\
diff --git a/app.py b/app.py
new file mode 100644
--- /dev/null
+++ b/app.py
@@ -0,0 +1,3 @@
+import os
+
+value = undefined_name
"""

type ExampleWriter = Callable[..., Path]


def comment(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": 1,
        "author": "reviewer",
        "path": "app.py",
        "line": 3,
        "url": "https://github.com/octo/widgets/pull/1#discussion_r1",
        "body": "undefined_name is never defined",
        "label": "issue",
        "category": "correctness",
        "rationale": "NameError at import time.",
    }
    fields.update(overrides)
    return fields


@pytest.fixture
def write_example(tmp_path: Path) -> ExampleWriter:
    """Write a small golden example directory and return the golden root."""

    def write(
        name: str = "octo__widgets__1",
        comments: list[dict[str, Any]] | None = None,
        diff: str = APP_DIFF,
        files: dict[str, str] | None = None,
    ) -> Path:
        directory = tmp_path / "golden" / name
        (directory / "files").mkdir(parents=True)
        (directory / "diff.patch").write_text(diff)
        for path, text in (
            files if files is not None else {"app.py": "import os\n\nvalue = undefined_name\n"}
        ).items():
            (directory / "files" / path).write_text(text)
        example = {
            "repo": "octo/widgets",
            "number": int(name.rsplit("__", 1)[-1]),
            "title": "Add app",
            "url": f"https://github.com/octo/widgets/pull/{name.rsplit('__', 1)[-1]}",
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
            "comments": [comment()] if comments is None else comments,
        }
        (directory / "example.json").write_text(json.dumps(example))
        return tmp_path / "golden"

    return write
