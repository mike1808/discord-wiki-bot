import os
from collections import namedtuple

from dotenv import load_dotenv

load_dotenv()

DB = namedtuple("DB", ["user", "password", "host", "database", "populate"])
Redis = namedtuple("Redis", ["host"])
Config = namedtuple("Config", ["db", "redis", "discord_token", "dev_guild_id"])

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
    dev_guild_id=int(os.getenv("DISCORD_DEV_GUILD_ID"))
    if os.getenv("DISCORD_DEV_GUILD_ID")
    else None,
)
