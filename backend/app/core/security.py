"""Primitivas criptográficas: contraseñas y tokens.

- Contraseñas: Argon2id (lento a propósito, resistente a ataques con GPU).
- Tokens de sesión: aleatorios de 256 bits. Como ya son impredecibles, basta
  con SHA-256 para guardarlos (no hace falta un hash lento).
"""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # parámetros recomendados por la librería (Argon2id)

# Hash de una contraseña cualquiera. Se usa cuando el correo no existe, para que
# la respuesta tarde lo mismo y no revele qué correos están registrados.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True si el hash se creó con parámetros más débiles que los actuales."""
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(token: str, expected_hash: str) -> bool:
    # compare_digest tarda lo mismo aunque difieran en el primer carácter.
    return hmac.compare_digest(hash_token(token), expected_hash)
