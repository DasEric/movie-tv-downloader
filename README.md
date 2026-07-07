# Movie / TV Downloader

> **Self-hosted auto-downloader** for Plex / Jellyfin / Emby, running in **one**
> Docker container. Add a movie or show in the Web-UI (or request it via
> Discord) and it lands in the right library folder with correct naming —
> ready for Plex to scan.

```
Docker Hub:  daseric/movie-tv-downloader
Default UI:  http://<host>:8080
```

Sources: **serienstream.to** (s.to), **aniworld.to**, **burning-series.io**,
**megakino**, **filmpalast.to**, **kinox.to**.

---

## Legal Disclaimer

movie-tv-downloader is a **client-side** tool that enables access to content hosted on third-party websites. It **does not host, upload, store, or distribute any media itself**.

This software is **not intended to promote piracy or copyright infringement**. You are solely responsible for how you use the movie-tv-downloader and for ensuring that your use **complies with applicable laws** and the **terms of service of the websites you access**.

The developer provides this project **"as is"** and is **not responsible for**:

- Third-party content
- External links
- The availability, accuracy, legality, or reliability of any third-party service

If you have concerns about specific content, **contact the relevant website owner, administrator, or hosting provider**.

---

## What it does

Open the Web-UI → search for a show or movie → pick episodes → **Add to
queue**. The container then:

1. **Scrapes** the source site for the requested title and language.
2. **Walks the hoster list** in your priority order (VOE → Vidmoly → Vidoza →
   Doodstream, plus Filemoon / Vidara / Vidsonic) until one yields a playable
   stream.
3. **Downloads** the stream with `yt-dlp` (HLS fragments, resume, retry,
   parallel fragments) — and prefers the **audio track in your chosen
   language**, so a German download really is German even when a hoster ships
   multiple audio tracks in one file.
4. **Converts** to H.264/AAC MP4 via `ffmpeg` (fast stream-copy remux, or full
   transcode as fallback).
5. **Moves** the file into the Plex-correct path:
   ```
   /movies/Movie Title (2024).mp4
   /tv/Show Name/S01/Show_Name_S01E01.mp4
   ```
6. **Fetches subtitles** (Subliminal, DE/EN by default — optional).
7. **Notifies** you (Discord webhook / Telegram — optional).

Everything runs in **one** container — no Redis, no Celery, no FlareSolverr,
no headless browser. FastAPI serves the REST API and the React frontend;
`curl_cffi` clears Cloudflare by impersonating a real Chrome TLS fingerprint
(with an opt-in `cloudscraper` fallback); `yt-dlp` downloads; `ffmpeg`
post-processes; the embedded `discord.py` bot runs in the same event loop.

---

## Sources

| Source | Type | Notes |
|---|---|---|
| `serienstream.to` (s.to) | Series | Native site search; falls back to a bare-IP endpoint if the domain is DNS-blocked |
| `aniworld.to` | Anime series | GerDub / GerSub / EngDub / EngSub, with sensible fallback chain |
| `burning-series.io` | Series | |
| `megakino` | Movies | German audio; dynamic domain via community tracker |
| `filmpalast.to` | Movies | |
| `kinox.to` | Movies **and** series | |

