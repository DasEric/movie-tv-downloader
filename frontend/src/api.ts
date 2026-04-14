// Thin fetch wrapper. Same-origin — FastAPI serves both API and built frontend.

export type ItemSource = "s.to" | "aniworld" | "megakino";
export type ItemKind = "movie" | "episode" | "season";
export type ItemStatus =
  | "queued"
  | "scraping"
  | "downloading"
  | "processing"
  | "completed"
  | "failed"
  | "paused"
  | "waiting_release";

export interface QueueItem {
  id: number;
  source: ItemSource;
  kind: ItemKind;
  title: string;
  url?: string | null;
  slug?: string | null;
  season?: number | null;
  episode?: number | null;
  language: string;
  quality: string;
  priority: number;
  order_index: number;
  status: ItemStatus;
  progress: number;
  speed?: string | null;
  eta?: string | null;
  current_hoster?: string | null;
  message?: string | null;
  tmdb_id?: number | null;
  release_date?: string | null;
  auto_download: boolean;
  output_path?: string | null;
  attempts: number;
  created_at: string;
  updated_at: string;
}

export interface SearchResult {
  title: string;
  url: string;
  year: number | null;
  source: string;
  poster: string | null;
  slug: string | null;
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`${r.status} ${r.statusText}: ${text}`);
  }
  return r.json();
}

export const api = {
  health: () => fetch("/api/health").then((r) => j<{ status: string; version: string }>(r)),
  info: () => fetch("/api/info").then((r) => j<any>(r)),

  listQueue: () => fetch("/api/queue").then((r) => j<QueueItem[]>(r)),
  addItem: (payload: any) =>
    fetch("/api/queue", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }).then((r) => j<QueueItem>(r)),
  addBulkEpisodes: (payload: any) =>
    fetch("/api/queue/bulk-episodes", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }).then((r) => j<QueueItem[]>(r)),
  addSeason: (payload: any) =>
    fetch("/api/queue/season", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }).then(
      (r) =>
        j<{ count: number; items: QueueItem[]; skipped: number; total: number; already_on_disk: number }>(r)
    ),
  addSeries: (payload: any) =>
    fetch("/api/queue/series", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }).then(
      (r) =>
        j<{ count: number; items: QueueItem[]; skipped: number; total: number; seasons: number; already_on_disk: number }>(r)
    ),
  remove: (id: number) => fetch(`/api/queue/${id}`, { method: "DELETE" }).then(j),
  pause: (id: number) => fetch(`/api/queue/${id}/pause`, { method: "POST" }).then(j),
  resume: (id: number) => fetch(`/api/queue/${id}/resume`, { method: "POST" }).then(j),
  retry: (id: number) => fetch(`/api/queue/${id}/retry`, { method: "POST" }).then(j),
  reorder: (order: number[]) =>
    fetch("/api/queue/reorder", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ order }),
    }).then(j),
  pauseAll: () =>
    fetch("/api/queue/pause-all", { method: "POST" }).then((r) =>
      j<{ paused: number }>(r)
    ),
  resumeAll: () =>
    fetch("/api/queue/resume-all", { method: "POST" }).then((r) =>
      j<{ resumed: number }>(r)
    ),
  stopAll: () =>
    fetch("/api/queue/stop-all", { method: "POST" }).then((r) =>
      j<{ stopped: number }>(r)
    ),
  retryAll: () =>
    fetch("/api/queue/retry-all", { method: "POST" }).then((r) =>
      j<{ retried: number }>(r)
    ),
  clearCompleted: () =>
    fetch("/api/queue", { method: "DELETE" }).then((r) => j<{ removed: number }>(r)),

  search: (source: ItemSource, q: string) =>
    fetch(`/api/search?source=${encodeURIComponent(source)}&q=${encodeURIComponent(q)}`).then(
      (r) => j<SearchResult[]>(r)
    ),
  listSeasons: (source: ItemSource, slug: string) =>
    fetch(
      `/api/search/seasons?source=${encodeURIComponent(source)}&slug=${encodeURIComponent(slug)}`
    ).then((r) => j<{ seasons: number[] }>(r)),
  listEpisodes: (source: ItemSource, slug: string, season: number) =>
    fetch(
      `/api/search/episodes?source=${encodeURIComponent(source)}&slug=${encodeURIComponent(
        slug
      )}&season=${season}`
    ).then((r) => j<{ episodes: number[] }>(r)),
  listEpisodesWithLang: (
    source: ItemSource,
    slug: string,
    season: number,
    language: string
  ) =>
    fetch(
      `/api/search/episodes-with-lang?source=${encodeURIComponent(
        source
      )}&slug=${encodeURIComponent(slug)}&season=${season}&language=${encodeURIComponent(
        language
      )}`
    ).then(
      (r) =>
        j<{
          episodes: {
            episode: number;
            has_language: boolean;
            actual_language: string | null;
          }[];
        }>(r)
    ),
  getShowDetails: (source: ItemSource, slug: string) =>
    fetch(
      `/api/search/details?source=${encodeURIComponent(source)}&slug=${encodeURIComponent(slug)}`
    ).then((r) =>
      j<{ title: string; poster: string | null; seasons: number[] }>(r)
    ),

  // ---- library (download status) ----
  checkShow: (title: string) =>
    fetch(
      `/api/library/show?title=${encodeURIComponent(title)}`
    ).then((r) =>
      j<{ found: boolean; seasons: Record<string, number[]>; total_episodes: number }>(r)
    ),
  checkMovie: (title: string, year?: number | null) =>
    fetch(
      `/api/library/movie?title=${encodeURIComponent(title)}${year ? `&year=${year}` : ""}`
    ).then((r) => j<{ found: boolean }>(r)),

  getSettings: () => fetch("/api/settings").then((r) => j<any>(r)),
  updateSettings: (payload: any) =>
    fetch("/api/settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }).then((r) => j<any>(r)),

  getLogs: () => fetch("/api/logs?limit=500").then((r) => j<any[]>(r)),

  // ---- watchlist (season watch) ----
  listWatches: () => fetch("/api/watchlist").then((r) => j<any[]>(r)),
  addWatch: (payload: any) =>
    fetch("/api/watchlist", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }).then((r) => j<any>(r)),
  removeWatch: (id: number) =>
    fetch(`/api/watchlist/${id}`, { method: "DELETE" }).then(j),
  checkWatchNow: (id: number) =>
    fetch(`/api/watchlist/${id}/check-now`, { method: "POST" }).then((r) =>
      j<any>(r)
    ),

  // ---- upcoming (watch-for-release) ----
  listUpcoming: () => fetch("/api/upcoming").then((r) => j<QueueItem[]>(r)),
  addUpcoming: (payload: any) =>
    fetch("/api/upcoming", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }).then((r) => j<QueueItem>(r)),
  removeUpcoming: (id: number) =>
    fetch(`/api/upcoming/${id}`, { method: "DELETE" }).then(j),
  tmdbUpcomingMovies: () =>
    fetch("/api/upcoming/tmdb/movies").then((r) => j<any[]>(r)),
  tmdbUpcomingTv: () =>
    fetch("/api/upcoming/tmdb/tv").then((r) => j<any[]>(r)),
  tmdbSearch: (q: string, kind: "movie" | "tv") =>
    fetch(
      `/api/upcoming/tmdb/search?q=${encodeURIComponent(q)}&kind=${kind}`
    ).then((r) => j<any[]>(r)),
};

