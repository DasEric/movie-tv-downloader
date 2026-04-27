# Movie / TV Downloader

> **Self-hosted Plex/Jellyfin/etc. auto-downloader** for `s.to`, `aniworld.to` and
> `megakino` — runs in **one** single Docker container. Drop it on your
> machine, point two volumes at your Plex libraries, and every item you add
> via the Web-UI (or request via Discord) ends up in the right folder with
> Plex-correct naming.

```
Docker Hub:  daseric/movie-tv-downloader
Default UI:  http://<host>:3000
```

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

You open the Web-UI → search for a show or movie → click → pick episodes
→ **Add to queue**. The container then:

1. **Scrapes** the source site (s.to for series, aniworld.to for anime,
   megakino for movies — megakino is German-only, English movie sources
   are planned)
2. **Walks the hoster list** in your configured priority order
   (VOE → Vidmoly → Vidoza → Doodstream) until one yields a playable stream
3. **Downloads** the stream with `yt-dlp` (fragmented HLS → chunked,
   resume, retry, parallel fragments)
4. **Converts** to H.264/AAC MP4 via `ffmpeg` — either stream-copy remux
   (fast) or full transcode (fallback)
5. **Moves** the finished file into the Plex-correct path:

   ```
   /movies/Movie Title (2024).mp4
   /tv/Show Name/S01/Show_Name_S01E01.mp4
   ```

6. **Fetches subtitles** (Subliminal, DE/EN by default — optional)
7. **Notifies** you via Discord webhook and/or Telegram (optional)
8. **Pre-schedules** movies for automatic download on release date via
   the **Upcoming** tab (requires a free TMDB API key)
9. **Auto-queues new episodes** of an ongoing season in your chosen
   language via the **Watchlist** tab (pure site-based, no TMDB
   needed)
10. **Accepts Discord slash-command requests** from your server members via
    the embedded Discord bot — two flows (standard approval vs advanced
    auto-enqueue), per-role permission, optional manual-upload channel

Everything happens inside **one** container — no Redis, no Celery, no
FlareSolverr, no headless browser. FastAPI hosts both the REST API and
the React frontend; `curl_cffi` bypasses Cloudflare by impersonating a
real Chrome TLS fingerprint;
`yt-dlp` handles the actual download; `ffmpeg` does the post-processing;
the embedded `discord.py` bot runs in the same event loop.

---

## Features at a glance

### Core
- 🎬 Three sources in one UI: `s.to` (series), `aniworld.to` (anime), `megakino` (movies, German only)
- 🔁 Hoster fallback chain (VOE → Vidmoly → Vidoza → Doodstream)
- 🎞 Always outputs **MP4** (H.264/AAC) — no `.ts` or `.mkv` leftovers
- 📂 Plex-conformant paths: `Title (Year).mp4` / `Show/S01/Show_S01E01.mp4`
- 📺 Posters pulled directly from the source HTML (no TMDB required)
- 🗂 Full queue management — pause/resume/retry/reorder/delete/concurrency
- ⏹ **Bulk queue controls** — pause-all / resume-all / stop-all / retry-all / clear-completed
- ⏹ **Real download cancellation** — hitting Delete on an in-flight item
  actually stops yt-dlp mid-fragment, not just the coroutine

### Automation
- 🔔 **Season Watchlist** — mark a season as "watch for new episodes" in a
  specific language; new episodes are auto-queued the moment the
  language-specific version goes live on the site. **Check now** button
  on each entry to probe immediately.
- 🎯 **Upcoming tab** — browse TMDB's upcoming movies/shows, click "Watch"
  on an unreleased title and the scheduler pulls it the second it hits
  megakino / s.to / aniworld in your chosen language
- ⏱ Runtime-configurable release-check interval (no container restart
  needed when you change it)

### Discord bot (embedded, optional)
- 🤖 **Slash commands** — `/film-anfrage` and `/serien-anfrage` let server
  members request content directly from Discord
- 🎚 **Two operating modes**
  - **Standard**: every request DMs the owner for approval (Annehmen /
    Ablehnen buttons). Nothing queues without a human in the loop.
  - **Advanced**: requests that resolve cleanly enqueue automatically.
    Only the not-found / manual-upload cases DM the owner.
