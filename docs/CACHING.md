# Configuring Caching and Redis in LiteLLM

When deploying LiteLLM Proxy alongside agentic frameworks (like Hermes) using Free Tier APIs, caching and rate-limit tracking become survival necessities rather than optional optimizations.

LiteLLM supports two types of caching:
1. **In-Memory Caching (Default)**
2. **Redis Caching (Recommended for Multi-Worker / Usage-Based Routing)**

This guide explains how to properly set up Redis caching in your `docker-compose.yml` and configure LiteLLM to use it as both a cache and a central "brain" for rate limits.

---

## The Dual Purpose of Redis

When you enable Redis in LiteLLM, it serves two critical functions:

1. **Exact-Match Caching:** If an agent sends the exact same prompt multiple times (e.g., getting stuck in a tool-calling loop), LiteLLM serves the cached response instantly. This saves your precious free-tier tokens and prevents API bans.
2. **Global Rate Limit Tracking (`usage-based-routing-v2`):** By default, if you run LiteLLM with 4 workers (Gunicorn), each worker maintains its own isolated counter of how many requests a specific API key has made. This leads to severe rate limit violations (HTTP 429). Redis acts as the central brain, ensuring all 4 workers share the same RPM/TPM counters, making usage-based routing mathematically accurate.

---

## 1. Adding Redis to Docker Compose

Add the following block to your `docker-compose.yml` to spin up a lightweight, persistent Redis container:

```yaml
  redis:
    image: redis:alpine
    container_name: litellm-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    volumes:
      - ${WORKDIR}/redis_data:/data
    command: redis-server --save 60 1 --loglevel warning
```

Update your `litellm` service in the `docker-compose.yml` to inject the Redis environment variables:

```yaml
  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    container_name: litellm
    restart: unless-stopped
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - REDIS_PASSWORD=
      # Important for Agent stability (10 minute timeout)
      - SERVER_TIMEOUT=600 
      - LITELLM_WORKERS=4
```

---

## 2. Configuring config.yaml

When generating your `config.yaml`, ensure you add the `cache_params` and `redis_` settings manually or via your Helper UI if supported. A fully optimized configuration looks like this:

```yaml
router_settings:
  routing_strategy: usage-based-routing-v2
  num_retries: 2
  # Connects the Router to Redis for Rate Limit tracking across workers
  redis_host: "os.environ/REDIS_HOST"
  redis_port: "os.environ/REDIS_PORT"
  redis_password: "os.environ/REDIS_PASSWORD"

litellm_settings:
  cache: true
  cache_params:
    type: redis
    host: "os.environ/REDIS_HOST"
    port: "os.environ/REDIS_PORT"
    password: "os.environ/REDIS_PASSWORD"
```

---

## 💡 Hints, Tips, and Best Practices

### 1. The `SERVER_TIMEOUT` Lifeline
When running agents, they often stream massive contexts (hundreds of past tool calls). While LiteLLM might be configured to wait 600 seconds, the underlying Python server (Uvicorn) kills connections after 60 seconds by default. You **must** pass `-e SERVER_TIMEOUT=600` to the LiteLLM container to prevent 502 Bad Gateway errors during deep reasoning tasks.

### 2. Semantic vs. Exact Match Caching
By default, LiteLLM caching relies on **Exact Matches**. If a timestamp or random seed changes in your prompt by a single character, it's a cache miss. 
*Warning:* Do not enable `type: semantic` caching unless you have a dedicated embedding model configured, as semantic caching requires embedding every incoming prompt, which can actually cost *more* money/rate-limits than the generation itself. Stick to Exact Match for free-tier optimization.

### 3. Eviction Policies
If your agents generate massive payloads, your Redis RAM usage will grow. The provided docker-compose snippet uses `redis:alpine` with default eviction. If you run out of memory, add `--maxmemory 500mb --maxmemory-policy allkeys-lru` to your Redis command.

### 4. Bypassing Cache for Specific Calls
If you want to force an agent to ignore the cache and generate a fresh response, pass `{"ttl": 0}` in the `litellm_kwargs` or metadata of your API request payload from your client application.
