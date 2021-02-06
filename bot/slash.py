import csv
import datetime
import functools
import io
import json
import logging
import typing
from collections import defaultdict
import asyncio


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
from bot.db import Guild, Topic, guild_topics
from bot.feedback import Feedback
from bot.util import check_has_permissions, Context

MAX_SUBCOMMANDS_ERROR_CODE = 50035

WIKI_COMMAND = config.command_prefix + "wiki"
WIKI_FEEDBACK_COMMAND = WIKI_COMMAND + "-feedback"
WIKI_HELP_COMMAND = WIKI_COMMAND + "-help"
WIKI_MANAGEMENT_COMMAND = WIKI_COMMAND + "-mgmt"

MANAGE_CHANNELS = discord.Permissions()
MANAGE_CHANNELS.manage_channels = True


class Slash(commands.Cog):
    def __init__(self, bot: discord.ext.commands.Bot):
        if not hasattr(bot, "slash"):
            # Creates new SlashCommand instance to bot if bot doesn't have.
            bot.slash = SlashCommand(bot, override_type=True, auto_register=False, auto_delete=False)

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

    # Handle wiki topics
    @commands.Cog.listener()
    async def on_socket_response(self, msg):
        if msg["t"] != "INTERACTION_CREATE":
            return

        d = msg["d"]
        if d["data"]["name"] != WIKI_COMMAND:
            return
        ctx = Context(SlashContext(self.slash.req, d, self.bot, self.logger))

        if "options" in d["data"] and d["data"]["options"]:
            subgroup = d["data"]["options"][0]
            wiki_group = subgroup["name"]
            if "options" in subgroup and subgroup["options"]:
                subcommand = subgroup["options"][0]
                wiki_key = subcommand["name"]
                args = {o["name"]: o["value"] for o in subcommand["options"]} if "options" in subcommand else {}
                self.logger.info("Calling %s/%s for guild %s", wiki_group, wiki_key, ctx.guild.id)
                try:
                    await self._topic_handler(ctx, wiki_group, wiki_key, **args)
                except Exception as ex:
                    await self.on_slash_command_error(ctx, ex)

    async def on_slash_command_error(self, ctx: Context, ex: Exception):
        self.logger.error(ex, exc_info=True)
        await ctx.send(
            f"Failed to process you command. Please try later or if the issue persist report it via `/{WIKI_COMMAND}-feedback` command"
        )

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
            command_args["reply_to"] = command_args["reply_to"]

        await command.invoke(**command_args)

    @db_session
    async def _setup_wiki_commands(self):
        tasks: list[typing.Coroutine] = []
        for guild in Guild.select():
            tasks.append(self.__sync_wiki_command(guild.id))
        await self.slash.sync_all_commands()
        result = await asyncio.gather(*tasks, return_exceptions=True)
        for res in result:
            if isinstance(res, Exception):
                self.logger.critical("Failed to sync wiki commands: %s", res, exc_info=True)

    @db_session
    async def _topic_handler(self, ctx: Context, group: str, key: str, **args):
        public = args["public"] if "public" in args else False
        reply_to = args["reply_to"] if "reply_to" in args else None

        await ctx.respond(eat=not public)

        topic = Topic.select(guild=str(ctx.guild.id), group=group, key=key).first()
        if topic is None:
            await ctx.send(content=f"Sorry we don't have anything about {group}/{key}", hidden=not public)
            return

        content = topic.content

        if reply_to:
            reply_id = int(reply_to)
            try:
                async for msg in ctx.channel.history(limit=10):
                    if msg.author.id == reply_id and msg.type == discord.MessageType.default:
                        return await msg.reply(content=content)
            except (
                discord.Forbidden,
                discord.HTTPException,
                discord.NotFound,
                TypeError,
                ValueError,
            ) as ex:
                self.logger.warn("Failed to fetch messages: %s", ex, exc_info=True)
                await ctx.send(
                    content="Couldn't find message to reply. Normally sending content.",
                    hidden=True,
                )

        await ctx.send(content=content, hidden=not public)

        self.analytics.view(
            ctx.guild.id if not isinstance(ctx.guild, int) else ctx.guild,
            f"{group}/{key}",
        )

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        name="upsert",
        description="Add or modify a topic",
        guild_ids=config.dev_guild_ids,
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

        self.bot.loop.create_task(self.__sync_wiki_command(ctx.guild.id))

        action = "added" if new else "modified"
        try:
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
        guild_ids=config.dev_guild_ids,
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
        topic.delete()
        # TODO: remove this and figure out how to make @db_session work with async
        commit()

        self.bot.loop.create_task(self.__sync_wiki_command(ctx.guild.id))

        await ctx.send(content=f"**{group}/{key}** was deleted.", hidden=True)

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        name="analytics",
        description="Get commands usage analytics",
        guild_ids=config.dev_guild_ids,
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
        guild_ids=config.dev_guild_ids,
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
        guild_ids=config.dev_guild_ids,
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

        for t in guild_topics(str(ctx.guild.id)):
            csvwriter.writerow([t.group, t.key, t.desc, t.content])
            count += 1

        await ctx.send(
            content=f"We successfuly exported **{count}** topcs!",
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
        guild_ids=config.dev_guild_ids,
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

        # TODO: remove this and figure out how to make @db_session work with async
        commit()
        self.bot.loop.create_task(self.__sync_wiki_command(ctx.guild.id))

        await ctx.send(
            content=f"Import was successfuly finished! **{added}** added and **{updated}** updated.",
        )

    @cog_ext.cog_slash(
        name=WIKI_FEEDBACK_COMMAND,
        description=f"Leave feedback to the bot developer",
        options=[
            manage_commands.create_option(
                name="feedback",
                description="Feedback message",
                option_type=SlashCommandOptionType.STRING,
                required=True,
            ),
        ],
        guild_ids=config.dev_guild_ids,
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

    @cog_ext.cog_slash(
        name=WIKI_HELP_COMMAND,
        description=f"Get help about WikiBot commands",
        guild_ids=config.dev_guild_ids,
    )
    @db_session
    async def _help(self, ctx: SlashContext):
        await ctx.respond(eat=True)

        author: discord.Member = await ctx.guild.fetch_member(ctx.author)

        embed = discord.Embed(title="Help", color=discord.Color.from_rgb(225, 225, 225))
        embed.set_footer(text=self.bot.user, icon_url=self.bot.user.avatar_url)
        embed.add_field(
            name=":information_source: General",
            value=f"`/{WIKI_COMMAND} <group> <key>`: Get wiki content of the specified topic"
            + f"\n`/{WIKI_FEEDBACK_COMMAND}`: {self.slash.commands[WIKI_FEEDBACK_COMMAND].description}"
            + f"\n`/{WIKI_HELP_COMMAND}`: {self.slash.commands[WIKI_HELP_COMMAND].description}",
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

        embed.add_field(
            name=f":grey_question: Available /{WIKI_COMMAND} commands",
            value="\n".join([f"`/{WIKI_COMMAND} {t.group} {t.key}`: {t.desc}" for t in guild_topics(str(ctx.guild.id))])
            or "No commands available",
            inline=False,
        )

        await author.send(embed=embed)

    @db_session
    async def __sync_wiki_command(self, guild_id: int):
        subcommand_options = [
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
        ]
        command = {
            "cmd_name": WIKI_COMMAND,
            "description": "Get wiki for your specified topic",
            "options": [],
        }
        groups = defaultdict(list)
        for topic in guild_topics(str(guild_id)):
            groups[topic.group].append(
                {
                    "name": topic.key,
                    "description": topic.desc,
                    "type": SlashCommandOptionType.SUB_COMMAND,
                    "options": subcommand_options,
                }
            )
        for (group, topics) in groups.items():
            subgroup = {
                "name": group,
                "description": "No Description.",
                "type": SlashCommandOptionType.SUB_COMMAND_GROUP,
                "options": topics,
            }
            command["options"].append(subgroup)

        await self.slash.req.add_slash_command(guild_id=guild_id, **command)

    def __delete_wiki_command(self, guild_id: int, group: str, key: str):
        command = None

        if WIKI_COMMAND in self.slash.subcommands:
            if group in self.slash.subcommands[WIKI_COMMAND]:
                if key in self.slash.subcommands[WIKI_COMMAND][group]:
                    if guild_id in self.slash.subcommands[WIKI_COMMAND][group][key].allowed_guild_ids:
                        command = self.slash.subcommands[WIKI_COMMAND][group][key]

        if command:
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
