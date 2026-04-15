import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { QueueView } from "./components/QueueView";
import { AddView } from "./components/AddView";
import { LogsView } from "./components/LogsView";
import { SettingsView } from "./components/SettingsView";
import { UpcomingView } from "./components/UpcomingView";
import { WatchlistView } from "./components/WatchlistView";
import { api, QueueItem, openEventsSocket } from "./api";

type Tab = "queue" | "add" | "watchlist" | "upcoming" | "logs" | "settings";

const TAB_ORDER: Tab[] = ["queue", "add", "watchlist", "upcoming", "logs", "settings"];

const TAB_GLYPH: Record<Tab, string> = {
  queue: "◆",
  add: "＋",
  watchlist: "◉",
  upcoming: "△",
  logs: "≡",
  settings: "✱",
};

export default function App() {
  const { t, i18n } = useTranslation();
  const [tab, setTab] = useState<Tab>("queue");
  const [items, setItems] = useState<QueueItem[]>([]);
  const [theme, setTheme] = useState<"dark" | "light">(
    (localStorage.getItem("h0melab.theme") as any) || "dark"
  );
  const [info, setInfo] = useState<any>(null);
  const [clock, setClock] = useState<string>(() => new Date().toLocaleTimeString());
  const [navCollapsed, setNavCollapsed] = useState(false);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("h0melab.theme", theme);
  }, [theme]);

  useEffect(() => {
    api.info().then(setInfo).catch(() => {});
    api.listQueue().then(setItems).catch(() => {});

    const ws = openEventsSocket((msg) => {
      if (msg.event === "queue.snapshot") {
        setItems(msg.data);
      } else if (msg.event === "queue.added") {
        setItems((prev) => [...prev.filter((p) => p.id !== msg.data.id), msg.data]);
      } else if (msg.event === "queue.updated") {
        setItems((prev) =>
          prev.map((p) => (p.id === msg.data.id ? { ...p, ...msg.data } : p))
        );
      } else if (msg.event === "queue.removed") {
        setItems((prev) => prev.filter((p) => p.id !== msg.data.id));
      } else if (msg.event === "queue.reordered") {
        api.listQueue().then(setItems).catch(() => {});
      }
    });

    const clockTimer = setInterval(
      () => setClock(new Date().toLocaleTimeString()),
      1000
    );

    return () => {
      clearInterval(clockTimer);
      try {
        ws.close();
      } catch {}
    };
  }, []);

  const counts = {
    running: items.filter((i) =>
      ["scraping", "downloading", "processing"].includes(i.status)
    ).length,
    waiting: items.filter((i) => i.status === "queued").length,
    completed: items.filter((i) => i.status === "completed").length,
    failed: items.filter((i) => i.status === "failed").length,
  };

  return (
    <div className={"app" + (navCollapsed ? " nav-collapsed" : "")}>
      {/* Ambient background layers */}
      <div className="bg-mesh" aria-hidden />
      <div className="bg-grain" aria-hidden />

      {/* === SIDEBAR === */}
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">H0</div>
          <div className="brand-wordmark">
            <span className="brand-line-1">H<i>0</i>melab</span>
            <span className="brand-line-2">// signal stream</span>
          </div>
        </div>

        <nav className="sidenav">
          {TAB_ORDER.map((k, i) => (
            <button
              key={k}
              className={"sidenav-item " + (tab === k ? "is-active" : "")}
              onClick={() => setTab(k)}
            >
              <span className="sidenav-index">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="sidenav-glyph">{TAB_GLYPH[k]}</span>
              <span className="sidenav-label">{t(`nav.${k}`)}</span>
              <span className="sidenav-bar" aria-hidden />
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="mini-stats">
            <div className="mini-stat">
              <span className="mini-stat-val">{counts.running}</span>
              <span className="mini-stat-lbl">RUN</span>
            </div>
            <div className="mini-stat">
              <span className="mini-stat-val">{counts.waiting}</span>
              <span className="mini-stat-lbl">WAIT</span>
            </div>
            <div className="mini-stat">
              <span className="mini-stat-val">{counts.completed}</span>
              <span className="mini-stat-lbl">DONE</span>
            </div>
          </div>
          <button
            className="sidebar-toggle"
            onClick={() => setNavCollapsed((p) => !p)}
            aria-label="Toggle sidebar"
          >
            {navCollapsed ? "»" : "«"}
          </button>
        </div>
      </aside>

      {/* === MAIN COLUMN === */}
      <div className="stage">
        {/* Top HUD bar */}
        <header className="hud">
          <div className="hud-left">
            <span className="hud-dot" data-kind="live" />
            <span className="hud-label">TRANSMISSION</span>
            <span className="hud-sep">/</span>
            <span className="hud-value">{t(`nav.${tab}`).toUpperCase()}</span>
          </div>

          <div className="hud-center">
            <span className="hud-chip">
              <em>{counts.running}</em> running
            </span>
            <span className="hud-chip">
              <em>{counts.waiting}</em> queued
            </span>
            <span className="hud-chip done">
              <em>{counts.completed}</em> done
            </span>
            {counts.failed > 0 && (
              <span className="hud-chip fail">
                <em>{counts.failed}</em> failed
              </span>
            )}
          </div>

          <div className="hud-right">
            <span className="hud-clock">{clock}</span>
            <select
              className="hud-select"
              value={i18n.language.startsWith("de") ? "de" : "en"}
              onChange={(e) => i18n.changeLanguage(e.target.value)}
              aria-label={t("common.language")}
            >
              <option value="de">DE</option>
              <option value="en">EN</option>
            </select>
            <button
              className="hud-theme"
              onClick={() => setTheme((p) => (p === "dark" ? "light" : "dark"))}
              title="Toggle theme"
            >
              {theme === "dark" ? "☀" : "☾"}
            </button>
          </div>
        </header>

        {/* Content */}
        <main>
          <div className="page-head">
            <span className="page-kicker">
              SECTION {String(TAB_ORDER.indexOf(tab) + 1).padStart(2, "0")}
              <span className="page-kicker-bar" />
            </span>
            <h1 className="page-title">{t(`nav.${tab}`)}</h1>
          </div>

          {tab === "queue" && <QueueView items={items} counts={counts} />}
          {tab === "add" && <AddView />}
          {tab === "watchlist" && <WatchlistView />}
          {tab === "upcoming" && (
            <UpcomingView tmdbConfigured={!!info?.configured?.tmdb} />
          )}
          {tab === "logs" && <LogsView />}
          {tab === "settings" && <SettingsView />}
        </main>

        {info?.homelab_credit && (
          <footer>
            <span className="foot-tick">◆</span>
            {t("footer.credit")}
            <span className="foot-tick">◆</span>
          </footer>
        )}
      </div>
    </div>
  );
}
