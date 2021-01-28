import redis

from config import config


VIEW_FIELD = "view"


class Analytics:
    def __init__(self):
        self._r = redis.Redis(host=config.redis.host, port=6379, db=0)

    def view(self, guild_id, command_name):
        self._r.hincrby(VIEW_FIELD + "_" + str(guild_id), command_name, 1)

    def retreive(self, guild_id):
        resp = self._r.hgetall(VIEW_FIELD + "_" + str(guild_id))
        views = [(k.decode("utf-8"), int(v.decode("utf-8"))) for (k, v) in resp.items()]
        return sorted(views, key=lambda r: r[1], reverse=True)
