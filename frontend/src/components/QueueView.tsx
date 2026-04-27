import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, QueueItem } from "../api";

interface Props {
  items: QueueItem[];
  counts: { running: number; waiting: number; completed: number; failed: number };
}

export function QueueView({ items, counts }: Props) {
  const { t } = useTranslation();

  if (items.length === 0) {
    return (
      <div className="card">
        <h2>{t("queue.heading")}</h2>
        <div className="empty-state">{t("common.empty")}</div>
      </div>
    );
  }

  const hasActive = counts.running > 0 || counts.waiting > 0;
  const hasPaused = items.some((i) => i.status === "paused");
  const hasRetryable = items.some(
    (i) =>
      i.status === "failed" ||
      (i.status === "paused" && i.message?.startsWith("[RATE_LIMITED]"))
  );

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{t("queue.heading")}</h2>
        <div className="badge-row">
          <span className="tag downloading">{t("queue.running", { count: counts.running })}</span>
          <span className="tag queued">{t("queue.waiting", { count: counts.waiting })}</span>
          <span className="tag completed">{t("queue.completed", { count: counts.completed })}</span>
          {counts.failed > 0 && (
            <span className="tag failed">{t("queue.failed", { count: counts.failed })}</span>
          )}
        </div>
      </div>

      {/* Bulk controls */}
      <div
        className="row wrap"
        style={{ marginTop: 14, gap: 8, justifyContent: "flex-start" }}
      >
        <button
          disabled={!hasActive}
          onClick={async () => {
            await api.pauseAll();
          }}
          title={t("queue.pauseAllHint") as string}
        >
          ⏸ {t("queue.pauseAll")}
        </button>
        <button
          disabled={!hasPaused}
          onClick={async () => {
            await api.resumeAll();
          }}
        >
          ▶ {t("queue.resumeAll")}
        </button>
        <button
          className="danger"
          disabled={!hasActive}
          onClick={async () => {
            if (confirm(t("queue.confirmStopAll"))) {
              await api.stopAll();
            }
          }}
          title={t("queue.stopAllHint") as string}
        >
          ⏹ {t("queue.stopAll")}
        </button>
        <button
          disabled={!hasRetryable}
          onClick={async () => {
            await api.retryAll();
          }}
        >
          {t("queue.retryAll")}
        </button>
        <div style={{ flex: 1 }} />
        <button
          className="danger"
          onClick={async () => {
            await api.clearCompleted();
          }}
        >
          {t("common.clearCompleted")}
        </button>
      </div>

      <div style={{ marginTop: 16 }} className="queue-list">
        {items
          .slice()
          .sort((a, b) => a.order_index - b.order_index || a.id - b.id)
          .map((it) => (
            <QueueRow key={it.id} item={it} />
          ))}
      </div>
    </div>
  );
}

