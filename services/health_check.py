import datetime


def _litellm_provider_name(provider_name):
    key = (provider_name or '').strip().lower().replace(' ', '').replace('-', '').replace('_', '')
    if key in ('google', 'googleai', 'googleai_studio', 'gemini'):
        return 'gemini'
    return key


def _litellm_model_name(actual_model, provider_name):
    model_name = (actual_model or '').strip()
    if not model_name:
        return model_name

    normalized_provider = _litellm_provider_name(provider_name)
    provider = (provider_name or '').strip().lower()

    for prefix in (provider + '/', normalized_provider + '/', 'google/', 'googleai/', 'google_ai/', 'gemini/'):
        if prefix != '/' and model_name.lower().startswith(prefix):
            model_name = model_name[len(prefix):]
            break

    if normalized_provider:
        return f'{normalized_provider}/{model_name}'

    return model_name


# Track whether litellm import succeeded (it may fail on systems without Rust)
_LITELLM_AVAILABLE = True
try:
    import litellm
    litellm.num_retries = 0
except Exception:
    _LITELLM_AVAILABLE = False


import os


def _map_exception_to_status(e):
    """Convert an exception into (status, message) for health reporting."""
    msg = str(e).lower()
    if 'unmapped provider' in msg or 'unable to map' in msg or 'unsupported provider' in msg or 'not supported' in msg or 'provider error' in msg:
        return 'skipped', str(e)[:300]
    if '503' in msg or 'high demand' in msg or 'temporarily' in msg or 'service unavailable' in msg or 'spikes in demand' in msg:
        return 'error', f'Service Temporarily Unavailable (503 High Demand): {str(e)[:250]}'
    if 'timeout' in msg or 'timed out' in msg:
        return 'error', f'Connection Timeout: {str(e)[:250]}'
    if 'missing gemini api key' in msg or 'google_api_key' in msg or '401' in msg or 'unauthorized' in msg:
        return 'error', f'API Key Config Error: {str(e)[:250]}'
    if 'not found' in msg or 'does not exist' in msg or '404' in msg or 'invalid model' in msg or 'model_not_found' in msg or 'no endpoints found' in msg:
        return 'not_found', str(e)[:300] or 'Model not found'
    return 'error', str(e)[:300] or 'Unknown error'


def _detect_model_type(model):
    """
    Distinguish model capability (chat, embedding, stt, tts, rerank)
    using model_type field and fallback name heuristics.
    """
    m_type = (model.get('model_type') or '').lower().strip()
    m_name = f"{model.get('name', '')} {model.get('actual_model', '')}".lower()

    if m_type in ('embedding', 'embeddings') or 'embed' in m_name or 'gecko' in m_name:
        return 'embedding'
    if m_type in ('audio_transcription', 'stt', 'speech_to_text', 'whisper') or 'whisper' in m_name or 'transcribe' in m_name:
        return 'stt'
    if m_type in ('audio_speech', 'tts', 'text_to_speech') or 'tts' in m_name or 'speech' in m_name or 'orpheus' in m_name:
        return 'tts'
    if m_type in ('rerank', 'reranking') or 'rerank' in m_name:
        return 'rerank'
    return 'chat'


def _generate_silent_wav(duration_sec=0.5, sample_rate=8000):
    """Generate a valid silent PCM WAV audio buffer of specified duration."""
    import struct
    num_samples = int(sample_rate * duration_sec)
    data_size = num_samples * 2  # 16-bit mono
    file_size = 36 + data_size
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        file_size,
        b'WAVE',
        b'fmt ',
        16,           # Subchunk1Size (16 for PCM)
        1,            # AudioFormat (1 for PCM)
        1,            # NumChannels (1 for Mono)
        sample_rate,  # SampleRate
        sample_rate * 2,  # ByteRate
        2,            # BlockAlign
        16,           # BitsPerSample
        b'data',
        data_size
    )
    return header + (b'\x00' * data_size)


def test_model(model, api_key, provider_name, api_base=None, timeout=20):
    """
    Send a model-type specific probe to verify endpoint availability.
    Supports Chat, Embedding, Speech-to-Text (STT), Text-to-Speech (TTS), and Rerank models.
    Returns (status, message, latency_ms) tuple.
    """
    if not _LITELLM_AVAILABLE:
        return 'skipped', 'litellm not installed - install it for live checks', 0.0

    actual_model = _litellm_model_name(model['actual_model'], provider_name)
    provider = _litellm_provider_name(provider_name)
    model_category = _detect_model_type(model)

    # Ensure Gemini API key environment variable is available for LiteLLM internal calls
    if provider == 'gemini' and api_key:
        os.environ['GEMINI_API_KEY'] = api_key
        os.environ['GOOGLE_API_KEY'] = api_key

    start_time = time.time()
    try:
        common_kwargs = {
            'model': actual_model,
            'api_key': api_key,
            'custom_llm_provider': provider,
            'timeout': timeout
        }
        if api_base:
            common_kwargs['api_base'] = api_base

        if model_category == 'embedding':
            if hasattr(litellm, 'embedding'):
                litellm.embedding(input=['health_check'], **common_kwargs)
            else:
                return 'skipped', 'litellm.embedding not supported', 0.0

        elif model_category == 'stt':
            if hasattr(litellm, 'transcription'):
                import io
                # Valid 0.5-second silent PCM WAV audio buffer for Whisper / STT probe
                silent_wav = _generate_silent_wav(duration_sec=0.5, sample_rate=8000)
                audio_file = io.BytesIO(silent_wav)
                audio_file.name = 'test.wav'
                litellm.transcription(file=audio_file, **common_kwargs)
            else:
                return 'skipped', 'litellm.transcription not supported', 0.0

        elif model_category == 'tts':
            if hasattr(litellm, 'speech'):
                voice = 'alloy'
                m_lower = actual_model.lower()
                tts_kwargs = dict(common_kwargs)
                if provider == 'groq' or 'canopylabs' in m_lower or 'orpheus' in m_lower:
                    voice = 'sultan'
                    tts_kwargs['response_format'] = 'wav'
                elif provider == 'gemini' or 'google' in m_lower:
                    voice = 'Puck'
                litellm.speech(input='hello', voice=voice, **tts_kwargs)
            else:
                return 'skipped', 'litellm.speech not supported', 0.0

        elif model_category == 'rerank':
            if hasattr(litellm, 'rerank'):
                litellm.rerank(query='test', documents=['test'], **common_kwargs)
            else:
                return 'skipped', 'litellm.rerank not supported', 0.0

        else:
            # Default: Chat / Completion models
            litellm.completion(messages=[{'role': 'user', 'content': 'test'}], **common_kwargs)

        latency_ms = round((time.time() - start_time) * 1000, 1)
        return 'ok', '', latency_ms

    except Exception as e:
        latency_ms = round((time.time() - start_time) * 1000, 1)
        status, message = _map_exception_to_status(e)
        return status, message, latency_ms


