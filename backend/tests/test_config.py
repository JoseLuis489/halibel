from pathlib import Path

from app.core.config import (
    get_user_data_dir,
    resolve_active_env_path,
    settings,
)


def test_settings_initialization():
    assert settings.PROJECT_NAME == "Halibel Runtime"
    assert settings.VERSION == "0.3.0"
    assert settings.PORT == 8000
    assert isinstance(settings.USER_DATA_DIR, Path)
    assert isinstance(settings.ACTIVE_ENV_FILE, Path)

def test_get_user_data_dir():
    user_dir = get_user_data_dir()
    assert user_dir.exists()
    assert user_dir.name == "Halibel"

def test_resolve_active_env_path():
    active_env = resolve_active_env_path()
    assert isinstance(active_env, Path)
    assert active_env.name == ".env"
