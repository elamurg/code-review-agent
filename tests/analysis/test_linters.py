import json
from pathlib import Path

import pytest

from reviewer.analysis.base import SourceReader, relative_path
from reviewer.analysis.execution import LocalExecutor, ToolFailed
from reviewer.analysis.linters import MypyAnalyser, RuffAnalyser, parse_mypy, parse_ruff
from reviewer.domain.models import Category, Severity


def ruff_item(code: str | None, row: int, filename: str = "/repo/app.py") -> dict[str, object]:
    return {
        "code": code,
        "message": f"message for {code}",
        "filename": filename,
        "location": {"row": row},
    }


@pytest.fixture
def source(tmp_path: Path) -> SourceReader:
    (tmp_path / "app.py").write_text("import os\n\nx = undefined\n")
    return SourceReader(tmp_path)


# --- parse_ruff -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "severity", "category"),
    [
        pytest.param("F401", Severity.LOW, Category.STYLE, id="unused-import-is-low-style"),
        pytest.param("F821", Severity.HIGH, Category.CORRECTNESS, id="undefined-name-is-high"),
        pytest.param(None, Severity.HIGH, Category.CORRECTNESS, id="syntax-error-has-no-code"),
        pytest.param("B006", Severity.MEDIUM, Category.CORRECTNESS, id="bugbear-is-medium"),
    ],
)
def test_ruff_severity_and_category(
    code: str | None, severity: Severity, category: Category, source: SourceReader
) -> None:
    (finding,) = parse_ruff([ruff_item(code, 3)], "/repo", source)
    assert (finding.severity, finding.category) == (severity, category)
    assert finding.source == "ruff"
    assert finding.confidence == 1.0


def test_ruff_paths_are_made_relative_and_evidence_is_the_source_line(
    source: SourceReader,
) -> None:
    (finding,) = parse_ruff([ruff_item("F821", 3, "/repo/app.py")], "/repo", source)
    assert finding.file == "app.py"
    assert finding.evidence == "x = undefined"


def test_ruff_skips_findings_without_a_line(source: SourceReader) -> None:
    assert parse_ruff([ruff_item("E902", 0)], "/repo", source) == ()


def test_ruff_output_that_is_not_a_list_fails(source: SourceReader) -> None:
    with pytest.raises(ToolFailed):
        parse_ruff({"oops": True}, "/repo", source)


# --- parse_mypy -----------------------------------------------------------------------


def mypy_line(severity: str = "error", line: int = 3, file: str = "app.py") -> str:
    return json.dumps(
        {
            "file": file,
            "line": line,
            "message": 'Name "undefined" is not defined',
            "code": "name-defined",
            "severity": severity,
        }
    )


def test_mypy_reads_one_json_object_per_line(source: SourceReader) -> None:
    stdout = "\n".join([mypy_line(line=3), mypy_line(line=1)]) + "\n"
    findings = parse_mypy(stdout, "/repo", source)
    assert [(f.line, f.message) for f in findings] == [
        (3, 'name-defined: Name "undefined" is not defined'),
        (1, 'name-defined: Name "undefined" is not defined'),
    ]
    assert all(f.source == "mypy" and f.category is Category.CORRECTNESS for f in findings)


def test_mypy_notes_are_not_findings(source: SourceReader) -> None:
    assert parse_mypy(mypy_line(severity="note"), "/repo", source) == ()


def test_mypy_line_that_is_not_json_fails(source: SourceReader) -> None:
    with pytest.raises(ToolFailed, match="not JSON"):
        parse_mypy("Success: no issues found\n", "/repo", source)


# --- relative_path --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "workdir", "expected"),
    [
        pytest.param("/src/pkg/a.py", "/src", "pkg/a.py", id="absolute-in-container"),
        pytest.param("./pkg/a.py", "/src", "pkg/a.py", id="dot-slash"),
        pytest.param("pkg/a.py", "/src", "pkg/a.py", id="already-relative"),
    ],
)
def test_relative_path(raw: str, workdir: str, expected: str) -> None:
    assert relative_path(raw, workdir) == expected


# --- against the broken fixture -------------------------------------------------------


def test_ruff_finds_the_broken_fixture(broken_repo: Path) -> None:
    findings = RuffAnalyser().analyse(["app.py"], checkout=broken_repo, executor=LocalExecutor())
    assert {(f.line, f.message.split(":")[0]) for f in findings} == {(3, "F401"), (30, "F821")}


def test_mypy_finds_the_broken_fixture(broken_repo: Path) -> None:
    findings = MypyAnalyser().analyse(["app.py"], checkout=broken_repo, executor=LocalExecutor())
    assert {(f.line, f.message.split(":")[0]) for f in findings} == {(30, "name-defined")}


def test_mypy_ignores_the_repositorys_own_config(tmp_path: Path) -> None:
    # A repo config could load plugins, which are arbitrary code. It must be ignored.
    (tmp_path / "mypy.ini").write_text("[mypy]\nplugins = not_a_real_plugin_module\n")
    (tmp_path / "ok.py").write_text("x: int = 1\n")
    findings = MypyAnalyser().analyse(["ok.py"], checkout=tmp_path, executor=LocalExecutor())
    assert findings == ()


def test_clean_file_has_no_findings(tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    for analyser in (RuffAnalyser(), MypyAnalyser()):
        assert analyser.analyse(["clean.py"], checkout=tmp_path, executor=LocalExecutor()) == ()
