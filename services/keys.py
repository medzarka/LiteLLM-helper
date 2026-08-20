from flask import Blueprint, request, jsonify
try:
    from ..models.models import Database, APIKey, Provider as DBProvider, AIModel
except (ImportError, ValueError):
    from models.models import Database, APIKey, Provider as DBProvider, AIModel

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
        
        # Try to resolve email_id. Fallback to a default one if not found.
        try:
            from ..models.models import EmailAccount
        except (ImportError, ValueError):
            from models.models import EmailAccount
        email_svc = EmailAccount(db)
        email_map = {e['email']: e['id'] for e in email_svc.get_all()}
        email_id = email_map.get(data['key_name'])
        if not email_id:
            # Create a fallback account for keys created manually
            try:
                email_id = email_svc.create(email=f"auto_{data['key_name']}@local", email_type="other")
            except:
                emails = email_svc.get_all()
                email_id = emails[0]['id'] if emails else 0
        
        key_service = APIKey(db)
        key_id = key_service.create(
            provider_id,
            email_id,
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

@bp.route('/keys/<int:key_id>', methods=['PUT'])
def update_key(key_id):
    """Update key details (name, value, status)"""
    data = request.get_json()
    is_active = data.get('is_active')
    key_name = data.get('key_name')
    key_value = data.get('key_value')
    
    if APIKey(db).update(key_id, is_active=is_active, key_name=key_name, key_value=key_value):
        return '', 204
    return '', 404

@bp.route('/keys/<int:key_id>/status', methods=['PUT'])
def update_key_status(key_id):
    """Update key active status"""
    is_active = request.json.get('is_active')
    if APIKey(db).update(key_id, is_active=is_active):
        return '', 204
    return '', 404

@bp.route('/providers/<provider_name>/keys/usage', methods=['GET'])
def list_keys_usage(provider_name):
    import datetime
    import json
    import os
    import hashlib
    import redis

    provider_obj = DBProvider(db).get_by_name(provider_name)
    if not provider_obj:
        return jsonify({'error': f'Provider {provider_name} not found'}), 404
        
    provider_id = provider_obj['id']
    keys = APIKey(db).get_by_provider(provider_id)
    models = AIModel(db).get_by_provider_by_id(provider_id)
    
    model_id = request.args.get('model_id')
    if model_id:
        models = [m for m in models if str(m['id']) == str(model_id)]
    
    total_rpm_limit = sum([m.get('rpm_limit') or 0 for m in models])
    total_rpd_limit = sum([m.get('rpd_limit') or 0 for m in models])
    total_rpm_month_limit = sum([m.get('rpm_month_limit') or 0 for m in models])

    total_tpm_limit = sum([m.get('tpm_limit') or 0 for m in models])
    total_tpd_limit = sum([m.get('tpd_limit') or 0 for m in models])
    total_tpm_month_limit = sum([m.get('tpm_month_limit') or 0 for m in models])
    
    redis_host = os.environ.get('REDIS_HOST', 'litellm-redis')
    redis_port = int(os.environ.get('REDIS_PORT', 6379))
    redis_password = os.environ.get('REDIS_PASSWORD', '')
    
    r = None
    try:
        r = redis.Redis(host=redis_host, port=redis_port, password=redis_password, decode_responses=True)
        r.ping()
    except Exception:
        r = None

    now = datetime.datetime.now(datetime.timezone.utc)
    month_tag = now.strftime("%Y-%m")

    usage_data = []
    for key in keys:
        key_value = key.get('key_value', '')
        hashed_key = hashlib.sha256(key_value.encode()).hexdigest()
        
        current_rpm = 0
        current_rpd = 0
        current_rpm_month = 0
        current_tpm = 0
        current_tpd = 0
        current_tpm_month = 0
        upstream_rate_limits = None
        
        if r:
            try:
                rpm_val = r.get(f"rpm:{hashed_key}") or r.get(f"rpm:{key_value}") or 0
                rpd_val = r.get(f"rpd:{hashed_key}") or r.get(f"rpd:{key_value}") or 0
                rpm_month_val = r.get(f"rpm_month:{month_tag}:{hashed_key}") or r.get(f"rpm_month:{hashed_key}") or r.get(f"rpm_month:{key_value}") or 0
                
                tpm_val = r.get(f"tpm:{hashed_key}") or r.get(f"tpm:{key_value}") or 0
                tpd_val = r.get(f"tpd:{hashed_key}") or r.get(f"tpd:{key_value}") or 0
                tpm_month_val = r.get(f"tpm_month:{month_tag}:{hashed_key}") or r.get(f"tpm_month:{hashed_key}") or r.get(f"tpm_month:{key_value}") or 0

                current_rpm = int(rpm_val)
                current_rpd = int(rpd_val)
                current_rpm_month = int(rpm_month_val)

                current_tpm = int(tpm_val)
                current_tpd = int(tpd_val)
                current_tpm_month = int(tpm_month_val)

                hdr_raw = r.get(f"upstream_hdr:{hashed_key}")
                if hdr_raw:
                    try:
                        upstream_rate_limits = json.loads(hdr_raw)
                    except Exception:
                        pass
            except Exception:
                pass
                
        usage_data.append({
            'id': key['id'],
            'key_name': key['key_name'],
            'is_active': key['is_active'],
            'rate_limit_scope': provider_obj.get('rate_limit_scope', 'cumulative'),
            'rpm_limit': total_rpm_limit,
            'rpd_limit': total_rpd_limit,
            'rpm_month_limit': total_rpm_month_limit,
            'tpm_limit': total_tpm_limit,
            'tpd_limit': total_tpd_limit,
            'tpm_month_limit': total_tpm_month_limit,
            'current_rpm': current_rpm,
            'current_rpd': current_rpd,
            'current_rpm_month': current_rpm_month,
            'current_tpm': current_tpm,
            'current_tpd': current_tpd,
            'current_tpm_month': current_tpm_month,
            'upstream_rate_limits': upstream_rate_limits,
        })
        
    return jsonify(usage_data)

@bp.route('/usage/webhook', methods=['POST'])
def usage_webhook():
    import os
    import redis
    import json
    import re
    import datetime
    
    expected_token = os.environ.get('LITELLM_HELPER_PASSWORD')
    if request.args.get('token') != expected_token:
        return jsonify({'error': 'Unauthorized'}), 401
        
    raw_payload = request.get_json(force=True, silent=True)
    if raw_payload is None:
        raw_payload = {}
        
    try:
        with open('/app/data/last_received_webhook.json', 'w') as f:
            json.dump(raw_payload, f, indent=2)
    except Exception as e:
        print("Could not write debug file:", e)
        
    # Standardize to a list of events (since generic_api sends json_array)
    if isinstance(raw_payload, list):
        events = raw_payload
    elif isinstance(raw_payload, dict):
        events = [raw_payload]
    else:
        events = []
        
    redis_host = os.environ.get('REDIS_HOST', 'litellm-redis')
    redis_port = int(os.environ.get('REDIS_PORT', 6379))
    redis_password = os.environ.get('REDIS_PASSWORD', '')
    
    processed_count = 0
    total_tokens_recorded = 0
    
    now = datetime.datetime.now(datetime.timezone.utc)
    month_tag = now.strftime("%Y-%m")
    
    try:
        r = redis.Redis(host=redis_host, port=redis_port, password=redis_password, decode_responses=True)
        
        def safe_incr(key, amount=1, ttl=86400):
            r.incrby(key, amount)
            if r.ttl(key) == -1:
                r.expire(key, ttl)

        for data in events:
            if not isinstance(data, dict):
                continue
                
            hashed_key = None
            
            # 1. Try top-level metadata
            if isinstance(data.get('metadata'), dict):
                hashed_key = data['metadata'].get('helper_hashed_key')
                
            # 2. Try litellm_params metadata
            if not hashed_key and isinstance(data.get('litellm_params'), dict):
                lp_metadata = data['litellm_params'].get('metadata')
                if isinstance(lp_metadata, dict):
                    hashed_key = lp_metadata.get('helper_hashed_key')
                    
            # 3. Try kwargs litellm_params metadata
            if not hashed_key and isinstance(data.get('kwargs'), dict):
                kw_lp = data['kwargs'].get('litellm_params')
                if isinstance(kw_lp, dict):
                    kw_lp_metadata = kw_lp.get('metadata')
                    if isinstance(kw_lp_metadata, dict):
                        hashed_key = kw_lp_metadata.get('helper_hashed_key')

            # 4. Try model_info
            if not hashed_key and isinstance(data.get('litellm_params'), dict):
                model_info = data['litellm_params'].get('model_info')
                if isinstance(model_info, dict):
                    hashed_key = model_info.get('helper_hashed_key')

            if not hashed_key and isinstance(data.get('model_info'), dict):
                hashed_key = data['model_info'].get('helper_hashed_key')
                
            # 5. Try model_id (LiteLLM sets model_id to model_info['id'])
            if not hashed_key and data.get('model_id'):
                candidate = data.get('model_id')
                if isinstance(candidate, str) and len(candidate) == 64:
                    hashed_key = candidate

            if not hashed_key and isinstance(data.get('hidden_params'), dict):
                candidate = data['hidden_params'].get('model_id')
                if isinstance(candidate, str) and len(candidate) == 64:
                    hashed_key = candidate
                
            # Fallback to search recursively if still not found
            if not hashed_key:
                payload_str = json.dumps(data)
                match = re.search(r'"helper_hashed_key"\s*:\s*"([^"]+)"', payload_str)
                if match:
                    hashed_key = match.group(1)

            if not hashed_key:
                print("Event did not contain helper_hashed_key:", json.dumps(data))
                continue
                
            # Extract usage / tokens
            usage = data.get('usage') or {}
            if not usage and isinstance(data.get('response'), dict):
                usage = data['response'].get('usage') or {}
                
            total_tokens = usage.get('total_tokens', 0) if isinstance(usage, dict) else 0

            # 1-minute, 1-day, and 35-day (calendar month) counters
            safe_incr(f"rpm:{hashed_key}", 1, 60)
            safe_incr(f"rpd:{hashed_key}", 1, 86400)
            safe_incr(f"rpm_month:{month_tag}:{hashed_key}", 1, 3024000)
            safe_incr(f"rpm_month:{hashed_key}", 1, 3024000)
            
            if total_tokens > 0:
                safe_incr(f"tpm:{hashed_key}", total_tokens, 60)
                safe_incr(f"tpd:{hashed_key}", total_tokens, 86400)
                safe_incr(f"tpm_month:{month_tag}:{hashed_key}", total_tokens, 3024000)
                safe_incr(f"tpm_month:{hashed_key}", total_tokens, 3024000)
                
            # Upstream rate limit headers extraction
            raw_headers = {}
            if isinstance(data.get('hidden_params'), dict) and isinstance(data['hidden_params'].get('additional_headers'), dict):
                raw_headers.update(data['hidden_params']['additional_headers'])
            if isinstance(data.get('additional_headers'), dict):
                raw_headers.update(data['additional_headers'])
                
            rate_limit_headers = {}
            for hk, hv in raw_headers.items():
                hk_lower = str(hk).lower().replace('-', '_')
                if any(term in hk_lower for term in ('ratelimit', 'rate_limit', 'quota', 'remaining', 'reset')):
                    rate_limit_headers[str(hk)] = str(hv)
                    
            if rate_limit_headers:
                try:
                    r.set(f"upstream_hdr:{hashed_key}", json.dumps(rate_limit_headers), ex=120)
                except Exception:
                    pass

            processed_count += 1
            total_tokens_recorded += total_tokens
            
        return jsonify({
            'status': 'success',
            'events_processed': processed_count,
            'recorded_tokens': total_tokens_recorded
        }), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

