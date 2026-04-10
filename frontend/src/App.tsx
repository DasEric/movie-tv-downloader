import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { QueueView } from "./components/QueueView";
import { AddView } from "./components/AddView";
import { LogsView } from "./components/LogsView";
import { SettingsView } from "./components/SettingsView";
import { api, QueueItem, openEventsSocket } from "./api";

type Tab = "queue" | "add" | "logs" | "settings";

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
      <nav className="nav">
        <div className="logo">
          H<span>0</span>melab Downloader
        </div>
        <div className="tabs">
          {(["queue", "add", "logs", "settings"] as Tab[]).map((k) => (
            <button
              key={k}
              className={"tab " + (tab === k ? "active" : "")}
              onClick={() => setTab(k)}
            >
              {t(`nav.${k}`)}
            </button>
          ))}
        </div>
        <div className="right">
          <select
            value={i18n.language.startsWith("de") ? "de" : "en"}
            onChange={(e) => i18n.changeLanguage(e.target.value)}
            aria-label={t("common.language")}
          >
            <option value="de">Deutsch</option>
            <option value="en">English</option>
          </select>
          <button
            className="ghost"
            onClick={() => setTheme((p) => (p === "dark" ? "light" : "dark"))}
            title="Toggle theme"
          >
            {theme === "dark" ? "☀" : "☾"}
          </button>
        </div>
      </nav>

      <main>
        {tab === "queue" && <QueueView items={items} counts={counts} />}
        {tab === "add" && <AddView />}
        {tab === "logs" && <LogsView />}
        {tab === "settings" && <SettingsView />}
      </main>

      {info?.homelab_credit && (
        <footer>{t("footer.credit")}</footer>
      )}
    </div>
  );
}
