import os
import json
import yaml
try:
    from ..models.models import Database, Provider as DBProvider, APIKey, AIModel, ModelFallback
except (ImportError, ValueError):
    from models.models import Database, Provider as DBProvider, APIKey, AIModel, ModelFallback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _normalize_provider_for_litellm(provider_name):
    raw = (provider_name or '').strip()
    key = raw.lower().replace(' ', '').replace('-', '').replace('_', '')
    provider_map = {
        'groq': 'groq',
        'openrouter': 'openrouter',
        'google': 'gemini',
        'googleai': 'gemini',
        'googleai_studio': 'gemini',
        'gemini': 'gemini',
        'cerebras': 'cerebras',
        'celerbras': 'cerebras',
        'mistral': 'mistral',
        'cohere': 'cohere',
        'huggingface': 'huggingface',
        'zai': 'zai',
        'openai': 'openai',
        'anthropic': 'anthropic',
    }
    return provider_map.get(key, raw.lower())


def _normalize_model_name(actual_model, provider_name):
    model_name = (actual_model or '').strip()
    if not model_name:
        return model_name

    normalized_provider = _normalize_provider_for_litellm(provider_name)
    provider = (provider_name or '').strip().lower()

    # Older records may have provider prefixes like "Groq/..." or "google/...".
    for prefix in (provider + '/', normalized_provider + '/', 'google/', 'googleai/', 'google_ai/', 'gemini/'):
        if prefix != '/' and model_name.lower().startswith(prefix):
            model_name = model_name[len(prefix):]
            break

    if normalized_provider:
        return f'{normalized_provider}/{model_name}'

    return model_name


def _build_router_settings_for_litellm():
    settings = load_rotation_settings()
    return {
        'routing_strategy': settings.get('routing_strategy', 'simple-shuffle'),
        'cooldown_time': int(settings.get('cooldown_time', 60)),
        'allowed_fails': int(settings.get('allowed_fails', 2)),
        'num_retries': int(settings.get('num_retries', 1)),
    }


def _load_json_file(path, default):
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            if data is not None:
                return data
        except Exception:
            pass
    return default


def load_rotation_settings():
    defaults = {
        'routing_strategy': 'simple-shuffle',
        'key_rotation_strategy': 'round-robin',
        'cooldown_time': 60,
        'allowed_fails': 2,
        'num_retries': 1,
    }
    data = _load_json_file('rotation_settings.json', {})
    if isinstance(data, dict):
        for k, v in data.items():
            if k in defaults:
                defaults[k] = v
    return defaults


def load_aggregations():
    data = _load_json_file('aggregations.json', [])
    return data if isinstance(data, list) else []


def load_fallbacks():
    """Load fallbacks from SQLite database with fallback to fallbacks.json"""
    try:
        db = Database()
        fb_list = ModelFallback(db).get_all()
        db.close()
        if fb_list:
            return fb_list
    except Exception:
        pass
    data = _load_json_file('fallbacks.json', [])
    return data if isinstance(data, list) else []


