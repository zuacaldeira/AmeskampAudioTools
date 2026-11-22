# Ameskamp Studio — umbrella site

The landing page, nginx reverse-proxy configuration, and deploy automation that ties every tool in this repository together under [ameskamp.studio](https://ameskamp.studio).

## Layout

| Subdomain                       | Tool             | Port (loopback) |
| ------------------------------- | ---------------- | --------------- |
| `ameskamp.studio`               | landing page     | nginx static    |
| `metadata.ameskamp.studio`      | audio-metadata   | 5001            |
| `match.ameskamp.studio`         | audio-match      | 5002            |
| `silence.ameskamp.studio`       | silence-trimmer  | 5000            |
| `events.ameskamp.studio`        | audio-events     | _(soon)_        |
| `similar.ameskamp.studio`       | audio-similar    | _(soon)_        |

All tools run as Docker containers (root `docker-compose.yml`) bound to `127.0.0.1`. A host-level nginx handles TLS termination and reverse-proxies each subdomain to the correct container.

## Infrastructure

- **Host:** Strato VPS at `217.154.2.230`, accessed as `root@217.154.2.230`.
- **Project path on VPS:** `/opt/ameskamp-audio-tools/`
- **SSL:** Let's Encrypt, one cert per subdomain, http-01 challenge via `/var/www/certbot`.
- **Renewal:** certbot installs its own systemd timer at install time — no extra cron needed.

## One-time prep

### 1. DNS records at strato.de

Add A records pointing every subdomain at the VPS IP:

| Type | Name                    | Value           |
| ---- | ----------------------- | --------------- |
| A    | `@` (ameskamp.studio)   | `217.154.2.230` |
| A    | `www`                   | `217.154.2.230` |
| A    | `metadata`              | `217.154.2.230` |
| A    | `match`                 | `217.154.2.230` |
| A    | `silence`               | `217.154.2.230` |
| A    | `events`                | `217.154.2.230` |
| A    | `similar`               | `217.154.2.230` |

DNS must propagate before `deploy.sh` can issue certs — confirm with `dig ameskamp.studio +short` returning the IP.

### 2. Secrets on the VPS

Create `/opt/ameskamp-audio-tools/.env` on the box (compose auto-loads it):

```bash
ssh root@217.154.2.230 'cat > /opt/ameskamp-audio-tools/.env' <<'EOF'
ACOUSTID_API_KEY=your_acoustid_key_here
EOF
```

Without this, `match.ameskamp.studio` returns 503 on every lookup.

## Deploying

From the repository root:

```bash
./ameskamp-studio/deploy.sh
```

The script:

1. **Rsyncs** the repo to `/opt/ameskamp-audio-tools/` (excludes `.git`, `.venv`, caches).
2. **Builds and starts** the umbrella docker-compose.
3. **Installs nginx snippets** (`ameskamp-ssl-params.conf`, `ameskamp-proxy-params.conf`) and per-subdomain server blocks. For subdomains with no certificate yet, a temporary HTTP-only stub is installed first so certbot can run.
4. **Issues** Let's Encrypt certificates for any subdomain that doesn't already have one, then swaps in the full HTTPS configs.
5. **Health-checks** each subdomain (`200`/`301`/`302` = OK).

Re-running the script is safe — already-issued certs are skipped, configs are refreshed in place.

## Local development

The umbrella compose works locally too. From the repo root:

```bash
docker compose up --build
```

Then hit each tool directly on its loopback port (no nginx involved):

- <http://localhost:5000> — silence-trimmer
- <http://localhost:5001> — audio-metadata
- <http://localhost:5002> — audio-match

The landing page is a static file — preview it with any HTTP server:

```bash
python3 -m http.server -d ameskamp-studio/landing 7777
# then open http://localhost:7777
```

## Adding a new tool to the umbrella

1. Add the tool's service to the root `docker-compose.yml`, binding to a unique loopback port.
2. Drop an nginx server-block file into `ameskamp-studio/nginx/conf.d/<sub>.ameskamp.studio.conf` (copy an existing one and swap the port).
3. Append the subdomain to the `SUBDOMAINS=(...)` array in `deploy.sh`.
4. Add the DNS record at strato.
5. Edit `ameskamp-studio/landing/index.html` — either add a new tool card or flip its badge from `SOON` to `LIVE`.
6. Re-run `./ameskamp-studio/deploy.sh`.

## Migrating from the old domain

`silence-trimmer` previously lived at `ameskamp.zuacaldeira.com`. After this deploy:

- The container is now reachable at `silence.ameskamp.studio`.
- The legacy nginx config at `/etc/nginx/sites-enabled/ameskamp.zuacaldeira.com` is left in place untouched. Remove it manually if you want to retire that domain, or rewrite it as a 301 to `https://silence.ameskamp.studio`.

## Troubleshooting

| Symptom                                                       | Look at                                                                |
| ------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Cert issuance fails with "DNS problem"                        | `dig <subdomain>.ameskamp.studio +short` — record not propagated yet.  |
| Subdomain returns 502                                         | `docker compose logs <service>` on the VPS — container likely crashing. |
| `match.ameskamp.studio` returns 503                           | `.env` is missing or `ACOUSTID_API_KEY` blank.                         |
| `nginx -t` fails after pulling new commits                    | Re-run `deploy.sh` — it copies the snippets that the configs include.   |
| Want to roll back                                             | `ssh root@217.154.2.230 'cd /opt/ameskamp-audio-tools && git log -1'` to see deployed SHA; revert in the working tree and re-deploy. |