import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def _check_provider_models(provider_task):
    """
    Worker function running in a dedicated thread for a single provider.
    Sequentially probes all models belonging to this provider with a pause between calls.
    """
    provider, api_key, models, timeout, delay_between_calls = provider_task
    p_name = provider['name']
    api_base = provider.get('api_base')
    provider_results = []

    for idx, m in enumerate(models):
        if idx > 0 and delay_between_calls > 0:
            time.sleep(delay_between_calls)

        m['provider_name'] = p_name
        if not _LITELLM_AVAILABLE:
            status, message, latency_ms = 'skipped', 'litellm not installed - install it for live checks', 0.0
        elif api_key:
            status, message, latency_ms = test_model(m, api_key, p_name, api_base=api_base, timeout=timeout)
        else:
            status, message, latency_ms = 'skipped', 'No active API key for this provider', 0.0
        provider_results.append((m, status, message, latency_ms))

    return provider_results


def run_health_check(timeout=20, delay_between_calls=0.5, model_ids=None):
    """
    Probe models concurrently with exactly 1 thread per provider.
    Each provider thread sequentially tests its own list of models with a pause (delay_between_calls seconds) between probes.
    """
    try:
        from ..models.models import Database, Provider as DBProvider, AIModel as DBAIModel, APIKey as DBAPIKey
    except (ImportError, ValueError):
        from models.models import Database, Provider as DBProvider, AIModel as DBAIModel, APIKey as DBAPIKey

    db = Database()
    checked_at = datetime.datetime.now().isoformat(timespec='seconds')
    provider_tasks = []

    providers = DBProvider(db).get_all()
    for provider in providers:
        p_id = provider['id']
        keys = DBAPIKey(db).get_by_provider(p_id)
        active_keys = [k for k in keys if k['is_active']]
        api_key = active_keys[0]['key_value'] if active_keys else None

        models = DBAIModel(db).get_by_provider_by_id(p_id)
        if models and model_ids is not None:
            models = [m for m in models if str(m['id']) in map(str, model_ids)]
        if models:
            provider_tasks.append((provider, api_key, models, timeout, delay_between_calls))

    results = []
    if provider_tasks:
        # Create exactly 1 thread per provider
        with ThreadPoolExecutor(max_workers=len(provider_tasks)) as executor:
            future_to_provider = {executor.submit(_check_provider_models, task): task for task in provider_tasks}
            for future in as_completed(future_to_provider):
                try:
                    p_results = future.result()
                    results.extend(p_results)
                except Exception as e:
                    task = future_to_provider[future]
                    p_name = task[0]['name']
                    for m in task[2]:
                        results.append((m, 'error', f'Provider thread error ({p_name}): {str(e)[:250]}', 0.0))

    # Persist results and build response report
    model_obj = DBAIModel(db)
    report = []
    for m, status, message, latency_ms in results:
        model_obj.update_health(m['id'], status, message, checked_at, latency_ms=latency_ms)
        history = model_obj.get_health_history(m['id'], limit=20)
        stats = model_obj.get_model_latency_stats(m['id'])
        last_success_at = model_obj.get_last_success_at(m['id'])
        report.append({
            'id': m['id'],
            'name': m['name'],
            'provider_name': m['provider_name'],
            'actual_model': m['actual_model'],
            'status': status,
            'message': message,
            'checked_at': checked_at,
            'latency_ms': latency_ms,
            'latency_stats': stats,
            'history': history,
            'last_success_at': last_success_at
        })

    db.close()
    return report


def get_all_models_with_health():
    """Fetch all models with their latest health status and last 20 check history."""
    try:
        from ..models.models import Database, Provider as DBProvider, AIModel as DBAIModel
    except (ImportError, ValueError):
        from models.models import Database, Provider as DBProvider, AIModel as DBAIModel

    db = Database()
    model_obj = DBAIModel(db)
    rows = []
    for p in DBProvider(db).get_all():
        for m in model_obj.get_by_provider_by_id(p['id']):
            m['provider_name'] = p['name']
            m['history'] = model_obj.get_health_history(m['id'], limit=20)
            m['latency_stats'] = model_obj.get_model_latency_stats(m['id'])
            m['last_success_at'] = model_obj.get_last_success_at(m['id'])
            rows.append(m)
    db.close()
    return rows
