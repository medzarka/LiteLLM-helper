from flask import Blueprint, request, jsonify
try:
    from ..models.models import AIModel, Database, Provider as DBProvider
except (ImportError, ValueError):
    from models.models import AIModel, Database, Provider as DBProvider

bp = Blueprint('models', __name__)
db = Database()

@bp.route('/models/discover', methods=['GET'])
def discover_models():
    """Returns a list of discovered free-tier models."""
    try:
        try:
            from ..services.discovery import discover_free_models
        except (ImportError, ValueError):
            from services.discovery import discover_free_models
            
        models = discover_free_models()
        
        # Check against existing local models
        cursor = db.conn.cursor()
        cursor.execute('SELECT id, actual_model FROM model')
        local_models = cursor.fetchall()
        
        # Create a mapping of actual_model -> list of local IDs (in case of multiple)
        local_map = {}
        for row in local_models:
            am = row[1]
            if am not in local_map:
                local_map[am] = []
            local_map[am].append(row[0])
            
        for m in models:
            # If the actual model string matches one in our local DB, flag it
            if m['id'] in local_map:
                m['existing_model_id'] = local_map[m['id']][0] # take the first match
                
        return jsonify(models), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/models/deprecated', methods=['GET'])
def get_deprecated_models():
    """Returns a list of local models that are no longer listed by their providers."""
    try:
        try:
            from ..services.discovery import get_all_provider_models
        except (ImportError, ValueError):
            from services.discovery import get_all_provider_models
            
        cursor = db.conn.cursor()
        
        # We need keys to pass to get_all_provider_models
        cursor.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE (p.provider_type = 'google' OR p.provider_type = 'gemini') AND k.is_active = 1 LIMIT 1")
        google_row = cursor.fetchone()
        google_api_key = google_row[0] if google_row else None
        
        cursor.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE p.provider_type = 'mistral' AND k.is_active = 1 LIMIT 1")
        mistral_row = cursor.fetchone()
        mistral_api_key = mistral_row[0] if mistral_row else None
        
        cursor.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE p.provider_type = 'groq' AND k.is_active = 1 LIMIT 1")
        groq_row = cursor.fetchone()
        groq_api_key = groq_row[0] if groq_row else None

        cursor.execute("SELECT k.key_value FROM api_key k JOIN provider p ON k.provider_id = p.id WHERE p.provider_type = 'cohere' AND k.is_active = 1 LIMIT 1")
        cohere_row = cursor.fetchone()
        cohere_api_key = cohere_row[0] if cohere_row else None
        
        provider_models = get_all_provider_models(google_api_key, mistral_api_key, groq_api_key, cohere_api_key)
        
        # Fetch all models from db with their provider types
        cursor.execute('''
            SELECT m.id, m.name, m.actual_model, p.provider_type, p.name as provider_name
            FROM model m
            JOIN provider p ON m.provider_id = p.id
        ''')
        local_models = cursor.fetchall()
        
        deprecated = []
        # Check supported provider types
        supported = ['gemini', 'mistral', 'openrouter', 'ollama', 'groq']
        
        for row in local_models:
            m_id, m_name, actual_model, p_type, p_name = row
            if p_type in supported:
                # If provider API was successfully queried (set is not empty) and model is not in it
                if len(provider_models.get(p_type, set())) > 0 and actual_model not in provider_models[p_type]:
                    deprecated.append({
                        'id': m_id,
                        'name': m_name,
                        'actual_model': actual_model,
                        'provider_type': p_type,
                        'provider_name': p_name
                    })
                    
        return jsonify(deprecated)
    except Exception as e:
        print(f"Deprecated check error: {e}")
        return jsonify({'error': str(e)}), 500

@bp.route('/providers/<provider_name>/models', methods=['POST'])
def create_model_from_provider(provider_name):
    """
    Create a model associated with a provider by name.
    This endpoint expects a provider name in the path rather than ID.
    """
    try:
        data = request.get_json()
        model_obj = AIModel(db)
        model_id = model_obj.create(
            provider_name=provider_name,
            name=data['name'],
            actual_model=data['actual_model'],
            model_type=data.get('model_type', ''),
            rpm_limit=data.get('rpm_limit', 30),
            tpm_limit=data.get('tpm_limit', 6000),
            rpd_limit=data.get('rpd_limit', 0),
            tpd_limit=data.get('tpd_limit', 0),
            rpm_month_limit=data.get('rpm_month_limit', 0),
            tpm_month_limit=data.get('tpm_month_limit', 0),
            timeout=data.get('timeout', 15),
            max_retries=data.get('max_retries', 2),
            supports_function_calling=data.get('supports_function_calling', True),
            skills=data.get('skills', []),
            model_size_b=data.get('model_size_b'),
            max_input_tokens=data.get('max_input_tokens'),
            max_output_tokens=data.get('max_output_tokens'),
            description=data.get('description')
        )
        # Automatically extract and populate model specs & skills
        try:
            try:
                from .metadata_extractor import autofill_model_specs_in_db
            except (ImportError, ValueError):
                from metadata_extractor import autofill_model_specs_in_db
            autofill_model_specs_in_db(model_id)
        except Exception:
            pass

        return jsonify({
            'id': model_id,
            'message': 'Model created and specs auto-extracted successfully'
        }), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@bp.route('/providers/<provider_name>/models', methods=['GET'])
def get_models_by_provider_name(provider_name):
    provider_obj = DBProvider(db).get_by_name(provider_name)
    if not provider_obj:
        return jsonify({'error': f'Provider {provider_name} not found'}), 404
    models = AIModel(db).get_by_provider_by_id(provider_obj['id'])
    return jsonify(models)

@bp.route('/providers/<int:provider_id>/models', methods=['GET'])
def get_models_by_provider(provider_id):
    """Get all models for a specific provider by ID"""
    models = AIModel(db).get_by_provider_by_id(provider_id)
    return jsonify(models)

@bp.route('/models', methods=['GET'])
def list_all_models():
    """List all models across every provider (used by the Merge UI)."""
    result = []
    for p in DBProvider(db).get_all():
        for m in AIModel(db).get_by_provider(p['name']):
            result.append({
                'id': m['id'],
                'name': m['name'],
                'provider_name': m['provider_name'],
                'actual_model': m['actual_model'],
            })
    return jsonify(result)

@bp.route('/models/<int:model_id>', methods=['GET'])
def get_model_by_id(model_id):
    """Get a specific model by ID"""
    model = AIModel(db).get(model_id)
    return jsonify(model) if model else ('', 404)

@bp.route('/models/<int:model_id>', methods=['PUT'])
def update_model_by_id(model_id):
    """Update a specific model by ID"""
    allowed_fields = [
        'name', 'actual_model', 'model_type', 'rpm_limit', 'tpm_limit',
        'rpd_limit', 'tpd_limit', 'rpm_month_limit', 'tpm_month_limit',
        'timeout', 'max_retries', 'supports_function_calling', 'skills',
        'model_size_b', 'max_input_tokens', 'max_output_tokens', 'description'
    ]
    updates = {}
    for field in allowed_fields:
        if field in request.json:
            updates[field] = request.json[field]
            
    model_data = AIModel(db).update(model_id, **updates)
    return ('', 204) if model_data else ('', 404)

@bp.route('/models/<int:model_id>', methods=['DELETE'])
def delete_model_by_id(model_id):
    """Delete a specific model by ID"""
    if AIModel(db).delete(model_id):
        return '', 204
    return '', 404