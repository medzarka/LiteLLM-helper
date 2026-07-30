from typing import Dict, List, Optional
try:
    from ..models import models as db_models
except (ImportError, ValueError):
    from models import models as db_models

class ModelService:
    """Single source of truth for all CRUD operations on provider, api_key, model, and fallback tables."""

    def __init__(self, db_path: str = "litellm_helper.db"):
        self.db = db_models.Database(db_path)

    # ---------- Provider CRUD ----------
    def get_all_providers(self) -> List[Dict]:
        return db_models.Provider(self.db).get_all()

    def get_provider_by_name(self, name: str) -> Optional[Dict]:
        return db_models.Provider(self.db).get_by_name(name)

    def get_provider_by_id(self, provider_id: int) -> Optional[Dict]:
        return db_models.Provider(self.db).get_by_id(provider_id)

    def create_provider(self, name: str, api_base: str = "", description: str = "") -> int:
        return db_models.Provider(self.db).create(name, api_base, description)

    def update_provider(self, provider_id: int, **kwargs) -> bool:
        return db_models.Provider(self.db).update(provider_id, **kwargs)

    def delete_provider(self, provider_id: int) -> bool:
        return db_models.Provider(self.db).delete(provider_id)

    # ---------- APIKey CRUD ----------
    def get_keys_by_provider(self, provider_id: int) -> List[Dict]:
        return db_models.APIKey(self.db).get_by_provider(provider_id)

    def create_key(self, provider_id: int, key_name: str, key_value: str, active: bool = True) -> int:
        return db_models.APIKey(self.db).create(provider_id, key_name, key_value, active)

    def update_key(self, key_id: int, is_active: Optional[bool] = None) -> bool:
        return db_models.APIKey(self.db).update(key_id, is_active)

    def delete_key(self, key_id: int) -> bool:
        return db_models.APIKey(self.db).delete(key_id)

    # ---------- Model (AIModel) CRUD ----------
    def get_models_by_provider_id(self, provider_id: int) -> List[Dict]:
        models = db_models.AIModel(self.db).get_by_provider_by_id(provider_id)
        return models

    def get_model(self, model_id: int) -> Optional[Dict]:
        return db_models.AIModel(self.db).get(model_id)

    def create_model(self, provider_name: str, name: str, actual_model: str, **kwargs) -> int:
        return db_models.AIModel(self.db).create(provider_name, name, actual_model, **kwargs)

    def update_model(self, model_id: int, **kwargs) -> bool:
        return db_models.AIModel(self.db).update(model_id, **kwargs)

    def delete_model(self, model_id: int) -> bool:
        return db_models.AIModel(self.db).delete(model_id)

    def update_model_health(self, model_id: int, status: str, message: str, checked_at: str) -> bool:
        return db_models.AIModel(self.db).update_health(model_id, status, message, checked_at)

    def get_model_health_history(self, model_id: int, limit: int = 20) -> List[Dict]:
        return db_models.AIModel(self.db).get_health_history(model_id, limit)

    def get_model_last_success(self, model_id: int) -> Optional[str]:
        return db_models.AIModel(self.db).get_last_success_at(model_id)

    # ---------- ModelFallback CRUD ----------
    def get_all_fallbacks(self) -> List[Dict]:
        return db_models.ModelFallback(self.db).get_all()

    def get_fallback_by_primary(self, primary_model: str) -> Optional[Dict]:
        return db_models.ModelFallback(self.db).get_by_primary(primary_model)

    def save_fallback(self, primary_model: str, fallback_models: List[str]) -> int:
        return db_models.ModelFallback(self.db).save(primary_model, fallback_models)

    def delete_fallback(self, primary_model: str) -> bool:
        return db_models.ModelFallback(self.db).delete(primary_model)

    def clear_all_fallbacks(self) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute('DELETE FROM model_fallback')
        self.db.conn.commit()
        return cursor.rowcount

    # ---------- Helper ----------
    def close(self):
        self.db.close()