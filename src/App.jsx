import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./index.css";
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const PIE_COLORS = [
  "#60a5fa",
  "#34d399",
  "#fbbf24",
  "#f87171",
  "#a78bfa",
  "#f472b6",
  "#22d3ee",
  "#fb923c",
];

function stripMarkdownTables(text) {
  if (!text) return "";
  const lines = text.split("\n");
  const cleaned = [];
  let inTable = false;
  for (const line of lines) {
    const trimmed = line.trim();
    const isTableRow = /^\|.*\|$/.test(trimmed);
    const isTableDivider = /^\|\s*[-:]+\s*(\|\s*[-:]+\s*)+\|?$/.test(trimmed);
    if (isTableRow || isTableDivider) {
      inTable = true;
      continue;
    }
    if (inTable && trimmed === "") {
      inTable = false;
      continue;
    }
    if (!inTable) cleaned.push(line);
  }
  return cleaned.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

function stripCodeFences(text) {
  if (!text) return "";
  return text.replace(/```[\s\S]*?```/g, "").trim();
}

function cleanSummaryText(text) {
  return stripCodeFences(stripMarkdownTables(text));
}

function formatBytes(n) {
  if (!Number.isFinite(n)) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

// Render a Heatmap manually (Recharts has no built-in heatmap)
function Heatmap({ data, xLabel, yLabel }) {
  if (!Array.isArray(data) || data.length === 0) return null;

  const rows = Array.from(new Set(data.map((d) => String(d.row))));
  const cols = Array.from(new Set(data.map((d) => String(d.col))));
  const values = data.map((d) => Number(d.value)).filter(Number.isFinite);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  const range = max - min || 1;

  const lookup = new Map();
  for (const d of data) lookup.set(`${d.row}::${d.col}`, Number(d.value));

  const colorFor = (v) => {
    if (!Number.isFinite(v)) return "#1f2937";
    const t = (v - min) / range;
    const r = Math.round(15 + t * 240);
    const g = Math.round(50 + (1 - Math.abs(t - 0.5) * 2) * 180);
    const b = Math.round(255 - t * 220);
    return `rgb(${r}, ${g}, ${b})`;
  };

  return (
    <div className="heatmap-wrap">
      {yLabel && <div className="heatmap-y-label">{yLabel}</div>}
      <div className="heatmap-table-wrap">
        <table className="heatmap-table">
          <thead>
            <tr>
              <th></th>
              {cols.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r}>
                <th>{r}</th>
                {cols.map((c) => {
                  const v = lookup.get(`${r}::${c}`);
                  return (
                    <td
                      key={c}
                      title={`${r} × ${c}: ${Number.isFinite(v) ? v : "—"}`}
                      style={{ background: colorFor(v) }}
                    >
                      {Number.isFinite(v) ? v.toFixed(2) : ""}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {xLabel && <div className="heatmap-x-label">{xLabel}</div>}
      </div>
    </div>
  );
}

function App() {
  const [files, setFiles] = useState([]); // CsvFileListItem[]
  const [activeFileId, setActiveFileId] = useState(null);
  const [activeFileDetail, setActiveFileDetail] = useState(null);
  const [editingDescriptionId, setEditingDescriptionId] = useState(null);
  const [descriptionDraft, setDescriptionDraft] = useState("");

  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "Hello! Upload one or more CSV files and ask a question about your data.",
    },
  ]);

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [models, setModels] = useState([]);
  const [prompts, setPrompts] = useState([]);
  const [selectedModelId, setSelectedModelId] = useState("nemotron");
  const [selectedPromptId, setSelectedPromptId] = useState("baseline");

  const [suggestions, setSuggestions] = useState([]);

  const [result, setResult] = useState({
    summary: "No response yet.",
    table: [],
    chartSpec: null,
    code: "",
  });

  const [codeCollapsed, setCodeCollapsed] = useState(true);
  const [tableSort, setTableSort] = useState({ key: null, dir: "asc" });
  const [copyState, setCopyState] = useState("idle"); // idle | done

  const chartContainerRef = useRef(null);
  const chatBoxRef = useRef(null);
  const fileInputRef = useRef(null);

  // ---- Initial bootstrap: models, prompts, files, history ----
  useEffect(() => {
    (async () => {
      try {
        const [mRes, pRes, fRes, hRes] = await Promise.all([
          fetch(`${API_BASE_URL}/api/chat/models`),
          fetch(`${API_BASE_URL}/api/chat/prompts`),
          fetch(`${API_BASE_URL}/api/files`),
          fetch(`${API_BASE_URL}/api/chat/history`),
        ]);
        if (mRes.ok) {
          const d = await mRes.json();
          setModels(d.models || []);
        }
        if (pRes.ok) {
          const d = await pRes.json();
          setPrompts(d.prompts || []);
        }
        if (fRes.ok) {
          const d = await fRes.json();
          setFiles(d || []);
          if (d && d.length > 0) setActiveFileId(d[0].id);
        }
        if (hRes.ok) {
          const d = await hRes.json();
          if (Array.isArray(d) && d.length > 0) {
            setMessages(
              d.map((m) => ({ role: m.role, content: m.content }))
            );
          }
        }
      } catch (err) {
        setError(`Cannot reach backend at ${API_BASE_URL}: ${err.message}`);
      }
    })();
  }, []);

  // ---- Load active file's full detail when selection changes ----
  useEffect(() => {
    if (!activeFileId) {
      setActiveFileDetail(null);
      return;
    }
    (async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/files/${activeFileId}`);
        if (res.ok) {
          const detail = await res.json();
          setActiveFileDetail(detail);
        }
      } catch {
        /* ignore */
      }
    })();
  }, [activeFileId]);

  // ---- Refresh suggestions whenever the file set changes ----
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/chat/suggestions`);
        if (res.ok) {
          const d = await res.json();
          setSuggestions(d.suggestions || []);
        }
      } catch {
        /* ignore */
      }
    })();
  }, [files]);

  // ---- Auto-scroll the chat box on new messages ----
  useEffect(() => {
    if (chatBoxRef.current) {
      chatBoxRef.current.scrollTop = chatBoxRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const refreshFiles = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/files`);
      if (res.ok) {
        const d = await res.json();
        setFiles(d || []);
        if (d.length === 0) setActiveFileId(null);
        else if (!d.some((f) => f.id === activeFileId)) setActiveFileId(d[0].id);
      }
    } catch {
      /* ignore */
    }
  };

  // ---- Multi-file upload ----
  const handleFileUpload = async (e) => {
    const list = Array.from(e.target.files || []);
    if (list.length === 0) return;
    setError("");

    const formData = new FormData();
    for (const f of list) formData.append("files", f);

    try {
      const res = await fetch(`${API_BASE_URL}/api/files/upload`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error("File upload failed.");
      const data = await res.json();

      const errs = (data.errors || [])
        .map((er) => `${er.filename}: ${er.error}`)
        .join("; ");
      if (errs) setError(errs);

      await refreshFiles();
      if (data.uploaded?.[0]) setActiveFileId(data.uploaded[0].id);
    } catch (err) {
      setError(err.message || "Failed to upload file(s).");
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDeleteFile = async (fileId) => {
    setError("");
    try {
      const res = await fetch(`${API_BASE_URL}/api/files/${fileId}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) throw new Error("Delete failed.");
      await refreshFiles();
    } catch (err) {
      setError(err.message || "Failed to delete file.");
    }
  };

  const handleSaveDescription = async (fileId) => {
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/files/${fileId}/description`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description: descriptionDraft }),
        }
      );
      if (!res.ok) throw new Error("Failed to update description.");
      setEditingDescriptionId(null);
      setDescriptionDraft("");
      await refreshFiles();
    } catch (err) {
      setError(err.message || "Failed to save description.");
    }
  };

  const handleClearChat = async () => {
    try {
      await fetch(`${API_BASE_URL}/api/chat/history`, { method: "DELETE" });
    } catch {
      /* ignore */
    }
    setMessages([
      {
        role: "assistant",
        content: "Conversation cleared. Ask a new question.",
      },
    ]);
    setResult({
      summary: "No response yet.",
      table: [],
      chartSpec: null,
      code: "",
    });
    setError("");
  };

  // ---- Send a question ----
  const handleSend = async () => {
    if (!input.trim() || loading) return;
    const userQuestion = input;
    setError("");
    setLoading(true);

    setMessages((prev) => [...prev, { role: "user", content: userQuestion }]);
    setInput("");

    let assistantText = "";
    let generatedCode = "";
    let executionTable = [];
    let executionScalar = "";
    let chartSpec = null;

    setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

    try {
      const response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: userQuestion,
          file_ids: files.map((f) => f.id),
          model_id: selectedModelId,
          prompt_id: selectedPromptId,
        }),
      });

      if (!response.ok || !response.body)
        throw new Error("Backend response failed.");

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";

        for (const event of events) {
          const line = event.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          const jsonStr = line.replace("data: ", "").trim();
          if (!jsonStr) continue;

          let parsed;
          try {
            parsed = JSON.parse(jsonStr);
          } catch {
            continue;
          }

          if (parsed.type === "text_delta") {
            assistantText += parsed.content || "";
            setMessages((prev) => {
              const updated = [...prev];
              updated[updated.length - 1] = {
                role: "assistant",
                content: assistantText,
              };
              return updated;
            });
          }
          if (parsed.type === "code_delta") {
            generatedCode += parsed.content || "";
          }
          if (parsed.type === "execution_result") {
            if (Array.isArray(parsed.result_table))
              executionTable = parsed.result_table;
            if (
              parsed.result_scalar !== null &&
              parsed.result_scalar !== undefined
            )
              executionScalar = String(parsed.result_scalar);
            if (parsed.error) setError(parsed.error);
          }
          if (parsed.type === "chart_spec") chartSpec = parsed;
          if (parsed.type === "error") {
            const errorText = parsed.content || "Backend error.";
            assistantText = errorText;
            setError(errorText);
            setMessages((prev) => {
              const updated = [...prev];
              updated[updated.length - 1] = {
                role: "assistant",
                content: errorText,
              };
              return updated;
            });
          }
        }
      }

      let tableResult = [];
      if (executionTable.length > 0) tableResult = executionTable;
      else if (executionScalar) tableResult = [{ result: executionScalar }];

      setResult({
        summary: cleanSummaryText(assistantText) || "No response returned.",
        table: tableResult,
        chartSpec,
        code: generatedCode,
      });
      setTableSort({ key: null, dir: "asc" });
    } catch (err) {
      const msg = err.message || "Failed to connect to backend.";
      setError(msg);
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: "assistant", content: msg };
        return updated;
      });
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleCopyResponse = async () => {
    try {
      const last = [...messages].reverse().find((m) => m.role === "assistant");
      const text = last?.content || result.summary || "";
      await navigator.clipboard.writeText(text);
      setCopyState("done");
      setTimeout(() => setCopyState("idle"), 1500);
    } catch {
      setError("Clipboard write failed.");
    }
  };

  // ---- Chart PNG download (works for SVG-based Recharts charts) ----
  const handleDownloadChart = () => {
    const wrap = chartContainerRef.current;
    if (!wrap) return;
    const svg = wrap.querySelector("svg");
    if (!svg) {
      setError("Chart download not supported for this chart type yet.");
      return;
    }
    const serialized = new XMLSerializer().serializeToString(svg);
    const svgBlob = new Blob([serialized], {
      type: "image/svg+xml;charset=utf-8",
    });
    const url = URL.createObjectURL(svgBlob);
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      const w = svg.viewBox?.baseVal?.width || svg.clientWidth || 800;
      const h = svg.viewBox?.baseVal?.height || svg.clientHeight || 400;
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#0b1220";
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url);
      canvas.toBlob((blob) => {
        if (!blob) return;
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `chart-${Date.now()}.png`;
        a.click();
        URL.revokeObjectURL(a.href);
      }, "image/png");
    };
    img.onerror = () => setError("Chart export failed.");
    img.src = url;
  };

  // ---- Sortable result table ----
  const sortedTable = useMemo(() => {
    if (!tableSort.key) return result.table;
    const arr = [...result.table];
    arr.sort((a, b) => {
      const va = a[tableSort.key];
      const vb = b[tableSort.key];
      if (va === vb) return 0;
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      const numA = Number(va);
      const numB = Number(vb);
      const cmp =
        Number.isFinite(numA) && Number.isFinite(numB)
          ? numA - numB
          : String(va).localeCompare(String(vb));
      return tableSort.dir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [result.table, tableSort]);

  const toggleSort = (key) => {
    setTableSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "asc" }
    );
  };

  // ---- Chart renderer ----
  const renderChart = () => {
    if (!result.chartSpec || !Array.isArray(result.chartSpec.data))
      return <p>No chart result available.</p>;

    const { chart_type, data, x_key, y_key, title, x_label, y_label } =
      result.chartSpec;
    if (!data.length) return <p>No chart result available.</p>;

    if (chart_type === "bar") {
      return (
        <div ref={chartContainerRef} style={{ width: "100%", height: 300 }}>
          <ResponsiveContainer>
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey={x_key} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Bar dataKey={y_key} name={title || y_key} fill="#60a5fa" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      );
    }
    if (chart_type === "line") {
      return (
        <div ref={chartContainerRef} style={{ width: "100%", height: 300 }}>
          <ResponsiveContainer>
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey={x_key} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line
                type="monotone"
                dataKey={y_key}
                name={title || y_key}
                stroke="#34d399"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      );
    }
    if (chart_type === "pie") {
      return (
        <div ref={chartContainerRef} style={{ width: "100%", height: 300 }}>
          <ResponsiveContainer>
            <PieChart>
              <Tooltip />
              <Legend />
              <Pie
                data={data}
                dataKey={y_key || "value"}
                nameKey={x_key || "name"}
                outerRadius={100}
                label
              >
                {data.map((_, index) => (
                  <Cell
                    key={index}
                    fill={PIE_COLORS[index % PIE_COLORS.length]}
                  />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
      );
    }
    if (chart_type === "scatter") {
      return (
        <div ref={chartContainerRef} style={{ width: "100%", height: 300 }}>
          <ResponsiveContainer>
            <ScatterChart>
              <CartesianGrid />
              <XAxis dataKey={x_key || "x"} name={x_key || "x"} />
              <YAxis dataKey={y_key || "y"} name={y_key || "y"} />
              <Tooltip cursor={{ strokeDasharray: "3 3" }} />
              <Scatter data={data} fill="#a78bfa" />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      );
    }
    if (chart_type === "heatmap") {
      return (
        <div ref={chartContainerRef}>
          <Heatmap data={data} xLabel={x_label} yLabel={y_label} />
        </div>
      );
    }
    return <p>Unsupported chart type: {chart_type}</p>;
  };

  const totalRows = files.reduce((s, f) => s + (f.row_count || 0), 0);
  const activePrompt = prompts.find((p) => p.id === selectedPromptId);

  return (
    <div className="app">
      {/* ---------------- LEFT PANEL: files + chat ---------------- */}
      <aside className="left-panel">
        <div className="panel-header">
          <h2>Chat with Your Data</h2>
          <p>Upload CSVs and ask natural-language questions.</p>
        </div>

        <div className="upload-box">
          <label htmlFor="csv-upload" className="upload-btn">
            + Upload CSV files
          </label>
          <input
            ref={fileInputRef}
            id="csv-upload"
            type="file"
            accept=".csv"
            multiple
            style={{ display: "none" }}
            onChange={handleFileUpload}
          />
          <p className="upload-note">
            {files.length === 0
              ? "Per file ≤ 50 MB · Total ≤ 200 MB"
              : `${files.length} file${files.length > 1 ? "s" : ""} · ${totalRows.toLocaleString()} rows total`}
          </p>
        </div>

        <div className="file-list">
          {files.length === 0 ? (
            <p className="muted-text">No files uploaded yet.</p>
          ) : (
            files.map((f) => (
              <div
                key={f.id}
                className={`file-item ${activeFileId === f.id ? "active" : ""}`}
                onClick={() => setActiveFileId(f.id)}
              >
                <div className="file-row">
                  <span className="file-name" title={f.filename}>
                    {f.filename}
                  </span>
                  <button
                    className="icon-btn danger"
                    title="Delete file"
                    onClick={(ev) => {
                      ev.stopPropagation();
                      handleDeleteFile(f.id);
                    }}
                  >
                    ✕
                  </button>
                </div>
                <div className="file-meta">
                  {f.row_count.toLocaleString()} rows · {f.column_count} cols ·{" "}
                  {formatBytes(f.file_size)} · {f.encoding}
                </div>
                {editingDescriptionId === f.id ? (
                  <div
                    className="desc-editor"
                    onClick={(ev) => ev.stopPropagation()}
                  >
                    <textarea
                      rows={2}
                      value={descriptionDraft}
                      placeholder="Describe this file (helps the AI)…"
                      onChange={(ev) => setDescriptionDraft(ev.target.value)}
                    />
                    <div className="desc-actions">
                      <button
                        className="mini-btn primary"
                        onClick={() => handleSaveDescription(f.id)}
                      >
                        Save
                      </button>
                      <button
                        className="mini-btn"
                        onClick={() => {
                          setEditingDescriptionId(null);
                          setDescriptionDraft("");
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="file-desc-row">
                    <span className="file-desc">
                      {f.description || <em className="muted-text">No description</em>}
                    </span>
                    <button
                      className="mini-btn"
                      onClick={(ev) => {
                        ev.stopPropagation();
                        setEditingDescriptionId(f.id);
                        setDescriptionDraft(f.description || "");
                      }}
                    >
                      Edit
                    </button>
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        <div className="selectors">
          <label className="selector">
            <span className="selector-label">Model</span>
            <select
              value={selectedModelId}
              onChange={(e) => setSelectedModelId(e.target.value)}
            >
              {models.length === 0 ? (
                <option value="nemotron">Nemotron 3 Super</option>
              ) : (
                models.map((m) => (
                  <option key={m.id} value={m.id} title={m.description}>
                    {m.name}
                  </option>
                ))
              )}
            </select>
          </label>

          <label className="selector">
            <span className="selector-label">
              Style{" "}
              {activePrompt?.description && (
                <span
                  className="info-badge"
                  title={activePrompt.description}
                >
                  ⓘ
                </span>
              )}
            </span>
            <select
              value={selectedPromptId}
              onChange={(e) => setSelectedPromptId(e.target.value)}
            >
              {prompts.length === 0 ? (
                <option value="baseline">Business Assistant</option>
              ) : (
                prompts.map((p) => (
                  <option key={p.id} value={p.id} title={p.description}>
                    {p.name}
                  </option>
                ))
              )}
            </select>
          </label>
        </div>

        <div className="chat-header">
          <span className="chat-title">Conversation</span>
          <button
            className="mini-btn"
            onClick={handleClearChat}
            disabled={loading}
          >
            Clear
          </button>
        </div>

        <div className="chat-box" ref={chatBoxRef}>
          {messages.map((msg, index) => (
            <div
              key={index}
              className={`message ${
                msg.role === "user" ? "user-message" : "assistant-message"
              }`}
            >
              <strong>{msg.role === "user" ? "You" : "AI"}:</strong>
              <div className="markdown-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {msg.content}
                </ReactMarkdown>
              </div>
            </div>
          ))}
          {loading && (
            <div className="message assistant-message">
              <strong>AI:</strong>
              <p>Analyzing your data…</p>
            </div>
          )}
        </div>

        <div className="input-box">
          <textarea
            placeholder="Ask a question (Enter to send, Shift+Enter for newline)…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
          />
          <button onClick={handleSend} disabled={loading}>
            {loading ? "…" : "Send"}
          </button>
        </div>

        {error && <p className="error-text">{error}</p>}
      </aside>

      {/* ---------------- CENTER PANEL: result ---------------- */}
      <main className="center-panel">
        <div className="result-card">
          <div className="result-header">
            <h2>Analysis Result</h2>
            <button
              className="mini-btn"
              onClick={handleCopyResponse}
              disabled={!result.summary || result.summary === "No response yet."}
            >
              {copyState === "done" ? "Copied ✓" : "Copy response"}
            </button>
          </div>

          <section className="card-section">
            <h3>Summary</h3>
            <div className="markdown-content summary-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {result.summary}
              </ReactMarkdown>
            </div>
          </section>

          <section className="card-section">
            <h3>Table Result</h3>
            {sortedTable.length > 0 ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      {Object.keys(sortedTable[0]).map((key) => (
                        <th
                          key={key}
                          onClick={() => toggleSort(key)}
                          className="sortable"
                        >
                          {key}
                          {tableSort.key === key
                            ? tableSort.dir === "asc"
                              ? " ▲"
                              : " ▼"
                            : ""}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedTable.map((row, index) => (
                      <tr key={index}>
                        {Object.values(row).map((value, i) => (
                          <td key={i}>
                            {value === null || value === undefined
                              ? ""
                              : String(value)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p>No table result available.</p>
            )}
          </section>

          <section className="card-section">
            <div className="section-header">
              <h3>Chart Result</h3>
              {result.chartSpec && (
                <button className="mini-btn" onClick={handleDownloadChart}>
                  Download PNG
                </button>
              )}
            </div>
            {renderChart()}
          </section>

          <section className="card-section">
            <div className="section-header">
              <h3>Generated Pandas Code</h3>
              {result.code && (
                <button
                  className="mini-btn"
                  onClick={() => setCodeCollapsed((v) => !v)}
                >
                  {codeCollapsed ? "Show" : "Hide"}
                </button>
              )}
            </div>
            {result.code ? (
              codeCollapsed ? (
                <p className="muted-text">Code hidden. Click Show to reveal.</p>
              ) : (
                <pre>{result.code}</pre>
              )
            ) : (
              <p>No pandas code returned for this answer.</p>
            )}
          </section>
        </div>
      </main>

      {/* ---------------- RIGHT PANEL: dataset detail + suggestions ---------------- */}
      <aside className="right-panel">
        <div className="info-card">
          <h3>Dataset Info</h3>
          {activeFileDetail ? (
            <>
              <p>
                <strong>File:</strong> {activeFileDetail.filename}
              </p>
              <p>
                <strong>Rows:</strong>{" "}
                {activeFileDetail.row_count.toLocaleString()}
              </p>
              <p>
                <strong>Columns:</strong> {activeFileDetail.column_count}
              </p>
              <p>
                <strong>Encoding:</strong> {activeFileDetail.encoding}
              </p>
              {activeFileDetail.description && (
                <p>
                  <strong>Description:</strong> {activeFileDetail.description}
                </p>
              )}
            </>
          ) : (
            <p className="muted-text">No file selected.</p>
          )}
        </div>

        {activeFileDetail && (
          <div className="info-card">
            <h3>Columns</h3>
            <ul className="columns-list">
              {activeFileDetail.columns_info.map((col) => (
                <li key={col.name}>
                  <div className="col-name">
                    {col.name}{" "}
                    <span className="col-type">{col.dtype}</span>
                  </div>
                  <div className="col-meta">
                    Non-null {(col.non_null_rate * 100).toFixed(1)}% · samples:{" "}
                    <span className="muted-text">
                      {col.sample_values.slice(0, 3).join(", ") || "—"}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {activeFileDetail && activeFileDetail.preview_data?.length > 0 && (
          <div className="info-card">
            <h3>Data Preview (first 10 rows)</h3>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    {activeFileDetail.columns_info.map((c) => (
                      <th key={c.name}>{c.name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {activeFileDetail.preview_data.map((row, i) => (
                    <tr key={i}>
                      {activeFileDetail.columns_info.map((c) => (
                        <td key={c.name}>
                          {row[c.name] === null || row[c.name] === undefined
                            ? ""
                            : String(row[c.name])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="info-card">
          <h3>Suggested Questions</h3>
          {suggestions.length === 0 ? (
            <p className="muted-text">Upload a file to see suggestions.</p>
          ) : (
            suggestions.map((q, i) => (
              <button
                key={i}
                className="suggest-btn"
                onClick={() => setInput(q)}
              >
                {q}
              </button>
            ))
          )}
        </div>
      </aside>
    </div>
  );
}

export default App;
