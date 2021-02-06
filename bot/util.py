import discord
import functools
from discord_slash import SlashContext
from discord_slash import SlashCommand, SlashCommandOptionType, SlashContext, cog_ext
from discord.ext import commands
import typing


def check_has_permissions(**kwargs):
    permissions = discord.Permissions(**kwargs)

    def decorate(func):
        @functools.wraps(func)
        async def wrapper(self, ctx: SlashContext, *args, **kwargs):
            user: typing.Union[discord.Member, None] = None

            if not isinstance(ctx.guild, int):
                user = ctx.author if not isinstance(ctx.author, int) else await ctx.guild.fetch_member(ctx.author)

            if user is not None and user.guild_permissions >= permissions:
                return await func(self, ctx, *args, **kwargs)
            else:
                await ctx.respond()
                author_id = ctx.author.id if isinstance(ctx.author, discord.Member) else ctx.author
                self.logger.info("Denied access to member: %d", author_id)
                return await ctx.send(
                    content="You are not allowed to manage Wiki topics!",
                )

        return wrapper

    return decorate


class Context:
    def __init__(self, context: typing.Union[commands.Context, SlashContext]):
        self.context = context

    async def send(self, *args, **kwargs):
        if isinstance(self.context, SlashContext):
            return await self.context.send(*args, **kwargs)
        else:
            return await self.context.send(kwargs["content"])

    async def respond(self, *args, **kwargs):
        if isinstance(self.context, SlashContext):
            return await self.context.respond(*args, **kwargs)

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
