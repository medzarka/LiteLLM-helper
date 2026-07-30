import os
import json
try:
    from ..models.models import Database, Provider as DBProvider, AIModel as DBAIModel, APIKey as DBAPIKey, ModelFallback
except (ImportError, ValueError):
    from models.models import Database, Provider as DBProvider, AIModel as DBAIModel, APIKey as DBAPIKey, ModelFallback


def _classify_model_tier(model):
    """Classify model capability tier based on model type, name, and skills."""
    name = (model.get('name') or '').lower()
    actual = (model.get('actual_model') or '').lower()
    m_type = (model.get('model_type') or '').lower()
    skills = [s.lower() for s in model.get('skills', [])]
    full = f"{name} {actual} {m_type}"

    if 'reasoning' in skills or any(k in full for k in ('r1', 'o1', 'o3', 'reasoning', 'think')):
        return 'reasoning'
    if 'coding' in skills or any(k in full for k in ('code', 'coder', 'codestral', 'laguna', 'agentic')):
        return 'coding'
    if 'multimodal' in skills or any(k in full for k in ('vision', 'multimodal', 'vl')):
        return 'multimodal'
    if any(k in full for k in ('flash', 'mini', 'small', '8b', '7b', '3b', '1b', 'instant', 'nano')):
        return 'fast'
    return 'general'


def _detect_category(model):
    """Detect model mode (chat, embedding, transcription, tts, rerank)."""
    m_type = (model.get('model_type') or '').lower().strip()
    m_full = f"{model.get('name', '')} {model.get('actual_model', '')}".lower()

    if m_type in ('embedding', 'embeddings') or 'embed' in m_full or 'gecko' in m_full:
        return 'embedding'
    if m_type in ('stt', 'audio_transcription', 'whisper') or 'whisper' in m_full or 'transcribe' in m_full:
        return 'transcription'
    if m_type in ('tts', 'audio_speech') or 'tts' in m_full or 'speech' in m_full or 'orpheus' in m_full:
        return 'audio_speech'
    if m_type == 'rerank' or 'rerank' in m_full:
        return 'rerank'
    return 'chat'


def generate_smart_fallbacks():
    """
    Generate smart, capability-matched, cross-provider fallback chains.
    Ensures primary models fall back to compatible alternative models on DIFFERENT providers.
    """
    db = Database()
    provider_obj = DBProvider(db)
    model_obj = DBAIModel(db)
    key_obj = DBAPIKey(db)

    providers = provider_obj.get_all()
    active_models = []

    # Map models with active keys
    for p in providers:
        p_keys = key_obj.get_by_provider(p['id'])
        if not any(k['is_active'] for k in p_keys):
            continue

        p_models = model_obj.get_by_provider_by_id(p['id'])
        for m in p_models:
            m['provider_name'] = p['name']
            m['category'] = _detect_category(m)
            m['tier'] = _classify_model_tier(m)
            active_models.append(m)

    generated_fallbacks = []

    for primary in active_models:
        primary_name = primary['name']
        p_provider = primary['provider_name']
        p_category = primary['category']
        p_tier = primary['tier']

        # Find candidate fallback models
        candidates = []
        for candidate in active_models:
            if candidate['name'] == primary_name:
                continue

            # Must match category (e.g. chat to chat, embedding to embedding)
            if candidate['category'] != p_category:
                continue

            # Cross-provider priority score
            score = 0
            # Higher score if different provider (cross-provider redundancy)
            if candidate['provider_name'] != p_provider:
                score += 100

            # Higher score if matching capability tier
            if candidate['tier'] == p_tier:
                score += 50
            elif (p_tier, candidate['tier']) in [('coding', 'general'), ('reasoning', 'general'), ('fast', 'general')]:
                score += 20

            # Favor models marked as 'ok' in health checks
            if candidate.get('last_status') == 'ok':
                score += 30
            elif candidate.get('last_status') in ('not_found', 'error'):
                score -= 50

            candidates.append((score, candidate['name']))

        # Sort candidates by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)

        # Pick top 2-3 fallback models
        fallback_names = [c[1] for c in candidates[:3] if c[0] > 0]
        if fallback_names:
            generated_fallbacks.append({
                'primary_model': primary_name,
                'fallback_models': fallback_names
            })

    db.close()
    return generated_fallbacks


def apply_smart_fallbacks():
    """Generate smart fallbacks and save them into the SQLite database."""
    fallbacks = generate_smart_fallbacks()
    db = Database()
    fb_obj = ModelFallback(db)
    saved_count = 0
    for fb in fallbacks:
        fb_obj.save(fb['primary_model'], fb['fallback_models'])
        saved_count += 1
    db.close()
    return saved_count
