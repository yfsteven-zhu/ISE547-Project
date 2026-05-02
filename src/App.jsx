import React, { useRef, useState } from "react";
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

const API_BASE_URL = "https://ise547-project.onrender.com";

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

    if (!inTable) {
      cleaned.push(line);
    }
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

function App() {
  const fileInputRef = useRef(null);

  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content: "Hello! Upload a CSV file and ask a question about your data.",
    },
  ]);

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [fileInfo, setFileInfo] = useState({
    filename: "",
    rows: 0,
    columns: [],
  });

  const [selectedFileIds, setSelectedFileIds] = useState([]);

  const [result, setResult] = useState({
    summary: "No response yet.",
    table: [],
    chartSpec: null,
    code: "",
  });

  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setError("");

    const formData = new FormData();
    formData.append("files", file);

    try {
      const response = await fetch(`${API_BASE_URL}/api/files/upload`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || "File upload failed.");
      }

      const data = await response.json();
      const uploaded = data?.uploaded?.[0];

      if (!uploaded) {
        throw new Error(data?.errors?.[0]?.error || "No uploaded file returned.");
      }

      setSelectedFileIds([uploaded.id]);
      setFileInfo({
        filename: uploaded.filename || "",
        rows: uploaded.row_count || 0,
        columns: (uploaded.columns_info || []).map((col) => col.name),
      });

      setResult({
        summary: `File "${uploaded.filename}" uploaded successfully.`,
        table: uploaded.preview_data || [],
        chartSpec: null,
        code: "",
      });

      setMessages([
        {
          role: "assistant",
          content: `Hello! File "${uploaded.filename}" uploaded successfully. Ask a question about your data.`,
        },
      ]);
    } catch (err) {
      setError(err.message || "Failed to upload file.");
    } finally {
      e.target.value = "";
    }
  };

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
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message: userQuestion,
          file_ids: selectedFileIds,
        }),
      });

      if (!response.ok || !response.body) {
        const text = await response.text().catch(() => "");
        throw new Error(text || "Backend response failed.");
      }

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
            if (Array.isArray(parsed.result_table)) {
              executionTable = parsed.result_table;
            }
            if (parsed.result_scalar !== null && parsed.result_scalar !== undefined) {
              executionScalar = String(parsed.result_scalar);
            }
            if (parsed.error) {
              setError(parsed.error);
            }
          }

          if (parsed.type === "chart_spec") {
            chartSpec = parsed;
          }

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
      if (executionTable.length > 0) {
        tableResult = executionTable;
      } else if (executionScalar) {
        tableResult = [{ result: executionScalar }];
      }

      setResult({
        summary: cleanSummaryText(assistantText) || "No response returned.",
        table: tableResult,
        chartSpec,
        code: generatedCode || "No pandas code returned.",
      });
    } catch (err) {
      const msg = err.message || "Failed to connect to backend.";
      setError(msg);
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = {
          role: "assistant",
          content: msg,
        };
        return updated;
      });
      setResult((prev) => ({
        ...prev,
        summary: cleanSummaryText(msg),
      }));
    } finally {
      setLoading(false);
    }
  };

  const renderChart = () => {
    if (!result.chartSpec || !Array.isArray(result.chartSpec.data)) {
      return <p>No chart result available.</p>;
    }

    const { chart_type, data, x_key, y_key, title } = result.chartSpec;

    if (!data.length) {
      return <p>No chart result available.</p>;
    }

    if (chart_type === "bar") {
      return (
        <div style={{ width: "100%", height: 260 }}>
          <ResponsiveContainer>
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey={x_key} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Bar dataKey={y_key} name={title || y_key} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      );
    }

    if (chart_type === "line") {
      return (
        <div style={{ width: "100%", height: 260 }}>
          <ResponsiveContainer>
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey={x_key} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey={y_key} name={title || y_key} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      );
    }

    if (chart_type === "pie") {
      return (
        <div style={{ width: "100%", height: 260 }}>
          <ResponsiveContainer>
            <PieChart>
              <Tooltip />
              <Legend />
              <Pie data={data} dataKey={y_key} nameKey={x_key} outerRadius={90} label>
                {data.map((_, index) => (
                  <Cell key={index} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
      );
    }

    if (chart_type === "scatter") {
      return (
        <div style={{ width: "100%", height: 260 }}>
          <ResponsiveContainer>
            <ScatterChart>
              <CartesianGrid />
              <XAxis dataKey={x_key} name={x_key} />
              <YAxis dataKey={y_key} name={y_key} />
              <Tooltip cursor={{ strokeDasharray: "3 3" }} />
              <Scatter data={data} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      );
    }

    return <p>No chart result available.</p>;
  };

  return (
    <div className="app">
      <aside className="left-panel">
        <div className="panel-header">
          <h2>Chat with Your Data</h2>
          <p>Ask natural language questions about your CSV</p>
        </div>

        <div className="upload-box">
          <button
            type="button"
            className="upload-btn"
            onClick={() => fileInputRef.current?.click()}
          >
            Choose CSV File
          </button>

          <input
            ref={fileInputRef}
            type="file"
            accept=".csv"
            style={{ display: "none" }}
            onChange={handleFileUpload}
          />

          <p className="upload-note">
            Current file: {fileInfo.filename || "No file selected"}
          </p>
        </div>

        <div className="chat-box">
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
              <p>Analyzing your data...</p>
            </div>
          )}
        </div>

        <div className="input-box">
          <input
            type="text"
            placeholder="Ask a question..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
          />
          <button onClick={handleSend} disabled={loading}>
            {loading ? "Loading..." : "Send"}
          </button>
        </div>

        {error && <p className="error-text">{error}</p>}
      </aside>

      <main className="center-panel">
        <div className="result-card">
          <h2>Analysis Result</h2>

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
            {result.table.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    {Object.keys(result.table[0]).map((key) => (
                      <th key={key}>{key}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.table.map((row, index) => (
                    <tr key={index}>
                      {Object.values(row).map((value, i) => (
                        <td key={i}>{value === null ? "" : String(value)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p>No table result available.</p>
            )}
          </section>

          <section className="card-section">
            <h3>Chart Result</h3>
            {renderChart()}
          </section>

          <section className="card-section">
            <h3>Generated Pandas Code</h3>
            {result.code ? <pre>{result.code}</pre> : <p>No code returned.</p>}
          </section>
        </div>
      </main>

      <aside className="right-panel">
        <div className="info-card">
          <h3>Dataset Info</h3>
          <p>
            <strong>Filename:</strong> {fileInfo.filename || "No file uploaded"}
          </p>
          <p>
            <strong>Rows:</strong> {fileInfo.rows}
          </p>
          <p>
            <strong>Columns:</strong> {fileInfo.columns.length}
          </p>
        </div>

        <div className="info-card">
          <h3>Columns</h3>
          {fileInfo.columns.length > 0 ? (
            <ul>
              {fileInfo.columns.map((col, index) => (
                <li key={index}>{col}</li>
              ))}
            </ul>
          ) : (
            <p>No columns available.</p>
          )}
        </div>

        <div className="info-card">
          <h3>Suggested Questions</h3>
          <button
            className="suggest-btn"
            onClick={() => setInput("What are the column names?")}
          >
            What are the column names?
          </button>
          <button
            className="suggest-btn"
            onClick={() => setInput("Show the first 5 rows.")}
          >
            Show the first 5 rows.
          </button>
          <button
            className="suggest-btn"
            onClick={() => setInput("Show summary statistics.")}
          >
            Show summary statistics.
          </button>
        </div>
      </aside>
    </div>
  );
}

export default App;