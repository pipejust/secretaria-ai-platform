"""Una persona, un nombre — aunque la transcripción diga cuatro.

El mismo compañero aparece como «Juan Diego Toro», «JDiego Toro», «Juan
Toro» y «TON618 Toro», que es su apodo en la videollamada. Cada variante
se contaba como alguien distinto: carril propio en el tablero, fila
propia en el informe de carga, y la sensación de que hay más gente de la
que hay.

El registro vive en base (`PersonAlias`) y no en el código porque estas
grafías aparecen constantemente y corregir una no debería costar un
despliegue.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

from sqlmodel import Session, select

from models import PersonAlias

logger = logging.getLogger(__name__)


def normalizar(nombre: Optional[str]) -> str:
    """Forma con la que se compara: sin tildes, sin signos, en minúsculas."""
    v = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", v.lower()).strip()


def cargar(db: Session, tenant_id: int) -> dict[str, tuple[str, str]]:
    """alias normalizado → (nombre bueno, correo bueno)."""
    filas = db.exec(
        select(PersonAlias).where(PersonAlias.tenant_id == tenant_id)
    ).all()
    return {f.alias: (f.canonical_name, f.canonical_email or "") for f in filas}


def canonizar(
    tabla: dict[str, tuple[str, str]],
    nombre: Optional[str],
    correo: Optional[str] = None,
) -> tuple[str, str]:
    """Devuelve (nombre, correo) ya unificados.

    El correo que venga se respeta si el alias no trae uno: puede ser el
    de un proyecto concreto, y pisarlo con el corporativo rompería el
    enlace con la ficha de ese cliente.
    """
    clave = normalizar(nombre)
    if not clave or clave not in tabla:
        return (nombre or "").strip(), (correo or "").strip()
    bueno_nombre, bueno_correo = tabla[clave]
    return bueno_nombre, (correo or "").strip() or bueno_correo


def registrar(
    db: Session, tenant_id: int, alias: str,
    canonical_name: str, canonical_email: str = "",
) -> Optional[PersonAlias]:
    """Añade un alias. Idempotente."""
    clave = normalizar(alias)
    if not clave or clave == normalizar(canonical_name):
        return None
    ya = db.exec(
        select(PersonAlias)
        .where(PersonAlias.tenant_id == tenant_id)
        .where(PersonAlias.alias == clave)
    ).first()
    if ya:
        ya.canonical_name = canonical_name
        ya.canonical_email = canonical_email or ya.canonical_email
        db.add(ya)
        return ya
    fila = PersonAlias(
        tenant_id=tenant_id, alias=clave,
        canonical_name=canonical_name, canonical_email=canonical_email,
    )
    db.add(fila)
    return fila
