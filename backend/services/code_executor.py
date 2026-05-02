"""
services/code_executor.py — Sandboxed pandas code execution.

The AI may generate Python/pandas code to answer questions that require
exact computation.  This module executes that code in a restricted
environment to prevent accidental (or malicious) access to the filesystem,
network, or interpreter internals.

Security model (appropriate for local, single-user Phase 1 deployment):
  - exec() runs in a namespace that contains only safe symbols.
  - Dangerous builtins (open, __import__, eval, exec, compile) are blocked.
  - A hard timeout (30 s) is enforced via a ThreadPoolExecutor future.

Note: exec() with a restricted namespace is NOT a production-grade sandbox.
For a multi-user Phase 2 deployment, consider using a subprocess with
resource limits or a dedicated sandboxing library.
"""

from __future__ import annotations

import contextlib
import io
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from models import CsvFile
from services import csv_parser

# Maximum seconds allowed for a single code execution
EXECUTION_TIMEOUT_SECONDS = 30

# Builtins that are explicitly removed from the sandbox namespace
_BLOCKED_BUILTINS = {
    "open", "__import__", "eval", "exec", "compile",
    "breakpoint", "input", "memoryview",
}


@dataclass
class ExecutionResult:
    """Outcome of a single sandboxed code execution."""
    output: str = ""              # captured stdout
    result_table: list[dict[str, Any]] | None = None  # if `result` is a DataFrame
    result_scalar: str | None = None  # if `result` is a scalar (int, float, str, …)
    error: str | None = None      # exception message if execution failed


# ---------------------------------------------------------------------------
# DataFrame loader
# ---------------------------------------------------------------------------

def load_dataframes(file_ids: list[str], db: Session) -> dict[str, pd.DataFrame]:
    """
    Load the DataFrames for the given file IDs from disk.

    Returns a dict mapping a Python-safe variable name to the DataFrame.
    The variable name is derived from the original filename:
      - extension stripped
      - spaces and hyphens replaced with underscores
      - leading digits prefixed with "df_"

    Example: "sales data 2024.csv" → "sales_data_2024"
    """
    frames: dict[str, pd.DataFrame] = {}
    records = db.query(CsvFile).filter(CsvFile.id.in_(file_ids)).all()

    for record in records:
        df = pd.read_csv(record.file_path, encoding=record.encoding)
        var_name = _filename_to_varname(record.filename)
        frames[var_name] = df

    return frames


def _filename_to_varname(filename: str) -> str:
    """Convert a filename to a valid, human-readable Python variable name."""
    stem = filename.rsplit(".", 1)[0]          # drop extension
    safe = stem.replace(" ", "_").replace("-", "_").replace(".", "_")
    # Ensure the name does not start with a digit
    if safe and safe[0].isdigit():
        safe = "df_" + safe
    # Keep only alphanumeric and underscore characters
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in safe)
    return safe or "df"


# ---------------------------------------------------------------------------
# Sandbox execution
# ---------------------------------------------------------------------------

def _build_safe_builtins() -> dict[str, Any]:
    """
    Build a restricted __builtins__ dict that exposes only safe built-in
    functions and removes dangerous ones.
    """
    import builtins
    safe = {
        name: getattr(builtins, name)
        for name in dir(builtins)
        if name not in _BLOCKED_BUILTINS and not name.startswith("__")
    }
    # Explicitly remove any that slipped through
    for blocked in _BLOCKED_BUILTINS:
        safe.pop(blocked, None)
    return safe


def _run_code(code: str, namespace: dict[str, Any]) -> None:
    """Execute `code` inside `namespace`.  Called inside a thread for timeout."""
    exec(code, namespace)  # noqa: S102 — intentional restricted exec


def execute_pandas_code(
    code: str,
    dataframes: dict[str, pd.DataFrame],
) -> ExecutionResult:
    """
    Execute AI-generated pandas code in a restricted namespace.

    The namespace exposes:
      - pd  (pandas)
      - np  (numpy)
      - One entry per DataFrame in `dataframes` (variable_name → DataFrame)

    The code should store its final answer in a variable named `result`.
    If `result` is a DataFrame, it is serialised to a list of dicts.
    If `result` is a scalar, it is converted to a string.

    Stdout is captured.  Any exception is caught and returned in the
    `error` field rather than propagated.

    Returns an ExecutionResult.
    """
    # Build the execution namespace
    namespace: dict[str, Any] = {
        "__builtins__": _build_safe_builtins(),
        "pd": pd,
        "np": np,
    }
    namespace.update(dataframes)

    # Capture stdout
    stdout_buffer = io.StringIO()

    def _exec_with_capture() -> None:
        with contextlib.redirect_stdout(stdout_buffer):
            _run_code(code, namespace)

    # Run with timeout
    result = ExecutionResult()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_exec_with_capture)
        try:
            future.result(timeout=EXECUTION_TIMEOUT_SECONDS)
        except FuturesTimeoutError:
            result.error = (
                f"Code execution timed out after {EXECUTION_TIMEOUT_SECONDS} seconds."
            )
            return result
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            return result

    result.output = stdout_buffer.getvalue()

    # Extract the `result` variable if the code produced one
    raw_result = namespace.get("result")
    if raw_result is not None:
        if isinstance(raw_result, pd.DataFrame):
            # Serialise DataFrame to list of dicts (NaN → None)
            clean = raw_result.where(raw_result.notna(), other=None)
            rows = clean.to_dict(orient="records")
            result.result_table = [
                {k: _safe_json_value(v) for k, v in row.items()}
                for row in rows
            ]
        else:
            result.result_scalar = str(raw_result)

    return result


def _safe_json_value(v: Any) -> Any:
    """Coerce numpy scalars and NaN to JSON-safe Python types."""
    if v is None:
        return None
    if isinstance(v, float) and (v != v):  # NaN check
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v
