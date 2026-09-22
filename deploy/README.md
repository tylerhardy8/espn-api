# Sharing the Fantasy Football Analyzer with friends

Each friend gets their **own instance** — their own URL, password, leagues,
ESPN cookies, and AI key. Nothing is shared between them. You run one small
server; they just visit a link.

## What you need

- A VPS with Docker installed (any $6-12/month box: Hetzner, DigitalOcean,
  Linode...) — 2 GB RAM comfortably runs ~8-10 friends
- A domain with a **wildcard DNS record**: `*.ffa.yourdomain.com -> <VPS IP>`

## One-time setup

```bash
git clone <this repo> && cd espn-api/deploy
cp .env.example .env        # edit: set DOMAIN=ffa.yourdomain.com
```

## Add a friend

```bash
./add-friend.sh mike
docker compose up -d --build
```

The script prints their URL (`https://mike.ffa.yourdomain.com`), login, and a
generated password — text those to them along with this:

> 1. Open the link, log in with the password I sent
> 2. Go to **Setup**, enter your ESPN league ID (it's in your league's URL)
> 3. Private league? Expand "How do I find my ESPN cookies?" and follow the steps
> 4. Want AI draft advice? Paste your own Anthropic API key in the AI section
>    (console.anthropic.com — a draft night costs a dollar or two on your card)

## Day-2 operations

| Task | Command |
|---|---|
| Update everyone to the latest code | `git pull && docker compose up -d --build` |
| Remove a friend | delete their block in `friends.yml` + `sites/<name>.caddy`, then `docker compose up -d --remove-orphans` |
| Reset a password | delete those two entries and re-run `./add-friend.sh <name>` (their config in `data/<name>` survives) |
| Back up everyone's configs | copy the `deploy/data/` directory |
| See logs | `docker compose logs -f ffa-mike` |

## Notes

- HTTPS is automatic (Caddy + Let's Encrypt) once DNS points at the box.
- Each instance runs gunicorn (2 workers). AI spend is on each friend's own
  key — an instance with no key simply shows AI features as "not configured".
- ESPN cookies expire after a season or so; the fix is re-pasting them via
  the same Setup steps.
