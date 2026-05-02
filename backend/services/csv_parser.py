"""
services/csv_parser.py — CSV parsing, schema extraction, and encoding detection.

All functions are pure data-processing utilities with no side effects
(no DB access, no file I/O beyond what is passed in as bytes/DataFrames).
"""

from __future__ import annotations

from typing import Any
import io

import chardet
import numpy as np
import pandas as pd


_ENCODING_FALLBACKS = ["utf-8", "utf-8-sig", "gb18030", "latin-1"]
_CHARDET_MIN_CONFIDENCE = 0.7


def robust_read_csv(raw_bytes: bytes, encoding: str | None = None) -> pd.DataFrame:
    encodings = []
    if encoding:
        encodings.append(encoding)
    encodings.extend(["utf-8", "utf-8-sig", "gb18030", "latin-1"])

    seen = set()
    deduped_encodings = []
    for enc in encodings:
        if enc not in seen:
            seen.add(enc)
            deduped_encodings.append(enc)

    separators = [",", ";", "\t", "|"]
    last_error = None

    for enc in deduped_encodings:
        try:
            text = raw_bytes.decode(enc, errors="replace")
        except Exception as e:
            last_error = e
            continue

        for sep in separators:
            try:
                df = pd.read_csv(
                    io.StringIO(text),
                    sep=sep,
                    engine="python",
                    quotechar='"',
                    skipinitialspace=True,
                    on_bad_lines="skip",
                )
                if df is not None and len(df.columns) >= 2 and len(df) >= 1:
                    return df
            except Exception as e:
                last_error = e

        try:
            df = pd.read_csv(
                io.StringIO(text),
                sep=None,
                engine="python",
                quotechar='"',
                skipinitialspace=True,
                on_bad_lines="skip",
            )
            if df is not None and len(df.columns) >= 2 and len(df) >= 1:
                return df
        except Exception as e:
            last_error = e

    raise ValueError(f"Failed to parse CSV: {last_error}")


def detect_encoding(raw_bytes: bytes) -> str:
    result = chardet.detect(raw_bytes)
    encoding = result.get("encoding") or ""
    confidence = result.get("confidence") or 0.0

    if encoding and confidence >= _CHARDET_MIN_CONFIDENCE:
        normalised = encoding.lower().replace("-", "_")
        if normalised == "utf_8_sig":
            return "utf-8-sig"
        return encoding.lower()

    sample = raw_bytes[:4096]
    for enc in _ENCODING_FALLBACKS:
        try:
            sample.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue

    return "latin-1"


def parse_csv(raw_bytes: bytes, encoding: str) -> pd.DataFrame:
    try:
        return robust_read_csv(raw_bytes, encoding)
    except Exception as exc:
        raise ValueError(f"Failed to parse CSV: {exc}") from exc


def _dtype_label(series: pd.Series) -> str:
    dtype = series.dtype

    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"

    if pd.api.types.is_object_dtype(dtype):
        sample = series.dropna().head(5)
        try:
            pd.to_datetime(sample)
            return "datetime"
        except Exception:
            pass
        return "string"

    return "string"


_MAX_SAMPLE_VALUES = 5
_MAX_SAMPLE_VALUE_LEN = 50


def extract_schema(df: pd.DataFrame) -> list[dict[str, Any]]:
    schema = []
    for col in df.columns:
        series = df[col]
        non_null_rate = round(float(series.notna().mean()), 4)

        unique_vals = series.dropna().unique()
        samples: list[str] = []
        for v in unique_vals[:_MAX_SAMPLE_VALUES]:
            s = str(v)
            if len(s) > _MAX_SAMPLE_VALUE_LEN:
                s = s[:_MAX_SAMPLE_VALUE_LEN] + "…"
            samples.append(s)

        schema.append(
            {
                "name": col,
                "dtype": _dtype_label(series),
                "non_null_rate": non_null_rate,
                "sample_values": samples,
            }
        )
    return schema


def get_preview(df: pd.DataFrame, n: int = 10) -> list[dict[str, Any]]:
    preview_df = df.head(n).copy()
    preview_df = preview_df.where(preview_df.notna(), other=None)

    rows = preview_df.to_dict(orient="records")

    cleaned: list[dict[str, Any]] = []
    for row in rows:
        cleaned.append({k: _coerce_value(v) for k, v in row.items()})
    return cleaned


def _coerce_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def generate_suggestions(files_metadata: list[dict[str, Any]]) -> list[str]:
    suggestions: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        if q not in seen:
            seen.add(q)
            suggestions.append(q)

    numeric_cols: list[tuple[str, str]] = []
    string_cols: list[tuple[str, str]] = []
    datetime_cols: list[tuple[str, str]] = []

    for file_meta in files_metadata:
        fname = file_meta.get("filename", "this file")
        for col in file_meta.get("columns_info", []):
            col_name = col["name"]
            dtype = col["dtype"]
            if dtype in ("integer", "float"):
                numeric_cols.append((col_name, fname))
            elif dtype == "string":
                string_cols.append((col_name, fname))
            elif dtype == "datetime":
                datetime_cols.append((col_name, fname))

    for col_name, fname in numeric_cols[:2]:
        add(f"What is the average, min, and max of '{col_name}'?")
        add(f"Show the top 10 rows sorted by '{col_name}' in descending order.")

    for col_name, fname in string_cols[:2]:
        add(f"What are the unique values in '{col_name}' and how many rows does each have?")

    if datetime_cols and numeric_cols:
        dt_col = datetime_cols[0][0]
        num_col = numeric_cols[0][0]
        add(f"Show the trend of '{num_col}' over '{dt_col}'.")

    add("Which columns have the most missing values?")
    add("Describe the overall data quality of the uploaded file(s).")

    if len(files_metadata) > 1:
        names = [m.get("filename", "file") for m in files_metadata[:2]]
        add(f"Compare the data in '{names[0]}' and '{names[1]}' — what are the key differences?")
        if numeric_cols:
            add(
                f"Is the distribution of '{numeric_cols[0][0]}' similar across '{names[0]}' "
                f"and '{names[1]}'?"
            )

    return suggestions[:8]