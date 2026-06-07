"""Pipeline común de procesamiento IA para una sesión.

Reutilizado por:
- routers/fireflies.py        → cuando llega un webhook de Fireflies
- routers/sessions_upload.py  → cuando el usuario sube audio o pega texto

División de responsabilidades:
- Groq (Llama 3.3 70B) → idioma, asistentes, temas, decisiones, riesgos, acuerdos
- Groq (Llama 3.3 70B) → deducción de proyecto por contexto (nivel 2)
- OpenAI (gpt-4o)      → action_items (tareas)

NO toca `raw_summary`. El summary lo gestiona quien llama:
- Webhook lo trae nativo de Fireflies (pre-cleaned con Groq).
- Upload manual lo deja vacío para que el admin lo escriba o lo derive.

CONTRATO DE COMPLETITUD (Sprint Estabilidad — fix 'procesos a medias'):

Una sesión sólo se considera COMPLETA si todos estos pasos terminaron OK:

  1. Groq insights (decisiones, riesgos, acuerdos, asistentes, temas).
  2. OpenAI action_items extraction.

Si alguno falla incluso después de los reintentos, la sesión queda con:
  - `processing_error` = descripción del fallo
  - `status` = 'pending' igualmente (para que el admin pueda revisar lo que sí
     se logró), pero queda marcada para reintento automático.

Los embeddings y las notificaciones son best-effort (no bloquean la
completitud, pero sí se loguean).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, TypeVar

from sqlmodel import Session, select

from models import ActionItem, MeetingSession, Project, ProjectContact
from services.groq_service import OpenAIService
from services.llm_groq import GroqLLMService

logger = logging.getLogger(__name__)

# ---- Configuración de reintentos ---------------------------------------
# 3 intentos con backoff exponencial + jitter. Total de espera en el peor
# caso: ~2 + ~4 = 6 s. Suficiente para superar timeouts puntuales o rate
# limits de OpenAI/Groq, sin alargar excesivamente el background task.
_MAX_RETRIES = 3
_BASE_BACKOFF_SEC = 2.0

T = TypeVar("T")


async def _call_with_retry(
    label: str,
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = _MAX_RETRIES,
    base_backoff: float = _BASE_BACKOFF_SEC,
) -> T:
    """Ejecuta `fn()` con reintentos exponenciales + jitter.

    Levanta la última excepción si todos los intentos fallan.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_retries + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == max_retries:
                logger.error(
                    "%s: agotados %s intentos, último error: %s",
                    label, max_retries, exc,
                )
                break
            sleep_for = base_backoff * (2 ** (attempt - 1))
            sleep_for += random.uniform(0, base_backoff / 2)
            logger.warning(
                "%s: intento %s/%s falló (%s). Reintentando en %.1fs.",
                label, attempt, max_retries, exc, sleep_for,
            )
            await asyncio.sleep(sleep_for)
    assert last_exc is not None  # mypy: garantizado por el for-else lógico
    raise last_exc


def _match_project_by_text(projects: list[Project], *texts: str) -> Optional[int]:
    haystack = "\n".join(t for t in texts if t).lower()
    for p in projects:
        if p.name and p.name.lower() in haystack:
            return p.id
    return None


def _canonical_speaker_name(name: str) -> str:
    """Normaliza un nombre para deduplicación / match cruzado.

    - Strip whitespace
    - Lowercase para comparar
    - Remueve sufijos numéricos que Fireflies a veces añade cuando el
      mismo speaker aparece en múltiples canales/sesiones ('Felipe1',
      'Tatiana Arango 2').
    - Remueve TODOS los espacios internos y puntos para tolerar las
      variantes que pone Fireflies. Caso real: transcript dice
      "[JDiego]" (sin espacio), project_contact dice "J. Diego Toro".
      Con canonical sin separadores → ambos colapsan a "jdiegotoro" /
      "jdiego" y el match opera. Sin esto, el speaker quedaba sin
      enriquecer y aparecía como "JDiego" con cargo y empresa vacíos.

    Devolvemos también solo letras unicode + dígitos (sin puntuación)
    para no romper con comas, ":", "-" etc. que a veces aparecen.
    """
    import re
    n = (name or "").strip()
    n = re.sub(r"\s*\d+\s*$", "", n)
    n = n.lower()
    # Eliminar todo lo que no sea letra unicode (incluye acentos/ñ).
    # Esto colapsa "j. diego toro", "j diego toro", "jdiegotoro" a la
    # misma clave: "jdiegotoro".
    return re.sub(r"[^\w]+", "", n, flags=re.UNICODE).replace("_", "")


