import logging

import discord
from discord.ext import commands
from discord_slash import SlashCommand, SlashContext, cog_ext
from discord_slash.error import RequestFailure
from discord_slash.utils import manage_commands
from pony.orm import db_session, select, ObjectNotFound

from bot import db
from bot.config import config
from bot.db import Guild, Topic

logger = logging.getLogger("wikibot.bot")

logging.getLogger("wikibot").setLevel(logging.DEBUG)


def setup():
    logging.basicConfig(level=logging.INFO)
    # logging.getLogger("discord_slash").setLevel(logging.DEBUG)

    logger.info("Runing DB setup")
    db.setup()

    if config.db.populate:
        logger.info("Requested DB population. Running.")
        db.populate_database()


class HelpBot(commands.Bot):
    @db_session
    async def on_ready(self):
        for guild in self.guilds:
            db.upsert_guild(str(guild.id), guild.name)
            print(f"{guild.name}: id: {guild.id}")

    @db_session
    async def on_guild_join(self, guild: discord.Guild):
        logger.info(f"We have been added to a new guild! Hi: f{guild.id}: f{guild.name}")
        try:
            guild = db.Guild[str(guild.id)]
            guild.disabled = False
        except ObjectNotFound:
            db.upsert_guild(str(guild.id), guild.name)

    @db_session
    async def on_guild_remove(self, guild: discord.Guild):
        logger.info(f"We have been removed from the guild guild! Bye: f{guild.id}: f{guild.name}")
        db.mark_guild_disabled(str(guild.id))


setup()

intents = discord.Intents()
intents.messages = True
intents.guilds = True

bot = HelpBot(
    "$",
    intents=intents,
    allowed_mentions=discord.AllowedMentions(everyone=False),
    help_command=commands.DefaultHelpCommand(),
)
slash = SlashCommand(bot)
bot.load_extension("bot.slash")

if __name__ == "__main__":
    bot.run(config.discord_token)
