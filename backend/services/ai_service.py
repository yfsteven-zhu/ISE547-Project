"""
services/ai_service.py — OpenRouter API integration with streaming SSE output.

Responsibilities:
  1. Build the system prompt from selected files' metadata.
  2. Format conversation history into the OpenAI-compatible messages format.
  3. Stream a response via Server-Sent Events (SSE).
  4. Detect AI-generated `python_analysis` code blocks and execute them
     via code_executor (feature 3.4).
  5. Detect AI-generated `chart_spec` JSON blocks, validate, and relay
     them as structured SSE events (feature 3.5).
  6. Provide preset interpretation prompt and LLM model registries.

SSE event types emitted by stream_chat_response():
  {"type": "text_delta",        "content": "..."}   — incremental text
  {"type": "code_start"}                             — start of analysis code block
  {"type": "code_delta",        "content": "..."}   — incremental code lines
  {"type": "code_end"}                               — end of analysis code block
  {"type": "executing"}                              — code is being run in sandbox
  {"type": "execution_result",  "output": "...",
                                "result_table": [...] | null,
                                "result_scalar": "..." | null,
                                "error": "..." | absent}
  {"type": "debug_start",       "attempt": N,
                                "max_retries": M}    — LLM debug-fix round N of M starting
  {"type": "chart_spec",        ...ChartSpec fields} — validated chart data
  {"type": "error",             "content": "..."}   — unrecoverable error shown to user
  {"type": "done"}                                   — stream complete (always last)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Generator

from openai import OpenAI

from schemas import ChartSpec
from services.code_executor import ExecutionResult, execute_pandas_code, _filename_to_varname

# ---------------------------------------------------------------------------
# OpenRouter client — configure once at module load
# ---------------------------------------------------------------------------

_client = OpenAI(
    api_key=os.environ.get("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

MODELS: dict[str, dict[str, str]] = {
    "nemotron": {
        "name": "Nemotron 3 Super",
        "router_id": "nvidia/nemotron-3-super-120b-a12b:free",
        "description": "NVIDIA Nemotron 3 Super 120B — high reasoning capability.",
    },
    "gpt_oss": {
        "name": "OpenAI gpt-oss-20b (free)",
        "router_id": "openai/gpt-oss-20b:free",
        "description": "OpenAI open-source 20B model via OpenRouter.",
    },
    "arcee_trinity": {
        "name": "Arcee AI Trinity Large Preview",
        "router_id": "arcee-ai/trinity-large-preview:free",
        "description": "Arcee AI Trinity Large — efficient instruction-following.",
    },
    "elephant": {
        "name": "Elephant",
        "router_id": "openrouter/elephant-alpha",
        "description": "OpenRouter Elephant Alpha — experimental model.",
    },
}

DEFAULT_MODEL_ID = "nemotron"

# ---------------------------------------------------------------------------
# Interpretation prompt registry (used for the Pass 2 / interpretation step)
# ---------------------------------------------------------------------------

INTERPRETATION_PROMPTS: dict[str, dict[str, str]] = {
    "baseline": {
        "name": "Business Assistant",
        "description": "Directly answers user questions based on data outputs.",
        "instruction": (
            "You are an E-commerce Business Assistant. Please carefully read the "
            "provided data, and review and directly answer the user's business queries. "
            "When answering, try your best to fulfill the user's requests and provide "
            "recommendations based on the information found within the data."
        ),
    },
    "COT": {
        "name": "Data Scientist",
        "description": "Ensure the absolute authenticity and logical consistency of data-driven conclusions.",
        "instruction": (
            "You are a highly rigorous Senior E-commerce Data Scientist. Your primary "
            "task is to ensure the absolute authenticity and logical consistency of your "
            "data-driven conclusions.\n"
            "Before answering any business questions from the user, you MUST internally "
            "execute the following verification steps:\n\n"
            "* Cross-Validation: You must manually compare the numerical rating against "
            "the actual semantic meaning of the reviewText.\n\n"
            "* Reject Hallucinations & Anomalies: If you discover self-contradictory "
            "'dirty data' (e.g., a perfect 5-star rating paired with a reviewText that "
            "is entirely complaining), you must explicitly flag these ASINs as anomalies "
            "and refuse to provide business advice based on them.\n\n"
            "* Strict Deduction: Only after filtering out noise (prioritizing reviews "
            "where verifiedPurchase is True and helpfulVote > 0) should you provide "
            "highly restrained, factual business insights."
        ),
    },
    "Action-Oriented": {
        "name": "Action-Oriented",
        "description": "Focus on core pain points in reviews to identify profitable opportunities and unmet blue-ocean markets.",
        "instruction": (
            "You are a top-tier E-commerce Brand Operator and Venture Capitalist who "
            "has led multi-million dollar product launches. You do not care about "
            "trivial data auditing details or minor data anomalies; you only care about "
            "'profitable business signals' and 'unmet blue-ocean markets'.\n\n"
            "Based on the provided ASIN data, please cut straight to the chase:\n\n"
            "* Be Direct: Do not give me a descriptive laundry list of data. Extract "
            "the core consumer pain points directly from the reviewText and tell me "
            "where the money-making opportunities lie.\n\n"
            "* Bold Strategies: Combine the user complaints with the price_value "
            "distributions to propose highly aggressive and disruptive new product "
            "development or marketing strategies.\n\n"
            "* Executive Perspective: Your answer must be highly persuasive, logically "
            "tight, and extremely actionable. Tell the brand exactly what immediate, "
            "concrete steps they need to take to capture the market, ignoring "
            "irrelevant background noise."
        ),
    },
}

DEFAULT_PROMPT_ID = "baseline"

# ---------------------------------------------------------------------------
# Token budget constants
# ---------------------------------------------------------------------------

MAX_TOKENS = 8192

# Maximum number of files included in a single system prompt
MAX_FILES_IN_PROMPT = 10

# Maximum preview rows injected per file into the AI context
MAX_PREVIEW_ROWS = 5

# Maximum sample values shown per column
MAX_SAMPLE_VALUES = 5

# Maximum conversation turns (user + model pairs) sent to the API.
# Older messages remain in the DB but are dropped from the API payload.
MAX_HISTORY_MESSAGES = 20

# Number of LLM-assisted debug-fix attempts when code execution fails.
MAX_DEBUG_RETRIES = 3

# ---------------------------------------------------------------------------
# System prompt assembly
# ---------------------------------------------------------------------------

# Code-generation prompt — fixed, not user-selectable.
# Instructs the AI to wrap exact-computation code in python_analysis blocks.
_SYSTEM_PROMPT_HEADER = """\
[ROLE]
You are a strict Python/Pandas expert and a data analysis assistant.
You answer the user's questions about the CSV file(s) listed below by either:
  (a) Direct textual reasoning from the schema/samples (only when the answer is
      already inferable from the metadata), OR
  (b) Generating exact pandas code wrapped in a `python_analysis` fenced block
      (for any question requiring computation, aggregation, filtering, joins,
      counts, sums, averages, etc.).

