import os
import sys
import json
import sqlite3
import time
import datetime
import requests
from dotenv import dotenv_values
from mcp.server import Server, NotificationOptions
from mcp.server.stdio import stdio_server
import mcp.types as types

# --- Security Verification ---
# Load the local .env configuration but DO NOT pollute os.environ if it's already set
base_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(base_dir, ".env")
env_config = dotenv_values(env_path)
expected_pwd = env_config.get("LITELLM_HELPER_PASSWORD")

if expected_pwd:
    provided_pwd = os.environ.get("MCP_API_KEY") or os.environ.get("LITELLM_HELPER_PASSWORD")
    if provided_pwd != expected_pwd:
        print("Unauthorized: Missing or incorrect MCP_API_KEY/LITELLM_HELPER_PASSWORD environment variable in the agent's MCP execution config.", file=sys.stderr)
        sys.exit(1)

app = Server("litellm-helper-mcp")

# Resolve DB path similarly to models.py
def get_db_path():
    return os.path.join(base_dir, 'data', 'litellm_helper.db')

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def get_db():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = dict_factory
    return conn

def snapshot_configuration():
    # Helper to create a version backup before destructive actions
    try:
        from services.versions import save_version
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_version(f"MCP_Auto_Backup_{timestamp.replace(' ', '_').replace(':', '-')}", "Automatic backup created by MCP Agent before modification.")
    except Exception as e:
        print(f"Failed to create version snapshot: {e}", file=sys.stderr)

