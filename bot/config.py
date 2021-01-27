from dotenv import load_dotenv
from collections import namedtuple
import os

load_dotenv()

DB = namedtuple("DB", ["user", "password", "host", "database"])
Redis = namedtuple("Redis", ["host"])
Config = namedtuple(
    "Config", ["db", "redis", "discord_token", "dev_guild_id", "guild_ids"]
)

config = Config(
    db=DB(
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST"),
        database=os.getenv("POSTGRES_DB"),
    ),
    redis=Redis(
        host=os.getenv("REDIS_HOST"),
    ),
    discord_token=os.getenv("DISCORD_TOKEN"),
    dev_guild_id=int(os.getenv("DISCORD_DEV_GUILD_ID")),
    guild_ids=[int(v) for v in os.getenv("DISCORD_GUILD_IDS").split(",")],
)
