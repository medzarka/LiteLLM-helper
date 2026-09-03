import os
import json
import io
import hmac
import time
import datetime
from threading import Lock
from flask import Flask, render_template, redirect, url_for, request, jsonify, session, send_file

from werkzeug.middleware.proxy_fix import ProxyFix

# Rate limiting data structure for login attempts (IP -> {count, lockout_until})
_FAILED_LOGINS = {}
_LOGIN_LOCK = Lock()
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_SECONDS = 900  # 15 minutes lockout after 5 failed attempts

class PrefixMiddleware:
    def __init__(self, wsgi_app, prefix=''):
        self.wsgi_app = wsgi_app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        prefix = environ.get('HTTP_X_FORWARDED_PREFIX', self.prefix)
        if prefix:
            environ['SCRIPT_NAME'] = prefix.rstrip('/')
            path_info = environ.get('PATH_INFO', '')
            if path_info.startswith(prefix):
                environ['PATH_INFO'] = path_info[len(prefix):] or '/'
        return self.wsgi_app(environ, start_response)

def create_app():
    app = Flask(__name__, 
                instance_relative_config=False,
                template_folder='templates')
    
    # Enable reverse proxy prefix and header support (for Traefik /litellm-helper prefix and HTTPS)
    app.wsgi_app = PrefixMiddleware(app.wsgi_app, prefix=os.environ.get('URL_PREFIX', ''))
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key')
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(hours=12)
    app.config['LITELLM_HELPER_PASSWORD'] = _load_password()
    if not app.config['LITELLM_HELPER_PASSWORD']:
        raise RuntimeError(
            'LITELLM_HELPER_PASSWORD is not set -> refusing to start the app unprotected.\n'
            'Fix: copy litellm_helper/v3/.env.example to litellm_helper/v3/.env and set a password,\n'
            'or export the LITELLM_HELPER_PASSWORD environment variable before starting.'
        )

    @app.after_request
    def _set_security_headers(response):
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        return response

    @app.before_request
    def _require_login():
        if request.endpoint in ('login', 'logout', 'static', 'keys.usage_webhook'):
            return

        # 1. Authelia SSO ForwardAuth Integration
        remote_user = request.headers.get('Remote-User')
        if remote_user:
            session['authenticated'] = True
            session['user'] = remote_user
            session['auth_type'] = 'authelia'
            remote_groups = [g.strip() for g in request.headers.get('Remote-Groups', '').split(',') if g.strip()]
            session['groups'] = remote_groups
            return

        # 2. Existing Session Check (e.g. from passkey login)
        if session.get('authenticated'):
            return

        # 3. Fallback: Check if app has password configured, else redirect to login
        if not app.config.get('LITELLM_HELPER_PASSWORD'):
            return

        return redirect(url_for('login'))

    from functools import wraps

    def require_login(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('authenticated'):
                return jsonify({'error': 'Unauthorized'}), 401
            return f(*args, **kwargs)
        return decorated_function
    
    # Register API blueprints
    try:
        from .services.providers import bp as providers_bp
        from .services.keys import bp as keys_bp
        from .services.models import bp as models_bp
        from .services.emails import bp as emails_bp
    except (ImportError, ValueError):
        from services.providers import bp as providers_bp
        from services.keys import bp as keys_bp
        from services.models import bp as models_bp
        from services.emails import bp as emails_bp
    
    app.register_blueprint(providers_bp, url_prefix='/api')
    app.register_blueprint(keys_bp, url_prefix='/api')
    app.register_blueprint(models_bp, url_prefix='/api')
    app.register_blueprint(emails_bp, url_prefix='/api')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        client_ip = request.remote_addr or '127.0.0.1'
        now = time.time()

        with _LOGIN_LOCK:
            record = _FAILED_LOGINS.get(client_ip, {'count': 0, 'lockout_until': 0})
            if record['lockout_until'] > now:
                remaining_mins = int((record['lockout_until'] - now) // 60) + 1
                return render_template(
                    'login.html',
                    error=f'Too many failed login attempts. Account temporarily locked out for security. Try again in {remaining_mins} minute(s).'
                ), 429

        if request.method == 'POST':
            pw = request.form.get('password', '')
            expected_pw = app.config.get('LITELLM_HELPER_PASSWORD', '')

            # Use constant-time comparison to protect against timing attacks
            is_valid = False
            if pw and expected_pw:
                is_valid = hmac.compare_digest(pw.encode('utf-8'), expected_pw.encode('utf-8'))

            if is_valid:
                with _LOGIN_LOCK:
                    _FAILED_LOGINS.pop(client_ip, None)
                session['authenticated'] = True
                session['user'] = 'Passkey'
                session['auth_type'] = 'passkey'
                session.permanent = True
                return redirect(url_for('providers'))
            else:
                with _LOGIN_LOCK:
                    record = _FAILED_LOGINS.get(client_ip, {'count': 0, 'lockout_until': 0})
                    record['count'] += 1
                    if record['count'] >= _MAX_FAILED_ATTEMPTS:
                        record['lockout_until'] = now + _LOCKOUT_SECONDS
                    _FAILED_LOGINS[client_ip] = record

                error_msg = 'Invalid password'
                if record['count'] >= 3 and record['count'] < _MAX_FAILED_ATTEMPTS:
                    remaining_attempts = _MAX_FAILED_ATTEMPTS - record['count']
                    error_msg += f' ({remaining_attempts} attempt(s) remaining before security lockout)'
                elif record['lockout_until'] > now:
                    error_msg = 'Too many failed login attempts. Access locked for 15 minutes.'

                return render_template('login.html', error=error_msg), 401 if record['lockout_until'] <= now else 429

        return render_template('login.html')

    @app.route('/logout')
    def logout():
        auth_type = session.get('auth_type')
        session.clear()
        if auth_type == 'authelia':
            root_domain = os.environ.get('ROOT_DOMAIN', 'bluewave.work')
            return redirect(f"https://auth.{root_domain}/logout")
        return redirect(url_for('login'))

    @app.teardown_appcontext
    def close_db(error):
        from flask import g
        db = g.pop('db', None)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    # Frontend Routes
    @app.route('/')
    def index():
        return redirect(url_for('providers'))
    
    @app.route('/providers')
    def providers():
        from services.models_service import ModelService
        svc = ModelService()
        providers_list = svc.get_all_providers()
        for p in providers_list:
            p['key_count'] = len(svc.get_keys_by_provider(p['id']))
            p['model_count'] = len(svc.get_models_by_provider_id(p['id']))
        import litellm
        provider_types = [p.value if hasattr(p, 'value') else str(p) for p in litellm.provider_list]
        return render_template('providers.html', providers=providers_list, active='providers', litellm_providers=provider_types)

    @app.route('/emails')
    def emails():
        try:
            from .models.models import Database, EmailAccount, Provider as DBProvider
        except (ImportError, ValueError):
            from models.models import Database, EmailAccount, Provider as DBProvider
        
        db = Database()
        emails_list = EmailAccount(db).get_all()
        providers_list = DBProvider(db).get_all()
        return render_template('emails.html', emails=emails_list, providers=providers_list, active='emails')
    
    @app.route('/keys')
    def keys():
        from services.models_service import ModelService
        svc = ModelService()
    
        provider_filter = request.args.get('provider')
        providers_list = svc.get_all_providers()
    
        if provider_filter:
            provider = svc.get_provider_by_name(provider_filter)
            if provider:
                keys_list = svc.get_keys_by_provider(provider['id'])
                for key in keys_list:
                    key['provider_name'] = provider_filter
            else:
                keys_list = []
        else:
            keys_list = []
            for p in providers_list:
                p_keys = svc.get_keys_by_provider(p['id'])
                for key in p_keys:
                    key['provider_name'] = p['name']
                keys_list.extend(p_keys)
    
        return render_template('keys.html', keys=keys_list, providers=providers_list, current_provider=provider_filter, active='keys')
    
    @app.route('/keys/provider/<provider_name>')
    def keys_provider_redirect(provider_name):
        return redirect(url_for('keys', provider=provider_name))

    @app.route('/keys-monitor')
    def keys_monitor():
        from services.models_service import ModelService
        svc = ModelService()
        providers_list = svc.get_all_providers()
        return render_template('keys_monitor.html', providers=providers_list, active='keys-monitor')

    
    @app.route('/models')
    def models():
        from services.models_service import ModelService
        svc = ModelService()

        def normalize_skills(raw_skills):
            if raw_skills is None:
                return []
            if isinstance(raw_skills, list):
                return [s.strip() for s in raw_skills if isinstance(s, str) and s.strip()]
            if isinstance(raw_skills, str):
                text = raw_skills.strip()
                if not text:
                    return []

                # Support JSON-like serialized lists when older data was stored as text.
                if text.startswith('[') and text.endswith(']'):
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, list):
                            return [s.strip() for s in parsed if isinstance(s, str) and s.strip()]
                    except (TypeError, ValueError):
                        pass

                separator = ',' if ',' in text else ('|' if '|' in text else None)
                if separator:
                    return [s.strip() for s in text.split(separator) if s.strip()]
                return [text]
            return []
        
        provider_filter = request.args.get('provider')
        skill_filter = request.args.getlist('skill')
        providers_output = svc.get_all_providers()
        all_skills = []
        
        # Pull provider metadata and associated models
        models_list = []
        for p in providers_output:
            provider_models = svc.get_models_by_provider_id(p["id"])
            for m in provider_models:
                m["provider_name"] = p["name"]
                m["skills"] = normalize_skills(m.get("skills"))
                all_skills.extend(m["skills"])
            models_list.extend(provider_models)
        
        # Apply provider filter
        if provider_filter:
            provider = svc.get_provider_by_name(provider_filter)
            if provider:
                models_list = svc.get_models_by_provider_id(provider["id"])
                for m in models_list:
                    m["provider_name"] = provider["name"]
                    m["skills"] = normalize_skills(m.get("skills"))
            else:
                models_list = []
        
        # Collect all unique skills for the dropdown
        all_skills = sorted(set(all_skills))
        
        # Filter by skills (OR logic: match any selected skill)
        if skill_filter:
            models_list = [m for m in models_list if any(s in m.get("skills", []) for s in skill_filter)]
        
        return render_template('models.html', models=models_list, providers=providers_output,
                                       current_provider=provider_filter, current_skills=skill_filter or [],
                                       all_skills=all_skills, active='models')
    
    @app.route('/models/health')
    def model_health():
        try:
            from .services.health_check import get_all_models_with_health
        except (ImportError, ValueError):
            from services.health_check import get_all_models_with_health
        models = get_all_models_with_health()
        return render_template('model_health.html', models=models, active='model_health')

    @app.route('/models/health/run', methods=['POST'])
    def run_health_check():
        try:
            from .services.health_check import run_health_check
        except (ImportError, ValueError):
            from services.health_check import run_health_check
        data = request.get_json(silent=True) or {}
        model_ids = data.get('model_ids')
        report = run_health_check(timeout=20, model_ids=model_ids)
        return jsonify(report)

    @app.route('/models/health/reset', methods=['POST'])
    def reset_health_checks():
        try:
            try:
                from .models.models import Database, AIModel
            except (ImportError, ValueError):
                from models.models import Database, AIModel
            db = Database()
            AIModel(db).reset_all_health()
            db.close()
            return jsonify({'status': 'success'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/models/provider/<provider_name>')
    def models_provider_redirect(provider_name):
        return redirect(url_for('models', provider=provider_name))
    
    @app.route('/aggregated-models')
    def aggregated_models():
        agg = compute_aggregations(only_aggregated=True)
        return render_template('aggregated_models.html',
                             aggregations=agg['aggregation_list'],
                             aggregated_count=len(agg['aggregation_list']),
                             total_models=agg['total_models'],
                             total_providers=agg['total_providers'],
                             total_active_keys=agg['total_active_keys'],
                             active='aggregated_models')

    @app.route('/aggregations', methods=['POST'])
    def create_aggregation():
        try:
            from .models.models import Database, Provider as DBProvider, AIModel
        except (ImportError, ValueError):
            from models.models import Database, Provider as DBProvider, AIModel
        try:
            data = request.get_json(silent=True) or {}
            shared_name = (data.get('shared_name') or '').strip()
            model_ids = data.get('model_ids') or []
            if not shared_name or not model_ids:
                return jsonify({'error': 'shared_name and model_ids are required'}), 400
            # Validate that the referenced model ids actually exist
            db = Database()
            existing = set()
            for p in DBProvider(db).get_all():
                for m in AIModel(db).get_by_provider(p['name']):
                    existing.add(m['id'])
            unknown = [mid for mid in model_ids if mid not in existing]
            if unknown:
                return jsonify({'error': f'Unknown model ids: {unknown}'}), 400
            overrides = load_aggregations()
            overrides = [o for o in overrides if o.get('shared_name') != shared_name]
            overrides.append({
                'shared_name': shared_name,
                'model_ids': [int(mid) for mid in model_ids],
                'skills': data.get('skills') or []
            })
            save_aggregations(overrides)
            return jsonify({'success': True, 'aggregation_list': compute_aggregations()['aggregation_list']})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/aggregations/<path:name>', methods=['PUT'])
    def update_aggregation(name):
        try:
            data = request.get_json(silent=True) or {}
            overrides = load_aggregations()
            found = next((o for o in overrides if o.get('shared_name') == name), None)
            if not found:
                return jsonify({'error': 'Aggregation not found'}), 404
            if data.get('shared_name'):
                found['shared_name'] = data['shared_name']
            if 'model_ids' in data:
                found['model_ids'] = [int(mid) for mid in data['model_ids']]
            if 'skills' in data:
                found['skills'] = data['skills']
            save_aggregations(overrides)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/aggregations/<path:name>', methods=['DELETE'])
    def delete_aggregation(name):
        overrides = load_aggregations()
        new = [o for o in overrides if o.get('shared_name') != name]
        if len(new) == len(overrides):
            return jsonify({'error': 'Aggregation not found'}), 404
        save_aggregations(new)
        return jsonify({'success': True})

    @app.route('/key-rotation')
    def key_rotation():
        agg = compute_aggregations()
        rotation_settings = load_rotation_settings()
        return render_template('key_rotation.html',
                             rotation_settings=rotation_settings,
                             aggregated_models=agg['aggregation_list'],
                             active='key_rotation')

    @app.route('/key-rotation/settings', methods=['POST'])
    def save_key_rotation_settings():
        try:
            data = request.get_json(silent=True) or {}
            settings = load_rotation_settings()
            for key in ('routing_strategy', 'key_rotation_strategy'):
                if key in data:
                    settings[key] = data[key]
            for key in ('cooldown_time', 'allowed_fails', 'num_retries'):
                if key in data:
                    try:
                        settings[key] = int(data[key])
                    except (TypeError, ValueError):
                        pass
            save_rotation_settings(settings)
            return jsonify({'success': True, 'settings': settings})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/key-rotation/test')
    def test_key_rotation():
        try:
            from .models.models import Database, APIKey as DBAPIKey, Provider as DBProvider
        except (ImportError, ValueError):
            from models.models import Database, APIKey as DBAPIKey, Provider as DBProvider
        model_name = request.args.get('model')
        if not model_name:
            return jsonify({'error': 'model parameter required'}), 400
        agg = compute_aggregations()
        target = next((a for a in agg['aggregation_list'] if a['shared_name'] == model_name), None)
        if not target:
            return jsonify({'error': f'Model {model_name} not found'}), 404
        db = Database()
        key_pool = []
        for provider_name in target['providers'].keys():
            provider_obj = DBProvider(db).get_by_name(provider_name)
            if not provider_obj:
                continue
            keys = DBAPIKey(db).get_by_provider(provider_obj['id'])
            for k in keys:
                if k['is_active']:
                    key_pool.append({'provider': provider_name, 'key_name': k['key_name']})
        if not key_pool:
            return jsonify({'warning': 'No active keys available for this model', 'sequence': []})
        sequence = [key_pool[i % len(key_pool)] for i in range(min(5, len(key_pool)))]
        return jsonify({'sequence': sequence})

    @app.route('/fallbacks')
    def fallbacks_page():
        try:
            from .services.models_service import ModelService
        except (ImportError, ValueError):
            from services.models_service import ModelService
        svc = ModelService()
        fallbacks_list = svc.get_all_fallbacks()
        
        # Collect available model names (individual model names + aggregated shared names)
        all_models_set = set()
        providers = svc.get_all_providers()
        for p in providers:
            p_models = svc.get_models_by_provider_id(p['id'])
            for m in p_models:
                all_models_set.add(m['name'])
        
        agg = compute_aggregations()
        for a in agg.get('aggregation_list', []):
            all_models_set.add(a['shared_name'])
            
        svc.close()
        sorted_models = sorted(all_models_set)
        
        return render_template(
            'fallbacks.html',
            fallbacks=fallbacks_list,
            available_models=sorted_models,
            active='fallbacks'
        )

    @app.route('/api/fallbacks', methods=['GET'])
    def get_fallbacks_api():
        try:
            from .services.models_service import ModelService
        except (ImportError, ValueError):
            from services.models_service import ModelService
        svc = ModelService()
        fallbacks = svc.get_all_fallbacks()
        svc.close()
        return jsonify(fallbacks)

    @app.route('/api/fallbacks', methods=['POST'])
    def save_fallback_api():
        try:
            from .services.models_service import ModelService
        except (ImportError, ValueError):
            from services.models_service import ModelService
        try:
            data = request.get_json(silent=True) or {}
            primary_model = (data.get('primary_model') or '').strip()
            fallback_models = data.get('fallback_models') or []
            
            if not primary_model:
                return jsonify({'error': 'primary_model is required'}), 400
            if not isinstance(fallback_models, list) or not fallback_models:
                return jsonify({'error': 'fallback_models list cannot be empty'}), 400
                
            # Exclude primary_model from its own fallback list if present
            fallback_models = [m for m in fallback_models if m != primary_model]
            
            svc = ModelService()
            svc.save_fallback(primary_model, fallback_models)
            svc.close()
            return jsonify({'success': True, 'primary_model': primary_model, 'fallback_models': fallback_models})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/api/fallbacks/clear', methods=['DELETE'])
    def clear_all_fallbacks_api():
        try:
            from .services.models_service import ModelService
        except (ImportError, ValueError):
            from services.models_service import ModelService
        try:
            svc = ModelService()
            count = svc.clear_all_fallbacks()
            svc.close()
            return jsonify({'success': True, 'count': count})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/api/fallbacks/<path:primary_model>', methods=['DELETE'])
    def delete_fallback_api(primary_model):
        try:
            from .services.models_service import ModelService
        except (ImportError, ValueError):
            from services.models_service import ModelService
        try:
            svc = ModelService()
            deleted = svc.delete_fallback(primary_model)
            svc.close()
            if deleted:
                return jsonify({'success': True})
            return jsonify({'error': 'Fallback rule not found'}), 404
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/api/fallbacks/smart-generate', methods=['POST'])
    def smart_generate_fallbacks_api():
        try:
            try:
                from .services.smart_fallbacks import apply_smart_fallbacks
            except (ImportError, ValueError):
                from services.smart_fallbacks import apply_smart_fallbacks
            
            saved_count = apply_smart_fallbacks()
            return jsonify({'success': True, 'saved_count': saved_count})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/api/models/<int:model_id>/autofill', methods=['POST'])
    def autofill_model_metadata_api(model_id):
        try:
            try:
                from .services.metadata_extractor import autofill_model_specs_in_db
            except (ImportError, ValueError):
                from services.metadata_extractor import autofill_model_specs_in_db

            autofill_model_specs_in_db(model_id)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/api/models/autofill-all', methods=['POST'])
    def autofill_all_models_api():
        try:
            try:
                from .models.models import Database, AIModel
                from .services.metadata_extractor import autofill_model_specs_in_db
            except (ImportError, ValueError):
                from models.models import Database, AIModel
                from services.metadata_extractor import autofill_model_specs_in_db

            db = Database()
            cursor = db.conn.cursor()
            cursor.execute('SELECT id FROM model')
            rows = cursor.fetchall()
            db.close()

            updated = 0
            for r in rows:
                autofill_model_specs_in_db(r[0])
                updated += 1

            return jsonify({'success': True, 'updated_count': updated})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/api/providers/sync-free-models', methods=['POST'])
    def sync_free_models_api():
        try:
            try:
                from .services.provider_sync import sync_openrouter_free_models
            except (ImportError, ValueError):
                from services.provider_sync import sync_openrouter_free_models

            result = sync_openrouter_free_models()
            return jsonify(result)
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/api/providers/free-catalog', methods=['GET'])
    def get_free_catalog_api():
        try:
            try:
                from .services.provider_sync import get_free_tier_catalog
            except (ImportError, ValueError):
                from services.provider_sync import get_free_tier_catalog

            catalog = get_free_tier_catalog()
            return jsonify(catalog)
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    @app.route('/hermes-agents', methods=['GET', 'POST'])
    def hermes_agents():
        try:
            from .services.hermes import load_hermes_agents, save_hermes_agents
        except (ImportError, ValueError):
            from services.hermes import load_hermes_agents, save_hermes_agents
            
        try:
            from .models.models import Database, AIModel
        except (ImportError, ValueError):
            from models.models import Database, AIModel
            
        if request.method == 'POST':
            new_mappings = {}
            for key, value in request.form.items():
                if value and (key.startswith('primary_hermes-') or key.startswith('fallback_hermes-')):
                    prefix, task_id = key.split('_', 1)
                    val = int(value) if value.isdigit() else value
                    if task_id not in new_mappings:
                        new_mappings[task_id] = {}
                    new_mappings[task_id][prefix] = val
            save_hermes_agents(new_mappings)
            return redirect(url_for('hermes_agents'))
            
        current_agents = load_hermes_agents()
        # Convert legacy flat format to dict format for the template
        for k, v in current_agents.items():
            if not isinstance(v, dict):
                current_agents[k] = {'primary': v}
        db = Database()
        try:
            from .models.models import Provider as DBProvider
        except (ImportError, ValueError):
            from models.models import Provider as DBProvider
            
        provider_obj = DBProvider(db)
        model_obj = AIModel(db)
        
        models = []
        for provider in provider_obj.get_all():
            models.extend(model_obj.get_by_provider(provider['name']))
            
        db.close()
        
        # Add aggregated models to the dropdown list
        try:
            agg_data = compute_aggregations(only_aggregated=True)
            for agg in agg_data.get('aggregation_list', []):
                models.append({
                    'id': agg['shared_name'],
                    'name': agg['shared_name'],
                    'provider_name': 'Aggregated',
                    'actual_model': agg['shared_name'],
                    'skills': list(agg.get('skills', []))
                })
        except Exception as e:
            print(f"Error loading aggregations for hermes_agents: {e}")
        return render_template('hermes_agents.html', models=models, current_agents=current_agents, active='hermes')

    @app.route('/export-config')
    def export_config():
        try:
            from .services.versions import list_versions
        except (ImportError, ValueError):
            from services.versions import list_versions
        versions = list_versions()
        return render_template('export_config.html', versions=versions, active='export')
    
    @app.route('/export/preview')
    def export_preview():
        try:
            from .services.export import generate_config
        except (ImportError, ValueError):
            from services.export import generate_config
    
        format_type = request.args.get('format', 'yaml')
        include_router = request.args.get('include_router') != 'false'
        include_general = request.args.get('include_general') != 'false'
        include_litellm = request.args.get('include_litellm') != 'false'
        include_individual = request.args.get('include_individual') in ('true', 'on', '1', 'yes')
        include_health_checks = request.args.get('include_health_checks') != 'false'
        include_fallbacks = request.args.get('include_fallbacks') != 'false'
        include_aggregations = request.args.get('include_aggregations') != 'false'
        exclude_unhealthy = request.args.get('exclude_unhealthy') in ('true', 'on', '1', 'yes')
        include_cache = request.args.get('include_cache') in ('true', 'on', '1', 'yes')

        config = generate_config(
            include_router=include_router,
            include_general=include_general,
            include_litellm=include_litellm,
            include_individual=include_individual,
            include_health_checks=include_health_checks,
            include_fallbacks=include_fallbacks,
            include_aggregations=include_aggregations,
            exclude_unhealthy=exclude_unhealthy,
            include_cache=include_cache
        )
    
        if format_type == 'yaml':
            import yaml
            content = yaml.dump(config, default_flow_style=False, sort_keys=False)
        else:
            content = json.dumps(config, indent=2)
    
        return jsonify({
            'format': format_type,
            'content': content
        })
    
    @app.route('/export/generate')
    def export_generate():
        try:
            from .services.export import generate_config
        except (ImportError, ValueError):
            from services.export import generate_config
        
        format_type = request.args.get('format', 'yaml')
        include_router = request.args.get('include_router') != 'false'
        include_general = request.args.get('include_general') != 'false'
        include_litellm = request.args.get('include_litellm') != 'false'
        include_individual = request.args.get('include_individual') in ('true', 'on', '1', 'yes')
        include_health_checks = request.args.get('include_health_checks') != 'false'
        include_fallbacks = request.args.get('include_fallbacks') != 'false'
        include_aggregations = request.args.get('include_aggregations') != 'false'
        exclude_unhealthy = request.args.get('exclude_unhealthy') in ('true', 'on', '1', 'yes')
        include_cache = request.args.get('include_cache') in ('true', 'on', '1', 'yes')
        
        config = generate_config(
            include_router=include_router,
            include_general=include_general,
            include_litellm=include_litellm,
            include_individual=include_individual,
            include_health_checks=include_health_checks,
            include_fallbacks=include_fallbacks,
            include_aggregations=include_aggregations,
            exclude_unhealthy=exclude_unhealthy,
            include_cache=include_cache
        )
        
        if format_type == 'yaml':
            import yaml
            content = yaml.dump(config, default_flow_style=False, sort_keys=False)
            mimetype = 'text/yaml'
            filename = 'litellm-config.yaml'
        else:
            content = json.dumps(config, indent=2)
            mimetype = 'application/json'
            filename = 'litellm-config.json'
        
        return send_file(
            io.BytesIO(content.encode('utf-8')),
            mimetype=mimetype,
            as_attachment=True,
            download_name=filename
        )
    
    @app.route('/export/sync-live', methods=['POST'])
    def export_sync_live():
        try:
            from .services.export import generate_config, sync_to_shared_volume
        except (ImportError, ValueError):
            from services.export import generate_config, sync_to_shared_volume
        
        format_type = request.args.get('format', 'yaml')
        include_router = request.args.get('include_router') != 'false'
        include_general = request.args.get('include_general') != 'false'
        include_litellm = request.args.get('include_litellm') != 'false'
        include_individual = request.args.get('include_individual') in ('true', 'on', '1', 'yes')
        include_health_checks = request.args.get('include_health_checks') != 'false'
        include_fallbacks = request.args.get('include_fallbacks') != 'false'
        include_aggregations = request.args.get('include_aggregations') != 'false'
        exclude_unhealthy = request.args.get('exclude_unhealthy') in ('true', 'on', '1', 'yes')
        include_cache = request.args.get('include_cache') in ('true', 'on', '1', 'yes')
        
        try:
            config = generate_config(
                include_router=include_router,
                include_general=include_general,
                include_litellm=include_litellm,
                include_individual=include_individual,
                include_health_checks=include_health_checks,
                include_fallbacks=include_fallbacks,
                include_aggregations=include_aggregations,
                exclude_unhealthy=exclude_unhealthy,
                include_cache=include_cache
            )
            
            result = sync_to_shared_volume(config, format_type)
            return jsonify(result)
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 400
    
    @app.route('/versions', methods=['POST'])
    def create_version():
        try:
            from .services.versions import save_version
        except (ImportError, ValueError):
            from services.versions import save_version
        name = (request.form.get('name') or '').strip()
        description = (request.form.get('description') or '').strip()
        if not name:
            return redirect(url_for('export_config'))
        save_version(name, description)
        return redirect(url_for('export_config'))

    @app.route('/versions/<path:file>/restore', methods=['POST'])
    def restore_version(file):
        try:
            from .services.versions import load_version, apply_state
        except (ImportError, ValueError):
            from services.versions import load_version, apply_state
        state = load_version(file)
        if state:
            apply_state(state)
        return redirect(url_for('export_config'))

    @app.route('/versions/<path:file>/delete', methods=['POST'])
    def delete_version_route(file):
        try:
            from .services.versions import delete_version
        except (ImportError, ValueError):
            from services.versions import delete_version
        delete_version(file)
        return redirect(url_for('export_config'))
    
    @app.route('/update-model-timeouts', methods=['POST'])
    @require_login
    def update_model_timeouts():
        """Update timeout and stream_timeout for all models"""
        try:
            from .models.models import Database, AIModel
        except (ImportError, ValueError):
            from models.models import Database, AIModel
        try:
            db = Database()
            model_obj = AIModel(db)
            count = model_obj.update_all_models_timeout(timeout=600.0, stream_timeout=300.0)
            return jsonify({
                'status': 'success',
                'updated_count': count
            }), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        finally:
            if 'db' in locals():
                db.close()
    
    @app.route('/documentation')
    def documentation():
        return render_template('documentation.html', active='documentation')

    # Initialize APScheduler for background jobs
    from flask_apscheduler import APScheduler
    scheduler = APScheduler()
    scheduler.init_app(app)
    
    # Weekly operations & model updates digest: configurable schedule (default: Monday at 06:00 AM)
    from services.notifications import send_weekly_digest
    digest_day = os.environ.get('DIGEST_DAY_OF_WEEK', 'mon')
    try:
        digest_hour = int(os.environ.get('DIGEST_HOUR', '6'))
    except (ValueError, TypeError):
        digest_hour = 6
    try:
        digest_minute = int(os.environ.get('DIGEST_MINUTE', '0'))
    except (ValueError, TypeError):
        digest_minute = 0

    scheduler.add_job(
        id='weekly_digest_job',
        func=send_weekly_digest,
        trigger='cron',
        day_of_week=digest_day,
        hour=digest_hour,
        minute=digest_minute
    )

    scheduler.start()

    return app


# Helper functions for templates
def keys_by_provider(provider_id):
    try:
        from .models.models import Database, APIKey
    except (ImportError, ValueError):
        from models.models import Database, APIKey
    db = Database()
    return APIKey(db).get_by_provider(provider_id)

def models_by_provider(provider_id):
    try:
        from .models.models import Database, AIModel
    except (ImportError, ValueError):
        from models.models import Database, AIModel
    db = Database()
    return AIModel(db).get_by_provider_by_id(provider_id)

# Load the access password from the environment or a local .env file.
def _load_password():
    # 1. OS environment variable takes precedence
    pw = os.environ.get('LITELLM_HELPER_PASSWORD')
    if pw:
        return pw

    # 2. Candidate directories where a .env file might live
    here = os.path.dirname(os.path.abspath(__file__))
    candidate_dirs = [
        here,                                   # litellm_helper/v3/ (next to .env.example)
        os.getcwd(),                            # current working directory
        os.path.dirname(os.path.dirname(here)), # project root
    ]

    # 3. Try python-dotenv if it happens to be installed (loads .env from CWD)
    try:
        from dotenv import load_dotenv
        load_dotenv()
        pw = os.environ.get('LITELLM_HELPER_PASSWORD')
        if pw:
            return pw
    except Exception:
        pass

    # 4. Fallback: minimally parse a .env file ourselves in any candidate dir
    for d in candidate_dirs:
        env_path = os.path.join(d, '.env')
        if os.path.exists(env_path):
            try:
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '=' in line:
                            k, v = line.split('=', 1)
                            if k.strip() == 'LITELLM_HELPER_PASSWORD':
                                val = v.strip().strip('"').strip("'")
                                if val:
                                    return val
            except Exception:
                pass
    return None

app = create_app()
# Rotation settings persistence (loaded by the key-rotation view, saved via /key-rotation/settings)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROTATION_SETTINGS_FILE = os.path.join(BASE_DIR, 'rotation_settings.json')

def load_rotation_settings():
    defaults = {
        'routing_strategy': 'simple-shuffle',
        'key_rotation_strategy': 'round-robin',
        'cooldown_time': 60,
        'allowed_fails': 2,
        'num_retries': 1,
    }
    if os.path.exists(ROTATION_SETTINGS_FILE):
        try:
            with open(ROTATION_SETTINGS_FILE) as f:
                data = json.load(f)
            for k, v in data.items():
                if k in defaults:
                    defaults[k] = v
        except Exception:
            pass
    return defaults

# Aggregation overrides persistence (rename / merge models under a shared name)
AGGREGATIONS_FILE = os.path.join(BASE_DIR, 'aggregations.json')

def load_aggregations():
    if os.path.exists(AGGREGATIONS_FILE):
        try:
            with open(AGGREGATIONS_FILE) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []

def save_aggregations(data):
    try:
        with open(AGGREGATIONS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def save_rotation_settings(settings):
    try:
        with open(ROTATION_SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass

def compute_aggregations(only_aggregated=False):
    try:
        from .models.models import Database, AIModel as DBAIModel, APIKey as DBAPIKey, Provider as DBProvider
    except (ImportError, ValueError):
        from models.models import Database, AIModel as DBAIModel, APIKey as DBAPIKey, Provider as DBProvider
    db = Database()

    # Apply user-defined aggregation overrides (rename / merge models under a shared name)
    overrides = load_aggregations()
    id_to_shared = {}
    shared_skills = {}
    for ov in overrides:
        shared_skills[ov['shared_name']] = ov.get('skills', []) or []
        for mid in ov.get('model_ids', []):
            id_to_shared[mid] = ov['shared_name']

    all_models = []
    providers_list = DBProvider(db).get_all()
    for provider in providers_list:
        models = DBAIModel(db).get_by_provider_by_id(provider['id'])
        for model in models:
            model['provider_name'] = provider['name']
            model['provider_api_base'] = provider['api_base']
            all_models.append(model)

    aggregations = {}
    for model in all_models:
        shared_name = id_to_shared.get(model['id'], model['name'])
        if shared_name not in aggregations:
            aggregations[shared_name] = {
                'shared_name': shared_name,
                'providers': {},
                'models': [],
                'total_models': 0,
                'total_keys': 0,
                'total_rpm': 0,
                'total_tpm': 0,
                'skills': set()
            }

        if model['provider_name'] not in aggregations[shared_name]['providers']:
            # Count this provider's active keys ONCE per aggregation (not once per model)
            active_count = 0
            provider_obj = DBProvider(db).get_by_name(model['provider_name'])
            if provider_obj:
                keys = DBAPIKey(db).get_by_provider(provider_obj['id'])
                active_count = len([k for k in keys if k['is_active']])
            aggregations[shared_name]['providers'][model['provider_name']] = {
                'api_base': model['provider_api_base'],
                'models': [],
                'keys': active_count
            }
            aggregations[shared_name]['total_keys'] += active_count

        aggregations[shared_name]['providers'][model['provider_name']]['models'].append({
            'actual_model': model['actual_model'],
            'model_type': model['model_type'],
            'rpm': model['rpm_limit'],
            'tpm': model['tpm_limit'],
            'timeout': model['timeout'],
            'max_retries': model['max_retries'],
            'function_calling': model['supports_function_calling'],
            'skills': model['skills']
        })

        aggregations[shared_name]['total_rpm'] += model.get('rpm_limit', 0) or 0
        aggregations[shared_name]['total_tpm'] += model.get('tpm_limit', 0) or 0

        aggregations[shared_name]['models'].append(model)
        aggregations[shared_name]['total_models'] += 1

        for skill in model['skills']:
            aggregations[shared_name]['skills'].add(skill)

    aggregation_list = []
    for agg in aggregations.values():
        for skill in shared_skills.get(agg['shared_name'], []):
            agg['skills'].add(skill)
        agg['skills'] = sorted(agg['skills'])
        agg['provider_count'] = len(agg['providers'])
        aggregation_list.append(agg)

    # When only_aggregated is requested (the Aggregated Models page), drop the
    # single-model "aggregations" that are really just plain per-provider models
    # -- those belong on the Models page, not here.
    if only_aggregated:
        aggregation_list = [
            agg for agg in aggregation_list
            if any(m['id'] in id_to_shared for m in agg['models'])
        ]

    # Summaries are computed from the aggregation_list actually being returned.
    # Distinct active keys across the providers involved, counted once per
    # provider (not once per aggregation) so a provider's key pool is not
    # double-counted across multiple aggregations.
    involved_providers = set()
    for agg in aggregation_list:
        involved_providers.update(agg['providers'].keys())
    total_models = sum(agg['total_models'] for agg in aggregation_list)
    total_active_keys = 0
    for pname in involved_providers:
        p = DBProvider(db).get_by_name(pname)
        if p:
            pkeys = DBAPIKey(db).get_by_provider(p['id'])
            total_active_keys += len([k for k in pkeys if k['is_active']])

    return {
        'aggregation_list': aggregation_list,
        'total_models': total_models,
        'total_providers': len(involved_providers),
        'total_active_keys': total_active_keys,
    }

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5001)