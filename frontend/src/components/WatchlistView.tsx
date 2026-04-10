import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";

interface Watch {
  id: number;
  source: string;
  slug: string;
  title: string;
  season: number;
  language: string;
  quality: string;
  poster: string | null;
  expires_at: string | null;
  last_checked: string | null;
  last_message: string | null;
  enqueued_episodes: number[];
  created_at: string | null;
}

export function WatchlistView() {
  const { t } = useTranslation();
  const [list, setList] = useState<Watch[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setList(await api.listWatches());
    } catch (e: any) {
      setMsg(e.message || "error");
    }
  };

  useEffect(() => {
    refresh();
    // Refresh every 30s while the tab is open — the scheduler updates the
    // `last_checked` / `last_message` fields on each tick.
    const timer = setInterval(refresh, 30_000);
    return () => clearInterval(timer);
  }, []);

  const remove = async (id: number) => {
    if (!confirm(t("watchlist.confirmRemove") as string)) return;
    try {
      await api.removeWatch(id);
      await refresh();
    } catch (e: any) {
      setMsg(e.message || "error");
    }
  };

  const checkNow = async (id: number) => {
    setBusy(true);
    setMsg(null);
    try {
      await api.checkWatchNow(id);
      await refresh();
      setMsg(t("watchlist.checkedNow") as string);
    } catch (e: any) {
      setMsg(e.message || "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{t("watchlist.heading")}</h2>
        <span className="tag queued">
          {t("watchlist.watching", { count: list.length })}
        </span>
      </div>

      <div
        style={{ marginTop: 10, fontSize: 13, color: "var(--text-dim)" }}
      >
        {t("watchlist.explanation")}
      </div>

      {msg && (
        <div style={{ marginTop: 8, color: "var(--text-dim)" }}>{msg}</div>
      )}

      {list.length === 0 ? (
        <div className="empty-state" style={{ marginTop: 24 }}>
          <p>{t("watchlist.empty")}</p>
          <p style={{ fontSize: 13, marginTop: 12 }}>
            {t("watchlist.emptyHint")}
          </p>
        </div>
      ) : (
        <div className="result-grid" style={{ marginTop: 16 }}>
          {list.map((w) => (
            <div key={w.id} className="result-card">
              <div className="poster-wrap">
                {w.poster ? (
                  <img src={w.poster} alt={w.title} loading="lazy" />
                ) : (
                  <div className="poster-placeholder">📺</div>
                )}
              </div>
              <div className="result-body">
                <div className="result-title">{w.title}</div>
                <div className="result-year">
                  {t("common.season")} {w.season} · {w.source} · {w.language}
                </div>
                <div className="result-year" style={{ fontSize: 11 }}>
                  📥 {t("watchlist.enqueuedCount", {
                    count: w.enqueued_episodes.length,
                  })}
                </div>
                {w.expires_at && (
                  <div className="result-year" style={{ fontSize: 11 }}>
                    ⏳ {t("watchlist.expires")}:{" "}
                    {new Date(w.expires_at).toLocaleDateString()}
                  </div>
                )}
                {w.last_checked && (
                  <div className="result-year" style={{ fontSize: 11 }}>
                    🔄 {new Date(w.last_checked).toLocaleString()}
                  </div>
                )}
                {w.last_message && (
                  <div
                    className="result-year"
                    style={{ fontSize: 11, opacity: 0.7 }}
                  >
                    {w.last_message}
                  </div>
                )}
              </div>
              <div className="row" style={{ padding: "0 12px 12px" }}>
                <button
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    checkNow(w.id);
                  }}
                  style={{ flex: 1 }}
                >
                  🔄 {t("watchlist.checkNow")}
                </button>
                <button
                  className="danger"
                  onClick={(e) => {
                    e.stopPropagation();
                    remove(w.id);
                  }}
                >
                  {t("common.delete")}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div
        style={{ marginTop: 14, fontSize: 12, color: "var(--text-dim)" }}
      >
        {t("watchlist.intervalNote")}
      </div>
    </div>
  );
}
