import os

import discord
from discord.ext import commands
from discord_slash import SlashCommand
from discord_slash import SlashContext
from discord_slash.utils import (
    manage_commands,
)

from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_IDS = [int(v) for v in os.getenv("DISCORD_GUILD_IDS").split(",")]

intents = discord.Intents()
intents.messages = True
client = discord.Client(intents=intents)
slash = SlashCommand(client, auto_register=True)


@client.event
async def on_ready():
    print(" ".join([f"{guild.name}: id: {guild.id}" for guild in client.guilds]))


db = {
    "wdt": "WDT is a technique to evently distribute coffe grounds and break clamps. More information: https://youtu.be/B3SsJhjP-Vo",
    "dial": "https://espressoaf.com/guides/beginner.html",
}

commands = {
    "compass": "Espresso compass",
    "dial": "Dialling in basics",
    "grindtime": "Entry level grinders",
    "phamboy": "Mid-range grinders",
    "machinery": "Entry level machines",
    "wdt": "WDT guide",
    "marker": "Alignment marker test",
    "profiles": "Coffee profiling",
    "acc": "Accessory Guide",
}


@slash.slash(
    name="espresso",
    description="help with espresso",
    options=[
        manage_commands.create_option(
            name="topic",
            description="help topic",
            option_type=3,
            required=True,
            choices=[{"name": v, "value": k} for (k, v) in commands.items()],
        ),
        manage_commands.create_option(
            name="public",
            description="make the response to be visible for everyone in the channel",
            option_type=5,
            required=False,
        ),
    ],
    guild_ids=GUILD_IDS,
)
async def _help(ctx, topic: str, public: bool = False):
    if topic in db:
        await ctx.send(content=db[topic], hidden=not public)
    else:
        await ctx.send(content=f"We don't have anything for {topic}", hidden=not public)


client.run(TOKEN)
