"""Backup e restauração lógicos do banco do Click Beleza.

  python scripts/backup_db.py backup [pasta]           cria backups/AAAAMMDD-HHMMSS.backup.json
  python scripts/backup_db.py restore arquivo.json     restaura (exige --confirmar)

Usa CLICK_BELEZA_DATABASE_URL. O arquivo contém dados pessoais e hashes de senha:
guarde-o criptografado e fora do repositório (a pasta backups/ já é ignorada pelo Git).
Para produção, prefira também pg_dump agendado e backups gerenciados do provedor.
"""
import json
import os
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import consulta_cep as backend  # noqa: E402


def backup(folder):
    url = os.environ["CLICK_BELEZA_DATABASE_URL"]
    backend.DATABASE_URL = url
    backend.ensure_schema()
    with psycopg.connect(url, connect_timeout=5) as connection:
        row = connection.execute("SELECT payload FROM click_beleza_state WHERE id = 1").fetchone()
        slots = connection.execute(
            "SELECT professional_id, day::text, slot, booking_id FROM booking_slots ORDER BY 1, 2, 3"
        ).fetchall()
    snapshot = {"createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "state": row[0] if row else None, "slots": slots}
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{time.strftime('%Y%m%d-%H%M%S')}.backup.json"
    target.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    print(f"Backup criado: {target} ({len(slots)} faixas de horário)")


def restore(path):
    url = os.environ["CLICK_BELEZA_DATABASE_URL"]
    backend.DATABASE_URL = url
    backend.ensure_schema()
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    backend._validate_data(snapshot["state"])
    with psycopg.connect(url, connect_timeout=5) as connection:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (backend.DataLock.ADVISORY_KEY,))
            connection.execute(
                "INSERT INTO click_beleza_state (id, payload) VALUES (1, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload",
                (json.dumps(snapshot["state"], ensure_ascii=False),),
            )
            connection.execute("DELETE FROM booking_slots")
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO booking_slots (professional_id, day, slot, booking_id) VALUES (%s, %s, %s, %s)",
                    snapshot["slots"],
                )
    print(f"Restaurado de {path} ({len(snapshot['slots'])} faixas de horário)")


def main(argv):
    if not os.environ.get("CLICK_BELEZA_DATABASE_URL"):
        print("Defina CLICK_BELEZA_DATABASE_URL.")
        return 2
    if len(argv) >= 2 and argv[1] == "backup":
        backup(Path(argv[2]) if len(argv) > 2 else Path(__file__).resolve().parent.parent / "backups")
        return 0
    if len(argv) >= 3 and argv[1] == "restore":
        if "--confirmar" not in argv:
            print("A restauração substitui os dados atuais. Repita com --confirmar.")
            return 2
        restore(Path(argv[2]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