def generate_config(
    include_router=True,
    include_general=True,
    include_litellm=True,
    include_individual=False,
    include_health_checks=False,
    include_fallbacks=True,
    include_aggregations=True,
    exclude_unhealthy=False
):
    """Generate a complete LiteLLM configuration based on v3 system description"""
    db = Database()
    provider_obj = DBProvider(db)
    key_obj = APIKey(db)
    model_obj = AIModel(db)
    
    try:
        from .hermes import load_hermes_agents
    except (ImportError, ValueError):
        from services.hermes import load_hermes_agents
        
    hermes_agents = load_hermes_agents()
    model_id_to_hermes_tasks = {}
    hermes_fallbacks = {}
    
    for h_task, mapping in hermes_agents.items():
        if isinstance(mapping, dict):
            p_id = mapping.get('primary')
            f_id = mapping.get('fallback')
        else:
            p_id = mapping
            f_id = None
            
        if p_id:
            if p_id not in model_id_to_hermes_tasks:
                model_id_to_hermes_tasks[p_id] = []
            model_id_to_hermes_tasks[p_id].append(h_task)
            
        if f_id:
            if f_id not in model_id_to_hermes_tasks:
                model_id_to_hermes_tasks[f_id] = []
            model_id_to_hermes_tasks[f_id].append(f"{h_task}-fallback")
            hermes_fallbacks[h_task] = f"{h_task}-fallback"

    config = {
        'model_list': []
    }
    
    if include_router:
        # Reflect the user's saved key-rotation configuration
        config['router_settings'] = _build_router_settings_for_litellm()
        if include_fallbacks:
            fallbacks = load_fallbacks()
            fb_format = []
            if fallbacks:
                for fb in fallbacks:
                    p_model = fb.get('primary_model')
                    f_models = fb.get('fallback_models', [])
                    if p_model and f_models:
                        fb_format.append({p_model: f_models})
                        
            # Inject Hermes native fallbacks
            for h_primary, h_fallback in hermes_fallbacks.items():
                fb_format.append({h_primary: [h_fallback]})
                
            if fb_format:
                config['router_settings']['fallbacks'] = fb_format

    if include_general:
        config['general_settings'] = {
            'background_health_checks': include_health_checks,
            'health_check_interval': 1800
        }

    if include_litellm:
        config['litellm_settings'] = {
            'drop_params': True,
            'cache': False
        }

    # Map model id -> shared aggregation name (user-defined merge / rename).
    id_to_shared = {}
    if include_aggregations:
        overrides = load_aggregations()
        for ov in overrides:
            for mid in ov.get('model_ids', []):
                id_to_shared[mid] = ov['shared_name']

    # Whitelist of valid top-level litellm_params recognized by LiteLLM Proxy
    ALLOWED_LITELLM_PARAMS = {
        'model', 'api_key', 'api_base', 'custom_llm_provider',
        'rpm', 'tpm', 'timeout', 'stream_timeout', 'max_retries',
        'model_info', 'organization', 'api_version', 'drop_params',
        'stop', 'temperature'
    }


    # Get all models with provider info
    providers = provider_obj.get_all()

    def _build_model_entry(model, provider, key, name, is_hermes=False):
        normalized_provider = _normalize_provider_for_litellm(model.get('provider_name'))
        normalized_model = _normalize_model_name(model.get('actual_model', ''), model.get('provider_name'))
        m_type = (model.get('model_type') or '').lower().strip()
        m_full = f"{model.get('name', '')} {model.get('actual_model', '')}".lower()

        if m_type == 'embedding' or 'embed' in m_full or 'gecko' in m_full:
            mode = 'embedding'
        elif m_type in ('stt', 'audio_transcription', 'whisper') or 'whisper' in m_full or 'transcribe' in m_full:
            mode = 'transcription'
        elif m_type in ('tts', 'audio_speech') or 'tts' in m_full or 'speech' in m_full:
            mode = 'audio_speech'
        elif m_type == 'rerank' or 'rerank' in m_full:
            mode = 'rerank'
        else:
            mode = 'chat'

        entry = {
            'model_name': name,
            'litellm_params': {
                'model': normalized_model,
                'api_key': key['key_value'],
                'custom_llm_provider': normalized_provider,
                'timeout': model.get('timeout', 60.0),
                'stream_timeout': model.get('stream_timeout', 20.0),
                'max_retries': model.get('max_retries', 2),
                'model_info': {
                    'mode': mode,
                    'supports_function_calling': bool(model.get('supports_function_calling', True)),
                    'skills': model.get('skills', [])
                }
            }
        }
        if model.get('max_input_tokens') and model['max_input_tokens'] > 0:
            entry['litellm_params']['model_info']['max_input_tokens'] = model['max_input_tokens']
        if model.get('rpd_limit') and model['rpd_limit'] > 0:
            entry['litellm_params']['model_info']['rpd_limit'] = model['rpd_limit']
        if model.get('tpd_limit') and model['tpd_limit'] > 0:
            entry['litellm_params']['model_info']['tpd_limit'] = model['tpd_limit']

        if bool(model.get('supports_function_calling', True)) or 'function_calling' in [s.lower() for s in model.get('skills', [])]:
            entry['litellm_params']['drop_params'] = True

        if model.get('rpm_limit') and model['rpm_limit'] > 0:
            entry['litellm_params']['rpm'] = model['rpm_limit']
        if model.get('tpm_limit') and model['tpm_limit'] > 0:
            entry['litellm_params']['tpm'] = model['tpm_limit']
        if provider.get('api_base'):
            entry['litellm_params']['api_base'] = provider['api_base']

        if is_hermes:
            entry['litellm_params']['stop'] = [
                "<|im_end|>",
                "<|tool_call>",
                "<|im_start|>",
                "<|\"|>"
            ]
            entry['litellm_params']['temperature'] = 0.1

        # Ensure top-level litellm_params strictly contains only LiteLLM recognized keys
        top_level_keys = list(entry['litellm_params'].keys())
        for k in top_level_keys:
            if k not in ALLOWED_LITELLM_PARAMS:
                entry['litellm_params']['model_info'][k] = entry['litellm_params'].pop(k)

        return entry

    for provider in providers:
        models = model_obj.get_by_provider(provider['name'])
        for model in models:
            if exclude_unhealthy and model.get('last_status') in ('not_found', 'error'):
                continue

            shared_name = id_to_shared.get(model['id'], model['name'])
            is_aggregated = model['id'] in id_to_shared
            keys = key_obj.get_by_provider(provider['id'])
            active_keys = [k for k in keys if k['is_active']]

            for key in active_keys:
                # 1. Always emit the (possibly shared/aggregated) entry.
                config['model_list'].append(_build_model_entry(model, provider, key, shared_name))
                # 2. Optionally ALSO emit the individual provider-specific entry so the
                #    user can route to the groq-only or openrouter-only variant directly.
                if include_individual and is_aggregated and include_aggregations:
                    config['model_list'].append(_build_model_entry(model, provider, key, model['name']))
                    
                # 3. Emit Hermes virtual aliases
                if model['id'] in model_id_to_hermes_tasks:
                    for h_task in model_id_to_hermes_tasks[model['id']]:
                        config['model_list'].append(_build_model_entry(model, provider, key, h_task, is_hermes=True))

    db.close()
    return config


