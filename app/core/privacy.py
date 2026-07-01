import re
from typing import Any

class DataMaskingMiddleware:
    EMAIL_REGEX = re.compile(r'[\w\.-]+@[\w\.-]+\.\w+')
    CREDENTIAL_REGEX = re.compile(r'(password|token|key|secret)\s*[:=]\s*[\'"]?[\w\-\.\!\@\#\$\%\^\&\*]+[\'"]?', re.IGNORECASE)

    @classmethod
    def redact_pii(cls, val: Any) -> Any:
        if isinstance(val, str):
            # Redact emails
            val = cls.EMAIL_REGEX.sub("[EMAIL]", val)
            # Redact credential patterns
            val = cls.CREDENTIAL_REGEX.sub(r"\1: [REDACTED_CREDENTIAL]", val)
            return val
        elif isinstance(val, dict):
            return {k: cls.redact_pii(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [cls.redact_pii(x) for x in val]
        return val


class SecretManager:
    """Secures credentials, preventing hardcoded keys or leaking secrets in logs/traces."""
    @staticmethod
    def get_secret(name: str, default: str = "") -> str:
        import os
        return os.getenv(name, default)