@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List available tools for managing LiteLLM Helper."""
    return [
        types.Tool(
            name="list_providers",
            description="Returns a list of all configured model providers. (Read-only)",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        types.Tool(
            name="list_models",
            description="Returns a list of all configured models and their statistics. Use this to analyze context lengths, RPMs, and skills before choosing a model. (Read-only)",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        types.Tool(
            name="get_hermes_agents",
            description="Returns the current Hermes Virtual Models configuration mapping task IDs (e.g. hermes-default, hermes-vision) to their assigned model IDs.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        types.Tool(
            name="update_hermes_agent",
            description="Updates the primary model assigned to a specific Hermes Virtual Model task (e.g., hermes-default).",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The Hermes task ID (e.g., hermes-default, hermes-vision, hermes-auxiliary)"
                    },
                    "primary_model_id": {
                        "type": "integer",
                        "description": "The Database ID of the model to assign to this task."
                    }
                },
                "required": ["task_id", "primary_model_id"]
            }
        ),
        types.Tool(
            name="discover_free_models",
            description="Scans supported provider APIs for newly available models that are not yet installed in the database.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="get_deprecated_models",
            description="Checks installed models against provider APIs and returns a list of models that are no longer available upstream.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        types.Tool(
            name="import_model",
            description="Imports a newly discovered model into the database. You MUST provide exactly matching 'provider_type' and 'actual_model_id' from the discovery payload.",
            inputSchema={
                "type": "object",
                "properties": {
                    "provider_type": {"type": "string", "description": "e.g., 'groq', 'openrouter', 'gemini', 'mistral', 'ollama'"},
                    "actual_model_id": {"type": "string", "description": "The upstream ID of the model"},
                    "name": {"type": "string", "description": "A friendly display name"},
                    "context_length": {"type": "integer"},
                    "rpm_limit": {"type": "integer", "description": "Set 0 for unlimited"},
                    "tpm_limit": {"type": "integer", "description": "Set 0 for unlimited"},
                    "rpd_limit": {"type": "integer", "description": "Set 0 for unlimited"},
                    "supports_function_calling": {"type": "boolean"},
                    "skills": {"type": "string", "description": "Comma separated list of skills e.g., 'vision, math'"}
                },
                "required": ["provider_type", "actual_model_id", "name"]
            }
        ),
        types.Tool(
            name="delete_model",
            description="Deletes a model from the database by its ID. ONLY use this for models explicitly returned by get_deprecated_models.",
            inputSchema={
                "type": "object",
                "properties": {
                    "model_id": {"type": "integer"}
                },
                "required": ["model_id"]
            }
        ),
        types.Tool(
            name="wait_for_litellm_restart",
            description="Pings the LiteLLM proxy health endpoint in a loop until it returns 200 OK. Call this after updating fallbacks to wait for the proxy to reload.",
            inputSchema={
                "type": "object",
                "properties": {
                    "health_url": {
                        "type": "string",
                        "description": "The full health URL for litellm proxy (default: http://127.0.0.1:4000/health)",
                        "default": "http://127.0.0.1:4000/health"
                    },
                    "max_wait_seconds": {
                        "type": "integer",
                        "description": "Maximum seconds to wait. (default 300)",
                        "default": 300
                    }
                }
            }
        )
    ]

@app.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    
    if name == "list_providers":
        conn = get_db()
        providers = conn.execute("SELECT * FROM provider").fetchall()
        conn.close()
        return [types.TextContent(type="text", text=json.dumps(providers, indent=2))]

    elif name == "list_models":
        conn = get_db()
        models = conn.execute("SELECT * FROM model").fetchall()
        conn.close()
        return [types.TextContent(type="text", text=json.dumps(models, indent=2))]

    elif name == "get_hermes_agents":
        from services.hermes import load_hermes_agents
        current_agents = load_hermes_agents()
        return [types.TextContent(type="text", text=json.dumps(current_agents, indent=2))]

    elif name == "update_hermes_agent":
        if not arguments or "task_id" not in arguments or "primary_model_id" not in arguments:
            raise ValueError("Missing required arguments for update_hermes_agent")
        
        task_id = arguments["task_id"]
        model_id = arguments["primary_model_id"]
        
        try:
            snapshot_configuration()
            from services.hermes import load_hermes_agents, save_hermes_agents
            agents = load_hermes_agents()
            
            # Ensure it is a dictionary format
            if task_id not in agents or not isinstance(agents[task_id], dict):
                agents[task_id] = {}
                
            agents[task_id]["primary"] = int(model_id)
            save_hermes_agents(agents)
            
            # trigger live sync to export the YAML and reload LiteLLM
            try: requests.post("http://127.0.0.1:5001/export/sync-live")
            except Exception: pass

            return [types.TextContent(type="text", text=f"Successfully assigned model ID {model_id} to Hermes task '{task_id}'!")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error updating hermes agent: {str(e)}")]

    elif name == "discover_free_models":
        try:
            from services.discovery import discover_free_models
            models = discover_free_models()
            
            # Filter existing models
            conn = get_db()
            local_models = conn.execute('SELECT actual_model FROM model').fetchall()
            local_map = {row['actual_model'] for row in local_models}
            conn.close()
            
            for m in models:
                if m['id'] in local_map:
                    m['is_installed'] = True
                else:
                    m['is_installed'] = False
                    
            return [types.TextContent(type="text", text=json.dumps(models, indent=2))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Discovery error: {str(e)}")]

    elif name == "get_deprecated_models":
        try:
            from services.discovery import get_all_provider_models
            conn = get_db()
            
            # We need keys to pass to get_all_provider_models
            google_row = conn.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE (p.provider_type = 'google' OR p.provider_type = 'gemini') AND k.is_active = 1 LIMIT 1").fetchone()
            google_api_key = google_row['key_value'] if google_row else None
            
            mistral_row = conn.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE p.provider_type = 'mistral' AND k.is_active = 1 LIMIT 1").fetchone()
            mistral_api_key = mistral_row['key_value'] if mistral_row else None
            
            groq_row = conn.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE p.provider_type = 'groq' AND k.is_active = 1 LIMIT 1").fetchone()
            groq_api_key = groq_row['key_value'] if groq_row else None

            cohere_row = conn.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE p.provider_type = 'cohere' AND k.is_active = 1 LIMIT 1").fetchone()
            cohere_api_key = cohere_row['key_value'] if cohere_row else None
            
            provider_models = get_all_provider_models(google_api_key, mistral_api_key, groq_api_key, cohere_api_key)
            
            local_models = conn.execute('''
                SELECT m.id, m.name, m.actual_model, p.provider_type, p.name as provider_name
                FROM model m
                JOIN provider p ON m.provider_id = p.id
            ''').fetchall()
            conn.close()
            
            deprecated = []
            supported = ['gemini', 'mistral', 'openrouter', 'ollama', 'groq']
            
            for row in local_models:
                p_type = row['provider_type']
                actual_model = row['actual_model']
                if p_type in supported:
                    if len(provider_models.get(p_type, set())) > 0 and actual_model not in provider_models[p_type]:
                        deprecated.append(row)
                        
            return [types.TextContent(type="text", text=json.dumps(deprecated, indent=2))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Deprecation check error: {str(e)}")]

    elif name == "import_model":
        if not arguments or "provider_type" not in arguments or "actual_model_id" not in arguments or "name" not in arguments:
            raise ValueError("Missing required arguments for import_model")
            
        snapshot_configuration() # Save version before modifications
        
        provider_type = arguments["provider_type"]
        actual_model = arguments["actual_model_id"]
        model_name = arguments["name"]
        
        conn = get_db()
        # Find matching provider
        provider = conn.execute("SELECT id FROM provider WHERE provider_type = ? LIMIT 1", (provider_type,)).fetchone()
        if not provider:
            conn.close()
            return [types.TextContent(type="text", text=f"Error: No provider found matching type '{provider_type}'. You must create it in the UI first.")]
            
        provider_id = provider['id']
        
        try:
            conn.execute('''
                INSERT INTO model (
                    provider_id, name, actual_model, context_length,
                    rpm_limit, tpm_limit, rpd_limit, supports_function_calling, skills
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                provider_id, model_name, actual_model,
                arguments.get("context_length", 8192),
                arguments.get("rpm_limit", 0),
                arguments.get("tpm_limit", 0),
                arguments.get("rpd_limit", 0),
                1 if arguments.get("supports_function_calling", True) else 0,
                arguments.get("skills", "")
            ))
            conn.commit()
            
            # trigger live sync
            try: requests.post("http://127.0.0.1:5001/export/sync-live")
            except Exception: pass
            
            return [types.TextContent(type="text", text=f"Successfully imported {model_name} into database!")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Database error: {str(e)}")]
        finally:
            conn.close()

    elif name == "delete_model":
        if not arguments or "model_id" not in arguments:
            raise ValueError("Missing model_id")
            
        snapshot_configuration() # Save version before modifications
        
        model_id = arguments["model_id"]
        conn = get_db()
        try:
            conn.execute("DELETE FROM model WHERE id = ?", (model_id,))
            conn.commit()
            
            # trigger live sync
            try: requests.post("http://127.0.0.1:5001/export/sync-live")
            except Exception: pass
            
            return [types.TextContent(type="text", text=f"Successfully deleted model ID {model_id}!")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Database error: {str(e)}")]
        finally:
            conn.close()

    elif name == "wait_for_litellm_restart":
        url = arguments.get("health_url", "http://127.0.0.1:4000/health") if arguments else "http://127.0.0.1:4000/health"
        max_wait = arguments.get("max_wait_seconds", 300) if arguments else 300
        
        start_time = time.time()
        success = False
        while time.time() - start_time < max_wait:
            try:
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    success = True
                    break
            except requests.exceptions.RequestException:
                pass
            time.sleep(5) # check every 5 seconds
            
        if success:
            return [types.TextContent(type="text", text="LiteLLM proxy is healthy and restarted successfully!")]
        else:
            return [types.TextContent(type="text", text="Timeout waiting for LiteLLM to restart.")]

    else:
        raise ValueError(f"Unknown tool: {name}")

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