def validate_config_non_empty(config):
    model_list = config.get('model_list', []) if isinstance(config, dict) else []
    if not model_list:
        raise ValueError(
            'Generated config has no model deployments. Add at least one active key to a model before exporting.'
        )

def sync_to_shared_volume(config_dict, format_type='yaml'):
    """
    Writes the config file to the shared Docker volume so that the running LiteLLM
    container can hot-reload it. Then performs a health check on LiteLLM.
    """
    # 1. Determine shared volume path
    # When running inside docker-compose, we mapped ./shared-config to /app/shared
    docker_shared_path = '/app/shared'
    local_shared_path = os.path.join(BASE_DIR, '..', 'shared-config')
    
    if os.path.exists(docker_shared_path) and os.path.isdir(docker_shared_path):
        target_dir = docker_shared_path
    else:
        # Fallback to local path for dev testing
        target_dir = local_shared_path
        os.makedirs(target_dir, exist_ok=True)
        
    filename = f'litellm-config.{format_type}'
    filepath = os.path.join(target_dir, 'config.yaml') # The docker-compose expects config.yaml regardless of the export format type
    
    # 2. Write the file
    try:
        with open(filepath, 'w') as f:
            if format_type == 'yaml':
                yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
            else:
                json.dump(config_dict, f, indent=2)
    except Exception as e:
        return {'success': False, 'error': f'Failed to write to shared volume: {str(e)}'}
        
    # 3. Ping LiteLLM container to ensure it's alive and processing
    import urllib.request
    import time
    
    time.sleep(1) # Give LiteLLM a moment to detect the file change via --watch
    
    # In docker-compose, the container is named `litellm` and exposes port 4000
    # Use litellm:4000 or fallback to localhost:4000
    urls_to_test = ['http://litellm:4000/health', 'http://127.0.0.1:4000/health']
    health_status = 'Unverified'
    
    master_key = os.environ.get('LITELLM_MASTER_KEY')
    for url in urls_to_test:
        try:
            req = urllib.request.Request(url)
            if master_key:
                req.add_header('Authorization', f'Bearer {master_key}')
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    health_status = 'Healthy'
                    break
        except Exception:
            pass
            
    return {
        'success': True, 
        'path': filepath,
        'litellm_health': health_status
    }
