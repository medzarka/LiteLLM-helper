<div align="center">
  <h1>LiteLLM Helper UI 🚀</h1>
  <p><strong>The Ultimate Control Plane for the LiteLLM AI Gateway</strong></p>
</div>

<br />

LiteLLM Helper v4 is a robust, web-based UI configuration generator and control plane for the LiteLLM Proxy. Stop manually writing complex YAML files! Use the responsive UI to effortlessly manage LLM providers, API keys, load balancing, models, and failover architectures.

## 🎯 App Objectives

LiteLLM Helper is designed to be the ultimate Control Plane for the LiteLLM AI Gateway.
Its primary objective is to simplify and automate the complex configuration of LiteLLM proxies. Instead of manually editing large YAML files, this app provides a structured UI to manage providers, API keys, routing rules, load balancing, and model capabilities. It ensures the AI stack remains robust, scalable, and easy to maintain by dynamically generating and syncing configurations to the live gateway.

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

## 🗺️ Interface Guide

Below is a comprehensive enumeration of every interface within the LiteLLM Helper, detailing its objectives, how to use it, and critical tips.

### 1. Providers
- **Objectives:** Define AI service providers (e.g., OpenAI, Groq, OpenRouter) and manage their base URLs.
- **Content:** A list of configured providers.
- **Steps:** Click "Add New Provider", select the type from the dropdown, and optionally set a custom API base URL.
- **Tips/Hints:** The API Base URL will auto-fill for common providers! 
- **Errors/Warnings:** Ensure you select the exact LiteLLM provider type. A mismatch will cause LiteLLM to fail routing.

### 2. Emails
- **Objectives:** Manage organizational email accounts used for registering API keys.
- **Content:** A list of email addresses categorized by type (e.g., Gmail, Outlook).
- **Steps:** Click "Add New Email", input the email address, and select its provider type.
- **Tips/Hints:** Useful for tracking which developer or team owns which API keys to avoid overlapping bans.

### 3. API Keys
- **Objectives:** Store, assign, and manage multiple API keys across providers and emails.
- **Content:** Secure vault of API keys linked to specific providers and emails.
- **Steps:** Click "Add New Key", select the Provider, select the Email, give the key a friendly name, and paste the value.
- **Tips/Hints:** You can instantly enable or disable a key using the toggle switch. Disabled keys are excluded from the final `config.yaml`.
- **Errors/Warnings:** LiteLLM will fail to start if it detects malformed keys. Ensure no extra spaces are pasted.

### 4. Models
- **Objectives:** Configure the specific AI models you intend to route traffic to, including their context windows, capabilities, rate limits, and custom skills.
- **Content:** Table of active models with RPM/TPM limits, Context window sizes, and inferred skills (like Math, Coding, Vision).
- **Steps:** Click "Add New Model", select the Provider, define the "Shared Name" (how you refer to it) and the "Actual Model" (what the provider calls it).
- **Tips/Hints:** We automatically infer skills (like `math`, `vision`, `coding`) based on the model's name and description. Use the "Discover Free Models" or "Inspect Deprecated Models" buttons to bulk-manage models via API!
- **Errors/Warnings:** Setting rate limits (RPM/TPM) to `0` means unlimited. Be careful with this on paid tiers.

### 5. Aggregated Models
- **Objectives:** Create shared aliases (e.g., `smart-model`) that route traffic across multiple underlying provider models for load balancing and redundancy.
- **Content:** A mapping of alias names to multiple backend models.
- **Steps:** Click "Add Aggregation", name the virtual model, and select all the backend models it should route to.
- **Tips/Hints:** Use Aggregated Models when you want to **load-balance** across multiple providers simultaneously (e.g., routing `llama3` traffic to both Groq and OpenRouter).

### 6. Fallbacks
- **Objectives:** Define failover chains so that if a primary model fails, the request is automatically routed to a backup model.
- **Content:** A mapping of a primary model to an ordered list of fallback models.
- **Steps:** Click "Add Fallback", select the Primary Model, and then select the Fallback Models in order of priority.
- **Tips/Hints:** Use Fallbacks when a primary model is strictly preferred, and a secondary is only used if the primary fails (unlike Aggregations, which round-robin traffic).
- **Errors/Warnings:** Do not create circular fallbacks (A -> B -> A). LiteLLM will throw an error.

