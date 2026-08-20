import os
import json

_LITELLM_AVAILABLE = True
try:
    import litellm
except Exception:
    _LITELLM_AVAILABLE = False


def extract_model_metadata(actual_model_name, provider_name=None):
    """
    Extract comprehensive metadata for a model using LiteLLM model info and heuristics.
    Returns dict with max_input_tokens, max_output_tokens, supports_function_calling, supports_vision, mode, skills, model_size_b.
    """
    actual = (actual_model_name or '').strip()
    provider = (provider_name or '').strip().lower()
    full_name = f"{provider}/{actual}" if provider and not actual.startswith(f"{provider}/") else actual

    metadata = {
        'max_input_tokens': 32768,
        'max_output_tokens': 4096,
        'supports_function_calling': True,
        'supports_vision': False,
        'mode': 'chat',
        'skills': [],
        'model_size_b': None
    }

    # Extract specs from LiteLLM database if available
    if _LITELLM_AVAILABLE and hasattr(litellm, 'get_model_info'):
        try:
            info = litellm.get_model_info(full_name)
            if info:
                if 'max_input_tokens' in info:
                    metadata['max_input_tokens'] = info['max_input_tokens']
                if 'max_output_tokens' in info:
                    metadata['max_output_tokens'] = info['max_output_tokens']
                if 'supports_function_calling' in info:
                    metadata['supports_function_calling'] = bool(info['supports_function_calling'])
                if 'supports_vision' in info:
                    metadata['supports_vision'] = bool(info['supports_vision'])
                if 'mode' in info:
                    metadata['mode'] = info['mode']
        except Exception:
            pass

    # Heuristic parameter size extraction (e.g. 70b, 27b, 8b, 3b)
    import re
    size_match = re.search(r'(\d+)\s*b\b', actual.lower())
    if size_match:
        metadata['model_size_b'] = int(size_match.group(1))

    # Skill classification
    skills = set()
    actual_lower = actual.lower()

    if any(k in actual_lower for k in ('code', 'coder', 'codestral', 'laguna', 'dev')):
        skills.add('Coding')
        skills.add('Agentic')

    if any(k in actual_lower for k in ('r1', 'o1', 'o3', 'reasoning', 'think', 'math')):
        skills.add('Reasoning')

    if any(k in actual_lower for k in ('vision', 'vl', 'multimodal', 'gemini-2', 'claude-3', 'gpt-4o')):
        skills.add('Multimodal')

    if metadata['max_input_tokens'] and metadata['max_input_tokens'] >= 64000:
        skills.add('Long-Context')

    if not skills:
        skills.add('General Chat')

    metadata['skills'] = list(skills)
    return metadata


def autofill_model_specs_in_db(model_id):
    """Auto-populate model specs into SQLite database for a specific model."""
    try:
        from ..models.models import Database, AIModel as DBAIModel
    except (ImportError, ValueError):
        from models.models import Database, AIModel as DBAIModel

    db = Database()
    cursor = db.conn.cursor()
    cursor.execute(
        '''SELECT m.id, m.name, m.actual_model, p.name as provider_name
           FROM model m JOIN provider p ON m.provider_id = p.id
           WHERE m.id = ?''',
        (model_id,)
    )
    row = cursor.fetchone()
    if row:
        meta = extract_model_metadata(row[2], row[3])
        cursor.execute(
            '''UPDATE model SET
               max_input_tokens = COALESCE(max_input_tokens, ?),
               supports_function_calling = ?,
               skills = ?,
               model_size_b = COALESCE(model_size_b, ?)
               WHERE id = ?''',
            (
                meta['max_input_tokens'],
                1 if meta['supports_function_calling'] else 0,
                json.dumps(meta['skills']),
                meta['model_size_b'],
                model_id
            )
        )
        db.conn.commit()
    db.close()
    return True
