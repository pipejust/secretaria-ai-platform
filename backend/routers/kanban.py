"""Tablero Kanban de tareas dentro de Acten.

Los campos `kanban_column` y `kanban_order` existían desde el sprint de
integración, pero solo los usaba la API pública: dentro de Acten no había
tablero, solo la lista de pendientes.

**Las columnas del tablero son los estados de la tarea.** No se inventa un
eje aparte: si la columna y el estado fueran cosas distintas, un mismo
pendiente podría estar «hecho» en la lista y «en curso» en el tablero, y
nadie sabría cuál creer. Arrastrar una tarjeta cambia el estado, con las
mismas reglas de transición que aplica la API — `cancelada` es terminal y
se rechaza con un mensaje claro en vez de moverse a medias.

`kanban_order` guarda la posición dentro de la columna. `kanban_column`
se sigue respetando para la plataforma externa, que sí puede tener
columnas propias; el tablero de Acten no lo usa para colocar.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import ActionItem, MeetingSession, Project, Tenant, User
from routers.auth import get_current_tenant, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kanban", tags=["Kanban"])

# Orden de izquierda a derecha. `cancelled` va al final porque es donde
# muere una tarea, no donde se trabaja.
COLUMNAS = [
    {"key": "pending",   "titulo": "Por hacer"},
    {"key": "blocked",   "titulo": "Bloqueada"},
    {"key": "done",      "titulo": "Hecha"},
    {"key": "cancelled", "titulo": "Cancelada"},
]
CLAVES = [c["key"] for c in COLUMNAS]

# Mismas reglas que la API pública (`integration_v1.VALID_TRANSITIONS`).
TRANSICIONES: dict[str, set[str]] = {
    "pending":   {"blocked", "done", "cancelled"},
    "blocked":   {"pending", "done", "cancelled"},
    "done":      {"pending"},
    "cancelled": set(),
}


# Carril de las tareas que no ha cogido nadie.
SIN_DUENO = "__sin_dueno__"


# Nombres que en realidad son dos personas: «Alejandro Cortés y Felipe
# Cortés». Nunca sirven de puente entre carriles — ver `Identidades`.
_SEPARADORES = (" y ", " & ", " + ", "/", ",", ";", " and ")


class Identidades:
    """Agrupa las mil formas de escribir a la misma persona.

    Dos cosas que enseñaron los datos reales, y ninguna era obvia:

    **El correo no sirve para agrupar.** Hay 6 tareas de «Lady Edith
    Ardila» con `owner_email = fcortes@nexura.com`, y otras 14 con «Por
    asignar» en ese campo. Agrupar por correo juntaba a Lady con Felipe.
    El correo se usa solo para mostrar y para «solo mías».

    **Un nombre compuesto hace de puente.** Solo hay dos tareas con
    «Alejandro Cortés y Felipe Cortés», pero bastaron para fundir los dos
    carriles en uno de 179 tarjetas: comparte dos palabras con cada uno.
    Los compuestos tienen carril propio y **no entran en el índice**.

    Lo que queda: se agrupan dos nombres cuando **uno contiene al otro**
    palabra por palabra. «Alejandro» entra en «Alejandro Cortés»; «Lady
    Ardila», en «Lady Edith Ardila Ramirez». Dos nombres que solo se
    cruzan a medias se quedan separados — mostrar un carril de más es
    barato; fundir a dos personas, no.
    """

    def __init__(self) -> None:
        self.indice: list[tuple[frozenset[str], str]] = []
        self.etiqueta: dict[str, str] = {}
        self.correo_de: dict[str, Optional[str]] = {}

    @staticmethod
    def es_compuesto(nombre: Optional[str]) -> bool:
        n = f" {(nombre or '').strip().lower()} "
        return any(sep in n for sep in _SEPARADORES)

    def clave(self, nombre: Optional[str], correo: Optional[str]) -> str:
        from services.servicios_sync import name_tokens, norm_email

        toks = frozenset(name_tokens(nombre))
        em = norm_email(correo)

        if not toks:
            # Sin nombre utilizable, el correo es lo único que hay.
            if not em:
                return SIN_DUENO
            self._registrar(em, nombre, em, indexar=False)
            return em

        if self.es_compuesto(nombre):
            k = " ".join(sorted(toks))
            self._registrar(k, nombre, em, indexar=False)
            return k

        encontrada: Optional[str] = None
        for t, k in self.indice:
            if toks <= t or t <= toks:
                encontrada = k
                break
        if encontrada is None:
            encontrada = " ".join(sorted(toks))
        self._registrar(encontrada, nombre, em, indexar=True, toks=toks)
        return encontrada

    def _registrar(
        self, clave: str, nombre: Optional[str], correo: Optional[str],
        *, indexar: bool, toks: Optional[frozenset[str]] = None,
    ) -> None:
        if indexar and toks:
            # Se guarda el conjunto más amplio visto: así «Alejandro» y
            # «Alejandro Cortés B» siguen cayendo en el mismo carril.
            for i, (t, k) in enumerate(self.indice):
                if k == clave:
                    self.indice[i] = (t | toks, k)
                    break
            else:
                self.indice.append((toks, clave))
        n = (nombre or "").strip()
        if n:
            actual = self.etiqueta.get(clave, "")
            if not actual or len(n.split()) > len(actual.split()):
                self.etiqueta[clave] = n
        if correo and not self.correo_de.get(clave):
            self.correo_de[clave] = correo


def _persona(item: ActionItem, ident: Identidades) -> dict[str, Any]:
    """Quién carga con la tarea. Clave agrupada por identidad real."""
    from services.owners import limpiar, tiene_responsable

    if not tiene_responsable(item.owner_name, item.owner_email):
        return {"clave": SIN_DUENO, "nombre": None, "correo": None}
    correo = limpiar(item.owner_email)
    nombre = limpiar(item.owner_name)
    clave = ident.clave(nombre, correo)
    if clave == SIN_DUENO:
        return {"clave": SIN_DUENO, "nombre": None, "correo": None}
    return {
        "clave": clave,
        "nombre": ident.etiqueta.get(clave) or nombre,
        "correo": ident.correo_de.get(clave) or correo,
    }


@router.get("")
def tablero(
    project_id: Optional[int] = Query(None),
    solo_mias: bool = Query(False),
    incluir_cerradas: bool = Query(
        False, description="Traer también hechas y canceladas.",
    ),
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
):
    """Tarjetas agrupadas por columna, con su persona para las calles."""
    from services.owners import tiene_responsable

    q = select(ActionItem).where(ActionItem.tenant_id == tenant.id)
    if project_id is not None:
        q = q.join(
            MeetingSession, MeetingSession.id == ActionItem.session_id,
        ).where(MeetingSession.project_id == project_id)
    items = list(db.exec(q).all())

    proyectos = {
        p.id: p.name
        for p in db.exec(select(Project).where(Project.tenant_id == tenant.id)).all()
    }
    ses_proy = {
        s.id: s.project_id
        for s in db.exec(
            select(MeetingSession).where(MeetingSession.tenant_id == tenant.id)
        ).all()
    }

    # «Solo mías» por correo **y** por nombre: la mayoría de las tareas
    # traen el nombre que dijo la transcripción y ningún correo, así que
    # filtrar solo por correo dejaría el tablero casi vacío.
    from services.servicios_sync import name_tokens
    mio = (user.email or "").strip().lower()
    mis_tokens = name_tokens(user.full_name) | name_tokens(
        (user.email or "").split("@")[0].replace(".", " ")
    )
    ident = Identidades()
    gente: dict[str, dict] = {}
    por_columna: dict[str, list] = {k: [] for k in CLAVES}

    for it in items:
        estado = it.status if it.status in CLAVES else "pending"
        if not incluir_cerradas and estado in ("done", "cancelled"):
            continue
        p = _persona(it, ident)
        if solo_mias:
            suyo = (p["correo"] or "").lower() == mio
            if not suyo and mis_tokens:
                t = name_tokens(p["nombre"])
                suyo = bool(t) and (t <= mis_tokens or mis_tokens <= t)
            if not suyo:
                continue

        if p["clave"] not in gente:
            gente[p["clave"]] = {
                "clave": p["clave"], "nombre": p["nombre"],
                "correo": p["correo"], "tarjetas": 0,
            }
        gente[p["clave"]]["tarjetas"] += 1

        pid = ses_proy.get(it.session_id)
        por_columna[estado].append({
            "id": it.id,
            "titulo": it.title or "",
            "descripcion": it.description or "",
            "prioridad": (it.priority or "media").lower(),
            "vence": it.due_date,
            "hora": it.due_time,
            "session_id": it.session_id,
            "proyecto": proyectos.get(pid) or "General",
            "project_id": pid,
            "persona": p,
            "sin_dueno": not tiene_responsable(it.owner_name, it.owner_email),
            "orden": it.kanban_order if it.kanban_order is not None else 10_000,
            "columna_externa": it.kanban_column,
        })

    for k in CLAVES:
        # Sin posición guardada, lo más urgente arriba: primero lo que
        # tiene fecha y antes vence.
        por_columna[k].sort(
            key=lambda c: (c["orden"], c["vence"] or "9999-12-31", c["id"])
        )

    for k, g in gente.items():
        if k != SIN_DUENO:
            g["nombre"] = ident.etiqueta.get(k) or g["nombre"]
    for col in por_columna.values():
        for c in col:
            k = c["persona"]["clave"]
            if k != SIN_DUENO:
                c["persona"]["nombre"] = ident.etiqueta.get(k) or c["persona"]["nombre"]

    personas = sorted(
        gente.values(),
        key=lambda g: (g["clave"] == SIN_DUENO, (g["nombre"] or "~").lower()),
    )
    return {
        "columnas": [
            {**c, "tarjetas": por_columna[c["key"]], "total": len(por_columna[c["key"]])}
            for c in COLUMNAS
        ],
        "personas": personas,
        "total": sum(len(v) for v in por_columna.values()),
    }


class MoverIn(BaseModel):
    columna: str
    orden: Optional[int] = None


@router.patch("/{item_id}")
def mover(
    item_id: int,
    payload: MoverIn,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
):
    """Mueve una tarjeta de columna y/o de posición."""
    destino = (payload.columna or "").strip()
    if destino not in CLAVES:
        raise HTTPException(422, f"Columna desconocida: '{destino}'.")

    item = db.get(ActionItem, item_id)
    # 404 y no 403: confirmar que existe una tarea de otra empresa ya es
    # decir de más.
    if not item or item.tenant_id != tenant.id:
        raise HTTPException(404, "Tarea no encontrada.")

    origen = item.status if item.status in CLAVES else "pending"
    if destino != origen and destino not in TRANSICIONES.get(origen, set()):
        raise HTTPException(
            409,
            f"No se puede pasar de «{origen}» a «{destino}». "
            + ("Una tarea cancelada no vuelve; crea otra."
               if origen == "cancelled" else "Transición no permitida."),
        )

    item.status = destino
    item.completed_at = datetime.now().isoformat() if destino == "done" else None
    if payload.orden is not None:
        item.kanban_order = payload.orden
    item.updated_at = datetime.now().isoformat()
    db.add(item)
    db.commit()
    db.refresh(item)

    # La plataforma conectada tiene que enterarse: si no, su tablero
    # muestra el estado viejo hasta que alguien recargue.
    try:
        from services.webhook_sender import send_event_bg
        send_event_bg("task.updated", {
            "task_id": item.id, "status": item.status, "source": "acten",
        }, tenant_id=item.tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook task.updated (%s) no enviado: %s", item.id, exc)

    return {"id": item.id, "columna": item.status, "orden": item.kanban_order}


class ReordenarIn(BaseModel):
    columna: str
    ids: list[int]


@router.post("/reordenar")
def reordenar(
    payload: ReordenarIn,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
):
    """Fija el orden de una columna entera tras soltar una tarjeta.

    Se manda la columna completa y no solo la tarjeta movida: calcular
    huecos entre posiciones acaba con dos tarjetas empatadas y un orden
    que depende de cómo desempate la base.
    """
    if (payload.columna or "").strip() not in CLAVES:
        raise HTTPException(422, "Columna desconocida.")
    for pos, tid in enumerate(payload.ids):
        it = db.get(ActionItem, tid)
        if not it or it.tenant_id != tenant.id:
            continue
        it.kanban_order = pos
        db.add(it)
    db.commit()
    return {"columna": payload.columna, "tarjetas": len(payload.ids)}
