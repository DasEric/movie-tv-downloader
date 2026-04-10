# Movie / TV Downloader

> **Self-hosted Plex/Jellyfin/etc. auto-downloader** for `s.to`, `aniworld.to` and
> `megakino` — runs in **one** single Docker container. Drop it on your
> machine, point two volumes at your Plex libraries, and every item you add
> via the Web-UI ends up in the right folder with Plex-correct naming.

```
Docker Hub:  daseric/movie-tv-downloader
Default UI:  http://<host>:3000
```

---

## What it does

You open the Web-UI → search for a show or movie → click → pick episodes
→ **Add to queue**. The container then:

1. **Scrapes** the source site (s.to for series, aniworld.to for anime,
   megakino for movies, megakino is for german i will add support for english sites soon)
2. **Walks the hoster list** in your configured priority order
   (VOE → Vidmoly → Vidoza → Doodstream) until one yields a playable stream
3. **Downloads** the stream with `yt-dlp` (fragmented HLS → chunked,
   resume, retry, parallel fragments)
4. **Converts** to H.264/AAC MP4 via `ffmpeg` — either stream-copy remux
   (fast) or full transcode (fallback)
5. **Moves** the finished file into the Plex-correct path:

   ```
   /movies/Movie Title (2024).mp4
   /tv/Show Name/Season 01/Show_Name_S01E01.mp4
   ```

6. **Fetches subtitles** (Subliminal, DE/EN by default — optional)
7. **Notifies** you via Discord and/or Telegram webhook (optional)
8. **Pre-schedules** movies for automatic download on release date
   (requires a free TMDB API key)

Everything happens inside **one** container — no Redis, no Celery, no
FlareSolverr, no headless browser. FastAPI hosts both the REST API and
the React frontend; `curl_cffi` bypasses Cloudflare by impersonating a
real Chrome TLS fingerprint; `yt-dlp` handles the actual download;
`ffmpeg` does the post-processing.

---

## Features at a glance

- 🎬 Three sources in one UI: `s.to`, `aniworld.to`, `megakino`
- 🔁 Hoster fallback chain (VOE → Vidmoly → Vidoza → Doodstream)
- 🎞 Always outputs **MP4** (H.264/AAC) — no `.ts` or `.mkv` leftovers
- 📂 Plex-conformant paths: `Title (Year).mp4` / `Show/Season XX/Show_SXXEYY.mp4`
- 📺 Posters pulled directly from the source HTML (no TMDB required)
- 🗂 Full queue management — pause/resume/retry/reorder/delete/concurrency
- 📡 Live updates via WebSocket (progress, speed, ETA, hoster, logs)
- 🌐 i18n — German & English, browser auto-detect + manual switcher
- 🌙 Dark/Light mode toggle
- 🔔 Discord & Telegram webhook notifications
- 📝 Auto-subtitle download via Subliminal (optional)
- 🎯 TMDB integration + "download when released" scheduler (optional)
- 🔀 Dynamic Megakino domain resolution via community domain tracker
- 🛡 Cloudflare bypass without a headless browser (`curl_cffi` Chrome impersonation)
- 🌀 Optional SOCKS5 / HTTP proxy support

---

## Quick start — `docker compose`

### Option 1 — Pull the published image (recommended)

Create a file called `docker-compose.yml` somewhere on your host:

```yaml
version: "3.9"
name: movie-tv-downloader

services:
  downloader:
    image: daseric/movie-tv-downloader:latest
    container_name: movie-tv-downloader
    restart: unless-stopped
    ports:
      - "8080:3000"
    environment:
      TZ: Europe/Berlin
      # Optional but recommended — enables posters via TMDB as well,
      # and the "download when released" scheduler:
      TMDB_API_KEY: ""
      # Runtime tuning (all optional, shown with defaults):
      CONCURRENCY: "3"
      DEFAULT_LANGUAGE: "de"
      QUALITY_PROFILE: "1080p"
      HOSTER_PRIORITY: "VOE,Vidmoly,Vidoza,Doodstream"
    volumes:
      # ==== THE IMPORTANT PART ====
      # Left side  = host path (where Plex already scans)
      # Right side = container path (hardcoded, do NOT change!)
      - /path/to/your/Movies:/movies
      - /path/to/your/TV:/tv
      # Config + scratch space (keep on fast local storage):
      - ./data/config:/config
      - ./data/tmp:/tmp/h0melab
```

