import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ItemSource, SearchResult } from "../api";

const SOURCES: {
  key: ItemSource;
  label: string;
  kind: "series" | "movie";
  languageLocked?: boolean;
}[] = [
  { key: "s.to", label: "s.to", kind: "series" },
  { key: "aniworld", label: "AniWorld", kind: "series" },
  { key: "megakino", label: "Megakino (german only)", kind: "movie", languageLocked: true },
];

export function AddView() {
  const { t } = useTranslation();
  const [source, setSource] = useState<ItemSource>("s.to");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [selected, setSelected] = useState<SearchResult | null>(null);
  const [seasons, setSeasons] = useState<number[]>([]);
  const [season, setSeason] = useState<number | null>(null);
  const [episodes, setEpisodes] = useState<number[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [language, setLanguage] = useState("de");
  const [quality, setQuality] = useState("1080p");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const currentSource = SOURCES.find((s) => s.key === source);
  const kind = currentSource?.kind ?? "series";
  const languageLocked = currentSource?.languageLocked ?? false;

  useEffect(() => {
    setSelected(null);
    setResults([]);
    setSeasons([]);
    setSeason(null);
    setEpisodes([]);
    setPicked(new Set());
    // megakino only has German content — force the language field back
    // to "de" so the queue item has a consistent value.
    if (languageLocked) {
      setLanguage("de");
    }
  }, [source, languageLocked]);

  const doSearch = async () => {
    if (!query.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.search(source, query.trim());
      setResults(r);
      if (r.length === 0) setMsg(t("add.noResults"));
    } catch (e: any) {
      setMsg(e.message || "error");
    } finally {
      setBusy(false);
    }
  };

  const pickResult = async (r: SearchResult) => {
    setSelected(r);
    setSeasons([]);
    setEpisodes([]);
    setPicked(new Set());

    if (kind === "movie") return;
    if (!r.slug) return;

    try {
      // One-shot fetch: poster + seasons + canonical title
      const details = await api.getShowDetails(source, r.slug);
      setSeasons(details.seasons);

      // Merge the details into BOTH the selected item AND the matching
      // entry in the results grid, so the card also updates.
      const merged: SearchResult = {
        ...r,
        title: details.title || r.title,
        poster: details.poster ?? r.poster,
      };
      setSelected(merged);
      setResults((prev) => prev.map((p) => (p.url === r.url ? merged : p)));

      if (details.seasons.length) {
        setSeason(details.seasons[0]);
        const e = await api.listEpisodes(source, r.slug, details.seasons[0]);
        setEpisodes(e.episodes);
      }
    } catch (e: any) {
      setMsg(e.message);
    }
  };

  const changeSeason = async (n: number) => {
    setSeason(n);
    setPicked(new Set());
    if (selected?.slug) {
      const e = await api.listEpisodes(source, selected.slug, n);
      setEpisodes(e.episodes);
    }
  };

  const toggleEpisode = (n: number) => {
    const next = new Set(picked);
    if (next.has(n)) next.delete(n);
    else next.add(n);
    setPicked(next);
  };

  const addMovie = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await api.addItem({
        source,
        kind: "movie",
        title: selected.title,
        url: selected.url,
        language,
        quality,
      });
      setMsg("✓");
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  };

  const addEpisodes = async () => {
    if (!selected || !season || picked.size === 0 || !selected.slug) return;
    setBusy(true);
    try {
      await api.addBulkEpisodes({
        source,
        slug: selected.slug,
        title: selected.title,
        season,
        episodes: Array.from(picked).sort((a, b) => a - b),
        language,
        quality,
      });
      setPicked(new Set());
      setMsg("✓");
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  };

  const addWholeSeason = async () => {
    if (!selected || !season || !selected.slug) return;
    setBusy(true);
    try {
      await api.addSeason({
        source,
        slug: selected.slug,
        title: selected.title,
        season,
        language,
        quality,
      });
      setMsg("✓");
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  };

  const addToWatchlist = async (expiresInDays: number | null) => {
    if (!selected || !season || !selected.slug) return;
    if (source === "megakino") return; // no seasons on megakino
    setBusy(true);
    try {
      await api.addWatch({
        source,
        slug: selected.slug,
        title: selected.title,
        season,
        language,
        quality,
        poster: selected.poster,
        expires_in_days: expiresInDays,
      });
      setMsg(t("add.addedToWatchlist") as string);
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h2>{t("add.heading")}</h2>

      <div className="grid-3">
        <div>
          <label>{t("add.source")}</label>
          <select value={source} onChange={(e) => setSource(e.target.value as ItemSource)}>
            {SOURCES.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label>{t("add.language")}</label>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            disabled={languageLocked}
            title={
              languageLocked
                ? (t("add.languageLockedHint") as string)
                : (t("add.languageHint") as string)
            }
          >
            <option value="de">{t("common.gerDub")}</option>
            <option value="de-sub">{t("common.gerSub")}</option>
            <option value="en">{t("common.engDub")}</option>
            <option value="en-sub">{t("common.engSub")}</option>
          </select>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            {languageLocked
              ? t("add.languageLockedHint")
              : t("add.languageHint")}
          </div>
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

      <div style={{ marginTop: 16 }}>
        <label>{t("add.query")}</label>
        <div className="row">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doSearch()}
            placeholder="…"
          />
          <button className="primary" onClick={doSearch} disabled={busy}>
            {busy ? t("add.searching") : t("common.search")}
          </button>
        </div>
      </div>

      {msg && <div style={{ marginTop: 8, color: "var(--text-dim)" }}>{msg}</div>}

      {results.length > 0 && (
        <>
          <h3>{t("add.results")}</h3>
          <div className="result-grid">
            {results.map((r) => (
              <div
                key={r.url}
                className={"result-card " + (selected?.url === r.url ? "active" : "")}
                onClick={() => pickResult(r)}
              >
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
                </div>
                {kind === "movie" && selected?.url === r.url && (
                  <button
                    className="primary"
                    onClick={(e) => {
                      e.stopPropagation();
                      addMovie();
                    }}
                    disabled={busy}
                  >
                    {t("add.addMovie")}
                  </button>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {selected && kind === "series" && (
        <div className="selected-show">
          {selected.poster ? (
            <img src={selected.poster} alt={selected.title} />
          ) : (
            <div
              className="poster-placeholder"
              style={{
                width: 90,
                aspectRatio: "2 / 3",
                borderRadius: 6,
                background: "var(--bg-elev)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              📺
            </div>
          )}
          <div>
            <h3 style={{ margin: 0 }}>{selected.title}</h3>
          </div>
        </div>
      )}

      {selected && kind === "series" && seasons.length > 0 && (
        <>
          <h3>{t("add.chooseSeason")}</h3>
          <div className="badge-row">
            {seasons.map((s) => (
              <button
                key={s}
                className={season === s ? "primary" : ""}
                onClick={() => changeSeason(s)}
              >
                {t("common.season")} {s}
              </button>
            ))}
          </div>

          {episodes.length > 0 && (
            <>
              <h3>{t("add.chooseEpisodes")}</h3>
              <div className="row">
                <button onClick={() => setPicked(new Set(episodes))}>
                  {t("add.selectAll")}
                </button>
                <button onClick={() => setPicked(new Set())}>{t("add.deselectAll")}</button>
              </div>
              <div className="episode-grid">
                {episodes.map((e) => (
                  <button
                    key={e}
                    className={picked.has(e) ? "selected" : ""}
                    onClick={() => toggleEpisode(e)}
                  >
                    E{String(e).padStart(2, "0")}
                  </button>
                ))}
              </div>
              <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
                <button onClick={addWholeSeason} disabled={busy}>
                  {t("add.addWholeSeason")}
                </button>
                <button
                  className="primary"
                  onClick={addEpisodes}
                  disabled={busy || picked.size === 0}
                >
                  {t("add.addSelected")} ({picked.size})
                </button>
              </div>

              {/* Season watchlist — auto-download new episodes as they appear */}
              <div
                style={{
                  marginTop: 20,
                  padding: 14,
                  border: "1px dashed var(--border)",
                  borderRadius: 8,
                }}
              >
                <h4 style={{ margin: "0 0 6px" }}>
                  🔔 {t("add.watchSeasonHeading")}
                </h4>
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--text-dim)",
                    marginBottom: 10,
                  }}
                >
                  {t("add.watchSeasonHint")}
                </div>
                <div className="row wrap" style={{ gap: 8 }}>
                  <button
                    onClick={() => addToWatchlist(7)}
                    disabled={busy}
                  >
                    {t("add.watchFor7d")}
                  </button>
                  <button
                    onClick={() => addToWatchlist(30)}
                    disabled={busy}
                  >
                    {t("add.watchFor30d")}
                  </button>
                  <button
                    onClick={() => addToWatchlist(90)}
                    disabled={busy}
                  >
                    {t("add.watchFor90d")}
                  </button>
                  <button
                    onClick={() => addToWatchlist(null)}
                    disabled={busy}
                  >
                    {t("add.watchForever")}
                  </button>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
