import logging

import discord
from discord.ext import commands
from discord_slash import SlashCommand, SlashContext, cog_ext
from discord_slash.error import RequestFailure
from discord_slash.utils import manage_commands
from pony.orm import db_session, select

from bot import db
from bot.config import config
from bot.db import Guild, Topic

logger = logging.getLogger("wikibot.bot")

logging.getLogger("wikibot").setLevel(logging.DEBUG)


def setup():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("discord_slash").setLevel(logging.DEBUG)

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
        logger.info(
            f"We have been added to a new guild! Hi: f{guild.id}: f{guild.name}"
        )
        db.upsert_guild(str(guild.id), guild.name)


setup()

intents = discord.Intents()
intents.messages = True
intents.guilds = True

bot = HelpBot(
    "/", intents=intents, allowed_mentions=discord.AllowedMentions(everyone=False)
)
bot.load_extension("bot.slash")

if __name__ == "__main__":
    bot.run(config.discord_token)
