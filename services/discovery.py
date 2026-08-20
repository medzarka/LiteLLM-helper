import requests
import re

import litellm

def extract_size_b(text):
    if not text:
        return None
    match = re.search(r'(\d+(\.\d+)?)b\b', str(text), re.IGNORECASE)
    if match:
        try:
            return int(round(float(match.group(1))))
        except:
            return None
    return None

def enrich_with_litellm(m):
    """
    Enriches a discovered model dictionary with precise metadata from LiteLLM.
    """
    provider = m.get('provider', '').lower()
    m_id = m.get('id', '')
    
    # Format model string for litellm.get_model_info
    litellm_model = m_id
    if provider == 'openrouter':
        if not litellm_model.startswith('openrouter/'):
            litellm_model = f"openrouter/{m_id}"
    elif provider == 'gemini' or provider == 'google':
        if not litellm_model.startswith('gemini/'):
            litellm_model = f"gemini/{m_id}"
    elif provider == 'mistral':
        if not litellm_model.startswith('mistral/'):
            litellm_model = f"mistral/{m_id}"
    elif provider == 'groq':
        if not litellm_model.startswith('groq/'):
            litellm_model = f"groq/{m_id}"
    
    try:
        info = litellm.get_model_info(litellm_model)
        if not info:
            return m
            
        # Context window
        if info.get('max_input_tokens'):
            m['context_length'] = info['max_input_tokens']
        elif info.get('max_tokens'):
            m['context_length'] = info['max_tokens']
            
        # Max output tokens
        if info.get('max_output_tokens'):
            m['max_output_tokens'] = info['max_output_tokens']
            
        # Skills
        skills = set(m.get('skills', []))
        if info.get('supports_vision'):
            skills.add('vision')
        if info.get('supports_function_calling'):
            m['supports_function_calling'] = True
        if info.get('supports_audio_input'):
            skills.add('audio_input')
        if info.get('supports_audio_output'):
            skills.add('audio_output')
            
        # Mode
        mode = info.get('mode', '')
        if mode == 'audio_transcription':
            m['model_type'] = 'stt'
            skills.add('stt')
        elif mode == 'audio_speech':
            m['model_type'] = 'tts'
            skills.add('tts')
        elif mode == 'image_generation':
            m['model_type'] = 'image'
            skills.add('image_generation')
        elif mode == 'embedding':
            m['model_type'] = 'embedding'
            skills.add('embedding')
        elif mode == 'chat' or mode == 'completion':
            m['model_type'] = 'chat'
            
        m['skills'] = list(skills)
        
    except Exception as e:
        # If litellm throws an exception (e.g. model not found), just ignore and use API-based guesses
        pass

    # Soft skills inference (applies to both litellm enriched and non-enriched models)
    search_text = (m.get('name', '') + ' ' + m.get('description', '') + ' ' + m.get('id', '')).lower()
    
    if 'math' in search_text or 'mathematics' in search_text:
        if 'math' not in m.get('skills', []):
            m.setdefault('skills', []).append('math')
            
    if 'code' in search_text or 'coding' in search_text or 'coder' in search_text or 'programming' in search_text:
        if 'coding' not in m.get('skills', []):
            m.setdefault('skills', []).append('coding')
            
    if 'reasoning' in search_text or 'thinking' in search_text or 'o1' in search_text or 'chain of thought' in search_text:
        if 'reasoning' not in m.get('skills', []):
            m.setdefault('skills', []).append('reasoning')

    if 'medical' in search_text or 'clinical' in search_text or 'biomedical' in search_text or 'health' in search_text:
        if 'medical' not in m.get('skills', []):
            m.setdefault('skills', []).append('medical')

    if 'instruct' in search_text or 'chat' in search_text or 'conversational' in search_text:
        if 'instruct' not in m.get('skills', []):
            m.setdefault('skills', []).append('instruct')

    if 'rp' in search_text or 'roleplay' in search_text or 'uncensored' in search_text:
        if 'roleplay' not in m.get('skills', []):
            m.setdefault('skills', []).append('roleplay')

    if 'agent' in search_text or 'autonomous' in search_text or 'tool use' in search_text:
        if 'agentic' not in m.get('skills', []):
            m.setdefault('skills', []).append('agentic')

    if 'translate' in search_text or 'translation' in search_text or 'multilingual' in search_text:
        if 'translation' not in m.get('skills', []):
            m.setdefault('skills', []).append('translation')

    return m