When in doubt, prefer (b) so the answer is computed precisely.

=== DATA CONTEXT ===
"""

_SYSTEM_PROMPT_FOOTER = """\

=== END OF DATA CONTEXT ===

[CRITICAL CONSTRAINTS]
1. Each DataFrame listed above is ALREADY LOADED in memory under the exact
   Python variable name shown on its `Variable:` line.  Use those names directly.
2. DO NOT create, define, mock, or reload any DataFrames.
3. ABSOLUTELY FORBIDDEN: pd.read_csv(), pd.read_excel(), open(), or ANY file I/O —
   the source files are NOT on disk in the sandbox; calling them will raise
   FileNotFoundError or ImportError.
4. ABSOLUTELY FORBIDDEN: import statements.  Only `pd` (pandas) and `np` (numpy)
   are pre-imported and available in the sandbox.
5. ONLY use column names explicitly listed in [DATA CONTEXT].  If a column you
   need is not listed, set
       result = "Error: required column not found"
   instead of guessing.

[MULTI-FILE JOIN RULES]
If the question requires combining data from two or more DataFrames:
1. PRIORITY KEY: Prefer a join column whose name contains "ASIN" or "asin"
   (case-insensitive, e.g. "ASIN", "productASIN", "asin_id") and which exists
   in BOTH DataFrames.
2. FALLBACK KEY: If no ASIN-style column exists in both, pick a single column
   with an EXACT name match across the DataFrames.  If no exact match, choose
   the most semantically similar identifier-like column (e.g. "s.no",
   "product_id", "id").
