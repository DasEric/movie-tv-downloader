import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ItemSource, SearchResult } from "../api";

interface CatalogSource {
  key: ItemSource;
  label: string;
  kind: "series" | "movie" | "both";
  languageLocked?: boolean;
}

const SOURCES: CatalogSource[] = [
  { key: "filmpalast.to", label: "FilmPalast", kind: "both" },
  { key: "kinox.to", label: "KinoX", kind: "both" },
  { key: "megakino", label: "Megakino", kind: "movie", languageLocked: true },
  { key: "s.to", label: "s.to", kind: "series" },
  { key: "burning-series.io", label: "Burning Series", kind: "series" },
  { key: "aniworld", label: "AniWorld", kind: "series" },
];

export function CatalogView() {
  const { t } = useTranslation();
  
  // State for Catalog Filters
  const [source, setSource] = useState<ItemSource>("filmpalast.to");
  const [category, setCategory] = useState("movies");
  const [languageFilter, setLanguageFilter] = useState("all");
  const [hosterFilter, setHosterFilter] = useState("all");
  const [sortOption, setSortOption] = useState("default");
  const [page, setPage] = useState(1);

  // Results & Selected Item details
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

  // References and local settings mirror
  const userTouchedLang = useRef(false);
  const userTouchedQual = useRef(false);
  const langCheckGen = useRef(0);

  // Library cache state
  const [librarySeasons, setLibrarySeasons] = useState<Record<string, number[]>>({});
  const [movieOnDisk, setMovieOnDisk] = useState(false);
  const [resultLibStatus, setResultLibStatus] = useState<Record<string, "complete" | "partial">>({});

  const currentSource = SOURCES.find((s) => s.key === source);
  const languageLocked = currentSource?.languageLocked ?? false;

  // Sync category when source changes to prevent illegal category selections
  useEffect(() => {
    if (currentSource) {
      if (currentSource.kind === "series") {
        setCategory("series");
      } else if (currentSource.kind === "movie") {
        setCategory("movies");
      } else {
        setCategory("movies");
      }
    }
    setPage(1);
    setSelected(null);
    setResults([]);
    setMsg(null);
  }, [source]);

  // Load Settings on mount
  useEffect(() => {
    api
      .getSettings()
      .then((s) => {
        if (!userTouchedQual.current && s?.quality_profile) {
          setQuality(s.quality_profile);
        }
        if (!userTouchedLang.current && s?.default_language) {
          setLanguage(s.default_language);
          setLanguageFilter(s.default_language);
        }
      })
      .catch(() => {});
  }, []);

  // Sync details check for selected show seasons/episodes language
  useEffect(() => {
    if (!selected?.slug || !season || !isSeriesSource(selected.source as any) || languageLocked) return;
    const gen = ++langCheckGen.current;
    setLoadingLang(true);
    setPicked(new Set());
    api
      .listEpisodesWithLang(selected.source as any, selected.slug, season, language)
      .then((e) => {
        if (gen === langCheckGen.current) setEpisodes(e.episodes);
      })
      .catch(() => {})
      .finally(() => {
        if (gen === langCheckGen.current) setLoadingLang(false);
      });
  }, [language]);

  // Main catalog load trigger
  const loadCatalog = async () => {
    setBusy(true);
    setMsg(null);
    setResultLibStatus({});
    try {
      const res = await api.getCatalog(
        source,
        category,
        page,
        languageFilter === "all" ? undefined : languageFilter,
        hosterFilter === "all" ? undefined : hosterFilter,
        sortOption === "default" ? undefined : sortOption
      );
      setResults(res);
      if (res.length === 0) setMsg("Keine Ergebnisse in diesem Katalog gefunden.");
      
      // Check library status for results
      for (const item of res) {
        if (isSeriesSource(item.source as any)) {
          api.checkShow(item.title).then((lib) => {
            if (lib.found) {
              const hasAll = lib.total_episodes > 0;
              setResultLibStatus((prev) => ({
                ...prev,
                [item.url]: hasAll ? "complete" : "partial",
              }));
            }
          }).catch(() => {});
        } else {
          api.checkMovie(item.title, item.year).then((lib) => {
            if (lib.found) {
              setResultLibStatus((prev) => ({ ...prev, [item.url]: "complete" }));
            }
          }).catch(() => {});
        }
      }
    } catch (e: any) {
      setMsg(`Fehler beim Laden des Katalogs: ${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  // Trigger catalog reload when parameters change
  useEffect(() => {
    if (!selected) {
      loadCatalog();
    }
  }, [source, category, languageFilter, hosterFilter, sortOption, page, selected]);

  const handleSelect = async (item: SearchResult) => {
    setSelected(item);
    setSeasons([]);
    setSeason(null);
    setEpisodes([]);
    setPicked(new Set());
    setLibrarySeasons({});
    setMovieOnDisk(false);
    setBusy(true);

    const isSeries = isSeriesSource(item.source as any);

    try {
      if (isSeries) {
        // Fetch show details
        const details = await api.getShowDetails(item.source as any, item.slug!);
        setSeasons(details.seasons);
        
        // Load library status
        const lib = await api.checkShow(item.title);
        if (lib.found) {
          setLibrarySeasons(lib.seasons);
        }

        if (details.seasons.length > 0) {
          const first = details.seasons[0];
          setSeason(first);
          const epData = await api.listEpisodesWithLang(
            item.source as any,
            item.slug!,
            first,
            language
          );
          setEpisodes(epData.episodes);
        }
      } else {
        const lib = await api.checkMovie(item.title, item.year);
        setMovieOnDisk(lib.found);
      }
    } catch (e: any) {
      setMsg(`Fehler beim Laden der Details: ${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const selectSeason = async (num: number) => {
    if (!selected?.slug) return;
    setSeason(num);
    setEpisodes([]);
    setPicked(new Set());
    setBusy(true);
    try {
      const epData = await api.listEpisodesWithLang(
        selected.source as any,
        selected.slug,
        num,
        language
      );
      setEpisodes(epData.episodes);
    } catch (e: any) {
      setMsg(e.message || "Failed to load episodes");
    } finally {
      setBusy(false);
    }
  };

  const toggleEpisode = (epNum: number) => {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(epNum)) next.delete(epNum);
      else next.add(epNum);
      return next;
    });
  };

  const enqueueSelected = async () => {
    if (!selected || picked.size === 0 || !season) return;
    setBusy(true);
    setMsg(null);
    try {
      await api.addBulkEpisodes({
        source: selected.source,
        title: selected.title,
        slug: selected.slug,
        season,
        episodes: Array.from(picked),
        language,
        quality,
      });
      setMsg(`Erfolgreich ${picked.size} Episoden zur Warteschlange hinzugefügt!`);
      setPicked(new Set());
      
      const lib = await api.checkShow(selected.title);
      if (lib.found) setLibrarySeasons(lib.seasons);
    } catch (e: any) {
      setMsg(e.message || "Fehler beim Hinzufügen");
    } finally {
      setBusy(false);
    }
  };

  const enqueueSeason = async () => {
    if (!selected || !season) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.addSeason({
        source: selected.source,
        title: selected.title,
        slug: selected.slug,
        season,
        language,
        quality,
      });
      setMsg(`${res.count} Episoden hinzugefügt! (übersprungen: ${res.skipped})`);
      
      const lib = await api.checkShow(selected.title);
      if (lib.found) setLibrarySeasons(lib.seasons);
    } catch (e: any) {
      setMsg(e.message || "Fehler beim Hinzufügen");
    } finally {
      setBusy(false);
    }
  };

  const enqueueSeries = async () => {
    if (!selected) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.addSeries({
        source: selected.source,
        title: selected.title,
        slug: selected.slug,
        language,
        quality,
      });
      setMsg(`Serie hinzugefügt! ${res.count} neue Episoden in Warteschlange.`);
      
      const lib = await api.checkShow(selected.title);
      if (lib.found) setLibrarySeasons(lib.seasons);
    } catch (e: any) {
      setMsg(e.message || "Fehler beim Hinzufügen");
    } finally {
      setBusy(false);
    }
  };

  const enqueueMovie = async () => {
    if (!selected) return;
    setBusy(true);
    setMsg(null);
    try {
      await api.addItem({
        source: selected.source,
        kind: "movie",
        title: selected.title,
        url: selected.url,
        slug: selected.slug,
        language,
        quality,
      });
      setMsg(`Film "${selected.title}" zur Warteschlange hinzugefügt!`);
      setMovieOnDisk(true);
    } catch (e: any) {
      setMsg(e.message || "Fehler beim Hinzufügen");
    } finally {
      setBusy(false);
    }
  };

  const addToWatchlist = async (days: number | null) => {
    if (!selected || !season) return;
    setBusy(true);
    setMsg(null);
    try {
      await api.addWatch({
        source: selected.source,
        slug: selected.slug,
        title: selected.title,
        season,
        language,
        quality,
        poster: selected.poster,
        expires_in_days: days,
      });
      setMsg(t("add.addedToWatchlist"));
    } catch (e: any) {
      setMsg(e.message || "Fehler beim Hinzufügen zur Watchlist");
    } finally {
      setBusy(false);
    }
  };

  const isSeriesSource = (src: ItemSource): boolean => {
    const s = SOURCES.find((x) => x.key === src);
    if (!s) return false;
    if (s.kind === "series") return true;
    if (s.kind === "both") {
      return category === "series";
    }
    return false;
  };

  const getHosterLabel = (hosters: string[] | null | undefined): string => {
    if (!hosters || hosters.length === 0) return "";
    return hosters.slice(0, 3).join(", ") + (hosters.length > 3 ? "..." : "");
  };

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

  return (
    <div className="card" style={{ background: "transparent", border: "none", padding: 0 }}>
      {msg && <div style={{ marginBottom: 14, color: "var(--text-dim)", fontSize: 13 }}>{msg}</div>}

      {selected ? (
        // ================= SELECTED DETAILS VIEW =================
        <div className="fade-in">
          <button className="btn-text" style={{ marginBottom: 16 }} onClick={() => setSelected(null)}>
            &larr; Zurück zum Katalog
          </button>
          
          <div className="selected-show">
            {selected.poster ? (
              <img src={selected.poster} alt={selected.title} />
            ) : (
              <div className="poster-placeholder" style={{ width: 96, aspectRatio: "2/3", borderRadius: "var(--radius-sm)", background: "var(--bg-3)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                {isSeriesSource(selected.source as any) ? "📺" : "🎬"}
              </div>
            )}
            <div style={{ flex: 1 }}>
              <span className="meta" style={{ display: "inline-block", background: "var(--bg-4)", padding: "2px 8px", borderRadius: 4, fontSize: 10, color: "var(--text)", textTransform: "uppercase", fontWeight: 600 }}>{selected.source}</span>
              <h3 style={{ marginTop: 4 }}>{selected.title}</h3>
              {selected.year && <div className="result-year">Released: {selected.year}</div>}
            </div>
          </div>

          <div className="card" style={{ marginTop: 16 }}>
            {/* Options bar */}
            <div className="grid-3">
              <div>
                <label>{t("common.quality")}</label>
                <select
                  value={quality}
                  onChange={(e) => {
                    userTouchedQual.current = true;
                    setQuality(e.target.value);
                  }}
                >
                  <option value="480p">480p</option>
                  <option value="720p">720p</option>
                  <option value="1080p">1080p</option>
                  <option value="1440p">1440p</option>
                  <option value="4k">4K (2160p)</option>
                  <option value="best">Best Available</option>
                </select>
              </div>

              {!languageLocked && (
                <div>
                  <label>{t("common.language")}</label>
                  <select
                    value={language}
                    onChange={(e) => {
                      userTouchedLang.current = true;
                      setLanguage(e.target.value);
                    }}
                  >
                    <option value="de">{t("common.gerDub")}</option>
                    <option value="de-sub">{t("common.gerSub")}</option>
                    <option value="en">{t("common.engDub")}</option>
                    <option value="en-sub">{t("common.engSub")}</option>
                  </select>
                </div>
              )}
            </div>

            {/* Action Buttons depending on kind */}
            {isSeriesSource(selected.source as any) ? (
              <div style={{ marginTop: 20 }}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                  <h3 style={{ margin: 0 }}>{t("add.chooseSeason")}</h3>
                  <button onClick={enqueueSeries} disabled={busy}>
                    {t("add.addWholeSeries")}
                  </button>
                </div>

                {seasons.length > 0 && (
                  <div style={{ marginTop: 14 }}>
                    <div className="badge-row">
                      {seasons.map((s) => {
                        const libEps = librarySeasons[String(s)];
                        const hasAll = libEps && episodes.length > 0 && libEps.length >= episodes.length && season === s;
                        const hasSome = libEps && libEps.length > 0;
                        return (
                          <button
                            key={s}
                            className={season === s ? "primary" : ""}
                            onClick={() => selectSeason(s)}
                          >
                            {hasSome && (
                              <span className={`season-badge ${hasAll ? "complete" : "partial"}`} />
                            )}
                            {t("common.season")} {s}
                          </button>
                        );
                      })}
                    </div>

                    {/* Episode List */}
                    <div style={{ marginTop: 22 }}>
                      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                        <h4 style={{ margin: 0 }}>
                          {t("add.chooseEpisodes")}
                          {loadingLang && (
                            <span style={{ marginLeft: 10, fontSize: 12, color: "var(--text-dim)", fontWeight: "normal" }}>
                              <span className="spinner" style={{ marginRight: 6 }} />
                              {t("add.loadingLanguageInfo")}
                            </span>
                          )}
                        </h4>
                        <div className="row" style={{ gap: 8 }}>
                          <button
                            onClick={() => setPicked(new Set(episodes.filter((e) => e.has_language).map((e) => e.episode)))}
                          >
                            {t("add.selectAll")}
                          </button>
                          <button onClick={() => setPicked(new Set())}>
                            {t("add.deselectAll")}
                          </button>
                        </div>
                      </div>

                      {episodes.length === 0 && !loadingLang ? (
                        <div style={{ color: "var(--text-dim)", padding: "16px 0", textAlign: "center" }}>
                          Keine Episoden gefunden.
                        </div>
                      ) : (
                        <div className="episode-grid" style={{ marginTop: 12 }}>
                          {episodes.map((ep) => {
                            const alreadyHas = (librarySeasons[season!] || []).includes(ep.episode);
                            const isPicked = picked.has(ep.episode);
                            const isFallback = ep.has_language && ep.actual_language !== language;
                            
                            return (
                              <button
                                key={ep.episode}
                                className={
                                  (!ep.has_language
                                    ? "unavailable"
                                    : isPicked
                                    ? "selected"
                                    : "") + (alreadyHas ? " on-disk" : "")
                                  }
                                onClick={() => ep.has_language && !alreadyHas && toggleEpisode(ep.episode)}
                                disabled={alreadyHas || !ep.has_language}
                                title={
                                  alreadyHas
                                    ? t("add.alreadyOnDisk") as string
                                    : !ep.has_language
                                    ? t("add.notAvailableInLang") as string
                                    : isFallback
                                    ? t("add.fallbackLang", { lang: langLabel(ep.actual_language) }) as string
                                    : undefined
                                }
                              >
                                <span>
                                  {alreadyHas && <span className="ep-check">&#10003;</span>}
                                  E{String(ep.episode).padStart(2, "0")}
                                </span>
                                {isFallback && (
                                  <span style={{ display: "block", fontSize: 9, opacity: 0.7, marginTop: 2 }}>
                                    {langLabel(ep.actual_language)}
                                  </span>
                                )}
                              </button>
                            );
                          })}
                        </div>
                      )}

                      <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
                        <button onClick={enqueueSeason} disabled={busy}>
                          {t("add.addWholeSeason")}
                        </button>
                        {picked.size > 0 && (
                          <button
                            className="primary"
                            onClick={enqueueSelected}
                            disabled={busy}
                          >
                            {t("add.addSelected")} ({picked.size})
                          </button>
                        )}
                      </div>
                    </div>

                    {/* Season Watchlist subscription */}
                    <div style={{ marginTop: 24, padding: 14, border: "1px dashed var(--border)", borderRadius: 8 }}>
                      <h4 style={{ margin: "0 0 6px" }}>🔔 {t("add.watchSeasonHeading")}</h4>
                      <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 10 }}>{t("add.watchSeasonHint")}</div>
                      <div className="row wrap" style={{ gap: 8 }}>
                        <button onClick={() => addToWatchlist(7)} disabled={busy}>{t("add.watchFor7d")}</button>
                        <button onClick={() => addToWatchlist(30)} disabled={busy}>{t("add.watchFor30d")}</button>
                        <button onClick={() => addToWatchlist(90)} disabled={busy}>{t("add.watchFor90d")}</button>
                        <button onClick={() => addToWatchlist(null)} disabled={busy} className="primary">{t("add.watchForever")}</button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              // Movie Actions
              <div style={{ marginTop: 20 }}>
                {movieOnDisk ? (
                  <div className="card" style={{ borderColor: "var(--good)", color: "var(--good)", padding: 12, display: "inline-block" }}>
                    ✓ Dieser Film befindet sich bereits in deiner Plex-Bibliothek!
                  </div>
                ) : (
                  <button className="primary" onClick={enqueueMovie} disabled={busy}>
                    {busy ? t("common.loading") : t("add.addMovie")}
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      ) : (
        // ================= CATALOG GRID BROWSE VIEW =================
        <div className="fade-in">
          {/* Catalog Filter Bar */}
          <div className="card">
            <div className="grid-3">
              <div>
                <label>Quelle</label>
                <select className="select" value={source} onChange={(e) => setSource(e.target.value as any)}>
                  {SOURCES.map((s) => (
                    <option key={s.key} value={s.key}>{s.label}</option>
                  ))}
                </select>
              </div>

              <div>
                <label>Kategorie</label>
                <select className="select" value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }}>
                  {currentSource?.kind !== "series" && <option value="movies">Filme</option>}
                  {currentSource?.kind !== "movie" && <option value="series">Serien</option>}
                  <option value="popular">Beliebt / Trends</option>
                </select>
              </div>

              <div>
                <label>Sprach-Filter</label>
                <select className="select" value={languageFilter} onChange={(e) => { setLanguageFilter(e.target.value); setPage(1); }}>
                  <option value="all">Alle Sprachen</option>
                  <option value="de">Deutsch</option>
                  <option value="en">Englisch</option>
                </select>
              </div>

              <div>
                <label>Hoster-Filter</label>
                <select className="select" value={hosterFilter} onChange={(e) => { setHosterFilter(e.target.value); setPage(1); }}>
                  <option value="all">Alle Hoster</option>
                  <option value="VOE">VOE</option>
                  <option value="Vidmoly">Vidmoly</option>
                  <option value="Vidoza">Vidoza</option>
                  <option value="Doodstream">Doodstream</option>
                  <option value="Vidara">Vidara</option>
                  <option value="Vidsonic">Vidsonic</option>
                </select>
              </div>

              <div>
                <label>Sortierung</label>
                <select className="select" value={sortOption} onChange={(e) => setSortOption(e.target.value)}>
                  <option value="default">Standard (Relevanz)</option>
                  <option value="newest">Neueste (Jahr absteigend)</option>
                  <option value="title">Alphabetisch (A-Z)</option>
                </select>
              </div>
            </div>

            <div className="row" style={{ marginTop: 16, justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                Seite {page} • {results.length} Einträge geladen
              </span>
              <button onClick={() => loadCatalog()} disabled={busy}>
                {busy ? "Lädt..." : "Aktualisieren"}
              </button>
            </div>
          </div>

          {/* Catalog Grid Results */}
          {busy && results.length === 0 ? (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "80px 0" }}>
              <span className="spinner" style={{ width: 24, height: 24 }} />
              <p style={{ color: "var(--text-dim)", marginTop: 12 }}>Katalog wird geladen...</p>
            </div>
          ) : results.length === 0 ? (
            <div className="card" style={{ textAlign: "center", padding: 40, color: "var(--text-dim)", marginTop: 16 }}>
              Keine Filme oder Serien gefunden. Versuche andere Filtereinstellungen.
            </div>
          ) : (
            <div style={{ marginTop: 16 }}>
              <div className="result-grid">
                {results.map((res, idx) => {
                  const status = resultLibStatus[res.url];
                  return (
                    <div
                      key={res.url + idx}
                      className="result-card"
                      onClick={() => handleSelect(res)}
                    >
                      <div className="poster-wrap">
                        {res.poster ? (
                          <img src={res.poster} alt={res.title} loading="lazy" />
                        ) : (
                          <div className="poster-placeholder">
                            {isSeriesSource(res.source as any) ? "📺" : "🎬"}
                          </div>
                        )}
                        {status && (
                          <span className={`download-badge ${status}`} />
                        )}
                      </div>

                      <div className="result-body">
                        <div className="result-title" title={res.title}>{res.title}</div>
                        <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginTop: 4 }}>
                          <span className="meta">{res.source}</span>
                          {res.year && <span className="result-year">({res.year})</span>}
                        </div>
                        {res.language && (
                          <div className="meta" style={{ marginTop: 2 }}>
                            Sprache: {langLabel(res.language)}
                          </div>
                        )}
                        {res.hosters && res.hosters.length > 0 && (
                          <div className="meta" style={{ marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={res.hosters.join(", ")}>
                            Hoster: {getHosterLabel(res.hosters)}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Catalog Pagination Controls */}
              {source && (
                <div className="row" style={{ justifyContent: "center", alignItems: "center", gap: 16, marginTop: 24 }}>
                  <button onClick={() => { setPage((p) => Math.max(1, p - 1)); setSelected(null); }} disabled={page === 1 || busy}>
                    &larr; Zurück
                  </button>
                  <span style={{ fontWeight: 600, fontSize: 16 }}>Seite {page}</span>
                  <button onClick={() => { setPage((p) => p + 1); setSelected(null); }} disabled={busy || results.length < 10}>
                    Vorwärts &rarr;
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
