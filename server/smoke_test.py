import os
import tempfile

os.environ["ALLOW_DEV_SIMULATOR"] = "true"
os.environ["BASE_PUBLIC_URL"] = "https://example.test"
os.environ["DEVICE_API_TOKEN"] = "test-device-token"
os.environ["WHATSAPP_VERIFY_TOKEN"] = "verify-token"
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "kargo_api_smoke_test.db")

try:
    os.remove(os.environ["DB_PATH"])
except FileNotFoundError:
    pass

from fastapi.testclient import TestClient

from app import app


client = TestClient(app)


def auth_headers():
    return {"X-Device-Token": "test-device-token"}


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_task_confirmation_and_updates():
    payload = {
        "record_id": "local-1",
        "phone": "+90 538 272 83 11",
        "name": "Test Musteri",
        "tracking_codes": ["123456789"],
        "delivery_code": "",
        "status": "Kod Bekleniyor",
        "updated_at": 1,
    }
    response = client.post("/api/tasks/upsert", json=payload, headers=auth_headers())
    assert response.status_code == 200
    confirmation_url = response.json()["confirmation_url"]
    token = confirmation_url.rsplit("/", 1)[-1]

    page = client.get(f"/t/{token}")
    assert page.status_code == 200
    assert "Teslimat kodu" in page.text

    submitted = client.post(f"/t/{token}", data={"code": "4567"})
    assert submitted.status_code == 200
    assert "Kod alındı" in submitted.text

    updates = client.get("/api/tasks/updates?since=0", headers=auth_headers())
    assert updates.status_code == 200
    task = updates.json()["tasks"][0]
    assert task["record_id"] == "local-1"
    assert task["delivery_code"] == "4567"


def test_webhook_verify_and_dev_simulator_dedupe():
    verify = client.get(
        "/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify-token&hub.challenge=abc123"
    )
    assert verify.status_code == 200
    assert verify.text == "abc123"

    response = client.post(
        "/api/dev/simulate-whatsapp",
        json={"phone": "+905382728311", "message": "Kod 7890", "message_id": "m-1"},
    )
    assert response.status_code == 200
    assert response.json()["result"]["status"] in {"pending", "updated"}

    duplicate = client.post(
        "/api/dev/simulate-whatsapp",
        json={"phone": "+905382728311", "message": "Kod 7890", "message_id": "m-1"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["result"]["status"] == "duplicate"

