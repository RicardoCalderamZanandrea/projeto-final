"""Cliente RFID: valida localmente, sinaliza o resultado e sincroniza eventos com o servidor."""
import argparse
import importlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime

# Configurações ficam no ambiente para não incluir endereço ou segredo diretamente no código.
BASE_URL = os.environ.get("ACCESS_API", "http://127.0.0.1:5000").rstrip("/")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
LOCAL_DB = os.environ.get("DEVICE_DATABASE", "device.sqlite")


@contextmanager
def db_connect():
    """Cria as tabelas locais e fecha o SQLite mesmo se uma operação falhar."""
    db = sqlite3.connect(LOCAL_DB)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
            -- Cópia local das permissões recebidas do servidor.
            CREATE TABLE IF NOT EXISTS employees (
                employee_id TEXT PRIMARY KEY, name TEXT NOT NULL, tag TEXT NOT NULL UNIQUE,
                role TEXT, allowed INTEGER NOT NULL, active INTEGER NOT NULL
            );
            -- Estado de presença e horário de entrada usados quando não há rede.
            CREATE TABLE IF NOT EXISTS state (
                employee_id TEXT PRIMARY KEY, inside INTEGER NOT NULL DEFAULT 0, entered_at TEXT
            );
            -- Fila durável: cada leitura fica salva até o servidor confirmar recebimento.
            CREATE TABLE IF NOT EXISTS outbox (
                event_uuid TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
        """)
        with db:
            yield db
    finally:
        db.close()


def api(method, path, payload=None):
    """Faz chamadas autenticadas ao backend usando somente a biblioteca padrão."""
    if not DEVICE_TOKEN:
        raise RuntimeError("Set DEVICE_TOKEN to the same value used by the server")
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE_URL + path, data=data, method=method, headers={
        "Authorization": "Bearer " + DEVICE_TOKEN,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def refresh_credentials():
    """Atualiza a cópia local; a transação evita deixar o cache vazio se a gravação falhar."""
    employees = api("GET", "/api/device/credentials")
    with db_connect() as db:
        db.execute("DELETE FROM employees")
        db.executemany("INSERT INTO employees VALUES(?,?,?,?,?,?)", [
            (e["employee_id"], e["name"], e["tag"], e["role"], e["allowed"], e["active"])
            for e in employees
        ])
    return len(employees)


def scan_tag(tag):
    """Classifica a tag com o último cache e persiste o evento antes de usar a rede."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with db_connect() as db:
        person = db.execute("SELECT * FROM employees WHERE tag=?", (tag,)).fetchone()
        employee_id = person["employee_id"] if person else None
        name = person["name"] if person else None
        duration = None
        state = db.execute("SELECT * FROM state WHERE employee_id=?", (employee_id,)).fetchone() if person else None
        if not person:
            event_type, outcome = "unknown_tag", "denied"
        # Uma pessoa já dentro sempre pode sair, ainda que sua permissão tenha sido revogada.
        elif state and state["inside"]:
            event_type, outcome = "exit", "granted"
            if state["entered_at"]:
                duration = max(0, int((datetime.fromisoformat(now) - datetime.fromisoformat(state["entered_at"])).total_seconds()))
            db.execute("UPDATE state SET inside=0,entered_at=NULL WHERE employee_id=?", (employee_id,))
        elif not person["active"] or not person["allowed"]:
            event_type, outcome = "access_denied", "denied"
        else:
            event_type, outcome = "entry", "granted"
            db.execute("INSERT INTO state(employee_id,inside,entered_at) VALUES(?,1,?) ON CONFLICT(employee_id) DO UPDATE SET inside=1,entered_at=excluded.entered_at", (employee_id, now))
        event = {
            "event_uuid": str(uuid.uuid4()), "occurred_at": now,
            "employee_id": employee_id, "employee_name": name, "tag": tag,
            "type": event_type, "outcome": outcome, "duration_seconds": duration,
        }
        # Gravar primeiro na fila local evita perder o evento se a conexão cair logo após a leitura.
        db.execute("INSERT INTO outbox VALUES(?,?)", (event["event_uuid"], json.dumps(event)))
    return event


def sync_outbox():
    """Envia até 500 eventos; só remove a fila após resposta válida do servidor."""
    with db_connect() as db:
        rows = db.execute("SELECT event_uuid,payload FROM outbox ORDER BY rowid LIMIT 500").fetchall()
    if not rows:
        return 0
    result = api("POST", "/api/device/events", {"events": [json.loads(row["payload"]) for row in rows]})
    # Se o envio falhou, a execução nem chega aqui e os eventos continuam na fila.
    with db_connect() as db:
        db.executemany("DELETE FROM outbox WHERE event_uuid=?", [(row["event_uuid"],) for row in rows])
    return result["accepted"]


def feedback(event, green=None, red=None, buzzer=None):
    """Usa sinais diferentes para acesso normal, negação e tag desconhecida."""
    if event["outcome"] == "granted":
        if green:
            green.on(); time.sleep(0.25); green.off()
        if buzzer:
            buzzer.beep(0.08, 0.05, n=1, background=False)
    else:
        if red:
            red.on(); time.sleep(0.6 if event["type"] == "unknown_tag" else 0.25); red.off()
        if buzzer:
            buzzer.beep(0.08, 0.08, n=3 if event["type"] == "unknown_tag" else 1, background=False)
    print(event["type"], event["employee_name"] or event["tag"], event["outcome"])


def main():
    """Inicia modo simulado no computador ou leitura RFID/GPIO no Raspberry Pi."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true", help="read RFID tags from stdin")
    args = parser.parse_args()
    try:
        refresh_credentials()
        print("Credential cache refreshed")
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print("Using cached credentials; refresh failed:", error)
    try:
        print("Synced queued events:", sync_outbox())
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print("Events remain queued until the server is reachable:", error)

    # Hardware é importado apenas no modo real; o simulador funciona sem bibliotecas de GPIO.
    green = red = buzzer = None
    if args.simulate:
        read_tag = lambda: input("RFID tag (blank to exit): ").strip()
    else:
        gpiozero = importlib.import_module("gpiozero")
        SimpleMFRC522 = importlib.import_module("mfrc522").SimpleMFRC522
        green = gpiozero.LED(int(os.environ.get("GREEN_LED_PIN", "17")))
        red = gpiozero.LED(int(os.environ.get("RED_LED_PIN", "27")))
        buzzer = gpiozero.Buzzer(int(os.environ.get("BUZZER_PIN", "22")))
        reader = SimpleMFRC522()
        read_tag = lambda: str(reader.read_id())

    last_refresh = time.monotonic()
    last_tag, last_scan = None, 0.0
    while True:
        if time.monotonic() - last_refresh > 60:
            try:
                refresh_credentials()
                print("Credential cache refreshed")
            except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
                print("Credential refresh failed:", error)
            try:
                print("Synced queued events:", sync_outbox())
            except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
                print("Events remain queued:", error)
            last_refresh = time.monotonic()
        tag = read_tag()
        if not tag:
            break
        # Atalho: bloqueia a mesma tag por 5 s; para uso sem supervisão, detectar remoção física da tag.
        if tag == last_tag and time.monotonic() - last_scan < 5:
            continue
        last_tag, last_scan = tag, time.monotonic()
        # A decisão local não depende da rede; a tentativa de sincronização vem depois de salvar o evento.
        event = scan_tag(tag)
        feedback(event, green, red, buzzer)
        try:
            sync_outbox()
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
            print("Event queued for later sync:", error)


if __name__ == "__main__":
    main()
