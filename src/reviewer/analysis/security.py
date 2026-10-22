"""bandit, semgrep and pip-audit, normalised into Finding objects.

These tell us what real security tooling already catches, which is exactly what the
security agent should not bother repeating.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath

from reviewer.analysis.base import (
    DEFAULT_TIMEOUT_SECONDS,
    SourceReader,
    changed_files,
    evidence,
    load_json,
    relative_path,
    run_checked,
)
from reviewer.analysis.execution import Executor, ToolFailed
from reviewer.domain.models import Category, Finding, PullRequest, Severity

_LEVEL_SEVERITY = {
    "LOW": Severity.LOW,
    "MEDIUM": Severity.MEDIUM,
    "HIGH": Severity.HIGH,
    "INFO": Severity.LOW,
    "WARNING": Severity.MEDIUM,
    "ERROR": Severity.HIGH,
}
_LEVEL_CONFIDENCE = {"LOW": 0.4, "MEDIUM": 0.7, "HIGH": 0.9}

# B101 assert_used fires on every test file. B403 and B404 fire on merely importing
# pickle or subprocess, and B603 on any subprocess call, safe or not. The dangerous uses
# have their own tests (B301, B602). Precision over recall: the rest is noise.
BANDIT_SKIPPED_TESTS = "B101,B403,B404,B603"


class BanditAnalyser:
    name = "bandit"

    def select(self, pull_request: PullRequest) -> tuple[str, ...]:
        return changed_files(pull_request, ".py")

    def command(self, files: Sequence[str]) -> list[str]:
        return ["bandit", "-f", "json", "-q", "--skip", BANDIT_SKIPPED_TESTS, "--", *files]

    def analyse(
        self,
        files: Sequence[str],
        *,
        checkout: Path,
        executor: Executor,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[Finding, ...]:
        # 0: nothing found, 1: issues found. Anything else is a crash.
        result = run_checked(
            executor, self.command(files), checkout=checkout, timeout=timeout, ok_codes=(0, 1)
        )
        return parse_bandit(load_json(result), result.workdir, SourceReader(checkout))


def parse_bandit(output: object, workdir: str, source: SourceReader) -> tuple[Finding, ...]:
    if not isinstance(output, dict) or not isinstance(output.get("results"), list):
        raise ToolFailed("bandit output has no results list")
    findings = []
    for item in output["results"]:
        path = relative_path(item["filename"], workdir)
        line = item["line_number"]
        findings.append(
            Finding(
                file=path,
                line=line,
                severity=_LEVEL_SEVERITY[item["issue_severity"]],
                category=Category.SECURITY,
                message=f"{item['test_id']}: {item['issue_text']}",
                evidence=evidence(item["issue_text"], source.line(path, line)),
                source="bandit",
                confidence=_LEVEL_CONFIDENCE[item["issue_confidence"]],
            )
        )
    return tuple(findings)


def bundled_semgrep_rules() -> str:
    return str(resources.files("reviewer.analysis") / "rules" / "semgrep.yml")


class SemgrepAnalyser:
    name = "semgrep"

    def __init__(self, rules_path: str | None = None) -> None:
        # Inside the sandbox image the rules live at a different path than on the host.
        self.rules_path = rules_path or bundled_semgrep_rules()

    def select(self, pull_request: PullRequest) -> tuple[str, ...]:
        return changed_files(pull_request, ".py")

    def command(self, files: Sequence[str]) -> list[str]:
        return [
            "semgrep",
            "scan",
            "--config",
            self.rules_path,
            "--json",
            "--metrics=off",
            "--disable-version-check",
            "--quiet",
            "--no-git-ignore",
            "--",
            *files,
        ]

    def analyse(
        self,
        files: Sequence[str],
        *,
        checkout: Path,
        executor: Executor,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[Finding, ...]:
        result = run_checked(
            executor, self.command(files), checkout=checkout, timeout=timeout, ok_codes=(0, 1)
        )
        return parse_semgrep(load_json(result), result.workdir, SourceReader(checkout))


def parse_semgrep(output: object, workdir: str, source: SourceReader) -> tuple[Finding, ...]:
    if not isinstance(output, dict) or not isinstance(output.get("results"), list):
        raise ToolFailed("semgrep output has no results list")
    findings = []
    for item in output["results"]:
        path = relative_path(item["path"], workdir)
        line = item["start"]["line"]
        extra = item["extra"]
        rule = item["check_id"].rsplit(".", 1)[-1]
        level = str(extra.get("metadata", {}).get("confidence", "MEDIUM")).upper()
        findings.append(
            Finding(
                file=path,
                line=line,
                severity=_LEVEL_SEVERITY.get(extra["severity"], Severity.MEDIUM),
                category=Category.SECURITY,
                message=f"{rule}: {extra['message']}",
                evidence=evidence(extra["message"], source.line(path, line)),
                source="semgrep",
                confidence=_LEVEL_CONFIDENCE.get(level, 0.7),
            )
        )
    return tuple(findings)


# name==version, optionally with extras, ignoring markers and comments after it.
_PINNED = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*(?P<version>[^\s;#]+)"
)


@dataclass(frozen=True, slots=True)
class Pin:
    name: str  # normalised, as pip-audit reports it
    version: str
    line: int
    text: str


def normalise_name(name: str) -> str:
    """PEP 503 normalisation: Foo_Bar.baz and foo-bar-baz are the same package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def pinned_requirements(text: str) -> tuple[Pin, ...]:
    """Exactly pinned requirements and where they are. Unpinned lines cannot be audited
    without resolving them, and resolving means downloading and running package code."""
    pins = []
    for number, line in enumerate(text.split("\n"), start=1):
        match = _PINNED.match(line)
        if match:
            pins.append(Pin(normalise_name(match["name"]), match["version"], number, line.strip()))
    return tuple(pins)


