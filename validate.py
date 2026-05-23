from dataclasses import dataclass
import os
from urllib.parse import quote

from coderag.storage.postgres_startup import ensure_postgres_schema_ready


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} debe estar definido para ejecutar validate.py")
    return value


def _build_postgres_dsn(
    *,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
) -> str:
    host_part = host
    if ":" in host and not host.startswith("["):
        host_part = f"[{host}]"
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@"
        f"{host_part}:{port}/{quote(database, safe='')}"
    )


@dataclass
class Config:
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    runtime_environment: str = "development"
    postgres_pool_size: int = 5
    postgres_pool_timeout: int = 30

    def resolve_postgres_dsn(self) -> str:
        return _build_postgres_dsn(
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
            user=self.postgres_user,
            password=self.postgres_password,
        )

    def resolve_postgres_startup_policy(self) -> str:
        return "validate" if self.runtime_environment == "production" else "auto_upgrade"


def main() -> None:
    postgres_host = _required_env("POSTGRES_HOST")
    postgres_port = int(_required_env("POSTGRES_PORT"))
    postgres_user = _required_env("POSTGRES_USER")
    postgres_password = _required_env("POSTGRES_PASSWORD")

    database_names = {
        "Empty": "e63883492579",
        "Legacy": "l63883492579",
        "Incomplete": "i63883492579",
    }
    for label, database_name in database_names.items():
        print(f"--- {label} ---")
        settings = Config(
            postgres_host=postgres_host,
            postgres_port=postgres_port,
            postgres_db=database_name,
            postgres_user=postgres_user,
            postgres_password=postgres_password,
        )
        try:
            result = ensure_postgres_schema_ready(settings)
            print(f"DSN: {settings.resolve_postgres_dsn()}")
            print(f"Result: {result}")
        except Exception as exc:
            print(f"Error: {exc}")


if __name__ == "__main__":
    main()
