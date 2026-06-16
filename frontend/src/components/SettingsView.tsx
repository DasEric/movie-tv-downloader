import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";

// Field names that hold secrets — handled with empty input + status badge
const SECRET_KEYS = [
  "tmdb_api_key",
  "discord_webhook_url",
  "telegram_bot_token",
  "discord_token",
] as const;

type DiscordStatus = { running: boolean; error: string | null };

export function SettingsView() {
  const { t } = useTranslation();
  const [form, setForm] = useState<any>(null);
  // Tracks which secret fields are currently set on the server (so we can
  // show a "configured" badge without ever leaking the value to the DOM).
  const [secretSet, setSecretSet] = useState<Record<string, boolean>>({});
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [discordStatus, setDiscordStatus] = useState<DiscordStatus | null>(null);

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

  // Poll discord bot status every 5s while the settings view is mounted.
  useEffect(() => {
    let cancelled = false;
    const fetchStatus = async () => {
      try {
        const r = await fetch("/api/discord/status");
        if (!r.ok) return;
        const s = (await r.json()) as DiscordStatus;
        if (!cancelled) setDiscordStatus(s);
      } catch {
        /* ignore */
      }
    };
    fetchStatus();
    const iv = setInterval(fetchStatus, 5000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
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
      </div>

      <h3>{t("settings.scheduler")}</h3>
      <div className="grid-2">
        <div>
          <label>{t("settings.releaseCheck")}</label>
          <input
            type="number"
            min={5}
            max={1440}
            value={form.release_check_interval_min ?? 60}
            onChange={(e) =>
              set(
                "release_check_interval_min",
                Math.max(5, Number(e.target.value) || 60)
              )
            }
          />
          <div
            style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}
          >
            {t("settings.releaseCheckHint")}
          </div>
        </div>
        <div>
          <label>&nbsp;</label>
          <div
            style={{
              fontSize: 12,
              color: "var(--text-dim)",
              padding: "10px 0",
            }}
          >
            {t("settings.schedulerExplanation")}
          </div>
        </div>
      </div>

      <h3>
        {t("settings.discord.section_title")}
        {discordStatus && (
          <span
            className={
              discordStatus.running
                ? "tag completed"
                : discordStatus.error
                ? "tag failed"
                : "tag"
            }
            style={{ marginLeft: 10, fontSize: 12 }}
          >
            {discordStatus.running
              ? t("settings.discord.status_running")
              : discordStatus.error
              ? t("settings.discord.status_error")
              : t("settings.discord.status_stopped")}
          </span>
        )}
      </h3>

      <div style={{ marginBottom: 14 }}>
        <label>
          <input
            type="checkbox"
            checked={!!form.discord_bot_enabled}
            onChange={(e) => set("discord_bot_enabled", e.target.checked)}
          />{" "}
          {t("settings.discord.enable")}
        </label>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
          {t("settings.discord.enable_hint")}
        </div>
      </div>

      <div style={{ marginBottom: 14 }}>
        <label>{t("settings.discord.mode_label")}</label>
        <div className="row" style={{ gap: 14, flexWrap: "wrap" }}>
          <label style={{ fontWeight: "normal" }}>
            <input
              type="radio"
              name="discord_mode"
              value="standard"
              disabled={!form.discord_bot_enabled}
              checked={form.discord_mode === "standard"}
              onChange={(e) => set("discord_mode", e.target.value)}
            />{" "}
            {t("settings.discord.mode_standard")}
          </label>
          <label style={{ fontWeight: "normal" }}>
            <input
              type="radio"
              name="discord_mode"
              value="advanced"
              disabled={!form.discord_bot_enabled}
              checked={form.discord_mode === "advanced"}
              onChange={(e) => set("discord_mode", e.target.value)}
            />{" "}
            {t("settings.discord.mode_advanced")}
          </label>
        </div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
          {form.discord_mode === "advanced"
            ? t("settings.discord.mode_advanced_hint")
            : t("settings.discord.mode_standard_hint")}
        </div>
      </div>

      <div className="grid-2">
        <div>
          <label>
            {t("settings.discord.token")}
            {secretSet.discord_token && (
              <span className="tag completed" style={{ marginLeft: 8 }}>
                {t("settings.configured")}
              </span>
            )}
          </label>
          <input
            type="password"
            disabled={!form.discord_bot_enabled}
            value={form.discord_token || ""}
            onChange={(e) => set("discord_token", e.target.value)}
            placeholder={
              secretSet.discord_token
                ? t("settings.leaveBlank")
                : t("settings.discord.token_hint")
            }
            autoComplete="off"
          />
        </div>
        <div>
          <label>{t("settings.discord.owner_id")}</label>
          <input
            type="text"
            disabled={!form.discord_bot_enabled}
            value={form.discord_owner_id || ""}
            onChange={(e) => set("discord_owner_id", e.target.value)}
            placeholder="123456789012345678"
          />
        </div>
        <div>
          <label>{t("settings.discord.upload_channel_id")}</label>
          <input
            type="text"
            disabled={!form.discord_bot_enabled}
            value={form.discord_upload_channel_id || ""}
            onChange={(e) => set("discord_upload_channel_id", e.target.value)}
            placeholder="123456789012345678"
          />
        </div>
        <div>
          <label>{t("settings.discord.request_role_id")}</label>
          <input
            type="text"
            disabled={!form.discord_bot_enabled}
            value={form.discord_request_role_id || ""}
            onChange={(e) => set("discord_request_role_id", e.target.value)}
            placeholder="123456789012345678"
          />
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            {t("settings.discord.request_role_hint")}
          </div>
        </div>
        <div>
          <label>{t("settings.discord.guild_id")}</label>
          <input
            type="text"
            disabled={!form.discord_bot_enabled}
            value={form.discord_guild_id || ""}
            onChange={(e) => set("discord_guild_id", e.target.value)}
            placeholder="123456789012345678"
          />
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            {t("settings.discord.guild_hint")}
          </div>
        </div>
        <div>
          <label>{t("settings.discord.cloudflareFallback")}</label>
          <select
            value={form.cloudflare_fallback_enabled ? "yes" : "no"}
            onChange={(e) =>
              set("cloudflare_fallback_enabled", e.target.value === "yes")
            }
          >
            <option value="yes">{t("common.yes")}</option>
            <option value="no">{t("common.no")}</option>
          </select>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            {t("settings.discord.cloudflareFallbackHint")}
          </div>
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