def discover_free_models():
    """
    Fetches dynamic openrouter free models and appends curated lists for Google, Mistral, and Ollama.
    """
    models = []
    
    # 1. Fetch from OpenRouter
    try:
        resp = requests.get('https://openrouter.ai/api/v1/models', timeout=5)
        if resp.status_code == 200:
            data = resp.json().get('data', [])
            for m in data:
                # Check if it's free
                pricing = m.get('pricing', {})
                if pricing.get('prompt') == '0' and pricing.get('completion') == '0':
                    architecture = m.get('architecture', {})
                    # For OpenRouter, tool capability is usually indicated, but we can assume false unless noted.
                    # Or just default to True for certain known ones, but let's default to False safely.
                    supports_tools = False
                    if architecture and ('tool_choice' in architecture or 'tools' in architecture or architecture.get('modality') == 'text->tool'):
                        supports_tools = True
                        
                    skills = []
                    modality = architecture.get('modality', '') if architecture else ''
                    if modality and 'image' in str(modality).lower():
                        skills.append('vision')
                    
                    m_id = m['id']
                    m_name = m.get('name', m_id)
                    size = extract_size_b(m_id) or extract_size_b(m_name)
                    
                    # Many openrouter models support function calling even if not explicit in architecture schema.
                    
                    # Hardcoded limit for free tier
                    models.append({
                        'provider': 'openrouter',
                        'id': m_id,
                        'name': m_name,
                        'context_length': m.get('context_length', 4096),
                        'supports_function_calling': supports_tools,
                        'rpm_limit': 20,
                        'tpm_limit': 40000,
                        'rpd_limit': 200,
                        'description': m.get('description', '')[:100] + '...' if len(m.get('description', '')) > 100 else m.get('description', ''),
                        'model_size_b': size,
                        'skills': skills
                    })
    except Exception as e:
        print("Failed to fetch from openrouter:", e)

    # Fetch API Keys from DB for live fetching
    try:
        try:
            from ..models.models import Database
        except (ImportError, ValueError):
            from models.models import Database
            
        db = Database()
        cursor = db.conn.cursor()
        
        # Get Google key
        cursor.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE p.provider_type IN ('google', 'gemini', 'googleai', 'googleai_studio') AND k.is_active = 1 LIMIT 1")
        g_row = cursor.fetchone()
        google_api_key = g_row[0] if g_row else None
        
        # Get Mistral key
        cursor.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE p.provider_type = 'mistral' AND k.is_active = 1 LIMIT 1")
        m_row = cursor.fetchone()
        mistral_api_key = m_row[0] if m_row else None
        
        # Get Groq key
        cursor.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE p.provider_type = 'groq' AND k.is_active = 1 LIMIT 1")
        gr_row = cursor.fetchone()
        groq_api_key = gr_row[0] if gr_row else None

        # Get Cohere key
        cursor.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE p.provider_type = 'cohere' AND k.is_active = 1 LIMIT 1")
        co_row = cursor.fetchone()
        cohere_api_key = co_row[0] if co_row else None
        
    except Exception as e:
        print("Error fetching keys for discovery:", e)
        google_api_key = None
        mistral_api_key = None
        groq_api_key = None
        cohere_api_key = None

    # 2. Google Models
    google_models = []
    if google_api_key:
        try:
            resp = requests.get(f'https://generativelanguage.googleapis.com/v1beta/models?key={google_api_key}', timeout=5)
            if resp.status_code == 200:
                data = resp.json().get('models', [])
                for m in data:
                    if 'generateContent' not in m.get('supportedGenerationMethods', []):
                        continue
                    m_id = m['name'].replace('models/', '')
                    m_name = m.get('displayName', m_id)
                    # Filter for models that have a free tier
                    if any(k in m_id for k in ['flash', 'pro', 'gemma']) and 'embedding' not in m_id:
                        skills = []
                        if '1.5' in m_id or 'vision' in m_id:
                            skills.append('vision')
                            
                        size = extract_size_b(m_id) or extract_size_b(m_name)
                        
                        if 'gemma' in m_id.lower():
                            rpm = 30; tpm = 16000; rpd = 14400
                        elif 'pro' in m_id.lower():
                            rpm = 2; tpm = 32000; rpd = 50
                        elif 'lite' in m_id.lower() or '8b' in m_id.lower():
                            rpm = 15; tpm = 250000; rpd = 500
                        elif 'flash' in m_id.lower():
                            rpm = 15; tpm = 1000000; rpd = 1500
                        else:
                            rpm = 5; tpm = 250000; rpd = 20
                            
                        google_models.append({
                            'provider': 'google',
                            'id': m_id,
                            'name': m_name,
                            'context_length': m.get('inputTokenLimit', 32768),
                            'supports_function_calling': True,
                            'rpm_limit': rpm,
                            'tpm_limit': tpm,
                            'rpd_limit': rpd,
                            'description': m.get('description', '')[:100] + '...' if len(m.get('description', '')) > 100 else m.get('description', ''),
                            'model_size_b': size,
                            'skills': skills
                        })
        except Exception as e:
            print("Failed to fetch from Google API:", e)

    models.extend(google_models)

    # 3. Cohere Models
    cohere_models = []
    if cohere_api_key:
        try:
            resp = requests.get('https://api.cohere.com/v1/models', headers={'Authorization': f'Bearer {cohere_api_key}'}, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get('models', [])
                for m in data:
                    endpoints = m.get('endpoints', [])
                    if 'chat' in endpoints or 'generate' in endpoints:
                        m_id = m['name']
                        cohere_models.append({
                            'provider': 'cohere',
                            'id': m_id,
                            'name': f"Cohere {m_id}",
                            'context_length': int(m.get('context_length', 128000)),
                            'supports_function_calling': True if 'chat' in endpoints else False,
                            'rpm_limit': 1000,
                            'tpm_limit': 100000,
                            'rpd_limit': 10000,
                            'description': 'Cohere powerful generative model.',
                            'model_size_b': None,
                            'skills': []
                        })
        except Exception as e:
            print("Failed to fetch from Cohere API:", e)
            
    models.extend(cohere_models)

    # 4. Mistral Models
    mistral_models = []
    mistral_ids = set()
    if mistral_api_key:
        try:
            resp = requests.get('https://api.mistral.ai/v1/models', headers={'Authorization': f'Bearer {mistral_api_key}'}, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get('data', [])
                for m in data:
                    capabilities = m.get('capabilities', {})
                    skills = []
                    if capabilities.get('vision'):
                        skills.append('vision')
                    
                    m_id = m['id']
                    m_name = m.get('name', m_id)
                    size = extract_size_b(m_id) or extract_size_b(m_name)
                    
                    mistral_models.append({
                        'provider': 'mistral',
                        'id': m_id,
                        'name': m_name,
                        'context_length': m.get('max_context_length', 32768),
                        'supports_function_calling': capabilities.get('function_calling', True),
                        'rpm_limit': 60,
                        'tpm_limit': 2000000,
                        'rpd_limit': 10000,
                        'description': m.get('description', '')[:100] + '...' if len(m.get('description', '')) > 100 else m.get('description', ''),
                        'model_size_b': size,
                        'skills': skills
                    })
                    mistral_ids.add(m_id)
        except Exception as e:
            print("Failed to fetch from Mistral API:", e)
            
    models.extend(mistral_models)
    
    # 4. Groq Models
    groq_models = []
    if groq_api_key:
        try:
            resp = requests.get('https://api.groq.com/openai/v1/models', headers={'Authorization': f'Bearer {groq_api_key}'}, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get('data', [])
                for m in data:
                    m_id = m['id']
                    # Groq doesn't provide much metadata in their models endpoint,
                    # but we can try to guess context size from the name for common ones.
                    # e.g., llama3-8b-8192
                    ctx_len = 8192
                    if '128k' in m_id: ctx_len = 128000
                    elif '32k' in m_id: ctx_len = 32768
                    elif '-8192' in m_id: ctx_len = 8192
                    
                    size = extract_size_b(m_id)
                    
                    skills = []
                    if 'vision' in m_id or 'llava' in m_id:
                        skills.append('vision')
                        
                    # Usually Groq models support function calling (llama 3, mixtral)
                    supports_fc = True
                    if 'gemma' in m_id: supports_fc = False
                    
                    groq_models.append({
                        'provider': 'groq',
                        'id': m_id,
                        'name': m_id.capitalize(),
                        'context_length': ctx_len,
                        'supports_function_calling': supports_fc,
                        'rpm_limit': 30,  # general free tier limit
                        'tpm_limit': 14400,
                        'rpd_limit': 14400,
                        'description': 'Groq ultra-fast LPU model.',
                        'model_size_b': size,
                        'skills': skills
                    })
        except Exception as e:
            print("Failed to fetch from Groq API:", e)
            
    models.extend(groq_models)

    # 5. Ollama Models (API Fetching)
    ollama_models = []
    try:
        resp = requests.get('https://ollama.com/api/tags', timeout=5)
        if resp.status_code == 200:
            data = resp.json().get('models', [])
            for m in data:
                m_name = m['name']
                # Try to clean up the tag to get base name
                base_name = m_name.split(':')[0] if ':' in m_name else m_name
                
                ctx_len = 8192
                skills = []
                supports_fc = True
                size = extract_size_b(m_name)
                
                # Fetch detailed info from /api/show
                try:
                    show_resp = requests.post('https://ollama.com/api/show', json={'name': m_name}, timeout=3)
                    if show_resp.status_code == 200:
                        show_data = show_resp.json()
                        capabilities = show_data.get('capabilities', [])
                        if 'vision' in capabilities:
                            skills.append('vision')
                        supports_fc = 'tools' in capabilities
                        
                        model_info = show_data.get('model_info', {})
                        arch = model_info.get('general.architecture')
                        if arch and f'{arch}.context_length' in model_info:
                            ctx_len = model_info[f'{arch}.context_length']
                        else:
                            for k, v in model_info.items():
                                if 'context_length' in k:
                                    ctx_len = v
                                    break
                                    
                        param_size = show_data.get('details', {}).get('parameter_size', '')
                        size = extract_size_b(param_size) or extract_size_b(m_name)
                except Exception as e:
                    print(f"Failed to fetch details for {m_name}: {e}")
                    
                if m_name and m_name not in [x['id'] for x in ollama_models]:
                    ollama_models.append({
                        'provider': 'ollama',
                        'id': m_name,
                        'name': m_name.capitalize(),
                        'context_length': ctx_len,
                        'supports_function_calling': supports_fc,
                        'rpm_limit': 9999,
                        'tpm_limit': 999999,
                        'rpd_limit': 99999,
                        'description': f"Ollama {base_name} model from the public registry.",
                        'model_size_b': size,
                        'skills': skills
                    })
    except Exception as e:
        print("Failed to fetch Ollama API:", e)
        
    if not ollama_models:
        # Fallback
        ollama_models = [
            {
                'provider': 'ollama',
                'id': 'llama3.1',
                'name': 'Llama 3.1 8B',
                'context_length': 131072,
                'supports_function_calling': True,
                'rpm_limit': 9999,
                'tpm_limit': 999999,
                'rpd_limit': 99999,
                'description': 'Fallback Ollama model.'
            }
        ]
        
    models.extend(ollama_models)
    
    # Enrich all discovered models with LiteLLM data
    enriched_models = [enrich_with_litellm(m) for m in models]
    return enriched_models

def get_all_provider_models(google_api_key=None, mistral_api_key=None, groq_api_key=None, cohere_api_key=None):
    """
    Fetches ALL models (paid and free) from supported providers to detect deprecated models.
    Returns a dictionary mapping provider IDs to a set of actual model strings.
    """
    provider_models = {
        'openrouter': set(),
        'gemini': set(),
        'mistral': set(),
        'ollama': set(),
        'groq': set(),
        'cohere': set()
    }
    
    # 1. OpenRouter
    try:
        resp = requests.get('https://openrouter.ai/api/v1/models', timeout=5)
        if resp.status_code == 200:
            for m in resp.json().get('data', []):
                provider_models['openrouter'].add(m['id'])
    except: pass
    
    # 2. Google
    if google_api_key:
        try:
            resp = requests.get(f'https://generativelanguage.googleapis.com/v1beta/models?key={google_api_key}', timeout=5)
            if resp.status_code == 200:
                for m in resp.json().get('models', []):
                    if 'generateContent' in m.get('supportedGenerationMethods', []):
                        provider_models['gemini'].add(m['name'].replace('models/', ''))
        except: pass
        
    # 3. Mistral
    if mistral_api_key:
        try:
            resp = requests.get('https://api.mistral.ai/v1/models', headers={'Authorization': f'Bearer {mistral_api_key}'}, timeout=5)
            if resp.status_code == 200:
                for m in resp.json().get('data', []):
                    provider_models['mistral'].add(m['id'])
        except: pass
        
    # 4. Ollama
    try:
        resp = requests.get('https://ollama.com/api/tags', timeout=5)
        if resp.status_code == 200:
            for m in resp.json().get('models', []):
                provider_models['ollama'].add(m['name'])
    except: pass
    
    # 5. Groq
    if groq_api_key:
        try:
            resp = requests.get('https://api.groq.com/openai/v1/models', headers={'Authorization': f'Bearer {groq_api_key}'}, timeout=5)
            if resp.status_code == 200:
                for m in resp.json().get('data', []):
                    provider_models['groq'].add(m['id'])
        except: pass

    # 6. Cohere
    if cohere_api_key:
        try:
            resp = requests.get('https://api.cohere.com/v1/models', headers={'Authorization': f'Bearer {cohere_api_key}'}, timeout=5)
            if resp.status_code == 200:
                for m in resp.json().get('models', []):
                    if 'chat' in m.get('endpoints', []) or 'generate' in m.get('endpoints', []):
                        provider_models['cohere'].add(m['name'])
        except: pass
    
    return provider_models