- 👥 **Role gating** — restrict who can use the commands via a Discord
  role ID (empty = everyone)
- 📤 **Manual upload channel** — when a request has no working source,
  the owner can drop a file in the configured channel and the bot wires
  it into the library
- ⚡ **Instant guild sync** — set a `guild_id` and commands appear
  instantly on your server (otherwise global sync takes up to an hour)
- ✅ **Live status badge** on the Settings page shows `running` /
  `error` / `stopped` in real time

### Plumbing
- 🌐 **Per-item language picker** — `GerDub` / `GerSub` / `EngDub` / `EngSub`
  (per queue item, independent of the UI language)
- 📡 Live updates via WebSocket (progress, speed, ETA, hoster, logs)
- 🌐 i18n — German & English UI, browser auto-detect + manual switcher
- 🌙 Dark/Light mode toggle
- 🔔 Discord webhook + Telegram bot notifications (in addition to the
  full Discord bot above)
- 📝 Auto-subtitle download via Subliminal (optional, language list
  configurable)
- 🔀 Dynamic Megakino domain resolution via community domain tracker
- 🛡 **Cloudflare bypass** without a headless browser
  (`curl_cffi` Chrome TLS impersonation)
- 🌀 Optional SOCKS5 / HTTP proxy support

### UI
- 🎨 Editorial/tactical redesign — sidebar navigation with numbered
  sections, live HUD status bar, corner-bracket cards, Space Grotesk
  + IBM Plex Mono + Instrument Serif typography
- 📊 Live "HUD" showing running / queued / completed / failed counters
  at all times

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
      CONCURRENCY: "1"
      DEFAULT_LANGUAGE: "de"
      QUALITY_PROFILE: "best"
      HOSTER_PRIORITY: "VOE,Vidmoly,Vidoza,Doodstream"
      # Optional Discord bot (leave blank to disable — you can also
      # configure all of this later from Settings → Discord bot):
      DISCORD_TOKEN: ""
      DISCORD_OWNER_ID: ""
      DISCORD_UPLOAD_CHANNEL_ID: ""
      DISCORD_REQUEST_ROLE_ID: ""
      DISCORD_GUILD_ID: ""
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
| `/tv` | Series (`Show/S01/Show_S01E01.mp4`) | Your Plex **TV Shows** library folder |
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

### Core

| Variable | Default | Description |
|---|---|---|
| `TZ` | `Europe/Berlin` | Container timezone — respects `tzdata` |
| `HOST_PORT` | `8080` | Host port (only used if you use the bundled `docker-compose.yml`) |
| `CONCURRENCY` | `1` | Max simultaneous downloads |
| `DEFAULT_LANGUAGE` | `de` | UI default + TMDB locale (`de` / `en`) |
| `QUALITY_PROFILE` | `best` | `480p` / `720p` / `1080p` / `1440p` / `4k` / `best` |
| `HOSTER_PRIORITY` | `VOE,Vidmoly,Vidoza,Doodstream` | Fallback order (first hoster is tried first) |
| `TMDB_API_KEY` | *(empty)* | **Recommended.** Enables release-date metadata & "download when released" |
| `PROXY_URL` | *(empty)* | Optional SOCKS5/HTTP proxy (`socks5://host:1080`) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `HOMELAB_CREDIT` | `true` | Show/hide the footer credit line |

### Notifications

| Variable | Default | Description |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | *(empty)* | Discord channel webhook for one-way completion notifications |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Telegram bot token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | *(empty)* | Telegram chat-id to send notifications to |

### Discord bot (optional — two-way slash-command requests)

| Variable | Default | Description |
|---|---|---|
| `DISCORD_TOKEN` | *(empty)* | Bot token from the Discord developer portal |
| `DISCORD_OWNER_ID` | *(empty)* | Discord user ID that receives approval DMs |
| `DISCORD_UPLOAD_CHANNEL_ID` | *(empty)* | Channel ID where owners drop manually-uploaded files |
| `DISCORD_REQUEST_ROLE_ID` | *(empty)* | Role ID allowed to use the request commands (blank = everyone) |
| `DISCORD_GUILD_ID` | *(empty)* | Guild ID for instant slash-command sync (blank = global, ~1h to propagate) |

