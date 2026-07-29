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

    def resolve_employee(self, work_email: str = "", national_id: str = "") -> Optional[dict]:
        p = {}
        if work_email:
            p["work_email"] = work_email
        elif national_id:
            p["national_id"] = national_id
        else:
            return None
        try:
            return self._get("/api/v1/api/employees/resolve", p)
        except Exception:
            return None


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
            },
            "proyectos": {
                "remotos": self.projects_seen,
                "enlazados": self.projects_linked,
                "sin_pareja": self.projects_unmatched,
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
      3. cédula
      4. nombre: ≥2 tokens en común (nombre + apellido)

    El criterio de nombre es el que rescata las fichas con correo de otro
    dominio, que por definición no pueden emparejar por correo.
    """
    r_uuid = (remote.get("id") or "").strip()
    r_email = norm_email(remote.get("work_email"))
    r_cid = norm_id(remote.get("national_id"))
    r_tokens = name_tokens(remote.get("full_name"))

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

    if r_cid:
        for obj in pool:
            if norm_id(getattr(obj, "phone", "") or "") == r_cid:
                add(obj, "cedula")

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

        for obj, how in matches:
            touched.add(id(obj))
            if how != "ya_enlazado":
                rep.bump(how)
            if dry_run:
                continue
            obj.external_ref = uuid
            # La empresa es maestro de la IDENTIDAD (nombre, cargo).
            if hasattr(obj, "name") and emp.get("full_name"):
                obj.name = emp["full_name"]
            if hasattr(obj, "full_name") and emp.get("full_name"):
                obj.full_name = emp["full_name"]
            if hasattr(obj, "role") and emp.get("position"):
                obj.role = emp["position"]
            if hasattr(obj, "position") and emp.get("position"):
                obj.position = emp["position"]
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

    locals_ = list(db.exec(select(Project).where(Project.tenant_id == tenant_id)).all())
    by_norm = {norm_text(p.name): p for p in locals_}

    for pr in remote:
        uuid = (pr.get("id") or "").strip()
        name = pr.get("name") or ""
        local = None
        for p in locals_:
            if (p.external_ref or "") == uuid and uuid:
                local = p
                break
        if local is None:
            local = by_norm.get(norm_text(name))

        if local is None:
            rep.projects_unmatched.append(name)
            continue

        rep.projects_linked.append({
            "uuid": uuid, "remoto": name, "acten": local.name,
            "miembros": len(pr.get("members") or []),
        })
        if not dry_run:
            local.external_ref = uuid
            local.managed_externally = True
            db.add(local)

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
