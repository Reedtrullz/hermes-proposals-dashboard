# Deployment

Hermes Proposals Dashboard supports local development, Docker Compose self-hosting, and the repository's configured Ansible VPS deployment.

## Configuration

| Variable | Purpose | Hosted guidance |
| --- | --- | --- |
| `HERMES_HOME` | SQLite database and trigger-file directory | Persistent volume such as `/data/hermes` |
| `HERMES_REQUIRE_AUTH` | Enables authentication enforcement | Set to `1` |
| `HERMES_API_KEY` | API client bypass key | Use a long random secret |
| `AUTH_URL` | Trusted authentication host used for sign-in redirect | Set to deployment auth host |

## Local Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
HERMES_REQUIRE_AUTH=0 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8089 --reload
```

Never expose the unauthenticated local-development configuration on a public host.

## Docker Compose

```bash
cp .env.example .env
# Edit HERMES_API_KEY and AUTH_URL.
docker compose up -d --build
```

The compose service exposes port `8089`, stores persistent application state in the `hermes-data` Docker volume, and mounts it at `/data/hermes`.

## Configured VPS Route

The repository's Ansible deployment is configured for:

```text
https://reidar.tech/proposals
```

The application container listens internally on port `8089` and the VPS maps it to localhost port `8091` before Caddy handles `/proposals*`, `/api/proposals*`, and `/api/agents*` requests.

## Ansible Deployment

The root playbook:

- Pulls `ghcr.io/reedtrullz/hermes-proposals-dashboard:latest`.
- Runs the `hermes-proposals-dashboard` container.
- Persists state in the `hermes_proposals_data` Docker volume.
- Requires auth and supplies the API key from encrypted vault data.
- Verifies `/health`.
- Adds the Caddy route handlers and reloads Caddy.
- Rolls back to a previous image if the new container fails health checks.

Validate and deploy:

```bash
ansible-playbook ansible-playbook.yml --syntax-check
ansible-playbook ansible-playbook.yml
```

## Secrets

`group_vars/vps/vault.yml` is intentionally encrypted. Maintain an encrypted vault secret for `vault_hermes_api_key`; never commit the local vault password file or a plaintext production API key.

## Operational Verification

After deployment:

1. Confirm the playbook's health check succeeds.
2. Confirm the authenticated `/proposals` route opens through `reidar.tech`.
3. Confirm SQLite state persists across container restarts.
4. Confirm a test proposal writes expected trigger data only when worker integration is intended.
5. Keep demo validation separate from real worker triggering.
