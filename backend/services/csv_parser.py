"""
services/csv_parser.py — CSV parsing, schema extraction, and encoding detection.

All functions are pure data-processing utilities with no side effects
(no DB access, no file I/O beyond what is passed in as bytes/DataFrames).
"""

from __future__ import annotations

import io
import json
from typing import Any

import chardet
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Encoding detection
# ---------------------------------------------------------------------------

# Fallback encoding chain tried in order when chardet confidence is too low
_ENCODING_FALLBACKS = ["utf-8", "gb18030", "latin-1"]
_CHARDET_MIN_CONFIDENCE = 0.7


def detect_encoding(raw_bytes: bytes) -> str:
    """
    Detect the character encoding of a CSV file given its raw bytes.

    Strategy:
      1. Use chardet for probabilistic detection.
      2. If chardet confidence >= 0.7, normalise and return its result.
      3. Otherwise try each encoding in _ENCODING_FALLBACKS in order;
         return the first one that decodes without error.
      4. Fall back to "latin-1" as a last resort (it never raises on any byte).

    gb18030 is used instead of gbk because it is a strict superset and handles
    all GBK characters as well as additional CJK characters.
    """
    result = chardet.detect(raw_bytes)
    encoding = result.get("encoding") or ""
    confidence = result.get("confidence") or 0.0

    if encoding and confidence >= _CHARDET_MIN_CONFIDENCE:
        # Normalise common aliases
        normalised = encoding.lower().replace("-", "_")
        if normalised in ("utf_8_sig",):
            return "utf-8-sig"
        return encoding.lower()

    # Low-confidence path: try fallbacks by actually decoding a sample
    sample = raw_bytes[:4096]
    for enc in _ENCODING_FALLBACKS:
        try:
            sample.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue

    return "latin-1"


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def parse_csv(raw_bytes: bytes, encoding: str) -> pd.DataFrame:
    """
    Parse raw CSV bytes into a DataFrame using the given encoding.
    Raises ValueError if the content cannot be parsed as CSV.
    """
    try:
        return pd.read_csv(io.BytesIO(raw_bytes), encoding=encoding)
    except Exception as exc:
        raise ValueError(f"Failed to parse CSV: {exc}") from exc


# ---------------------------------------------------------------------------
# dtype mapping
# ---------------------------------------------------------------------------

def _dtype_label(series: pd.Series) -> str:
    """
    Map a pandas Series dtype to a human-readable label used in the API
    response and in the AI system prompt.
    """
    dtype = series.dtype

    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"

    # Attempt to detect datetime stored as strings
    if pd.api.types.is_object_dtype(dtype):
        sample = series.dropna().head(5)
        try:
            pd.to_datetime(sample, infer_datetime_format=True)
            return "datetime"
        except Exception:
            pass
        return "string"

    return "string"


# ---------------------------------------------------------------------------
# Schema extraction
# ---------------------------------------------------------------------------

_MAX_SAMPLE_VALUES = 5
_MAX_SAMPLE_VALUE_LEN = 50  # characters; long strings are truncated


def extract_schema(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Extract per-column schema information from a DataFrame.

    Returns a list of dicts with keys:
      name          — column name
      dtype         — human-readable type label
      non_null_rate — fraction of non-null values (0.0 – 1.0), rounded to 4 dp
      sample_values — up to 5 unique non-null values, all cast to string
    """
    schema = []
    for col in df.columns:
        series = df[col]
        non_null_rate = round(float(series.notna().mean()), 4)

        # Collect unique non-null sample values
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


# ---------------------------------------------------------------------------
# Preview data
# ---------------------------------------------------------------------------

def get_preview(df: pd.DataFrame, n: int = 10) -> list[dict[str, Any]]:
    """
    Return the first `n` rows of a DataFrame as a list of dicts suitable
    for JSON serialisation.

    NaN and NaT values are replaced with None so that json.dumps does not
    raise on float('nan').  numpy integer types are cast to Python int so
    that json.dumps handles them correctly.
    """
    preview_df = df.head(n).copy()

    # Replace NaN/NaT with None
    preview_df = preview_df.where(preview_df.notna(), other=None)

    rows = preview_df.to_dict(orient="records")

    # Coerce numpy scalars (e.g. np.int64, np.float64) to native Python types
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        cleaned.append(
            {
                k: _coerce_value(v)
                for k, v in row.items()
            }
        )
    return cleaned


def _coerce_value(v: Any) -> Any:
    """Convert numpy scalar types to their Python equivalents for JSON safety."""
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


# ---------------------------------------------------------------------------
# Suggested questions (template-based, no Claude API call)
# ---------------------------------------------------------------------------

def generate_suggestions(files_metadata: list[dict[str, Any]]) -> list[str]:
    """
    Generate a list of suggested analysis questions based on the schema of
    the provided files.  Questions are derived from column names and types
    using simple templates — no AI call is made.

    files_metadata: list of dicts with keys "filename" and "columns_info"
                    (each column_info dict has "name" and "dtype").
    """
    suggestions: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        if q not in seen:
            seen.add(q)
            suggestions.append(q)

    numeric_cols: list[tuple[str, str]] = []  # (col_name, filename)
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

    # Numeric column suggestions
    for col_name, fname in numeric_cols[:2]:
        add(f"What is the average, min, and max of '{col_name}'?")
        add(f"Show the top 10 rows sorted by '{col_name}' in descending order.")

    # String / categorical suggestions
    for col_name, fname in string_cols[:2]:
        add(f"What are the unique values in '{col_name}' and how many rows does each have?")

    # Datetime suggestions (combined with a numeric column if available)
    if datetime_cols and numeric_cols:
        dt_col = datetime_cols[0][0]
        num_col = numeric_cols[0][0]
        add(f"Show the trend of '{num_col}' over '{dt_col}'.")

    # Missing value suggestion — always useful
    add("Which columns have the most missing values?")

    # Data quality suggestion
    add("Describe the overall data quality of the uploaded file(s).")

    # Cross-file suggestion when multiple files are present
    if len(files_metadata) > 1:
        names = [m.get("filename", "file") for m in files_metadata[:2]]
        add(f"Compare the data in '{names[0]}' and '{names[1]}' — what are the key differences?")
        if numeric_cols:
            add(
                f"Is the distribution of '{numeric_cols[0][0]}' similar across '{names[0]}' "
                f"and '{names[1]}'?"
            )

    return suggestions[:8]  # cap at 8 to keep the UI clean
