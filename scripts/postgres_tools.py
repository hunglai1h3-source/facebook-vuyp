"""Credential-safe PostgreSQL command helpers; importing never connects or mutates."""
import hashlib
import os
from urllib.parse import parse_qs, unquote, urlsplit


def database_parameters(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
            raise ValueError
        if any(character.isspace() for character in parsed.hostname):
            raise ValueError
        parameters = {
            "host": parsed.hostname, "port": parsed.port or 5432,
            "dbname": unquote(parsed.path.lstrip("/")),
            "user": unquote(parsed.username or ""), "password": unquote(parsed.password or ""),
            "connect_timeout": int(os.environ.get("POSTGRES_CONNECT_TIMEOUT", "15")),
        }
        query = parse_qs(parsed.query)
        for key in ("sslmode", "sslrootcert", "sslcert", "sslkey"):
            if key in query:
                parameters[key] = query[key][-1]
        if os.environ.get('APP_ENV', '').lower() in {'production', 'prod'} and parameters['host'] not in {'127.0.0.1', 'localhost', '::1'}:
            parameters.setdefault('sslmode', os.environ.get('POSTGRES_SSLMODE', 'require'))
            if parameters['sslmode'] not in {'require', 'verify-ca', 'verify-full'}:
                if os.environ.get('ALLOW_INSECURE_POSTGRES', '').lower() in {'true', '1', 'yes'}:
                    pass
                else:
                    raise ValueError
        return parameters
    except (TypeError, ValueError):
        raise ValueError("A valid PostgreSQL DATABASE_URL is required.") from None


def database_identity(url):
    values = database_parameters(url)
    return values["host"].lower().rstrip("."), values["port"], values["dbname"]


def libpq_environment(url):
    """Never inherit connection overrides or expose credentials in process argv."""
    values = database_parameters(url)
    child_env = {key: value for key, value in os.environ.items()
                 if not key.upper().startswith("PG") and key not in {"DATABASE_URL", "RESTORE_DATABASE_URL"}}
    mapping = {
        "host": "PGHOST", "port": "PGPORT", "dbname": "PGDATABASE",
        "user": "PGUSER", "password": "PGPASSWORD", "connect_timeout": "PGCONNECT_TIMEOUT",
        "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT", "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY",
    }
    child_env.update({mapping[key]: str(value) for key, value in values.items()})
    return child_env


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
