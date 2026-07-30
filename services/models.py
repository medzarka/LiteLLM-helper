from flask import Blueprint, request, jsonify
try:
    from ..models.models import AIModel, Database, Provider as DBProvider
except (ImportError, ValueError):
    from models.models import AIModel, Database, Provider as DBProvider

bp = Blueprint('models', __name__)
db = Database()

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
            max_input_tokens=data.get('max_input_tokens')
        )
        return jsonify({
            'id': model_id,
            'message': 'Model created successfully'
        }), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

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
    model_data = AIModel(db).update(
        model_id,
        name=request.json.get('name'),
        actual_model=request.json.get('actual_model'),
        model_type=request.json.get('model_type'),
        rpm_limit=request.json.get('rpm_limit'),
        tpm_limit=request.json.get('tpm_limit'),
        rpd_limit=request.json.get('rpd_limit'),
        tpd_limit=request.json.get('tpd_limit'),
        rpm_month_limit=request.json.get('rpm_month_limit'),
        tpm_month_limit=request.json.get('tpm_month_limit'),
        timeout=request.json.get('timeout'),
        max_retries=request.json.get('max_retries'),
        supports_function_calling=request.json.get('supports_function_calling'),
        skills=request.json.get('skills', []),
        model_size_b=request.json.get('model_size_b'),
        max_input_tokens=request.json.get('max_input_tokens')
    )
    return ('', 204) if model_data else ('', 404)

@bp.route('/models/<int:model_id>', methods=['DELETE'])
def delete_model_by_id(model_id):
    """Delete a specific model by ID"""
    if AIModel(db).delete(model_id):
        return '', 204
    return '', 404