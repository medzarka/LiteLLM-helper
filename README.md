<div align="center">
  <h1>LiteLLM Helper UI 🚀</h1>
  <p><strong>The Ultimate Control Plane for the LiteLLM AI Gateway</strong></p>
</div>

<br />

LiteLLM Helper v3 is a robust, web-based UI configuration generator and control plane for the LiteLLM Proxy. Stop manually writing complex YAML files! Use the responsive UI to effortlessly manage LLM providers, API keys, load balancing, models, and failover architectures.

## ✨ Features

- **Provider Management:** Easily add custom AI service providers (e.g., OpenRouter, Groq, OpenAI).
- **API Key Vault:** Store, rotate, and manage API keys seamlessly.
- **Aggregated Models:** Load balance requests by grouping multiple provider models under one shared alias.
- **Failover / Fallbacks:** Designate secondary backup models in case primary models fail or rate limit.
- **Hermes Agent Mapping:** Map static virtual aliases (like `hermes-vision`) directly to backend models. Assign primary and fallback models to ensure Hermes tasks never fail due to rate limits. Swap out Hermes models instantly without touching Hermes configuration.
- **Key Rotation Strategies:** Choose between Round Robin, Random, Least Busy, or Failover Priority rotations.
- **Live Syncing & Versioning:** Generate the `config.yaml`, hot-reload the running LiteLLM container without downtime, and save configuration snapshots to roll back at any time.
- **Model Health Metrics:** Ping and verify models in real time from the dashboard.

## 🛠 Prerequisites

Before deploying the AI stack, ensure the host has the following:
- Docker & Docker Compose
- Target Host running Linux/ARM (or equivalent host capable of running the Docker containers)
- Python 3.10+ (if running the helper locally outside of Docker)

## 🚀 Installation & Deployment

We provide a bundled setup script and `docker-compose.yml` to spin up LiteLLM Helper, the LiteLLM Gateway, Open-WebUI, and Qdrant in one unified stack.

### 1. Initialize Host Directories
First, set up the permanent host volume directories by running the included `setup.sh` script. This script establishes the `/home/mgrsys/DATA/var/ai-ui` environment.

```bash
chmod +x setup.sh
./setup.sh
```

**What this script does:**
- Creates dedicated data directories for Postgres (if used), Open-WebUI, LiteLLM, Qdrant, Hermes, and the LiteLLM Helper internal database.
- Copies local starting configurations into the permanent `litellm` shared volume.
- Secures the folder permissions for the Docker engine.

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and fill out the variables:

```bash
cp .env.example .env
```
Ensure the following key configurations are set:

- **`LITELLM_HELPER_PASSWORD`**: Secures the Helper UI.
- **`LITELLM_HELPER_PORT`**: The port to expose the helper UI on.
- **`WORKDIR`**: The base directory on the host for all volume data (defaults to `/home/mgrsys/DATA/var/ai-ui`).
- **External IPs and Ports**: If Open-WebUI's external RAG capabilities are utilized, define the IPs/hostnames and ports where these services reside (`SEARXNG_IP`, `SEARXNG_PORT`, `DOCLING_IP`, `DOCLING_PORT`, `INFINITY_IP`, `INFINITY_PORT`).

### 3. Launch the Stack
Run docker-compose to launch the full AI proxy UI stack:

```bash
docker-compose up -d
```

### 4. Access the Services
Once running, the applications can be accessed at:

- **LiteLLM Helper UI:** [http://localhost:5001](http://localhost:5001)
- **LiteLLM Proxy API:** [http://localhost:4000](http://localhost:4000)
- **Open-WebUI (Chat):** [http://localhost:3030](http://localhost:3030)
- **Qdrant Vector DB:** [http://localhost:6333](http://localhost:6333)

## 🔄 Live Hot-Reloading

When changes are made in the **LiteLLM Helper UI** (e.g. adding a new model or updating an API key), simply navigate to the **Export Config** tab. 

Clicking **"Sync to Live LiteLLM"** will safely overwrite the shared `config.yaml` in the host volume (`/home/mgrsys/DATA/var/ai-ui/litellm`). The LiteLLM container is running with the `--reload` parameter, meaning it will detect the change and restart its workers immediately—zero manual restarts required!

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! 
Feel free to check out the [Issues page](https://github.com/medzarka/LiteLLM-helper/issues).

## 📝 Credits

Developed and maintained by [Mohamed Zarka](https://github.com/medzarka) (@medzarka).
For inquiries, reach out at `medzarka@gmail.com`.
