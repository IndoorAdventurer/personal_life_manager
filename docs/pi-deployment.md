# Raspberry Pi Deployment Guide

This guide walks through running `plm-web` as a persistent service on a Raspberry Pi
so it is available on your LAN.

The MCP server (`plm-mcp`) runs on whatever machine you use Claude Code on — it does
not need to run on the Pi. Both processes share the same JSON data directory; if you
want the MCP server and web UI on the same machine, that works too — just run both.

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
PLM_PORT=8000
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

The web UI is now available at `http://<pi-hostname>.local:8000` from any device on
your LAN.

---

## 4. Find your Pi on the LAN

```bash
# On the Pi — show IP address
hostname -I

# Most modern home routers also support mDNS, so you can use:
# http://raspberrypi.local:8000
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

Edit `/etc/caddy/Caddyfile`:

```
# Proxy the entire site — no subpath, no PLM_ROOT_PATH needed
:80 {
    reverse_proxy localhost:8000
}
```

Restart Caddy:

```bash
sudo systemctl reload caddy
```

The web UI is now available at `http://<pi-hostname>.local` (port 80, no port number
in the URL). If you give your Pi a real domain name, Caddy will obtain and renew a
Let's Encrypt certificate automatically.

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

## 7. Updating

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
| SSE live reload not working | Caddy proxies SSE correctly by default; if using another proxy, ensure response buffering is disabled |
| Page auto-reloads repeatedly | Known issue — see post-MVP polish items in CLAUDE.md. Workaround: ignore or add a debounce |
