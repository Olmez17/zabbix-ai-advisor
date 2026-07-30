# Zabbix AI Advisor

A small FastAPI service that receives Zabbix alert/problem data, sends it
to Google's Gemini API for a short root-cause diagnosis, and displays it
on a simple web dashboard — with an optional human-approved remediation
action.

Gemini offers a **permanent, no-credit-card free tier**, which is more
than enough for this scale of use. See the "Choosing a model" note below
— not all Gemini model names stay available forever, so this project
uses a `-latest` alias to stay resilient to Google's frequent model
retirements.

## Architecture

```
Zabbix (trigger action) --webhook--> FastAPI /webhook --> Gemini API
                                              |
                                              v
                                         SQLite (alerts.db)
                                              |
                                              v
                                      Dashboard (GET /)
```

## 0) Before pushing to GitHub (critical security step)

Never commit `GEMINI_API_KEY` or `WEBHOOK_SECRET` as plain text. Use a
`.env` file instead:

```bash
cd ~/zabbix
cp zabbix-ai-advisor/.env.example .env
nano .env   # fill in GEMINI_API_KEY and WEBHOOK_SECRET with real values
```

Make sure `docker-compose.yml`'s `ai-advisor` service uses the
`${GEMINI_API_KEY}`-style variable references shown in
`docker-compose.snippet.yml` (not hardcoded values). Docker Compose
automatically reads a `.env` file in the same directory.

The provided `.gitignore` already excludes `.env` — move it to your repo
root (`~/zabbix/.gitignore`) if it isn't already there.

### Pushing to GitHub

```bash
cd ~/zabbix
git init
git add .
git status   # confirm .env is NOT listed!
git commit -m "Zabbix + Docker monitoring + AI Advisor"
gh repo create your-repo-name --public --source=. --remote=origin --push
```

**Final check:** open `docker-compose.yml` on GitHub and confirm
`GEMINI_API_KEY` / `WEBHOOK_SECRET` show placeholders like
`${GEMINI_API_KEY}`, never a real value.

## 1) Copy the folder to your server

Copy this whole folder to `~/zabbix/zabbix-ai-advisor/` (i.e. as a
subdirectory next to `~/zabbix/docker-compose.yml`).

```bash
ls ~/zabbix
# docker-compose.yml  agent2-nginx.conf  zabbix-ai-advisor/
```

## 2) Add the service to docker-compose.yml

Merge the block from `docker-compose.snippet.yml` into the `services:`
section of `~/zabbix/docker-compose.yml`, and add the `ai-advisor-data:`
line under `volumes:`.

## 3) Get a free Gemini API key

1. Go to **aistudio.google.com/apikey**
2. Sign in with a Google account (no credit card required)
3. Click **"Create API key"**, copy it into your `.env` file

### Choosing a model

Google renames and retires specific Gemini model versions fairly often
(we personally hit 404 errors on `gemini-2.5-flash`, `gemini-3.5-flash`,
and `gemini-2.5-flash-lite` while building this — all "no longer
available to new users" despite being recent). To avoid re-debugging
this every few months, this project defaults to `gemini-flash-lite-latest`,
an alias Google keeps pointed at whatever their current lightweight Flash
model is.

If you ever see a `404 NOT_FOUND ... no longer available` error, list the
models your key actually has access to and pick a working one:

```bash
docker exec -it zabbix-ai-advisor python3 -c "
import os
from google import genai
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
for m in client.models.list():
    if 'generateContent' in (m.supported_actions or []):
        print(m.name)
"
```

Prefer `-latest` aliases (`gemini-flash-latest`, `gemini-flash-lite-latest`)
over pinned version numbers — they're more resilient to Google's model
lifecycle changes. Also watch out for free-tier daily quotas: newer
preview-class models (e.g. `gemini-3.5-flash`) can have very low daily
limits (as low as 20 requests/day), while established models like
`gemini-2.0-flash` or the `-latest` aliases typically get much higher
quotas (~1,000+ requests/day).

## 4) Build & run

```bash
cd ~/zabbix
docker compose up -d --build ai-advisor
docker logs zabbix-ai-advisor
```

Test:

```bash
curl http://localhost:8001/health
# {"status":"ok","ai_enabled":true}
```

Open the dashboard: `http://<your-server-ip>:8001`

## 5) Create a Zabbix Webhook media type

**Alerts → Media types → Create media type**

- **Name:** `AI Advisor`
- **Type:** `Webhook`
- **Parameters:**

| Name | Value |
|---|---|
| URL | `http://ai-advisor:8001/webhook` |
| Secret | *(same value as `WEBHOOK_SECRET` in your `.env`)* |
| Host | `{HOST.NAME}` |
| Severity | `{EVENT.SEVERITY}` |
| EventName | `{EVENT.NAME}` |
| Message | `{ALERT.MESSAGE}` |
| Status | `{EVENT.STATUS}` |

- Paste the contents of `zabbix_webhook.js` into the **Script** box.
- Enable and save.

> We use `ai-advisor` as the hostname because both containers share the
> same Docker Compose network — Compose's built-in DNS resolves service
> names to container IPs automatically.

## 6) Add the media type to your user

**Users → Users → [your user] → Media → Add**
- **Type:** `AI Advisor`
- **Status:** Enabled

## 7) Add it to a trigger action

**Alerts → Actions → Trigger actions → [your action]** → **Operations** →
edit the operation → make sure **"Send to media type"** is set to
`All available` (or explicitly include `AI Advisor`).

> **Important:** the operation must have a **Custom message** defined
> (Subject + Message fields filled in). Without it, `{ALERT.MESSAGE}`
> resolves to empty and the webhook script fails with
> `"No message defined for media type."`

## 8) Approving remediation actions

The AI appends a hidden, structured action suggestion at the end of its
analysis (not shown to the user). If it identifies a concrete,
automatable action, the dashboard shows an **"Apply this action"** button.

**Security design — important:**
- The AI's free-text output is **never** executed directly as a command.
- Only three fixed, whitelisted action types are allowed:
  `restart_container`, `stop_container`, `start_container`.
- The container name is validated against the real Docker API
  (`docker_client.containers.get(...)`) before anything runs — a
  made-up/nonexistent name simply fails, nothing executes.
- Actions only run when **you** click the button (human-in-the-loop) —
  the AI never acts on its own.

This requires mounting `docker.sock` into the `ai-advisor` container
(already set up in `docker-compose.snippet.yml`, without `:ro` since
restart/stop/start need write access).

⚠️ **Note:** granting write access to `docker.sock` means this container
can control **any** Docker container on the host, not just monitored
ones. A reasonable trade-off for a personal/lab setup, but worth
isolating further in production (dedicated user, a scoped proxy, etc.).

## 9) Test

```bash
docker stop weather-app-web-1
```

Wait a couple of minutes, then check `http://<your-server-ip>:8001` — a
new card should appear with the real Zabbix problem and Gemini's
diagnosis, plus an "Apply this action" button if applicable.

```bash
docker start weather-app-web-1
```

## Notes

- `GEMINI_MODEL` defaults to `gemini-flash-lite-latest`. See "Choosing a
  model" above if you hit a 404 or 429 error.
- Free-tier prompts/responses may be used by Google to improve their
  models. Keep that in mind if your alert messages ever contain sensitive
  data.
- The dashboard auto-refreshes every 30 seconds.
- Data is persisted in the `ai-advisor-data` Docker volume
  (`/data/alerts.db`).
