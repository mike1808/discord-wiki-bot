import discord
import functools
from discord.member import flatten_user
from discord_slash import SlashContext
from discord_slash import SlashCommand, SlashCommandOptionType, SlashContext, cog_ext
from discord.ext import commands
from bot.db import Guild
from pony.orm import db_session
import typing


def check_can_configure():
    original = commands.has_permissions(manage_roles=True, manage_channels=True).predicate

    async def extended_check(ctx: commands.Context):
        if ctx.guild is None:
            return False

        with db_session:
            guild = Guild[str(ctx.guild.id)]

        return ctx.guild.owner_id == ctx.author.id or await original(ctx) or await has_role(guild.mgmt_roles, ctx.author)

    return my_check(extended_check)
    # TODO: use this when discord slash can assign permission for subcommands
    # return commands.check(extended_check)


async def has_role(allowed_roles, user):
    user_roles = user.roles
    if not user_roles:
        user_roles = await user.fetch_roles()

    for role in user_roles:
        if str(role.id) in allowed_roles:
            return True
    return False

def my_check(predicate):
    def decorate(func):
        @functools.wraps(func)
        async def wrapper(self, ctx: SlashContext, *args, **kwargs):
            try:
                allowed = await predicate(ctx)
            except commands.CheckFailure:
                allowed = False

            if allowed:
                return await func(self, ctx, *args, **kwargs)
            else:
                author_id = ctx.author_id
                self.logger.info("Denied access to member: %d, from guild: %d", author_id, ctx.guild_id)
                return await ctx.send(
                    content="You are not allowed to use this command!",
                )

        return wrapper

    return decorate

def check_has_permissions(**kwargs):
    permissions = discord.Permissions(**kwargs)

    def decorate(func):
        @functools.wraps(func)
        async def wrapper(self, ctx: SlashContext, *args, **kwargs):
            user: typing.Union[discord.Member, None] = None

            if not isinstance(ctx.guild, int):
                user = ctx.author if not isinstance(ctx.author, int) else await ctx.guild.fetch_member(ctx.author_id)

            if user is not None and user.guild_permissions >= permissions:
                return await func(self, ctx, *args, **kwargs)
            else:
                author_id = ctx.author_id
                self.logger.info("Denied access to member: %d", author_id)
                return await ctx.send(
                    content="You are not allowed to manage Wiki topics!",
                )

        return wrapper

    return decorate


class Context:
    def __init__(self, context: typing.Union[commands.Context, SlashContext]):
        self.context = context

    async def send(self, content, **kwargs):
        if isinstance(self.context, SlashContext):
            return await self.context.send(content, **kwargs)
        else:
            return await self.context.send(content)

    def __getattr__(self, name):
        return getattr(self.context, name)


class Subcommand(cog_ext.CogSubcommandObject, commands.Command):
    def __init__(self, func, name, **attrs):
        pass


def parse_wiki_topic_args(args):
    if len(args) < 2:
        return None, None, []

    if len(args) == 2:
        return args[0], args[1], []

    return args[0], args[1], args[2:]
