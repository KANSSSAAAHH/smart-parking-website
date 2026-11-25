#!/usr/bin/env python3
"""SmartPark REST API and static web server; uses only the Python standard library."""

import hmac
import json
import math
import mimetypes
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen


APP_DIR = Path(__file__).resolve().parent
DATABASE_SETTING = os.environ.get("SMARTPARK_DB", "smartpark.sqlite3")
DATABASE_PATH = Path(DATABASE_SETTING)
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = APP_DIR / DATABASE_PATH
DEVICE_API_TOKEN = os.environ.get("DEVICE_API_TOKEN", "")
DEVICE_TIMEOUT_SECONDS = int(os.environ.get("DEVICE_TIMEOUT_SECONDS", "60"))
AI_API_URL = os.environ.get("AI_API_URL", "")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "")
MAX_REQUEST_BYTES = 65536


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@contextmanager
def open_database():
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open_database() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS slots (
                slot INTEGER PRIMARY KEY CHECK (slot BETWEEN 1 AND 3),
                status TEXT CHECK (status IN ('empty', 'occupied') OR status IS NULL),
                distance_cm REAL CHECK (distance_cm IS NULL OR distance_cm >= 0),
                updated_at TEXT
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS device_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                device_id TEXT,
                last_seen TEXT,
                ldr_lux REAL CHECK (ldr_lux IS NULL OR ldr_lux >= 0),
                ir_entry INTEGER CHECK (ir_entry IN (0, 1) OR ir_entry IS NULL),
                ir_exit INTEGER CHECK (ir_exit IN (0, 1) OR ir_exit IS NULL),
                ir_entry_updated_at TEXT,
                ir_exit_updated_at TEXT
            )"""
        )
        device_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(device_state)")
        }
        for column in ("ir_entry_updated_at", "ir_exit_updated_at"):
            if column not in device_columns:
                connection.execute(
                    "ALTER TABLE device_state ADD COLUMN {}".format(column)
                )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS parking_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot INTEGER NOT NULL REFERENCES slots(slot),
                entered_at TEXT NOT NULL,
                exited_at TEXT,
                duration_seconds INTEGER,
                CHECK (duration_seconds IS NULL OR duration_seconds >= 0)
            )"""
        )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS one_open_session_per_slot
            ON parking_sessions(slot) WHERE exited_at IS NULL"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at TEXT NOT NULL,
                device_id TEXT,
                slot INTEGER NOT NULL REFERENCES slots(slot),
                status TEXT CHECK (status IN ('empty', 'occupied') OR status IS NULL),
                distance_cm REAL CHECK (distance_cm IS NULL OR distance_cm >= 0),
                ldr_lux REAL,
                ir_entry INTEGER,
                ir_exit INTEGER
            )"""
        )
        for slot_number in range(1, 4):
            connection.execute(
                "INSERT OR IGNORE INTO slots (slot, status) VALUES (?, NULL)",
                (slot_number,),
            )
        connection.execute("INSERT OR IGNORE INTO device_state (id) VALUES (1)")


def parse_optional_number(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("{} harus berupa angka.".format(name))
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("{} harus berupa angka non-negatif yang valid.".format(name))
    return result


def parse_optional_boolean(value, name):
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("{} harus berupa boolean.".format(name))
    return int(value)


def validate_telemetry(payload):
    if not isinstance(payload, dict):
        raise ValueError("Body JSON harus berupa object.")
    device_id = payload.get("device_id")
    if device_id is not None and (not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 100):
        raise ValueError("device_id harus berupa teks dengan panjang 1–100 karakter.")

    slots = payload.get("slots")
    if not isinstance(slots, list) or not slots:
        raise ValueError("slots harus berupa array dengan minimal satu slot.")
    parsed_slots = []
    seen = set()
    for item in slots:
        if not isinstance(item, dict):
            raise ValueError("Setiap item slots harus berupa object.")
        number = item.get("slot")
        if isinstance(number, bool) or not isinstance(number, int) or number not in (1, 2, 3):
            raise ValueError("Nomor slot hanya boleh 1, 2, atau 3.")
        if number in seen:
            raise ValueError("Nomor slot tidak boleh dikirim lebih dari sekali.")
        seen.add(number)
        status = item.get("status")
        if status is not None and status not in ("empty", "occupied"):
            raise ValueError("status slot harus 'empty' atau 'occupied'.")
        parsed_slots.append(
            {
                "slot": number,
                "status": status,
                "distance_cm": parse_optional_number(item.get("distance_cm"), "distance_cm"),
            }
        )
    return {
        "device_id": device_id.strip() if device_id else None,
        "slots": parsed_slots,
        "ldr_lux": parse_optional_number(payload.get("ldr_lux"), "ldr_lux"),
        "ir_entry": parse_optional_boolean(payload.get("ir_entry"), "ir_entry"),
        "ir_exit": parse_optional_boolean(payload.get("ir_exit"), "ir_exit"),
    }


def record_telemetry(payload):
    received_at = utc_now()
    with open_database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE device_state SET device_id = COALESCE(?, device_id),
            last_seen = ?, ldr_lux = COALESCE(?, ldr_lux),
            ir_entry = COALESCE(?, ir_entry), ir_exit = COALESCE(?, ir_exit),
            ir_entry_updated_at = CASE WHEN ? IS NULL THEN ir_entry_updated_at ELSE ? END,
            ir_exit_updated_at = CASE WHEN ? IS NULL THEN ir_exit_updated_at ELSE ? END
            WHERE id = 1""",
            (
                payload["device_id"],
                received_at,
                payload["ldr_lux"],
                payload["ir_entry"],
                payload["ir_exit"],
                payload["ir_entry"],
                received_at,
                payload["ir_exit"],
                received_at,
            ),
        )
        for reported in payload["slots"]:
            number = reported["slot"]
            previous = connection.execute(
                "SELECT status FROM slots WHERE slot = ?", (number,)
            ).fetchone()["status"]
            new_status = reported["status"]
            if new_status is not None:
                connection.execute(
                    """UPDATE slots SET status = ?, distance_cm = COALESCE(?, distance_cm), updated_at = ?
                    WHERE slot = ?""",
                    (new_status, reported["distance_cm"], received_at, number),
                )
                if new_status != previous:
                    if new_status == "occupied":
                        active = connection.execute(
                            "SELECT id FROM parking_sessions WHERE slot = ? AND exited_at IS NULL",
                            (number,),
                        ).fetchone()
                        if active is None:
                            connection.execute(
                                "INSERT INTO parking_sessions (slot, entered_at) VALUES (?, ?)",
                                (number, received_at),
                            )
                    elif previous == "occupied":
                        active = connection.execute(
                            """SELECT id, entered_at FROM parking_sessions
                            WHERE slot = ? AND exited_at IS NULL ORDER BY id DESC LIMIT 1""",
                            (number,),
                        ).fetchone()
                        if active is not None:
                            started = datetime.fromisoformat(active["entered_at"].replace("Z", "+00:00"))
                            ended = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
                            duration = max(0, int((ended - started).total_seconds()))
                            connection.execute(
                                """UPDATE parking_sessions SET exited_at = ?, duration_seconds = ?
                                WHERE id = ?""",
                                (received_at, duration, active["id"]),
                            )
            elif reported["distance_cm"] is not None:
                connection.execute(
                    "UPDATE slots SET distance_cm = ?, updated_at = ? WHERE slot = ?",
                    (reported["distance_cm"], received_at, number),
                )
            connection.execute(
                """INSERT INTO telemetry
                (received_at, device_id, slot, status, distance_cm, ldr_lux, ir_entry, ir_exit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    received_at,
                    payload["device_id"],
                    number,
                    new_status,
                    reported["distance_cm"],
                    payload["ldr_lux"],
                    payload["ir_entry"],
                    payload["ir_exit"],
                ),
            )
    return received_at


def get_status():
    with open_database() as connection:
        slots = [
            dict(row)
            for row in connection.execute(
                "SELECT slot, status, distance_cm, updated_at FROM slots ORDER BY slot"
            ).fetchall()
        ]
        device_row = connection.execute(
            """SELECT device_id, last_seen, ldr_lux, ir_entry, ir_exit,
            ir_entry_updated_at, ir_exit_updated_at FROM device_state WHERE id = 1"""
        ).fetchone()
    now = datetime.now(timezone.utc)
    last_seen = device_row["last_seen"]
    online = False
    if last_seen:
        last_seen_date = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        online = max(0, (now - last_seen_date).total_seconds()) <= DEVICE_TIMEOUT_SECONDS
    occupied = sum(1 for slot in slots if slot["status"] == "occupied")
    available = sum(1 for slot in slots if slot["status"] == "empty")
    return {
        "slots": slots,
        "device": {
            "id": device_row["device_id"],
            "status": "online" if online else "offline",
            "last_seen": last_seen,
        },
        "sensors": {
            "ldr_lux": device_row["ldr_lux"],
            "ir_entry": None if device_row["ir_entry"] is None else bool(device_row["ir_entry"]),
            "ir_exit": None if device_row["ir_exit"] is None else bool(device_row["ir_exit"]),
            "ir_entry_updated_at": device_row["ir_entry_updated_at"],
            "ir_exit_updated_at": device_row["ir_exit_updated_at"],
        },
        "summary": {
            "total": 3,
            "occupied": occupied,
            "available": available,
            "known_slots": sum(1 for slot in slots if slot["status"] is not None),
        },
        "server_time": utc_now(),
    }


def valid_date(value, parameter):
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError("{} harus berformat YYYY-MM-DD.".format(parameter))


def query_history(query):
    slot_values = query.get("slot", [])
    slot = slot_values[0] if slot_values else ""
    if slot and slot not in ("1", "2", "3"):
        raise ValueError("Filter slot hanya boleh 1, 2, atau 3.")
    start_date = valid_date((query.get("from") or [None])[0], "from")
    end_date = valid_date((query.get("to") or [None])[0], "to")
    search = ((query.get("q") or [""])[0] or "").strip()[:100]
    try:
        limit = int((query.get("limit") or ["200"])[0])
    except ValueError:
        raise ValueError("limit harus berupa angka.")
    if limit < 1 or limit > 200:
        raise ValueError("limit harus berada di antara 1 dan 200.")

    conditions = []
    values = []
    if slot:
        conditions.append("slot = ?")
        values.append(int(slot))
    if start_date:
        conditions.append("date(entered_at) >= ?")
        values.append(start_date)
    if end_date:
        conditions.append("date(entered_at) <= ?")
        values.append(end_date)
    if search:
        if search.casefold() in ("terisi", "aktif", "masih parkir"):
            conditions.append("exited_at IS NULL")
        elif search.casefold() in ("keluar", "selesai", "kosong"):
            conditions.append("exited_at IS NOT NULL")
        else:
            conditions.append(
                """(CAST(slot AS TEXT) LIKE ? OR CAST(id AS TEXT) LIKE ?
                OR entered_at LIKE ? OR COALESCE(exited_at, '') LIKE ?)"""
            )
            values.extend(["%{}%".format(search)] * 4)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with open_database() as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM parking_sessions" + where, values
        ).fetchone()[0]
        records = [
            dict(row)
            for row in connection.execute(
                """SELECT id, slot, entered_at, exited_at, duration_seconds
                FROM parking_sessions"""
                + where
                + " ORDER BY entered_at DESC LIMIT ?",
                values + [limit],
            ).fetchall()
        ]
    return {"records": records, "total": total}


def build_factual_answer(question, status, history):
    lowered = question.casefold()
    slots = status["slots"]
    unknown = [slot["slot"] for slot in slots if slot["status"] is None]
    available = [slot["slot"] for slot in slots if slot["status"] == "empty"]
    occupied = [slot["slot"] for slot in slots if slot["status"] == "occupied"]
    prefix = ""
    if unknown:
        prefix = "{} slot belum melaporkan status. ".format(len(unknown))

    if any(word in lowered for word in ("riwayat", "durasi", "kendaraan masuk", "kendaraan keluar")):
        records = history["records"][:5]
        if not records:
            return "Belum ada sesi parkir yang tersimpan di database."
        summaries = []
        for record in records:
            if record["exited_at"]:
                duration = format_duration(record["duration_seconds"])
                summaries.append(
                    "Slot {}: masuk {}, keluar {}, durasi {}.".format(
                        record["slot"], record["entered_at"], record["exited_at"], duration
                    )
                )
            else:
                summaries.append(
                    "Slot {}: kendaraan masuk {}, masih terparkir.".format(
                        record["slot"], record["entered_at"]
                    )
                )
        return "Riwayat terbaru dari database: " + " ".join(summaries)
    if any(word in lowered for word in ("ldr", "cahaya", "intensitas", "sensor ir")):
        sensors = status["sensors"]
        return "Data sensor pendukung: LDR {} lux; IR masuk {}; IR keluar {}.".format(
            sensors["ldr_lux"] if sensors["ldr_lux"] is not None else "belum tersedia",
            format_signal(sensors["ir_entry"]),
            format_signal(sensors["ir_exit"]),
        )
    if any(word in lowered for word in ("kosong", "tersedia", "parkir", "tempat", "slot")):
        if len(unknown) == 3:
            return "Belum ada status yang diterima dari ketiga sensor, jadi ketersediaan parkir belum dapat dipastikan."
        available_text = (
            "Slot {} terkonfirmasi kosong.".format(", ".join(str(number) for number in available))
            if available
            else "Tidak ada slot yang terkonfirmasi kosong."
        )
        occupied_text = (
            "Slot {} terkonfirmasi terisi.".format(", ".join(str(number) for number in occupied))
            if occupied
            else "Tidak ada slot yang terkonfirmasi terisi."
        )
        return "{}Saat ini {} slot terkonfirmasi tersedia dan {} slot terkonfirmasi terisi. {} {}".format(
            prefix, len(available), len(occupied), available_text, occupied_text
        ).strip()
    if any(word in lowered for word in ("berapa", "jumlah", "kapasitas", "analisis", "kondisi", "penuh")):
        if len(unknown) == 3:
            return "Belum ada data status dari sensor. Kapasitas terpakai belum dapat dihitung."
        percent = round(len(occupied) / 3 * 100)
        return "{}Dari 3 slot, {} terkonfirmasi terisi dan {} terkonfirmasi kosong; penggunaan terkonfirmasi {}%.".format(
            prefix, len(occupied), len(available), percent
        ).strip()
    return (
        "Saya hanya dapat menjawab berdasarkan data parkir yang tersedia. "
        "Tanyakan status slot, kapasitas, sensor LDR/IR, atau riwayat parkir."
    )


def format_signal(value):
    if value is None:
        return "belum tersedia"
    return "aktif" if value else "tidak aktif"


def format_duration(seconds):
    if seconds is None:
        return "belum selesai"
    minutes = seconds // 60
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return "{} hari {} jam".format(days, hours)
    if hours:
        return "{} jam {} menit".format(hours, minutes)
    return "{} menit".format(minutes)


def answer_with_model(question, status, history):
    configured = (AI_API_URL, AI_API_KEY, AI_MODEL)
    if not any(configured):
        return None
    if not all(configured):
        raise RuntimeError("Konfigurasi AI belum lengkap: isi AI_API_URL, AI_API_KEY, dan AI_MODEL.")
    context = {
        "parkir_saat_ini": status,
        "riwayat_terbaru": history["records"][:20],
    }
    payload = {
        "model": AI_MODEL,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Anda adalah asisten SmartPark berbahasa Indonesia. Jawab hanya berdasarkan "
                    "JSON konteks sistem berikut. Jangan mengarang status, jarak, waktu, atau "
                    "riwayat; jika data tidak tersedia, nyatakan tidak tersedia. Slot parkir "
                    "hanya bernomor 1 sampai 3. Konteks: "
                    + json.dumps(context, ensure_ascii=False)
                ),
            },
            {"role": "user", "content": question},
        ],
    }
    request = Request(
        AI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + AI_API_KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Model AI tidak dapat dihubungi: {}.".format(error))
    try:
        answer = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Respons dari model AI tidak memiliki format jawaban yang dikenali.")
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("Model AI mengembalikan jawaban kosong.")
    return answer.strip()


class SmartParkHandler(BaseHTTPRequestHandler):
    server_version = "SmartPark/1.0"

    def log_message(self, format_string, *args):
        sys.stderr.write(
            "{} - {}\n".format(self.address_string(), format_string % args)
        )

    def send_json(self, status_code, payload):
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Content-Length tidak valid.")
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("Ukuran request harus antara 1 dan 65536 byte.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Body harus berisi JSON UTF-8 yang valid.")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self.send_json(200, get_status())
            return
        if parsed.path == "/api/history":
            try:
                self.send_json(200, query_history(parse_qs(parsed.query)))
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            return
        if parsed.path == "/api/health":
            self.send_json(200, {"status": "ok", "server_time": utc_now()})
            return
        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/telemetry":
            if not DEVICE_API_TOKEN:
                self.send_json(
                    503,
                    {"error": "Penerimaan telemetry belum dikonfigurasi. Atur DEVICE_API_TOKEN di backend."},
                )
                return
            authorization = self.headers.get("Authorization", "")
            supplied_token = authorization[7:] if authorization.startswith("Bearer ") else ""
            supplied_token = self.headers.get("X-Device-Token", supplied_token)
            if not hmac.compare_digest(supplied_token, DEVICE_API_TOKEN):
                self.send_json(401, {"error": "Token perangkat tidak valid."})
                return
            try:
                payload = validate_telemetry(self.read_json())
                received_at = record_telemetry(payload)
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
                return
            self.send_json(202, {"accepted": True, "received_at": received_at})
            return
        if parsed.path == "/api/assistant":
            try:
                payload = self.read_json()
                question = payload.get("question") if isinstance(payload, dict) else None
                if not isinstance(question, str) or not question.strip() or len(question) > 1000:
                    raise ValueError("question wajib diisi dan maksimal 1000 karakter.")
                status = get_status()
                history = query_history({"limit": ["20"]})
                answer = answer_with_model(question.strip(), status, history)
                source = "model" if answer is not None else "rules"
                if answer is None:
                    answer = build_factual_answer(question.strip(), status, history)
                self.send_json(200, {"answer": answer, "source": source})
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            except RuntimeError as error:
                self.send_json(503, {"error": str(error)})
            return
        self.send_json(404, {"error": "Endpoint tidak ditemukan."})

    def serve_static(self, request_path):
        decoded_path = unquote(request_path)
        relative_path = "index.html" if decoded_path in ("", "/") else decoded_path.lstrip("/")
        target = (APP_DIR / relative_path).resolve()
        try:
            target.relative_to(APP_DIR)
        except ValueError:
            self.send_json(404, {"error": "File tidak ditemukan."})
            return
        if not target.is_file():
            self.send_json(404, {"error": "File tidak ditemukan."})
            return
        content = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)


def main():
    if DEVICE_TIMEOUT_SECONDS < 1:
        raise ValueError("DEVICE_TIMEOUT_SECONDS harus lebih besar dari nol.")
    initialize_database()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer((host, port), SmartParkHandler)
    print("SmartPark berjalan di http://{}:{}".format(host, port), flush=True)
    print("Database SQLite: {}".format(DATABASE_PATH), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMenghentikan SmartPark.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
