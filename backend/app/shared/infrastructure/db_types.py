"""Database type decorators for cross-dialect compatibility.

Enables native PostgreSQL UUID in production and CHAR(36) in SQLite test environments.
"""

import uuid
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import CHAR, TypeDecorator


class GUID(TypeDecorator):
    """Platform-independent GUID/UUID type.

    Uses PostgreSQL's UUID type natively, otherwise uses CHAR(36), storing as
    Python uuid.UUID objects.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == "postgresql":
            return str(value) if not isinstance(value, uuid.UUID) else value
        else:
            if not isinstance(value, uuid.UUID):
                return str(uuid.UUID(str(value)))
            else:
                return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            if not isinstance(value, uuid.UUID):
                return uuid.UUID(str(value))
            else:
                return value
