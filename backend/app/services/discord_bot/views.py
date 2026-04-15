"""
Discord UI views (dropdowns, buttons, modals).

The user-facing command (`/film-anfrage`, `/serien-anfrage`) sends a
MediaRequestView → user picks a result → ConfirmationView shows details
and a "Bestätigen" button → then we branch on the bot mode:

  * standard  → DM owner with OwnerApprovalView (Annehmen / Ablehnen).
                Annehmen re-runs flow.decide() and enqueues; on NOT_FOUND
                the buttons switch to the manual-upload view.
  * advanced  → if ALREADY_COMPLETE: short-circuit user DM.
                if NOT_FOUND:        DM user + DM owner with UploadedView.
                otherwise:           enqueue immediately, DM user.
"""
from __future__ import annotations

import logging
from typing import Literal

import discord

from app.services import settings_store
from app.services.discord_bot import flow, strings as S
from app.services.discord_bot.flow import RequestDecision, RequestPath

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _details_embed(decision: RequestDecision) -> discord.Embed:
    embed = discord.Embed(
        title=decision.title,
        description=decision.overview or "",
        color=discord.Color.blue(),
    )
    if decision.poster_url:
        embed.set_thumbnail(url=decision.poster_url)
    date_field = (
        "Veröffentlichungsdatum" if decision.media_type == "movie" else "Erstausstrahlung"
    )
    if decision.year:
        embed.add_field(name=date_field, value=str(decision.year))
    if decision.rating is not None:
        embed.add_field(name="Bewertung", value=f"{decision.rating}/10")
    return embed


def _channel_embed(decision: RequestDecision, kind: str, season: int | None) -> discord.Embed:
    if kind == "new_season" and season is not None:
        title = S.EMBED_NEW_SEASON_AVAILABLE.format(title=decision.title, season=season)
    elif decision.media_type == "movie":
        title = S.EMBED_MOVIE_AVAILABLE.format(title=decision.title)
    else:
        title = S.EMBED_SERIES_AVAILABLE.format(title=decision.title)
    embed = discord.Embed(
        title=title,
        description=decision.overview or "",
        color=discord.Color.green(),
    )
    if decision.poster_url:
        embed.set_image(url=decision.poster_url)
    date_field = (
        "Veröffentlichungsdatum" if decision.media_type == "movie" else "Erstausstrahlung"
    )
    if decision.year:
        embed.add_field(name=date_field, value=str(decision.year))
    if decision.rating is not None:
        embed.add_field(name="Bewertung", value=f"{decision.rating}/10")
    return embed


async def _get_owner(client: discord.Client) -> discord.User | None:
    owner_id = await settings_store.get("discord_owner_id")
    if not owner_id:
        return None
    try:
        return await client.fetch_user(int(owner_id))
    except Exception as e:
        log.warning("could not fetch discord owner %r: %s", owner_id, e)
        return None


async def _get_upload_channel(client: discord.Client) -> discord.abc.Messageable | None:
    ch_id = await settings_store.get("discord_upload_channel_id")
    if not ch_id:
        return None
    try:
        return await client.fetch_channel(int(ch_id))
    except Exception as e:
        log.warning("could not fetch upload channel %r: %s", ch_id, e)
        return None


# ------------------------------------------------------------------
# Dropdown / selection
# ------------------------------------------------------------------

class MediaRequestView(discord.ui.View):
    """Initial dropdown shown to the user after /film-anfrage or
    /serien-anfrage. TMDB results is a list of `_map_movie` / `_map_tv`
    dicts from services.tmdb."""

    def __init__(self, results: list[dict], user: discord.User, media_type: Literal["movie", "series"]):
        super().__init__(timeout=120)
        self.results = results
        self.user = user
        self.media_type = media_type
        self.add_item(_MediaSelect(results, media_type))