You can change **all** of these later from the Settings tab in the UI.
UI changes are persisted to SQLite and override the env-var values.

### Getting a free TMDB key (30 seconds)

Not strictly required — posters are scraped from the source HTML
anyway — but **strongly** recommended for release-date metadata and the
"download when released" scheduler:

1. Sign up → <https://www.themoviedb.org/signup>
2. Go to <https://www.themoviedb.org/settings/api>
3. Copy the **"API Read Access Token (v3)"**
4. Paste it into `TMDB_API_KEY` (env var) or into *Settings → API keys* in the UI

---

## How the pipeline works

```
┌───────────────────── Docker container ─────────────────────┐
│                                                             │
│   FastAPI  (uvicorn, port 3000)                             │
│   ├── /api/queue       Queue CRUD + bulk pause/resume/stop  │
│   ├── /api/watchlist   Season Watchlist CRUD                │
│   ├── /api/upcoming    TMDB upcoming + "watch for release"  │
│   ├── /api/library     Disk-state probes (already on disk?) │
│   ├── /api/discord     Bot live status                      │
│   ├── /api/ws/*        WebSocket (progress + logs)          │
│   └── /                Built React frontend (6 tabs)        │
│                                                             │
│   QueueManager (asyncio + SQLite persistence)               │
│   └── Worker task per queue item:                           │
│       1. Scraper (s.to / aniworld / megakino)               │
│       2. Hoster resolver (VOE / Vidmoly / Vidoza / Dood)    │
│       3. yt-dlp → /tmp/h0melab/<uuid>/                      │
│          (threading.Event for real mid-download cancel)     │
│       4. ffmpeg → H.264/AAC MP4                             │
│       5. Move → /movies or /tv with Plex naming             │
│       6. Subliminal (DE/EN subs)                            │
│       7. Discord webhook / Telegram / Discord-bot DM        │
│                                                             │
│   APScheduler (runtime-reconfigurable interval)             │
│   ├── Upcoming  → search megakino / s.to / aniworld for     │
│   │               unreleased titles                         │
│   └── Watchlist → per-episode language probe                │
│                   (spawns queue items when lang appears)    │
│                                                             │
│   Discord bot (discord.py, same event loop, supervised)     │
│   ├── /film-anfrage    → TMDB pick → decide → enqueue/ask   │
│   ├── /serien-anfrage  → TMDB pick → decide → enqueue/ask   │
│   └── Mode: standard (owner-approved) | advanced (auto)     │
│                                                             │
│   HTTP stack                                                │
│   └── curl_cffi — Chrome TLS fingerprint                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

Why one container works:

- **No Redis** — an `asyncio.Semaphore` bounds concurrency, SQLite
  persists the queue
- **No FlareSolverr** — `curl_cffi`'s Chrome-TLS impersonation sails
  through Cloudflare's Turnstile on all three sites
- **No Celery worker** — the queue runs in the same event loop as the
  API, interrupted items are re-queued on startup
- **No separate bot process** — `discord.py` runs in the same event
  loop, supervised by an idempotent reconciler that restarts it when
  the token changes

---

## Using the UI

The web interface has six tabs: **Queue**, **Add**, **Watchlist**,
**Upcoming**, **Logs**, **Settings** — navigated via the left sidebar.
The top HUD bar shows a live status line (section name + running /
queued / completed / failed counters + clock).

### Add tab — manual download

Pick a source → type the title **or paste a URL** from the source site
(e.g. `https://s.to/serie/stream/the-rookie`) → press **Search**.

You'll see a grid of result cards with posters. Cards show a
**download-status badge** in the bottom-right corner if the title (or
some episodes of it) are already on disk — green tick = complete,
yellow minus = partial. Click a card:

- **For a movie** (megakino) → the "Add Movie" button appears on the
  card → click it → it lands in the queue
- **For a series** (s.to / aniworld) → a "Selected show" banner opens
  below with the poster and title → pick a season → pick episodes →
  **Add selected** / **Add whole season** / **Auto-download new episodes**
  (the last one puts the season on the Watchlist, see below)

