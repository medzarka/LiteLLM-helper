import os
import json
import datetime

from .export import load_aggregations, load_rotation_settings

# Versions are stored as JSON snapshots in this directory (CWD-relative,
# same as litellm_helper.db / *.json). Each file is a full, restorable
# capture of the app state at the moment it was saved.
VERSIONS_DIR = 'versions'


def _ensure_dir():
    os.makedirs(VERSIONS_DIR, exist_ok=True)


def _index_path():
    return os.path.join(VERSIONS_DIR, 'index.json')


def _slug(name):
    s = name.strip().lower()
    s = ''.join(c if (c.isalnum() or c in '-_') else '-' for c in s)
    s = s.strip('-') or 'version'
    return s


# --------------------------------------------------------------------------
# State capture / restore
# --------------------------------------------------------------------------

def capture_state():
    """Return a serialisable snapshot of the full app state."""
    try:
        from ..models.models import Database, Provider as DBProvider, APIKey, AIModel, ModelFallback
    except (ImportError, ValueError):
        from models.models import Database, Provider as DBProvider, APIKey, AIModel, ModelFallback

    db = Database()
    providers = DBProvider(db).get_all()

    models = []
    for p in providers:
        for m in AIModel(db).get_by_provider_by_id(p['id']):
            models.append({
                'id': m['id'],
                'provider_id': m['provider_id'],
                'name': m['name'],
                'actual_model': m['actual_model'],
                'model_type': m.get('model_type'),
                'rpm_limit': m.get('rpm_limit', 0),
                'tpm_limit': m.get('tpm_limit', 0),
                'rpd_limit': m.get('rpd_limit', 0),
                'tpd_limit': m.get('tpd_limit', 0),
                'rpm_month_limit': m.get('rpm_month_limit', 0),
                'tpm_month_limit': m.get('tpm_month_limit', 0),
                'timeout': m.get('timeout', 15),
                'max_retries': m.get('max_retries', 2),
                'supports_function_calling': m.get('supports_function_calling', 1),
                'skills': m.get('skills', []),
            })

    keys = []
    for p in providers:
        for k in APIKey(db).get_by_provider(p['id']):
            keys.append({
                'id': k['id'],
                'provider_id': k['provider_id'],
                'key_name': k['key_name'],
                'key_value': k['key_value'],
                'is_active': k['is_active'],
            })

    fallbacks = ModelFallback(db).get_all()
    db.close()

    return {
        'captured_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'providers': providers,
        'models': models,
        'keys': keys,
        'fallbacks': fallbacks,
        'aggregations': load_aggregations(),
        'rotation_settings': load_rotation_settings(),
    }


def apply_state(state):
    """Restore a previously captured snapshot into the live DB + JSON files."""
    try:
        from ..models.models import Database, ModelFallback
    except (ImportError, ValueError):
        from models.models import Database, ModelFallback

    db = Database()

    # Delete children first to respect FK constraints.
    db.conn.execute('DELETE FROM api_key')
    db.conn.execute('DELETE FROM model')
    db.conn.execute('DELETE FROM provider')
    db.conn.execute('DELETE FROM model_fallback')

    # Re-insert with explicit IDs so aggregation model_ids stay valid.
    for p in state.get('providers', []):
        db.conn.execute(
            'INSERT INTO provider (id, name, api_base, description) VALUES (?, ?, ?, ?)',
            (p['id'], p['name'], p.get('api_base', ''), p.get('description', ''))
        )

    for m in state.get('models', []):
        db.conn.execute(
            '''INSERT INTO model
               (id, provider_id, name, actual_model, model_type, rpm_limit, tpm_limit, rpd_limit,
                tpd_limit, rpm_month_limit, tpm_month_limit, timeout, max_retries,
                supports_function_calling, skills)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (m['id'], m['provider_id'], m['name'], m['actual_model'], m.get('model_type'),
             m.get('rpm_limit', 0), m.get('tpm_limit', 0), m.get('rpd_limit', 0),
             m.get('tpd_limit', 0), m.get('rpm_month_limit', 0), m.get('tpm_month_limit', 0),
             m.get('timeout', 15), m.get('max_retries', 2), m.get('supports_function_calling', 1),
             json.dumps(m.get('skills', [])))
        )

    for k in state.get('keys', []):
        db.conn.execute(
            '''INSERT INTO api_key (id, provider_id, key_name, key_value, is_active)
               VALUES (?, ?, ?, ?, ?)''',
            (k['id'], k['provider_id'], k['key_name'], k['key_value'], k.get('is_active', 1))
        )

    for fb in state.get('fallbacks', []):
        ModelFallback(db).save(fb['primary_model'], fb.get('fallback_models', []))

    # Reset sqlite_sequence so new autoincrement ids continue past the restored ones.
    for table in ('provider', 'model', 'api_key', 'model_fallback'):
        row = db.conn.execute(f'SELECT MAX(id) FROM {table}').fetchone()
        max_id = row[0] if row and row[0] is not None else 0
        db.conn.execute(
            'INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES (?, ?)',
            (table, max_id)
        )

    db.conn.commit()
    db.close()

    # Persist the JSON-backed settings.
    _write_json('aggregations.json', state.get('aggregations', []))
    _write_json('rotation_settings.json', state.get('rotation_settings', {}))
    _write_json('fallbacks.json', state.get('fallbacks', []))


# --------------------------------------------------------------------------
# Version file management
# --------------------------------------------------------------------------

def save_version(name, description=''):
    _ensure_dir()
    state = capture_state()
    state['name'] = name
    state['description'] = description

    slug = _slug(name)
    path = os.path.join(VERSIONS_DIR, f'{slug}.json')
    counter = 1
    while os.path.exists(path):
        path = os.path.join(VERSIONS_DIR, f'{slug}-{counter}.json')
        counter += 1

    with open(path, 'w') as f:
        json.dump(state, f, indent=2)
    _update_index()
    return os.path.basename(path)


def list_versions():
    _ensure_dir()
    index = _read_index()
    versions = []
    for entry in index:
        path = os.path.join(VERSIONS_DIR, entry['file'])
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
            except Exception:
                data = {}
            versions.append({
                'file': entry['file'],
                'name': data.get('name', entry['file']),
                'description': data.get('description', ''),
                'captured_at': data.get('captured_at', ''),
                'provider_count': len(data.get('providers', [])),
                'model_count': len(data.get('models', [])),
                'key_count': len(data.get('keys', [])),
            })
    # Newest first
    versions.sort(key=lambda v: v['captured_at'], reverse=True)
    return versions


def load_version(file):
    path = os.path.join(VERSIONS_DIR, os.path.basename(file))
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def delete_version(file):
    path = os.path.join(VERSIONS_DIR, os.path.basename(file))
    if os.path.exists(path):
        os.remove(path)
    _update_index()
    return True


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------

def _read_index():
    p = _index_path()
    if os.path.exists(p):
        try:
            with open(p) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _update_index():
    entries = []
    if os.path.isdir(VERSIONS_DIR):
        for fn in sorted(os.listdir(VERSIONS_DIR)):
            if fn.endswith('.json') and fn != 'index.json':
                entries.append({'file': fn})
    with open(_index_path(), 'w') as f:
        json.dump(entries, f, indent=2)


def _write_json(path, data):
    try:
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass
