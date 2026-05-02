from __future__ import annotations

import io
import json
import os
import re
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ..models import CsvFile


@dataclass
class ExecutionResult:
    output: str = ""
    result_table: list[dict[str, Any]] | None = None
    result_scalar: str | None = None
    error: str | None = None


def _filename_to_varname(filename: str) -> str:
    name = os.path.splitext(os.path.basename(filename))[0].lower()
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")
    if not name:
        name = "df"
    if name[0].isdigit():
        name = f"df_{name}"
    return name


def load_dataframes(file_ids: list[str], db: Session) -> dict[str, pd.DataFrame]:
    dataframes: dict[str, pd.DataFrame] = {}

    if not file_ids:
        return dataframes

    records = db.query(CsvFile).filter(CsvFile.id.in_(file_ids)).all()

    for record in records:
        if not os.path.exists(record.file_path):
            continue

        df = pd.read_csv(record.file_path, encoding=record.encoding)
        var_name = _filename_to_varname(record.filename)
        dataframes[var_name] = df

    return dataframes


def _normalize_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _dataframe_to_records(df: pd.DataFrame, max_rows: int = 20) -> list[dict[str, Any]]:
    safe_df = df.head(max_rows).copy()
    safe_df = safe_df.replace({np.nan: None})
    records = safe_df.to_dict(orient="records")
    return [
        {str(k): _normalize_value(v) for k, v in row.items()}
        for row in records
    ]


def execute_pandas_code(code: str, dataframes: dict[str, pd.DataFrame]) -> ExecutionResult:
    local_vars: dict[str, Any] = {
        "pd": pd,
        "np": np,
    }

    for name, df in dataframes.items():
        local_vars[name] = df.copy()

    stdout_buffer = io.StringIO()

    try:
        with redirect_stdout(stdout_buffer):
            exec(code, {}, local_vars)

        output = stdout_buffer.getvalue().strip()
        result = local_vars.get("result", None)

        if isinstance(result, pd.DataFrame):
            return ExecutionResult(
                output=output,
                result_table=_dataframe_to_records(result),
                result_scalar=None,
                error=None,
            )

        if isinstance(result, pd.Series):
            series_df = result.to_frame().reset_index()
            return ExecutionResult(
                output=output,
                result_table=_dataframe_to_records(series_df),
                result_scalar=None,
                error=None,
            )

        if result is None:
            return ExecutionResult(
                output=output,
                result_table=None,
                result_scalar=None,
                error=None,
            )

        return ExecutionResult(
            output=output,
            result_table=None,
            result_scalar=str(result),
            error=None,
        )

    except Exception as exc:
        return ExecutionResult(
            output=stdout_buffer.getvalue().strip(),
            result_table=None,
            result_scalar=None,
            error=f"{type(exc).__name__}: {exc}",
        )