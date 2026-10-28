import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

# Banco temporário deixa os testes isolados e impede alterar o banco usado no projeto.
_temp_dir = tempfile.TemporaryDirectory()
os.environ["DATABASE"] = os.path.join(_temp_dir.name, "test.sqlite")

import app


def response_json(response):
    """Decodifica JSON da resposta para o analisador de tipos do editor."""
    return json.loads(response.get_data(as_text=True))


class AccessFlowTest(unittest.TestCase):
    """Testa as rotas Flask sem iniciar um servidor HTTP real."""
    def setUp(self):
        app.init_db()
        self.client = app.app.test_client()
        self.client.post("/login", data={"username": "admin", "password": "admin"})

    def test_pages_use_external_templates_and_assets(self):
        client = app.app.test_client()
        login_page = client.get("/login")
        self.assertIn(b"Security room", login_page.data)
        dashboard = self.client.get("/")
        self.assertIn(b"/static/app.js", dashboard.data)
        asset = client.get("/static/app.js")
        self.assertEqual(asset.status_code, 200)
        asset.close()

    def test_scan_requires_authentication(self):
        client = app.app.test_client()
        self.assertEqual(client.post("/api/scan", json={"tag": "anything"}).status_code, 401)

    def test_access_decisions_and_export(self):
        # Cobre cadastro/edição, entrada, saída com duração, negação, tag desconhecida e CSV.
        response = self.client.post("/api/employees", json={
            "name": "Ada", "employee_id": "E1", "tag": "TAG1", "allowed": True
        })
        self.assertEqual(response.status_code, 201)
        employee = response_json(self.client.get("/api/employees"))[0]
        updated = self.client.patch(f"/api/employees/{employee['id']}", json={"role": "Lead Artist"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(response_json(self.client.post("/api/scan", json={"tag": "TAG1"}))["type"], "entry")
        with app.connect() as db:
            db.execute("UPDATE employees SET entered_at=?,allowed=0 WHERE employee_id='E1'",
                       ((datetime.now().astimezone() - timedelta(hours=1)).isoformat(),))
        exit_event = response_json(self.client.post("/api/scan", json={"tag": "TAG1"}))
        self.assertEqual(exit_event["type"], "exit")
        self.assertGreaterEqual(exit_event["duration_seconds"], 3599)
        self.assertEqual(response_json(self.client.post("/api/scan", json={"tag": "UNKNOWN"}))["type"], "unknown_tag")
        denied = self.client.post("/api/employees", json={
            "name": "Bea", "employee_id": "E2", "tag": "TAG2", "allowed": False
        })
        self.assertEqual(denied.status_code, 201)
        self.assertEqual(response_json(self.client.post("/api/scan", json={"tag": "TAG2"}))["type"], "access_denied")
        self.assertEqual(self.client.get("/events.csv").status_code, 200)
        export = self.client.get("/events.csv").data
        self.assertIn(b"unknown_tag", export)
        self.assertIn(b"duration_seconds", export)

    def test_device_sync_authentication_and_idempotency(self):
        # Reenviar o mesmo UUID deve ser seguro: o servidor aceita o primeiro e ignora a cópia.
        app.DEVICE_TOKEN = "test-device-token"
        client = app.app.test_client()
        auth = {"Authorization": "Bearer test-device-token"}
        self.assertEqual(client.get("/api/device/credentials", headers=auth).status_code, 200)
        event = {
            "event_uuid": "00000000-0000-4000-8000-000000000001", "occurred_at": datetime.now().astimezone().isoformat(),
            "employee_id": "E1", "employee_name": "Ada", "tag": "TAG1",
            "type": "entry", "outcome": "granted", "duration_seconds": None,
        }
        first = client.post("/api/device/events", headers=auth, json={"events": [event]})
        repeated = client.post("/api/device/events", headers=auth, json={"events": [event]})
        self.assertEqual(response_json(first)["accepted"], 1)
        self.assertEqual(response_json(repeated)["accepted"], 0)
        invalid = client.post("/api/device/events", headers=auth, json={"events": [{"type": []}]})
        self.assertEqual(invalid.status_code, 400)
        app.DEVICE_TOKEN = ""


if __name__ == "__main__":
    unittest.main()
