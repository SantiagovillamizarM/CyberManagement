"""Login, logout y datos del administrador conectado."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CSRF_COOKIE, SESSION_COOKIE, get_current_admin, get_current_session
from app.core.config import get_settings
from app.db.session import get_db
from app.models import AdminSession, AdminUser
from app.schemas.auth import AdminOut, LoginRequest
from app.services import auth

router = APIRouter(prefix="/auth", tags=["autenticación"])


class LoginResponse(BaseModel):
    admin: AdminOut
    csrf_token: str  # el panel lo envía en la cabecera X-CSRF-Token


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> LoginResponse:
    try:
        admin = auth.authenticate(db, body.email, body.password)
    except auth.InvalidCredentialsError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Correo o contraseña incorrectos")

    new = auth.create_session(
        db,
        admin,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    settings = get_settings()
    max_age = settings.session_absolute_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        new.token,
        max_age=max_age,
        httponly=True,  # el JavaScript de la página no puede leerla
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api",
    )
    response.set_cookie(
        CSRF_COOKIE,
        new.csrf_token,
        max_age=max_age,
        httponly=False,  # el panel sí necesita leerla
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return LoginResponse(admin=AdminOut.model_validate(admin), csrf_token=new.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session: AdminSession = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> None:
    auth.revoke_session(session)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/api")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/me", response_model=AdminOut)
def me(admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
    return admin