Search accepts either a **title** (uses each site's own search index) or a
**pasted URL** from the source site.

---

## Features

**Core**
- 🎬 Six sources in one UI (see table above)
- 🔁 Hoster fallback chain, configurable priority
- 🈁 **Language-correct audio** — prefers the requested language's audio track
- 🎞 Always outputs **MP4** (H.264/AAC) — no `.ts` / `.mkv` leftovers
- 📂 Plex-conformant paths + posters scraped straight from the source
- 🗂 Full queue management — pause / resume / retry / reorder / delete, plus
  bulk pause-all / resume-all / stop-all / retry-all / clear-completed
- ⏹ **Real cancellation** — deleting an in-flight item actually stops yt-dlp
  mid-fragment

**Storage routing**
- 📁 **Per-source output paths** — send e.g. AniWorld to a separate `/anime`
  library while s.to stays in `/tv`. Optional and safe: leave blank to use the
  default, and if a configured path isn't mounted the download **automatically
  falls back** to the default library instead of getting lost. (See
  [Per-source storage paths](#per-source-storage-paths).)

**Automation**
- 🔔 **Season Watchlist** — mark a season "watch for new episodes" in a
  specific language; new episodes auto-queue the moment that language goes
  live. **Check now** probes immediately.
- 🎯 **Upcoming tab** — browse/search TMDB, mark an unreleased title, and the
  scheduler pulls it the second it appears on a source in your language.
- ⏱ Runtime-configurable release-check interval (no restart needed)

**Discord bot (embedded, optional)**
- 🤖 `/film-anfrage` and `/serien-anfrage` slash commands
- 🎚 **Standard** (owner approves each request) or **Advanced** (clean matches
  auto-enqueue) mode
- 👥 Optional role gating, manual-upload channel, instant guild sync, live
  status badge

**Plumbing**
- 📡 Live updates via WebSocket (progress, speed, ETA, hoster, logs)
- 🌐 German & English UI (auto-detect + switcher), dark/light mode
- 🔔 Discord webhook + Telegram notifications
- 📝 Auto-subtitles via Subliminal (optional)
- 🛡 Cloudflare bypass without a headless browser (`curl_cffi`, opt-in
  `cloudscraper` fallback)
- 🌀 Optional SOCKS5 / HTTP proxy

---

## Quick start — `docker compose`

### Pull the published image (recommended)

Create a `docker-compose.yml`:

```yaml
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
      TMDB_API_KEY: ""            # optional but recommended (see below)
      CONCURRENCY: "1"
      DEFAULT_LANGUAGE: "de"
      QUALITY_PROFILE: "best"
      HOSTER_PRIORITY: "VOE,Vidmoly,Vidoza,Doodstream"
    volumes:
      # Left = host path (where Plex scans) · Right = fixed container path
      - /path/to/your/Movies:/movies
      - /path/to/your/TV:/tv
      - ./data/config:/config
      - ./data/tmp:/tmp/h0melab
      # Optional extra libraries (see "Per-source storage paths"):
      # - /path/to/your/Anime:/anime
```

Then:

```bash
docker compose up -d
docker compose logs -f
```

Open `http://<your-host>:8080` — done.

### Build from source

Clone the repo, copy `.env.example` to `.env`, edit the paths, then:

```bash
docker compose up -d --build
```

---

## The volume contract — the important part

The container has four **fixed** internal paths. Mount your host folders to
these **exact** container paths:

| Container path (fixed) | What goes here | Point the host side at |
|---|---|---|
| `/movies` | Finished movies (`Title (Year).mp4`) | Your Plex **Movies** library |
| `/tv` | Series (`Show/S01/Show_S01E01.mp4`) | Your Plex **TV Shows** library |
| `/config` | SQLite DB + persisted domain caches | A small persistent local folder |
| `/tmp/h0melab` | Raw downloads (auto-cleaned) | A local folder on fast storage |

`/movies` and `/tv` can live on completely different physical drives.

**Don't** mount under a prefix like `/data/movies` — the container writes to
`/movies` and `/tv` directly. Mounting elsewhere sends downloads into the
container's ephemeral layer, where they vanish on restart.

### Optional: extra libraries (e.g. Anime)

On top of the four fixed paths you can mount **any number of extra volumes** and
route individual sources to them (see
[Per-source storage paths](#per-source-storage-paths)). Unlike the four above,
the container path here is **not fixed — you choose it freely**; it only has to
match what you enter in the Settings UI.

| Container path (your choice) | What goes here | Point the host side at |
|---|---|---|
| `/anime` *(example — any name works)* | A source you route here (e.g. AniWorld) | Your separate **Anime** library |

```yaml
volumes:
  - /path/to/your/Movies:/movies
  - /path/to/your/TV:/tv
  - /path/to/your/Anime:/anime          # ← extra library, freely named
  - ./data/config:/config
  - ./data/tmp:/tmp/h0melab
```

Then set the source's path to `/anime` under **Settings → Speicherpfade pro
Quelle**. If you enter a path but forget to mount it, the download safely
falls back to `/tv` / `/movies` — see the next section.

---

## Per-source storage paths

By default all series go to `/tv` and all movies to `/movies`. If you keep
separate Plex libraries — e.g. a dedicated **Anime** library fed by AniWorld —
you can override the TV and/or movie root **per source** under
**Settings → Speicherpfade pro Quelle**.

How it works:

1. **Mount** the extra library as a volume, e.g. add `- /host/Anime:/anime` to
   your compose file and recreate the container.
2. In **Settings → Speicherpfade pro Quelle**, set AniWorld's TV path to the
   in-container path `/anime`. (The value you type is the **right/container**
   side of the mount.)
3. Save. New AniWorld episodes now land in `/anime`; everything else still
   goes to `/tv`.

**It's optional and fail-safe:**
- Leave a field **blank** → that source uses the default `/tv` or `/movies`.
- If you type a path but **forget to mount it**, the download **automatically
  falls back** to the default library (and logs a warning) — nothing is ever
  written into the ephemeral container layer and lost.

The "already downloaded" badges and the Watchlist duplicate-skip logic scan
**all** configured roots, so custom paths don't cause re-downloads.

---

## Environment variables

All optional — the container runs on sane defaults; only the volume mounts are
required. Everything here is also editable at runtime in **Settings** (UI
changes persist to SQLite and override env vars).

### Core

| Variable | Default | Description |
|---|---|---|
| `TZ` | `Europe/Berlin` | Container timezone |
| `HOST_PORT` | `8080` | Host port (bundled compose only) |
| `CONCURRENCY` | `1` | Max simultaneous downloads |
| `DEFAULT_LANGUAGE` | `de` | UI default + TMDB locale (`de` / `en`) |
| `QUALITY_PROFILE` | `best` | `480p` / `720p` / `1080p` / `1440p` / `4k` / `best` |
| `HOSTER_PRIORITY` | `VOE,Vidmoly,Vidoza,Doodstream` | Fallback order |
| `TMDB_API_KEY` | *(empty)* | **Recommended** — enables release metadata + "download when released" |
| `PROXY_URL` | *(empty)* | Optional SOCKS5/HTTP proxy (`socks5://host:1080`) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `HOMELAB_CREDIT` | `true` | Show/hide footer credit |

### Notifications

| Variable | Description |
|---|---|
| `DISCORD_WEBHOOK_URL` | Discord webhook for one-way completion notifications |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | Telegram chat-id to notify |

### Discord bot (optional — two-way slash-command requests)

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from the Discord developer portal |
| `DISCORD_OWNER_ID` | User ID that receives approval DMs |
| `DISCORD_UPLOAD_CHANNEL_ID` | Channel for manually-uploaded files |
| `DISCORD_REQUEST_ROLE_ID` | Role allowed to use commands (blank = everyone) |
| `DISCORD_GUILD_ID` | Guild ID for instant sync (blank = global, ~1h) |

### Free TMDB key (30 seconds)

Not required (posters are scraped from the source) but recommended for
release-date metadata and the "download when released" scheduler:

1. Sign up → <https://www.themoviedb.org/signup>
2. <https://www.themoviedb.org/settings/api> → copy the **API Read Access Token (v3)**
3. Paste it into `TMDB_API_KEY` or *Settings → API keys*.

---

## Language handling

The **UI language** (top-right switcher) only changes interface text. The
**content language** (what's downloaded) is picked **per queue item** in the
Add tab:

| Option | Downloads |
|---|---|
| `GerDub` (German) | German audio |
| `GerSub` | Original audio + German subs |
| `EngDub` (English) | English audio |
| `EngSub` | Original audio + English subs |

The scraper filters the hoster list by the site's language flag/label before
trying anything, and the downloader then prefers the matching audio track
inside the stream — so multi-audio hosters (like VOE) no longer slip English
audio into a German download. Movie sources (megakino/filmpalast) are German by
default.

---

## The Discord bot (optional)

Lets server members request content via slash commands. It runs in the same
container and is supervised by a reconciler that starts/stops/restarts it when
settings change — no restart needed.

**Setup:** create an app at <https://discord.com/developers/applications> →
**Bot** → reset & copy token → enable **Message Content Intent** → invite with
scopes `bot` + `applications.commands`. Then in **Settings → Discord bot**:
enable the bot, paste the token and your Owner ID, optionally set a Guild ID
(instant command sync), an upload channel, and a request role. The status badge
flips to **running** within a couple seconds.

**Commands:** `/film-anfrage`, `/serien-anfrage`.

**Modes:**
- **Standard** — every request DMs the owner with Accept/Decline. Nothing
  queues without approval; not-found requests offer a manual-upload flow.
- **Advanced** — clean matches enqueue automatically; only not-found requests
  DM the owner. Already-on-disk requests short-circuit with a friendly reply.

---

## Plex setup

1. Point a Plex **Movies** library at the host path you mounted to `/movies`.
2. Point a **TV Shows** library at the host path you mounted to `/tv` (and any
   extra library, e.g. Anime, at its custom path).
3. Enable *"Update library automatically"*.

Naming matches Plex's default scanner:

```
/movies/The Matrix (1999).mp4
/tv/The Rookie/S08/The_Rookie_S08E01.mp4
```

Season folders use the short form `S08` (not `Season 08`) — accepted by Plex,
Jellyfin and Emby.

---

## Troubleshooting

**Downloads finish but files aren't on my NAS** — Volume mounts are wrong. The
container writes to `/movies` and `/tv`. Check with
`docker exec movie-tv-downloader ls -la /movies /tv`. If a custom per-source
path is empty in the listing, it probably wasn't mounted — the app falls back
to the default in that case (check the logs for a warning).

**"not available in <language>" / no hosters** — That title/episode genuinely
has no working hoster in that language right now (common for brand-new
episodes, or when the German dub trails the original). Retry later, or pick a
different language.

**"Cloudflare / captcha challenge"** — Usually `curl_cffi` handles it. Make
sure **Settings → Cloudflare fallback** is on (default). If it persists, wait
10–15 min or set `PROXY_URL` to a residential proxy.

**s.to unreachable** — The site moved to `serienstream.to`; the app uses it
automatically and falls back to a bare-IP endpoint if your ISP DNS-blocks the
domain. If both fail, a proxy usually helps.

**Watchlist never queues new episodes** — Check the chosen **language** is
actually available for the newer episodes (Watchlist is strict and waits for
it) and that the **release-check interval** has fired. Use **Check now** to
probe immediately; `last_message` explains what it found.

**Plex can't read the files** — Files are owned by `root:root` (readable by
default). To have Plex own them, set `user: "1000:1000"` in compose (use
`id <plexuser>` on the host).

---

## Upgrading

```bash
# published image
docker compose pull && docker compose up -d

# local build
git pull && docker compose up -d --build
```

Your queue, settings, domain caches and bot config live in `/config` and
survive updates.

---

## Tech stack

| Layer | What |
|---|---|
| Frontend | React 18 + Vite + TypeScript + react-i18next |
| Backend | FastAPI + SQLModel + aiosqlite + APScheduler |
| HTTP | `curl_cffi` (Chrome TLS), opt-in `cloudscraper` fallback |
| Downloader | `yt-dlp` |
| Post-processing | `ffmpeg` (H.264/AAC MP4) |
| Subtitles | `subliminal` (optional) |
| Discord bot | `discord.py` 2.4, same event loop, supervised |
| Persistence | SQLite (WAL) |
| Concurrency | `asyncio.Semaphore` (no Redis) |
| Runtime | `python:3.12-slim`, `tini` as PID 1 |

---

## Credits

Made with ♥ by **Eric** (Discord: `@daseric`). Toggle the footer credit via
`HOMELAB_CREDIT`.

Scraper/extractor inspiration from
[`phoenixthrush/AniWorld-Downloader`](https://github.com/phoenixthrush/AniWorld-Downloader)
(MIT) and [`Yezun-hikari/Megakino-Downloader`](https://github.com/Yezun-hikari/Megakino-Downloader)
(MIT). Cloudflare fallback uses
[`VeNoMouS/cloudscraper`](https://github.com/VeNoMouS/cloudscraper) (MIT).
