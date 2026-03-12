# Raspberry Pi Deployment Guide

This guide walks through running `plm-web` as a persistent service on a Raspberry Pi
so it is available on your LAN (or from anywhere via a reverse proxy).

The MCP server (`plm-mcp`) runs on whatever machine you use Claude Code on — it does
not need to run on the Pi. Both processes share the same JSON data directory; if you
want the MCP server and web UI on the same machine, that works too — just run both.

---

## Which installation method?

| Your situation | Recommended approach |
|---|---|
| Pi running **Raspberry Pi OS Bookworm** (Debian 12) or newer | [Direct install](#1-install-the-package-on-the-pi) — Python 3.11+ ships out of the box |
| Pi running **Bullseye** (Debian 11) or older | [Docker install](#docker-deployment) — avoids the Python 3.9 version conflict |
| Not sure | Run `python3 --version` on the Pi; if it's below 3.11, use Docker |

---

## 1. Install the package on the Pi

```bash
# Install Python 3.12+ if not already present
sudo apt update && sudo apt install -y python3 python3-pip

# Clone the repo (or copy the directory)
git clone https://github.com/your-username/personal_life_manager ~/plm
cd ~/plm

# Install (user-level, no venv needed)
# -e means "editable": pip links to this directory instead of copying files,
# so git pull + reinstall picks up changes without reinstalling from scratch.
# The [dev] extras (pytest etc.) are optional here — leave them off if you
# won't run tests on the Pi.
pip3 install --user -e .

# Confirm the entry points are on PATH
# (pip --user installs to ~/.local/bin — make sure it's in your PATH)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

plm-web --help   # should print usage
```

---

## 2. Set up environment variables

Rather than exporting variables in your shell profile, create a dedicated env file
that the systemd service will load automatically:

```bash
mkdir -p ~/.config/plm
cat > ~/.config/plm/env <<'EOF'
PLM_PASSWORD=change-me
PLM_SESSION_SECRET=use-a-long-random-string-here
PLM_PORT=2026
# PLM_DATA_DIR=~/.local/share/plm   # uncomment to override
# PLM_ROOT_PATH=/plm                 # uncomment if behind a reverse proxy
EOF

chmod 600 ~/.config/plm/env   # keep the password secret
```

Generate a good session secret:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## 3. Create a systemd user service

This runs `plm-web` as a **user service** under your own account, rather than as
root. User services live in `~/.config/systemd/user/` and run with your permissions.

The `<<'EOF'` syntax below is a heredoc — it feeds everything between the two `EOF`
markers into the command's stdin. The quotes around `'EOF'` prevent the shell from
expanding variables inside the block, so `%h` (systemd's placeholder for `$HOME`)
is written literally into the file rather than being substituted now.

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/plm-web.service <<'EOF'
[Unit]
Description=Personal Life Manager web UI
After=network.target

[Service]
# Load environment variables from the env file
EnvironmentFile=%h/.config/plm/env

# The entry point installed by pip
ExecStart=%h/.local/bin/plm-web

# Restart automatically if it crashes
Restart=on-failure
RestartSec=5

# Write logs to journald
StandardOutput=journal
StandardError=journal
SyslogIdentifier=plm-web

[Install]
WantedBy=default.target
EOF
```

Enable and start. Note the `--user` flag — this is what distinguishes user services
from system services (the `systemctl enable` commands you may have used before were
probably for system-level services running as root).

`loginctl enable-linger` is needed because by default systemd tears down user sessions
when no one is logged in. With linger enabled, your user session stays alive at boot
so user services start automatically — just like system services do.

```bash
# Enable linger so the service starts at boot without an active login session
loginctl enable-linger $USER

# Reload systemd, enable, and start
systemctl --user daemon-reload
systemctl --user enable plm-web
systemctl --user start plm-web

# Check it's running
systemctl --user status plm-web

# Follow logs
journalctl --user -u plm-web -f
```

The web UI is now available at `http://<pi-hostname>.local:2026` from any device on
your LAN (replace `2026` with your `PLM_PORT` if you changed it).

---

## 4. Find your Pi on the LAN

```bash
# On the Pi — show IP address
hostname -I

# Most modern home routers also support mDNS, so you can use:
# http://raspberrypi.local:2026
# (replace "raspberrypi" with whatever you named your Pi)
```

---

## 5. Optional: Caddy reverse proxy (HTTPS + friendly URL)

[Caddy](https://caddyserver.com) is a simple, modern web server that handles HTTPS
automatically. It also proxies Server-Sent Events (the live-reload feature) correctly
out of the box — no extra configuration needed.

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

Edit `/etc/caddy/Caddyfile`.

**Option A — PLM gets the whole domain** (simplest; no `PLM_ROOT_PATH` needed):

```
your.domain.com {
    reverse_proxy localhost:2026
}
```

**Option B — PLM lives at a subpath** (e.g. `your.domain.com/plm/`, alongside other
services like Jellyfin):

```
your.domain.com {
    # handle_path strips the /plm prefix before forwarding to the app.
    # Use handle_path here, NOT handle — handle passes the full /plm/... path
    # to the app, which then sees it doubled (/plm/plm/...) and returns 404s.
    handle_path /plm/* {
        reverse_proxy localhost:2026
    }

    # Other services on the same domain:
    handle_path /jelly/* {
        reverse_proxy localhost:8096
    }
}
```

When using Option B, set `PLM_ROOT_PATH=/plm` in your env file (or Docker env vars)
so the app generates correct URLs for links, redirects, and form actions.

Reload Caddy:

```bash
sudo systemctl reload caddy
```

If you give your Pi a real domain name, Caddy will obtain and renew a Let's Encrypt
certificate automatically — HTTPS requires no extra configuration.

---

---

## Docker deployment

Use this approach if your Pi runs Debian Bullseye (or any OS with Python < 3.11).
Docker isolates the app in its own Python 3.12 environment with zero impact on the
host system.

### Install Docker

```bash
# Official convenience script — works on all Debian/Raspberry Pi OS versions
curl -fsSL https://get.docker.com | sh

# Add your user to the docker group so you don't need sudo
sudo usermod -aG docker $USER

# Log out and back in for the group change to take effect, then verify:
docker run hello-world
```

### Set up your env file

The repo ships an `.env.example` template. Copy it and fill in your secrets:

```bash
cd ~/personal_life_manager
cp .env.example .env
```

Edit `.env`:

```
PLM_PASSWORD=change-me
PLM_SESSION_SECRET=use-a-long-random-string-here

# Optional — uncomment to override defaults:
# PLM_PORT=2026
# PLM_ROOT_PATH=/plm
```

Generate a good session secret:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Keep `.env` private — it's gitignored and never committed.

### Start the container

```bash
docker compose up -d --build
```

This builds the image (first time ~1–2 min; subsequent builds reuse cached layers),
then starts the container in the background with `--restart unless-stopped` so it
auto-starts at boot and restarts on crash.

The data directory (`~/.local/share/plm/`) is bind-mounted into the container, so
data lives on the host — Syncthing keeps working and data survives image rebuilds.

### Useful commands

```bash
# Follow logs
docker compose logs -f

# Stop / start
docker compose stop
docker compose start

# Stop and remove the container (data on host is unaffected)
docker compose down
```

### Updating

After every `git pull`, one command rebuilds and restarts:

```bash
cd ~/personal_life_manager
git pull
docker compose up -d --build
```

### Caddy with Docker

The Caddy setup is identical to the direct install — see [section 5](#5-optional-caddy-reverse-proxy-https--friendly-url) above.
Caddy proxies to `localhost:2026` (or whatever `PLM_PORT` you set) regardless of
whether the app runs in Docker or not.

---

## 6. Connecting the MCP server (on a different machine)

If you run Claude Code on your laptop and `plm-web` on the Pi, the recommended setup
is to sync the data directory between the two machines:

### Option A — Sync data with Syncthing (recommended)

Run [Syncthing](https://syncthing.net) on both machines and sync
`~/.local/share/plm/`. Both `plm-mcp` (laptop) and `plm-web` (Pi) then read and
write the same logical data store. Changes Claude makes on the laptop appear in the
browser on the Pi within seconds.

### Option B — MCP server also on the Pi (data lives on Pi)

Install `plm-mcp` on the Pi and point Claude Code at it via SSH transport or a
remote development session. Data never leaves the Pi.

### Option C — Everything on the laptop

Run both `plm-mcp` and `plm-web` on your laptop. Skip the Pi entirely. Useful for
trying things out before committing to a Pi deployment.

---

## 7. Updating (direct install)

The `-e` (editable) install means that `git pull` updates the source in place.
A reinstall is still needed to pick up any new entry points or dependencies added
to `pyproject.toml`:

```bash
cd ~/plm
git pull
pip3 install --user -e .
systemctl --user restart plm-web
```

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Service won't start | `journalctl --user -u plm-web -f` — look for `PLM_PASSWORD` or `PLM_SESSION_SECRET` missing |
| `plm-web: command not found` | `~/.local/bin` not in `$PATH` — re-read the PATH note in step 1 |
| Browser shows connection refused | `systemctl --user status plm-web` — confirm it's active |
| Caddy subpath returns 404 | Make sure you used `handle_path`, not `handle` — see section 5 |
| Browser warns "insecure connection" on forms | Set `PLM_ROOT_PATH` correctly and ensure Caddy forwards `X-Forwarded-Proto` (it does by default) |
| SSE live reload not working | Caddy proxies SSE correctly by default; if using another proxy, ensure response buffering is disabled |
| Page auto-reloads repeatedly | Known issue — see post-MVP polish items in CLAUDE.md. Workaround: ignore or add a debounce |
