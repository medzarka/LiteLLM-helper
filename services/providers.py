from flask import Blueprint, request, jsonify
try:
    from ..models.models import Database, Provider as DBProvider
except (ImportError, ValueError):
    from models.models import Database, Provider as DBProvider

bp = Blueprint('providers', __name__)
db = Database()

@bp.route('/providers', methods=['POST'])
def create_provider():
    try:
        data = request.get_json()
        provider_obj = DBProvider(db)
        provider_id = provider_obj.create(
            name=data['name'],
            api_base=data.get('api_base', ''),
            description=data.get('description', '')
        )
        return jsonify({
            'id': provider_id,
            'name': data['name'],
            'api_base': data.get('api_base', ''),
            'description': data.get('description', '')
        }), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@bp.route('/providers', methods=['GET'])
def list_providers():
    providers = DBProvider(db).get_all()
    return jsonify(providers)

@bp.route('/providers/<int:provider_id>', methods=['GET'])
def get_provider(provider_id):
    """Get provider by ID for consistency in v3 API"""
    provider = DBProvider(db).get_by_id(provider_id)
    return jsonify(provider) if provider else ('', 404)

@bp.route('/providers/<int:provider_id>', methods=['PUT'])
def update_provider(provider_id):
    try:
        provider = DBProvider(db).get_by_id(provider_id)
        if not provider:
            return jsonify({'error': f'Provider not found: {provider_id}'}), 404
        
        DBProvider(db).update(
                    provider['id'],
                    name=request.json.get('name'),
                    api_base=request.json.get('api_base', provider.get('api_base')),
                    description=request.json.get('description', provider.get('description'))
                )
        updated = DBProvider(db).get_by_id(provider_id)
        return jsonify(updated), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@bp.route('/providers/<int:provider_id>', methods=['DELETE'])
def delete_provider(provider_id):
    provider = DBProvider(db).get_by_id(provider_id)
    if provider and DBProvider(db).delete(provider['id']):
        return '', 204
    return '', 404