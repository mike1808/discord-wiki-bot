class Topic:
    def __init__(self, key: str, group: str, description: str, content: str):
        self.key = key
        self.group = group
        self.description = description
        self.content = content


db: dict[str, Topic] = {
    "compass": Topic(
        "compass",
        "workflow",
        "Espresso compass",
        "https://www.baristahustle.com/blog/the-espresso-compass/",
    ),
    "dial": Topic(
        "dial",
        "workflow",
        "Dialling in basics",
        "https://espressoaf.com/guides/beginner.html",
    ),
    "grindtime": Topic(
        "grindtime",
        "buyers-guide",
        "Entry level grinders",
        "It's grind time! Click here for a list of Pham's suggested entry lever grinders. https://espressoaf.com/reccs/grinders.html",
    ),
    "phamboy": Topic(
        "phamboy",
        "buyers-guide",
        "Mid-range grinders",
        "Looking to step up to a midrange grinder? Click here for Pham's list.\n\nhttps://espressoaf.com/reccs/Midrange_grinders.html",
    ),
    "machinery": Topic(
        "machinery",
        "buyers-guide",
        "Entry level machines",
        "Need a list of community recommended entry level machines? Check this out.\nhttps://espressoaf.com/reccs/entryMachines.html",
    ),
    "wdt": Topic("wdt", "workflow", "WDT guide", "https://youtu.be/B3SsJhjP-Vo"),
    "marker": Topic(
        "marker", "workflow", "Alignment marker test", "https://youtu.be/8BCvoo5Sm2c"
    ),
    "profiles": Topic(
        "profiles",
        "workflow",
        "Coffee profiling",
        "https://espressoaf.com/guides/profiling.html",
    ),
    "acc": Topic(
        "acc",
        "buyers-guide",
        "Accessory Guide",
        "https://espressoaf.com/guides/accessories.html",
    ),
}
