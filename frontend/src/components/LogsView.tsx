import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { openLogsSocket } from "../api";

interface LogEntry {
  ts?: string;
  level?: string;
  logger?: string;
  msg?: string;
  event?: string;
}

export function LogsView() {
  const { t } = useTranslation();
  const [lines, setLines] = useState<LogEntry[]>([]);
  const [level, setLevel] = useState<"ALL" | "INFO" | "WARNING" | "ERROR">("ALL");
  const [auto, setAuto] = useState(true);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ws = openLogsSocket((entry: LogEntry) => {
      if (entry.event === "ping") return;
      setLines((prev) => {
        const next = [...prev, entry];
        return next.slice(-2000);
      });
    });
    return () => {
      try {
        ws.close();
      } catch {}
    };
  }, []);

  useEffect(() => {
    if (auto && boxRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight;
    }
  }, [lines, auto]);

  const filtered = lines.filter((l) => {
    if (!l.level) return true;
    if (level === "ALL") return true;
    if (level === "INFO") return ["INFO", "WARNING", "ERROR", "CRITICAL"].includes(l.level);
    if (level === "WARNING") return ["WARNING", "ERROR", "CRITICAL"].includes(l.level);
    if (level === "ERROR") return ["ERROR", "CRITICAL"].includes(l.level);
    return true;
  });

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>{t("logs.heading")}</h2>
        <div className="row">
          <select value={level} onChange={(e) => setLevel(e.target.value as any)}>
            <option value="ALL">All</option>
            <option value="INFO">Info</option>
            <option value="WARNING">Warn</option>
            <option value="ERROR">Error</option>
          </select>
          <label style={{ display: "flex", alignItems: "center", gap: 6, margin: 0 }}>
            <input
              type="checkbox"
              checked={auto}
              onChange={(e) => setAuto(e.target.checked)}
              style={{ width: "auto" }}
            />
            {t("logs.autoscroll")}
          </label>
          <button onClick={() => setLines([])}>{t("logs.clear")}</button>
        </div>
      </div>

      <div className="log-viewer" ref={boxRef}>
        {filtered.map((l, i) => (
          <div key={i} className={`log-line ${l.level || "INFO"}`}>
            {l.ts && <span style={{ color: "#64748b" }}>{l.ts.slice(11, 19)} </span>}
            {l.level && <span>[{l.level}] </span>}
            {l.msg}
          </div>
        ))}
      </div>
    </div>
  );
}
