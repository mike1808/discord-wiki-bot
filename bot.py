import os

import discord
from discord.ext import commands
from discord_slash import SlashCommand
from discord_slash import SlashContext
from discord_slash.utils import (
    manage_commands,
)
from discord_slash.error import (
    RequestFailure,
)
import json

from dotenv import load_dotenv
from db import db

import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("discord_slash").setLevel(logging.DEBUG)

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_IDS = [int(v) for v in os.getenv("DISCORD_GUILD_IDS").split(",")]

MAX_SUBCOMMANDS_ERROR_CODE = 50035

intents = discord.Intents()
intents.messages = True
client = discord.Client(intents=intents)
slash = SlashCommand(client, auto_register=True, auto_delete=True)


@client.event
async def on_ready():
    print(" ".join([f"{guild.name}: id: {guild.id}" for guild in client.guilds]))


def add_topic(topic: str, group: str, description: str, content: str):
    slash.subcommand(
        base="wiki",
        name=topic,
        description=description,
        subcommand_group=group,
        options=[
            manage_commands.create_option(
                name="public",
                description="make the response be visible for everyone else in the channel",
                option_type=5,
                required=False,
            ),
        ],
        guild_ids=GUILD_IDS,
    )(topic_handler(content))


def topic_handler(content: str):
    async def _handler(ctx, public: bool = False):
        await ctx.send(content=content, hidden=not public)

    return _handler


for (_, topic) in db.items():
    add_topic(topic.key, topic.group, topic.description, topic.content)


@slash.subcommand(
    base="wikimgmt",
    name="upsert",
    description="add or modify a topic",
    guild_ids=GUILD_IDS,
    options=[
        manage_commands.create_option(
            name="group",
            description="group used with /wiki <group>",
            option_type=3,
            required=True,
        ),
        manage_commands.create_option(
            name="topic",
            description="topic used with /wiki <group> <topic>",
            option_type=3,
            required=True,
        ),
        manage_commands.create_option(
            name="description",
            description="description which will appear in the UI",
            option_type=3,
            required=True,
        ),
        manage_commands.create_option(
            name="content",
            description="a message sent to the user",
            option_type=3,
            required=True,
        ),
    ],
)
async def _topic_upsert(ctx, group: str, topic: str, description: str, content: str):
    adding = True
    if topic in db:
        del slash.subcommands["wiki"][db[topic].group][topic]
        del db[topic]
        await slash.delete_unused_commands()
        adding = False

    db[topic] = Topic(topic, group, description, content)
    add_topic(topic, group, description, content)

    action = "added" if adding else "modified"
    try:
        await slash.register_all_commands()
    except RequestFailure as e:
        error = json.loads(e.msg)
        if error["code"] == MAX_SUBCOMMANDS_ERROR_CODE:
            await ctx.send(
                content=f"Failed to upsert topic **{topic}**.\n"
                + f"You have reached maximum number of topics for the **{group}** group. Please add this topic to another group.\n"
                + f"See bot logs for more details.",
                hidden=True,
            )
        else:
            await ctx.send(
                content=f"Failed to upsert topic **{topic}**. See bot logs.",
                hidden=True,
            )
        return

    await ctx.send(content=f"Topic **{topic}** was {action}.", hidden=True)


@slash.subcommand(
    base="wikimgmt",
    name="delete",
    description="delete topic",
    guild_ids=GUILD_IDS,
    options=[
        manage_commands.create_option(
            name="topic",
            description="topic used with /wiki",
            option_type=3,
            required=True,
        ),
    ],
)
async def _topic_delete(ctx, topic: str):
    if topic not in db:
        await ctx.send(
            content=f"Topic **{topic}** is not in the database.",
            hidden=True,
        )
        return

    del slash.subcommands["wiki"][db[topic].group][topic]
    del db[topic]

    await slash.register_all_commands()

    await ctx.send(content=f"Topic **{topic}** was deleted.", hidden=True)


client.run(TOKEN)
