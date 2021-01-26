import json

import db
import discord
from config import config
from db import Guild, Topic
from discord.ext import commands
from discord_slash import SlashCommand, SlashContext, cog_ext
from discord_slash.error import RequestFailure
from discord_slash.utils import manage_commands
from pony.orm import db_session, select, commit

MAX_SUBCOMMANDS_ERROR_CODE = 50035

WIKI_COMMAND = "wiki"
WIKI_MANAGEMENT_COMMAND = "wiki-mgmt"


class Slash(commands.Cog):
    def __init__(self, bot):
        if not hasattr(bot, "slash"):
            # Creates new SlashCommand instance to bot if bot doesn't have.
            bot.slash = SlashCommand(
                bot, override_type=True, auto_register=True, auto_delete=True
            )
        self.bot = bot
        self.bot.slash.get_cog_commands(self)

    async def reload_commands(self):
        self.bot.slash.get_cog_commands(self)
        await self.bot.slash.register_all_commands()

    def cog_unload(self):
        self.bot.slash.remove_cog_commands(self)

    @cog_ext.cog_slash(name="test")
    async def _test(self, ctx: SlashContext):
        embed = discord.Embed(title="embed test")
        await ctx.send(content="test", embeds=[embed])

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        name="upsert",
        description="add or modify a topic",
        guild_ids=config.guild_ids,
        options=[
            manage_commands.create_option(
                name="group",
                description=f"group used with /{WIKI_COMMAND} <group>",
                option_type=3,
                required=True,
            ),
            manage_commands.create_option(
                name="key",
                description=f"key used with /{WIKI_COMMAND} <group> <key>",
                option_type=3,
                required=True,
            ),
            manage_commands.create_option(
                name="description",
                description=f"description which will appear in the UI",
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
    @db_session
    async def _topic_upsert(
        self, ctx: SlashContext, group: str, key: str, description: str, content: str
    ):
        adding = False
        topic = Topic.select(
            lambda t: t.guild.id == str(ctx.guild.id)
            and t.group == group
            and t.key == key
        ).first()

        if topic is None:
            topic = Topic(
                guild=str(ctx.guild.id),
                group=group,
                key=key,
                desc=description,
                content=content,
            )
            adding = True
        else:
            topic.desc = description
            topic.content = content
            adding = False

        # TODO: remove this and figure out how to make @db_session work with async
        commit()

        self.bot.slash.remove_cog_commands(self)
        add_wiki_command(ctx.guild.id, group, key, description, content)

        action = "added" if adding else "modified"
        try:
            await self.reload_commands()
        except RequestFailure as e:
            error = json.loads(e.msg)
            if error["code"] == MAX_SUBCOMMANDS_ERROR_CODE:
                await ctx.send(
                    content=f"Failed to upsert topic **{group}/{key}**.\n"
                    + f"You have reached maximum number of topics for the **{group}** group. Please add this topic to another group.\n"
                    + f"See bot logs for more details.",
                    hidden=True,
                )
            else:
                await ctx.send(
                    content=f"Failed to upsert topic **{group}/{key}**. See bot logs.",
                    hidden=True,
                )
            raise e

        await ctx.send(content=f"**{group}/{key}** was {action}.", hidden=True)

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        name="delete",
        description="delete topic",
        guild_ids=config.guild_ids,
        options=[
            manage_commands.create_option(
                name="group",
                description=f"group used with /{WIKI_COMMAND} <group>",
                option_type=3,
                required=True,
            ),
            manage_commands.create_option(
                name="key",
                description=f"key used with /{WIKI_COMMAND} <group> <key>",
                option_type=3,
                required=True,
            ),
        ],
    )
    @db_session
    async def _topic_delete(self, ctx: SlashContext, group: str, key: str):
        topic = Topic.select(
            lambda t: t.guild.id == str(ctx.guild.id)
            and t.group == group
            and t.key == key
        ).first()

        print("topic is", topic)

        if topic is None:
            await ctx.send(
                content=f"**{group}/{key}** is not in the database.",
                hidden=True,
            )
            return

        self.bot.slash.remove_cog_commands(self)
        delete_wiki_command(ctx.guild.id, group, key)
        topic.delete()
        # TODO: remove this and figure out how to make @db_session work with async
        commit()

        await self.reload_commands()
        await ctx.send(content=f"**{group}/{key}** was deleted.", hidden=True)


@db_session
def setup_wiki_commands():
    for topic in Topic.select():
        add_wiki_command(
            int(topic.guild.id),
            topic.group,
            topic.key,
            topic.desc,
            topic.content,
        )


# We need to dynamically add slash commands for every Wiki topic
# but slash extension only allows that via class methods
# so we need to assign a method decorated with cog_ext.cog_subcommand
# under some unique name
# TODO: super hack, rewrite
def add_wiki_command(guild: int, group: str, key: str, desc: str, content: str):
    setattr(
        Slash,
        f"__{guild}_{group}_{key}",
        cog_ext.cog_subcommand(
            base=WIKI_COMMAND,
            name=key,
            description=desc,
            subcommand_group=group,
            options=[
                manage_commands.create_option(
                    name="public",
                    description="make the response be visible for everyone else in the channel",
                    option_type=5,
                    required=False,
                ),
            ],
            guild_ids=[guild],
        )(topic_handler(content)),
    )


def delete_wiki_command(guild: int, group: str, key: str):
    setattr(Slash, f"__{guild}_{group}_{key}", None)


def topic_handler(content: str):
    async def _handler(self: Slash, ctx: SlashContext, public: bool = False):
        await ctx.send(content=content, hidden=not public)

    return _handler


def setup(bot):
    setup_wiki_commands()
    bot.add_cog(Slash(bot))