### 7. Hermes Agents
- **Objectives:** Map task-specific virtual models (e.g., `hermes-vision`, `hermes-mcp`) directly to the backend models. 
- **Content:** A dashboard of specific agentic tasks with recommended models.
- **Steps:** Select the primary model you want to assign to each Hermes task from the dropdowns and click Save.
- **Tips/Hints:** This interface allows Hermes to reliably request task-specific models without needing to edit the Hermes configuration. Swap out Hermes models instantly!

### 8. Settings
- **Objectives:** Configure global rotation strategies and retry limits.
- **Content:** Settings for Routing Strategy (e.g., simple-shuffle, least-busy), Key Rotation Strategy, Cooldown Times, and Max Retries.
- **Steps:** Adjust the sliders/dropdowns and click "Save Settings".
- **Tips/Hints:** `least-busy` is excellent for high-traffic environments, while `simple-shuffle` (Round Robin) is best for general use.

### 9. Export Config
- **Objectives:** Generate the final `config.yaml` file, sync it live to the LiteLLM Docker container, and manage Configuration Versions (snapshots).
- **Content:** The raw YAML output, a sync button, and a version history table.
- **Steps:** Select which modules to include in the export via the checkboxes, review the YAML, and click "Sync to Live LiteLLM".
- **Tips/Hints:** There is no need to restart LiteLLM! As long as the container is running with `--reload`, it will hot-reload the changes instantly.
- **Errors/Warnings:** Always create a new Version snapshot before syncing experimental changes, so you can quickly restore if LiteLLM crashes.

### 10. Documentation
- **Objectives:** Provide in-app guidance and references.
- **Content:** This exact guide, minus the installation steps.

### 11. MCP (Model Context Protocol) Server
- **Objectives:** Allow autonomous agents (like Hermes or Claude Desktop) to connect directly to the LiteLLM Helper via standard MCP protocols. The agent can monitor models, discover new free-tier models, safely delete deprecated ones, and update the Hermes Virtual Model bindings.
- **Content:** An `mcp_server.py` script that acts as a bridge between the agent and the Helper UI database.
- **Steps:** 
  1. Add the MCP server configuration to your agent's config file (e.g., `claude_desktop_config.json`), pointing the `command` to the helper's `venv/bin/python` and `args` to `mcp_server.py`.
  2. Set the `MCP_API_KEY` environment variable in the agent's config to match your `LITELLM_HELPER_PASSWORD` for security.
- **Tips/Hints:** We recommend instructing your agent to run periodically (e.g., using a cron job) to clean up deprecated models and pull in newly discovered ones. The agent will automatically snapshot the configuration version before making any modifications!
- **Errors/Warnings:** **Security Warning:** The MCP server operates locally over `stdio`, meaning it does not open network ports. However, because it has database write access, only connect trusted agent environments to this server.

### 12. Keys Monitor
- **Objectives:** Monitor the daily and monthly API usage of your registered keys per provider.
- **Content:** Interactive grid displaying rate limit metrics, progress bars, and dynamic summaries.
- **Steps:** Navigate to the Keys Monitor interface, select a Provider, and optionally select a Model. The view will automatically fetch live usage data from Redis.
- **Tips/Hints:** Rate limits are displayed contextually. You can sort keys by highest or lowest usage to easily identify which keys are close to hitting their limits.

### 13. Notifications
- **Objectives:** Receive automated emails about newly discovered free-tier models, deprecated models, and daily summaries of API usage.
- **Content:** Automated email reports powered by background job schedulers (`APScheduler`).
- **Steps:** Ensure you have configured the SMTP variables (like `SMTP_SERVER` and `SMTP_PASSWORD`) in your `.env` file. 
- **Tips/Hints:** If using Gmail, you must use a Google "App Password" rather than your standard account password. The jobs run automatically in the background (Usage runs daily at 11:50 PM, Model Discovery runs Mon/Thu at 9:00 AM).
## 🤝 Contributing
Contributions, issues, and feature requests are welcome! 
Feel free to check out the [Issues page](https://github.com/medzarka/LiteLLM-helper/issues).

## 📝 Credits
Developed and maintained by [Mohamed Zarka](https://github.com/medzarka) (@medzarka).
For inquiries, reach out at `medzarka@gmail.com`.
