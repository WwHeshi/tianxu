"""Authenticated encryption for secrets stored in PostgreSQL."""

import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import app_environment

DEVELOPMENT_MASTER_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
CREDENTIAL_ENCRYPTION_VERSION = "v1"


class SecretEncryptionError(RuntimeError):
    """Raised when the master key or encrypted value cannot be used safely."""


def _decode_master_key(encoded: str) -> bytes:
    try:
        key = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise SecretEncryptionError("APP_ENCRYPTION_KEY 必须是 Base64 编码") from exc
    if len(key) != 32:
        raise SecretEncryptionError("APP_ENCRYPTION_KEY 解码后必须恰好为 32 字节")
    return key


@dataclass(frozen=True)
class SecretCipher:
    key: bytes

    @classmethod
    def from_environment(cls) -> "SecretCipher":
        encoded = os.getenv("APP_ENCRYPTION_KEY", "").strip()
        if not encoded:
            raise SecretEncryptionError("服务端尚未配置 APP_ENCRYPTION_KEY")
        if encoded == DEVELOPMENT_MASTER_KEY and app_environment() not in {
            "development",
            "local",
            "test",
        }:
            raise SecretEncryptionError("生产环境禁止使用默认开发主密钥")
        return cls(_decode_master_key(encoded))

    def encrypt(self, plaintext: str, *, scope: str, key_version: str) -> str:
        nonce = os.urandom(12)
        associated_data = f"tianxu:model-credential:{scope}:{key_version}".encode()
        ciphertext = AESGCM(self.key).encrypt(nonce, plaintext.encode(), associated_data)
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, token: str, *, scope: str, key_version: str) -> str:
        try:
            payload = base64.urlsafe_b64decode(token.encode("ascii"))
            if len(payload) < 29:
                raise ValueError("encrypted payload is too short")
            nonce, ciphertext = payload[:12], payload[12:]
            associated_data = f"tianxu:model-credential:{scope}:{key_version}".encode()
            plaintext = AESGCM(self.key).decrypt(nonce, ciphertext, associated_data)
            return plaintext.decode()
        except (UnicodeError, binascii.Error, InvalidTag, ValueError) as exc:
            raise SecretEncryptionError("模型 API 密钥无法解密，请重新保存设置") from exc
