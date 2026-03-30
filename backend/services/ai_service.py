"""
services/ai_service.py — Gemini 2.5 Pro integration with streaming SSE output.

Responsibilities:
  1. Build the system prompt from selected files' metadata.
  2. Format conversation history into the Gemini messages format.
  3. Stream a response via Server-Sent Events (SSE).
  4. Detect AI-generated `python_analysis` code blocks and execute them
     via code_executor (feature 3.4).
  5. Detect AI-generated `chart_spec` JSON blocks, validate, and relay
     them as structured SSE events (feature 3.5).

SSE event types emitted by stream_chat_response():
  {"type": "text_delta",        "content": "..."}   — incremental text
  {"type": "code_start"}                             — start of analysis code block
  {"type": "code_delta",        "content": "..."}   — incremental code lines
  {"type": "code_end"}                               — end of analysis code block
  {"type": "executing"}                              — code is being run in sandbox
  {"type": "execution_result",  "output": "...",
                                "result_table": [...] | null,
                                "result_scalar": "..." | null}
  {"type": "chart_spec",        ...ChartSpec fields} — validated chart data
  {"type": "error",             "content": "..."}   — recoverable error
  {"type": "done"}                                   — stream complete (always last)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Generator

import google.generativeai as genai

from schemas import ChartSpec
from services.code_executor import ExecutionResult, execute_pandas_code

# ---------------------------------------------------------------------------
# Gemini client — configure once at module load
# ---------------------------------------------------------------------------

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# Gemini 2.5 Pro model name.
# Update this constant if Google releases a newer stable identifier.
MODEL = "gemini-2.5-pro"

MAX_TOKENS = 8192

# ---------------------------------------------------------------------------
# Token budget constants
# ---------------------------------------------------------------------------

# Maximum number of files included in a single system prompt
MAX_FILES_IN_PROMPT = 10

# Maximum preview rows injected per file into the AI context
MAX_PREVIEW_ROWS = 5

# Maximum sample values shown per column
MAX_SAMPLE_VALUES = 5

# Maximum conversation turns (user + model pairs) sent to the API.
# Older messages remain in the DB but are dropped from the API payload.
MAX_HISTORY_MESSAGES = 20

# ---------------------------------------------------------------------------
# System prompt assembly
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_HEADER = """\
You are a data analysis assistant.  The user has uploaded the following CSV file(s).
You must answer their questions based strictly on the data described below.

When exact figures are needed that cannot be inferred from the sample rows alone,
you MUST generate executable pandas code to compute the answer precisely.

=== INSTRUCTIONS FOR CODE GENERATION ===
If a question requires exact computation (counts, sums, averages, groupings, etc.):
  1. Respond ONLY with a fenced code block tagged `python_analysis`:
     ```python_analysis
     # your pandas code here
     # DataFrames are available as variables named after the filename
     # (extension stripped, spaces/hyphens replaced with underscores)
     # Store your final answer in a variable named `result`
     # (a DataFrame or a scalar value such as int, float, or str)
     result = df.groupby("category")["revenue"].sum().reset_index()
     ```
  2. Do NOT include any explanatory text in this first response — only the code block.
  3. The system will execute the code and provide you with the output.
     You will then be asked to write the final answer based on that output.

=== INSTRUCTIONS FOR CHART GENERATION ===
When your final answer would benefit from a visualisation, append a fenced JSON
block tagged `chart_spec` AFTER your text answer:
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
chart_type must be one of: "bar", "line", "pie", "scatter", "heatmap"
Choose the chart type that best fits the data:
  - Categorical comparison   → bar
  - Trend over time          → line
  - Part-of-whole proportion → pie
  - Correlation (2 numerics) → scatter
  - Correlation matrix       → heatmap
Include at most ONE chart per response.

=== DATA CONTEXT ===
"""

_SYSTEM_PROMPT_FOOTER = """\

=== END OF DATA CONTEXT ===

Additional guidelines:
- Format numeric results as Markdown tables when appropriate.
- Be concise and precise.  Do not invent data not present in the schema or samples.
- When comparing multiple files, reference them by name and identify potential join keys.
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

    lines: list[str] = []
    lines.append(f"=== FILE {index}: {filename} ===")
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
# Message format conversion
# ---------------------------------------------------------------------------