def _prettify_name(name: str) -> str:
    """Normaliza presentación: quita sufijos numéricos y convierte
    UPPERCASE → Title Case sin romper acentos."""
    import re
    n = re.sub(r"\s*\d+\s*$", "", (name or "").strip()).strip()
    if not n:
        return ""
    # Si está TODO en mayúsculas (más de 3 chars letras), Title Case.
    letters = [c for c in n if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        # Title Case respetando partículas comunes en español
        parts = n.split()
        small_words = {"de", "del", "la", "las", "los", "y", "e", "o", "u", "a", "el"}
        titled = []
        for i, part in enumerate(parts):
            if i > 0 and part.lower() in small_words:
                titled.append(part.lower())
            else:
                titled.append(part.capitalize())
        n = " ".join(titled)
    return n


def _extract_speakers_from_transcript(transcript: str) -> list[str]:
    """Saca la lista única de nombres de speaker que aparecen al inicio de
    cada línea con formato `[Nombre]` — formato que pone Fireflies cuando
    arma el transcript de las sentences.

    Devuelve nombres ya prettyficados (sin sufijos numéricos, sin
    UPPERCASE estridente), deduplicados case-insensitive.

    Filtra placeholders genéricos como 'Speaker', 'Speaker 1', etc.

    Esto es la FUENTE DE VERDAD para asistentes — solo quienes hablaron
    son asistentes, no los mencionados.
    """
    import re

    raw_counts: dict[str, dict] = {}  # canonical → {best_name, count}
    for line in (transcript or "").split("\n"):
        m = re.match(r"^\[([^\]]+)\]\s*", line)
        if not m:
            continue
        spk = m.group(1).strip()
        if not spk:
            continue
        low = spk.lower()
        # Filtrar placeholders genéricos de Fireflies
        if low == "speaker" or re.match(r"^speaker\s*\d*$", low):
            continue
        canon = _canonical_speaker_name(spk)
        if not canon:
            continue
        if canon not in raw_counts:
            raw_counts[canon] = {"best_name": spk, "count": 1}
        else:
            entry = raw_counts[canon]
            entry["count"] += 1
            current = entry["best_name"]
            if _name_quality_score(spk) > _name_quality_score(current):
                entry["best_name"] = spk
    # Prettyficar y ordenar
    pretty = sorted({_prettify_name(e["best_name"]) for e in raw_counts.values()}, key=str.lower)
    return [p for p in pretty if p]


def _name_quality_score(name: str) -> int:
    """Score subjetivo para elegir la 'mejor' representación de un nombre.
    Title Case > UPPERCASE > lowercase. Más palabras > menos."""
    if not name:
        return 0
    score = 0
    # Penalizar all-caps
    if name == name.upper() and any(c.isalpha() for c in name):
        score -= 5
    # Premiar palabras múltiples (nombre+apellido)
    score += len(name.split()) * 3
    # Premiar Title Case
    if name == name.title():
        score += 2
    # Premiar longitud (preferir nombres completos sobre cortos)
    score += min(len(name), 30) // 5
    return score


def _merge_speakers_with_groq_attendees(
    real_speakers: list[str],
    groq_attendees: list[dict],
    project_contacts: list[dict],
) -> list[dict]:
    """Combina los speakers REALES (del transcript) con la metadata que Groq
    o los contacts del proyecto puedan aportar sobre role/entity.

    Regla: solo aparecen en la lista final los nombres que efectivamente
    hablaron (los que están en `real_speakers`). Para cada uno, intentamos
    enriquecer con:
        1. project_contacts (match por nombre canonical)
        2. groq_attendees (match por nombre canonical)
    Si no hay match en ninguno, se usa role="—" y entity="—".
    """
    # Index para enriquecimiento por nombre canonical
    contacts_idx = {
        _canonical_speaker_name(c.get("name", "")): c
        for c in (project_contacts or []) if c.get("name")
    }
    groq_idx = {
        _canonical_speaker_name(a.get("name", "")): a
        for a in (groq_attendees or []) if isinstance(a, dict) and a.get("name")
    }

    # Helpers de tokenización para los match levels:
    # - tokens canonicalizados ≥3 chars (filtra iniciales sueltas "J")
    # - apellido = último token de ≥3 chars
    # - nombres = todos los tokens excepto el último
    def _tokens_canonical(full_name: str) -> list[str]:
        parts = (full_name or "").strip().split()
        return [t for t in (_canonical_speaker_name(p) for p in parts) if len(t) >= 3]

    def _last_token_canonical(full_name: str) -> str:
        toks = _tokens_canonical(full_name)
        return toks[-1] if toks else ""

    def _first_tokens_canonical(full_name: str) -> list[str]:
        toks = _tokens_canonical(full_name)
        return toks[:-1] if len(toks) > 1 else []

    # Index secundario por APELLIDO — usado tanto por el match de nivel 2
    # (nombre+apellido) como por el de nivel 3 (apellido solo).
    contacts_by_lastname: dict[str, list[dict]] = {}
    for c in (project_contacts or []):
        ln = _last_token_canonical(c.get("name", ""))
        if ln and len(ln) >= 3:
            contacts_by_lastname.setdefault(ln, []).append(c)

    out: list[dict] = []
    for spk in real_speakers:
        canon = _canonical_speaker_name(spk)
        role = ""
        entity = ""
        email = ""
        # CRÍTICO: cuando el speaker matchea con un project_contact, el
        # nombre "correcto" es el del CONTACTO, no el del transcript.
        # Fireflies suele cortar acentos / pegar nombres / etc. Si el
        # admin del proyecto registró "Juan Diego Toro" como contact y
        # el transcript dice "[JDiego]", el final_attendees debe decir
        # "Juan Diego Toro" — es el nombre real con el que se le va a
        # asignar tareas y enviar correos.
        #
        # Jerarquía de match (pedido del usuario — de más preciso a
        # menos preciso, paramos en el primero que matchee):
        #   1. Nombre canonical COMPLETO (más preciso, sin ambigüedad)
        #   2. Nombre + apellido: al menos un primer-nombre Y el apellido
        #      del speaker existen en algún token canonical del contact.
        #      Ej: speaker "Juan Toro" matchea contact "Juan Diego Toro"
        #      porque "juan" ∈ {juan,diego} y "toro" == "toro".
        #   3. Apellido solo (último token), pero EXACTAMENTE 1 contact con
        #      ese apellido (si hay 2+ es ambiguo → mejor no enriquecer).
        #   4. No hay match → se deja el nombre del transcript tal cual.
        display_name = spk
        matched_contact = None

        # 1) Match exacto por nombre canonical completo.
        if canon in contacts_idx:
            matched_contact = contacts_idx[canon]
        else:
            spk_lastname = _last_token_canonical(spk)
            spk_first_tokens = set(_first_tokens_canonical(spk))

            # 2) Match por nombre + apellido — más preciso que solo
            #    apellido porque exige overlap también en algún nombre.
            #    Si hay 1 sólo contact que cumple ambas condiciones, gana.
            if spk_lastname and len(spk_lastname) >= 3 and spk_first_tokens:
                ln_candidates = contacts_by_lastname.get(spk_lastname, [])
                strict_matches = []
                for c in ln_candidates:
                    c_all_tokens = set(_tokens_canonical(c.get("name", "")))
                    if spk_first_tokens & c_all_tokens:
                        strict_matches.append(c)
                if len(strict_matches) == 1:
                    matched_contact = strict_matches[0]

            # 3) Fallback final: apellido solo, único contact.
            if matched_contact is None and spk_lastname and len(spk_lastname) >= 3:
                ln_candidates = contacts_by_lastname.get(spk_lastname, [])
                if len(ln_candidates) == 1:
                    matched_contact = ln_candidates[0]

        if matched_contact is not None:
            role = matched_contact.get("role") or ""
            entity = matched_contact.get("entity") or matched_contact.get("organization") or ""
            email = matched_contact.get("email") or ""
            contact_name = (matched_contact.get("name") or "").strip()
            if contact_name:
                display_name = contact_name
        elif canon in groq_idx:
            g = groq_idx[canon]
            role = g.get("role") or ""
            entity = g.get("entity") or ""
            # Solo usamos el nombre de Groq si parece más completo que
            # el del speaker (Groq a veces infiere apellidos del contexto).
            groq_name = (g.get("name") or "").strip()
            if groq_name and len(groq_name) > len(spk):
                display_name = groq_name
        out.append({
            "name": display_name,
            "role": role or "—",
            "entity": entity or "—",
            "email": email,
        })
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def process_session_with_ai(
    db: Session,
    session_id: int,
    *,
    auto_match_project: bool = True,
) -> None:
    """Ejecuta el pipeline completo sobre la sesión `session_id`.

    Asume que la sesión ya tiene `raw_transcript` cargado.

    Marca completitud al final:
    - Si todos los pasos críticos OK → `processing_error = ''` y
      `processing_completed_at = now`.
    - Si alguno falla incluso tras reintentos → `processing_error` describe
      qué se rompió, y la sesión queda disponible para retry manual o
      automático.
    """
    session_obj = db.get(MeetingSession, session_id)
    if not session_obj:
        logger.error("process_session_with_ai: sesión %s no existe", session_id)
        return

    transcript = (session_obj.raw_transcript or "").strip()
    if not transcript:
        logger.warning(
            "process_session_with_ai: sesión %s sin transcript, saltando IA.",
            session_id,
        )
        # Sin transcript no hay nada que reintentar — esto no es un fallo
        # del pipeline, es input vacío.
        session_obj.processing_error = "no_transcript"
        session_obj.processing_attempts = (session_obj.processing_attempts or 0) + 1
        db.add(session_obj)
        db.commit()
        return

    # Sumamos intento al inicio para que incluso si todo se cae el contador
    # refleje que se intentó.
    session_obj.processing_attempts = (session_obj.processing_attempts or 0) + 1
    db.add(session_obj)
    db.commit()

    groq = GroqLLMService()
    openai = OpenAIService()

    # ---------- 0. Idioma de output del tenant ----------
    # Toda la salida de IA (resumen, decisiones, riesgos, acuerdos, tareas)
    # se genera en el idioma que el tenant configuró como default
    # (Tenant.default_language: 'es' | 'ca' | 'en'). La transcripción
    # original puede venir en cualquier idioma — el LLM traduce.
    from models import Tenant as _Tenant
    _tenant = db.get(_Tenant, session_obj.tenant_id)
    tenant_lang = (_tenant.default_language if _tenant else "es") or "es"
    logger.info(
        "Pipeline AI sesión %s: output_language=%s (tenant_id=%s)",
        session_id, tenant_lang, session_obj.tenant_id,
    )

    # Dict de errores por paso. Si al final tiene algo, la sesión queda
    # marcada como incompleta.
    errors: dict[str, str] = {}

    # ---------- 1. Project matching nivel 1 (literal) ----------
    matched_project_id = session_obj.project_id
    projects = db.exec(select(Project)).all()
    if auto_match_project and not matched_project_id:
        matched_project_id = _match_project_by_text(
            projects, session_obj.title or "", transcript
        )

    project_contacts: list[dict] = []
    if matched_project_id:
        db_contacts = db.exec(
            select(ProjectContact).where(ProjectContact.project_id == matched_project_id)
        ).all()
        # IMPORTANTE incluir `entity` (empresa) — Groq lo usa para que cada
        # asistente quede con su organización y para que las tareas sepan
        # a qué empresa pertenece el responsable. Sin esto, el merge de
        # speakers contra contacts no podía propagar la empresa al UI.
        project_contacts = [
            {"name": c.name, "email": c.email, "role": c.role, "entity": c.entity}
            for c in db_contacts
        ]

    # ---------- 2. Groq → fundamentals + insights (CRÍTICO, con retry) ----------
    insights: dict = {}
    try:
        insights = await _call_with_retry(
            f"groq.insights[session={session_id}]",
            lambda: groq.process_fundamentals_and_insights(
                transcript, project_contacts, output_language=tenant_lang
            ),
        )
    except Exception as exc:  # noqa: BLE001
        errors["insights"] = f"Groq insights falló tras reintentos: {exc}"
        logger.exception("Groq insights agotó reintentos para sesión %s", session_id)

    # ---------- 3. Project matching nivel 2 (Groq deduce por contexto) ----------
    if auto_match_project and not matched_project_id:
        proj_dict_list = [
            {"id": p.id, "name": p.name, "description": p.description}
            for p in projects
        ]
        try:
            deduced_id = await _call_with_retry(
                f"groq.deduce_project[session={session_id}]",
                lambda: groq.deduce_project(
                    session_obj.raw_summary or transcript[:6000], proj_dict_list
                ),
                max_retries=2,  # menos crítico
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Groq deduce_project falló para sesión %s (no bloquea): %s",
                session_id, exc,
            )
            deduced_id = None
        if deduced_id:
            matched_project_id = deduced_id
            db_contacts = db.exec(
                select(ProjectContact).where(
                    ProjectContact.project_id == matched_project_id
                )
            ).all()
            project_contacts = [
                {"name": c.name, "email": c.email, "role": c.role, "entity": c.entity}
                for c in db_contacts
            ]

    # ---------- 4. Persistir insights (incluso parciales) ----------
    session_obj.project_id = matched_project_id
    if insights:
        session_obj.language = (
            insights.get("language") or session_obj.language or "Español"
        )
        session_obj.processed_decisions = insights.get("decisions", "") or ""
        session_obj.processed_risks = insights.get("risks", "") or ""
        session_obj.processed_agreements = insights.get("agreements", "") or ""
        session_obj.processed_themes = json.dumps(
            insights.get("themes", []) or [], ensure_ascii=False
        )

    # ASISTENTES: fuente de verdad = speakers que efectivamente hablaron
    # en el transcript (marcadores `[Nombre]` que pone Fireflies). Antes
    # confiábamos en lo que Groq inferíera del texto, pero terminaba
    # incluyendo a gente que solo era MENCIONADA en la conversación
    # (invitados que no entraron, personas referidas, etc.).
    # Fallback: si el transcript NO tiene marcadores `[Name]` (caso de
    # uploads manuales o pegados como texto plano), caemos a la lista
    # que produjo Groq — es lo mejor que tenemos.
    real_speakers = _extract_speakers_from_transcript(transcript)
    groq_atts = insights.get("attendees", []) if insights else []
    if real_speakers:
        final_attendees = _merge_speakers_with_groq_attendees(
            real_speakers, groq_atts, project_contacts,
        )
    else:
        logger.info(
            "Sesión %s: transcript sin marcadores [Nombre], usando attendees de Groq como fallback.",
            session_id,
        )
        # Limpieza: prettificar nombres, deduplicar, filtrar placeholders.
        import re as _re
        seen_canon = set()
        final_attendees = []
        for g in groq_atts:
            if not isinstance(g, dict):
                continue
            raw_name = (g.get("name") or "").strip()
            if not raw_name:
                continue
            low = raw_name.lower()
            # Filtrar placeholders genéricos como 'Speaker', 'Speaker 1', etc.
            if low == "speaker" or _re.match(r"^speaker\s*\d*$", low):
                continue
            canon = _canonical_speaker_name(raw_name)
            if canon in seen_canon:
                continue
            seen_canon.add(canon)
            final_attendees.append({
                "name": _prettify_name(raw_name),
                "role": g.get("role") or "—",
                "entity": g.get("entity") or "—",
                "email": g.get("email") or "",
            })
    session_obj.processed_attendees = json.dumps(final_attendees, ensure_ascii=False)
    db.add(session_obj)
    db.commit()

    # ---------- 5. OpenAI → action_items (CRÍTICO, con retry) ----------
    # Pasamos también las secciones ya procesadas (decisions, agreements,
    # summary) para que la extracción de tareas tenga TODA la información
    # estructurada a su disposición. Esto evita que se pierdan
    # compromisos que estaban claros en los acuerdos pero diluidos en el
    # transcript ruidoso.
    tasks_payload: dict = {"action_items": []}
    try:
        tasks_payload = await _call_with_retry(
            f"openai.tasks[session={session_id}]",
            lambda: openai.process_transcript_for_tasks_only(
                transcript,
                project_contacts,
                decisions=session_obj.processed_decisions or "",
                agreements=session_obj.processed_agreements or "",
                summary=session_obj.raw_summary or "",
                output_language=tenant_lang,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        errors["tasks"] = f"OpenAI tasks falló tras reintentos: {exc}"
        logger.exception(
            "OpenAI tasks agotó reintentos para sesión %s", session_id,
        )

    # IDEMPOTENCIA EN REINTENTOS: si el pipeline ya había corrido antes y
    # creado tareas (parciales), las borramos para que el re-run sea la
    # fuente de verdad. Solo borramos las que NO han sido aprobadas/editadas
    # por un humano (`is_approved == False`), para no perder trabajo manual
    # del admin sobre las tareas que sí estaban bien.
    if (session_obj.processing_attempts or 0) > 1 and "tasks" not in errors:
        prev_items = db.exec(
            select(ActionItem)
            .where(ActionItem.session_id == session_id)
            .where(ActionItem.tenant_id == session_obj.tenant_id)
            .where(ActionItem.is_approved == False)  # noqa: E712
        ).all()
        for prev in prev_items:
            db.delete(prev)
        if prev_items:
            db.commit()
            logger.info(
                "Sesión %s: borradas %s tareas previas no aprobadas antes de re-IA.",
                session_id, len(prev_items),
            )

    # Index para post-match de owner_email cuando el LLM no lo encuentra.
    # Construimos una lookup por nombre canonical -> email a partir de:
    #   1) project_contacts (fuente primaria — el admin las definió)
    #   2) processed_attendees recién mergeados (speakers que hablaron)
    # Así, si el LLM dice owner_name="Camila" y deja owner_email="", lo
    # rellenamos automáticamente con el email de Camila del proyecto.
    _email_by_canon: dict[str, str] = {}
    for _c in (project_contacts or []):
        _nm = _canonical_speaker_name(_c.get("name") or "")
        _em = (_c.get("email") or "").strip().lower()
        if _nm and _em:
            _email_by_canon[_nm] = _em
    for _att in (final_attendees or []):
        _nm = _canonical_speaker_name(_att.get("name") or "")
        _em = (_att.get("email") or "").strip().lower()
        if _nm and _em and _nm not in _email_by_canon:
            _email_by_canon[_nm] = _em

    def _resolve_owner_email(name: str, current_email: str) -> str:
        """Devuelve el email del owner: respeta el que vino del LLM si
        parece válido (tiene '@'), si no, lookup por nombre. Conserva ''
        si el nombre es 'Unknown' o 'Por asignar'."""
        if current_email and "@" in current_email:
            return current_email.strip().lower()
        if not name:
            return ""
        # Skip placeholders del LLM cuando no hay responsable claro.
        nl = name.strip().lower()
        if nl in ("unknown", "por asignar", "sin asignar", "no asignado", "n/a", "-"):
            return ""
        canon = _canonical_speaker_name(name)
        return _email_by_canon.get(canon, "")

    created_tasks = 0
    for item_data in tasks_payload.get("action_items", []) or []:
        if isinstance(item_data, str):
            title_v = "Tarea Detectada"
            description = item_data.strip()
            owner_name = "Unknown"
            owner_email = ""
            due_date = None
            due_time = None
            priority = "media"
        elif isinstance(item_data, dict):
            title_v = str(item_data.get("title") or "").strip()
            description = str(item_data.get("description") or "").strip()
            owner_name = str(item_data.get("owner_name") or "Unknown")
            owner_email = str(item_data.get("owner_email") or "")
            due_date = item_data.get("due_date") or None
            due_time = (item_data.get("due_time") or "").strip() or None
            priority = (item_data.get("priority") or "media").lower().strip()
            if priority not in ("alta", "media", "baja"):
                priority = "media"
        else:
            continue

        if not title_v and not description:
            continue

        # Post-match: si el LLM dejó owner_email vacío, intentamos llenarlo
        # con el email del contacto del proyecto o del attendee que matchee
        # por nombre canonical. Soluciona el bug reportado donde tareas
        # asignadas a "Camila" llegaban sin correo aunque Camila estuviera
        # en project_contacts.
        owner_email = _resolve_owner_email(owner_name, owner_email)

        db.add(
            ActionItem(
                tenant_id=session_obj.tenant_id,
                session_id=session_id,
                owner_name=owner_name,
                owner_email=owner_email,
                title=title_v or "Tarea sin título",
                description=description,
                due_date=due_date,
                due_time=due_time,
                priority=priority,
                is_approved=False,
            )
        )
        created_tasks += 1
    db.commit()

    # ---------- 5b. Notificar al owner si tiene cuenta en el workspace ----
    # Releemos las tareas recién creadas y disparamos una notif por cada
    # owner_email que matchee con un User. Si el owner es externo (no
    # tiene cuenta), simplemente se ignora — no rompe el pipeline.
    #
    # GATE de seguridad: si el pipeline tuvo errores, NO notificamos a
    # los owners — porque la lista de tareas puede estar incompleta o
    # mezclada (ej. Groq falló asignando responsables). El admin tiene
    # que reintentar primero. Cuando el pipeline termine sin errores,
    # las notificaciones salen normalmente.
    if errors:
        logger.warning(
            "Sesión %s incompleta — saltando notificaciones a owners de tareas.",
            session_id,
        )
    else:
        try:
            from services.notification_service import (
                notify_owner_email,
                KIND_TASK_ASSIGNED,
            )
            new_items = db.exec(
                select(ActionItem)
                .where(ActionItem.session_id == session_id)
                .where(ActionItem.tenant_id == session_obj.tenant_id)
            ).all()
            for it in new_items:
                notify_owner_email(
                    db,
                    tenant_id=session_obj.tenant_id,
                    owner_email=it.owner_email,
                    kind=KIND_TASK_ASSIGNED,
                    title=f"Te asignaron una tarea: {it.title[:120]}",
                    body=(it.description or "")[:300],
                    link_to=f"/admin/curation/{session_id}",
                    entity_type="action_item",
                    entity_id=it.id,
                )
        except Exception:
            logger.exception(
                "No se pudieron emitir notificaciones de tareas para sesión %s",
                session_id,
            )

    # ---------- 6. Embeddings (Sprint 00 — RAG foundation, NO crítico) ----
    try:
        from services.embedding_service import embed_session
        chunks = await embed_session(db, session_id)
        logger.info(
            "Sesión %s: %s chunks de embeddings indexados.", session_id, chunks
        )
    except Exception:
        logger.exception(
            "embed_session falló para sesión %s (no bloquea curación).", session_id
        )

    # ---------- 7. Marcar completitud + estado final ----------
    db.refresh(session_obj)

    if errors:
        # Pipeline incompleto. Persistimos el detalle para que el admin lo vea
        # y el cron de retry lo rescate.
        session_obj.processing_error = "; ".join(
            f"{step}: {msg}" for step, msg in errors.items()
        )[:2000]
        # NO seteamos processing_completed_at — sigue vacío, lo cual marca
        # la sesión como "no terminada".
    else:
        session_obj.processing_error = ""
        session_obj.processing_completed_at = _now_iso()

    if session_obj.status == "processing":
        session_obj.status = "pending"
    db.add(session_obj)
    db.commit()

    # ---------- 8. Notificar a los admins ---------------------------------
    try:
        from services.notification_service import (
            notify_admins,
            KIND_SESSION_PROCESSED,
        )
        if errors:
            notify_admins(
                db,
                tenant_id=session_obj.tenant_id,
                kind=KIND_SESSION_PROCESSED,
                title=f"Sesión incompleta: {session_obj.title[:120]}",
                body=(
                    "El pipeline IA terminó con errores. "
                    f"Pasos fallidos: {', '.join(errors.keys())}. "
                    "Reintentar desde la sesión."
                ),
                link_to=f"/admin/curation/{session_id}",
                entity_type="session",
                entity_id=session_id,
            )
        else:
            notify_admins(
                db,
                tenant_id=session_obj.tenant_id,
                kind=KIND_SESSION_PROCESSED,
                title=f"Sesión analizada: {session_obj.title[:120]}",
                body=(
                    f"La IA terminó de procesar la sesión. "
                    f"{len(insights.get('attendees', []) or [])} asistentes, "
                    f"{created_tasks} tareas detectadas."
                ),
                link_to=f"/admin/curation/{session_id}",
                entity_type="session",
                entity_id=session_id,
            )
    except Exception:
        logger.exception(
            "No se pudo emitir notif de session_processed para %s", session_id,
        )

    logger.info(
        "Pipeline IA terminado para sesión %s (proyecto=%s, tareas=%s, errores=%s).",
        session_id,
        matched_project_id,
        created_tasks,
        list(errors.keys()) or "ninguno",
    )
