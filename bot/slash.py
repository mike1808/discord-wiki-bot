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
from discord import guild
import discord.ext.commands
import discord_slash.error
from discord.ext import commands
from discord_slash import SlashCommandOptionType, SlashContext, cog_ext, ComponentContext
from discord_slash.utils import manage_commands
from discord_slash.utils.manage_components import (
    create_button,
    create_actionrow,
    wait_for_component,
    create_select,
    create_select_option,
)
from discord_slash.utils.manage_commands import create_permission,  update_single_command_permissions
from discord_slash.model import SlashCommandPermissionType
import discord_slash.model
from pony.orm import commit, db_session, select

from bot import db
from bot.analytics import Analytics
from bot.config import config
from bot.db import Guild, Topic, guild_topics, mark_guild_disabled
from bot.feedback import Feedback
from bot.util import Context, parse_wiki_topic_args, check_can_configure, my_check
from bot.embed_paginator import PaginatedEmbed

MAX_SUBCOMMANDS_ERROR_CODE = 50035

WIKI_COMMAND = config.command_prefix + "wiki"
WIKI_FEEDBACK_COMMAND = WIKI_COMMAND + "-feedback"
WIKI_HELP_COMMAND = WIKI_COMMAND + "-help"
WIKI_MANAGEMENT_COMMAND = WIKI_COMMAND + "-mgmt"
WIKI_CONFIG_COMMAND = WIKI_COMMAND + "-config"
WIKI_BULK_COMMAND = WIKI_COMMAND + "-bulk"

WIKI_ADMIN_COMMANDS = [WIKI_MANAGEMENT_COMMAND]

MANAGE_CHANNELS = discord.Permissions()
MANAGE_CHANNELS.manage_channels = True


@db_session
def _setup_permissions():
    guilds = db.active_guilds()
    permissions = {
        int(guild.id): _create_permissions_for_guild(guild)
        for guild in guilds if guild.mgmt_roles is not None and len(guild.mgmt_roles[:]) > 0
    }
    return permissions

def _create_permissions_for_guild(guild):
    return [create_permission(int(guild.id), SlashCommandPermissionType.ROLE, False)] + [create_permission(int(role), SlashCommandPermissionType.ROLE, True) for role in guild.mgmt_roles]

