import discord
import functools
from discord_slash import SlashContext
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