3. VALIDATION: The join key must NOT be a free-text or long-string column
   (e.g. description, title, review_body).
4. JOIN EXECUTION:
     - Cast join keys to string and strip whitespace before merging:
         left[key]  = left[key].astype(str).str.strip()
         right[key] = right[key].astype(str).str.strip()
     - Identify the primary table (the one with more unique key values) and
       perform a LEFT JOIN (`how="left"`) from it.
5. STRICTLY PROHIBITED: concatenating multiple columns to form a key,
   or using fuzzy / approximate matching.

[ROBUSTNESS]
1. TYPE SAFETY: Check column existence (`if 'col' in df.columns:`) before
   operating on it.
2. DATETIME HANDLING: For date-related questions use
   `pd.to_datetime(df['col'], errors='coerce')` to avoid format crashes.
3. NUMERIC SAFETY: When operating numerically on a column that may contain
   string values, coerce with `pd.to_numeric(df['col'], errors='coerce')`.
4. DO NOT wrap your logic in a try/except block.  Let any exception surface —
   the system has an automated debug-and-retry loop that will ask you to fix
   the code if execution fails.

[CODE GENERATION FORMAT]
When you choose path (b) (generating code):
  1. Respond with EXACTLY ONE fenced code block tagged `python_analysis`:
     ```python_analysis
     # use the DataFrame variable names from [DATA CONTEXT]
     # store the final answer in a variable named `result`
     # `result` may be: a scalar (int / float / str), or a DataFrame
     # (call .head(20) on large DataFrames to keep the output compact)
     result = products.groupby("category")["price_value"].mean().reset_index()
     ```
  2. Output ONLY this fenced block in your first response — no prose, no other
     fences, no print() calls, no re-imports.
  3. The system will execute the code and call you again with the output;
     you will then write the user-facing answer.

[CHART GENERATION FORMAT]
When your FINAL textual answer (in the second pass) would benefit from a
visualisation, append a fenced JSON block tagged `chart_spec` AFTER the text:
  ```chart_spec
  {
    "chart_type": "bar",
    "title": "Revenue by Category",
    "data": [{"name": "Electronics", "value": 120000}, ...],
    "x_key": "name",
    "y_key": "value",
    "x_label": "Category",
    "y_label": "Revenue"
  }
  ```
chart_type must be one of: "bar", "line", "pie", "scatter", "heatmap".
Choose the chart that best fits the data:
  - Categorical comparison   → bar
  - Trend over time          → line
  - Part-of-whole proportion → pie
  - Correlation (2 numerics) → scatter
  - Correlation matrix       → heatmap
Include at most ONE chart per response.

[GENERAL GUIDELINES]
- Format numeric results as Markdown tables when appropriate.
- Be concise and precise.  Do not invent data not present in the schema/samples.
- When comparing multiple files, reference them by name (or by Variable name)
  and identify potential join keys explicitly.