// Auto-reconnecting WebSocket wrapper. Returns a "handle" with a close()
// method so React effects can tear it down. Reconnect uses exponential
// backoff capped at 10 s.
export interface ReconnectingSocket {
  close: () => void;
}

function openReconnectingSocket(
  path: string,
  onMessage: (msg: any) => void
): ReconnectingSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}${path}`;
  let ws: WebSocket | null = null;
  let closed = false;
  let attempt = 0;
  let retryTimer: number | null = null;

  const connect = () => {
    if (closed) return;
    try {
      ws = new WebSocket(url);
    } catch {
      schedule();
      return;
    }
    ws.onopen = () => {
      attempt = 0;
    };
    ws.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data));
      } catch {}
    };
    ws.onclose = () => {
      ws = null;
      schedule();
    };
    ws.onerror = () => {
      try {
        ws?.close();
      } catch {}
    };
  };

  const schedule = () => {
    if (closed) return;
    const delay = Math.min(1000 * Math.pow(2, attempt), 10_000);
    attempt += 1;
    retryTimer = window.setTimeout(connect, delay);
  };

  connect();

  return {
    close() {
      closed = true;
      if (retryTimer != null) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
      try {
        ws?.close();
      } catch {}
    },
  };
}

export function openEventsSocket(
  onMessage: (msg: any) => void
): ReconnectingSocket {
  return openReconnectingSocket("/api/ws/events", onMessage);
}

export function openLogsSocket(
  onMessage: (msg: any) => void
): ReconnectingSocket {
  return openReconnectingSocket("/api/ws/logs", onMessage);
}
