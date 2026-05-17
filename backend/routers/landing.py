"""Landing CMS — endpoints públicos + admin.

Tres superficies:

  GET  /api/public/landing            — PÚBLICO. Devuelve el contenido del
                                        landing del tenant 'acten' (defaults
                                        merged sobre el JSON guardado).
  PUT  /api/landing-cms/              — ADMIN del tenant 'acten'. Patch parcial.
  POST /api/landing-cms/reset         — ADMIN del tenant 'acten'. Volver a defaults.
  POST /api/public/landing/contact    — PÚBLICO. Envía el formulario de
                                        contacto al email administrativo
                                        configurado en el CMS.

  GET  /api/v1/landing_page_content   — Legacy stub. Mantiene shape antiguo
                                        para clientes que aún no migran.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, validator
from sqlmodel import Session, select

from database import DEFAULT_TENANT_SLUG, get_session
from models import Tenant, User
from routers.auth import get_current_tenant, require_admin
from services import landing_content_service


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Router 1 — público (no auth)
# ─────────────────────────────────────────────────────────────────────────────

public_router = APIRouter(prefix="/api/public", tags=["Landing (Público)"])


def _resolve_acten_tenant(db: Session) -> Tenant:
    tenant = db.exec(
        select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)
    ).first()
    if not tenant:
        # El tenant 'acten' siempre existe en producción (lo crea el bootstrap
        # de database.py). Si no, algo grave pasó — pero igual devolvemos los
        # defaults sin escribir, para que el landing no se caiga.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant principal no disponible.",
        )
    return tenant


@public_router.get("/landing")
def get_public_landing_content(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Devuelve el contenido del landing público.

    Sin auth — lo consume el SPA que sirve acten.app sin token.
    """
    tenant = _resolve_acten_tenant(db)
    return landing_content_service.get_landing_content(db, tenant.id)