class Slash(commands.Cog):
    mgmt_permissions = _setup_permissions()

    def __init__(self, bot: discord.ext.commands.Bot):
        self.bot = bot
        self.slash = bot.slash

        self.bot.loop.create_task(self._setup_wiki_commands())

        self.analytics = Analytics()
        self.logger = logging.getLogger("wikibot.slash")
        self.feedback = Feedback()

    def cog_unload(self):
        self.feedback.close()

    # Handle wiki topics
    @commands.Cog.listener()
    async def on_socket_response(self, msg):
        if msg["t"] != "INTERACTION_CREATE":
            return

        d = msg["d"]
        if d["data"] is not None and d["data"].get("name", "") != WIKI_COMMAND:
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

    async def on_command_error(self, err: commands.CommandError):
        if isinstance(err, commands.CommandNotFound):
            self.logger.warn("Command not found error: %s", err, exc_info=True)
        else:
            self.logger.error("Command error: %s", err, exc_info=True)

    @commands.command(name=WIKI_COMMAND)
    async def _fallback_wiki_command(self, ctx: commands.Context, *args):
        wiki_group, wiki_key, command_args = parse_wiki_topic_args(args)
        if wiki_group is None or wiki_key is None:
            return

        command_args = parse_command_args(
            command_args,
        )

        my_ctx = Context(ctx)

        try:
            await self._topic_handler(my_ctx, wiki_group, wiki_key, **command_args)
        except Exception as ex:
            await self.on_slash_command_error(my_ctx, ex)

    @db_session
    async def _setup_wiki_commands(self):
        tasks: list[typing.Coroutine] = []
        for guild in Guild.select(disabled=False):
            tasks.append(self.__sync_wiki_command(int(guild.id)))
        try:
            await self.slash.sync_all_commands()
        except Exception as ex:
            self.logger.warn("Failed to sync slash commands: %s", ex, exc_info=True)

        result = await asyncio.gather(*tasks, return_exceptions=True)
        for res in result:
            if isinstance(res, Exception):
                self.logger.critical("Failed to sync wiki commands: %s", res, exc_info=True)

        self.logger.info("Syncing done.")

    @db_session
    async def _topic_handler(self, ctx: Context, group: str, key: str, **args):
        hidden = args["hidden"] if "hidden" in args else False
        reply_to = args["reply_to"] if "reply_to" in args else None

        topic = Topic.select(guild=str(ctx.guild.id), group=group, key=key).first()
        if topic is None:
            await ctx.send(content=f"Sorry we don't have anything about {group}/{key}", hidden=hidden)
            return

        content = topic.content

        if reply_to:
            reply_id = int(reply_to)
            try:
                async for msg in ctx.channel.history(limit=10):
                    if msg.author.id == reply_id and msg.type == discord.MessageType.default:
                        await msg.reply(content=content)
                        await ctx.send("Replied!", hidden=True)
                        break
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
        else:
            await ctx.send(content=content, hidden=hidden)

        self.analytics.view(
            ctx.guild.id,
            f"{group}/{key}",
        )

    @cog_ext.cog_subcommand(
        base=WIKI_MANAGEMENT_COMMAND,
        base_permissions=mgmt_permissions,
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
            manage_commands.create_option(
                name="alias",
                description="Alias to be used with old-shool !help commands",
                option_type=SlashCommandOptionType.STRING,
                required=False,
            ),
        ],
    )
    @check_can_configure()
    @db_session
    async def _topic_upsert(
        self, ctx: SlashContext, group: str, key: str, description: str, content: str, alias: str = ""
    ):
        topic, new = db.upsert_topic(str(ctx.guild.id), group, key, description, content, alias)

        author_id = ctx.author_id
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
    @check_can_configure()
    @db_session
    async def _topic_delete(self, ctx: SlashContext, group: str, key: str):
        topic = Topic.select(
            lambda t: t.guild.id == str(ctx.guild.id) and t.group == str.lower(group) and t.key == str.lower(key)
        ).first()

        if topic is None:
            await ctx.send(
                content=f"**{group}/{key}** is not in the database.",
                hidden=True,
            )
            return

        author_id = ctx.author_id
        self.logger.info(
            f"deleting topic: {ctx.guild.id} /{WIKI_COMMAND} {group} {key} by member: {author_id}",
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
    @check_can_configure()
    @db_session
    async def _analytics(self, ctx: SlashContext):
        views = self.analytics.retreive(ctx.guild.id)

        embed = discord.Embed(title="Wiki Analytics", color=discord.Color.from_rgb(225, 225, 225))
        embed.set_footer(text=self.bot.user, icon_url=self.bot.user.avatar_url)
        for (command, view_count) in views:
            embed.add_field(name=command, value=str(view_count), inline=False)

        await ctx.send(embed=embed)

    @cog_ext.cog_slash(
        name=WIKI_CONFIG_COMMAND,
        guild_ids=config.dev_guild_ids,
        description="Configure WikiBot",
        permissions=mgmt_permissions,
    )
    @my_check(commands.has_permissions(manage_roles=True).predicate)
    @db_session
    async def _wiki_config(self, ctx: SlashContext):
        roles = await ctx.guild.fetch_roles()
        role_select = create_select(
            options=[create_select_option(role.name, value=str(role.id)) for role in roles],
            placeholder="Choose the role or roles",
            min_values=1,
            max_values=len(roles),
        )

        action_row = create_actionrow(role_select)
        await ctx.send(
            "Please select the role or roles which will be able to run WikiBot management commands.",
            components=[action_row],
        )
        component_ctx: ComponentContext = await wait_for_component(self.bot, components=action_row)
        await component_ctx.edit_origin(components=[])

        guild = Guild[str(ctx.guild_id)]
        guild.mgmt_roles = component_ctx.selected_options or []
        commit()

        if component_ctx.selected_options:
            names = ", ".join([role.name for role in roles if str(role.id) in component_ctx.selected_options])
            await component_ctx.send(content=f"Nice! Now only {names} can control me!")
        else:
            await component_ctx.send(content=f"Looks like you didn't select anything. Ok...")

        self._update_permissions(guild)

    @cog_ext.cog_subcommand(
        base=WIKI_BULK_COMMAND,
        base_permissions=mgmt_permissions,
        name="help",
        description=f"Show help with `/{WIKI_BULK_COMMAND}` commands",
        guild_ids=config.dev_guild_ids,
    )
    @check_can_configure()
    async def _bulk_help(self, ctx: SlashContext):
        await ctx.send(
            content='Bulk import and export commands consume and produce CSV files. CSV files should be delimited with a single quota `,` and use double quotes `"`.'
            + "\nIt should contain 4 columns and the header is optional. Those columns are `group,key,description,content,alias`."
            + "\nTo iupdate_single_command_permissionsn you have to use the `/{WIKI_COMMAND} bulk import` command to import the topics."
            + "\nWikiBot will search the latest 5 messages in the channel and select the latest your message and try to download your CSV file."
            + "\nThen it will import all provided topics. Be careful! It will override the description and content of all topics currently created.",
            hidden=True,
        )

    @cog_ext.cog_subcommand(
        base=WIKI_BULK_COMMAND,
        name="export",
        description=f"Export all existing topics to CSV file",
        guild_ids=config.dev_guild_ids,
    )
    @check_can_configure()
    @db_session
    async def _bulk_export(self, ctx: SlashContext):
        await ctx.defer()

        author_id = ctx.author_id
        self.logger.info(
            f"sending export by request of member: %d",
            author_id,
        )

        csvoutput = io.StringIO()
        csvwriter = csv.writer(csvoutput, quoting=csv.QUOTE_MINIMAL)
        csvwriter.writerow(["group", "key", "desc", "content", "alias"])
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
        base=WIKI_BULK_COMMAND,
        name="import",
        description=f"Import topics from CSV file",
        guild_ids=config.dev_guild_ids,
    )
    @check_can_configure()
    @db_session
    async def _bulk_import(self, ctx: SlashContext):
        await ctx.defer()

        author_id = ctx.author_id
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
            topic, new = db.upsert_topic(
                str(ctx.guild.id), row[0], row[1], row[2], row[3], row[4] if len(row) == 5 else ""
            )
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

    async def on_error(self, ctx, error):
        await ctx.send("error")

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
        self.logger.info(
            f"member: %d:%s gave feedback",
            ctx.author_id,
            ctx.author.display_name,
        )

        try:
            self.feedback.send_feedback(ctx.author_id, ctx.author.display_name, ctx.guild.id, ctx.guild.name, feedback)
        except Exception as e:
            self.logger.critical("Failed to send feeback: %s", e, exc_info=True)

        await ctx.send("Thank you for your feedback!", hidden=True)

    @cog_ext.cog_slash(
        name=WIKI_HELP_COMMAND,
        description=f"Get help about WikiBot commands",
        guild_ids=config.dev_guild_ids,
    )
    @db_session
    async def _help(self, ctx: SlashContext):
        author = ctx.author

        embed = PaginatedEmbed(title=f"Help for {ctx.guild.name}", color=discord.Color.from_rgb(225, 225, 225))
        embed.set_footer(text=self.bot.user, icon_url=self.bot.user.avatar_url)
        embed.add_field(
            name=":information_source: General",
            value=f"`/{WIKI_COMMAND} <group> <key>`: Get wiki content of the specified topic"
            + f"\n`/{WIKI_FEEDBACK_COMMAND}`: {self.slash.commands[WIKI_FEEDBACK_COMMAND].description}"
            + f"\n`/{WIKI_HELP_COMMAND}`: {self.slash.commands[WIKI_HELP_COMMAND].description}",
            inline=False,
        )
        if isinstance(author, discord.Member) and author.guild_permissions >= MANAGE_CHANNELS:
            help = ""
            for base in [WIKI_BULK_COMMAND, WIKI_MANAGEMENT_COMMAND]:
                for (name, x) in self.slash.subcommands[base].items():
                    if isinstance(x, discord_slash.model.CogSubcommandObject):
                        help += f"`/{base} {name}`: {x.description}\n"
                    else:
                        for (subname, x) in self.slash.subcommands[base][name].items():
                            help += f"`/{base} {name} {subname}`: {x.description}\n"

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

        for e in embed.pages():
            await author.send(embed=e)
        await ctx.send("Check your DMs for help!", hidden=True)

    @db_session
    async def __sync_wiki_command(self, guild_id: int):
        aliases = []
        subcommand_options = [
            manage_commands.create_option(
                name="reply_to",
                description="Reply to the last message of specified user",
                option_type=SlashCommandOptionType.USER,
                required=False,
            ),
            manage_commands.create_option(
                name="hidden",
                description="Make the response be visible only by you",
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
            if topic.alias:
                aliases.append(topic)

        for (group, topics) in groups.items():
            subgroup = {
                "name": group,
                "description": "No Description.",
                "type": SlashCommandOptionType.SUB_COMMAND_GROUP,
                "options": topics,
            }
            command["options"].append(subgroup)

        cmd_check = self._create_command_check(guild_id)
        for topic in aliases:
            cmd = commands.Command(
                self._create_wiki_bot_command_callback(topic),
                name=topic.alias,
                description=topic.desc,
                help=topic.desc,
                checks=[cmd_check],
                cog=self,
            )
            self.bot.remove_command(topic.alias)
            self.bot.add_command(cmd)

        try:
            await self.slash.req.add_slash_command(guild_id=guild_id, **command)
        except discord.Forbidden as e:
            self.logger.warn("Not syncing commands for guild: %s, Reason: %s", guild_id, e)
            mark_guild_disabled(str(guild_id))

    def _create_wiki_bot_command_callback(self, topic: Topic):
        async def callback(ctx: commands.Context):
            if str(ctx.guild.id) == topic.guild.id:
                await ctx.send(topic.content)

        return callback

    def _create_command_check(self, guild_id: int):
        async def is_for_guild(ctx: commands.Context):
            return ctx.guild.id == guild_id

        return is_for_guild

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
    
    def _update_permissions(self, guild):
        if len(guild.mgmt_roles) > 0:
            self.mgmt_permissions[int(guild.id)] = _create_permissions_for_guild(guild)
        else:
            self.mgmt_permissions.pop(int(guild.id), None)

        # very hacky solution, it reloads the whole cog just to trigger sync
        self.bot.reload_slash()
            


def parse_command_args(args: list[str]) -> dict[str, object]:
    ret = {}
    for arg in args:
        k, v = arg.split(":", maxsplit=1)
        ret[k] = v

    return ret


def setup(bot: commands.Bot):
    bot.add_cog(Slash(bot))
