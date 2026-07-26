import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "install_remote.py"
SPEC = importlib.util.spec_from_file_location("install_remote", MODULE_PATH)
install_remote = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(install_remote)


def test_candidate_env_overrides_existing_deployment_values():
    old_config = {
        "exchange": {"key": "old-key", "secret": "old-secret", "password": "old-passphrase"},
        "api_server": {
            "username": "old-user",
            "password": "old-password",
            "jwt_secret_key": "old-jwt",
        },
    }
    old_env = {
        "FREQTRADE__EXCHANGE__KEY": "old-env-key",
        "FREQTRADE__EXCHANGE__SECRET": "old-env-secret",
        "FREQTRADE__EXCHANGE__PASSWORD": "old-env-passphrase",
    }
    candidate_env = {
        "FREQTRADE__EXCHANGE__KEY": "candidate-key",
        "FREQTRADE__EXCHANGE__SECRET": "candidate-secret",
        "FREQTRADE__EXCHANGE__PASSWORD": "candidate-passphrase",
        "FREQTRADE__API_SERVER__USERNAME": "candidate-user",
        "FREQTRADE__API_SERVER__PASSWORD": "candidate-password",
        "FREQTRADE__API_SERVER__JWT_SECRET_KEY": "candidate-jwt",
    }

    resolved = install_remote.resolve_credentials(old_config, old_env, candidate_env)

    assert resolved == {
        "exchange_key": "candidate-key",
        "exchange_secret": "candidate-secret",
        "exchange_password": "candidate-passphrase",
        "api_username": "candidate-user",
        "api_password": "candidate-password",
        "jwt_secret": "candidate-jwt",
    }
