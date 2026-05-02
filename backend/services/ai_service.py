"""
services/ai_service.py — OpenRouter API integration with streaming SSE output.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Generator

from openai import OpenAI

from ..schemas import ChartSpec
from .code_executor import (
    ExecutionResult,
    execute_pandas_code,
    _filename_to_varname,
)

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

MAX_TOKENS = 8192
MAX_FILES_IN_PROMPT = 10
MAX_PREVIEW_ROWS = 5
MAX_SAMPLE_VALUES = 5
MAX_HISTORY_MESSAGES = 20
MAX_DEBUG_RETRIES = 3

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
   Python variable name shown on its `Variable:` line. Use those names directly.
2. DO NOT create, define, mock, or reload any DataFrames.
3. ABSOLUTELY FORBIDDEN: pd.read_csv(), pd.read_excel(), open(), or ANY file I/O.
4. ABSOLUTELY FORBIDDEN: import statements. Only `pd` and `np` are available.
5. ONLY use column names explicitly listed in [DATA CONTEXT]. If a column you
   need is not listed, set:
       result = "Error: required column not found"

[CODE GENERATION FORMAT]
When generating code:
  1. Respond with EXACTLY ONE fenced code block tagged `python_analysis`.
  2. Output ONLY this fenced block in your first response.
  3. The system will execute the code and call you again with the output.

[CHART GENERATION FORMAT]
When your final textual answer would benefit from a visualisation, append a
fenced JSON block tagged `chart_spec`.

[GENERAL GUIDELINES]
- Format numeric results as Markdown tables when appropriate.
- Be concise and precise.
"""


def _get_client() -> OpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")
    return OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )


def build_system_prompt(files_context: list[dict[str, Any]]) -> str:
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


def _build_messages(
    system_prompt: str,
    history: list[dict[str, str]],
    user_message: str,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]

    raw = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in history[-MAX_HISTORY_MESSAGES:]
    ]
    raw.append({"role": "user", "content": user_message})

    collapsed: list[dict[str, str]] = []
    for msg in raw:
        role = "assistant" if msg["role"] == "assistant" else "user"
        if collapsed and collapsed[-1]["role"] == role:
            collapsed[-1]["content"] += "\n" + msg["content"]
        else:
            collapsed.append({"role": role, "content": msg["content"]})

    messages.extend(collapsed)
    return messages


def get_prompt_list() -> list[dict[str, str]]:
    return [
        {"id": pid, "name": p["name"], "description": p["description"]}
        for pid, p in INTERPRETATION_PROMPTS.items()
    ]


def get_model_list() -> list[dict[str, str]]:
    return [
        {"id": mid, "name": m["name"], "description": m["description"]}
        for mid, m in MODELS.items()
    ]


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _normalize_code_block(block_tag: str, code_text: str) -> tuple[str, str]:
    tag = (block_tag or "").strip().lower()
    code = code_text.strip()

    if tag == "python_analysis":
        return "python_analysis", code

    if tag == "chart_spec":
        return "chart_spec", code

    if tag == "python":
        lines = code.splitlines()
        if lines:
            first = lines[0].strip().lower()
            if first == "python_analysis":
                return "python_analysis", "\n".join(lines[1:]).strip()
            if first == "chart_spec":
                return "chart_spec", "\n".join(lines[1:]).strip()
        return "python_analysis", code

    if tag == "":
        lines = code.splitlines()
        if lines:
            first = lines[0].strip().lower()
            if first == "python_analysis":
                return "python_analysis", "\n".join(lines[1:]).strip()
            if first == "chart_spec":
                return "chart_spec", "\n".join(lines[1:]).strip()

    return tag, code


_CODE_BLOCK_OPEN = re.compile(r"```([a-zA-Z_]+)?")
_CODE_BLOCK_CLOSE = "```"


