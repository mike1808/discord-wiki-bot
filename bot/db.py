import csv
import sys

from pony.orm import *

from bot.config import config

db = Database(
    provider="postgres",
    user=config.db.user,
    password=config.db.password,
    host=config.db.host,
    database=config.db.database,
)


class Guild(db.Entity):
    id = PrimaryKey(str)
    name = Optional(str)
    topics = Set("Topic")


class Topic(db.Entity):
    guild = Required(Guild, index=True)
    group = Required(str)
    key = Required(str)
    desc = Optional(str)
    content = Required(str)

    composite_key(guild, group, key)


def upsert_topic(guild_id, group, key, desc, content):
    topic = Topic.select(
        lambda t: t.guild.id == guild_id and t.group == group and t.key == key
    ).first()

    if topic is None:
        topic = Topic(
            guild=guild_id,
            group=group,
            key=key,
            desc=desc,
            content=content,
        )
        new = True
    else:
        topic.desc = desc
        topic.content = content
        new = False

    return (topic, new)


def upsert_guild(guild_id, guild_name):
    try:
        guild = Guild[guild_id]
    except ObjectNotFound:
        guild = None

    new = False
    if guild is None:
        guild = Guild(id=guild_id, name=guild_name)
        new = True
    return (guild, new)


def setup():
    set_sql_debug(True)
    db.generate_mapping(create_tables=True)


@db_session
def populate_database():
    guild = Guild(
        id=str(config.dev_guild_id),
        name="My Server",
    )
    topics = [
        Topic(
            key="compass",
            group="workflow",
            desc="Espresso compass",
            content="https://www.baristahustle.com/blog/the-espresso-compass/",
            guild=guild,
        ),
        Topic(
            key="dial",
            group="workflow",
            desc="Dialling in basics",
            content="https://espressoaf.com/guides/beginner.html",
            guild=guild,
        ),
        Topic(
            key="grindtime",
            group="buyers-guide",
            desc="Entry level grinders",
            content="It's grind time! Click here for a list of Pham's suggested entry lever grinders. https://espressoaf.com/reccs/grinders.html",
            guild=guild,
        ),
        Topic(
            key="phamboy",
            group="buyers-guide",
            desc="Mid-range grinders",
            content="Looking to step up to a midrange grinder? Click here for Pham's list.\n\nhttps://espressoaf.com/reccs/Midrange_grinders.html",
            guild=guild,
        ),
        Topic(
            key="machinery",
            group="buyers-guide",
            desc="Entry level machines",
            content="Need a list of community recommended entry level machines? Check this out.\nhttps://espressoaf.com/reccs/entryMachines.html",
            guild=guild,
        ),
        Topic(
            key="wdt",
            group="workflow",
            desc="WDT guide",
            content="https://youtu.be/B3SsJhjP-Vo",
            guild=guild,
        ),
        Topic(
            key="marker",
            group="workflow",
            desc="Alignment marker test",
            content="https://youtu.be/8BCvoo5Sm2c",
            guild=guild,
        ),
        Topic(
            key="profiles",
            group="workflow",
            desc="Coffee profiling",
            content="https://espressoaf.com/guides/profiling.html",
            guild=guild,
        ),
        Topic(
            key="acc",
            group="buyers-guide",
            desc="Accessory Guide",
            content="https://espressoaf.com/guides/accessories.html",
            guild=guild,
        ),
    ]

    for topic in topics:
        topic.guild = guild

    commit()


@db_session
def export_to_csv():
    topics_writer = csv.writer(sys.stderr, delimiter=",")
    for topic in Topic.select(lambda t: t.guild.id == str(config.dev_guild_id)):
        topics_writer.writerow([topic.group, topic.key, topic.desc, topic.content])


if __name__ == "__main__":
    setup()
    export_to_csv()
