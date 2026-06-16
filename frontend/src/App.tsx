import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { QueueView } from "./components/QueueView";
import { AddView } from "./components/AddView";
import { CatalogView } from "./components/CatalogView";
import { LogsView } from "./components/LogsView";
import { SettingsView } from "./components/SettingsView";
import { UpcomingView } from "./components/UpcomingView";
import { WatchlistView } from "./components/WatchlistView";
import { api, QueueItem, openEventsSocket } from "./api";

type Tab = "queue" | "add" | "catalog" | "watchlist" | "upcoming" | "logs" | "settings";

const TAB_ORDER: Tab[] = ["queue", "add", "catalog", "watchlist", "upcoming", "logs", "settings"];

// Simple inline icon set — single weight, consistent stroke
function Icon({ name }: { name: Tab }) {
  const common = {
    width: 18,
    height: 18,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (name) {
    case "queue":
      return (
        <svg {...common}>
          <path d="M4 6h16M4 12h16M4 18h10" />
        </svg>
      );
    case "add":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8v8M8 12h8" />
        </svg>
      );
    case "watchlist":
      return (
        <svg {...common}>
          <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      );
    case "upcoming":
      return (
        <svg {...common}>
          <rect x="3" y="5" width="18" height="16" rx="2" />
          <path d="M16 3v4M8 3v4M3 10h18" />
        </svg>
      );
    case "logs":
      return (
        <svg {...common}>
          <path d="M4 4h12l4 4v12H4z" />
          <path d="M16 4v4h4" />
          <path d="M8 13h8M8 17h5" />
        </svg>
      );
    case "settings":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
        </svg>
      );
    case "catalog":
      return (
        <svg {...common}>
          <rect x="3" y="3" width="7" height="9" rx="1" />
          <rect x="14" y="3" width="7" height="5" rx="1" />
          <rect x="14" y="12" width="7" height="9" rx="1" />
          <rect x="3" y="16" width="7" height="5" rx="1" />
        </svg>
      );
  }
}

function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="H0melab Downloader"
    >
      <rect width="64" height="64" rx="14" fill="var(--brand-mark-bg)" />
      <path
        d="M32 14l14 8v20l-14 8-14-8V22z"
        fill="none"
        stroke="var(--brand-accent)"
        strokeWidth="3"
        strokeLinejoin="round"
      />
      <path d="M26 26v12l12-6z" fill="var(--brand-accent)" />
    </svg>
  );
}

export default function App() {
  const { t, i18n } = useTranslation();
  const [tab, setTab] = useState<Tab>("queue");
  const [items, setItems] = useState<QueueItem[]>([]);
  const [theme, setTheme] = useState<"dark" | "light">(
    (localStorage.getItem("h0melab.theme") as any) || "dark"
  );
  const [info, setInfo] = useState<any>(null);

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

    return () => {
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
    <div className="app">
      <div className="bg-glow" aria-hidden />

      {/* ============ SIDEBAR ============ */}
      <aside className="sidebar">
        <div className="brand">
          <Logo size={36} />
          <div className="brand-text">
            <span className="brand-name">
              H<span className="brand-name-accent">0</span>melab
            </span>
            <span className="brand-sub">Downloader</span>
          </div>
        </div>

        <nav className="sidenav">
          {TAB_ORDER.map((k) => (
            <button
              key={k}
              className={"sidenav-item " + (tab === k ? "is-active" : "")}
              onClick={() => setTab(k)}
            >
              <span className="sidenav-icon">
                <Icon name={k} />
              </span>
              <span className="sidenav-label">{t(`nav.${k}`)}</span>
              {k === "queue" && counts.running > 0 && (
                <span className="sidenav-badge">{counts.running}</span>
              )}
              {k === "queue" && counts.running === 0 && counts.waiting > 0 && (
                <span className="sidenav-badge dim">{counts.waiting}</span>
              )}
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <button
            className="chip-btn"
            onClick={() => setTheme((p) => (p === "dark" ? "light" : "dark"))}
            title="Toggle theme"
            aria-label="Toggle theme"
          >
            {theme === "dark" ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
              </svg>
            )}
          </button>
          <select
            className="chip-select"
            value={i18n.language.startsWith("de") ? "de" : "en"}
            onChange={(e) => i18n.changeLanguage(e.target.value)}
            aria-label={t("common.language")}
          >
            <option value="de">DE</option>
            <option value="en">EN</option>
          </select>
        </div>
      </aside>

      {/* ============ MAIN ============ */}
      <div className="stage">
        <header className="topbar">
          <div className="topbar-title">
            <h1>{t(`nav.${tab}`)}</h1>
            <span className="topbar-sub">
              {tab === "queue" && t("queue.heading")}
              {tab === "add" && t("add.heading")}
              {tab === "catalog" && "Mediatheken & Kataloge durchstöbern"}
              {tab === "watchlist" && t("watchlist.heading")}
              {tab === "upcoming" && t("upcoming.heading")}
              {tab === "logs" && t("logs.heading")}
              {tab === "settings" && t("settings.heading")}
            </span>
          </div>

          <div className="topbar-stats">
            <Stat label={t("queue.running", { count: counts.running })} value={counts.running} tone={counts.running > 0 ? "live" : "muted"} />
            <Stat label={t("queue.waiting", { count: counts.waiting })} value={counts.waiting} tone="muted" />
            <Stat label={t("queue.completed", { count: counts.completed })} value={counts.completed} tone="good" />
            {counts.failed > 0 && (
              <Stat label={t("queue.failed", { count: counts.failed })} value={counts.failed} tone="bad" />
            )}
          </div>
        </header>

        <main>
          {tab === "queue" && <QueueView items={items} counts={counts} />}
          {tab === "add" && <AddView />}
          {tab === "catalog" && <CatalogView />}
          {tab === "watchlist" && <WatchlistView />}
          {tab === "upcoming" && (
            <UpcomingView tmdbConfigured={!!info?.configured?.tmdb} />
          )}
          {tab === "logs" && <LogsView />}
          {tab === "settings" && <SettingsView />}
        </main>

        {info?.homelab_credit && <footer>{t("footer.credit")}</footer>}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "live" | "muted" | "good" | "bad";
}) {
  return (
    <div className={`stat stat-${tone}`}>
      <span className="stat-dot" />
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label.replace(/\d+\s*/, "").trim()}</span>
    </div>
  );
}
