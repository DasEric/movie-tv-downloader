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

      <div style={{ marginTop: 16 }} className="queue-list">
        {items
          .slice()
          .sort((a, b) => a.order_index - b.order_index || a.id - b.id)
          .map((it) => (
            <QueueRow key={it.id} item={it} />
          ))}
      </div>

      <div style={{ marginTop: 16, textAlign: "right" }}>
        <button
          className="danger"
          onClick={async () => {
            await api.clearCompleted();
          }}
        >
          {t("common.clearCompleted")}
        </button>
      </div>
    </div>
  );
}

function QueueRow({ item }: { item: QueueItem }) {
  const { t } = useTranslation();
  const label =
    item.kind === "movie"
      ? `🎬 ${item.title}`
      : `📺 ${item.title} S${String(item.season).padStart(2, "0")}E${String(
          item.episode
        ).padStart(2, "0")}`;

  return (
    <div className="queue-item">
      <div>
        <div className="title-row">
          <span className="title">{label}</span>
          <span className={`tag ${item.status}`}>{t(`queue.status.${item.status}`)}</span>
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

        {item.message && <div className="meta" style={{ marginTop: 6 }}>{item.message}</div>}
        {item.output_path && item.status === "completed" && (
          <div className="meta" style={{ marginTop: 6 }}>
            {t("queue.output")}: <code>{item.output_path}</code>
          </div>
        )}
      </div>

      <div className="actions">
        {item.status === "downloading" || item.status === "queued" ? (
          <button onClick={() => api.pause(item.id)}>{t("common.pause")}</button>
        ) : item.status === "paused" ? (
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
