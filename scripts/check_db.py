"""Verificação de integridade do banco do Click Beleza (somente leitura).

Uso: python scripts/check_db.py   (requer CLICK_BELEZA_DATABASE_URL)
Sai com código 1 se encontrar inconsistências.
"""
import os
import sys
from collections import Counter

import psycopg

ACTIVE = {"solicitado", "confirmado"}


def main():
    url = os.environ.get("CLICK_BELEZA_DATABASE_URL")
    if not url:
        print("Defina CLICK_BELEZA_DATABASE_URL.")
        return 2
    problems = []
    with psycopg.connect(url, connect_timeout=5) as connection:
        row = connection.execute("SELECT payload FROM click_beleza_state LIMIT 1").fetchone()
        if not row:
            print("Banco vazio: nenhum estado salvo.")
            return 1
        state = row[0]
        slots = connection.execute("SELECT booking_id, professional_id, day FROM booking_slots").fetchall()

    professionals = {item["id"] for item in state["professionals"]}
    bookings = {item["id"]: item for item in state["bookings"]}
    users = state["users"]

    for name, values in (
        ("profissionais", [item["id"] for item in state["professionals"]]),
        ("agendamentos", [item["id"] for item in state["bookings"]]),
        ("usuários", [item["id"] for item in users]),
    ):
        duplicates = [key for key, count in Counter(values).items() if count > 1]
        if duplicates:
            problems.append(f"IDs duplicados em {name}: {duplicates}")
    emails = Counter(user.get("email", "").casefold() for user in users)
    problems += [f"E-mail duplicado: {mail}" for mail, count in emails.items() if count > 1]
    problems += [
        f"Usuário {user['id']} aponta para profissional inexistente"
        for user in users if user.get("professionalId") and user["professionalId"] not in professionals
    ]
    problems += [
        f"Agendamento {bid} aponta para profissional inexistente"
        for bid, item in bookings.items() if item.get("professionalId") not in professionals
    ]
    slot_owners = {booking_id for booking_id, _, _ in slots}
    problems += [f"Faixas órfãs sem agendamento ativo: {bid}" for bid in slot_owners
                 if bid not in bookings or bookings[bid].get("status") not in ACTIVE]
    problems += [f"Agendamento ativo sem faixas reservadas: {bid}" for bid, item in bookings.items()
                 if item.get("status") in ACTIVE and bid not in slot_owners]
    problems += [f"Senha sem hash no usuário {user['id']}" for user in users if not user.get("passwordHash")]

    print(f"Profissionais: {len(professionals)} | Agendamentos: {len(bookings)} | Contas: {len(users)} | Faixas: {len(slots)}")
    print("\n".join(problems) if problems else "Banco íntegro.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
