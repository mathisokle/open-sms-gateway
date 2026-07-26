# Installation

From a fresh Linux install to a gateway that sends and receives, with a checkpoint after every step
so a failure is localised immediately. Budget about 30 minutes on a Raspberry Pi, most of it
waiting for the first Docker build; on an x86 machine it is closer to ten.

Hardware prerequisites and the SIM PIN are covered in [hardware.md](hardware.md). Read that first
if you have not picked parts yet.

## 0. Before you start

- A Linux host with a **64-bit kernel and a free USB port**, reachable over SSH. A Raspberry Pi 3
  or newer, a mini PC, a NAS or a VM with USB passthrough all work — see
  [hardware.md](hardware.md#any-linux-host-will-do).
- SIM7600E-H dongle plugged in, both antennas attached, SIM inserted with its **PIN disabled**
- An adequate power supply — on a Pi that means 5 V / 2.5 A or better

Commands below assume a Debian-family distribution (`apt`). On another distribution only the
package-manager lines change.

## 1. Free the serial port

```bash
sudo systemctl disable --now ModemManager
```

**Checkpoint:** `systemctl is-enabled ModemManager` prints `disabled`, `masked`, or an error saying
the unit does not exist. Any of those is fine.

## 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # then log out and back in
```

**Checkpoint:** `docker compose version` prints a version. If the install fails complaining about a
package lock, wait for unattended-upgrades to finish and run it again.

## 3. Find the modem's AT port

```bash
ls -l /dev/serial/by-id/
```

Copy the full path of the entry containing **`-if02-port0`**:

```
usb-SimTech__Incorporated_SimTech__Incorporated_0123456789ABCDEF-if02-port0
```

**Checkpoint:** you have a path under `/dev/serial/by-id/` that contains `if02`. If the directory is
empty, the dongle is not enumerating — try another cable or port before continuing. Do not
substitute `/dev/ttyUSB2`; see [hardware.md](hardware.md#the-serial-port-that-matters).

## 4. Get the code

```bash
git clone https://github.com/mathisokle/open-sms-gateway.git
cd open-sms-gateway
cp .env.example .env
```

## 5. Configure

Generate the two secrets first:

```bash
openssl rand -hex 32     # use this for SECRET_KEY
openssl rand -base64 18  # a reasonable ADMIN_PASSWORD
```

Then edit `.env`:

```ini
ADMIN_USER=admin
ADMIN_PASSWORD=<at least 12 characters>
SECRET_KEY=<at least 32 characters>

HOST_MODEM_DEVICE=/dev/serial/by-id/usb-SimTech...-if02-port0
MODEM_FAKE=0

MESSAGES_PER_MINUTE=6
TZ=Europe/Zurich
```

The gateway refuses to start with a weak or missing secret rather than running insecurely, and it
reports every problem at once. Every variable is documented in
[configuration.md](configuration.md).

**Checkpoint:** `grep -c . .env` shows your file is not empty, and `HOST_MODEM_DEVICE` contains the
`if02` path from step 3.

## 6. Build and start

```bash
docker compose up -d --build
```

The first build on a Pi 3 takes several minutes — it installs `python3-gammu` from apt and the
Python dependencies with pip. Later builds are much faster.

**Checkpoint:**

```bash
docker compose ps          # api and worker both "running"
curl -s localhost:8080/healthz
```

`/healthz` should answer with `"status": "ok"` and `"modem": {"connected": true}`. A `503` means the
worker has not written a heartbeat in the last 120 seconds — check `docker compose logs worker`.

If a container is stuck restarting, read the log: a configuration problem is printed as
`invalid configuration: ...` and names every issue at once.

## 7. First login

Open `http://<gateway>:8080/admin` and log in with `ADMIN_USER` / `ADMIN_PASSWORD`.

The dashboard should show the modem as connected, with a signal percentage, your operator's name
and a recent "worker last seen". If the modem shows as not connected while the containers are
healthy, the worker is talking to the wrong serial port — revisit step 3.

**Set the gateway number** under Settings. The modem cannot read the SIM's own number reliably, so
this is a display-only field that makes the dashboard and chat headers meaningful.

## 8. Send the first message

Use Settings → **Send test SMS**, to your own phone. Watch it move from `queued` to `sending` to
`sent` under Messages.

**Checkpoint:** the message arrives on your phone. If it goes to `failed`, the `error` column names
the reason — usually credit, a blocked recipient, or a malformed number.

## 9. Create an API token

Under **API Tokens**, create a token with a label describing its consumer (`monitoring`,
`order-system`). The plaintext is shown **exactly once** — copy it now; afterwards only the prefix
remains, because the gateway stores nothing but a SHA-256 hash.

```bash
curl -X POST http://<gateway>:8080/api/v1/messages \
  -H "Authorization: Bearer sms_..." \
  -H "Content-Type: application/json" \
  -d '{"to": "+41791234567", "body": "Hello from the gateway."}'
```

**Checkpoint:** a `201` with a message id, and the message appears in the panel.

## 10. Receiving

Send an SMS *to* the gateway's number from your phone. Within about five seconds it appears under
Chats and Messages.

To have inbound messages pushed to your own service, set a **webhook URL** under Settings. The
gateway generates a signing secret; verify it on your side as described in
[manual/webhooks.md](manual/webhooks.md). Delivery attempts, response codes and retries are visible
under **Webhook Log**.

**Checkpoint:** an inbound SMS appears in the panel, and — if configured — a `delivered` row in the
webhook log.

## 11. Make it survive reboots

Both services use `restart: unless-stopped`, so Docker starts them again after a reboot. Confirm it
rather than assume it:

```bash
sudo reboot
# after it comes back:
docker compose ps && curl -s localhost:8080/healthz
```

**Checkpoint:** both containers are running and `/healthz` is `ok`, without you having run anything.

## Where to go next

- Take a backup and know how to restore it: [operations.md](operations.md#backup-and-restore)
- Decide how the gateway is reachable, before exposing it: [security.md](security.md)
- Tune the throttle and the rate limit: [configuration.md](configuration.md)
- Anything misbehaving: [manual/troubleshooting.md](manual/troubleshooting.md)

## Updating an existing installation

```bash
cd open-sms-gateway
git pull
docker compose up -d --build
```

Database migrations are additive and run automatically at startup. Take a backup first anyway —
it is one file, and it costs nothing. Full procedure and rollback:
[operations.md](operations.md#updating).