Episodes already on disk are marked with a green border and a ✓ in the
episode grid — you can still re-add them if you want a re-download.
Episodes not available in your chosen language are greyed out /
struck through; clicking one prompts you to add the season to the
Watchlist instead.

### Language handling

The **UI language** (top-right switcher) only affects labels and text in
the interface. The **content language** (what actually gets downloaded)
is picked **per queue item** via the "Sprachversion / Language track"
dropdown in the Add tab. Choices:

| Option | What it downloads |
|---|---|
| `GerDub` (German) | German audio track (Hoster filter: "Deutsch") |
| `GerSub` (German subs) | Japanese/English audio + German subs |
| `EngDub` (English) | English audio track |
| `EngSub` (English subs) | Japanese audio + English subs |

Megakino is German-only — the language dropdown is disabled when you
select it as source. s.to and aniworld.to both support all four
combinations; the scraper filters the hoster list by the HTML
`data-language-label` / `data-lang-key` attribute before trying anything.

### Queue tab

Each queue item shows:
- Title + source badge + language/quality tags
- Current status (queued / scraping / downloading / processing / done)
- Progress bar with percentage, speed (`10.12 MiB/s`), ETA (`1:23`)
- Current hoster being tried
- Per-item actions: **pause** / **resume** / **retry** (for failed items) / **delete**

Above the list, five **bulk controls**:

- **⏸ Pause all** — pauses every running/queued item. In-flight downloads
  are properly aborted at the next fragment boundary (not just the
  coroutine — yt-dlp actually stops writing bytes).
- **▶ Resume all** — flips every paused item back to queued.
- **⏹ Stop all** — same as pause-all but with a confirm dialog. Items
  stay in the `paused` state so you can resume later.
- **Retry all** — retries every failed or rate-limited item in one click.
- **Clear completed** — removes all successfully-completed items from
  the list (the files stay on disk, of course).

**Rate-limited items** (e.g. `[RATE_LIMITED]` prefix from s.to throttle)
are flagged with a yellow warning tag and a direct "Open in browser"
link so you can solve the site-side challenge manually, then click
**Retry**.

**Delete behaviour** — if you hit Delete on an item that's currently
downloading, yt-dlp is signalled to abort at the next fragment boundary
and the scratch directory is cleaned up. No wasted bandwidth on a
download you don't want.

### Watchlist tab — auto-download new episodes of an ongoing season

Use case: *"I want every new episode of The Rookie Season 8 in German,
as soon as it's released on s.to."*

How to add a watch:

1. Go to **Add** → search the show → pick a season → below the episode
   grid there's a **🔔 Auto-download new episodes** box
2. Pick a duration — **7 days** / **30 days** / **90 days** / **forever**
3. Click — you're done. The watch is now active.

What the scheduler does on every tick:

1. `list_episodes(slug, season)` on the source site
2. For each episode that's not yet in the watch's `enqueued_episodes` set:
   - Hits the episode page
   - Checks whether the **requested language** is actually listed as a
     hoster option (via `data-language-label="Deutsch"` or
     `data-lang-key="1"` matching)
   - If **yes** → spawns an `EPISODE` queue item and marks it as enqueued
     → the worker downloads it
   - If **no** → leaves it for the next tick (the German dub might only
     come out 3 days after the English original)
3. If `expires_at` is in the past → delete the watch

Click **Check now** on any watchlist entry to trigger a probe immediately
instead of waiting for the next scheduled tick.

### Upcoming tab — watch for releases that don't exist anywhere yet

Use case: *"Avatar: The Way of Water 2 has a release date of Dec 2027,
and I want to download it in English the moment it hits megakino."*

Requires a **TMDB API key** (if you haven't set one yet the tab shows a
hint to the Settings tab).

The tab has two sections:

1. **Your watchlist** — items you've marked as "watch for release",
   showing the poster, release date (if known), source, language, and
   last-check message.
2. **TMDB search & browse** — either browse TMDB's curated "upcoming
   movies" / "upcoming TV" lists, or search for a specific title by name.
   Click the **+ Watch** button on any result to add it.

