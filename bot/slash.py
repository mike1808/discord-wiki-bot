import csv
import datetime
import functools
import io
import json
import logging

import discord
import discord.ext.commands
import discord_slash.error
from discord.ext import commands
from discord_slash import SlashCommand, SlashContext, cog_ext
from discord_slash.utils import manage_commands
from pony.orm import commit, db_session, select

from bot import db
from bot.analytics import Analytics
from bot.config import config
from bot.db import Guild, Topic

MAX_SUBCOMMANDS_ERROR_CODE = 50035

WIKI_COMMAND = "wiki"
WIKI_MANAGEMENT_COMMAND = "wiki-mgmt"

MANAGE_CHANNELS = discord.Permissions()
MANAGE_CHANNELS.manage_channels = True


def allow_only(permissions: discord.Permissions):
    def decorate(func):
        @functools.wraps(func)
        async def wrapper(self, ctx: SlashContext, *args, **kwargs):
            user: discord.Member = (
                ctx.author
                if not isinstance(ctx.author, int)
                else await ctx.guild.fetch_member(ctx.author)
            )
            if user.guild_permissions >= permissions:
                return await func(self, ctx, *args, **kwargs)
            else:
                await ctx.respond()
                author_id = (
                    ctx.author.id
                    if isinstance(ctx.author, discord.Member)
                    else ctx.author
                )
                self.logger.info("Denied access to member: %d", author_id)
                return await ctx.send(
                    content="You are not allowed to manage Wiki topics!",
                )

        return wrapper

    return decorate


