"""Cliente PULL de la plataforma de Servicios/RRHH.

Acten **consume** la API de Servicios (ellos son el maestro de empleados y
proyectos) y deja los datos idénticos de este lado, enlazados por el UUID
del empleado (`external_ref`).

Contrato: `docs/INTEGRACION_ACTEN_RRHH.md`

Reglas críticas:

* **La incremental pide `status=all`**, no `active`. Si filtramos por
  activos, quien deja de serlo *desaparece* del resultado en vez de llegar
  marcado como inactivo — y Acten lo mantendría activo para siempre,
  asignándole tareas después de haberse ido.
* **Se respeta `429` + `Retry-After`.** Su tope es 120 req/min.
* **La llave es el UUID**, nunca el correo: la gente cambia de correo.
* Nunca se piden ni almacenan salario, datos bancarios ni documentos.
"""

from __future__ import annotations

import logging
import os
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from sqlmodel import Session, select

from models import Project, ProjectContact, User

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3


# ── Normalización para emparejar ──────────────────────────────────────

def norm_email(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def norm_text(v: Optional[str]) -> str:
    """minúsculas, sin acentos, espacios colapsados."""
    s = unicodedata.normalize("NFD", (v or "").strip().lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.split())


def norm_project(v: Optional[str]) -> str:
    """Nombre de proyecto comparable: quita sufijos entre paréntesis.

    «Softnexus (interno)» y «Softnexus» son el mismo proyecto — el
    paréntesis es una aclaración humana, no parte del nombre.
    """
    import re
    s = re.sub(r"\([^)]*\)", " ", v or "")
    return norm_text(s)


def norm_id(v: Optional[str]) -> str:
    """Cédula: solo dígitos."""
    return "".join(c for c in (v or "") if c.isdigit())


def name_tokens(v: Optional[str]) -> set[str]:
    return {t for t in norm_text(v).split() if len(t) >= 3}


# ── Cliente HTTP ──────────────────────────────────────────────────────

class ServiciosClient:
    """Cliente de la API externa. Honra `429` con `Retry-After`."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.base_url = (base_url or os.getenv("SERVICIOS_API_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("SERVICIOS_API_KEY", "")

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        if not self.configured:
            raise RuntimeError(
                "Falta configurar SERVICIOS_API_BASE_URL / SERVICIOS_API_KEY."
            )
        url = f"{self.base_url}{path}"
        headers = {"X-API-Key": self.api_key}
        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                with httpx.Client(timeout=DEFAULT_TIMEOUT) as c:
                    r = c.get(url, params=params or {}, headers=headers)
                if r.status_code == 429:
                    wait = float(r.headers.get("Retry-After", "5") or 5)
                    logger.warning("Servicios 429 — esperando %.0fs", wait)
                    time.sleep(min(wait, 60))
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("Servicios GET %s intento %s falló: %s",
                               path, attempt + 1, exc)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"No se pudo consultar {path}: {last_exc}")

    def employees(self, status: str = "all", updated_since: Optional[str] = None) -> list[dict]:
        p: dict[str, Any] = {"status": status}
        if updated_since:
            p["updated_since"] = updated_since
        return self._get("/api/v1/api/employees", p).get("items", []) or []

    def projects(self, status: str = "active") -> list[dict]:
        return self._get("/api/v1/api/projects", {"status": status}).get("items", []) or []

    def resolve_employee(
        self, work_email: str = "", name: str = "",
    ) -> tuple[Optional[dict], str]:
        """Resuelve una persona contra su directorio. Devuelve (ficha, estado).

        Estados: `ok` · `ambiguo` (409) · `no_existe` (404) · `error`.

        **El 409 y el 404 significan cosas distintas y no se tratan igual.**
        409 = la persona existe pero el nombre no basta para distinguirla
        (tienen dos hermanos Cortés Burgos); hay que escoger a mano, nunca
        adivinar — asignar mal es darle a alguien las actas de otro.
        404 = no está dada de alta.
        """
        p: dict[str, Any] = {}
        if work_email:
            p["work_email"] = work_email
        elif name:
            p["name"] = name
        else:
            return None, "error"
        if not self.configured:
            return None, "error"
        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as c:
                r = c.get(
                    f"{self.base_url}/api/v1/api/employees/resolve",
                    params=p, headers={"X-API-Key": self.api_key},
                )
            if r.status_code == 200:
                return r.json(), "ok"
            if r.status_code == 409:
                return r.json(), "ambiguo"
            if r.status_code == 404:
                return None, "no_existe"
            return None, "error"
        except Exception as exc:  # noqa: BLE001
            logger.warning("resolve falló (%s): %s", p, exc)
            return None, "error"


# ── Resultado del sync ────────────────────────────────────────────────

@dataclass
class SyncReport:
    dry_run: bool = True
    employees_seen: int = 0
    matched_by: dict[str, int] = field(default_factory=dict)
    linked: list[dict] = field(default_factory=list)
    already_linked: int = 0
    unmatched_remote: list[dict] = field(default_factory=list)
    unmatched_local: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    projects_seen: int = 0
    projects_linked: list[dict] = field(default_factory=list)
    projects_unmatched: list[str] = field(default_factory=list)
    contacts_created: list[dict] = field(default_factory=list)
    duplicate_users: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def bump(self, key: str) -> None:
        self.matched_by[key] = self.matched_by.get(key, 0) + 1

    def as_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "employees": {
                "remotos": self.employees_seen,
                "ya_enlazados": self.already_linked,
                "enlazados_ahora": len(self.linked),
                "por_criterio": self.matched_by,
                "detalle": self.linked,
                "sin_pareja_en_acten": self.unmatched_remote,
                "en_acten_sin_pareja_remota": self.unmatched_local,
                "desactivados": self.deactivated,
                "cuentas_duplicadas": self.duplicate_users,
            },
            "proyectos": {
                "remotos": self.projects_seen,
                "enlazados": self.projects_linked,
                "creados_en_acten": self.projects_unmatched,
                "contactos_creados": self.contacts_created,
            },
            "errores": self.errors,
        }


# ── Emparejamiento ────────────────────────────────────────────────────

def _match_people(
    remote: dict,
    contacts: list[ProjectContact],
    users: list[User],
) -> list[tuple[Any, str]]:
    """Empareja un empleado remoto con TODAS sus fichas en Acten.

    Una misma persona puede tener **varias filas** porque participa en
    varios proyectos, y en cada uno puede figurar con un correo distinto
    (ej. Felipe con `@nexura.com` en el proyecto de ese cliente y con
    `@softnexus.io` en el interno). Todas esas fichas son la misma
    persona y deben apuntar al mismo UUID.

    Criterios, de más fiable a menos:
      1. `external_ref` ya asignado
      2. correo corporativo exacto
      3. nombre: ≥2 tokens en común

    **NO se empareja por cédula**: sus `national_id` no tienen formato
    homogéneo (unos con separadores de millar, otros sin) y solo produce
    falsos negativos. Lo normalizarán más adelante.

    Para el nombre se usa `display_name` («Felipe Cortés») y no
    `full_name` («Andrés Felipe Cortés Burgos»): el legal lleva piezas que
    nadie usa al nombrar a la persona, y con dos hermanos Cortés Burgos
    comparar el legal completo acerca peligrosamente a confundirlos.
    """
    r_uuid = (remote.get("id") or "").strip()
    r_email = norm_email(remote.get("work_email"))
    # display_name es el nombre por el que se le llama — el que coincide
    # con nuestras fichas. Fallback al legal si aún no viene.
    r_tokens = name_tokens(remote.get("display_name") or remote.get("full_name"))

    pool: list[Any] = list(contacts) + list(users)
    out: list[tuple[Any, str]] = []
    seen: set[int] = set()

    def add(obj: Any, how: str) -> None:
        if id(obj) not in seen:
            seen.add(id(obj))
            out.append((obj, how))

    for obj in pool:
        if r_uuid and (getattr(obj, "external_ref", None) or "") == r_uuid:
            add(obj, "ya_enlazado")

    if r_email:
        for obj in pool:
            if norm_email(getattr(obj, "email", "")) == r_email:
                add(obj, "correo")

    if len(r_tokens) >= 2:
        for obj in pool:
            local = getattr(obj, "name", None) or getattr(obj, "full_name", "")
            if len(r_tokens & name_tokens(local)) >= 2:
                add(obj, "nombre")

    return out


def sync_employees(
    db: Session, tenant_id: int, client: ServiciosClient,
    *, dry_run: bool = True, report: Optional[SyncReport] = None,
) -> SyncReport:
    """Trae el directorio y enlaza por UUID. `status=all` a propósito."""
    rep = report or SyncReport(dry_run=dry_run)
    remote = client.employees(status="all")
    rep.employees_seen = len(remote)

    contacts = list(db.exec(
        select(ProjectContact)
        .join(Project, Project.id == ProjectContact.project_id)
        .where(Project.tenant_id == tenant_id)
    ).all())
    users = list(db.exec(select(User).where(User.tenant_id == tenant_id)).all())

    touched: set[int] = set()

    for emp in remote:
        uuid = (emp.get("id") or "").strip()
        status = (emp.get("status") or "").lower()
        matches = _match_people(emp, contacts, users)

        if not matches:
            if status == "active":
                rep.unmatched_remote.append({
                    "id": uuid, "nombre": emp.get("full_name"),
                    "correo": emp.get("work_email"),
                })
            continue

        nuevos = [(o, h) for o, h in matches if h != "ya_enlazado"]
        rep.already_linked += len(matches) - len(nuevos)

        if nuevos:
            rep.linked.append({
                "uuid": uuid,
                "remoto": emp.get("full_name"),
                "fichas_en_acten": [
                    {
                        "acten": getattr(o, "name", None) or getattr(o, "full_name", ""),
                        "correo": getattr(o, "email", ""),
                        "criterio": h,
                    }
                    for o, h in nuevos
                ],
            })

        # Un empleado puede tener VARIAS fichas de contacto (una por
        # proyecto), pero solo UNA cuenta de usuario: `uq_user_external_ref`
        # lo impone porque de esa cuenta cuelgan los permisos. Si hay más
        # de un User candidato (ej. la misma persona con dos correos), se
        # enlaza el de coincidencia por correo y el resto se reporta.
        user_matches = [(o, h) for o, h in matches if isinstance(o, User)]
        chosen_user = None
        if user_matches:
            chosen_user = next(
                (o for o, h in user_matches if h in ("ya_enlazado", "correo")),
                user_matches[0][0],
            )
            for o, _ in user_matches:
                if o is not chosen_user:
                    rep.duplicate_users.append({
                        "empleado": emp.get("display_name") or emp.get("full_name"),
                        "cuenta_no_enlazada": getattr(o, "email", ""),
                    })

        for obj, how in matches:
            if isinstance(obj, User) and obj is not chosen_user:
                continue
            touched.add(id(obj))
            if how != "ya_enlazado":
                rep.bump(how)
            if dry_run:
                continue
            obj.external_ref = uuid
            # La empresa es maestro de la IDENTIDAD (nombre, cargo).
            # En pantalla va `display_name` («Felipe Cortés»), no el legal
            # («Andrés Felipe Cortés Burgos»), que es para contratos.
            shown = emp.get("display_name") or emp.get("full_name")
            position = emp.get("position")
            # Explícito por tipo: `ProjectContact.role` es texto, pero
            # `User.role` es una RELACIÓN al modelo Role — asignarle una
            # cadena rompe el mapeo de SQLAlchemy.
            if isinstance(obj, ProjectContact):
                if shown:
                    obj.name = shown
                if position:
                    obj.role = position
            elif isinstance(obj, User):
                if shown:
                    obj.full_name = shown
                if position:
                    obj.position = position
            # El CORREO NO se pisa: en cada proyecto la persona puede
            # figurar con un correo distinto (el del cliente), y eso es
            # deliberado. Solo se rellena si está vacío.
            if not (getattr(obj, "email", "") or "").strip() and emp.get("work_email"):
                obj.email = emp["work_email"]
            # Alguien que dejó de estar activo se desactiva de este lado.
            if status in ("inactive", "draft") and hasattr(obj, "is_active"):
                obj.is_active = False
            db.add(obj)

        if status in ("inactive", "draft"):
            rep.deactivated.append(emp.get("full_name") or uuid)

    for c in contacts:
        if id(c) not in touched:
            rep.unmatched_local.append(f"{c.name} <{c.email}>")

    if not dry_run:
        db.commit()
    return rep


def sync_projects(
    db: Session, tenant_id: int, client: ServiciosClient,
    *, dry_run: bool = True, report: Optional[SyncReport] = None,
) -> SyncReport:
    """Enlaza proyectos por nombre normalizado y marca `managed_externally`."""
    rep = report or SyncReport(dry_run=dry_run)
    remote = client.projects(status="active")
    rep.projects_seen = len(remote)
    # Directorio indexado por UUID: se usa al crear fichas de integrantes.
    try:
        emp_by_uuid = {(e.get("id") or "").strip(): e for e in client.employees(status="all")}
    except Exception:
        emp_by_uuid = {}

    locals_ = list(db.exec(select(Project).where(Project.tenant_id == tenant_id)).all())
    by_norm = {norm_project(p.name): p for p in locals_}

    for pr in remote:
        uuid = (pr.get("id") or "").strip()
        name = pr.get("name") or ""
        local = None
        for p in locals_:
            if (p.external_ref or "") == uuid and uuid:
                local = p
                break
        if local is None:
            local = by_norm.get(norm_project(name))

        if local is None:
            # Ellos son el maestro de proyectos: lo que exista allá y no
            # aquí se ESPEJA. Un proyecto sin integrantes todavía no es un
            # error — se poblará después y hay que recogerlo entonces.
            rep.projects_unmatched.append(name)
            if not dry_run:
                local = Project(
                    tenant_id=tenant_id,
                    name=name,
                    description=pr.get("client_name") or "",
                    external_ref=uuid,
                    managed_externally=True,
                    is_active=(pr.get("status") or "active") == "active",
                )
                db.add(local)
                db.commit()
                db.refresh(local)
                locals_.append(local)
            else:
                continue

        miembros = pr.get("members") or []
        rep.projects_linked.append({
            "uuid": uuid, "remoto": name, "acten": local.name,
            "miembros": len(miembros),
        })
        if not dry_run:
            local.external_ref = uuid
            local.managed_externally = True
            db.add(local)
            # Integrantes: crear la ficha si falta y aplicar el correo POR
            # PROYECTO (`members[].email`), que puede ser el del cliente.
            # Si viene vacío, se conserva/cae al corporativo.
            for m in miembros:
                emp_uuid = (m.get("employee_id") or "").strip()
                if not emp_uuid:
                    continue
                m_email = (m.get("email") or "").strip()
                existentes = list(db.exec(
                    select(ProjectContact)
                    .where(ProjectContact.project_id == local.id)
                    .where(ProjectContact.external_ref == emp_uuid)
                ).all())
                if existentes:
                    if m_email:
                        for c in existentes:
                            c.email = m_email
                            db.add(c)
                    continue
                # No existe la ficha en este proyecto → crearla con los
                # datos del directorio (ellos mandan sobre la identidad).
                emp = emp_by_uuid.get(emp_uuid) or {}
                db.add(ProjectContact(
                    project_id=local.id,
                    name=emp.get("display_name") or emp.get("full_name") or "",
                    email=m_email or emp.get("work_email") or "",
                    role=m.get("role") or emp.get("position") or "",
                    entity=pr.get("client_name") or "",
                    external_ref=emp_uuid,
                ))
                rep.contacts_created.append({
                    "proyecto": local.name,
                    "persona": emp.get("display_name") or emp.get("full_name") or emp_uuid,
                })

    if not dry_run:
        db.commit()
    return rep


def run_full_sync(
    db: Session, tenant_id: int, *, dry_run: bool = True,
    client: Optional[ServiciosClient] = None,
) -> dict:
    c = client or ServiciosClient()
    rep = SyncReport(dry_run=dry_run)
    try:
        sync_projects(db, tenant_id, c, dry_run=dry_run, report=rep)
    except Exception as exc:  # noqa: BLE001
        rep.errors.append(f"proyectos: {exc}")
    try:
        sync_employees(db, tenant_id, c, dry_run=dry_run, report=rep)
    except Exception as exc:  # noqa: BLE001
        rep.errors.append(f"empleados: {exc}")
    return rep.as_dict()
