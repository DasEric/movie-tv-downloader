import { useEffect, useRef, useState } from "react";
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
  const [episodes, setEpisodes] = useState<
    { episode: number; has_language: boolean; actual_language: string | null }[]
  >([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [language, setLanguage] = useState("de");
  const [loadingLang, setLoadingLang] = useState(false);
  const [quality, setQuality] = useState("1080p");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // Library state: what episodes/movies are already on disk
  const [librarySeasons, setLibrarySeasons] = useState<Record<string, number[]>>({});
  const [movieOnDisk, setMovieOnDisk] = useState(false);

  // Generation counter: incremented every time a new language check starts.
  // Stale background callbacks compare their snapshot against this and
  // bail out if a newer check has been triggered since.
  const langCheckGen = useRef(0);

  const currentSource = SOURCES.find((s) => s.key === source);
  const kind = currentSource?.kind ?? "series";
  const languageLocked = currentSource?.languageLocked ?? false;

  const langLabel = (code: string | null): string => {
    if (!code) return "";
    const map: Record<string, string> = {
      de: t("common.gerDub"),
      "de-dub": t("common.gerDub"),
      "de-sub": t("common.gerSub"),
      en: t("common.engDub"),
      "en-dub": t("common.engDub"),
      "en-sub": t("common.engSub"),
    };
    return map[code] ?? code;
  };

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

  // Re-check language availability when user changes the language dropdown.
  // Keep existing episode numbers visible (optimistic), only update has_language.
  useEffect(() => {
    if (!selected?.slug || !season || kind !== "series" || languageLocked) return;
    const gen = ++langCheckGen.current;
    setLoadingLang(true);
    setPicked(new Set());
    setEpisodes((prev) =>
      prev.map((e) => ({ ...e, has_language: true, actual_language: language }))
    );
    api
      .listEpisodesWithLang(source, selected.slug, season, language)
      .then((e) => {
        if (gen === langCheckGen.current) setEpisodes(e.episodes);
      })
      .catch(() => {})
      .finally(() => {
        if (gen === langCheckGen.current) setLoadingLang(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [language]);

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
    setLibrarySeasons({});
    setMovieOnDisk(false);

    if (kind === "movie") {
      // Check if movie is on disk
      api.checkMovie(r.title, r.year).then((res) => setMovieOnDisk(res.found)).catch(() => {});
      return;
    }
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

      // Check what's already on disk (background, non-blocking)
      const showTitle = details.title || r.title;
      api.checkShow(showTitle).then((lib) => setLibrarySeasons(lib.seasons)).catch(() => {});

      if (details.seasons.length) {
        const firstSeason = details.seasons[0];
        setSeason(firstSeason);

        // Step 1: load episode numbers instantly
        try {
          const fast = await api.listEpisodes(source, r.slug, firstSeason);
          setEpisodes(
            fast.episodes.map((ep) => ({ episode: ep, has_language: true, actual_language: language }))
          );
        } catch {
          // ignore — episodes just won't show
        }

        // Step 2: language check in background (guarded by generation counter)
        const gen = ++langCheckGen.current;
        setLoadingLang(true);
        api
          .listEpisodesWithLang(source, r.slug, firstSeason, language)
          .then((lang) => {
            if (gen === langCheckGen.current) setEpisodes(lang.episodes);
          })
          .catch(() => {})
          .finally(() => {
            if (gen === langCheckGen.current) setLoadingLang(false);
          });
      }
    } catch (e: any) {
      setMsg(e.message);
    }
  };

  const changeSeason = async (n: number) => {
    setSeason(n);
    setPicked(new Set());
    setEpisodes([]);
    if (!selected?.slug) return;

    // Bump generation so any in-flight background check from a previous
    // season/pickResult is discarded when it resolves.
    const gen = ++langCheckGen.current;

    // Step 1: load episode numbers instantly (fast, single request)
    try {
      const fast = await api.listEpisodes(source, selected.slug, n);
      if (gen !== langCheckGen.current) return; // stale
      setEpisodes(
        fast.episodes.map((ep) => ({ episode: ep, has_language: true, actual_language: language }))
      );
    } catch (e: any) {
      setMsg(e.message || "failed to load episodes");
      return;
    }

    // Step 2: check language availability in background
    setLoadingLang(true);
    try {
      const lang = await api.listEpisodesWithLang(source, selected.slug, n, language);
      if (gen !== langCheckGen.current) return; // stale
      setEpisodes(lang.episodes);
      setPicked(new Set());
    } catch {
      // Language check failed — keep optimistic view
    } finally {
      if (gen === langCheckGen.current) setLoadingLang(false);
    }
  };

  const [unavailableClickEp, setUnavailableClickEp] = useState<number | null>(null);

  const toggleEpisode = (ep: { episode: number; has_language: boolean; actual_language: string | null }) => {
    if (!ep.has_language) {
      // Episode not in desired language — show watchlist prompt
      setUnavailableClickEp(ep.episode);
      setWatchlistPrompt({ skipped: 1, total: 1, added: 0 });
      return;
    }
    const next = new Set(picked);
    if (next.has(ep.episode)) next.delete(ep.episode);
    else next.add(ep.episode);
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

  const [watchlistPrompt, setWatchlistPrompt] = useState<{
    skipped: number;
    total: number;
    added: number;
  } | null>(null);

  const addWholeSeason = async () => {
    if (!selected || !season || !selected.slug) return;
    setBusy(true);
    try {
      const res = await api.addSeason({
        source,
        slug: selected.slug,
        title: selected.title,
        season,
        language,
        quality,
      });
      if (res.skipped > 0) {
        setWatchlistPrompt({
          skipped: res.skipped,
          total: res.total,
          added: res.count,
        });
      } else {
        setMsg("✓");
      }
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  };

  const handleWatchlistPromptYes = async () => {
    setWatchlistPrompt(null);
    setUnavailableClickEp(null);
    await addToWatchlist(null);
  };

  const handleWatchlistPromptNo = () => {
    setWatchlistPrompt(null);
    setUnavailableClickEp(null);
    if (!unavailableClickEp) setMsg("✓"); // only show checkmark for season-add, not single click
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
                  {/* Download status badge */}
                  {kind === "movie" && selected?.url === r.url && movieOnDisk && (
                    <span className="download-badge complete" />
                  )}
                  {kind === "series" && selected?.url === r.url && Object.keys(librarySeasons).length > 0 && (() => {
                    const libSeasonCount = Object.keys(librarySeasons).length;
                    const allComplete = seasons.length > 0 && libSeasonCount >= seasons.length;
                    return <span className={`download-badge ${allComplete ? "complete" : "partial"}`} />;
                  })()}
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
            {seasons.map((s) => {
              const libEps = librarySeasons[String(s)];
              const hasAll = libEps && episodes.length > 0 && libEps.length >= episodes.length && season === s;
              const hasSome = libEps && libEps.length > 0;
              return (
                <button
                  key={s}
                  className={season === s ? "primary" : ""}
                  onClick={() => changeSeason(s)}
                >
                  {hasSome && (
                    <span className={`season-badge ${hasAll ? "complete" : "partial"}`} />
                  )}
                  {t("common.season")} {s}
                </button>
              );
            })}
          </div>

          {episodes.length > 0 && (
            <>
              <h3>
                {t("add.chooseEpisodes")}
                {loadingLang && (
                  <span style={{ marginLeft: 10, fontSize: 12, color: "var(--text-dim)", fontWeight: "normal" }}>
                    <span className="spinner" style={{ marginRight: 6 }} />
                    {t("add.loadingLanguageInfo")}
                  </span>
                )}
              </h3>
              {(() => {
                const available = episodes.filter((e) => e.has_language).length;
                const total = episodes.length;
                if (available < total) {
                  return (
                    <div
                      style={{
                        fontSize: 12,
                        color: available === 0 ? "var(--danger)" : "var(--text-dim)",
                        marginBottom: 8,
                      }}
                    >
                      {available === 0
                        ? t("add.noEpisodesInLang")
                        : t("add.availableCount", {
                            available,
                            total,
                            language: langLabel(language),
                          })}
                    </div>
                  );
                }
                return null;
              })()}
              <div className="row">
                <button
                  onClick={() =>
                    setPicked(
                      new Set(
                        episodes.filter((e) => e.has_language).map((e) => e.episode)
                      )
                    )
                  }
                >
                  {t("add.selectAll")}
                </button>
                <button onClick={() => setPicked(new Set())}>{t("add.deselectAll")}</button>
              </div>
              <div className="episode-grid">
                {episodes.map((e) => {
                  const isFallback =
                    e.has_language &&
                    e.actual_language != null &&
                    e.actual_language !== language;
                  const onDisk = librarySeasons[String(season)]?.includes(e.episode);
                  return (
                    <button
                      key={e.episode}
                      className={
                        (!e.has_language
                          ? "unavailable"
                          : picked.has(e.episode)
                          ? "selected"
                          : "") + (onDisk ? " on-disk" : "")
                      }
                      onClick={() => toggleEpisode(e)}
                      disabled={!e.has_language}
                      title={
                        !e.has_language
                          ? (t("add.notAvailableInLang") as string)
                          : isFallback
                          ? (t("add.fallbackLang", {
                              lang: langLabel(e.actual_language),
                            }) as string)
                          : undefined
                      }
                    >
                      <span>
                        {onDisk && <span className="ep-check">&#10003;</span>}
                        E{String(e.episode).padStart(2, "0")}
                      </span>
                      {isFallback && (
                        <span
                          style={{
                            display: "block",
                            fontSize: 9,
                            opacity: 0.7,
                            marginTop: 2,
                          }}
                        >
                          {langLabel(e.actual_language)}
                        </span>
                      )}
                    </button>
                  );
                })}
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
      {/* Modal: offer watchlist when episodes are unavailable */}
      {watchlistPrompt && (
        <div className="modal-backdrop" onClick={handleWatchlistPromptNo}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h3>{t("add.watchSeasonHeading")}</h3>
            <p>
              {unavailableClickEp != null
                ? t("add.episodeNotInLang", {
                    episode: `E${String(unavailableClickEp).padStart(2, "0")}`,
                    language: langLabel(language),
                  })
                : watchlistPrompt.added > 0
                ? t("add.seasonPartial", {
                    added: watchlistPrompt.added,
                    skipped: watchlistPrompt.skipped,
                    total: watchlistPrompt.total,
                    language: langLabel(language),
                  })
                : t("add.seasonNoneAvailable", {
                    total: watchlistPrompt.total,
                    language: langLabel(language),
                  })}
            </p>
            <div className="row" style={{ gap: 8 }}>
              <button className="primary" onClick={handleWatchlistPromptYes}>
                {t("add.addToWatchlistYes")}
              </button>
              <button onClick={handleWatchlistPromptNo}>
                {t("add.addToWatchlistNo")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