def stream_chat_response(
    message: str,
    history: list[dict[str, str]],
    files_context: list[dict[str, Any]],
    dataframes: dict,
    model_id: str = DEFAULT_MODEL_ID,
    prompt_id: str = DEFAULT_PROMPT_ID,
) -> Generator[str, None, None]:
    model_entry = MODELS.get(model_id, MODELS[DEFAULT_MODEL_ID])
    router_model = model_entry["router_id"]

    prompt_entry = INTERPRETATION_PROMPTS.get(
        prompt_id,
        INTERPRETATION_PROMPTS[DEFAULT_PROMPT_ID],
    )
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
        if "OPENROUTER_API_KEY" in error_msg or "Missing OPENROUTER_API_KEY" in error_msg:
            error_msg = "Missing OPENROUTER_API_KEY in your .env file."
        elif "API_KEY" in error_msg.upper() or "authentication" in error_msg.lower():
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
    client = _get_client()

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=MAX_TOKENS,
        stream=True,
    )

    buffer = ""
    in_code_block = False
    code_block_tag = ""
    code_buffer = ""

    for chunk in response:
        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta
        chunk_text = delta.content
        if not chunk_text:
            continue

        buffer += chunk_text

        if not in_code_block:
            match = _CODE_BLOCK_OPEN.search(buffer)
            if match:
                pre_text = buffer[: match.start()]
                if pre_text:
                    yield _sse({"type": "text_delta", "content": pre_text})

                in_code_block = True
                code_block_tag = (match.group(1) or "").strip().lower()
                code_buffer = ""
                buffer = buffer[match.end():]

                if code_block_tag in ("python_analysis", "python", ""):
                    yield _sse({"type": "code_start"})
            else:
                safe_len = max(0, len(buffer) - 14)
                if safe_len:
                    yield _sse({"type": "text_delta", "content": buffer[:safe_len]})
                    buffer = buffer[safe_len:]

        if in_code_block:
            close_pos = buffer.find(_CODE_BLOCK_CLOSE)
            if close_pos == -1:
                if code_block_tag in ("python_analysis", "python", ""):
                    yield _sse({"type": "code_delta", "content": buffer})
                code_buffer += buffer
                buffer = ""
            else:
                final_chunk = buffer[:close_pos]
                if code_block_tag in ("python_analysis", "python", ""):
                    if final_chunk:
                        yield _sse({"type": "code_delta", "content": final_chunk})
                    yield _sse({"type": "code_end"})
                code_buffer += final_chunk

                buffer = buffer[close_pos + 3:]
                in_code_block = False

                normalized_tag, normalized_code = _normalize_code_block(
                    code_block_tag, code_buffer
                )

                if normalized_tag == "python_analysis" and is_first_pass:
                    yield from _execute_and_continue(
                        normalized_code,
                        messages,
                        dataframes,
                        model=model,
                        interpretation_instruction=interpretation_instruction,
                    )
                    return
                elif normalized_tag == "chart_spec":
                    yield from _emit_chart_spec(normalized_code)
                else:
                    if code_buffer.strip():
                        yield _sse({"type": "text_delta", "content": f"```{code_block_tag}\n{code_buffer.strip()}\n```"})

                code_buffer = ""
                code_block_tag = ""

    if buffer and not in_code_block:
        yield _sse({"type": "text_delta", "content": buffer})


def _build_execution_summary(exec_result: ExecutionResult) -> str:
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
    pass2_messages = list(original_messages)
    pass2_messages.append({
        "role": "assistant",
        "content": f"```python_analysis\n{code}\n```",
    })
    pass2_messages.append({
        "role": "user",
        "content": (
            f"The code was executed. Here is the result:\n\n"
            f"```\n{execution_summary}\n```\n\n"
            f"Now please write the final answer for the user based on this result. "
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

    execution_summary = _build_execution_summary(exec_result)
    pass2_messages = _build_pass2_messages(
        original_messages,
        code,
        execution_summary,
        interpretation_instruction,
    )
    yield from _stream_pass(
        pass2_messages,
        dataframes,
        is_first_pass=False,
        model=model,
        interpretation_instruction=interpretation_instruction,
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
    attempt = MAX_DEBUG_RETRIES - retries_left + 1
    yield _sse(
        {"type": "debug_start", "attempt": attempt, "max_retries": MAX_DEBUG_RETRIES}
    )

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

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=model,
            messages=debug_messages,
            max_tokens=MAX_TOKENS,
            stream=False,
        )
        full_text = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        yield _sse({"type": "error", "content": f"Debug API call failed: {exc}"})
        return

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
            "content": "The AI was unable to produce a fix for the code. Please rephrase your question.",
        })
        return

    yield _sse({"type": "code_start"})
    yield _sse({"type": "code_delta", "content": fixed_code})
    yield _sse({"type": "code_end"})

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
                    f"Code execution failed after {MAX_DEBUG_RETRIES} debug attempts. "
                    f"Last error: {exec_result.error}"
                ),
            })
        return

    execution_summary = _build_execution_summary(exec_result)
    pass2_messages = _build_pass2_messages(
        original_messages,
        fixed_code,
        execution_summary,
        interpretation_instruction,
    )
    yield from _stream_pass(
        pass2_messages,
        dataframes,
        is_first_pass=False,
        model=model,
        interpretation_instruction=interpretation_instruction,
    )


def _emit_chart_spec(raw_json: str) -> Generator[str, None, None]:
    try:
        data = json.loads(raw_json)
        spec = ChartSpec(**data)
        event: dict[str, Any] = {"type": "chart_spec"}
        event.update(spec.model_dump(exclude_none=True))
        yield _sse(event)
    except Exception:
        pass