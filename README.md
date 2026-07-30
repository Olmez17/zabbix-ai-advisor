# Zabbix + Docker Monitoring + AI Advisor

This repo contains a Docker Compose–based Zabbix monitoring stack
(MySQL, Zabbix server/web/agent) integrated with a custom **AI Advisor**
service. When Zabbix fires an alert, it's forwarded via webhook to a
FastAPI service that asks Google's Gemini API for a short root-cause
diagnosis, then displays it on a live dashboard — with an optional
human-approved remediation action (restart/stop/start a container).

## Architecture

```
Zabbix (trigger action) --webhook--> FastAPI /webhook --> Gemini API
                                              |
                                              v
                                         SQLite (alerts.db)
                                              |
                                              v
                                  Dashboard (approve & remediate)
```

## Contents

- `docker-compose.yml` — Zabbix server/web/agent + MySQL + AI Advisor services
- `agent2-nginx.conf` — Nginx `stub_status` monitoring config for Zabbix agent2
- `zabbix-ai-advisor/` — AI Advisor service source code (see the [detailed README](zabbix-ai-advisor/README.md))

## Features

- Docker container monitoring via Zabbix agent2's built-in Docker plugin
  (CPU, memory, network, health status, restart count, auto-discovery of
  running **and stopped** containers)
- Nginx reverse status monitoring via a custom UserParameter + dependent items
- Email alerting through Zabbix's native email media type
- **AI Advisor**: a webhook-triggered FastAPI service that sends alert
  context to Gemini (free tier, no credit card required) and returns a
  short, structured diagnosis
- Human-in-the-loop remediation: the AI can suggest a concrete action
  (`restart_container` / `stop_container` / `start_container`), but it is
  never executed automatically — a person must click "Apply" on the
  dashboard, and the backend only ever calls a small whitelisted set of
  Docker SDK operations (never arbitrary shell commands)

## Setup

See [zabbix-ai-advisor/README.md](zabbix-ai-advisor/README.md) for full
setup instructions, including how to get a free Gemini API key and how to
wire up the Zabbix webhook media type.

## Security notes

- Secrets (`GEMINI_API_KEY`, `WEBHOOK_SECRET`) are read from a local `.env`
  file, which is git-ignored and never committed.
- The AI Advisor container has write access to `/var/run/docker.sock` in
  order to execute approved remediation actions. This means it can control
  *any* container on the host, not just monitored ones — a reasonable
  trade-off for a personal/lab environment, but worth isolating further
  (e.g. a scoped proxy) in a production setting.
