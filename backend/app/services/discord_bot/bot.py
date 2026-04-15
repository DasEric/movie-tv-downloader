"""
The `commands.Bot` subclass and the two slash commands.

Keeps things flat — no cogs. The commands are registered in setup_hook().
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.services import settings_store, tmdb
from app.services.discord_bot import strings as S
from app.services.discord_bot.views import MediaRequestView

log = logging.getLogger(__name__)


def make_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.message_content = True
    return intents


class PlexDownloaderBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="/", intents=make_intents())

    async def setup_hook(self) -> None:
        await self._register_commands()
        guild_id = await settings_store.get("discord_guild_id") or ""
        if guild_id:
            try:
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info(
                    "synced %d commands to guild %s (instant)", len(synced), guild_id
                )
            except Exception as e:
                log.warning("guild-scoped sync failed, falling back to global: %s", e)
                try:
                    synced = await self.tree.sync()
                    log.info("synced %d commands globally", len(synced))
                except Exception as e2:
                    log.warning("global sync failed: %s", e2)
        else:
            try:
                synced = await self.tree.sync()
                log.info(
                    "synced %d commands globally (~1h to propagate)", len(synced)
                )
            except Exception as e:
                log.warning("global sync failed: %s", e)

    async def on_ready(self) -> None:
        if self.user:
            log.info("discord bot logged in as %s (id=%s)", self.user, self.user.id)

    async def _register_commands(self) -> None:
        self.tree.add_command(_film_anfrage)
        self.tree.add_command(_serien_anfrage)


# ----------------------------------------------------------------------
# Role check
# ----------------------------------------------------------------------

def _has_request_role():
    async def predicate(interaction: discord.Interaction) -> bool:
        role_id_s = await settings_store.get("discord_request_role_id") or ""
        if not role_id_s:
            # No role configured → everyone may request.
            return True
        try:
            role_id = int(role_id_s)
        except ValueError:
            return True
        member = interaction.user
        if isinstance(member, discord.Member):
            if any(r.id == role_id for r in member.roles):
                return True
        return False

    return app_commands.check(predicate)


# ----------------------------------------------------------------------
# Slash commands
# ----------------------------------------------------------------------

@app_commands.command(name="film-anfrage", description="Frage einen Film an")
@app_commands.describe(movie_name="Der Name des Films, den du anfragen möchtest")
@_has_request_role()
async def _film_anfrage(interaction: discord.Interaction, movie_name: str) -> None:
    results = await tmdb.search_movie(movie_name)
    if not results:
        await interaction.response.send_message(S.NO_RESULTS_MOVIE, ephemeral=True)
        return
    await interaction.response.send_message(
        S.PICK_PROMPT_MOVIE,
        view=MediaRequestView(results, interaction.user, "movie"),
        ephemeral=True,
    )


@app_commands.command(name="serien-anfrage", description="Frage eine Serie an")
@app_commands.describe(series_name="Der Name der Serie, die du anfragen möchtest")
@_has_request_role()
async def _serien_anfrage(interaction: discord.Interaction, series_name: str) -> None:
    results = await tmdb.search_tv(series_name)
    if not results:
        await interaction.response.send_message(S.NO_RESULTS_SERIES, ephemeral=True)
        return
    await interaction.response.send_message(
        S.PICK_PROMPT_SERIES,
        view=MediaRequestView(results, interaction.user, "series"),
        ephemeral=True,
    )


@_film_anfrage.error
async def _film_err(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    await _handle_cmd_error(interaction, error)


@_serien_anfrage.error
async def _serien_err(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    await _handle_cmd_error(interaction, error)


async def _handle_cmd_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.CheckFailure):
        msg = S.MISSING_ROLE
    else:
        msg = S.GENERIC_ERROR.format(err=error)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        log.exception("failed to report command error")
