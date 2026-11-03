"""Crea la empresa y el administrador inicial a partir del .env.

Uso (desde la carpeta backend):
    python -m scripts.create_admin

Es seguro ejecutarlo varias veces: si el administrador ya existe, no cambia nada.
"""

import sys

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import PROJECT_ROOT
from app.db.session import SessionLocal
from app.services.auth import WeakPasswordError, create_initial_admin


class InitialAdminSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    admin_email: str
    admin_initial_password: SecretStr
    company_name: str


def main() -> int:
    config = InitialAdminSettings()
    with SessionLocal() as db:
        try:
            admin, created = create_initial_admin(
                db,
                email=config.admin_email,
                password=config.admin_initial_password.get_secret_value(),
                company_name=config.company_name,
            )
        except WeakPasswordError as exc:
            print(f"[FALLA] {exc}")
            return 1
        db.commit()

    if created:
        print(f"[OK] Administrador creado: {admin.email} (rol: {admin.role.value})")
        print("     La contraseña es ADMIN_INITIAL_PASSWORD del archivo .env")
    else:
        print(f"[OK] El administrador {admin.email} ya existía. No se modificó nada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