What the scheduler does on every tick for each upcoming item:

- **Movie**: searches megakino for the title. If found → fills in the
  URL on the queue item, flips status to `QUEUED`, and the worker
  downloads it. If not found → updates a `last checked` timestamp.
- **TV show**: searches s.to / aniworld for the title, lists the
  requested season's episodes, and **for each episode checks if the
  user's chosen language is actually available**. Episodes that are
  already available in the right language get spawned as queue items;
  episodes that aren't yet in the right language get moved to a
  **Season Watchlist** entry so the per-episode language probing keeps
  going on subsequent ticks.

### Check interval

Both the Watchlist and Upcoming tabs are driven by the same scheduler.
The interval is set in **Settings → Release check (minutes)** — default
`60`. Change it at runtime (no container restart needed) — the scheduler
re-reads the setting on every tick and reschedules its own job if the
value changed.

Set it to `30` for "twice an hour", `120` for "every two hours", `360`
for "every six hours", etc. There's no upper or lower limit other than
"don't spam the source sites" — `30` is a reasonable floor.

### Live logs

The **Logs** tab streams everything happening in the backend in real
time, with level filtering (All / Info / Warn / Error). Useful for
debugging scraper failures, hoster issues, or seeing exactly what the
scheduler is doing (*"checked 2026-04-11 14:30 — no new episodes in de"*
etc.).

### Settings

Everything the env vars set is also editable at runtime from the
Settings tab. API keys and bot tokens are stored server-side and never
sent back to the UI — you only see a **"configured"** badge next to the
field. Key sections:

- **General** — concurrency, default language, quality profile, hoster
  priority
- **API keys** — TMDB, Discord webhook, Telegram bot + chat ID, SOCKS5/HTTP
  proxy
- **Scheduler** — release check interval (drives Watchlist + Upcoming)
- **Discord bot** — full bot configuration (see the dedicated section
  below)
- **Subtitles** — toggle auto-subs on/off, configure the language list
  (comma-separated, e.g. `de, en`)

---

## The Discord bot

The embedded Discord bot lets your server members request content
directly via slash commands. It lives in the same container, runs in
the same event loop as FastAPI, and is supervised by a reconciler that
starts / stops / restarts it whenever the relevant settings change —
no restart required.

### Setup (5 minutes)

1. Go to <https://discord.com/developers/applications> → *New Application*
2. Sidebar → **Bot** → *Reset Token* → copy it
3. **Privileged Gateway Intents** → enable *Message Content Intent*
4. Sidebar → **Installation** → *OAuth2 URL* → scopes `bot` +
   `applications.commands` → invite it to your server
5. In the UI → *Settings → Discord bot*:
   - Tick **Enable bot**
   - Paste the token
   - Paste your own Discord user ID into **Owner ID** (right-click your
     name in Discord with Developer Mode on → Copy ID)
   - **Guild ID** → paste your server ID for instant command sync
     (leave empty for global sync, which takes up to an hour)
   - **Upload channel ID** → a private channel where you'll drop
     manually-uploaded files for titles not found on any source
   - **Request role ID** (optional) → restrict who can use the commands
6. Save — the status badge flips to **running** within ~2 seconds

### Commands

| Command | What it does |
|---|---|
| `/film-anfrage` | Request a movie by title. Bot searches TMDB, shows a picker of results, user confirms. |
| `/serien-anfrage` | Request a series by title — same flow, plus season picker. |

### Standard vs Advanced mode

Both modes start the same: user picks a TMDB result → confirms details.
From there:

- **Standard** (default, recommended for open servers):
  - Every request DMs the owner with an **Annehmen / Ablehnen**
    (Accept / Decline) button pair and the full TMDB details
  - Clicking **Annehmen** re-runs the resolution pipeline, finds the
    best source, and enqueues
  - If the pipeline comes up empty (title not on any source), the
    buttons switch to a **manual upload** view so the owner can drop
    the file in the upload channel
- **Advanced** (hands-off for trusted servers):
  - Requests that resolve cleanly on megakino / s.to / aniworld in the
    requested language **enqueue immediately** — the user gets a DM
    confirming success
  - Requests that hit the *not found* branch still DM the owner with
    the manual-upload view (you can't auto-magic a title that doesn't
    exist on any source)
  - Requests for content **already complete on disk** short-circuit
    with a friendly "already available" DM to the user

