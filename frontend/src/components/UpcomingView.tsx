import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, QueueItem } from "../api";

type TmdbKind = "movie" | "tv";

interface TmdbResult {
  tmdb_id: number;
  title: string;
  year: string;
  release_date: string | null;
  poster: string | null;
  overview: string | null;
}

export function UpcomingView({ tmdbConfigured }: { tmdbConfigured: boolean }) {
  const { t } = useTranslation();
  const [list, setList] = useState<QueueItem[]>([]);
  const [kind, setKind] = useState<TmdbKind>("movie");
  const [results, setResults] = useState<TmdbResult[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [language, setLanguage] = useState("de");
  const [quality, setQuality] = useState("1080p");
  const [season, setSeason] = useState(1);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const items = await api.listUpcoming();
      setList(items);
    } catch (e: any) {
      setMsg(e.message || "error");
    }
  };

  useEffect(() => {
    if (tmdbConfigured) {
      refresh();
      loadDefault();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tmdbConfigured, kind]);

  const loadDefault = async () => {
    if (!tmdbConfigured) return;
    setBusy(true);
    try {
      const data =
        kind === "movie"
          ? await api.tmdbUpcomingMovies()
          : await api.tmdbUpcomingTv();
      setResults(data);
    } catch (e: any) {
      setMsg(e.message || "error");
    } finally {
      setBusy(false);
    }
  };

  const doSearch = async () => {
    if (!searchQuery.trim()) {
      loadDefault();
      return;
    }
    setBusy(true);
    try {
      const data = await api.tmdbSearch(searchQuery.trim(), kind);
      setResults(data);
    } catch (e: any) {
      setMsg(e.message || "error");
    } finally {
      setBusy(false);
    }
  };

  const addToUpcoming = async (r: TmdbResult) => {
    setBusy(true);
    setMsg(null);
    try {
      const payload = {
        source: kind === "movie" ? "megakino" : "s.to",
        kind: kind === "movie" ? "movie" : "season",
        title: r.title,
        year: r.year ? parseInt(r.year, 10) : undefined,
        tmdb_id: r.tmdb_id,
        release_date: r.release_date ? r.release_date.slice(0, 10) : undefined,
        language,
        quality,
        season: kind === "tv" ? season : undefined,
        poster: r.poster,
      };
      await api.addUpcoming(payload);
      await refresh();
      setMsg(t("upcoming.added") as string);
    } catch (e: any) {
      setMsg(e.message || "error");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    if (!confirm(t("upcoming.confirmRemove") as string)) return;
    try {
      await api.removeUpcoming(id);
      await refresh();
    } catch (e: any) {
      setMsg(e.message || "error");
    }
  };

  if (!tmdbConfigured) {
    return (
      <div className="card">
        <h2>{t("upcoming.heading")}</h2>
        <div className="empty-state">
          <p>{t("upcoming.needsTmdbKey")}</p>
          <p style={{ marginTop: 12, fontSize: 13 }}>
            {t("upcoming.needsTmdbKeyHint")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      {/* Watchlist */}
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>{t("upcoming.heading")}</h2>
          <span className="tag queued">
            {t("upcoming.watching", { count: list.length })}
          </span>
        </div>

        {list.length === 0 ? (
          <div className="empty-state">{t("upcoming.emptyWatchlist")}</div>
        ) : (
          <div className="result-grid" style={{ marginTop: 14 }}>
            {list.map((it) => (
              <div key={it.id} className="result-card">
                <div className="poster-wrap">
                  {it.output_path ? (
                    <img src={it.output_path} alt={it.title} loading="lazy" />
                  ) : (
                    <div className="poster-placeholder">
                      {it.kind === "movie" ? "🎬" : "📺"}
                    </div>
                  )}
                </div>
                <div className="result-body">
                  <div className="result-title">{it.title}</div>
                  {it.release_date && (
                    <div className="result-year">
                      📅 {String(it.release_date).slice(0, 10)}
                    </div>
                  )}
                  {it.kind === "season" && it.season && (
                    <div className="result-year">
                      {t("common.season")} {it.season}
                    </div>
                  )}
                  <div className="result-year">
                    {it.source} · {it.language}
                  </div>
                  {it.message && (
                    <div
                      className="result-year"
                      style={{ fontSize: 11, opacity: 0.7 }}
                    >
                      {it.message}
                    </div>
                  )}
                </div>
                <button
                  className="danger"
                  onClick={(e) => {
                    e.stopPropagation();
                    remove(it.id);
                  }}
                >
                  {t("common.delete")}
                </button>
              </div>
            ))}
          </div>
        )}

        <div
          style={{
            marginTop: 14,
            fontSize: 12,
            color: "var(--text-dim)",
          }}
        >
          {t("upcoming.intervalNote")}
        </div>
      </div>

      {/* TMDB search & add */}
      <div className="card">
        <h2>{t("upcoming.addHeading")}</h2>

        <div className="grid-3">
          <div>
            <label>{t("upcoming.kind")}</label>
            <select value={kind} onChange={(e) => setKind(e.target.value as TmdbKind)}>
              <option value="movie">🎬 {t("common.movie")}</option>
              <option value="tv">📺 {t("common.series")}</option>
            </select>
          </div>
          <div>
            <label>{t("add.language")}</label>
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              disabled={kind === "movie"}
              title={
                kind === "movie" ? (t("upcoming.movieLangHint") as string) : undefined
              }
            >
              <option value="de">{t("common.gerDub")}</option>
              <option value="de-sub">{t("common.gerSub")}</option>
              <option value="en">{t("common.engDub")}</option>
              <option value="en-sub">{t("common.engSub")}</option>
            </select>
          </div>
          <div>
            <label>{t("add.quality")}</label>
            <select value={quality} onChange={(e) => setQuality(e.target.value)}>
              <option>480p</option>
              <option>720p</option>
              <option>1080p</option>
              <option>1440p</option>
              <option>4k</option>
              <option>best</option>
            </select>
          </div>
        </div>

        {kind === "tv" && (
          <div style={{ marginTop: 12 }}>
            <label>{t("upcoming.seasonToWatch")}</label>
            <input
              type="number"
              min={1}
              value={season}
              onChange={(e) => setSeason(Math.max(1, Number(e.target.value) || 1))}
              style={{ maxWidth: 120 }}
            />
          </div>
        )}

        <div style={{ marginTop: 16 }}>
          <label>{t("upcoming.searchTmdb")}</label>
          <div className="row">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doSearch()}
              placeholder={t("upcoming.searchPlaceholder") as string}
            />
            <button className="primary" onClick={doSearch} disabled={busy}>
              {busy ? t("add.searching") : t("common.search")}
            </button>
            <button onClick={loadDefault} disabled={busy}>
              {t("upcoming.showUpcoming")}
            </button>
          </div>
        </div>

        {msg && <div style={{ marginTop: 8, color: "var(--text-dim)" }}>{msg}</div>}

        <div className="result-grid" style={{ marginTop: 16 }}>
          {results.map((r) => (
            <div key={r.tmdb_id} className="result-card">
              <div className="poster-wrap">
                {r.poster ? (
                  <img src={r.poster} alt={r.title} loading="lazy" />
                ) : (
                  <div className="poster-placeholder">
                    {kind === "movie" ? "🎬" : "📺"}
                  </div>
                )}
              </div>
              <div className="result-body">
                <div className="result-title">{r.title}</div>
                {r.year && <div className="result-year">({r.year})</div>}
                {r.release_date && (
                  <div className="result-year" style={{ fontSize: 11 }}>
                    📅 {String(r.release_date).slice(0, 10)}
                  </div>
                )}
              </div>
              <button
                className="primary"
                onClick={(e) => {
                  e.stopPropagation();
                  addToUpcoming(r);
                }}
                disabled={busy}
              >
                + {t("upcoming.watch")}
              </button>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
