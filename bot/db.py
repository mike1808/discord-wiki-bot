from config import config
from pony.orm import *

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

    composite_index(guild, group, key)


def setup():
    set_sql_debug(True)
    db.generate_mapping(create_tables=True)


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