def build_gemini_messages(
    history: list[dict[str, str]],
    user_message: str,
) -> list[dict[str, Any]]:
    """
    Convert conversation history + current user message into Gemini's
    expected messages format.

    Gemini uses role "model" instead of "assistant", and wraps text in
    a `parts` list.

    Only the most recent MAX_HISTORY_MESSAGES are included.
    Messages must alternate between "user" and "model"; consecutive same-role
    messages are collapsed by joining their content.
    """
    raw = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in history[-MAX_HISTORY_MESSAGES:]
    ]
    raw.append({"role": "user", "content": user_message})

    # Collapse consecutive same-role messages (Gemini API requirement)
    collapsed: list[dict[str, str]] = []
    for msg in raw:
        gemini_role = "model" if msg["role"] == "assistant" else "user"
        if collapsed and collapsed[-1]["role"] == gemini_role:
            collapsed[-1]["content"] += "\n" + msg["content"]
        else:
            collapsed.append({"role": gemini_role, "content": msg["content"]})

    # Convert to Gemini parts format
    return [
        {"role": m["role"], "parts": [{"text": m["content"]}]}
        for m in collapsed
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
    dataframes: dict,  # variable_name → pd.DataFrame, pre-loaded by the router
) -> Generator[str, None, None]:
    """
    Main entry point for the chat streaming pipeline.

    Yields SSE-formatted strings.  Always ends with a `done` event.

    Two-pass flow for computation questions (feature 3.4):
      Pass 1: Ask Gemini.  If it returns a `python_analysis` block, intercept.
      Execute: Run the code via code_executor.
      Pass 2: Feed execution results back to Gemini for the final answer.

    Single-pass flow for direct / inferrable questions:
      Pass 1: Gemini streams a text answer (possibly with a chart_spec block).
    """
    system_prompt = build_system_prompt(files_context)
    messages = build_gemini_messages(history, message)

    try:
        yield from _stream_pass(system_prompt, messages, dataframes, is_first_pass=True)
    except Exception as exc:
        # Catch-all: surface the error as an SSE event so the frontend can display it
        error_msg = str(exc)
        if "API_KEY" in error_msg.upper() or "authentication" in error_msg.lower():
            error_msg = "Invalid or missing Gemini API key. Check your .env file."
        elif "quota" in error_msg.lower() or "429" in error_msg:
            error_msg = "Gemini API quota exceeded. Please try again shortly."
        elif "model" in error_msg.lower() and "not found" in error_msg.lower():
            error_msg = f"Model '{MODEL}' not found. Check the MODEL constant in ai_service.py."
        yield _sse({"type": "error", "content": error_msg})
    finally:
        yield _sse({"type": "done"})


def _stream_pass(
    system_prompt: str,
    messages: list[dict[str, Any]],
    dataframes: dict,
    is_first_pass: bool,
) -> Generator[str, None, None]:
    """
    Execute one Gemini API streaming call and handle the response.

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
    # Create a model instance with the dynamic system instruction.
    # GenerativeModel instantiation is lightweight (no API call).
    model = genai.GenerativeModel(
        model_name=MODEL,
        system_instruction=system_prompt,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=MAX_TOKENS,
        ),
    )

    response = model.generate_content(messages, stream=True)

    buffer = ""
    in_code_block = False
    code_block_tag = ""   # "python_analysis" or "chart_spec"
    code_buffer = ""      # accumulates content inside a fenced block

    for chunk in response:
        # Extract text from the chunk safely.
        # Gemini may yield non-text chunks (safety ratings, usage metadata).
        try:
            chunk_text = chunk.text
        except (ValueError, AttributeError):
            # No text in this chunk (e.g. safety rating chunk) — skip
            continue

        if not chunk_text:
            continue

        buffer += chunk_text

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
        else:
            # Inside a fenced block — look for the closing ```
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
                        system_prompt,
                        messages,
                        dataframes,
                    )
                    return  # Pass 2 handles the remainder of the response

                elif code_block_tag == "chart_spec":
                    yield from _emit_chart_spec(code_buffer.strip())

                code_buffer = ""
                code_block_tag = ""

    # Flush any remaining buffered text after the stream ends
    if buffer and not in_code_block:
        yield _sse({"type": "text_delta", "content": buffer})


def _execute_and_continue(
    code: str,
    system_prompt: str,
    original_messages: list[dict[str, Any]],
    dataframes: dict,
) -> Generator[str, None, None]:
    """
    Execute AI-generated pandas code and make a second Gemini call (Pass 2)
    with the execution result injected as context.
    """
    yield _sse({"type": "executing"})

    exec_result: ExecutionResult = execute_pandas_code(code, dataframes)

    # Build and emit the execution_result SSE event
    result_event: dict[str, Any] = {
        "type": "execution_result",
        "output": exec_result.output,
        "result_table": exec_result.result_table,
        "result_scalar": exec_result.result_scalar,
    }
    if exec_result.error:
        result_event["error"] = exec_result.error
    yield _sse(result_event)

    # Summarise execution output for Pass 2
    if exec_result.error:
        execution_summary = f"Code execution failed with error:\n{exec_result.error}"
    else:
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
        execution_summary = "\n\n".join(parts) if parts else "(no output produced)"

    # Inject code + execution result into the message history for Pass 2
    pass2_messages = list(original_messages)
    pass2_messages.append({
        "role": "model",
        "parts": [{"text": f"```python_analysis\n{code}\n```"}],
    })
    pass2_messages.append({
        "role": "user",
        "parts": [{"text": (
            f"The code was executed.  Here is the result:\n\n"
            f"```\n{execution_summary}\n```\n\n"
            "Now please write the final answer for the user based on this result.  "
            "You may include a chart_spec block if a visualisation would help."
        )}],
    })

    yield from _stream_pass(system_prompt, pass2_messages, dataframes, is_first_pass=False)


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