class Slash(commands.Cog):
    def __init__(self, bot: discord.ext.commands.Bot):
        if not hasattr(bot, "slash"):
            # Creates new SlashCommand instance to bot if bot doesn't have.
            bot.slash = SlashCommand(
                bot, override_type=True, auto_register=True, auto_delete=True
            )
        self.bot = bot
        self.bot.slash.get_cog_commands(self)
        self.analytics = Analytics()
        self.logger = logging.getLogger("wikibot.slash")

    async def reload_commands(self):
        self.bot.slash.get_cog_commands(self)
        await self.bot.slash.register_all_commands()

    def cog_unload(self):
        self.bot.slash.remove_cog_commands(self)

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        name="upsert",
        description="add or modify a topic",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
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
    @allow_only(MANAGE_CHANNELS)
    @db_session
    async def _topic_upsert(
        self, ctx: SlashContext, group: str, key: str, description: str, content: str
    ):
        await ctx.respond()
        topic, new = db.upsert_topic(
            str(ctx.guild.id), group, key, description, content
        )

        author_id = (
            ctx.author.id if isinstance(ctx.author, discord.Member) else ctx.author
        )
        self.logger.info(
            f"upserting new topic: %d /{WIKI_COMMAND} %s %s %s %s by member: %d",
            ctx.guild.id,
            group,
            key,
            description,
            content,
            author_id,
        )

        # TODO: remove this and figure out how to make @db_session work with async
        commit()

        self.bot.slash.remove_cog_commands(self)
        add_wiki_command(ctx.guild.id, group, key, description, content)

        action = "added" if new else "modified"
        try:
            await self.reload_commands()
            await ctx.send(content=f"**{group}/{key}** was {action}.", hidden=True)
        except discord_slash.error.RequestFailure as e:
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
            failed = True

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        name="delete",
        description="delete topic",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
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
    @allow_only(MANAGE_CHANNELS)
    @db_session
    async def _topic_delete(self, ctx: SlashContext, group: str, key: str):
        await ctx.respond()
        topic = Topic.select(
            lambda t: t.guild.id == str(ctx.guild.id)
            and t.group == group
            and t.key == key
        ).first()

        if topic is None:
            await ctx.send(
                content=f"**{group}/{key}** is not in the database.",
                hidden=True,
            )
            return

        author_id = (
            ctx.author.id if isinstance(ctx.author, discord.Member) else ctx.author
        )
        self.logger.info(
            f"deleteing topic: %d /{WIKI_COMMAND} %s %s by member: %d",
            ctx.guild.id,
            group,
            key,
            author_id,
        )
        self.bot.slash.remove_cog_commands(self)
        delete_wiki_command(ctx.guild.id, group, key)
        topic.delete()
        # TODO: remove this and figure out how to make @db_session work with async
        commit()

        await self.reload_commands()
        await ctx.send(content=f"**{group}/{key}** was deleted.", hidden=True)

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        name="analytics",
        description="get commands usage analytics",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
    )
    @allow_only(MANAGE_CHANNELS)
    @db_session
    async def _analytics(self, ctx: SlashContext):
        await ctx.respond()
        views = self.analytics.retreive(
            ctx.guild.id if not isinstance(ctx.guild, int) else ctx.guild
        )

        embed = discord.Embed(
            title="Wiki Analytics", color=discord.Color.from_rgb(225, 225, 225)
        )
        embed.set_footer(text=self.bot.user, icon_url=self.bot.user.avatar_url)
        for (command, view_count) in views:
            embed.add_field(name=command, value=str(view_count), inline=False)

        await ctx.send(embed=embed)

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        subcommand_group="bulk",
        name="help",
        description=f"show help with `/{WIKI_COMMAND} import` commands",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
    )
    @allow_only(MANAGE_CHANNELS)
    async def _bulk_help(self, ctx: SlashContext):
        await ctx.respond()
        await ctx.send(
            content='Bulk import and export commands consume and produce CSV files. CSV files should be delimited with a single quota `,` and use double quotes `"`.'
            + "\nIt should contain 4 columns and the header is optional. Those columns are `group,key,description,content.`."
            + "\nTo import your topics you should create a CSV file and upload it to Discord in the same channel where you are going to use the import command."
            + f"\nThen you have to use the `/{WIKI_COMMAND} bulk import` command to import the topics."
            + "\nWikiBot will search the latest 5 messages in the channel and select the latest your message and try to download your CSV file."
            + "\nThen it will import all provided topics. Be careful! It will override the description and content of all topics currently created.",
            hidden=True,
        )

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        subcommand_group="bulk",
        name="export",
        description=f"export all existing topics to CSV file",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
    )
    @allow_only(MANAGE_CHANNELS)
    @db_session
    async def _bulk_export(self, ctx: SlashContext):
        await ctx.respond()
        author_id = (
            ctx.author.id if isinstance(ctx.author, discord.Member) else ctx.author
        )
        self.logger.info(
            f"sending export by request of member: %d",
            author_id,
        )

        csvoutput = io.StringIO()
        csvwriter = csv.writer(csvoutput, quoting=csv.QUOTE_MINIMAL)
        csvwriter.writerow(["group", "key", "desc", "content"])
        count = 0

        for t in Topic.select(lambda t: t.guild.id == str(ctx.guild.id)):
            csvwriter.writerow([t.group, t.key, t.desc, t.content])
            count += 1

        await ctx.send(
            content=f"We successfuly exported f{count} topcs!",
            file=discord.File(
                io.BytesIO(str.encode(csvoutput.getvalue())),
                filename=f"wiki_topics_{datetime.datetime.utcnow()}.csv",
            ),
        )

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        subcommand_group="bulk",
        name="import",
        description=f"import topics from CSV file",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
    )
    @allow_only(MANAGE_CHANNELS)
    @db_session
    async def _bulk_import(self, ctx: SlashContext):
        await ctx.respond()

        author_id = (
            ctx.author.id if isinstance(ctx.author, discord.Member) else ctx.author
        )
        self.logger.info(
            f"trying to import by request of member: %d",
            author_id,
        )

        csvcontent = None
        try:
            async for msg in ctx.channel.history(limit=5):
                if len(msg.attachments) > 0:
                    print(msg.attachments[0].filename)
                if (
                    msg.author.id == author_id
                    and len(msg.attachments) > 0
                    and msg.attachments[0].filename.endswith(".csv")
                ):
                    csvcontent = io.BytesIO()
                    await msg.attachments[0].save(csvcontent)

        except (
            discord.Forbidden,
            discord.HTTPException,
            discord.NotFound,
            TypeError,
            ValueError,
        ) as e:
            self.logger.warning(
                e,
                exc_info=True,
            )
            return await ctx.send(
                content="Couldn't find message with CSV file to import. Aborting.",
                hidden=True,
            )

        if csvcontent is None:
            return await ctx.send(
                content="Couldn't find message with CSV file to import. Aborting.",
                hidden=True,
            )

        self.bot.slash.remove_cog_commands(self)

        added = 0
        updated = 0

        csvreader = csv.reader(
            io.StringIO(csvcontent.read().decode("utf-8")), quoting=csv.QUOTE_MINIMAL
        )
        for row in csvreader:
            topic, new = db.upsert_topic(
                str(ctx.guild.id), row[0], row[1], row[2], row[3]
            )
            if new:
                added += 1
            else:
                updated += 1

            add_wiki_command(
                ctx.guild.id, topic.group, topic.key, topic.desc, topic.content
            )

        # TODO: remove this and figure out how to make @db_session work with async
        commit()
        await self.reload_commands()
        await ctx.send(
            content=f"Import was successfuly finished! {added} added and {updated} updated.",
        )


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
                    name="reply_to",
                    description="reply to the last message of specified user",
                    option_type=6,
                    required=False,
                ),
                manage_commands.create_option(
                    name="public",
                    description="make the response be visible for everyone else in the channel",
                    option_type=5,
                    required=False,
                ),
            ],
            guild_ids=[guild],
        )(topic_handler(f"{group}/{key}", content)),
    )


def delete_wiki_command(guild: int, group: str, key: str):
    setattr(Slash, f"__{guild}_{group}_{key}", None)


def topic_handler(command_name: str, content: str):
    async def _handler(
        self: Slash,
        ctx: SlashContext,
        reply_to: discord.Member = None,
        public: bool = False,
    ):
        await ctx.respond(eat=True)
        if reply_to:
            try:
                async for msg in ctx.channel.history(limit=10):
                    if msg.author == reply_to:
                        return await msg.reply(content)
            except (
                discord.Forbidden,
                discord.HTTPException,
                discord.NotFound,
                TypeError,
                ValueError,
            ):
                await ctx.send(
                    content="Couldn't find message to reply. Normally sending content.",
                    hidden=True,
                )

        await ctx.send(content=content, hidden=not public)

        self.analytics.view(
            ctx.guild.id if not isinstance(ctx.guild, int) else ctx.guild, command_name
        )

    return _handler


def setup(bot):
    setup_wiki_commands()
    bot.add_cog(Slash(bot))
