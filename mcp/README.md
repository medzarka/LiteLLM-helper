# MCP Server Instructions for Agentic Models

The LiteLLM Helper v4 application includes a native Model Context Protocol (MCP) server that runs over `stdio`. This allows autonomous agentic models (like Claude, Hermes, or any other MCP-compatible client) to securely manage the LiteLLM proxy configuration.

## Agent Capabilities
Once connected, your agent will have access to the following tools:
1. **list_providers**: Read the currently configured providers (e.g., Groq, OpenRouter).
2. **list_models**: Analyze the context length, speed (RPM/TPM limits), and skills of the currently installed models.
3. **discover_free_models**: Poll external provider APIs to find new models that aren't in the database yet.
4. **get_deprecated_models**: Cross-reference installed models with the external APIs to find dead or deprecated models.
5. **import_model**: Add a newly discovered model to the database.
6. **delete_model**: Remove deprecated models from the database.
7. **get_hermes_agents**: See what models are currently assigned to the Hermes Virtual Model interfaces.
8. **update_hermes_agent**: Assign the best available model (by ID) to a specific Hermes task (e.g., `hermes-vision`).
9. **wait_for_litellm_restart**: Pings the LiteLLM proxy until it restarts after an export.

*Note: The MCP server automatically creates a configuration version snapshot before allowing any destructive actions (like import/delete).*

## Connecting Your Agent

Because the MCP server runs locally over standard input/output (stdio), you must configure your agent to execute the python script locally.

### Step 1: Locate the Python Environment
Ensure you point the agent to the python executable within the application's virtual environment to guarantee all dependencies (like `mcp` and `flask`) are available.
- **Python Path:** `/absolute/path/to/litellm_helper/v3/venv/bin/python`
- **Script Path:** `/absolute/path/to/litellm_helper/v3/mcp_server.py`

### Step 2: Configure the Agent Client (e.g., Claude Desktop)
Add the following to your agent's MCP configuration JSON file:

```json
{
  "mcpServers": {
    "litellm-helper": {
      "command": "/Users/mzarka/mycloud/Homelab/litellm/litellm_helper/v3/venv/bin/python",
      "args": [
        "/Users/mzarka/mycloud/Homelab/litellm/litellm_helper/v3/mcp_server.py"
      ],
      "env": {
        "MCP_API_KEY": "YOUR_LITELLM_HELPER_PASSWORD"
      }
    }
  }
}
```

### Step 3: Security & Authentication
The `LITELLM_HELPER_PASSWORD` is defined in your `.env` file at the root of the LiteLLM Helper repository. 

For the MCP server to authorize the agent, the agent **must** pass the exact same password using the `MCP_API_KEY` environment variable (as shown in the JSON above). If the key is missing or incorrect, the MCP server will immediately shut down.

## Suggested Autonomous Workflows
You can prompt your agentic model to run periodic maintenance tasks:
> "Analyze my LiteLLM configuration using the MCP server. Discover if there are any new free models available from my configured providers, and if so, import them. Then, check if any models are deprecated and safely delete them. Finally, review the `hermes-agents` configuration and ensure the smartest reasoning model is assigned to `hermes-compression`, and wait for the proxy to restart."