"""


def build_system_prompt(files_context: list[dict[str, Any]]) -> str:
    """
    Assemble the system prompt from the metadata of the selected files.

    files_context: list of dicts with keys matching CsvFileDetail fields
                   (filename, description, row_count, column_count, encoding,
                    columns_info, preview_data).
    """
    prompt = _SYSTEM_PROMPT_HEADER
    cap = min(len(files_context), MAX_FILES_IN_PROMPT)

    if len(files_context) > MAX_FILES_IN_PROMPT:
        prompt += (
            f"[Note: showing first {MAX_FILES_IN_PROMPT} of "
            f"{len(files_context)} selected files due to context limits]\n\n"
        )

    for i, file_meta in enumerate(files_context[:cap], start=1):
        prompt += _format_file_section(i, file_meta)

    prompt += _SYSTEM_PROMPT_FOOTER
    return prompt


def _format_file_section(index: int, file_meta: dict[str, Any]) -> str:
    """Format the system prompt section for a single file."""
    filename = file_meta.get("filename", f"file_{index}")
    description = file_meta.get("description") or ""
    row_count = file_meta.get("row_count", "?")
    column_count = file_meta.get("column_count", "?")
    encoding = file_meta.get("encoding", "utf-8")
    columns_info = file_meta.get("columns_info", [])
    preview_data = file_meta.get("preview_data", [])

    var_name = _filename_to_varname(filename)

    lines: list[str] = []
    lines.append(f"=== FILE {index}: {filename} ===")
    lines.append(f"Variable: {var_name}    (use this exact name to reference the DataFrame)")
    if description:
        lines.append(f"Description: {description}")
    lines.append(f"Rows: {row_count:,} | Columns: {column_count} | Encoding: {encoding}")
    lines.append("")

    # Column schema table
    lines.append("Columns:")
    for col in columns_info:
        name = col.get("name", "?")
        dtype = col.get("dtype", "?")
        rate = col.get("non_null_rate", 1.0)
        samples = col.get("sample_values", [])[:MAX_SAMPLE_VALUES]
        sample_str = ", ".join(str(s) for s in samples) if samples else "—"
        lines.append(
            f"  - {name} ({dtype}): non-null {rate * 100:.1f}% | samples: {sample_str}"
        )
    lines.append("")

    # Preview rows as a Markdown table
    preview_rows = preview_data[:MAX_PREVIEW_ROWS]
    if preview_rows and columns_info:
        col_names = [c.get("name", "") for c in columns_info]
        header = "| " + " | ".join(col_names) + " |"
        separator = "| " + " | ".join("---" for _ in col_names) + " |"
        lines.append(f"Sample Data (first {len(preview_rows)} rows):")
        lines.append(header)
        lines.append(separator)
        for row in preview_rows:
            cells = [str(row.get(c, "")) for c in col_names]
            lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Message format construction (OpenAI-compatible)
# ---------------------------------------------------------------------------

def _build_messages(
    system_prompt: str,
    history: list[dict[str, str]],
    user_message: str,
) -> list[dict[str, Any]]:
    """
    Build an OpenAI-format message list.

    Structure:
      [{"role": "system", "content": <system_prompt>},
       ... history (trimmed to MAX_HISTORY_MESSAGES, alternating roles) ...,
       {"role": "user", "content": <user_message>}]

    Consecutive same-role messages in history are collapsed by joining
    their content with a newline.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]

    raw = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in history[-MAX_HISTORY_MESSAGES:]
    ]
    raw.append({"role": "user", "content": user_message})

    # Collapse consecutive same-role messages
    collapsed: list[dict[str, str]] = []
    for msg in raw:
        role = "assistant" if msg["role"] == "assistant" else "user"
        if collapsed and collapsed[-1]["role"] == role:
            collapsed[-1]["content"] += "\n" + msg["content"]
        else:
            collapsed.append({"role": role, "content": msg["content"]})

    messages.extend(collapsed)
    return messages


# ---------------------------------------------------------------------------
# Registry accessors (used by routers)
# ---------------------------------------------------------------------------

def get_prompt_list() -> list[dict[str, str]]:
    """Return interpretation prompt preset metadata for the /prompts endpoint."""
    return [
        {"id": pid, "name": p["name"], "description": p["description"]}
        for pid, p in INTERPRETATION_PROMPTS.items()
    ]


def get_model_list() -> list[dict[str, str]]:
    """Return model preset metadata for the /models endpoint."""
    return [
        {"id": mid, "name": m["name"], "description": m["description"]}
        for mid, m in MODELS.items()
    ]


# ---------------------------------------------------------------------------
# SSE formatting helpers
# ---------------------------------------------------------------------------