@public_router.get("/landing/people")
def get_public_landing_people(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Personas reales del sistema para la sección 'testimonios'.

    Agrega owners de action_items (gente que recibió tareas) + ProjectContacts
    (participantes registrados por el admin) del tenant 'acten'. Dedup por
    email. Si hay un User con ese email, usamos su avatar_url y full_name.

    No expone emails para evitar harvesting — solo nombre + rol + avatar.
    Limita a 8 personas para que el grid del landing quepa.
    """
    from models import ActionItem, ProjectContact, Project, User

    tenant = _resolve_acten_tenant(db)

    # 1) owners de action_items (gente que recibió tareas en sesiones reales)
    items = db.exec(
        select(ActionItem.owner_name, ActionItem.owner_email)
        .where(ActionItem.tenant_id == tenant.id)
        .where(ActionItem.owner_email != "")
    ).all()

    # 2) ProjectContacts (participantes registrados de proyectos del tenant).
    #    `entity` = empresa/organización a la que pertenece el contacto —
    #    útil para mostrar "Gerente · Colpensiones" en la card.
    contacts = db.exec(
        select(ProjectContact.name, ProjectContact.email, ProjectContact.role, ProjectContact.entity)
        .join(Project, Project.id == ProjectContact.project_id)
        .where(Project.tenant_id == tenant.id)
        .where(ProjectContact.email != "")
    ).all()

    by_email: dict[str, dict[str, Any]] = {}
    for name, email in items:
        key = (email or "").lower().strip()
        if not key or key in by_email:
            continue
        by_email[key] = {"name": (name or "").strip(), "role": "", "entity": ""}
    for name, email, role, entity in contacts:
        key = (email or "").lower().strip()
        if not key:
            continue
        if key in by_email:
            if not by_email[key]["role"] and role:
                by_email[key]["role"] = role
            if not by_email[key]["name"] and name:
                by_email[key]["name"] = name
            if not by_email[key].get("entity") and entity:
                by_email[key]["entity"] = entity
        else:
            by_email[key] = {
                "name": (name or "").strip(),
                "role": (role or "").strip(),
                "entity": (entity or "").strip(),
            }

    # 3) Avatares: si el email matchea un User, traemos su avatar_url + position.
    # API_BASE_URL para absolutizar paths relativos (/static/avatars/...) — el
    # SPA del landing corre en acten.app pero los avatares viven en api.acten.app,
    # así que sin absolutización el browser pega 404 contra acten.app/static/...
    import os
    import unicodedata
    api_base = (os.environ.get("PUBLIC_BASE_URL") or "https://api.acten.app").rstrip("/")
    tenant_company = (tenant.name or "Acten").strip()

    def _absolutize(url: str) -> str:
        if not url:
            return ""
        if url.startswith(("http://", "https://", "data:")):
            return url
        if url.startswith("/"):
            return api_base + url
        return api_base + "/" + url

    if by_email:
        users = db.exec(
            select(User.email, User.avatar_url, User.full_name, User.position)
            .where(User.tenant_id == tenant.id)
        ).all()
        for email, avatar_url, full_name, position in users:
            key = (email or "").lower().strip()
            if key in by_email:
                if avatar_url:
                    by_email[key]["avatar_url"] = _absolutize(avatar_url)
                if full_name and (not by_email[key]["name"] or len(full_name) > len(by_email[key]["name"])):
                    by_email[key]["name"] = full_name
                # User.position SOBRESCRIBE el role de project_contact — el perfil
                # de usuario de plataforma es la verdad sobre el cargo de esa
                # persona, vs un role específico de proyecto. Antes solo seteaba
                # cuando el role venía vacío y Felipe (CEO en User, Líder Técnico
                # en project_contact) salía con "Líder Técnico".
                if position:
                    by_email[key]["role"] = f"{position} de {tenant_company}"
                # Flag: es usuario real de la plataforma (no solo project_contact).
                by_email[key]["is_platform_user"] = True

    def _initials(n: str) -> str:
        parts = [p for p in n.strip().split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    # 4) Dedup secundario por nombre normalizado. Cubre el caso típico:
    #    "Alejandro Cortes" (action_item) y "Alejandro Cortés Burgos"
    #    (project_contact) son la misma persona pero con email distinto.
    #    Agrupamos por primeros 2 tokens del nombre sin acentos/case y
    #    nos quedamos con la entrada más completa (avatar > role > nombre largo).
    def _normalize_name(n: str) -> str:
        s = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower()
        parts = [p for p in s.split() if p]
        return " ".join(parts[:2])

    by_norm_name: dict[str, dict[str, Any]] = {}
    for person in by_email.values():
        name = (person.get("name") or "").strip() or "Persona"
        key = _normalize_name(name) or name.lower()
        if key not in by_norm_name:
            by_norm_name[key] = {**person, "name": name}
            continue
        existing = by_norm_name[key]
        # Mejorar entrada: tomar avatar si la nueva lo tiene, role si falta,
        # entity si falta, nombre más largo (más completo) si aplica.
        if person.get("avatar_url") and not existing.get("avatar_url"):
            existing["avatar_url"] = person["avatar_url"]
        if person.get("role") and not existing.get("role"):
            existing["role"] = person["role"]
        if person.get("entity") and not existing.get("entity"):
            existing["entity"] = person["entity"]
        if name and len(name) > len(existing.get("name") or ""):
            existing["name"] = name

    # 5) Filtro de calidad — el landing es vitrina pública, evitamos nombres
    #    que no se ven serios en un sitio comercial:
    #    - 1 sola palabra ("Angiecita", "Federico") suele ser nickname interno
    #    - Que empiezan con "Equipo " ("Equipo de Infraestructura") son
    #      grupos no personas.
    def _is_display_worthy(name: str) -> bool:
        name = (name or "").strip()
        if not name:
            return False
        parts = name.split()
        if len(parts) < 2:
            return False
        if name.lower().startswith(("equipo ", "grupo ", "team ")):
            return False
        return True

    out: list[dict[str, Any]] = []
    for person in by_norm_name.values():
        name = person.get("name") or "Persona"
        if not _is_display_worthy(name):
            continue
        # Componer el role mostrado: si tenemos role + entity
        # (Gerente · Colpensiones) lo unimos. Si solo role, va como está.
        # Si solo entity, mostramos "Equipo · Empresa".
        base_role = (person.get("role") or "").strip()
        entity = (person.get("entity") or "").strip()
        if base_role and entity:
            shown_role = f"{base_role} · {entity}"
        elif base_role:
            shown_role = base_role
        elif entity:
            shown_role = f"Equipo · {entity}"
        else:
            shown_role = f"Equipo {tenant_company}"
        out.append({
            "name": name,
            "role": shown_role,
            "company": entity,            # útil si el frontend quiere mostrarlo aparte
            "avatar_url": person.get("avatar_url") or "",
            "initials": _initials(name),
            "_is_platform_user": person.get("is_platform_user", False),
            "_has_real_role": bool(base_role or entity),
        })

    # Sort de "prueba social":
    #   1. Avatar real primero (visual más rico)
    #   2. Usuarios de plataforma (Felipe → CEO de Acten) antes que solo
    #      project_contacts (sin User detrás)
    #   3. Que tengan un role real (no el default genérico)
    #   4. Alfabético como tiebreaker
    out.sort(key=lambda p: (
        0 if p["avatar_url"] else 1,
        0 if p["_is_platform_user"] else 1,
        0 if p["_has_real_role"] else 1,
        p["name"].lower(),
    ))
    # Limpia los flags internos antes de devolver
    for p in out:
        p.pop("_is_platform_user", None)
        p.pop("_has_real_role", None)
    return {"items": out[:8]}


@public_router.get("/landing/trust-logos")
def get_public_trust_logos(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Lista de empresas reales (tenants activos) que aparecen en la trust band.

    Sin auth. Excluye el tenant 'acten' (es la propia plataforma).
    Si el tenant tiene logo subido en branding_json, lo expone como data URL;
    si no, el frontend renderiza el name como wordmark. Cualquier campo extra
    de branding (company_name) tiene prioridad sobre el slug.
    """
    import json
    tenants = db.exec(
        select(Tenant)
        .where(Tenant.is_active == True)  # noqa: E712
        .where(Tenant.slug != DEFAULT_TENANT_SLUG)
    ).all()
    out: list[Dict[str, Any]] = []
    for t in tenants:
        brand: Dict[str, Any] = {}
        if t.branding_json:
            try:
                brand = json.loads(t.branding_json) or {}
            except (json.JSONDecodeError, TypeError):
                brand = {}
        display_name = (brand.get("company_name") or t.name or t.slug).strip()
        logo = (brand.get("logo_data_url") or "").strip()
        # Si es data URL o URL absoluta servible, la pasamos. Cualquier otro
        # valor raro queda como cadena vacía → el frontend cae a wordmark.
        if not (logo.startswith("data:") or logo.startswith("http")):
            logo = ""
        out.append({
            "slug": t.slug,
            "name": display_name,
            "logo_url": logo,
        })
    # Ordenamos: primero los que tienen logo (jerarquía visual), luego alfabético.
    out.sort(key=lambda x: (0 if x["logo_url"] else 1, x["name"].lower()))
    return {"items": out}


# ─── Contact form ───────────────────────────────────────────────────────────
#
# Rate limit naïve in-memory (por IP). En producción real conviene reemplazar
# por slowapi o Redis, pero para un form de contacto del landing alcanza con
# evitar spam burst desde un solo origen.
_CONTACT_RATE: Dict[str, list[float]] = defaultdict(list)
_CONTACT_RATE_WINDOW_SECONDS = 60 * 15   # 15 minutos
_CONTACT_RATE_MAX = 10                    # máximo 10 mensajes por IP / 15 min
# Subido de 3/10min → 10/15min. El límite anterior se disparaba con poca
# actividad legítima (usuario interno probando, equipo demo, IP compartida
# de oficina). 10 es seguro contra spam burst sin frustrar uso normal.


# Validación de email casera para evitar la dependencia `email-validator`
# (`pydantic.EmailStr`). Cubre el 99% de casos legítimos sin agregar deps.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ContactSubmission(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=5, max_length=240)
    company: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    message: str = Field(min_length=10, max_length=4000)
    # Honeypot: cualquier valor no vacío en este campo invisible = bot.
    website: str | None = Field(default="")

    @validator("email")
    def _validate_email(cls, v: str) -> str:
        v = (v or "").strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("Email inválido.")
        return v.lower()


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    window_start = now - _CONTACT_RATE_WINDOW_SECONDS
    hits = [t for t in _CONTACT_RATE[ip] if t >= window_start]
    if len(hits) >= _CONTACT_RATE_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados mensajes desde tu IP. Vuelve a intentar en unos minutos.",
        )
    hits.append(now)
    _CONTACT_RATE[ip] = hits