Then:

```bash
docker compose up -d
docker compose logs -f
```

Open `http://<your-host>:8080` in a browser — done.

### Option 2 — Build from source

Clone the repo, copy `.env.example` to `.env`, edit the paths, then:

```bash
docker compose up -d --build
```

This builds the image locally from the Dockerfile instead of pulling it.

---

## Quick start — plain `docker run`

If you don't like Compose:

```bash
docker run -d \
  --name movie-tv-downloader \
  --restart unless-stopped \
  -p 8080:3000 \
  -e TZ=Europe/Berlin \
  -e TMDB_API_KEY="" \
  -v /path/to/your/Movies:/movies \
  -v /path/to/your/TV:/tv \
  -v /srv/movie-tv-downloader/config:/config \
  -v /srv/movie-tv-downloader/tmp:/tmp/h0melab \
  daseric/movie-tv-downloader:latest
```

---

## The volume contract — this is the important part

The container has **exactly four** internal paths hardcoded. You must
mount your host folders to these **exact** container paths:

| Container path (fixed — do NOT rename) | What goes here | Where to point the host side |
|---|---|---|
| `/movies` | All finished movies (`Title (Year).mp4`) | Your Plex **Movies** library folder |
| `/tv` | Series (`Show/Season XX/Show_SXXEYY.mp4`) | Your Plex **TV Shows** library folder |
| `/config` | SQLite DB + persisted Megakino domain | A small, persistent local folder |
| `/tmp/h0melab` | Raw downloads (auto-cleaned after conversion) | A local folder on fast storage |

**You can put `/movies` and `/tv` on completely different physical
drives** — that's exactly the whole point of separating them.

### Example: movies on one disk, TV on another

```yaml
volumes:
  # Movies on the 4 TB drive
  - /mnt/bigdisk/Media/Filme:/movies

  # TV shows on a different drive (e.g. a 16 TB HDD)
  - /mnt/archive/Serien:/tv

  # Config + tmp stay on the fast system SSD
  - /srv/movie-tv-downloader/config:/config
  - /srv/movie-tv-downloader/tmp:/tmp/h0melab
```

