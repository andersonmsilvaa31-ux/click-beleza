import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import consulta_cep


def main():
    if not consulta_cep.DATABASE_URL:
        raise RuntimeError("Configure CLICK_BELEZA_DATABASE_URL antes de criar o administrador.")
    email = os.environ.get("CLICK_BELEZA_ADMIN_LOGIN", "admin@clickbeleza.local").strip().casefold()
    if not email:
        raise RuntimeError("Configure um login válido para o administrador.")
    data = consulta_cep.load_data()
    if any(user.get("role") == "admin" for user in data["users"]):
        raise RuntimeError("Já existe uma conta de administrador. Nenhuma senha foi alterada.")
    if any(user.get("email", "").casefold() == email for user in data["users"]):
        raise RuntimeError("Este login já está associado a outra conta.")
    password = secrets.token_urlsafe(24)
    data["users"].append(consulta_cep.new_user(email, password, "admin"))
    consulta_cep.save_data(data)
    print(f"Login do administrador: {email}")
    print(f"Senha inicial (mostrada uma vez): {password}")
    print("Guarde a senha em um gerenciador seguro; ela não pode ser recuperada do banco.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"Erro: {error}", file=sys.stderr)
        raise SystemExit(1)
