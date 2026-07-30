
# Role

Act as a senior developer in python-based flask web application.

# Tasks

You task is to throughfully follow the following instructions to develop a personal web based application.
This application will be used to manage AI providers, API keys and the AI models.
Then, the application could generate a ready configuration for litellm.
The configuration includes also how litellm will handle key rotation through selecting the strategy and through models aggregation.

# Description

The application has only one admin user that can manage the application settings and content. The admin password of this application will be provided as an environment variable. The application uses SQLite as its database, with the database file `litellm_helper.db` stored in the `data` directory.

--- 

## Managing providers

As a first interface, the application will provide CRUD (Create, Read, Update, Delete) operations for managing the AI providers, including optional `api_base`. It includes also a textfield to insert information on that provider.

--- 

## Managing AI API calls Keys

Then, a nother interface is used to handle API keys. Each API key is associated with a provider. The key has also a field to store the key value, and also a field to store the key name (ex. the refered email used to create the key). The keys interface will be also a CRUD interface with organized keys with their providers.

--- 

## Managing AI Models

After, another interface is used to handle the AI models. Each model is associated with a provider. The model has also a field to store the model name, and also a field to store the model type (ex. `gpt-4`). The models interface will be also a CRUD interface with organized models with their providers. The model will handle also the rate limits : for the tocken limits (TPM : tocken per minute, TPD : tocken per day, TPM : tocken per month) and also for requests limits (RPM : request per minute, RPD : request per day, RPM : request per month).

**Rate limits management:**

| Column | Type | Default | Meaning |
|--------|------|---------|---------|
| `rpm_limit` | Integer | 30 | Requests per minute |
| `tpm_limit` | Integer | 6000 | Tokens per minute |
| `rpd_limit` | Integer | 0 | Requests per day (0 = unlimited) |
| `tpd_limit` | Integer | 0 | Tokens per day (0 = unlimited) |
| `rpm_month_limit` | Integer | 0 | Requests per month (0 = unlimited) |
| `tpm_month_limit` | Integer | 0 | Tokens per month (0 = unlimited) |

All limits are **optional** – a value of `0` signals "no limit" and the field is omitted from the exported YAML.

**Automatic Model‑Key Mapping (Export‑Time)**

During config generation (see later):

```
For each Model in DB:
  - Find all ACTIVE APIKeys belonging to provider
  - For each Key:
    - Create a ModelKeyMapping entry (conceptual; stored in the export only)
    - Use the shared_model_name as the LiteLLM model_name
```

**Example**
```
Provider: groq
Keys: [key-1, key-2, key-3] (all active)
Model: actual_model="openai/gpt-oss-120b", shared_model_name="shared-gpt-oss-120b"

Result in exported model_list:
- model_name: shared-gpt-oss-120b   (using key-1)
- model_name: shared-gpt-oss-120b   (using key-2)
- model_name: shared-gpt-oss-120b   (using key-3)
```

**Manual Overrides (Optional)**

- Users can manually create ModelKey Mapping entries via the UI to override the automatic behavior.
- Useful for cross‑provider models or when a specific key should be excluded.

--- 

## Managing Model Aggregation

Then, another interface is used to handle the model aggregation. Indeed, since I am using litellm with free tier models, I will provide many keys for a given model in order to overcome the rate limits. Besides, some models are common across providers (ex. `gpt-oss-120B`), so I create a new aggregated model that is common across at least two providers, and then I will provide a single model entry for this aggregated model. So, this will help to further overcome the rate limits for these common models by providing more keys for one aggregated model.

During export each such model produces a separate entry under that name, giving LiteLLM multiple keys to rotate among.

**Cross‑Provider Aggregation Example**

```
Provider: groq
  Model: actual_model="openai/gpt-oss-120b", shared_model_name="shared-gpt-oss-120b"
  Keys: [key-1, key-2, key-3]

Provider: openrouter
  Model: actual_model="openai/gpt-oss-120b", shared_model_name="shared-gpt-oss-120b"
  Keys: [key-4, key-5]
```

**Result in model_list** (each entry gets its own provider, key, and model‑specific limits):
```yaml
- model_name: shared-gpt-oss-120b
  litellm_params:
    model: groq/openai/gpt-oss-120b
    api_key: <key-1>
    custom_llm_provider: groq
    rpm: 30
    tpm: 6000
    # ... other limits from the groq model

- model_name: shared-gpt-oss-120b
  litellm_params:
    model: openrouter/openai/gpt-oss-120b
    api_key: <key-4>
    custom_llm_provider: openrouter
    api_base: https://openrouter.ai/api/v1
    rpm: 60
    tpm: 12000
    # ... other limits from the openrouter model
```

**Aggregated Models Management Interface**

To manage models that share the same `shared_model_name` the UI provides:

- **Aggregated Models List Page** – displays all models grouped by `shared_model_name`.
- **Edit Aggregated Model Modal** – allows users to:
  - View/modify the `shared_model_name` (changing it merges or creates a new aggregation)
  - Edit provider‑specific model details (`actual_model`, `timeout`, `max_retries`, `mode`, `skills`, rate limits)
  - See which keys from the provider are available for this model
  - Add/remove the model from the aggregation (does not delete the underlying model record)
- **Aggregated Model Card** – shows:
  - Shared model name (e.g., `shared-gpt-oss-120b`)
  - Number of models in the aggregation
  - Number of distinct providers involved
  - Summary totals of RPM/TPM across all keys (optional)
  - Skill badges collected from any model in the aggregation


---

## Configuring Key Selection

After, another interface is used to configure how the LiteLLM proxy chooses among multiple keys for the same model at request time. This is **separate** from the export‑time automatic mapping.

The proxy supports four strategies, configured via the RouterSettings entity:

| Strategy | Description |
|----------|-------------|
| **Least Requests (default)** | Selects the API key with the lowest recent request count. |
| **Round Robin** | Rotates through keys in order based on last‑used time. |
| **Random** | Selects a random key from the available pool. |
| **Weighted (by RPM/TPM limits)** | Chooses keys weighted by their rate‑limit values (higher limits = higher probability). |


Additional Rotation Settings:

- **Routing Strategy** – chooses how the proxy selects among multiple models (simple‑shuffle, latency‑based, cost‑based).  
- **Cooldown Time (seconds)** – minimum time between rotation attempts for a given key.  
- **Allowed Fails** – number of consecutive failed requests before a key is considered unhealthy.  
- **Num Retries** – total number of retry attempts before giving up on a request.

**Important**: Key rotation works **only** when multiple keys exist for the same `shared_model_name` (as produced by the aggregation feature). The export step creates those multiple entries; the rotation step chooses among them at runtime.

---

## Export to YAML (litellm config)

Finally, the application will provide an option to export the litellm configuration to a YAML file. The application will generate then a litellm compatible config file that can be used to run the litellm proxy. The config file will include all the routing, key rotation, and litellm settings configured in the application. 

**Export Example (With Aggregation & Key Rotation Settings)**

```yaml
# === Top‑level sections (router, general, litellm) ===
router_settings:
  routing_strategy: simple-shuffle
  key_rotation_strategy: least-requests   # from RouterSettings
  cooldown_time: 30
  allowed_fails: 2
  num_retries: 2

general_settings:
  background_health_checks: true
  health_check_interval: 1800

litellm_settings:
  drop_params: true
  cache: false

# === model_list ===
model_list:
# Aggregated model: groq keys (shared-gpt-oss-120b)
- model_name: shared-gpt-oss-120b
  litellm_params:
    model: groq/openai/gpt-oss-120b
    api_key: gsk_xxx1
    custom_llm_provider: groq
    rpm: 30
    tpm: 6000
    rpd: 10000
    tpd: 500000
    timeout: 15
    max_retries: 2
    model_info:
      mode: chat
      supports_function_calling: true
      skills: [reasoning, coding]

- model_name: shared-gpt-oss-120b
  litellm_params:
    model: groq/openai/gpt-oss-120b
    api_key: gsk_xxx2s
    custom_llm_provider: groq
    rpm: 30
    tpm: 6000
    rpd: 10000
    tpd: 500000
    timeout: 15
    max_retries: 2
    model_info:
      mode: chat
      supports_function_calling: true
      skills: [reasoning, coding]

# Aggregated model: openrouter key
- model_name: shared-gpt-oss-120b
  litellm_params:
    model: openrouter/openai/gpt-oss-120b
    api_key: sk-or-xxx
    custom_llm_provider: openrouter
    api_base: https://openrouter.ai/api/v1   # from Provider.base_url
    rpm: 60
    tpm: 12000
    rpd: 20000
    tpd: 1000000
    timeout: 15
    max_retries: 2
    model_info:
      mode: chat
      supports_function_calling: true
      skills: [reasoning]

# Single‑provider model (no aggregation)
- model_name: llama-4-scout
  litellm_params:
    model: groq/meta-llama/llama-4-scout-17b-16e-instruct
    api_key: gsk_xxx1
    custom_llm_provider: groq
    rpm: 30
    tpm: 6000
    rpd: 0          # unlimited per day
    tpd: 0          # unlimited per day
    rpm_month_limit: 0
    tpm_month_limit: 0
    timeout: 15
    max_retries: 2
    model_info:
      mode: chat
      supports_function_calling: false
      skills: []
```

---


## Application Contraints

- the application will be easy to use and configure
- the application will have well organized and documented code
- the application will be deployed on a docker-based platform. So we can run it locally or on docker (both are possible).
- the application will use sqlite as the database