New movies automatically land in `/mnt/bigdisk/Media/Filme/` on disk 1,
new episodes in `/mnt/archive/Serien/` on disk 2.
Plex picks them up on its next scan (or instantly if "Update library
automatically" is on).

### Example: point straight at existing Plex libraries

If you already have Plex running with these libraries:

```
/volume1/video/Filme         ← Plex "Movies" library
/volume2/media/Serien        ← Plex "TV Shows" library on a second volume
```

Then mount them exactly like this:

```yaml
volumes:
  - /volume1/video/Filme:/movies
  - /volume2/media/Serien:/tv
  - /volume1/docker/movie-tv-downloader/config:/config
  - /volume1/docker/movie-tv-downloader/tmp:/tmp/h0melab
```

Downloads appear **inside your existing Plex libraries** — no copying,
no moving, no symlinks.

### DON'T do this ❌

```yaml
# WRONG — there is no /data in the container
volumes:
  - /volume1/Downloader-Data:/data
  - /volume2/Filme:/data/movies
  - /volume2/Serien:/data/tv
```

The container writes to `/movies` and `/tv`, **not** to `/data/movies`
or `/data/tv`. With the above config your downloads land in the
container's ephemeral filesystem and disappear on the next restart /
rebuild. You **must** mount the host paths directly to `/movies` and
`/tv`, not under some prefix.

---

## Environment variables

All of these are optional — the container runs with sane defaults if
you set nothing except the volume mounts.

| Variable | Default | Description |
|---|---|---|
| `TZ` | `Europe/Berlin` | Container timezone — respects `tzdata` |
| `HOST_PORT` | `8080` | Host port (only used if you use the bundled `docker-compose.yml`) |
| `CONCURRENCY` | `3` | Max simultaneous downloads |
| `DEFAULT_LANGUAGE` | `de` | UI default + TMDB locale (`de` / `en`) |
| `QUALITY_PROFILE` | `1080p` | `480p` / `720p` / `1080p` / `1440p` / `4k` / `best` |
| `HOSTER_PRIORITY` | `VOE,Vidmoly,Vidoza,Doodstream` | Fallback order (first hoster is tried first) |
| `TMDB_API_KEY` | *(empty)* | **Recommended.** Enables release-date metadata & "download when released" |
| `DISCORD_WEBHOOK_URL` | *(empty)* | Discord channel webhook for success/failure notifications |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Telegram bot token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | *(empty)* | Telegram chat-id to send notifications to |
| `PROXY_URL` | *(empty)* | Optional SOCKS5/HTTP proxy (`socks5://host:1080`) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `HOMELAB_CREDIT` | `true` | Show/hide the footer credit line |

You can change **all** of these later from the Settings tab in the UI.
UI changes are persisted to SQLite and override the env-var values.

### Getting a free TMDB key (30 seconds)

Not strictly required — posters are scraped from the source HTML
anyway — but **strongly** recommended for release-date metadata and the
"download when released" scheduler:

1. Sign up → <https://www.themoviedb.org/signup>
2. Go to <https://www.themoviedb.org/settings/api>
3. Copy the **"API Read Access Token (v3)"**
4. Paste it into `TMDB_API_KEY` (env var) or into *Settings → API-Schlüssel* in the UI

---

## How the pipeline works

```
┌──────────────── Docker container ────────────────┐
│                                                   │
│   FastAPI  (uvicorn, port 3000)                   │
│   ├── /api/*          REST API                    │
│   ├── /api/ws/*       WebSocket (progress + logs) │
│   └── /               Built React frontend        │
│                                                   │
│   QueueManager (asyncio + SQLite persistence)     │
│   └── Worker task per item:                       │
│       1. Scraper (s.to / aniworld / megakino)     │
│       2. Hoster resolver (VOE / Vidmoly / …)      │
│       3. yt-dlp  → /tmp/h0melab/<uuid>/           │
│       4. ffmpeg  → H.264/AAC MP4                  │
│       5. Move    → /movies or /tv with naming     │
│       6. Subliminal (DE/EN subs)                  │
│       7. Discord/Telegram notify                  │
│                                                   │
│   APScheduler — TMDB release checks               │
│   curl_cffi   — Chrome TLS fingerprint bypass     │
│                                                   │
└───────────────────────────────────────────────────┘
```

Why one container works:

- **No Redis** — an `asyncio.Semaphore` bounds concurrency, SQLite
  persists the queue
- **No FlareSolverr** — `curl_cffi`'s Chrome-TLS impersonation sails
  through Cloudflare's Turnstile on all three sites
- **No Celery worker** — the queue runs in the same event loop as the
  API, interrupted items are re-queued on startup

---

## Using the UI

### Search

Type the exact show/movie title **or paste a URL** from the source site
(e.g. `https://s.to/serie/stream/the-rookie`). Both work.

You'll see a grid of result cards with posters. Click one:

- **For a movie** (megakino) → the "Add Movie" button appears on the
  card → click it → it lands in the queue
- **For a series** (s.to / aniworld) → a "Selected show" banner opens
  below with the poster and title → pick a season → pick episodes →
  **Add selected** (or **Add whole season**)

### Queue

Each queue item shows:
- Title + source badge + language/quality tags
- Current status (queued / scraping / downloading / processing / done)
- Progress bar with percentage, speed (`10.12 MiB/s`), ETA (`1:23`)
- Current hoster being tried
- Actions: pause / resume / retry (for failed items) / delete

Clear completed items via the "Clear completed" button.

### Live logs

The **Logs** tab streams everything happening in the backend in real
time, with level filtering. Useful for debugging scraper failures or
hoster issues.

### Settings

Everything the env vars set is also editable at runtime from the
Settings tab. API keys are stored server-side and never sent back to
the UI — you only see a "configured" badge next to the field.

---

## Plex setup

1. In Plex, create a **Movies** library and point it at the same host
   path you mounted to `/movies`
2. Create a **TV Shows** library and point it at the host path you
   mounted to `/tv`
3. Enable *"Update library automatically"* in Plex settings (or
   schedule a periodic refresh)

That's it. When a download finishes, the file is already in the right
place — no sync step, no symlinks, no second copy.

The folder and file naming scheme matches Plex's default scanner:

```
/movies/The Matrix (1999).mp4
/tv/The Rookie/Season 08/The_Rookie_S08E01.mp4
```

---

## Troubleshooting

### Downloads finish but the files aren't on my NAS
Your volume mounts are wrong. The container writes to `/movies` and
`/tv` (hardcoded). If you mounted them to `/data/movies` or anywhere
else, the files end up in the container's ephemeral layer and get lost
on restart. See the [volume contract](#the-volume-contract--this-is-the-important-part) section above.

Check from the host with:

```bash
docker exec movie-tv-downloader ls -la /movies /tv
```

If the listing shows `root root` with nothing inside even though your
host folder is populated, the mount points are wrong. Fix them and
restart.

### "s.to: no hosters found"
That episode genuinely has no working hosters on s.to right now.
Happens sometimes for the very newest episodes (hosters haven't uploaded
yet). Click **Retry** on the item in a few hours.

### "Cloudflare / captcha challenge"
`curl_cffi`'s Chrome impersonation is *usually* enough, but s.to
occasionally throws an actual Turnstile modal. Wait 10-15 minutes and
retry. If it persists, try setting `PROXY_URL` to a residential proxy.

### No posters for series
Make sure you're on the latest image. Older builds had a regex bug that
missed `og:image` tags when the attribute order was reversed.

```bash
docker compose pull
docker compose up -d
```

### Plex can't read the downloaded files
The container runs as root inside, so downloaded files are owned by
`root:root`. Plex can still read them (read permission is open by
default), but if you want Plex to *own* the files, set the container
user in your compose file:

```yaml
user: "1000:1000"     # replace with `id plex` on your host
```

### The scheduler doesn't actually schedule anything
The "download when released" feature needs a TMDB API key. Set it in
the env vars or via the Settings tab. Items in `waiting_release` state
are checked hourly.

### High disk use in `/tmp/h0melab`
Normally the scratch dir is auto-cleaned after each successful download.
If a download fails mid-way the tmp files are removed by the error
handler. If you still see stale files, `docker compose restart` does a
full cleanup on boot.

---

## Upgrading

With the published image:

```bash
docker compose pull
docker compose up -d
```

With a local build:

```bash
git pull
docker compose up -d --build
```

Your queue, settings and Megakino domain cache survive updates because
they live in the `/config` volume.

---

## Data paths reference

| Host side (example) | Container side | Persistent? | Purpose |
|---|---|---|---|
| `/mnt/bigdisk/Movies` | `/movies` | ✅ | Finished movies |
| `/mnt/archive/Serien` | `/tv` | ✅ | Finished TV shows |
| `./data/config` | `/config` | ✅ | SQLite DB, Megakino domain cache |
| `./data/tmp` | `/tmp/h0melab` | ⚠ scratch | Raw download buffers (auto-deleted) |

---

## Architecture / tech stack

| Layer | What |
|---|---|
| Frontend | React 18 + Vite + TypeScript + react-i18next |
| Backend | FastAPI + SQLModel + aiosqlite + APScheduler |
| HTTP | `curl_cffi` (Chrome TLS fingerprint) |
| Downloader | `yt-dlp` nightly |
| Post-processing | `ffmpeg` (H.264/AAC MP4) |
| Persistence | SQLite with WAL mode |
| Queue | `asyncio.Semaphore` (no Redis) |
| Event bus | In-process pub/sub → WebSocket |
| Runtime | `python:3.12-slim` with `tini` as PID 1 |

---

## Credits

Made with ♥ by **Eric** (Discord: `@daseric`). Toggleable via the
`HOMELAB_CREDIT` env var in the footer.

Heavy inspiration from the excellent
[`phoenixthrush/AniWorld-Downloader`](https://github.com/phoenixthrush/AniWorld-Downloader)
(MIT) for the s.to / aniworld.to scrapers and hoster extractors, and
from [`Yezun-hikari/Megakino-Downloader`](https://github.com/Yezun-hikari/Megakino-Downloader)
(MIT) for the Megakino extractor and the community-maintained domain
tracker it uses.
