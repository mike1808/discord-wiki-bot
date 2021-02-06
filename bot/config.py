import os
from collections import namedtuple

from dotenv import load_dotenv

load_dotenv()

DB = namedtuple("DB", ["user", "password", "host", "database", "populate"])
Redis = namedtuple("Redis", ["host"])
SMTP = namedtuple("SMTP", ["host", "email", "password", "from_email"])
Config = namedtuple(
    "Config",
    ["db", "redis", "discord_token", "dev_guild_ids", "smtp", "command_prefix"],
    defaults=[None, None, "", None, None, ""],
)

config = Config(
    db=DB(
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST"),
        database=os.getenv("POSTGRES_DB"),
        populate=os.getenv("POSTGRES_POPULATE") == "1",
    ),
    redis=Redis(
        host=os.getenv("REDIS_HOST"),
    ),
    discord_token=os.getenv("DISCORD_TOKEN"),
    dev_guild_ids=[int(s) for s in os.getenv("DISCORD_DEV_GUILD_IDS").split(",")]
    if os.getenv("DISCORD_DEV_GUILD_IDS")
    else None,
    smtp=SMTP(
        host=os.getenv("WIKIBOT_SMTP_HOST"),
        email=os.getenv("WIKIBOT_SMTP_EMAIL"),
        from_email=os.getenv("WIKIBOT_SMTP_FROM_EMAIL"),
        password=os.getenv("WIKIBOT_SMTP_PASSWORD"),
    ),
    command_prefix=os.getenv("WIKIBOT_COMMAND_PREFIX") or "",
)
