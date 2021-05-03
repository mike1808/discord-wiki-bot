import discord


class PaginatedEmbed(discord.Embed):
    def __init__(self, max_size=1024, **kwargs):
        super().__init__(**kwargs)
        self._pages = [self]
        self.max_size = max_size

    def add_field(self, **args):
        super().add_field(**args)

        if len(self) >= self.max_size:

            copy = self.copy()
            fields = [f for f in self._fields]
            copy._fields = []

            last_field = self.fields[-1]
            self.remove_field(-1)

            copy.add_field(name=last_field.name, value=last_field.value, inline=last_field.inline)

            self._pages += self._split_embed(copy)

    def _split_embed(self, embed: discord.Embed):
        if len(embed) < self.max_size:
            return [embed]

        copy = embed.copy()
        copy._fields = []
        last_field = embed.fields[-1]
        lines = last_field.value.split("\n")
        embed.remove_field(-1)

        embed.add_field(name=last_field.name, value="\n".join(lines[: len(lines) // 2]), inline=last_field.inline)
        copy.add_field(name=last_field.name, value="\n".join(lines[len(lines) // 2 :]), inline=last_field.inline)

        return [embed] + self._split_embed(copy)

    def pages(self):
        n = len(self._pages)
        for i, e in enumerate(self._pages):
            e.title += f" {i+1}/{n}"

        return self._pages
