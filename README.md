# AniFlow

A self-hosted anime season calendar and watchlist, with [Shoko Server](https://shokoanime.com/) integration and, optionally, an automatic download manager that follows rules you set yourself.

- **Season calendar.** See what airs this season and the next, with air dates and times, and add a show to your watchlist from the calendar in one click. Shows you already track are marked, and so are sequels to them.
- **Watchlist.** Everything you follow, with its episodes, what you already have on disk and what is still missing.
- **Shoko Server.** Connect your Shoko Server to see what it holds, adopt its series into your watchlist, and let AniFlow hand files over to Shoko's import folder and find them again once Shoko has filed them.
- **Downloads (optional).** For each anime you choose an exact release profile (group, resolution, source, censorship). AniFlow only ever queues releases that match those choices, hands them to your download client and files the finished episodes into your library. Nothing is changed silently, and AniFlow does not delete your media unless you ask it to.

> [!CAUTION]
> **AniFlow ships with no indexers and no feeds.** Without them it is a calendar and watchlist and does not search for anything. To use the download part you add your own sites under *Indexer Profiles* and *RSS Source Profiles*. AniFlow does not provide, recommend or link to any.

## Quick start

You need Docker with Docker Compose. A download client (qBittorrent, Deluge or Transmission) is only needed for downloads.

Run it with Docker Compose or with the Docker CLI.

**Docker Compose.** Create a `docker-compose.yml`:

```yaml
services:
  aniflow:
    image: aniflow/aniflow:latest
    container_name: aniflow
    environment:
      TZ: Europe/Berlin
    user: "1000:1000"
    ports:
      - "9630:9630"
    volumes:
      - /docker/aniflow/config:/config
      - /docker/aniflow/data:/data
      - /media/anime:/media
      - /media/downloads:/downloads
    restart: unless-stopped
```

Adjust the timezone, the host paths on the left of each volume, and `user:` (it must own the config and data folders and be able to read and write the media and download folders). Then:

```bash
mkdir -p /docker/aniflow/config /docker/aniflow/data
chown -R 1000:1000 /docker/aniflow
docker compose up -d
```

**Docker CLI.** The same setup as one command (after the `mkdir` and `chown` above):

```bash
docker run -d \
  --name=aniflow \
  --user 1000:1000 \
  -e TZ=Europe/Berlin \
  -p 9630:9630 \
  -v /docker/aniflow/config:/config \
  -v /docker/aniflow/data:/data \
  -v /media/anime:/media \
  -v /media/downloads:/downloads \
  --restart unless-stopped \
  aniflow/aniflow:latest
```

Open `http://<host>:9630` and follow the setup wizard. Everything else is configured in the wizard and in *Settings*; there is nothing to edit by hand. To update with Compose: `docker compose pull && docker compose up -d`. With the Docker CLI: `docker pull aniflow/aniflow:latest`, `docker stop aniflow`, `docker rm aniflow`, then run the `docker run` command again.

## Media folders and libraries

AniFlow sees four folders inside the container:

| Folder | What it is |
|---|---|
| `/config` | AniFlow's settings and log |
| `/data` | Its database and cover cache. Back this up |
| `/media` | Your anime |
| `/downloads` | Where your download client saves, as AniFlow sees it |

A **library** in AniFlow is a name and one folder. Mount the folder that holds all your anime at `/media` and make your libraries `/media` itself or subfolders of it (`/media/anime`, `/media/adult`). You can have several libraries, and a show lives in one of them. If you prefer, mount each library folder on its own instead; both ways can be mixed.

If you use Shoko Server, AniFlow can link a library to one of Shoko's import folders, and Shoko then decides where files end up. Mount Shoko's hand-over folder at `/import`.

One thing to get right: AniFlow, your download client and (if used) Shoko must all reach the same files, so the paths inside their containers have to match what you tell AniFlow. The setup wizard and the *Checks* page test this and say what to change.

## First start

AniFlow has one account. On the first start it prints a one-time code to the container log, and the setup wizard asks for it, so that only someone who can read the log can create the account:

```bash
docker logs aniflow 2>&1 | grep "one-time code"
```

Forgot the password? Choose *Forgot your password?* on the login page and read the new code from the log the same way. It works once and lasts an hour.

## Getting started

1. **Watchlist → Add anime**: search for a show and add it to your watchlist. Its library can be changed on the anime's page.
2. **Calendar**: browse the season, and press *Add* on a show to put it on your watchlist.
3. Open an anime to see its episodes and, for downloads, set its release profile.
4. The **In Progress** page shows what is happening and offers a fix when something needs you.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Container refuses to start with `chmod ... operation not permitted` (TrueNAS) | The dataset uses NFSv4 ACLs in restricted mode, which forbids a change Docker makes on every bind mount. Switch the dataset to POSIX ACLs or set `aclmode=passthrough` |
| Setup asks for a code | It is in the container log: `docker logs aniflow 2>&1 \| grep "one-time code"` |
| Downloads say "not configured" | Add your download client under *Settings → Downloads* and press *Test connection* |
| Files are not found or cannot be placed | Open the *Checks* page: it tests every folder and says what to change |
| Import stuck on `target_exists` | A different file occupies the destination. Resolve it by hand; the import retries afterwards |
| Port already in use | Change the left side of `ports:` (for example `"9631:9630"`) |

## Backup and restore

Stop the container and copy `/config` and `/data`. To restore, put them back and start again; migrations bring older data forward automatically.

## Security notes

AniFlow is built for your own network. It asks for a login, but the connection is plain HTTP, so the password travels in the clear. Do not expose it to the internet directly: put it behind a reverse proxy with HTTPS, or reach it through a VPN. Passwords are stored as salted hashes. Keys and passwords for your integrations are stored in plain text in `/config`, so protect that folder, and they are never returned by the API or written to the log.

## Legal notice

The first screen of the setup wizard is a short notice that has to be accepted before an account is created: what AniFlow is and is not (it hosts and distributes no content and ships no indexers or feeds), which services it contacts, that it collects and sends nothing about you, that you are responsible for what you download, and the usual no-warranty terms. The same text, in English and German, is in [`LEGAL.md`](LEGAL.md) and [`LEGAL.de.md`](LEGAL.de.md). It is a plain-language notice, not legal advice.

## Privacy: what AniFlow contacts

AniFlow has no analytics, no crash reporting, no update check and no account with us. Nothing is sent anywhere unless one of the things below is switched on or you configured it. What it does contact, and who starts the request:

| Who | Where | What it receives | When |
|---|---|---|---|
| Server | AniList (`graphql.anilist.co`), Kitsu (`kitsu.io`) | search words, titles and ids of what you add | metadata lookups; both are used by default |
| Server | Jikan (`api.jikan.moe`), MyAnimeList (`api.myanimelist.net`), Shikimori (`shikimori.one`) | titles and ids | only if you switch the source on; MyAnimeList also needs your own client id |
| Server | AniDB (`anidb.net`) | nothing but the request: AniFlow downloads the published title dump | about once a week, never more than that |
| Server | GitHub (`raw.githubusercontent.com`, the anime-lists id mapping) | nothing but the request | about once a week |
| Server | `api.country.is`, `ipwho.is`, `api.ipify.org` | nothing but the request | only when you press the proxy check |
| Server | the indexers and feeds you add | search words and the server's address | when you search or a feed is scanned |
| Server | your download client, Shoko, your proxy | what the integration needs, including the credentials you gave | only the addresses you configured, normally on your own network |
| Server | the image servers of the metadata providers | nothing but the request, through your proxy if you set one | once per cover, the first time it is shown; AniFlow keeps the picture in `data/cover-cache` and serves it itself |

Covers never load in the visitor's browser from a provider: the browser asks AniFlow (`/api/cover`), and AniFlow asks the provider once. So a provider sees the server's address, never the visitor's. Only the providers' own image hosts can be fetched this way, over https; nothing else.

Every request AniFlow makes identifies itself as `AniFlow/<version>` and carries no identifier of yours. Fonts are bundled, nothing is loaded from a CDN, and the page uses no tracking cookies.

**What AniFlow stores** (all in `/data` and `/config`, nothing leaves the machine): your watchlist and its settings, the login name and a salted hash of the password, the hashed login sessions, the names of paired TVs, the log and the Activity list. The *Download diagnostics* button in Settings builds a zip you save yourself; it contains titles and folder paths, with every secret removed. It is never sent anywhere, so read it before attaching it to a report. To erase everything, stop the container and delete both folders.

**The browser stores** the session cookie (needed to stay logged in) and a few display preferences in local storage (language, sort order, the adult-content filter). Nothing in them tracks you.

## Data sources and non-affiliation

Anime information comes from **AniList**, **Kitsu**, and optionally **Jikan** (an unofficial MyAnimeList interface), the **MyAnimeList** API and **Shikimori**. Alternative titles come from the **AniDB** published title dump, and the id mapping between those services from the [anime-lists](https://github.com/Fribb/anime-lists) project. Cover images and descriptions belong to their respective owners and are shown, not redistributed.

AniFlow is an independent project. It is not affiliated with, endorsed by or sponsored by AniList, Kitsu, MyAnimeList, Jikan, Shikimori, AniDB, Shoko, qBittorrent, Deluge, Transmission, Nyaa or any other service it works with. Their names are used only to say what AniFlow connects to. Please keep to the terms of each service; AniFlow spaces its requests out on purpose and never fetches AniDB's web pages.

AniFlow does not host, index or distribute any media. It searches the indexers you add and hands what matches your own rules to your download client; what you download and whether you may is your responsibility.

## Help and contact

Problems and questions: open an issue on [GitHub](https://github.com/aniflow-apps/aniflow/issues) or write to [aniflow@proton.me](mailto:aniflow@proton.me). Say which version you run, and attach the diagnostics file from the Checks page (read it first and remove what you do not want to share).

## Licence

AniFlow is licensed under [Creative Commons Attribution-NonCommercial-NoDerivatives 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/) (CC BY-NC-ND 4.0, see [`LICENSE`](LICENSE)): you may use it and share unmodified copies with credit, but not commercially, and not modified versions. The exceptions are the bundled parser under `backend/app/vendor/` (MIT), the Sora typeface (SIL Open Font License) and the other libraries listed in [`THIRD-PARTY-NOTICES.txt`](frontend/public/THIRD-PARTY-NOTICES.txt), which is also served by the app. The logo was made with an AI image tool and is not covered by this licence.
