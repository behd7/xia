"""Shared fail-closed policy for headless, GPU and real-client regressions."""
from __future__ import annotations

from pathlib import Path
import re


def claim_output(output: Path) -> None:
    """One top-level run per directory; never delete old evidence to reuse it."""
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(f"Use a new or empty --output directory: {output}")
    # Exclusive creation also rejects two runners racing for the same empty dir.
    with (output / ".hd2d-run").open("x") as lock:
        lock.write("Reserved for one HD2D validation run. Do not reuse.\n")

# Godot may print any of these and still exit 0. Warnings are not silently
# promoted to errors; the complete original logs remain evidence.
ERROR_LOG = re.compile(
    r"SCRIPT ERROR|SHADER ERROR|Parse Error|Shader compilation failed|\bERROR\s*:|"
    r"Traceback \(most recent call last\)|\b(?:[A-Z0-9]+_)*FAIL\b|"
    r"\bfailures\s*[=:]\s*[1-9][0-9]*", re.I,
)
CONTRACT_END = 'HD2D_FULL_MAP_PASS:'


def run_errors(returncode: int | None, log: str, marker: str | None = None) -> list[str]:
    errors = []
    if returncode != 0:
        errors.append(f'exit code {returncode}')
    match = ERROR_LOG.search(log)
    if match:
        errors.append(f'error log: {match.group(0)}')
    if marker and marker not in log:
        errors.append(f'missing final marker: {marker}')
    return errors


def probe_errors(report: object) -> list[str]:
    """A JSON file or exit 0 alone is not a completed/passing GPU probe."""
    if not isinstance(report, dict) or report.get('passed') is not True:
        return ['probe did not explicitly pass']
    # Actor probe's established schema uses 'checks'; keep it compatible.
    results = report.get('results', report.get('checks'))
    if not isinstance(results, list) or not results:
        return ['probe has no assertion results']
    if any(not isinstance(item, dict) or item.get('passed') is not True for item in results):
        return ['probe contains incomplete/failed assertions']
    return []
