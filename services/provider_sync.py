import os
import json
import urllib.request
try:
    from ..models.models import Database, Provider as DBProvider, AIModel as DBAIModel, APIKey as DBAPIKey
    from .metadata_extractor import extract_model_metadata
except (ImportError, ValueError):
    from models.models import Database, Provider as DBProvider, AIModel as DBAIModel, APIKey as DBAPIKey
    from services.metadata_extractor import extract_model_metadata

_LITELLM_AVAILABLE = True
try:
    import litellm
except Exception:
    _LITELLM_AVAILABLE = False


def get_free_tier_catalog():
    """
    Generate a full catalog of providers offering free tier models and their free model lists,
    combining LiteLLM's internal model database and live OpenRouter catalog API.
    """
    catalog = {}

    # 1. Inspect LiteLLM internal model database if available
    if _LITELLM_AVAILABLE and hasattr(litellm, 'model_cost'):
        for model_name, info in litellm.model_cost.items():
            if not isinstance(info, dict):
                continue
            provider_raw = (info.get('litellm_provider') or 'other').strip().lower()
            
            # Format provider display name
            provider_display = provider_raw.replace('_', ' ').replace('-', ' ').title()
            if 'openrouter' in provider_raw:
                provider_display = 'OpenRouter'
            elif 'groq' in provider_raw:
                provider_display = 'Groq'
            elif 'gemini' in provider_raw or 'google' in provider_raw:
                provider_display = 'Google'
            elif 'mistral' in provider_raw:
                provider_display = 'Mistral'
            elif 'cohere' in provider_raw:
                provider_display = 'Cohere'
            elif 'ollama' in provider_raw:
                provider_display = 'Ollama'
            elif 'fireworks' in provider_raw:
                provider_display = 'Fireworks AI'
            elif 'together' in provider_raw:
                provider_display = 'Together AI'

            input_cost = float(info.get('input_cost_per_token') or 1.0)
            output_cost = float(info.get('output_cost_per_token') or 1.0)
            max_tokens = info.get('max_tokens') or info.get('max_input_tokens') or 32768

            # Identify free models
            if (input_cost == 0 and output_cost == 0) or ':free' in model_name or '-free' in model_name:
                if provider_display not in catalog:
                    catalog[provider_display] = []

                if not any(m['actual_model'] == model_name for m in catalog[provider_display]):
                    catalog[provider_display].append({
                        'name': model_name.split('/')[-1],
                        'actual_model': model_name,
                        'max_input_tokens': max_tokens,
                        'is_free': True,
                        'source': 'LiteLLM DB'
                    })

    # 2. Live OpenRouter Free Catalog
    try:
        url = 'https://openrouter.ai/api/v1/models'
        req = urllib.request.Request(url, headers={'User-Agent': 'LiteLLM-Helper/3.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode('utf-8'))
                or_models = payload.get('data', [])
                if 'OpenRouter' not in catalog:
                    catalog['OpenRouter'] = []

                for item in or_models:
                    m_id = item.get('id', '')
                    pricing = item.get('pricing', {})
                    p_cost = float(pricing.get('prompt') or 0)
                    c_cost = float(pricing.get('completion') or 0)
                    if m_id.endswith(':free') or (p_cost == 0 and c_cost == 0):
                        if not any(m['actual_model'] == m_id for m in catalog['OpenRouter']):
                            catalog['OpenRouter'].append({
                                'name': m_id.split('/')[-1],
                                'actual_model': m_id,
                                'max_input_tokens': item.get('context_length', 32768),
                                'is_free': True,
                                'source': 'OpenRouter Live API'
                            })
    except Exception:
        pass

    # Sort provider dictionary
    result = {}
    total_models = 0
    for p_name in sorted(catalog.keys()):
        models = catalog[p_name]
        if models:
            result[p_name] = {
                'provider_name': p_name,
                'free_model_count': len(models),
                'models': sorted(models, key=lambda x: x['name'])
            }
            total_models += len(models)

    return {
        'success': True,
        'total_free_providers': len(result),
        'total_free_models': total_models,
        'providers': result
    }


def sync_openrouter_free_models():
    """
    Fetch live OpenRouter model catalog from https://openrouter.ai/api/v1/models.
    Discovers all currently active free tier models (:free suffix or 0 cost) and syncs them into SQLite DB.
    """
    url = 'https://openrouter.ai/api/v1/models'
    req = urllib.request.Request(url, headers={'User-Agent': 'LiteLLM-Helper/3.0'})
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.status != 200:
                return {'success': False, 'error': f'OpenRouter API returned HTTP {response.status}'}
            payload = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        return {'success': False, 'error': f'Failed to fetch OpenRouter catalog: {str(e)}'}

    data_models = payload.get('data', [])
    if not data_models:
        return {'success': False, 'error': 'No model data returned by OpenRouter API'}

    db = Database()
    provider_obj = DBProvider(db)
    model_obj = DBAIModel(db)

    providers = provider_obj.get_all()
    openrouter_provider = next((p for p in providers if p['name'].lower() == 'openrouter'), None)
    if not openrouter_provider:
        db.close()
        return {'success': False, 'error': 'OpenRouter provider not found in database'}

    p_id = openrouter_provider['id']
    existing_models = {m['actual_model']: m for m in model_obj.get_by_provider_by_id(p_id)}

    added = []
    updated = []

    for item in data_models:
        model_id = item.get('id', '')
        pricing = item.get('pricing', {})
        prompt_cost = float(pricing.get('prompt') or 0)
        completion_cost = float(pricing.get('completion') or 0)
        is_free = model_id.endswith(':free') or (prompt_cost == 0 and completion_cost == 0)

        if not is_free:
            continue

        context_length = item.get('context_length', 32768)
        clean_name = f"openrouter-{model_id.replace('/', '-')}"
        
        extracted = extract_model_metadata(model_id, 'openrouter')
        skills = extracted.get('skills', ['General Chat'])

        if model_id in existing_models:
            existing_m = existing_models[model_id]
            if existing_m.get('max_input_tokens') != context_length:
                cursor = db.conn.cursor()
                cursor.execute('UPDATE model SET max_input_tokens = ? WHERE id = ?', (context_length, existing_m['id']))
                db.conn.commit()
                updated.append(model_id)
        else:
            try:
                model_obj.create(
                    provider_name='OpenRouter',
                    name=clean_name,
                    actual_model=model_id,
                    model_type='chat',
                    rpm_limit=20,
                    tpm_limit=50000,
                    rpd_limit=1000,
                    tpd_limit=0,
                    timeout=60,
                    max_retries=2,
                    supports_function_calling=True,
                    skills=skills,
                    max_input_tokens=context_length
                )
                added.append(model_id)
            except Exception:
                pass

    db.close()
    return {
        'success': True,
        'added_count': len(added),
        'updated_count': len(updated),
        'added_models': added,
        'updated_models': updated
    }
