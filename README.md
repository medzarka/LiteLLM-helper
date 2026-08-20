# ⚡ LiteLLM Helper & Gateway Control Plane

A self-hosted management UI, analytics dashboard, and automated configuration generator for the **LiteLLM AI Gateway / Proxy**.

Optimized for **ARM64 (Orange Pi 5 Plus 32GB RAM)** and generic Linux/Docker environments.

---

## 🏛️ Architecture Overview

```mermaid
graph TD
    Client[AI Clients / Agents / WebUI] -->|Port 4000| LiteLLM[LiteLLM Proxy / Gateway]
    Admin([Admin / Browser]) -->|Port 5001| Helper[LiteLLM Helper UI & Control Plane]
    
    Helper -->|Generates / Updates| Config[config.yaml Shared Volume]
    LiteLLM -->|Hot Reloads| Config
    LiteLLM -->|Usage Webhook| Helper
    LiteLLM -->|Routing Cache & RPM Tracking| Redis[(LiteLLM Redis Cache)]
```

---

## 🎯 Features

- **Dynamic YAML Generation:** Manage upstream LLM providers, API keys, fallback routes, and load balancing rules via web UI.
- **Hot Reloading:** LiteLLM dynamically watches `config.yaml` and reloads models with zero downtime.
- **Usage & Rate Tracking:** Captures token counts, request latency, and cost analytics via Redis and SQLite.
- **Automatic Key Rotation & Fallbacks:** Seamlessly route requests to backup models when providers experience rate limits.
- **Email Alerts:** Send notifications via SMTP when usage quotas are reached.

---

## 🚀 Quick Start

### 1. Configure Environment
```bash
cp .env.example .env
nano .env
```
Generate strong keys:
```bash
openssl rand -hex 16
```

### 2. Deploy the Stack
```bash
docker compose up -d
```
The `init-volumes` container will automatically create `/data` storage directories and initialize an empty `config.yaml` if not already present.

### 3. Access the Services
- **LiteLLM Helper Dashboard:** `http://<server-ip>:5001`
- **LiteLLM Proxy API Endpoint:** `http://<server-ip>:4000/v1`

---

## ⚙️ Hardware Tuning (Orange Pi 5 Plus 32GB RAM)

- **Worker Concurrency:** `LITELLM_WORKERS=4` allocates parallel worker processes across the RK3588's 8 CPU cores.
- **Ulimits:** Configured `nofile: 65536` for high concurrent network socket throughput.
- **Log Rotation:** Docker JSON logging is limited to `10m` / max 3 files.

---

## 🔒 Security Best Practices

- Change `LITELLM_HELPER_PASSWORD`, `FLASK_SECRET_KEY`, and `LITELLM_MASTER_KEY` before deployment.
- `.env` and `data/` are strictly ignored by Git to protect API keys and usage history databases.
- The `shared_net` Docker network attaches to your reverse proxy with `external: true`.
