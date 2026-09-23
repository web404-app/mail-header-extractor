import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Row = Record<string, string>;

type Mailbox = {
  email: string;
  password: string;
  provider: string;
  host: string;
  port: number;
  ssl: boolean;
};

const API = "/api";

const FIELDS = [
  ["from", "From"],
  ["sender", "Sender"],
  ["subject", "Subject"],
  ["to", "To"],
  ["cc", "Cc"],
  ["date", "Date"],
  ["message_id", "Message-ID"],
  ["return_path", "Return-Path"],
  ["content_type", "Content-Type"],
  ["reply_to", "Reply-To"],
  ["client_ip", "Client-IP"],
  ["received", "Received"],
  ["authentication_results", "Authentication-Results"],
  ["dkim", "DKIM"],
  ["spf", "SPF"],
  ["dmarc", "DMARC"]
] as const;

const DEFAULT_FIELDS = [
  "from",
  "subject",
  "to",
  "date",
  "message_id",
  "return_path",
  "client_ip",
  "dkim",
  "spf",
  "dmarc"
];

function App() {
  const [accessPassword, setAccessPassword] = useState("");
  const [appPasswordRequired, setAppPasswordRequired] = useState(false);

  const [mailbox, setMailbox] = useState<Mailbox>({
    email: "",
    password: "",
    provider: "auto",
    host: "",
    port: 993,
    ssl: true
  });

  const [folders, setFolders] = useState<string[]>([]);
  const [folder, setFolder] = useState("INBOX");
  const [limit, setLimit] = useState(50);
  const [selectedFields, setSelectedFields] = useState<string[]>(DEFAULT_FIELDS);
  const [newestFirst, setNewestFirst] = useState(true);

  const [rows, setRows] = useState<Row[]>([]);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("Ready.");
  const [busy, setBusy] = useState(false);
  const [dark, setDark] = useState(true);
  const [apiOk, setApiOk] = useState<boolean | null>(null);

  useEffect(() => {
    fetch(`${API}/health`)
      .then(async (response) => {
        if (!response.ok) throw new Error("API unavailable");
        return response.json();
      })
      .then((data) => {
        setApiOk(data?.ok === true);
      })
      .catch(() => setApiOk(false));

    fetch(`${API}/config`)
      .then((response) => response.json())
      .then((data) => {
        setAppPasswordRequired(Boolean(data?.app_password_required));
      })
      .catch(() => {});
  }, []);

  const visibleRows = useMemo(() => {
    const search = query.trim().toLowerCase();

    if (!search) return rows;

    return rows.filter((row) =>
      Object.values(row).some((value) =>
        String(value || "").toLowerCase().includes(search)
      )
    );
  }, [rows, query]);

  function updateMailbox<K extends keyof Mailbox>(
    key: K,
    value: Mailbox[K]
  ) {
    setMailbox((current) => ({
      ...current,
      [key]: value
    }));
  }

  function headers() {
    return accessPassword
      ? { "X-App-Password": accessPassword }
      : {};
  }

  async function api(path: string, options: RequestInit = {}) {
    const response = await fetch(API + path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...headers(),
        ...(options.headers || {})
      }
    });

    const text = await response.text();

    let data: any = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { detail: text };
    }

    if (!response.ok) {
      throw new Error(data.detail || `HTTP ${response.status}`);
    }

    return data;
  }

  async function testConnection() {
    if (!mailbox.email.trim()) {
      setMessage("Enter the mailbox email first.");
      return;
    }

    if (!mailbox.password) {
      setMessage("Enter the mailbox app password first.");
      return;
    }

    setBusy(true);
    setMessage("Connecting to IMAP...");

    try {
      const connection = await api("/test-connection", {
        method: "POST",
        body: JSON.stringify(mailbox)
      });

      const folderData = await api("/folders", {
        method: "POST",
        body: JSON.stringify(mailbox)
      });

      const loadedFolders = folderData.folders || [];
      setFolders(loadedFolders);

      if (loadedFolders.includes("INBOX")) {
        setFolder("INBOX");
      } else if (loadedFolders.length) {
        setFolder(loadedFolders[0]);
      }

      setMessage(
        `Connected successfully — ${connection.provider} / ${connection.host}`
      );
    } catch (error: any) {
      setMessage(error?.message || "Connection failed.");
    } finally {
      setBusy(false);
    }
  }

  async function extract() {
    if (!mailbox.email.trim() || !mailbox.password) {
      setMessage("Enter the mailbox email and app password.");
      return;
    }

    if (!selectedFields.length) {
      setMessage("Select at least one header field.");
      return;
    }

    setBusy(true);
    setRows([]);
    setMessage(`Extracting up to ${limit} message headers...`);

    try {
      const result = await api("/extract", {
        method: "POST",
        body: JSON.stringify({
          ...mailbox,
          folder,
          limit,
          fields: selectedFields,
          newest_first: newestFirst
        })
      });

      setRows(result.rows || []);
      setMessage(
        `Finished — ${result.extracted || 0} messages extracted.`
      );
    } catch (error: any) {
      setMessage(error?.message || "Extraction failed.");
    } finally {
      setBusy(false);
    }
  }

  async function exportResults(format: string) {
    if (!rows.length) {
      setMessage("No results to export.");
      return;
    }

    try {
      const response = await fetch(`${API}/export`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...headers()
        },
        body: JSON.stringify({
          format,
          fields: selectedFields,
          rows
        })
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Export failed.");
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");

      anchor.href = url;
      anchor.download = `mail_headers.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);

      setMessage(`${format.toUpperCase()} exported successfully.`);
    } catch (error: any) {
      setMessage(error?.message || "Export failed.");
    }
  }

  function toggleField(field: string, checked: boolean) {
    setSelectedFields((current) => {
      if (checked) {
        return current.includes(field) ? current : [...current, field];
      }

      return current.filter((item) => item !== field);
    });
  }

  return (
    <div className={dark ? "app dark" : "app"}>
      <header>
        <div>
          <div className="badge">V4.2 • VERCEL • NO DATABASE</div>
          <h1>Mail Header Extractor</h1>
          <p>
            Extract email headers through IMAP without storing mailbox
            credentials.
          </p>
        </div>

        <div className="header-actions">
          <div className={`api-pill ${apiOk === true ? "ok" : apiOk === false ? "bad" : ""}`}>
            <span />
            {apiOk === true ? "API online" : apiOk === false ? "API offline" : "Checking API"}
          </div>

          <button className="theme" onClick={() => setDark(!dark)}>
            {dark ? "☀ Light" : "☾ Dark"}
          </button>
        </div>
      </header>

      <main>
        {appPasswordRequired && (
          <section className="security">
            <div>
              <b>Private app access enabled</b>
              <span>Enter the access password configured in Vercel.</span>
            </div>
            <input
              type="password"
              value={accessPassword}
              onChange={(event) => setAccessPassword(event.target.value)}
              placeholder="App access password"
            />
          </section>
        )}

        <section className="card">
          <div className="title">
            <b>01</b>
            <div>
              <h2>Mailbox</h2>
              <p>
                Gmail, Outlook, Yahoo and iCloud use their provider IMAP
                presets.
              </p>
            </div>
          </div>

          <div className="grid">
            <label>
              Email
              <input
                value={mailbox.email}
                onChange={(event) =>
                  updateMailbox("email", event.target.value)
                }
                placeholder="you@example.com"
                autoComplete="off"
              />
            </label>

            <label>
              App Password
              <input
                type="password"
                value={mailbox.password}
                onChange={(event) =>
                  updateMailbox("password", event.target.value)
                }
                placeholder="••••••••••••••••"
                autoComplete="off"
              />
            </label>

            <label>
              Provider
              <select
                value={mailbox.provider}
                onChange={(event) =>
                  updateMailbox("provider", event.target.value)
                }
              >
                <option value="auto">Auto detect</option>
                <option value="gmail">Gmail</option>
                <option value="outlook">Outlook / Microsoft 365</option>
                <option value="yahoo">Yahoo</option>
                <option value="icloud">iCloud</option>
                <option value="custom">Custom IMAP</option>
              </select>
            </label>

            <label>
              Custom IMAP Host
              <input
                value={mailbox.host}
                onChange={(event) =>
                  updateMailbox("host", event.target.value)
                }
                placeholder="Leave empty for Gmail / Outlook / Yahoo / iCloud"
              />
            </label>
          </div>

          <button
            className="primary"
            disabled={busy}
            onClick={testConnection}
          >
            {busy ? "Connecting..." : "Test Connection & Load Folders"}
          </button>

          <div className="hint">
            <strong>Gmail:</strong> choose Gmail and leave Custom IMAP Host
            empty. Use a Google App Password, not your normal Gmail password.
          </div>
        </section>

        <section className="card">
          <div className="title">
            <b>02</b>
            <div>
              <h2>Extraction</h2>
              <p>Maximum 200 messages per extraction request.</p>
            </div>
          </div>

          <div className="grid three">
            <label>
              Folder
              <select
                value={folder}
                onChange={(event) => setFolder(event.target.value)}
              >
                {folders.length ? (
                  folders.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))
                ) : (
                  <option value="INBOX">INBOX</option>
                )}
              </select>
            </label>

            <label>
              Messages
              <input
                type="number"
                min="1"
                max="200"
                value={limit}
                onChange={(event) => {
                  const value = Number(event.target.value);
                  setLimit(Math.min(200, Math.max(1, value || 1)));
                }}
              />
            </label>

            <label className="check">
              <input
                type="checkbox"
                checked={newestFirst}
                onChange={(event) => setNewestFirst(event.target.checked)}
              />
              Newest first
            </label>
          </div>

          <div className="fh">
            <div>
              <h3>Header Fields</h3>
              <small>{selectedFields.length} selected</small>
            </div>

            <div>
              <button onClick={() => setSelectedFields(FIELDS.map((x) => x[0]))}>
                All
              </button>
              <button onClick={() => setSelectedFields([])}>
                None
              </button>
            </div>
          </div>

          <div className="fields">
            {FIELDS.map(([id, label]) => (
              <label className="field" key={id}>
                <input
                  type="checkbox"
                  checked={selectedFields.includes(id)}
                  onChange={(event) =>
                    toggleField(id, event.target.checked)
                  }
                />
                {label}
              </label>
            ))}
          </div>

          <div className="status">
            <span>{message}</span>
            {busy && <i />}
          </div>

          <button
            className="primary"
            disabled={busy}
            onClick={extract}
          >
            {busy ? "Extracting..." : "START EXTRACTION"}
          </button>
        </section>

        <section className="card">
          <div className="title">
            <b>03</b>
            <div>
              <h2>Results</h2>
              <p>
                {rows.length} extracted · {visibleRows.length} visible
              </p>
            </div>
          </div>

          <div className="toolbar">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search extracted results..."
            />

            <div className="export-buttons">
              {["csv", "xlsx", "json", "txt"].map((format) => (
                <button
                  key={format}
                  onClick={() => exportResults(format)}
                >
                  {format.toUpperCase()}
                </button>
              ))}
            </div>
          </div>

          <div className="table">
            {visibleRows.length ? (
              <table>
                <thead>
                  <tr>
                    {selectedFields.map((field) => (
                      <th key={field}>
                        {FIELDS.find((item) => item[0] === field)?.[1]}
                      </th>
                    ))}
                  </tr>
                </thead>

                <tbody>
                  {visibleRows.map((row, index) => (
                    <tr key={index}>
                      {selectedFields.map((field) => (
                        <td
                          key={field}
                          title={row[field] || ""}
                        >
                          {row[field] || "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="empty">
                <div className="empty-icon">✉</div>
                <h3>No results yet</h3>
                <p>
                  Connect your mailbox, select the fields you need and start
                  extraction.
                </p>
              </div>
            )}
          </div>
        </section>

        <footer>
          <span>Mail Header Extractor V4.2</span>
          <span>Mailbox credentials are not persisted by this application.</span>
        </footer>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
