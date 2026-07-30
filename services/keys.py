from flask import Blueprint, request, jsonify
try:
    from ..models.models import Database, APIKey, Provider as DBProvider
except (ImportError, ValueError):
    from models.models import Database, APIKey, Provider as DBProvider

bp = Blueprint('keys', __name__)
db = Database()

@bp.route('/providers/<provider_name>/keys', methods=['POST'])
def create_key(provider_name):
    try:
        # Find provider ID by name
        provider_obj = DBProvider(db).get_by_name(provider_name)
        if not provider_obj:
            return jsonify({'error': f'Provider {provider_name} not found'}), 404
        provider_id = provider_obj['id']
        
        data = request.get_json()
        key_service = APIKey(db)
        key_id = key_service.create(
            provider_id,
            data['key_name'],
            data['key_value'],
            active=data.get('active', True)
        )
        return jsonify({
            'id': key_id,
            'key_name': data['key_name'],
            'is_active': data.get('active', True)
        }), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@bp.route('/providers/<provider_name>/keys', methods=['GET'])
def list_keys(provider_name):
    provider_obj = DBProvider(db).get_by_name(provider_name)
    if not provider_obj:
        return jsonify({'error': f'Provider {provider_name} not found'}), 404
    provider_id = provider_obj['id']
    keys = APIKey(db).get_by_provider(provider_id)
    return jsonify(keys)

@bp.route('/keys/<int:key_id>', methods=['DELETE'])
def delete_key(key_id):
    if APIKey(db).delete(key_id):
        return '', 204
    return '', 404

@bp.route('/keys/<int:key_id>/status', methods=['PUT'])
def update_key_status(key_id):
    """Update key active status"""
    is_active = request.json.get('is_active')
    if APIKey(db).update(key_id, is_active=is_active):
        return '', 204
    return '', 404