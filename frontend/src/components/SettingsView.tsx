import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";

// Field names that hold secrets — handled with empty input + status badge
const SECRET_KEYS = [
  "tmdb_api_key",
  "discord_webhook_url",
  "telegram_bot_token",
] as const;

export function SettingsView() {
  const { t } = useTranslation();
  const [form, setForm] = useState<any>(null);
  // Tracks which secret fields are currently set on the server (so we can
  // show a "configured" badge without ever leaking the value to the DOM).
  const [secretSet, setSecretSet] = useState<Record<string, boolean>>({});
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .getSettings()
      .then((data) => {
        // Capture *_set flags first, then strip the masked-bullet values
        // so they never appear in the form state.
        const flags: Record<string, boolean> = {};
        const cleaned = { ...data };
        for (const k of SECRET_KEYS) {
          flags[k] = !!data[`${k}_set`];
          cleaned[k] = ""; // never display the masked bullets
        }
        setSecretSet(flags);
        setForm(cleaned);
      })
      .catch(() => {});
  }, []);

  if (!form) return <div className="card">{t("common.loading")}</div>;

  const set = (k: string, v: any) => setForm((p: any) => ({ ...p, [k]: v }));

  const save = async () => {
    setSaving(true);
    const payload: any = { ...form };

    // Don't send empty strings for secrets — that would mean "no change"
    // when the field is already configured server-side.
    for (const k of SECRET_KEYS) {
      if (payload[k] === "" || payload[k] == null) {
        delete payload[k];
      }
    }

    if (typeof payload.hoster_priority === "string") {
      payload.hoster_priority = payload.hoster_priority
        .split(",")
        .map((x: string) => x.trim())
        .filter(Boolean);
    }
    if (typeof payload.subtitle_languages === "string") {
      payload.subtitle_languages = payload.subtitle_languages
        .split(",")
        .map((x: string) => x.trim())
        .filter(Boolean);
    }

    try {
      const updated = await api.updateSettings(payload);
      const flags: Record<string, boolean> = {};
      const cleaned = { ...updated };
      for (const k of SECRET_KEYS) {
        flags[k] = !!updated[`${k}_set`];
        cleaned[k] = "";
      }
      setSecretSet(flags);
      setForm(cleaned);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card">
      <h2>{t("settings.heading")}</h2>

      <h3>{t("settings.general")}</h3>
      <div className="grid-3">
        <div>
          <label>{t("settings.concurrency")}</label>
          <input
            type="number"
            min={1}
            max={20}
            value={form.concurrency}
            onChange={(e) => set("concurrency", Number(e.target.value))}
          />
        </div>
        <div>
          <label>{t("settings.defaultLanguage")}</label>
          <select
            value={form.default_language}
            onChange={(e) => set("default_language", e.target.value)}
          >
            <option value="de">Deutsch</option>
            <option value="en">English</option>
          </select>
        </div>
        <div>
          <label>{t("settings.qualityProfile")}</label>
          <select
            value={form.quality_profile}
            onChange={(e) => set("quality_profile", e.target.value)}
          >
            <option>480p</option>
            <option>720p</option>
            <option>1080p</option>
            <option>1440p</option>
            <option>4k</option>
            <option>best</option>
          </select>
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <label>{t("settings.hosterPriority")}</label>
        <input
          type="text"
          value={
            Array.isArray(form.hoster_priority)
              ? form.hoster_priority.join(", ")
              : form.hoster_priority
          }
          onChange={(e) => set("hoster_priority", e.target.value)}
        />
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
          {t("settings.hosterHint")}
        </div>
      </div>

      <h3>{t("settings.apiKeys")}</h3>
      <div className="grid-2">
        <div>
          <label>
            {t("settings.tmdbApiKey")}
            {secretSet.tmdb_api_key && (
              <span className="tag completed" style={{ marginLeft: 8 }}>
                {t("settings.configured")}
              </span>
            )}
          </label>
          <input
            type="password"
            value={form.tmdb_api_key || ""}
            onChange={(e) => set("tmdb_api_key", e.target.value)}
            placeholder={
              secretSet.tmdb_api_key
                ? t("settings.leaveBlank")
                : t("settings.tmdbHint")
            }
            autoComplete="off"
          />
        </div>
        <div>
          <label>{t("settings.proxyUrl")}</label>
          <input
            type="text"
            value={form.proxy_url || ""}
            onChange={(e) => set("proxy_url", e.target.value)}
            placeholder="socks5://host:port"
          />
        </div>
        <div>
          <label>
            {t("settings.discordWebhook")}
            {secretSet.discord_webhook_url && (
              <span className="tag completed" style={{ marginLeft: 8 }}>
                {t("settings.configured")}
              </span>
            )}
          </label>
          <input
            type="password"
            value={form.discord_webhook_url || ""}
            onChange={(e) => set("discord_webhook_url", e.target.value)}
            placeholder={secretSet.discord_webhook_url ? t("settings.leaveBlank") : ""}
            autoComplete="off"
          />
        </div>
        <div>
          <label>
            {t("settings.telegramToken")}
            {secretSet.telegram_bot_token && (
              <span className="tag completed" style={{ marginLeft: 8 }}>
                {t("settings.configured")}
              </span>
            )}
          </label>
          <input
            type="password"
            value={form.telegram_bot_token || ""}
            onChange={(e) => set("telegram_bot_token", e.target.value)}
            placeholder={secretSet.telegram_bot_token ? t("settings.leaveBlank") : ""}
            autoComplete="off"
          />
        </div>
        <div>
          <label>{t("settings.telegramChatId")}</label>
          <input
            type="text"
            value={form.telegram_chat_id || ""}
            onChange={(e) => set("telegram_chat_id", e.target.value)}
          />
        </div>
        <div>
          <label>{t("settings.releaseCheck")}</label>
          <input
            type="number"
            min={5}
            max={1440}
            value={form.release_check_interval_min}
            onChange={(e) => set("release_check_interval_min", Number(e.target.value))}
          />
        </div>
      </div>

      <h3>{t("settings.subtitles")}</h3>
      <div className="grid-2">
        <div>
          <label>{t("settings.autoSubtitles")}</label>
          <select
            value={form.auto_subtitles ? "yes" : "no"}
            onChange={(e) => set("auto_subtitles", e.target.value === "yes")}
          >
            <option value="yes">{t("common.yes")}</option>
            <option value="no">{t("common.no")}</option>
          </select>
        </div>
        <div>
          <label>{t("settings.subtitleLanguages")}</label>
          <input
            type="text"
            value={
              Array.isArray(form.subtitle_languages)
                ? form.subtitle_languages.join(", ")
                : form.subtitle_languages
            }
            onChange={(e) => set("subtitle_languages", e.target.value)}
          />
        </div>
      </div>

      <div className="row" style={{ marginTop: 20, justifyContent: "flex-end" }}>
        {saved && <span style={{ color: "var(--success)" }}>{t("settings.saved")}</span>}
        <button className="primary" onClick={save} disabled={saving}>
          {t("common.save")}
        </button>
      </div>
    </div>
  );
}
