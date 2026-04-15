"""User-facing German strings for the Discord bot.

Kept out of the main i18n system because the bot only speaks German —
matching the behaviour of the prior standalone H0melabBot. Each string
is a `.format()` template; callers pass keyword args.
"""

# --- search / dropdown ---
SELECT_MOVIE       = "Wähle einen Film aus..."
SELECT_SERIES      = "Wähle eine Serie aus..."
NO_RESULTS_MOVIE   = "Ich konnte keinen Film mit diesem Namen finden."
NO_RESULTS_SERIES  = "Ich konnte keine Serie mit diesem Namen finden."
PICK_PROMPT_MOVIE  = "Bitte wähle den Film aus, den du anfragen möchtest:"
PICK_PROMPT_SERIES = "Bitte wähle die Serie aus, die du anfragen möchtest:"
MISSING_ROLE       = "Du hast nicht die erforderliche Rolle, um diesen Befehl zu verwenden."
GENERIC_ERROR      = "Ein Fehler ist aufgetreten: {err}"

# --- standard mode: owner approval flow ---
REQUEST_SENT        = "Deine Anfrage wurde an den Besitzer gesendet."
REQUEST_CANCELLED   = "Anfrage abgebrochen."
REQUEST_ACCEPTED    = "Deine Anfrage für **{title}** wurde angenommen und wird bearbeitet."
REQUEST_DECLINED    = "Deine Anfrage für **{title}** wurde abgelehnt. Grund: {reason}"
OWNER_REQUEST_TITLE_MOVIE  = "Neue Filmanfrage"
OWNER_REQUEST_TITLE_SERIES = "Neue Serienanfrage"
OWNER_ACCEPTED_LABEL  = "Angenommen"
OWNER_DECLINED_LABEL  = "Abgelehnt"
OWNER_NOT_FOUND_BANNER = "**Nicht auf den Scrapern gefunden** — manuell hochladen und unten markieren."

# --- advanced mode: direct action ---
DOWNLOAD_STARTED = "Deine Anfrage für **{title}** wurde gefunden und wird heruntergeladen. Du bekommst eine Benachrichtigung, sobald sie verfügbar ist."
NOT_FOUND_DM     = "Deine Anfrage für **{title}** konnte leider auf keiner Quelle gefunden werden. Der Besitzer wurde informiert."

# --- both modes ---
ALREADY_AVAILABLE = "**{title}** ist bereits in der Bibliothek verfügbar."

# --- completion notifications ---
DONE_USER_DM     = "Deine Anfrage für **{title}** ist jetzt verfügbar."
FAILED_USER_DM   = "Beim Download von **{title}** ist ein Fehler aufgetreten. Der Besitzer wurde informiert."
OWNER_FAILED_DM  = "Download von **{title}** fehlgeschlagen: {reason}"

# --- channel embed titles ---
EMBED_MOVIE_AVAILABLE      = "{title} ist jetzt verfügbar!"
EMBED_SERIES_AVAILABLE     = "{title} ist jetzt verfügbar!"
EMBED_NEW_SEASON_AVAILABLE = "Neue Staffel: {title} — Staffel {season}"

# --- owner manual-upload view (NOT_FOUND branch) ---
MARK_UPLOADED_LABEL  = "Als hochgeladen markieren"
MARK_UPLOADED_DONE   = "**{title}** wurde als hochgeladen markiert."

# --- confirm view ---
CONFIRM_LABEL = "Bestätigen"
CANCEL_LABEL  = "Abbrechen"