def is_requirements_file(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.suffix == ".txt" and (
        pure.name.startswith("requirements") or "requirements" in pure.parent.parts
    )


class PipAuditAnalyser:
    """Checks pinned dependencies against known vulnerabilities.

    The one analyser that needs network: it asks a vulnerability database. It never
    installs anything (--disable-pip --no-deps), so no code from the PR is executed,
    and it only receives the pinned lines on stdin, not the repository.
    """

    name = "pip-audit"

    def select(self, pull_request: PullRequest) -> tuple[str, ...]:
        return tuple(p for p in changed_files(pull_request, ".txt") if is_requirements_file(p))

    def command(self) -> list[str]:
        return [
            "pip-audit",
            "--requirement",
            "/dev/stdin",
            "--no-deps",
            "--disable-pip",
            "--format",
            "json",
            "--progress-spinner",
            "off",
        ]

    def analyse(
        self,
        files: Sequence[str],
        *,
        checkout: Path,
        executor: Executor,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[Finding, ...]:
        findings: list[Finding] = []
        for path in files:
            pins = pinned_requirements((checkout / path).read_text(errors="replace"))
            if not pins:
                continue
            stdin = "".join(f"{pin.name}=={pin.version}\n" for pin in pins)
            # 0: no vulnerabilities, 1: vulnerabilities found.
            result = run_checked(
                executor,
                self.command(),
                checkout=checkout,
                timeout=timeout,
                ok_codes=(0, 1),
                network=True,
                stdin=stdin,
            )
            findings.extend(parse_pip_audit(load_json(result), path, pins))
        return tuple(findings)


def parse_pip_audit(output: object, path: str, pins: Sequence[Pin]) -> tuple[Finding, ...]:
    if not isinstance(output, dict) or not isinstance(output.get("dependencies"), list):
        raise ToolFailed("pip-audit output has no dependencies list")
    by_name = {pin.name: pin for pin in pins}
    findings = []
    for dependency in output["dependencies"]:
        vulns = dependency.get("vulns") or []
        pin = by_name.get(normalise_name(dependency["name"]))
        if not vulns or pin is None:
            continue
        ids = ", ".join(v["id"] for v in vulns[:5]) + (", ..." if len(vulns) > 5 else "")
        # Each vulnerability's earliest fix; upgrading past the latest of them fixes all.
        earliest_fixes = [
            min(v["fix_versions"], key=_version_key) for v in vulns if v.get("fix_versions")
        ]
        upgrade = (
            f" Upgrade to {max(earliest_fixes, key=_version_key)} or later."
            if earliest_fixes
            else ""
        )
        findings.append(
            Finding(
                file=path,
                line=pin.line,
                severity=Severity.HIGH,
                category=Category.SECURITY,
                message=(
                    f"{pin.name} {pin.version} has {len(vulns)} known "
                    f"vulnerabilit{'y' if len(vulns) == 1 else 'ies'} ({ids}).{upgrade}"
                ),
                evidence=pin.text,
                source="pip-audit",
                confidence=1.0,
            )
        )
    return tuple(findings)


def _version_key(version: str) -> tuple[int, ...]:
    """Numeric ordering for versions, so 2.10 sorts after 2.9 (as strings it does not)."""
    return tuple(int(part) for part in re.findall(r"\d+", version))