def _render_contact_email_html(
    db: Session,
    tenant: Tenant,
    payload: "ContactSubmission",
    client_ip: str,
    msg_id: int,
) -> str:
    """Renderiza el correo de contacto usando el template branded común
    (email_base.html + email_landing_contact.html). Mismo look que los
    correos de sesiones — header con logo del tenant, paleta de marca,
    footer con datos corporativos.

    Si el render del template falla por cualquier razón, cae a un HTML
    mínimo para que el admin reciba algo y pueda atender al contacto.
    """
    import datetime as _dt
    try:
        from services.email_service import EmailService
        es = EmailService(db=db, tenant_id=tenant.id)
        template = es.jinja_env.get_template("email_landing_contact.html")
        admin_inbox_url = os.environ.get("ADMIN_BASE_URL", "https://admin.acten.app").rstrip("/") \
            + "/admin/landing-cms/mensajes"
        return template.render(
            brand=es.branding,
            current_year=_dt.datetime.now().year,
            contact_name=payload.name,
            contact_email=payload.email,
            contact_company=payload.company or "",
            contact_role=payload.role or "",
            contact_message=payload.message,
            contact_ip=client_ip,
            contact_created_at=_dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            admin_inbox_url=admin_inbox_url,
        )
    except Exception:
        logger.exception("contact form: render del template falló — fallback HTML mínimo.")
        # Fallback simple para no perder el envío si el template está roto.
        safe = lambda s: (s or "—").replace("<", "&lt;").replace(">", "&gt;")
        return (
            f"<h2>Nuevo mensaje desde el landing</h2>"
            f"<p><b>Nombre:</b> {safe(payload.name)}</p>"
            f"<p><b>Email:</b> {safe(payload.email)}</p>"
            f"<p><b>Empresa:</b> {safe(payload.company)}</p>"
            f"<p><b>Cargo:</b> {safe(payload.role)}</p>"
            f"<p><b>Mensaje:</b></p><pre>{safe(payload.message)}</pre>"
        )