### Live status badge

Next to the *Discord bot* section header, a tag shows the real-time bot
state: **running** (green), **error** (red) with the failure reason in
the tooltip, or **stopped** (grey). Polled every 5 seconds from
`/api/discord/status`.

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
/tv/The Rookie/S08/The_Rookie_S08E01.mp4
```

> **Season folder convention**: short form `S08` (not `Season 08`).
> Plex, Jellyfin, Emby and the `TheTVDB` / `TheMovieDB` agents all
> accept this style — it keeps the paths terse and matches the
> filename stem.

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
`curl_cffi`'s Chrome impersonation is *usually* enough. If you see
persistent 403s with "Just a moment…" in the body, open s.to in your
browser, solve the captcha manually, then retry the download. If that
doesn't help, wait 10-15 minutes and retry, or set `PROXY_URL` to a
residential proxy.

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

### The Upcoming tab says "TMDB API key required"
The Upcoming tab hits `/api/upcoming/tmdb/movies` which needs the key.
Set it in the env vars or via Settings → API keys. The Watchlist tab
does **not** need a TMDB key because it searches the source sites
directly.

### My season watch never queues new episodes even though the show has them
Two things to check:

1. Is the **language** you chose actually available for the newer
   episodes? Watchlist is strict — if you picked `GerDub` but the German
   dub for E07 hasn't been released yet (only English), the scheduler
   correctly leaves it waiting. Hit **Check now** to confirm; the
   `last_message` will tell you something like *"3 episodes on site but
   none in de yet"*.
2. Has the **release check interval** actually fired yet? Default is
   60 minutes. Change it in Settings → Release check, or hit the
   **Check now** button on the watch entry to probe immediately.

### The scheduler doesn't actually schedule anything
Check the **Release check (minutes)** setting. If it's empty or 0, no
job is scheduled. Default is 60. Also verify the scheduler actually
started — look for `scheduler started (release check every 60m, tz=…)`
in the Logs tab.

### The Discord bot won't start
Check **Settings → Discord bot → status badge**:

- **error** → hover the tooltip or check the Logs tab. Common causes:
  - Invalid token (regenerate one in the developer portal)
  - Missing *Message Content Intent* in the bot's privileged intents
  - The bot wasn't invited to the guild matching `DISCORD_GUILD_ID`
- **stopped** → either *Enable bot* is unchecked or the token field is
  empty
- **running** but **slash commands don't show up** → if you didn't set
  a `DISCORD_GUILD_ID`, global sync can take up to an hour. Set the
  guild ID to get them instantly.

### Discord bot commands appear but fail with "missing role"
You've set **Request role ID** to a role that the requesting user
doesn't have. Either add them to the role, change the role ID, or clear
the setting (empty = everyone can request).

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

Your queue, settings, Megakino domain cache, and Discord bot config
all survive updates because they live in the `/config` volume.

---

## Data paths reference

| Host side (example) | Container side | Persistent? | Purpose |
|---|---|---|---|
| `/mnt/bigdisk/Movies` | `/movies` | ✅ | Finished movies |
| `/mnt/archive/Serien` | `/tv` | ✅ | Finished TV shows |
| `./data/config` | `/config` | ✅ | SQLite DB, Megakino domain cache, bot config |
| `./data/tmp` | `/tmp/h0melab` | ⚠ scratch | Raw download buffers (auto-deleted) |

---

## Architecture / tech stack

| Layer | What |
|---|---|
| Frontend | React 18 + Vite + TypeScript + react-i18next (Space Grotesk / IBM Plex Mono / Instrument Serif) |
| Backend | FastAPI + SQLModel + aiosqlite + APScheduler |
| HTTP | `curl_cffi` (Chrome TLS fingerprint) |
| Downloader | `yt-dlp` nightly |
| Post-processing | `ffmpeg` (H.264/AAC MP4) |
| Subtitles | `subliminal` (optional) |
| Discord bot | `discord.py` 2.4, same event loop, supervised |
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

