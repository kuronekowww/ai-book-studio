import importlib

from fastapi.testclient import TestClient


def test_settings_api_lists_and_switches_models_without_exposing_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AI_BOOK_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_BOOK_STUDIO_PROVIDER", "demo")
    monkeypatch.setenv("AI_BOOK_STUDIO_API_KEY", "secret-test-key")
    monkeypatch.setenv("AI_BOOK_STUDIO_MODEL", "demo-model")

    main = importlib.import_module("app.main")
    main = importlib.reload(main)
    client = TestClient(main.app)

    initial = client.get("/api/settings/status")
    assert initial.status_code == 200
    initial_data = initial.json()
    assert len(initial_data["available_models"]) == 8
    assert any(
        item["id"] == "doubao-seed-2.0-pro"
        and item["provider"] == "openai-compatible"
        for item in initial_data["available_models"]
    )
    assert "secret-test-key" not in initial.text

    switched = client.put(
        "/api/settings/model", json={"model_id": "kimi-k3"}
    )
    assert switched.status_code == 200
    assert switched.json()["current_model_id"] == "kimi-k3"
    assert switched.json()["model"] == "kimi-k3"
    assert "secret-test-key" not in switched.text

    rejected = client.put(
        "/api/settings/model", json={"model_id": "unknown-model"}
    )
    assert rejected.status_code == 400
    assert client.get("/api/settings/status").json()["current_model_id"] == "kimi-k3"
