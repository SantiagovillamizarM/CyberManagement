import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey, enum_column
from app.models.enums import AdminRole


class AdminUser(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "admin_users"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    # Se guarda siempre en minúsculas (lo garantiza el servicio).
    email: Mapped[str] = mapped_column(String(254), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[AdminRole] = mapped_column(enum_column(AdminRole), default=AdminRole.ADMIN)
    is_active: Mapped[bool] = mapped_column(default=True)
    last_login_at: Mapped[datetime | None]
    failed_login_attempts: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None]

    sessions: Mapped[list["AdminSession"]] = relationship(back_populates="admin_user")


class AdminSession(UUIDPrimaryKey, Base):
    """Sesión del panel. Solo se guarda el hash del token: si roban la BD, no sirve."""

    __tablename__ = "admin_sessions"

    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    revoked_at: Mapped[datetime | None]

    admin_user: Mapped[AdminUser] = relationship(back_populates="sessions")
