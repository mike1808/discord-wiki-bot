import logging

import db
import discord
from config import config
from db import Guild, Topic
from discord.ext import commands
from discord_slash import SlashCommand, SlashContext, cog_ext
from discord_slash.error import RequestFailure
from discord_slash.utils import manage_commands
from pony.orm import db_session, select


def setup():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("discord_slash").setLevel(logging.DEBUG)

    db.setup()


class HelpBot(commands.Bot):
    async def on_ready(self):
        print(" ".join([f"{guild.name}: id: {guild.id}" for guild in self.guilds]))

    async def on_guild_join(self, guild: discord.Guild):
        # TODO: make a DB entry
        print("We have been added to a new guild!")


setup()

intents = discord.Intents()
intents.messages = True
intents.guilds = True

bot = HelpBot(
    "/", intents=intents, allowed_mentions=discord.AllowedMentions(everyone=False)
)
client = discord.Client()
client.event
bot.load_extension("slash")

if __name__ == "__main__":
    bot.run(config.discord_token)