function QueueRow({ item }: { item: QueueItem }) {
  const { t } = useTranslation();
  const [showCaptchaWarning, setShowCaptchaWarning] = useState(false);

  const label =
    item.kind === "movie"
      ? `🎬 ${item.title}`
      : `📺 ${item.title} S${String(item.season).padStart(2, "0")}E${String(
          item.episode
        ).padStart(2, "0")}`;

  const isRateLimited = item.message?.startsWith("[RATE_LIMITED]") ?? false;
  const displayMessage = isRateLimited
    ? item.message!.replace("[RATE_LIMITED] ", "")
    : item.message;

  // Build s.to episode URL for the "open in browser" link
  const stoUrl =
    isRateLimited && item.source === "s.to" && item.slug && item.season && item.episode
      ? `https://s.to/serie/stream/${item.slug}/staffel-${item.season}/episode-${item.episode}`
      : isRateLimited && item.source === "s.to"
      ? "https://s.to"
      : null;

  return (
    <div className="queue-item">
      <div>
        <div className="title-row">
          <span className="title">{label}</span>
          <span className={`tag ${isRateLimited ? "warning" : item.status}`}>
            {isRateLimited ? t("queue.status.rate_limited") : t(`queue.status.${item.status}`)}
          </span>
          <span className="tag">{item.source}</span>
          <span className="tag">{item.language}</span>
          <span className="tag">{item.quality}</span>
          {item.current_hoster && (
            <span className="tag">
              {t("queue.hoster")}: {item.current_hoster}
            </span>
          )}
        </div>

        {(item.status === "downloading" || item.status === "processing" || item.status === "scraping") && (
          <div className="progress-line">
            <div className="progressbar">
              <div style={{ width: `${item.progress || 0}%` }} />
            </div>
            <span className="pct">{(item.progress || 0).toFixed(1)}%</span>
            {item.speed && <span className="meta">{item.speed}</span>}
            {item.eta && (
              <span className="meta">
                {t("queue.eta")}: {item.eta}
              </span>
            )}
          </div>
        )}

        {isRateLimited && (
          <div
            className="rate-limit-box"
            style={{
              marginTop: 8,
              padding: 10,
              background: "rgba(245, 158, 11, 0.1)",
              border: "1px solid var(--warning)",
              borderRadius: 6,
              fontSize: 13,
            }}
          >
            <div style={{ marginBottom: 6 }}>{displayMessage}</div>
            <div className="row" style={{ gap: 8 }}>
              {stoUrl && (
                <button
                  className="btn-link"
                  style={{
                    display: "inline-block",
                    padding: "4px 10px",
                    background: "var(--warning)",
                    color: "#000",
                    borderRadius: 4,
                    border: "none",
                    cursor: "pointer",
                    fontSize: 12,
                  }}
                  onClick={() => setShowCaptchaWarning(true)}
                >
                  {t("queue.openInBrowser")}
                </button>
              )}
              <button
                style={{ fontSize: 12, padding: "4px 10px" }}
                onClick={() => api.resume(item.id)}
              >
                {t("common.retry")}
              </button>
            </div>
          </div>
        )}

        {showCaptchaWarning && stoUrl && (
          <div className="modal-backdrop" onClick={() => setShowCaptchaWarning(false)}>
            <div className="modal-box" onClick={(e) => e.stopPropagation()}>
              <h3 style={{ color: "var(--warning)", marginTop: 0 }}>
                {t("queue.captchaWarning.title")}
              </h3>
              <div
                style={{
                  background: "rgba(245, 158, 11, 0.15)",
                  border: "2px solid var(--warning)",
                  borderRadius: 8,
                  padding: "14px 16px",
                  marginBottom: 16,
                  fontSize: 14,
                  lineHeight: 1.6,
                }}
              >
                <p style={{ margin: "0 0 10px 0", fontWeight: 600 }}>
                  {t("queue.captchaWarning.text1")}
                </p>
                <p style={{ margin: 0 }}>
                  {t("queue.captchaWarning.text2")}
                </p>
              </div>
              <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
                <button onClick={() => setShowCaptchaWarning(false)}>
                  {t("common.cancel")}
                </button>
                <a
                  href={stoUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="primary"
                  style={{
                    display: "inline-block",
                    padding: "6px 16px",
                    borderRadius: 4,
                    textDecoration: "none",
                    fontSize: 13,
                  }}
                  onClick={() => setShowCaptchaWarning(false)}
                >
                  {t("queue.captchaWarning.confirm")}
                </a>
              </div>
            </div>
          </div>
        )}

        {!isRateLimited && displayMessage && (
          <div className="meta" style={{ marginTop: 6 }}>{displayMessage}</div>
        )}
        {item.output_path && item.status === "completed" && (
          <div className="meta" style={{ marginTop: 6 }}>
            {t("queue.output")}: <code>{item.output_path}</code>
          </div>
        )}
      </div>

      <div className="actions">
        {item.status === "downloading" || item.status === "queued" ? (
          <button onClick={() => api.pause(item.id)}>{t("common.pause")}</button>
        ) : item.status === "paused" && !isRateLimited ? (
          <button onClick={() => api.resume(item.id)}>{t("common.resume")}</button>
        ) : item.status === "failed" ? (
          <button onClick={() => api.retry(item.id)}>{t("common.retry")}</button>
        ) : null}
        <button
          className="danger"
          onClick={async () => {
            if (confirm(t("queue.confirmDelete"))) {
              await api.remove(item.id);
            }
          }}
        >
          {t("common.delete")}
        </button>
      </div>
    </div>
  );
}
