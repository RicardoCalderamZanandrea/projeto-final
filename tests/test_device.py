import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta

# Usa um arquivo separado para comprovar o comportamento do cliente sem tocar no dispositivo real.
_temp_dir = tempfile.TemporaryDirectory()
os.environ["DEVICE_DATABASE"] = os.path.join(_temp_dir.name, "device.sqlite")
device = importlib.import_module("device")


class OfflineDeviceTest(unittest.TestCase):
    """Testa decisões e fila local sem servidor nem GPIO."""
    def setUp(self):
        with device.db_connect() as db:
            db.execute("DELETE FROM employees")
            db.execute("DELETE FROM state")
            db.execute("DELETE FROM outbox")
            db.execute("INSERT INTO employees VALUES(?,?,?,?,?,?)", ("E1", "Ada", "TAG1", "Artist", 1, 1))
            db.execute("INSERT INTO employees VALUES(?,?,?,?,?,?)", ("E2", "Bea", "TAG2", "Artist", 0, 1))

    def test_offline_access_decisions_are_queued_with_duration(self):
        # Simula uma visita de 35 minutos e confirma que cada tipo de leitura fica enfileirado.
        entry = device.scan_tag("TAG1")
        self.assertEqual(entry["type"], "entry")
        with device.db_connect() as db:
            db.execute("UPDATE state SET entered_at=? WHERE employee_id='E1'",
                       ((datetime.now().astimezone() - timedelta(minutes=35)).isoformat(),))
        exit_event = device.scan_tag("TAG1")
        self.assertEqual(exit_event["type"], "exit")
        self.assertGreaterEqual(exit_event["duration_seconds"], 2099)
        self.assertEqual(device.scan_tag("TAG2")["type"], "access_denied")
        self.assertEqual(device.scan_tag("UNKNOWN")["type"], "unknown_tag")
        with device.db_connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
