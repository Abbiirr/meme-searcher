from pathlib import Path

from vidsearch.config import _load_dotenv_defaults


def test_load_dotenv_defaults_sets_missing_env(monkeypatch, tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "LITELLM_MASTER_KEY=sk-test-key",
                "LITELLM_URL=http://127.0.0.1:4000",
                "VIDSEARCH_ENABLE_CAPTIONS=true",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("LITELLM_URL", raising=False)
    monkeypatch.delenv("VIDSEARCH_ENABLE_CAPTIONS", raising=False)

    loaded = _load_dotenv_defaults(dotenv)

    assert loaded == {
        "LITELLM_MASTER_KEY": "sk-test-key",
        "LITELLM_URL": "http://127.0.0.1:4000",
        "VIDSEARCH_ENABLE_CAPTIONS": "true",
    }


def test_load_dotenv_defaults_preserves_existing_env(monkeypatch, tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "LITELLM_MASTER_KEY=sk-from-dotenv",
                "LITELLM_URL=http://127.0.0.1:4000",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-explicit")
    monkeypatch.delenv("LITELLM_URL", raising=False)

    loaded = _load_dotenv_defaults(dotenv)

    assert loaded == {"LITELLM_URL": "http://127.0.0.1:4000"}


def test_load_dotenv_defaults_strips_quotes(monkeypatch, tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        '\n'.join(
            [
                'OPEN_WEBUI_ADMIN_EMAIL="admin@localhost"',
                "OPEN_WEBUI_ADMIN_PASSWORD='admin'",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("OPEN_WEBUI_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("OPEN_WEBUI_ADMIN_PASSWORD", raising=False)

    loaded = _load_dotenv_defaults(dotenv)

    assert loaded == {
        "OPEN_WEBUI_ADMIN_EMAIL": "admin@localhost",
        "OPEN_WEBUI_ADMIN_PASSWORD": "admin",
    }
