from pathlib import Path

import pytest

from reviewer.analysis.base import SourceReader
from reviewer.analysis.execution import LocalExecutor, ToolFailed
from reviewer.analysis.security import (
    BanditAnalyser,
    PipAuditAnalyser,
    SemgrepAnalyser,
    is_requirements_file,
    parse_bandit,
    parse_pip_audit,
    parse_semgrep,
    pinned_requirements,
)
from reviewer.domain.models import Category, Severity


@pytest.fixture
def source(tmp_path: Path) -> SourceReader:
    (tmp_path / "app.py").write_text('password = "x"\nrun(cmd, shell=True)\n')
    return SourceReader(tmp_path)


# --- parse_bandit ---------------------------------------------------------------------


def test_bandit_levels_map_to_severity_and_confidence(source: SourceReader) -> None:
    output = {
        "results": [
            {
                "filename": "./app.py",
                "line_number": 2,
                "test_id": "B602",
                "issue_text": "shell=True",
                "issue_severity": "HIGH",
                "issue_confidence": "MEDIUM",
            }
        ]
    }
    (finding,) = parse_bandit(output, "/src", source)
    assert finding.file == "app.py"
    assert finding.severity is Severity.HIGH
    assert finding.confidence == 0.7
    assert finding.category is Category.SECURITY
    assert finding.evidence == "run(cmd, shell=True)"
    assert finding.message == "B602: shell=True"


def test_bandit_output_without_results_fails(source: SourceReader) -> None:
    with pytest.raises(ToolFailed):
        parse_bandit({"errors": []}, "/src", source)


# --- parse_semgrep --------------------------------------------------------------------


def test_semgrep_uses_rule_name_severity_and_metadata_confidence(source: SourceReader) -> None:
    output = {
        "results": [
            {
                "check_id": "opt.reviewer.requests-tls-verification-disabled",
                "path": "app.py",
                "start": {"line": 1},
                "extra": {
                    "severity": "ERROR",
                    "message": "TLS verification is disabled.",
                    "metadata": {"confidence": "HIGH"},
                },
            }
        ]
    }
    (finding,) = parse_semgrep(output, "/src", source)
    assert finding.message == "requests-tls-verification-disabled: TLS verification is disabled."
    assert (finding.severity, finding.confidence) == (Severity.HIGH, 0.9)


# --- requirements ---------------------------------------------------------------------


def test_pinned_requirements_finds_exact_pins_and_their_lines() -> None:
    text = (
        "# deps\n"
        "Jinja2==2.4.1\n"
        "requests>=2.0\n"
        "Flask_Login[extra] == 0.6.2 ; python_version > '3.8'  # pinned\n"
        "-r other.txt\n"
    )
    pins = pinned_requirements(text)
    assert [(p.name, p.version, p.line) for p in pins] == [
        ("jinja2", "2.4.1", 2),
        ("flask-login", "0.6.2", 4),
    ]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("requirements.txt", True),
        ("requirements-dev.txt", True),
        ("requirements/base.txt", True),
        ("docs/requirements.txt", True),
        ("notes.txt", False),
        ("requirements.in", False),
    ],
)
def test_is_requirements_file(path: str, expected: bool) -> None:
    assert is_requirements_file(path) is expected


def test_pip_audit_finding_lands_on_the_pinned_line() -> None:
    pins = pinned_requirements("# deps\njinja2==2.4.1\n")
    output = {
        "dependencies": [
            {
                "name": "jinja2",
                "version": "2.4.1",
                "vulns": [
                    {"id": "PYSEC-1", "fix_versions": ["2.7.2", "3.0.0"]},
                    {"id": "PYSEC-2", "fix_versions": ["2.10.1"]},
                ],
            },
            {"name": "safe", "version": "1.0", "vulns": []},
        ]
    }
    (finding,) = parse_pip_audit(output, "requirements.txt", pins)
    assert (finding.file, finding.line) == ("requirements.txt", 2)
    assert finding.severity is Severity.HIGH
    assert "2 known vulnerabilities (PYSEC-1, PYSEC-2)" in finding.message
    # 2.10.1 is later than 2.7.2, even though "2.10.1" < "2.7.2" as strings.
    assert finding.message.endswith("Upgrade to 2.10.1 or later.")
    assert finding.evidence == "jinja2==2.4.1"


# --- against the broken fixture -------------------------------------------------------


def test_bandit_finds_the_hardcoded_secret_and_dangerous_calls(broken_repo: Path) -> None:
    findings = BanditAnalyser().analyse(["app.py"], checkout=broken_repo, executor=LocalExecutor())
    assert {(f.line, f.message.split(":")[0]) for f in findings} == {
        (9, "B105"),  # hardcoded password
        (13, "B602"),  # shell=True
        (17, "B301"),  # pickle.loads
        (21, "B501"),  # verify=False
        (25, "B608"),  # SQL built from a string
    }


def test_semgrep_finds_what_its_rules_cover(broken_repo: Path) -> None:
    findings = SemgrepAnalyser().analyse(["app.py"], checkout=broken_repo, executor=LocalExecutor())
    assert {(f.line, f.message.split(":")[0]) for f in findings} == {
        (21, "requests-tls-verification-disabled"),
        (25, "sql-built-with-string-formatting"),
    }


@pytest.mark.network
def test_pip_audit_finds_the_vulnerable_dependency(broken_repo: Path) -> None:
    findings = PipAuditAnalyser().analyse(
        ["requirements.txt"], checkout=broken_repo, executor=LocalExecutor()
    )
    assert [(f.line, f.message.split(" ")[0]) for f in findings] == [(2, "jinja2")]