class _MediaSelect(discord.ui.Select):
    def __init__(self, results: list[dict], media_type: Literal["movie", "series"]):
        options: list[discord.SelectOption] = []
        for r in results[:25]:
            title = r.get("title") or ""
            year = r.get("year") or ""
            label = f"{title} ({year})" if year and year != "0000" else title
            if not label:
                continue
            options.append(discord.SelectOption(label=label[:100], value=str(r["tmdb_id"])))

        if not options:
            options.append(discord.SelectOption(label="Keine Treffer", value="0"))

        placeholder = S.SELECT_MOVIE if media_type == "movie" else S.SELECT_SERIES
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)
        self._media_type = media_type

    async def callback(self, interaction: discord.Interaction) -> None:
        tmdb_id_str = self.values[0]
        if tmdb_id_str == "0":
            await interaction.response.send_message(
                "Keine passenden Treffer.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            decision = await flow.decide(int(tmdb_id_str), self._media_type)
        except Exception as e:
            log.exception("flow.decide failed")
            await interaction.followup.send(
                S.GENERIC_ERROR.format(err=e), ephemeral=True
            )
            return

        await interaction.followup.send(
            embed=_details_embed(decision),
            view=ConfirmationView(decision, interaction.user),
            ephemeral=True,
        )


# ------------------------------------------------------------------
# Confirmation (user clicks "Bestätigen")
# ------------------------------------------------------------------

class ConfirmationView(discord.ui.View):
    def __init__(self, decision: RequestDecision, user: discord.User):
        super().__init__(timeout=300)
        self.decision = decision
        self.user = user

    @discord.ui.button(label=S.CONFIRM_LABEL, style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self._dispatch(interaction)
        except Exception as e:
            log.exception("ConfirmationView.confirm dispatch failed")
            await interaction.followup.send(S.GENERIC_ERROR.format(err=e), ephemeral=True)

    @discord.ui.button(label=S.CANCEL_LABEL, style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(S.REQUEST_CANCELLED, ephemeral=True)
        self.stop()

    async def _dispatch(self, interaction: discord.Interaction) -> None:
        d = self.decision

        # Short-circuit: already downloaded.
        if d.path is RequestPath.ALREADY_COMPLETE:
            await interaction.followup.send(
                S.ALREADY_AVAILABLE.format(title=d.title), ephemeral=True
            )
            return

        mode = await settings_store.get("discord_mode", "standard")

        if mode == "standard":
            owner = await _get_owner(interaction.client)
            if owner is None:
                await interaction.followup.send(
                    "Kein Besitzer konfiguriert. Bitte in den Einstellungen eintragen.",
                    ephemeral=True,
                )
                return
            await _send_owner_approval(owner, d, self.user)
            await interaction.followup.send(S.REQUEST_SENT, ephemeral=True)
            return

        # ---- advanced mode ----
        if d.path is RequestPath.NOT_FOUND:
            owner = await _get_owner(interaction.client)
            if owner is not None:
                await _send_owner_not_found(owner, d, self.user)
            try:
                await self.user.send(S.NOT_FOUND_DM.format(title=d.title))
            except discord.Forbidden:
                pass
            await interaction.followup.send(
                S.NOT_FOUND_DM.format(title=d.title), ephemeral=True
            )
            return

        # Found + auto-download
        await flow.enqueue_from_request(
            d,
            requester_id=str(self.user.id),
            guild_id=str(interaction.guild_id or ""),
            channel_id=str(interaction.channel_id or ""),
        )
        try:
            await self.user.send(S.DOWNLOAD_STARTED.format(title=d.title))
        except discord.Forbidden:
            pass
        await interaction.followup.send(
            S.DOWNLOAD_STARTED.format(title=d.title), ephemeral=True
        )


# ------------------------------------------------------------------
# Standard mode: owner approval view
# ------------------------------------------------------------------

async def _send_owner_approval(
    owner: discord.User, decision: RequestDecision, requester: discord.User
) -> None:
    title_label = S.OWNER_REQUEST_TITLE_MOVIE if decision.media_type == "movie" else S.OWNER_REQUEST_TITLE_SERIES
    embed = discord.Embed(title=title_label, color=discord.Color.orange())
    embed.add_field(
        name="Titel" if decision.media_type == "movie" else "Serientitel",
        value=decision.title,
        inline=False,
    )
    embed.add_field(name="Angefragt von", value=requester.mention, inline=False)
    embed.add_field(name="TMDb ID", value=str(decision.tmdb_id), inline=False)
    if decision.path is RequestPath.NEW_SEASON and decision.seasons_to_download:
        embed.add_field(
            name="Neue Staffeln",
            value=", ".join(str(s) for s in decision.seasons_to_download),
            inline=False,
        )
    if decision.poster_url:
        embed.set_thumbnail(url=decision.poster_url)
    await owner.send(embed=embed, view=OwnerApprovalView(decision, requester))


class OwnerApprovalView(discord.ui.View):
    def __init__(self, decision: RequestDecision, requester: discord.User):
        super().__init__(timeout=None)
        self.decision = decision
        self.requester = requester

    async def _disable_all(self) -> None:
        for c in self.children:
            if isinstance(c, discord.ui.Button):
                c.disabled = True

    @discord.ui.button(label="Annehmen", style=discord.ButtonStyle.primary)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        d = self.decision
        # Re-run decide() to guard against stale state (e.g. someone
        # dropped the file in the library between request and approval).
        try:
            fresh = await flow.decide(d.tmdb_id, d.media_type)
        except Exception as e:
            log.exception("re-decide on accept failed")
            await interaction.followup.send(
                S.GENERIC_ERROR.format(err=e), ephemeral=True
            )
            return

        if fresh.path is RequestPath.ALREADY_COMPLETE:
            try:
                await self.requester.send(S.ALREADY_AVAILABLE.format(title=fresh.title))
            except discord.Forbidden:
                pass
            await self._disable_all()
            await interaction.edit_original_response(
                content=f"**{fresh.title}** war bereits verfügbar.", view=self
            )
            return

        if fresh.path is RequestPath.NOT_FOUND:
            # Switch view to the manual-upload flow.
            await interaction.edit_original_response(
                content=S.OWNER_NOT_FOUND_BANNER,
                view=UploadedMarkView(fresh, self.requester),
            )
            try:
                await self.requester.send(S.NOT_FOUND_DM.format(title=fresh.title))
            except discord.Forbidden:
                pass
            return

        # Enqueue.
        msg = interaction.message
        await flow.enqueue_from_request(
            fresh,
            requester_id=str(self.requester.id),
            guild_id="",
            channel_id="",
            owner_msg_id=str(msg.id) if msg else None,
        )
        try:
            await self.requester.send(S.REQUEST_ACCEPTED.format(title=fresh.title))
        except discord.Forbidden:
            pass
        for c in self.children:
            if isinstance(c, discord.ui.Button):
                if c.label == "Annehmen":
                    c.disabled = True
                    c.label = S.OWNER_ACCEPTED_LABEL
                elif c.label == "Ablehnen":
                    c.disabled = True
        await interaction.edit_original_response(
            content=f"Anfrage für **{fresh.title}** angenommen.", view=self
        )

    @discord.ui.button(label="Ablehnen", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            _DeclineModal(self.decision, self.requester, self, interaction.message)
        )


class _DeclineModal(discord.ui.Modal, title="Grund für die Ablehnung"):
    reason = discord.ui.TextInput(label="Grund")

    def __init__(
        self,
        decision: RequestDecision,
        requester: discord.User,
        original_view: OwnerApprovalView,
        original_message: discord.Message | None,
    ):
        super().__init__()
        self.decision = decision
        self.requester = requester
        self.original_view = original_view
        self.original_message = original_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.requester.send(
                S.REQUEST_DECLINED.format(title=self.decision.title, reason=self.reason.value)
            )
        except discord.Forbidden:
            pass
        await interaction.response.send_message(
            "Der Benutzer wurde benachrichtigt.", ephemeral=True
        )
        if self.original_message is not None:
            for c in self.original_view.children:
                if isinstance(c, discord.ui.Button):
                    c.disabled = True
            await self.original_message.edit(
                content=f"Anfrage für **{self.decision.title}** abgelehnt.",
                view=self.original_view,
            )


# ------------------------------------------------------------------
# NOT_FOUND owner flow (both modes): "Als hochgeladen markieren"
# ------------------------------------------------------------------

async def _send_owner_not_found(
    owner: discord.User, decision: RequestDecision, requester: discord.User
) -> None:
    embed = discord.Embed(
        title=f"Nicht gefunden: {decision.title}",
        description=(
            "Dieser Titel wurde auf keiner Scraper-Quelle gefunden. "
            "Bitte manuell hochladen und unten als hochgeladen markieren."
        ),
        color=discord.Color.red(),
    )
    embed.add_field(name="Angefragt von", value=requester.mention, inline=False)
    embed.add_field(name="TMDb ID", value=str(decision.tmdb_id), inline=False)
    if decision.poster_url:
        embed.set_thumbnail(url=decision.poster_url)
    await owner.send(embed=embed, view=UploadedMarkView(decision, requester))


class UploadedMarkView(discord.ui.View):
    def __init__(self, decision: RequestDecision, requester: discord.User):
        super().__init__(timeout=None)
        self.decision = decision
        self.requester = requester

    @discord.ui.button(label=S.MARK_UPLOADED_LABEL, style=discord.ButtonStyle.success)
    async def mark(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Post availability embed in the upload channel. We don't know
        # how the owner structured the upload (movie vs. series vs. season)
        # so use a generic availability kind.
        channel = await _get_upload_channel(interaction.client)
        if channel is not None:
            kind = "movie" if self.decision.media_type == "movie" else "full_series"
            try:
                await channel.send(embed=_channel_embed(self.decision, kind, None))
            except Exception as e:
                log.warning("uploaded: channel post failed: %s", e)
        # DM requester.
        try:
            await self.requester.send(S.DONE_USER_DM.format(title=self.decision.title))
        except discord.Forbidden:
            pass
        for c in self.children:
            if isinstance(c, discord.ui.Button):
                c.disabled = True
        await interaction.response.edit_message(
            content=S.MARK_UPLOADED_DONE.format(title=self.decision.title), view=self
        )