@public_router.post("/landing/contact")
async def submit_contact_form(
    payload: ContactSubmission,
    request: Request,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Recibe el form de contacto del landing y lo envía por email.

    - Honeypot anti-bot
    - Rate limit por IP
    - Email destino: `contact.email` del CMS (defaultea a hola@acten.app)
    """
    # Honeypot: si el bot rellenó el campo invisible, devolvemos 200 sin
    # hacer nada (no le damos pista de que detectamos el ataque).
    if (payload.website or "").strip():
        logger.info("contact form honeypot triggered, dropping silently")
        return {"status": "ok"}

    client_ip = (request.client.host if request.client else "unknown") or "unknown"
    _check_rate_limit(client_ip)

    tenant = _resolve_acten_tenant(db)
    content = landing_content_service.get_landing_content(db, tenant.id)
    contact_block = content.get("contact", {}) or {}
    target_email = (contact_block.get("email") or "").strip() or "hola@acten.app"

    subject = f"[Contacto Landing] {payload.name} — {payload.company or 'Sin empresa'}"

    # 1) Persistir el mensaje SIEMPRE — pase lo que pase con el email, el
    #    admin debe poder verlo en su UI. Esto es la verdad: el SMTP es solo
    #    "notificación adicional" para que llegue a la bandeja.
    from models import ContactMessage, Notification, User as UserModel
    cm = ContactMessage(
        tenant_id=tenant.id,
        name=payload.name.strip(),
        email=payload.email.strip(),
        company=(payload.company or "").strip() or None,
        role=(payload.role or "").strip() or None,
        message=payload.message.strip(),
        ip=client_ip,
        user_agent=(request.headers.get("user-agent") or "")[:500],
        referer=(request.headers.get("referer") or "")[:500],
        status="new",
        email_status="pending",
    )
    db.add(cm)
    db.commit()
    db.refresh(cm)

    # 2) Enviar el email notificación al destinatario configurado en CMS.
    sent_flag = False
    error_msg = ""
    try:
        from services.email_service import EmailService
        email_service = EmailService(db=db, tenant_id=tenant.id)
        logger.info(
            "contact form email attempt: tenant=%s api_key_set=%s from=%s to=%s msg_id=%s",
            tenant.slug, bool(email_service.api_key),
            email_service.from_email, target_email, cm.id,
        )
        if not email_service.api_key:
            logger.warning(
                "contact form: tenant '%s' NO tiene Resend api_key configurado "
                "en IntegrationSetting(provider_name='smtp'). El correo NO se envía "
                "— se simula en consola. Configurar en /admin/settings.",
                tenant.slug,
            )
        # Render del email con el template branded común (email_base.html +
        # email_landing_contact.html). Mismo look que el resto de los correos
        # de la plataforma (sesiones, action items, welcome, etc.).
        html = _render_contact_email_html(db, tenant, payload, client_ip, cm.id)
        sent_flag = await email_service._send_html_email(
            to_email=target_email,
            subject=subject,
            html_content=html,
        )
        if sent_flag:
            cm.email_status = "sent"
            logger.info("contact form email OK msg_id=%s → %s", cm.id, target_email)
        else:
            cm.email_status = "failed"
            cm.email_error = "EmailService devolvió sent=False sin excepción (revisar SMTP config)"
            logger.warning("contact form email no se envió (sent=False) msg_id=%s", cm.id)
    except Exception as exc:
        error_msg = str(exc)[:400]
        cm.email_status = "failed"
        cm.email_error = error_msg
        logger.exception("contact form: excepción enviando email msg_id=%s — %s", cm.id, error_msg)
        # No tiramos 500: el mensaje YA está persistido. El usuario fue
        # atendido (queda visible en /admin/landing-cms-mensajes).
    finally:
        try:
            db.add(cm); db.commit()
        except Exception:
            db.rollback()

    # 3) Notificar a los admins del tenant (campana del topbar). 1 notif
    #    por admin para que cada uno la cierre individualmente.
    try:
        from sqlmodel import select as _select
        admin_users = db.exec(
            _select(UserModel)
            .where(UserModel.tenant_id == tenant.id)
            .where(UserModel.is_active == True)  # noqa: E712
        ).all()
        # Filtramos en Python al rol "admin" o is_superadmin para evitar
        # depender de schema de role (relación lazy puede ser lenta).
        for u in admin_users:
            role_name = (u.role.name if u.role else "").lower()
            if role_name not in ("admin",) and not u.is_superadmin:
                continue
            db.add(Notification(
                tenant_id=tenant.id,
                user_id=u.id,
                kind="contact_message",
                title=f"Nuevo mensaje del landing — {payload.name}",
                body=(payload.message or "")[:200],
                link_to="/admin/landing-cms/mensajes",
                entity_type="contact_message",
                entity_id=cm.id,
            ))
        db.commit()
    except Exception:
        logger.exception("contact form: no pude crear Notifications para admins")
        db.rollback()

    return {"status": "ok", "id": cm.id}


@public_router.get("/landing/_email_test_send")
async def landing_email_test_send(
    to: str | None = None,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """DIAGNOSTIC — fuerza un envío REAL con la config actual y devuelve el
    resultado exacto. Acepta ?to= para probar contra un buzón controlado
    (útil para descartar si el destinatario por defecto no existe).
    """
    tenant = _resolve_acten_tenant(db)
    from services.email_service import EmailService
    es = EmailService(db=db, tenant_id=tenant.id)
    content = landing_content_service.get_landing_content(db, tenant.id)
    default_target = (content.get("contact", {}).get("email") or "").strip() or "hola@acten.app"
    target = (to or default_target).strip()

    # Validación mínima de email para no llamar Resend con basura
    if "@" not in target or "." not in target.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Param ?to= con email inválido.")

    result: Dict[str, Any] = {
        "from_email": es.from_email,
        "to_email": target,
        "used_default_to": to is None,
        "api_key_set": bool(es.api_key),
    }
    try:
        # Llamamos directo a Resend para capturar el response ID y poder
        # cruzarlo con el dashboard si hace falta.
        import resend
        if es.api_key:
            # Usamos el MISMO template branded que recibe el admin en producción.
            # Antes este endpoint mandaba HTML inline crudo y el test no
            # reflejaba el look real → la verificación visual no servía.
            mock_payload = ContactSubmission(
                name="Visitante de prueba",
                email="diagnostic@example.com",
                company="Empresa de Prueba",
                role="Cargo de prueba",
                message=(
                    "Este es un envío DE DIAGNÓSTICO disparado desde "
                    "/api/public/landing/_email_test_send. Si recibes este "
                    "correo con la marca de Acten (logo + colores + footer), "
                    "el form de contacto del landing está bien configurado.\n\n"
                    "Datos del envío:\n"
                    f"  · From: {es.from_email}\n"
                    f"  · To:   {target}\n"
                ),
                website="",
            )
            html_body = _render_contact_email_html(
                db=db, tenant=tenant, payload=mock_payload,
                client_ip="diagnostic", msg_id=0,
            )
            resend.api_key = es.api_key
            response = resend.Emails.send({
                "from": es.from_email,
                "to": target,
                "subject": "[DIAG] Test desde Acten landing — diagnóstico",
                "html": html_body,
            })
            result["status"] = "sent"
            result["resend_id"] = response.get("id") if isinstance(response, dict) else str(response)
            result["template"] = "email_landing_contact.html (branded)"
            result["error"] = None
        else:
            result["status"] = "no_api_key"
            result["error"] = "EmailService.api_key vacío — modo simulación"
    except Exception as exc:
        result["status"] = "exception"
        result["error_type"] = type(exc).__name__
        result["error_message"] = str(exc)[:800]
    return result


@public_router.get("/landing/_email_status")
def landing_email_status(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """DIAGNOSTIC — endpoint público que reporta si el email del tenant 'acten'
    está bien configurado para enviar el form de contacto. No expone el API
    key ni nada sensible — solo banderas + el from/to addresses.
    """
    tenant = _resolve_acten_tenant(db)
    from services.email_service import EmailService
    es = EmailService(db=db, tenant_id=tenant.id)
    content = landing_content_service.get_landing_content(db, tenant.id)
    target = (content.get("contact", {}).get("email") or "").strip() or "hola@acten.app"
    return {
        "tenant_slug": tenant.slug,
        "tenant_id": tenant.id,
        "api_key_configured": bool(es.api_key),
        "api_key_length": len(es.api_key) if es.api_key else 0,
        "from_email": es.from_email,
        "contact_to_email": target,
        "hint": (
            "Si api_key_configured=False, el correo NO se envía (modo simulación). "
            "Configura el Resend API key en /admin/settings → SMTP del tenant acten."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Router 2 — admin (auth + rol admin + solo tenant 'acten')
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/landing-cms", tags=["Landing CMS (Admin)"])


def _require_acten_tenant(tenant: Tenant = Depends(get_current_tenant)) -> Tenant:
    """Solo el admin del tenant 'acten' puede editar el contenido del landing."""
    if tenant.slug != DEFAULT_TENANT_SLUG:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el tenant principal (Acten) puede editar el landing público.",
        )
    return tenant


@router.get("/")
def admin_get_landing(
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(_require_acten_tenant),
) -> Dict[str, Any]:
    """Versión admin del GET — devuelve el mismo shape que /api/public/landing."""
    return landing_content_service.get_landing_content(db, tenant.id)


@router.put("/")
def admin_put_landing(
    patch: Dict[str, Any],
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(_require_acten_tenant),
) -> Dict[str, Any]:
    """Patch parcial sobre el contenido. Acepta sección a sección o el JSON
    completo — sólo las claves presentes en `patch` se sobrescriben.
    """
    if not isinstance(patch, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El payload debe ser un objeto JSON.",
        )
    return landing_content_service.update_landing_content(db, tenant.id, patch)


@router.post("/reset")
def admin_reset_landing(
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(_require_acten_tenant),
) -> Dict[str, Any]:
    """Vuelve al contenido por defecto. Útil si el admin rompió algo."""
    return landing_content_service.reset_landing_content(db, tenant.id)


# ─────────────────────────────────────────────────────────────────────────────
# Mensajes del formulario de contacto — bandeja para el admin
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/messages")
def admin_list_contact_messages(
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(_require_acten_tenant),
) -> Dict[str, Any]:
    """Lista los mensajes del form de contacto, recientes primero.

    Filtra opcional por estado ('new' | 'read' | 'replied' | 'archived').
    Limita a 100 por default — la bandeja del admin no necesita pagination
    todavía (volumen esperado bajo).
    """
    from models import ContactMessage
    q = select(ContactMessage).where(ContactMessage.tenant_id == tenant.id)
    if status:
        q = q.where(ContactMessage.status == status)
    q = q.order_by(ContactMessage.created_at.desc()).limit(max(1, min(limit, 500)))
    rows = db.exec(q).all()
    unread = db.exec(
        select(ContactMessage)
        .where(ContactMessage.tenant_id == tenant.id)
        .where(ContactMessage.status == "new")
    ).all()
    return {
        "items": [
            {
                "id": m.id,
                "name": m.name,
                "email": m.email,
                "company": m.company or "",
                "role": m.role or "",
                "message": m.message,
                "ip": m.ip or "",
                "status": m.status,
                "email_status": m.email_status,
                "email_error": m.email_error or "",
                "read_at": m.read_at,
                "replied_at": m.replied_at,
                "created_at": m.created_at,
            }
            for m in rows
        ],
        "unread_count": len(unread),
    }


@router.patch("/messages/{msg_id}")
def admin_update_contact_message(
    msg_id: int,
    patch: Dict[str, Any],
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(_require_acten_tenant),
) -> Dict[str, Any]:
    """Actualiza el estado de un mensaje. Solo acepta 'status' (whitelist
    de valores) para evitar tocar campos sensibles desde el frontend."""
    import datetime as _dt
    from models import ContactMessage
    msg = db.get(ContactMessage, msg_id)
    if not msg or msg.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
    new_status = (patch.get("status") or "").strip()
    if new_status not in ("new", "read", "replied", "archived"):
        raise HTTPException(
            status_code=400,
            detail="Estado inválido. Use: new, read, replied o archived.",
        )
    now = _dt.datetime.now().isoformat()
    msg.status = new_status
    if new_status == "read" and not msg.read_at:
        msg.read_at = now
    if new_status == "replied" and not msg.replied_at:
        msg.replied_at = now
    db.add(msg); db.commit(); db.refresh(msg)
    return {"id": msg.id, "status": msg.status}


@router.delete("/messages/{msg_id}")
def admin_delete_contact_message(
    msg_id: int,
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(_require_acten_tenant),
) -> Dict[str, Any]:
    """Borra definitivamente un mensaje. Operación irreversible."""
    from models import ContactMessage
    msg = db.get(ContactMessage, msg_id)
    if not msg or msg.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
    db.delete(msg); db.commit()
    return {"deleted": msg_id}


# ─────────────────────────────────────────────────────────────────────────────
# Router 3 — legacy stub (mantener para no romper clientes viejos)
# ─────────────────────────────────────────────────────────────────────────────

legacy_router = APIRouter(prefix="/api/v1", tags=["Landing (Legacy)"])


class _LegacyLandingContent(BaseModel):
    title: str
    description: str
    features: list[str]


@legacy_router.get("/landing_page_content", response_model=_LegacyLandingContent)
def get_legacy_landing_content(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Legacy — versión simplificada del contenido para clientes que no se
    migraron al nuevo endpoint público."""
    tenant = db.exec(
        select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)
    ).first()
    if tenant:
        content = landing_content_service.get_landing_content(db, tenant.id)
    else:
        content = landing_content_service.DEFAULT_CONTENT
    return {
        "title": content.get("hero", {}).get("title", "Acten"),
        "description": content.get("hero", {}).get("subtitle", ""),
        "features": [
            (f.get("title") or "")
            for f in (content.get("features", {}).get("items") or [])
            if f.get("title")
        ],
    }
