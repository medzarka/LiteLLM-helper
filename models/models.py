import sqlite3
import json
import os

class Database:
    def __init__(self, db_path='litellm_helper.db'):
        if db_path is None:
            db_path = 'litellm_helper.db'

        # Resolve relative DB paths against the v3 directory so runtime cwd
        # differences do not silently point to a different database.
        if not os.path.isabs(db_path):
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, 'data', db_path)


        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # Enforce foreign-key constraints so deletes cascade consistently
        self.conn.execute('PRAGMA foreign_keys = ON')
        self.create_schema()
        self.initialize_rotation_settings()

    def create_schema(self):
        """Create database schema based on v3 system description"""
        cursor = self.conn.cursor()
        
        # Providers table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS provider (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            provider_type TEXT DEFAULT 'custom',
            api_base TEXT,
            description TEXT,
            rate_limit_scope TEXT DEFAULT 'cumulative',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        '''
        )
        try:
            cursor.execute("ALTER TABLE provider ADD COLUMN rate_limit_scope TEXT DEFAULT 'cumulative'")
        except sqlite3.OperationalError:
            pass
        
        # Email Accounts table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS email_account (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL DEFAULT '',
            email_type TEXT NOT NULL DEFAULT 'other',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Rotation settings table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS rotation_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            routing_strategy TEXT NOT NULL DEFAULT 'simple-shuffle',
            key_rotation_strategy TEXT NOT NULL DEFAULT 'round-robin',
            cooldown_time INTEGER NOT NULL DEFAULT 60,
            allowed_fails INTEGER NOT NULL DEFAULT 2,
            num_retries INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # API Keys table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_key (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL,
            email_id INTEGER NOT NULL,
            key_name TEXT NOT NULL,
            key_value TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (provider_id) REFERENCES provider (id),
            FOREIGN KEY (email_id) REFERENCES email_account (id)
        )
        ''')
        
        # Models table with all rate limit fields from system description
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS model (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL,
            name TEXT UNIQUE NOT NULL,
            actual_model TEXT NOT NULL,
            model_type TEXT,
            rpm_limit INTEGER DEFAULT 30,
            tpm_limit INTEGER DEFAULT 6000,
            rpd_limit INTEGER DEFAULT 0,
            tpd_limit INTEGER DEFAULT 0,
            rpm_month_limit INTEGER DEFAULT 0,
            tpm_month_limit INTEGER DEFAULT 0,
            timeout REAL DEFAULT 600.0,
            stream_timeout REAL DEFAULT 300.0,
            max_retries INTEGER DEFAULT 2,
            supports_function_calling BOOLEAN DEFAULT 1,
            model_size_b INTEGER DEFAULT NULL,
            max_input_tokens INTEGER DEFAULT NULL,
            max_output_tokens INTEGER DEFAULT NULL,
            description TEXT DEFAULT NULL,
            last_checked TEXT DEFAULT NULL,
            last_status TEXT DEFAULT NULL,
            last_message TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (provider_id) REFERENCES provider (id)
        )
        ''')
        
        # Model Fallbacks table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS model_fallback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            primary_model TEXT UNIQUE NOT NULL,
            fallback_models TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Model Health History table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS model_health_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            latency_ms REAL DEFAULT 0,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (model_id) REFERENCES model (id) ON DELETE CASCADE
        )
        ''')
        
        self.conn.commit()
        # Add health-check columns if this is an existing DB (safe no-op on fresh DBs).
        self._ensure_model_columns()

    def _ensure_model_columns(self):
        cursor = self.conn.cursor()
        cursor.execute('PRAGMA table_info(model)')
        existing = {row[1] for row in cursor.fetchall()}
        for col, ctype in (('last_checked', 'TEXT'), ('last_status', 'TEXT'), ('last_message', 'TEXT'), ('model_size_b', 'INTEGER'), ('stream_timeout', 'REAL'), ('max_input_tokens', 'INTEGER'), ('max_output_tokens', 'INTEGER'), ('description', 'TEXT')):
            if col not in existing:
                try:
                    cursor.execute(f'ALTER TABLE model ADD COLUMN {col} {ctype}')
                except Exception:
                    pass

        cursor.execute('PRAGMA table_info(model_health_history)')
        h_existing = {row[1] for row in cursor.fetchall()}
        if 'latency_ms' not in h_existing:
            try:
                cursor.execute('ALTER TABLE model_health_history ADD COLUMN latency_ms REAL DEFAULT 0')
            except Exception:
                pass

        self.conn.commit()

    def initialize_rotation_settings(self):
        """Initialize rotation settings with default values from rotation_settings.json"""
        cursor = self.conn.cursor()

        # Check if rotation settings already exist
        cursor.execute('SELECT COUNT(*) FROM rotation_settings')
        if cursor.fetchone()[0] == 0:
            # Insert default rotation settings
            cursor.execute('''
                INSERT INTO rotation_settings (
                    routing_strategy, key_rotation_strategy, cooldown_time, allowed_fails, num_retries
                ) VALUES (?, ?, ?, ?, ?)
            ''', (
                'simple-shuffle', 'round-robin', 60, 2, 1
            ))
            self.conn.commit()

    def close(self):
        self.conn.close()

    def get_providers(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT id, name, provider_type, api_base, description, created_at FROM provider')
        providers = []
        for row in cursor.fetchall():
            providers.append({
                'id': row[0],
                'name': row[1],
                'api_base': row[2],
                'description': row[3],
                'created_at': row[4]
            })
        return providers


def _resolve_scope(row_scope, provider_type):
    pt = (provider_type or '').strip().lower()
    if pt in ('gemini', 'google', 'googleai', 'googleai_studio', 'vertex_ai', 'vertexai'):
        return 'per_model' if not row_scope or row_scope == 'cumulative' else row_scope
    return row_scope or 'cumulative'

class Provider:
    def __init__(self, db):
        self.db = db

    def create(self, name, api_base='', description='', provider_type='custom', rate_limit_scope=None):
        cursor = self.db.conn.cursor()
        scope = rate_limit_scope or _resolve_scope(None, provider_type)
        try:
            cursor.execute(
                'INSERT INTO provider (name, provider_type, api_base, description, rate_limit_scope) VALUES (?, ?, ?, ?, ?)',
                (name, provider_type, api_base, description, scope)
            )
            self.db.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            self.db.conn.rollback()
            raise Exception(f"Failed to create provider: {e}")

    def get_all(self):
        cursor = self.db.conn.cursor()
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_provider_name ON provider(name)')
        cursor.execute('SELECT id, name, provider_type, api_base, description, rate_limit_scope, created_at FROM provider')
        providers = []
        for row in cursor.fetchall():
            providers.append({
                'id': row[0],
                'name': row[1],
                'provider_type': row[2],
                'api_base': row[3],
                'description': row[4],
                'rate_limit_scope': _resolve_scope(row[5], row[2]),
                'created_at': row[6]
            })
        return providers

    def get_by_name(self, name):
        cursor = self.db.conn.cursor()
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_provider_name ON provider(name)')
        cursor.execute('SELECT id, name, provider_type, api_base, description, rate_limit_scope, created_at FROM provider WHERE name = ?', (name,))
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0],
                'name': row[1],
                'provider_type': row[2],
                'api_base': row[3],
                'description': row[4],
                'rate_limit_scope': _resolve_scope(row[5], row[2]),
                'created_at': row[6]
            }
        return None

    def get_by_id(self, provider_id):
        cursor = self.db.conn.cursor()
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_provider_id ON provider(id)')
        cursor.execute('SELECT id, name, provider_type, api_base, description, rate_limit_scope, created_at FROM provider WHERE id = ?', (provider_id,))
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0],
                'name': row[1],
                'provider_type': row[2],
                'api_base': row[3],
                'description': row[4],
                'rate_limit_scope': _resolve_scope(row[5], row[2]),
                'created_at': row[6]
            }
        return None

    def update(self, provider_id, name=None, provider_type=None, api_base=None, description=None, rate_limit_scope=None):
        cursor = self.db.conn.cursor()
        updates = []
        params = []
        if name is not None:
            updates.append('name = ?')
            params.append(name)
        if provider_type is not None:
            updates.append('provider_type = ?')
            params.append(provider_type)
        if api_base is not None:
            updates.append('api_base = ?')
            params.append(api_base)
        if description is not None:
            updates.append('description = ?')
            params.append(description)
        if rate_limit_scope is not None:
            updates.append('rate_limit_scope = ?')
            params.append(rate_limit_scope)
        if updates:
            params.append(provider_id)
            cursor.execute(f"UPDATE provider SET {', '.join(updates)} WHERE id = ?", params)
            self.db.conn.commit()

    def delete(self, provider_id):
        # Cascade: remove the provider's models and keys first, then the provider.
        cursor = self.db.conn.cursor()
        cursor.execute('DELETE FROM model WHERE provider_id = ?', (provider_id,))
        cursor.execute('DELETE FROM api_key WHERE provider_id = ?', (provider_id,))
        cursor.execute('DELETE FROM provider WHERE id = ?', (provider_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0


class APIKey:
    def __init__(self, db):
        self.db = db

    def create(self, provider_id, email_id, key_name, key_value, active=True):
        cursor = self.db.conn.cursor()
        try:
            cursor.execute(
                'INSERT INTO api_key (provider_id, email_id, key_name, key_value, is_active) VALUES (?, ?, ?, ?, ?)',
                (provider_id, email_id, key_name, key_value, active)
            )
            self.db.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            self.db.conn.rollback()
            raise Exception(f"Failed to create API key: {e}")

    def get_by_provider(self, provider_id):
        cursor = self.db.conn.cursor()
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_api_key_provider_id ON api_key(provider_id)')
        cursor.execute(
            'SELECT id, provider_id, email_id, key_name, key_value, is_active, created_at FROM api_key WHERE provider_id = ?',
            (provider_id,)
        )
        keys = []
        for row in cursor.fetchall():
            keys.append({
                'id': row[0],
                'provider_id': row[1],
                'email_id': row[2],
                'key_name': row[3],
                'key_value': row[4],
                'is_active': row[5],
                'created_at': row[6]
            })
        return keys

    def update(self, key_id, is_active=None, key_name=None, key_value=None):
        cursor = self.db.conn.cursor()
        updates = []
        params = []
        if is_active is not None:
            updates.append('is_active = ?')
            params.append(is_active)
        if key_name is not None:
            updates.append('key_name = ?')
            params.append(key_name)
        if key_value is not None:
            updates.append('key_value = ?')
            params.append(key_value)
            
        if updates:
            params.append(key_id)
            cursor.execute(f"UPDATE api_key SET {', '.join(updates)} WHERE id = ?", params)
            self.db.conn.commit()
            return cursor.rowcount > 0
        return False

    def delete(self, key_id):
        cursor = self.db.conn.cursor()
        cursor.execute('DELETE FROM api_key WHERE id = ?', (key_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0


class AIModel:
    def __init__(self, db):
        self.db = db

    def create(self, provider_name, name, actual_model, model_type='', rpm_limit=30, tpm_limit=6000,
             rpd_limit=0, tpd_limit=0, rpm_month_limit=0, tpm_month_limit=0, timeout=15, max_retries=2,
             supports_function_calling=True, skills=None, model_size_b=None, max_input_tokens=None, max_output_tokens=None, description=None):
        # Get provider
        cursor = self.db.conn.cursor()
        cursor.execute('SELECT id FROM provider WHERE name = ?', (provider_name,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Provider with name '{provider_name}' does not exist")
        provider_id = row[0]

        # Validate provider exists
        if not provider_id:
            raise ValueError(f"Provider with name '{provider_name}' does not exist")

        skills_json = json.dumps(skills) if skills and isinstance(skills, (list, tuple)) else '[]'
        try:
            cursor.execute(
                '''INSERT INTO model
                   (provider_id, name, actual_model, model_type, rpm_limit, tpm_limit, rpd_limit, tpd_limit,
                    rpm_month_limit, tpm_month_limit, timeout, max_retries, supports_function_calling, skills, model_size_b, max_input_tokens, max_output_tokens, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (provider_id, name, actual_model, model_type, rpm_limit, tpm_limit, rpd_limit, tpd_limit,
                 rpm_month_limit, tpm_month_limit, timeout, max_retries, supports_function_calling, skills_json, model_size_b, max_input_tokens, max_output_tokens, description)
            )
            self.db.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            self.db.conn.rollback()
            raise Exception(f"Failed to create model: {e}")

    def get_by_provider_by_id(self, provider_id):
        cursor = self.db.conn.cursor()
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_model_provider_id ON model(provider_id)')
        cursor.execute(
            '''SELECT id, provider_id, name, actual_model, model_type,
                      rpm_limit, tpm_limit, rpd_limit, tpd_limit,
                      rpm_month_limit, tpm_month_limit, timeout, max_retries,
                      supports_function_calling, skills, model_size_b,
                      created_at, last_checked, last_status, last_message, max_input_tokens, max_output_tokens, description
               FROM model
               WHERE provider_id = ?''',
            (provider_id,)
        )
        models = []
        for row in cursor.fetchall():
            models.append({
                'id': row[0],
                'provider_id': row[1],
                'name': row[2],
                'actual_model': row[3],
                'model_type': row[4],
                'rpm_limit': row[5],
                'tpm_limit': row[6],
                'rpd_limit': row[7],
                'tpd_limit': row[8],
                'rpm_month_limit': row[9],
                'tpm_month_limit': row[10],
                'timeout': row[11],
                'max_retries': row[12],
                'supports_function_calling': row[13],
                'skills': json.loads(row[14]) if row[14] else [],
                'model_size_b': row[15],
                'created_at': row[16],
                'last_checked': row[17] if len(row) > 17 else '',
                'last_status': row[18] if len(row) > 18 else '',
                'last_message': row[19] if len(row) > 19 else '',
                'max_input_tokens': row[20] if len(row) > 20 else None,
                'max_output_tokens': row[21] if len(row) > 21 else None,
                'description': row[22] if len(row) > 22 else ''
            })
        return models

    def get_by_provider(self, provider_name):
        cursor = self.db.conn.cursor()
        cursor.execute(
            '''SELECT m.id, m.provider_id, m.name, m.actual_model, m.model_type,
                      m.rpm_limit, m.tpm_limit, m.rpd_limit, m.tpd_limit,
                      m.rpm_month_limit, m.tpm_month_limit, m.timeout, m.max_retries,
                      m.supports_function_calling, m.skills, m.model_size_b,
                      m.created_at, p.name as provider_name,
                      m.last_checked, m.last_status, m.last_message, m.max_input_tokens, m.max_output_tokens, m.description
               FROM model m
               JOIN provider p ON m.provider_id = p.id
               WHERE p.name = ?''',
            (provider_name,)
        )
        models = []
        for row in cursor.fetchall():
            models.append({
                'id': row[0],
                'provider_id': row[1],
                'name': row[2],
                'actual_model': row[3],
                'model_type': row[4],
                'rpm_limit': row[5],
                'tpm_limit': row[6],
                'rpd_limit': row[7],
                'tpd_limit': row[8],
                'rpm_month_limit': row[9],
                'tpm_month_limit': row[10],
                'timeout': row[11],
                'max_retries': row[12],
                'supports_function_calling': row[13],
                'skills': json.loads(row[14]) if row[14] else [],
                'model_size_b': row[15],
                'created_at': row[16],
                'provider_name': row[17],
                'last_checked': row[18] if len(row) > 18 else '',
                'last_status': row[19] if len(row) > 19 else '',
                'last_message': row[20] if len(row) > 20 else '',
                'max_input_tokens': row[21] if len(row) > 21 else None,
                'max_output_tokens': row[22] if len(row) > 22 else None,
                'description': row[23] if len(row) > 23 else ''
            })
        return models

    def get(self, model_id):
        cursor = self.db.conn.cursor()
        cursor.execute(
            '''SELECT m.id, m.provider_id, m.name, m.actual_model, m.model_type,
                      m.rpm_limit, m.tpm_limit, m.rpd_limit, m.tpd_limit,
                      m.rpm_month_limit, m.tpm_month_limit, m.timeout, m.max_retries,
                      m.supports_function_calling, m.skills, m.model_size_b,
                      m.created_at, p.name as provider_name,
                      m.last_checked, m.last_status, m.last_message, m.max_input_tokens, m.max_output_tokens, m.description
               FROM model m
               JOIN provider p ON m.provider_id = p.id
               WHERE m.id = ?''',
            (model_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0],
                'provider_id': row[1],
                'name': row[2],
                'actual_model': row[3],
                'model_type': row[4],
                'rpm_limit': row[5],
                'tpm_limit': row[6],
                'rpd_limit': row[7],
                'tpd_limit': row[8],
                'rpm_month_limit': row[9],
                'tpm_month_limit': row[10],
                'timeout': row[11],
                'max_retries': row[12],
                'supports_function_calling': row[13],
                'skills': json.loads(row[14]) if row[14] else [],
                'model_size_b': row[15],
                'created_at': row[16],
                'provider_name': row[17],
                'last_checked': row[18] if len(row) > 18 else '',
                'last_status': row[19] if len(row) > 19 else '',
                'last_message': row[20] if len(row) > 20 else '',
                'max_input_tokens': row[21] if len(row) > 21 else None,
                'max_output_tokens': row[22] if len(row) > 22 else None,
                'description': row[23] if len(row) > 23 else ''
            }
        return None

    def update(self, model_id, **kwargs):
        cursor = self.db.conn.cursor()
        allowed_fields = ['name', 'actual_model', 'model_type', 'rpm_limit', 'tpm_limit',
                         'rpd_limit', 'tpd_limit', 'rpm_month_limit', 'tpm_month_limit',
                         'timeout', 'stream_timeout', 'max_retries', 'supports_function_calling', 'skills', 'model_size_b', 'max_input_tokens', 'max_output_tokens', 'description']
        updates = []
        params = []
        for field in allowed_fields:
            if field in kwargs:
                if field == 'skills':
                    params.append(json.dumps(kwargs[field]))
                else:
                    params.append(kwargs[field])
                updates.append(f'{field} = ?')
        if updates:
            params.append(model_id)
            cursor.execute(f"UPDATE model SET {', '.join(updates)} WHERE id = ?", params)
            self.db.conn.commit()
            return cursor.rowcount > 0
        return False

    def update_all_models_timeout(self, timeout=600.0, stream_timeout=300.0):
        """Update timeout and stream_timeout for all models"""
        cursor = self.db.conn.cursor()
        cursor.execute(
            'UPDATE model SET timeout = ?, stream_timeout = ?',
            (timeout, stream_timeout)
        )
        self.db.conn.commit()
        return cursor.rowcount

    def update_health(self, model_id, status, message, checked_at, latency_ms=0):
        """Record the outcome of a live availability check for a model in history and update current status."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            'UPDATE model SET last_status = ?, last_message = ?, last_checked = ? WHERE id = ?',
            (status, message, checked_at, model_id)
        )
        cursor.execute(
            'INSERT INTO model_health_history (model_id, status, message, checked_at, latency_ms) VALUES (?, ?, ?, ?, ?)',
            (model_id, status, message, checked_at, float(latency_ms or 0))
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def get_health_history(self, model_id, limit=20):
        """Retrieve the latest `limit` health checks for a model, ordered chronologically (oldest to newest)."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            '''SELECT status, message, checked_at, latency_ms
               FROM (
                   SELECT status, message, checked_at, latency_ms, id
                   FROM model_health_history
                   WHERE model_id = ?
                   ORDER BY id DESC
                   LIMIT ?
               ) ORDER BY id ASC''',
            (model_id, limit)
        )
        rows = cursor.fetchall()
        return [
            {
                'status': r[0],
                'message': r[1],
                'checked_at': r[2],
                'latency_ms': round(r[3], 1) if len(r) > 3 and r[3] is not None else 0
            } for r in rows
        ]

    def get_model_latency_stats(self, model_id):
        """Calculate latency and availability stats for a model."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            '''SELECT AVG(latency_ms), MIN(latency_ms), MAX(latency_ms),
                      SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
               FROM model_health_history
               WHERE model_id = ? AND latency_ms > 0''',
            (model_id,)
        )
        row = cursor.fetchone()
        if row and row[0] is not None:
            return {
                'avg_latency': round(row[0], 1),
                'min_latency': round(row[1], 1),
                'max_latency': round(row[2], 1),
                'uptime_percent': round(row[3], 1) if row[3] is not None else 100.0
            }
        return {'avg_latency': 0, 'min_latency': 0, 'max_latency': 0, 'uptime_percent': 100.0}

    def get_last_success_at(self, model_id):
        """Retrieve the timestamp of the last successful health check ('ok') for a model."""
        cursor = self.db.conn.cursor()
        cursor.execute(
            '''SELECT checked_at
               FROM model_health_history
               WHERE model_id = ? AND status = 'ok'
               ORDER BY id DESC
               LIMIT 1''',
            (model_id,)
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def delete(self, model_id):
        cursor = self.db.conn.cursor()
        try:
            cursor.execute('DELETE FROM model_health_history WHERE model_id = ?', (model_id,))
            cursor.execute('DELETE FROM model WHERE id = ?', (model_id,))
            self.db.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            self.db.conn.rollback()
            raise Exception(f"Failed to delete model: {e}")

    def delete_health_history_for_model(self, model_id):
        cursor = self.db.conn.cursor()
        try:
            cursor.execute('DELETE FROM model_health_history WHERE model_id = ?', (model_id,))
            self.db.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            self.db.conn.rollback()
            raise Exception(f"Failed to delete health history: {e}")

    def reset_all_health(self):
        cursor = self.db.conn.cursor()
        try:
            cursor.execute('DELETE FROM model_health_history')
            cursor.execute('''
                UPDATE model 
                SET last_status = 'unknown', 
                    last_checked = NULL, 
                    last_message = NULL
            ''')
            self.db.conn.commit()
            return True
        except sqlite3.Error as e:
            self.db.conn.rollback()
            raise Exception(f"Failed to reset health checks: {e}")


class ModelFallback:
    def __init__(self, db):
        self.db = db

    def get_all(self):
        cursor = self.db.conn.cursor()
        cursor.execute('SELECT id, primary_model, fallback_models, created_at FROM model_fallback ORDER BY primary_model')
        rows = cursor.fetchall()
        fallbacks = []
        for r in rows:
            try:
                fb_list = json.loads(r[2]) if r[2] else []
            except Exception:
                fb_list = []
            fallbacks.append({
                'id': r[0],
                'primary_model': r[1],
                'fallback_models': fb_list,
                'created_at': r[3]
            })
        return fallbacks

    def get_by_primary(self, primary_model):
        cursor = self.db.conn.cursor()
        cursor.execute('SELECT id, primary_model, fallback_models, created_at FROM model_fallback WHERE primary_model = ?', (primary_model,))
        r = cursor.fetchone()
        if r:
            try:
                fb_list = json.loads(r[2]) if r[2] else []
            except Exception:
                fb_list = []
            return {
                'id': r[0],
                'primary_model': r[1],
                'fallback_models': fb_list,
                'created_at': r[3]
            }
        return None

    def save(self, primary_model, fallback_models):
        cursor = self.db.conn.cursor()
        fb_json = json.dumps(fallback_models if isinstance(fallback_models, list) else [])
        cursor.execute(
            '''INSERT INTO model_fallback (primary_model, fallback_models)
               VALUES (?, ?)
               ON CONFLICT(primary_model) DO UPDATE SET fallback_models = excluded.fallback_models''',
            (primary_model, fb_json)
        )
        self.db.conn.commit()
        return cursor.lastrowid

    def delete(self, primary_model):
        cursor = self.db.conn.cursor()
        cursor.execute('DELETE FROM model_fallback WHERE primary_model = ?', (primary_model,))
        self.db.conn.commit()
        return cursor.rowcount > 0

class EmailAccount:
    def __init__(self, db):
        self.db = db

    def create(self, email, password='', email_type='other'):
        cursor = self.db.conn.cursor()
        try:
            cursor.execute(
                'INSERT INTO email_account (email, password, email_type) VALUES (?, ?, ?)',
                (email, password, email_type)
            )
            self.db.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            self.db.conn.rollback()
            raise Exception(f"Failed to create email account: {e}")

    def get_all(self):
        cursor = self.db.conn.cursor()
        cursor.execute('SELECT id, email, password, email_type, created_at FROM email_account ORDER BY email ASC')
        emails = []
        for row in cursor.fetchall():
            # count keys
            cursor.execute('SELECT COUNT(*) FROM api_key WHERE email_id = ?', (row[0],))
            key_count = cursor.fetchone()[0]
            emails.append({
                'id': row[0],
                'email': row[1],
                'password': row[2],
                'email_type': row[3],
                'created_at': row[4],
                'key_count': key_count
            })
        return emails

    def get_by_id(self, email_id):
        cursor = self.db.conn.cursor()
        cursor.execute('SELECT id, email, password, email_type, created_at FROM email_account WHERE id = ?', (email_id,))
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0],
                'email': row[1],
                'password': row[2],
                'email_type': row[3],
                'created_at': row[4]
            }
        return None

    def update(self, email_id, email=None, password=None, email_type=None):
        cursor = self.db.conn.cursor()
        updates = []
        params = []
        if email is not None:
            updates.append('email = ?')
            params.append(email)
        if password is not None:
            updates.append('password = ?')
            params.append(password)
        if email_type is not None:
            updates.append('email_type = ?')
            params.append(email_type)
        if updates:
            params.append(email_id)
            cursor.execute(f"UPDATE email_account SET {', '.join(updates)} WHERE id = ?", params)
            self.db.conn.commit()

    def delete(self, email_id):
        cursor = self.db.conn.cursor()
        # cascade delete keys
        cursor.execute('DELETE FROM api_key WHERE email_id = ?', (email_id,))
        cursor.execute('DELETE FROM email_account WHERE id = ?', (email_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0

