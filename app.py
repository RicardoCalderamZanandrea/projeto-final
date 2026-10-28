import csv
import hmac
import io
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chave-secreta")
DB_PATH = os.environ.get("DATABASE", "access.db")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")


@contextmanager
def connect():
    """Abre uma conexao SQLite e garante commit/rollback e fechamento"""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    # `CREATE IF NOT EXISTS` mantem os dados existentes
    # `ALTER TABLE` adiciona campos novos sem apagar o banco
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, employee_id TEXT NOT NULL UNIQUE,
                tag TEXT NOT NULL UNIQUE, role TEXT NOT NULL DEFAULT '',
                allowed INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
                inside INTEGER NOT NULL DEFAULT 0, entered_at TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, employee_id TEXT,
                employee_name TEXT, tag TEXT NOT NULL, type TEXT NOT NULL,
                outcome TEXT NOT NULL, event_uuid TEXT, duration_seconds INTEGER
            );
            CREATE TABLE IF NOT EXISTS admins (
                username TEXT PRIMARY KEY, password_hash TEXT NOT NULL
            );
        """)
        employee_columns = {row[1] for row in db.execute("PRAGMA table_info(employees)")}
        event_columns = {row[1] for row in db.execute("PRAGMA table_info(events)")}
        if "entered_at" not in employee_columns:
            db.execute("ALTER TABLE employees ADD COLUMN entered_at TEXT")
        if "event_uuid" not in event_columns:
            db.execute("ALTER TABLE events ADD COLUMN event_uuid TEXT")
        if "duration_seconds" not in event_columns:
            db.execute("ALTER TABLE events ADD COLUMN duration_seconds INTEGER")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS events_event_uuid ON events(event_uuid)")
        if not db.execute("SELECT 1 FROM admins LIMIT 1").fetchone():
            db.execute("INSERT INTO admins VALUES (?, ?)", (
                os.environ.get("ADMIN_USER", "admin"),
                generate_password_hash(os.environ.get("ADMIN_PASSWORD", "admin")),
            ))


def login_required(fn):
    """restringe rotas de administracao a sessao criada apos o login"""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return jsonify(error="Login required"), 401
        return fn(*args, **kwargs)
    return wrapped


@app.get("/login")
def login_page():
    return render_template("login.html")


@app.post("/login")
def login():
    # aceita formulario do navegador ou JSON, mas compara a senha com o hash armazenado
    data = request.get_json(silent=True) or request.form
    with connect() as db:
        admin = db.execute("SELECT * FROM admins WHERE username = ?", (data.get("username", ""),)).fetchone()
    if admin and check_password_hash(admin["password_hash"], data.get("password", "")):
        session["admin"] = admin["username"]
        return jsonify(ok=True) if request.is_json else redirect(url_for("dashboard"))
    if request.is_json:
        return jsonify(error="Invalid username or password"), 401
    return render_template("login.html", error="Invalid username or password"), 401


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.get("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.get("/api/employees")
@login_required
def employees():
    with connect() as db:
        rows = db.execute("SELECT id,name,employee_id,tag,role,allowed,active,inside FROM employees ORDER BY name").fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/employees")
@login_required
def add_employee():
    data = request.get_json(silent=True) or {}
    required = ("name", "employee_id", "tag")
    if any(not str(data.get(key, "")).strip() for key in required):
        return jsonify(error="name, employee_id and tag are required"), 400
    try:
        with connect() as db:
            db.execute("INSERT INTO employees(name,employee_id,tag,role,allowed,active) VALUES(?,?,?,?,?,?)",
                       (data["name"].strip(), data["employee_id"].strip(), data["tag"].strip(),
                        data.get("role", "").strip(), int(bool(data.get("allowed"))), int(bool(data.get("active", True)))))
    except sqlite3.IntegrityError:
        return jsonify(error="Employee ID and RFID tag must be unique"), 409
    return jsonify(ok=True), 201


@app.patch("/api/employees/<int:employee_id>")
@login_required
def update_employee(employee_id):
    data = request.get_json(silent=True) or {}
    # a lista branca evita que o cliente escolha colunas sql arbitrarias
    fields = {key: data[key] for key in ("name", "employee_id", "tag", "role", "allowed", "active") if key in data}
    if not fields:
        return jsonify(error="No fields to update"), 400
    for key in ("allowed", "active"):
        if key in fields:
            fields[key] = int(bool(fields[key]))
    try:
        with connect() as db:
            result = db.execute("UPDATE employees SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?",
                                (*fields.values(), employee_id))
            if not result.rowcount:
                return jsonify(error="Employee not found"), 404
    except sqlite3.IntegrityError:
        return jsonify(error="Employee ID and RFID tag must be unique"), 409
    return jsonify(ok=True)


@app.delete("/api/employees/<int:employee_id>")
@login_required
def delete_employee(employee_id):
    with connect() as db:
        result = db.execute("DELETE FROM employees WHERE id=?", (employee_id,))
    return (jsonify(ok=True), 200) if result.rowcount else (jsonify(error="Employee not found"), 404)


def record_scan(tag):
    """decide o resultado de uma leitura e grava evento/ocupacao na mesma transacao"""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect() as db:
        person = db.execute("SELECT * FROM employees WHERE tag=?", (tag,)).fetchone()
        employee_id = person["employee_id"] if person else None
        name = person["name"] if person else None
        duration = None
        if not person:
            event_type, outcome = "unknown_tag", "denied"
        # se ja esta dentro, permite a saida mesmo apos revogação de acesso (saida segura)
        elif person["inside"]:
            event_type, outcome = "exit", "granted"
            duration = max(0, int((datetime.fromisoformat(now) - datetime.fromisoformat(person["entered_at"])).total_seconds())) if person["entered_at"] else None
            db.execute("UPDATE employees SET inside=0, entered_at=NULL WHERE id=?", (person["id"],))
        elif not person["active"] or not person["allowed"]:
            event_type, outcome = "access_denied", "denied"
        else:
            event_type, outcome = "entry", "granted"
            db.execute("UPDATE employees SET inside=1, entered_at=? WHERE id=?", (now, person["id"]))
        # UUID identifica o evento de forma única, útil para sincronizar filas offline sem duplicar logs.
        event_uuid = str(uuid.uuid4())
        db.execute("INSERT INTO events(occurred_at,employee_id,employee_name,tag,type,outcome,event_uuid,duration_seconds) VALUES(?,?,?,?,?,?,?,?)",
                   (now, employee_id, name, tag, event_type, outcome, event_uuid, duration))
    return {"timestamp": now, "employee": name, "type": event_type, "outcome": outcome, "duration_seconds": duration, "event_uuid": event_uuid}


@app.post("/api/scan")
def scan():
    if not session.get("admin") and not device_authenticated():
        return jsonify(error="Device authentication required"), 401
    data = request.get_json(silent=True) or {}
    tag = str(data.get("tag", "")).strip()
    if not tag:
        return jsonify(error="tag is required"), 400
    return jsonify(record_scan(tag))


def device_authenticated():
    """valida o segredo compartilhado enviado pelo cliente embarcado"""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    return bool(DEVICE_TOKEN) and hmac.compare_digest(token, DEVICE_TOKEN)


@app.get("/api/device/credentials")
def device_credentials():
    if not device_authenticated():
        return jsonify(error="Device authentication required"), 401
    with connect() as db:
        rows = db.execute("SELECT employee_id,name,tag,role,allowed,active FROM employees").fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/device/events")
def sync_device_events():
    """recebe lotes validados; uuid unico torna seguro reenviar lotes apos falhas"""
    if not device_authenticated():
        return jsonify(error="Device authentication required"), 401
    data = request.get_json(silent=True) or {}
    events = data.get("events")
    if not isinstance(events, list) or len(events) > 500:
        return jsonify(error="events must be a list of at most 500 records"), 400
    valid_types = {"entry": "granted", "exit": "granted", "access_denied": "denied", "unknown_tag": "denied"}
    for event in events:
        if not isinstance(event, dict):
            return jsonify(error="Each event must be an object"), 400
        event_type = event.get("type")
        if not isinstance(event_type, str) or event_type not in valid_types or event.get("outcome") != valid_types[event_type]:
            return jsonify(error="Invalid event type or outcome"), 400
        if not all(isinstance(event.get(key), str) and event[key].strip() for key in ("event_uuid", "occurred_at", "tag")):
            return jsonify(error="event_uuid, occurred_at and tag are required"), 400
        if len(event["tag"]) > 128 or any(event.get(key) is not None and not isinstance(event[key], str) for key in ("employee_id", "employee_name")):
            return jsonify(error="Invalid employee or tag fields"), 400
        try:
            uuid.UUID(event["event_uuid"])
            occurred_at = datetime.fromisoformat(event["occurred_at"])
            if occurred_at.tzinfo is None:
                raise ValueError
        except ValueError:
            return jsonify(error="event_uuid must be a UUID and occurred_at a timezone-aware ISO timestamp"), 400
        duration = event.get("duration_seconds")
        if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int) or duration < 0 or event_type != "exit"):
            return jsonify(error="duration_seconds must be a non-negative exit duration"), 400
    # validamos o lote inteiro antes de gravar qualquer item, evitando sincronizacao parcial invalida
    accepted = 0
    with connect() as db:
        for event in events:
            result = db.execute("""INSERT OR IGNORE INTO events
                (occurred_at,employee_id,employee_name,tag,type,outcome,event_uuid,duration_seconds)
                VALUES(?,?,?,?,?,?,?,?)""",
                (event["occurred_at"], event.get("employee_id"), event.get("employee_name"), event["tag"],
                 event["type"], event["outcome"], event["event_uuid"], event.get("duration_seconds")))
            if not result.rowcount:
                continue
            accepted += 1
            person = db.execute("SELECT id FROM employees WHERE tag=?", (event["tag"],)).fetchone()
            if person and event["type"] == "entry":
                db.execute("UPDATE employees SET inside=1,entered_at=? WHERE id=?", (event["occurred_at"], person["id"]))
            elif person and event["type"] == "exit":
                db.execute("UPDATE employees SET inside=0,entered_at=NULL WHERE id=?", (person["id"],))
    return jsonify(accepted=accepted)


@app.get("/api/events")
@login_required
def event_list():
    with connect() as db:
        rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/occupancy")
@login_required
def occupancy():
    with connect() as db:
        rows = db.execute("SELECT name,employee_id,role FROM employees WHERE inside=1 ORDER BY name").fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/events.csv")
@login_required
def export_csv():
    """exporta o formato consumido pelo notebook de analise em pandas"""
    with connect() as db:
        rows = db.execute("SELECT occurred_at,employee_id,employee_name,tag,type,outcome,duration_seconds FROM events ORDER BY id").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "employee_id", "employee_name", "tag", "type", "outcome", "duration_seconds"])
    writer.writerows(tuple(row) for row in rows)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=access_events.csv"})



# Garante que o banco e eventuais migrações estejam prontos ao iniciar o servidor.
init_db()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=os.environ.get("FLASK_DEBUG") == "1")
