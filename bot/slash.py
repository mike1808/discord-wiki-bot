import csv
import datetime
import functools
import io
import json
import logging
import typing

import discord
import discord.ext.commands
import discord_slash.error
from discord.ext import commands
from discord_slash import SlashCommand, SlashCommandOptionType, SlashContext, cog_ext
from discord_slash.utils import manage_commands
import discord_slash.model
from pony.orm import commit, db_session, select

from bot import db
from bot.analytics import Analytics
from bot.config import config
from bot.db import Guild, Topic
from bot.feedback import Feedback
from bot.util import check_has_permissions, Context

MAX_SUBCOMMANDS_ERROR_CODE = 50035

WIKI_COMMAND = config.command_prefix + "wiki"
WIKI_MANAGEMENT_COMMAND = config.command_prefix + "wiki-mgmt"

MANAGE_CHANNELS = discord.Permissions()
MANAGE_CHANNELS.manage_channels = True


class Slash(commands.Cog):
    def __init__(self, bot: discord.ext.commands.Bot):
        if not hasattr(bot, "slash"):
            # Creates new SlashCommand instance to bot if bot doesn't have.
            bot.slash = SlashCommand(bot, override_type=True, auto_register=True, auto_delete=True)

        self.bot = bot
        self.slash = bot.slash

        self.bot.loop.create_task(self._setup_wiki_commands())

        self.slash.get_cog_commands(self)

        self.analytics = Analytics()
        self.logger = logging.getLogger("wikibot.slash")
        self.feedback = Feedback()

    def cog_unload(self):
        self.slash.remove_cog_commands(self)
        self.feedback.close()

    async def _reload_commands(self):
        await self.slash.register_all_commands()

    @commands.command(name=WIKI_COMMAND)
    async def _fallback_wiki_command(self, ctx: commands.Context, *args):
        def find_slash_command(
            group, path
        ) -> typing.Tuple[typing.Union[discord_slash.model.SubcommandObject, None], list[str]]:
            if len(path) == 0 or ":" in path[0]:
                return (group, path)

            if path[0] in group:
                return find_slash_command(group[path[0]], path[1:])

            return (None, path)

        command, command_args = find_slash_command(self.slash.subcommands[WIKI_COMMAND], args)
        if command is None:
            return

        guild_id = ctx.guild.id if not isinstance(ctx.guild, int) else ctx.guild
        if guild_id not in command.allowed_guild_ids:
            return

        command_args = parse_command_args(
            command_args,
        )
        if "reply_to" in command_args:
            cached = ctx.guild.get_member(int(command_args["reply_to"]))
            if cached:
                command_args["reply_to"] = cached
            else:
                command_args["reply_to"] = await ctx.guild.fetch_member(int(command_args["reply_to"]))
        else:
            command_args["reply_to"] = None

        await command.invoke(*[Context(ctx), command_args["reply_to"], False])

    @db_session
    async def _setup_wiki_commands(self):
        for topic in Topic.select():
            self.__add_wiki_command(
                int(topic.guild.id),
                topic.group,
                topic.key,
                topic.desc,
                topic.content,
            )

    def _topic_handler(self, command_name: str, content: str):
        async def _wiki_topic(
            ctx: Context,
            reply_to: discord.Member = None,
            public: bool = False,
        ):
            await ctx.respond(eat=False)
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
                ctx.guild.id if not isinstance(ctx.guild, int) else ctx.guild,
                command_name,
            )

        return _wiki_topic

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        name="upsert",
        description="Add or modify a topic",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
        options=[
            manage_commands.create_option(
                name="group",
                description=f"Group used with /{WIKI_COMMAND} <group>",
                option_type=SlashCommandOptionType.STRING,
                required=True,
            ),
            manage_commands.create_option(
                name="key",
                description=f"Key used with /{WIKI_COMMAND} <group> <key>",
                option_type=SlashCommandOptionType.STRING,
                required=True,
            ),
            manage_commands.create_option(
                name="description",
                description=f"Description which will appear in the UI",
                option_type=SlashCommandOptionType.STRING,
                required=True,
            ),
            manage_commands.create_option(
                name="content",
                description="A message sent to the user",
                option_type=SlashCommandOptionType.STRING,
                required=True,
            ),
        ],
    )
    @check_has_permissions(manage_channels=True)
    @db_session
    async def _topic_upsert(self, ctx: SlashContext, group: str, key: str, description: str, content: str):
        await ctx.respond()
        topic, new = db.upsert_topic(str(ctx.guild.id), group, key, description, content)

        author_id = ctx.author.id if not isinstance(ctx.author, int) else ctx.author
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

        self.__add_wiki_command(ctx.guild.id, group, key, description, content)

        action = "added" if new else "modified"
        try:
            await self._reload_commands()
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
        description="Delete topic",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
        options=[
            manage_commands.create_option(
                name="group",
                description=f"Group used with /{WIKI_COMMAND} <group>",
                option_type=SlashCommandOptionType.STRING,
                required=True,
            ),
            manage_commands.create_option(
                name="key",
                description=f"Key used with /{WIKI_COMMAND} <group> <key>",
                option_type=SlashCommandOptionType.STRING,
                required=True,
            ),
        ],
    )
    @check_has_permissions(manage_channels=True)
    @db_session
    async def _topic_delete(self, ctx: SlashContext, group: str, key: str):
        await ctx.respond()
        topic = Topic.select(lambda t: t.guild.id == str(ctx.guild.id) and t.group == group and t.key == key).first()

        if topic is None:
            await ctx.send(
                content=f"**{group}/{key}** is not in the database.",
                hidden=True,
            )
            return

        author_id = ctx.author.id if not isinstance(ctx.author, int) else ctx.author
        self.logger.info(
            f"deleteing topic: %d /{WIKI_COMMAND} %s %s by member: %d",
            ctx.guild.id,
            group,
            key,
            author_id,
        )
        self.__delete_wiki_command(ctx.guild.id, group, key)
        topic.delete()
        # TODO: remove this and figure out how to make @db_session work with async
        commit()

        await self._reload_commands()
        await ctx.send(content=f"**{group}/{key}** was deleted.", hidden=True)

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        name="analytics",
        description="Get commands usage analytics",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
    )
    @check_has_permissions(manage_channels=True)
    @db_session
    async def _analytics(self, ctx: SlashContext):
        await ctx.respond()
        views = self.analytics.retreive(ctx.guild.id if not isinstance(ctx.guild, int) else ctx.guild)

        embed = discord.Embed(title="Wiki Analytics", color=discord.Color.from_rgb(225, 225, 225))
        embed.set_footer(text=self.bot.user, icon_url=self.bot.user.avatar_url)
        for (command, view_count) in views:
            embed.add_field(name=command, value=str(view_count), inline=False)

        await ctx.send(embed=embed)

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        subcommand_group="bulk",
        name="help",
        description=f"Show help with `/{WIKI_MANAGEMENT_COMMAND} import` commands",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
    )
    @check_has_permissions(manage_channels=True)
    async def _bulk_help(self, ctx: SlashContext):
        await ctx.respond(eat=True)
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
        description=f"Export all existing topics to CSV file",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
    )
    @check_has_permissions(manage_channels=True)
    @db_session
    async def _bulk_export(self, ctx: SlashContext):
        await ctx.respond()
        author_id = ctx.author.id if not isinstance(ctx.author, int) else ctx.author
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
        description=f"Import topics from CSV file",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
    )
    @check_has_permissions(manage_channels=True)
    @db_session
    async def _bulk_import(self, ctx: SlashContext):
        await ctx.respond()

        author_id = ctx.author.id if isinstance(ctx.author, discord.Member) else ctx.author
        self.logger.info(
            f"trying to import by request of member: %d",
            author_id,
        )

        csvcontent = None
        try:
            async for msg in ctx.channel.history(limit=5):
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

        added = 0
        updated = 0

        csvreader = csv.reader(io.StringIO(csvcontent.read().decode("utf-8")), quoting=csv.QUOTE_MINIMAL)
        for row in csvreader:
            topic, new = db.upsert_topic(str(ctx.guild.id), row[0], row[1], row[2], row[3])
            if new:
                added += 1
            else:
                updated += 1

            self.__add_wiki_command(ctx.guild.id, topic.group, topic.key, topic.desc, topic.content)

        # TODO: remove this and figure out how to make @db_session work with async
        commit()
        await self._reload_commands()
        await ctx.send(
            content=f"Import was successfuly finished! {added} added and {updated} updated.",
        )

    @cog_ext.cog_subcommand(
        base=WIKI_COMMAND,
        name="feedback",
        description=f"Leave feedback to the bot developer",
        options=[
            manage_commands.create_option(
                name="feedback",
                description="Feedback message",
                option_type=SlashCommandOptionType.STRING,
                required=True,
            ),
        ],
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
    )
    @db_session
    async def _feedback(self, ctx: SlashContext, feedback: str):
        await ctx.respond(eat=True)

        author: discord.Member = await ctx.guild.fetch_member(ctx.author)

        self.logger.info(
            f"member: %d:%s gave feedback",
            author.id,
            author.display_name,
        )

        try:
            self.feedback.send_feedback(author.id, author.name, ctx.guild.id, ctx.guild.name, feedback)
        except Exception as e:
            self.logger.critical("Failed to send feeback", e, exc_info=True)

        await ctx.send("Thank you for your feedback!", hidden=True)

    @cog_ext.cog_subcommand(
        base=WIKI_COMMAND,
        name="help",
        description=f"Get help about WikiBot commands",
        guild_ids=[config.dev_guild_id] if config.dev_guild_id else None,
    )
    async def _help(self, ctx: SlashContext):
        await ctx.respond(eat=True)

        author: discord.Member = await ctx.guild.fetch_member(ctx.author)

        embed = discord.Embed(title="Help", color=discord.Color.from_rgb(225, 225, 225))
        embed.set_footer(text=self.bot.user, icon_url=self.bot.user.avatar_url)
        embed.add_field(
            name=":information_source: General",
            value=f"`/{WIKI_COMMAND} <group> <key>`: Get wiki content of the specified topic"
            + f"\n`/{WIKI_COMMAND} feedback`: {self.slash.subcommands[WIKI_COMMAND]['feedback'].description}",
            inline=False,
        )
        if author.guild_permissions >= MANAGE_CHANNELS:
            help = ""
            for (name, x) in self.slash.subcommands[WIKI_MANAGEMENT_COMMAND].items():
                if isinstance(x, discord_slash.model.CogSubcommandObject):
                    help += f"`/{WIKI_MANAGEMENT_COMMAND} {name}`: {x.description}\n"
                else:
                    for (subname, x) in self.slash.subcommands[WIKI_MANAGEMENT_COMMAND][name].items():
                        help += f"`/{WIKI_MANAGEMENT_COMMAND} {name} {subname}`: {x.description}\n"

            embed.add_field(
                name=":wrench: Settings",
                value=help,
                inline=False,
            )

        await author.send(embed=embed)

    def __add_wiki_command(self, guild: int, group: str, key: str, desc: str, content: str):
        self.slash.subcommand(
            base=WIKI_COMMAND,
            name=key,
            description=desc,
            subcommand_group=group,
            options=[
                manage_commands.create_option(
                    name="reply_to",
                    description="Reply to the last message of specified user",
                    option_type=SlashCommandOptionType.USER,
                    required=False,
                ),
                manage_commands.create_option(
                    name="public",
                    description="Make the response be visible for everyone else in the channel",
                    option_type=SlashCommandOptionType.BOOLEAN,
                    required=False,
                ),
            ],
            guild_ids=[guild],
        )(self._topic_handler(f"{group}/{key}", content))

    def __delete_wiki_command(self, guild_id: int, group: str, key: str):
        command = None

        if WIKI_COMMAND in self.slash.subcommands:
            if group in self.slash.subcommands[WIKI_COMMAND]:
                if key in self.slash.subcommands[WIKI_COMMAND][group]:
                    if guild_id in self.slash.subcommands[WIKI_COMMAND][group][key].allowed_guild_ids:
                        command = self.slash.subcommands[WIKI_COMMAND][group][key]

        command.allowed_guild_ids = [g for g in command.allowed_guild_ids if g != guild_id]
        if len(command.allowed_guild_ids) == 0:
            del self.slash.subcommands[WIKI_COMMAND][group][key]


def parse_command_args(args: list[str]) -> dict[str, object]:
    ret = {}
    for arg in args:
        k, v = arg.split(":", maxsplit=1)
        ret[k] = v

    return ret


def setup(bot: commands.Bot):
    bot.add_cog(Slash(bot))