def _sse(data: dict[str, Any]) -> str:
    """Serialise a dict as a single SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# Fenced block detection patterns
# ---------------------------------------------------------------------------

_CODE_BLOCK_OPEN = re.compile(r"```(python_analysis|chart_spec)")
_CODE_BLOCK_CLOSE = "```"


# ---------------------------------------------------------------------------
# Core streaming pipeline
# ---------------------------------------------------------------------------

def stream_chat_response(
    message: str,
    history: list[dict[str, str]],
    files_context: list[dict[str, Any]],
    dataframes: dict,
    model_id: str = DEFAULT_MODEL_ID,
    prompt_id: str = DEFAULT_PROMPT_ID,
) -> Generator[str, None, None]:
    """
    Main entry point for the chat streaming pipeline.

    Yields SSE-formatted strings.  Always ends with a `done` event.

    Two-pass flow for computation questions (feature 3.4):
      Pass 1: Ask the LLM.  If it returns a `python_analysis` block, intercept.
      Execute: Run the code via code_executor.
      Pass 2: Feed execution results + selected interpretation prompt back to the LLM.

    Single-pass flow for direct / inferrable questions:
      Pass 1: LLM streams a text answer (possibly with a chart_spec block).
    """
    # Resolve model router ID (fall back to default for unknown IDs)
    model_entry = MODELS.get(model_id, MODELS[DEFAULT_MODEL_ID])
    router_model = model_entry["router_id"]

    # Resolve interpretation instruction (fall back to default for unknown IDs)
    prompt_entry = INTERPRETATION_PROMPTS.get(prompt_id, INTERPRETATION_PROMPTS[DEFAULT_PROMPT_ID])
    interpretation_instruction = prompt_entry["instruction"]

    system_prompt = build_system_prompt(files_context)
    messages = _build_messages(system_prompt, history, message)

    try:
        yield from _stream_pass(
            messages,
            dataframes,
            is_first_pass=True,
            model=router_model,
            interpretation_instruction=interpretation_instruction,
        )
    except Exception as exc:
        error_msg = str(exc)
        if "API_KEY" in error_msg.upper() or "authentication" in error_msg.lower():
            error_msg = "Invalid or missing OpenRouter API key. Check your .env file."
        elif "quota" in error_msg.lower() or "429" in error_msg:
            error_msg = "OpenRouter API quota exceeded. Please try again shortly."
        elif "model" in error_msg.lower() and "not found" in error_msg.lower():
            error_msg = f"Model '{router_model}' not found on OpenRouter."
        yield _sse({"type": "error", "content": error_msg})
    finally:
        yield _sse({"type": "done"})


def _stream_pass(
    messages: list[dict[str, Any]],
    dataframes: dict,
    is_first_pass: bool,
    model: str,
    interpretation_instruction: str,
) -> Generator[str, None, None]:
    """
    Execute one OpenRouter API streaming call and handle the response.

    If a `python_analysis` block is detected in the stream:
      - Emit code_start / code_delta / code_end events.
      - Execute the code in the sandbox.
      - Emit executing / execution_result events.
      - Recurse for Pass 2 with execution results injected.

    If a `chart_spec` block is detected:
      - Parse and validate the JSON.
      - Emit a chart_spec event.

    Plain text is emitted as text_delta events.
    """
    response = _client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=MAX_TOKENS,
        stream=True,
    )

    buffer = ""
    in_code_block = False
    code_block_tag = ""   # "python_analysis" or "chart_spec"
    code_buffer = ""      # accumulates content inside a fenced block

    for chunk in response:
        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta
        chunk_text = delta.content
        if not chunk_text:
            continue

        buffer += chunk_text

        # Phase 1: if not yet in a code block, look for an opening fence.
        # Using a separate `if in_code_block` below (not `else`) means that
        # when the opening AND closing fences arrive in the same chunk we
        # handle the close immediately instead of waiting for the next chunk.
        if not in_code_block:
            match = _CODE_BLOCK_OPEN.search(buffer)
            if match:
                # Emit any text that appeared before the opening fence
                pre_text = buffer[: match.start()]
                if pre_text:
                    yield _sse({"type": "text_delta", "content": pre_text})

                in_code_block = True
                code_block_tag = match.group(1)
                code_buffer = ""
                # Consume the buffer up to (and including) the opening fence tag
                buffer = buffer[match.end():]

                if code_block_tag == "python_analysis":
                    yield _sse({"type": "code_start"})
                # chart_spec blocks are buffered silently until closed
            else:
                # No fence detected yet; safely emit all but the trailing chars
                # that might be the start of a fence spanning two chunks
                safe_len = max(0, len(buffer) - 14)
                if safe_len:
                    yield _sse({"type": "text_delta", "content": buffer[:safe_len]})
                    buffer = buffer[safe_len:]

        # Phase 2: if now inside a fenced block, look for the closing ```.
        # This intentionally runs in the same iteration as Phase 1 so that
        # models which return the entire block in one chunk are handled correctly.
        if in_code_block:
            close_pos = buffer.find(_CODE_BLOCK_CLOSE)
            if close_pos == -1:
                # Block not yet closed; stream code lines
                if code_block_tag == "python_analysis":
                    yield _sse({"type": "code_delta", "content": buffer})
                code_buffer += buffer
                buffer = ""
            else:
                # Block is now closed
                final_chunk = buffer[:close_pos]
                if code_block_tag == "python_analysis":
                    if final_chunk:
                        yield _sse({"type": "code_delta", "content": final_chunk})
                    yield _sse({"type": "code_end"})
                code_buffer += final_chunk

                buffer = buffer[close_pos + 3:]  # skip past the closing ```
                in_code_block = False

                if code_block_tag == "python_analysis" and is_first_pass:
                    # Execute the code and recurse for Pass 2
                    yield from _execute_and_continue(
                        code_buffer.strip(),
                        messages,
                        dataframes,
                        model=model,
                        interpretation_instruction=interpretation_instruction,
                    )
                    return  # Pass 2 handles the remainder of the response

                elif code_block_tag == "chart_spec":
                    yield from _emit_chart_spec(code_buffer.strip())

                code_buffer = ""
                code_block_tag = ""

    # Flush any remaining buffered text after the stream ends
    if buffer and not in_code_block:
        yield _sse({"type": "text_delta", "content": buffer})


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------

def _build_execution_summary(exec_result: ExecutionResult) -> str:
    """Summarise a successful execution result as a plain-text string for Pass 2."""
    parts: list[str] = []
    if exec_result.output:
        parts.append(f"stdout:\n{exec_result.output}")
    if exec_result.result_table is not None:
        parts.append(
            f"result (DataFrame, {len(exec_result.result_table)} rows):\n"
            + json.dumps(exec_result.result_table[:20], ensure_ascii=False)
        )
    if exec_result.result_scalar is not None:
        parts.append(f"result: {exec_result.result_scalar}")
    return "\n\n".join(parts) if parts else "(no output produced)"


def _build_pass2_messages(
    original_messages: list[dict[str, Any]],
    code: str,
    execution_summary: str,
    interpretation_instruction: str,
) -> list[dict[str, Any]]:
    """Build the Pass 2 message list with execution results appended."""
    pass2_messages = list(original_messages)
    pass2_messages.append({
        "role": "assistant",
        "content": f"```python_analysis\n{code}\n```",
    })
    pass2_messages.append({
        "role": "user",
        "content": (
            f"The code was executed.  Here is the result:\n\n"
            f"```\n{execution_summary}\n```\n\n"
            f"Now please write the final answer for the user based on this result.  "
            f"{interpretation_instruction}"
        ),
    })
    return pass2_messages


def _execute_and_continue(
    code: str,
    original_messages: list[dict[str, Any]],
    dataframes: dict,
    model: str,
    interpretation_instruction: str,
) -> Generator[str, None, None]:
    """
    Execute AI-generated pandas code and either:
      - Proceed to Pass 2 (interpretation) on success, or
      - Delegate to _debug_and_retry on failure (up to MAX_DEBUG_RETRIES attempts).
    """
    yield _sse({"type": "executing"})
    exec_result: ExecutionResult = execute_pandas_code(code, dataframes)

    result_event: dict[str, Any] = {
        "type": "execution_result",
        "output": exec_result.output,
        "result_table": exec_result.result_table,
        "result_scalar": exec_result.result_scalar,
    }
    if exec_result.error:
        result_event["error"] = exec_result.error
    yield _sse(result_event)

    if exec_result.error:
        yield from _debug_and_retry(
            failed_code=code,
            error=exec_result.error,
            original_messages=original_messages,
            dataframes=dataframes,
            model=model,
            interpretation_instruction=interpretation_instruction,
            retries_left=MAX_DEBUG_RETRIES,
        )
        return

    # Success — proceed to interpretation (Pass 2)
    execution_summary = _build_execution_summary(exec_result)
    pass2_messages = _build_pass2_messages(
        original_messages, code, execution_summary, interpretation_instruction
    )
    yield from _stream_pass(
        pass2_messages, dataframes, is_first_pass=False,
        model=model, interpretation_instruction=interpretation_instruction,
    )


def _debug_and_retry(
    failed_code: str,
    error: str,
    original_messages: list[dict[str, Any]],
    dataframes: dict,
    model: str,
    interpretation_instruction: str,
    retries_left: int,
) -> Generator[str, None, None]:
    """
    Ask the LLM to fix failed code, re-execute, and retry up to retries_left times.

    Flow per attempt:
      1. Emit debug_start event.
      2. Call LLM (non-streaming) with the failed code + error, request a fix.
      3. Extract the fixed python_analysis block from the response.
      4. Emit code_start / code_delta / code_end for the fixed code.
      5. Execute the fixed code in the sandbox.
      6. Emit executing / execution_result.
      7a. On success → proceed to Pass 2 (interpretation).
      7b. On failure with retries remaining → recurse.
      7c. On failure with no retries left → emit error event.
    """
    attempt = MAX_DEBUG_RETRIES - retries_left + 1
    yield _sse({"type": "debug_start", "attempt": attempt, "max_retries": MAX_DEBUG_RETRIES})

    # Build the debug prompt: original context + failed code + error
    debug_messages = list(original_messages)
    debug_messages.append({
        "role": "assistant",
        "content": f"```python_analysis\n{failed_code}\n```",
    })
    debug_messages.append({
        "role": "user",
        "content": (
            f"The code raised an error:\n\n"
            f"```\n{error}\n```\n\n"
            "Please fix the code. Respond ONLY with a corrected `python_analysis` "
            "fenced code block — no explanations, just the fixed code."
        ),
    })

    # Non-streaming call: the debug response is short (just a code block)
    try:
        response = _client.chat.completions.create(
            model=model,
            messages=debug_messages,
            max_tokens=MAX_TOKENS,
            stream=False,
        )
        full_text = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        yield _sse({"type": "error", "content": f"Debug API call failed: {exc}"})
        return

    # Extract the fixed code: prefer python_analysis tag, fall back to generic python
    fixed_code: str | None = None
    for pattern in (
        r"```python_analysis\s*\n(.*?)\n?```",
        r"```python\s*\n(.*?)\n?```",
    ):
        m = re.search(pattern, full_text, re.DOTALL)
        if m:
            fixed_code = m.group(1).strip()
            break

    if not fixed_code:
        yield _sse({
            "type": "error",
            "content": (
                "The AI was unable to produce a fix for the code. "
                "Please rephrase your question."
            ),
        })
        return

    # Emit the fixed code as SSE events so the UI can display it
    yield _sse({"type": "code_start"})
    yield _sse({"type": "code_delta", "content": fixed_code})
    yield _sse({"type": "code_end"})

    # Execute the fixed code
    yield _sse({"type": "executing"})
    exec_result: ExecutionResult = execute_pandas_code(fixed_code, dataframes)

    result_event: dict[str, Any] = {
        "type": "execution_result",
        "output": exec_result.output,
        "result_table": exec_result.result_table,
        "result_scalar": exec_result.result_scalar,
    }
    if exec_result.error:
        result_event["error"] = exec_result.error
    yield _sse(result_event)

    if exec_result.error:
        if retries_left > 1:
            yield from _debug_and_retry(
                failed_code=fixed_code,
                error=exec_result.error,
                original_messages=original_messages,
                dataframes=dataframes,
                model=model,
                interpretation_instruction=interpretation_instruction,
                retries_left=retries_left - 1,
            )
        else:
            yield _sse({
                "type": "error",
                "content": (
                    f"Code execution failed after {MAX_DEBUG_RETRIES} debug "
                    f"attempts. Last error: {exec_result.error}"
                ),
            })
        return

    # Fixed code succeeded — proceed to interpretation (Pass 2)
    execution_summary = _build_execution_summary(exec_result)
    pass2_messages = _build_pass2_messages(
        original_messages, fixed_code, execution_summary, interpretation_instruction
    )
    yield from _stream_pass(
        pass2_messages, dataframes, is_first_pass=False,
        model=model, interpretation_instruction=interpretation_instruction,
    )


def _emit_chart_spec(raw_json: str) -> Generator[str, None, None]:
    """
    Parse, validate, and emit a chart_spec SSE event.
    Skips silently if the JSON is malformed or fails Pydantic validation.
    """
    try:
        data = json.loads(raw_json)
        spec = ChartSpec(**data)
        event: dict[str, Any] = {"type": "chart_spec"}
        event.update(spec.model_dump(exclude_none=True))
        yield _sse(event)
    except Exception:
        # Invalid chart spec — do not break the stream
        pass
