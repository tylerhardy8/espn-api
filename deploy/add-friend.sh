#!/usr/bin/env bash
# Add a friend's instance: generates a password, writes their Caddy site
# (subdomain + basic auth) and their app service in friends.yml.
#
# Usage: ./add-friend.sh <name>
#   <name> becomes the subdomain and container suffix: lowercase letters,
#   digits, and hyphens only (e.g. ./add-friend.sh mike -> mike.$DOMAIN)
#
# After adding: docker compose up -d --build
# To remove a friend: delete their block from friends.yml and sites/<name>.caddy,
# then: docker compose up -d --remove-orphans   (their config survives in
# deploy/data/<name> until you delete that directory)

set -euo pipefail
cd "$(dirname "$0")"

NAME="${1:-}"
if [[ ! "$NAME" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    echo "Usage: $0 <name>   (lowercase letters, digits, hyphens)"
    exit 1
fi

if [[ ! -f .env ]]; then
    echo "Missing .env — copy .env.example and set DOMAIN first."
    exit 1
fi
source .env

mkdir -p sites
touch friends.yml
if grep -q "ffa-${NAME}:" friends.yml 2>/dev/null; then
    echo "Friend '${NAME}' already exists in friends.yml"
    exit 1
fi

PASSWORD="$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-16)"
HASH="$(docker run --rm caddy:2 caddy hash-password --plaintext "$PASSWORD")"

cat > "sites/${NAME}.caddy" <<EOF
${NAME}.{\$DOMAIN} {
	basic_auth {
		${NAME} ${HASH}
	}
	reverse_proxy ffa-${NAME}:5000
}
EOF

# Seed the services map on first run
if ! grep -q "^services:" friends.yml; then
    printf 'services:\n' >> friends.yml
fi

# Per-friend config lives in a bind mount for easy backup (deploy/data/<name>)
mkdir -p "data/${NAME}"

cat >> friends.yml <<EOF

  ffa-${NAME}:
    build: ..
    restart: unless-stopped
    entrypoint: ["gunicorn"]
    command: ["-w", "2", "-b", "0.0.0.0:5000", "--timeout", "180",
              "fantasy_football_analyzer.web:create_app()"]
    volumes:
      - ./data/${NAME}:/root
    networks:
      - ffa
EOF

echo
echo "============================================================"
echo "  Friend added: ${NAME}"
echo "  URL:      https://${NAME}.${DOMAIN}"
echo "  Login:    ${NAME}"
echo "  Password: ${PASSWORD}"
echo "============================================================"
echo
echo "Now run:  docker compose up -d --build"
echo "Then send them the URL + login and point them at the Setup page."
echo "(Save the password somewhere — it is only shown once; the config"
echo "stores just the hash. Re-run this after deleting their files to reset.)"
