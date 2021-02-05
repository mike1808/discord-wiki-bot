# WikiBot

A bot to help your Discord server users with some frequently asked questions or
common topics.

## Deployment

You can add already deployed bot to your server from
[wikibot.manukyan.dev](https://wikibot.manukyan.dev).

## Installation

### Local or docker-compose

WikiBot requires Python 3.7+ and has few external dependencies:

* PostgreSQL - for storing data such wiki topics or joined guilds
* Reids - for storing analytics data

For quick start you can use provided docker-compose file to start-up a ready to
go environmet. But before you have to setup some environment variables. An easy
way to do it is to copy sample.env to .env and set all required variables:

```bash
cp sample.env .env
```

After that you can use:

```bash
docker-compose up -d
```

> It's **highly** recommended to use `DISCORD_DEV_GUILD_ID` environment
> variable. Otherwise all slash commands will be registered as __global__ which
> are cached in Discord for one hour, so for any change you have to wait at
> least an hour.

### Kubernetes

You can deploy WikiBot to your Kubernetes cluster.

#### Requirements

You need few external tools to deploy to your cluster:

* [ytt](https://carvel.dev/ytt/) - for config templating
* [kapp](https://carvel.dev/kapp/) - for deployment

Also, you have to copy `sample-values.yml` to `values.yml` and edit the
appropriate values.

#### Deploying

To deploy, run:

```bash
ytt -f deploy -f values.yml | kapp deploy -a wikibot -n wikibot -y -f -
```

It will render templates and deploy to `wikibot` namespace.

