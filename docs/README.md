# LiteLLM Helper v3

A web-based configuration generator for LiteLLM proxy, designed to manage providers, API keys, models, key rotation, and aggregated model configurations.

## Features

- **Provider Management** - Add/remove LLM providers (Groq, OpenRouter, etc.)
- **API Key Management** - Store and track multiple keys per provider
- **Model Configuration** - Define models with rate limits, skills, and feature flags
- **Key Rotation** - Configure and test round-robin key rotation across providers
- **Aggregated Models** - Pool multiple provider models under a shared model name for load balancing
- **Model Health Check** - Probe your models to verify they're still available (optional, requires litellm)
- **Version Snapshots** - Save/restore full configuration states for rollback
- **Config Export** - Generate valid LiteLLM `config.yaml/json` with all your settings

## Prerequisites

- Python 3.9+
- pip (Python package manager)

## Installation

### 1. Clone / Navigate to the Project

```bash
cd /path/to/litellm  # or wherever you've placed this project
```

### 2. Create a Virtual Environment (Recommended)

```bash
# Create a virtual environment in the v3 directory
cd litellm_helper/v3
python3 -m venv venv

# Activate it:
# On macOS/Linux:
source venv/bin/activate

# On Windows:
# .\venv\Scripts\activate
```

### 3. Install Dependencies

```bash
# Core dependencies (always required)
pip install -r requirements.txt

# Optional: For live model health checks, install litellm:
# This may require Rust/Cargo on some systems. If you see build errors,
# skip this step - the app will work but health checks won't run.
pip install litellm>=1.70.0
```

### 4. Database Setup

The application uses SQLite as its database. The database file `litellm_helper.db` will be automatically created in the `data` directory when you first run the application.

### 4. Configure Password Protection

The app requires a password to run (fail-closed security model):

```bash
# Copy the example environment file
cp .env.example .env

# Edit it and set your password:
echo "LITELLM_HELPER_PASSWORD=your-strong-password" > .env
```

Or set it directly in your shell:
```bash
export LITELLM_HELPER_PASSWORD=your-strong-password
```

## Running the Application

```bash
# Make sure virtual environment is activated
source venv/bin/activate

# Run the Flask development server (port 5001)
python3 run.py
```

Or with password inline:
```bash
LITELLM_HELPER_PASSWORD=yourpassword python3 run.py
```

## Docker Deployment

### 1. Build and Run with Docker

```bash
# Build the Docker image
cd litellm_helper/v3
docker build -t litellm-helper .

# Run the container (replace 'yourpassword' with your actual password)
LITELLM_HELPER_PASSWORD=yourpassword docker run -d -p 5001:5001 -v $(pwd)/data:/app/data --name litellm-helper --restart unless-stopped litellm-helper
```

### 2. Using Docker Compose (Recommended)

```bash
# Set your password as an environment variable
export LITELLM_HELPER_PASSWORD=yourpassword

# Start the service
cd litellm_helper/v3
docker-compose up -d
```

The app will be available at http://localhost:5001

**Important**: If the server fails to start with "Address already in use", you can stop and remove the existing container:
```bash
docker stop litellm-helper
docker rm litellm-helper
```

## Usage Guide

### 1. Add Providers (optional)

Go to **Providers** → Click "Add Provider" → Fill in:
- Name: Provider slug (e.g., `groq`, `openrouter`)
- API Base: Custom endpoint (optional; defaults to provider's standard URL)

### 2. Add API Keys

Go to **API Keys** → Select a provider → Add keys:
- Key Name: Identifier for your reference
- Key Value: The actual API key
- Active: Whether this key should be used in rotation

### 3. Add Models

Go to **Models** → Click "Add Model":
- Provider: Which provider this model belongs to
- Name: Model identifier for your internal use (e.g., `groq-gpt-oss-120B`)
- Actual Model: Provider-specific model string
- RPM/TPM: Rate limits
- Skills: Comma-separated (e.g., `coding, math, reasoning`)

### 4. Create Aggregated Models

Go to **Aggregated Models** → Click "Merge Models":
- Shared Model Name: The unified name (e.g., `shared-gpt-oss-120B`)
- Select Models: Check which provider models to pool under this name

This creates a load-balanced endpoint that cycles through all selected models' keys.

### 5. Configure Key Rotation (optional)

Go to **Key Rotation** → Set:
- Routing Strategy: How keys are distributed
- Key Rotation Strategy: How keys are rotated on failure
- Cooldown / Retries: Failure handling

### 6. Export Configuration

Go to **Export Config** → Choose:
- Format: YAML or JSON
- "Also export individual provider models": Include non-aggregated models alongside shared ones
- Click "Generate Config" → Download the file

Use this file as `config.yaml` for your LiteLLM proxy.

### 7. Model Health Check (optional)

Go to **Model Health** → Click "Run Health Check":
- Tests each model with a minimal API call
- Shows `OK`, `Not Found`, `Error`, or `Skipped` status
- **Note**: Requires `litellm` package. Without it, all models show "Skipped".

### 8. Version Snapshots

Go to **Versions** → Save current state before major changes:
- Enter a name (e.g., "before-update")
- Add optional description
- Click "Save Version"

To rollback: Find the version in the list → Click "Restore".

## Database

- SQLite database file: `litellm_helper.db` (created automatically)
- Aggregation config: `aggregations.json`
- Key rotation settings: `rotation_settings.json`
- Version snapshots: `versions/*.json`

These files are in `.gitignore` - they persist locally but aren't committed.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `Database is not defined` on create aggregation | Fixed in this version |
| "Port 5001 in use" | Run `lsof -ti :5001 \| xargs -r kill -9` |
| Password not accepted | Check `.env` file location and `LITELLM_HELPER_PASSWORD` value |
| Health check shows all "Skipped" | Install litellm: `pip install litellm` (may fail on systems without Rust) |
| Aggregation not in exported config | Make sure you're using the Merge modal (which uses `model_ids`, not names) |

## Architecture

```
litellm_helper/v3/
├── run.py                    # Entry point
├── app.py                    # Flask routes/views
├── requirements.txt          # Python dependencies
├── .env.example              # Template for password config
├── models/
│   └── models.py             # Database layer (SQLite)
├── services/
│   ├── providers.py          # Provider CRUD API
│   ├── keys.py               # Key CRUD API
│   ├── models.py             # Model CRUD API
│   ├── export.py             # Config generation
│   ├── health_check.py       # Optional model testing
│   └── versions.py           # Snapshot management
└── templates/
    ├── base.html             # Layout with nav
    ├── providers.html
    ├── keys.html
    ├── models.html
    ├── aggregated_models.html
    ├── key_rotation.html
    ├── export_config.html
    ├── model_health.html       # Health check page
    └── versions.html           # Version management
```

## License

MIT – see project root for full license.