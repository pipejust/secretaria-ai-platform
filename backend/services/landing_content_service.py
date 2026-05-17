"""Landing CMS service.

Gestiona el contenido editable del landing público de acten.app. El admin
edita los textos, features, testimonios, precios, etc. desde
/admin/landing-cms; el landing público en https://acten.app/ lee este
contenido vía GET /api/public/landing (sin auth).

Almacenamiento: JSON serializado en `Tenant.landing_content_json`. Solo se
usa para el tenant 'acten' (las demás empresas usan el admin directamente).

Patrón mismo que `branding_service`: lee el JSON, hace merge sobre los
defaults para garantizar que el frontend siempre reciba todas las claves
esperadas aunque el admin no haya guardado nada todavía.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, Dict

from sqlmodel import Session

from models import Tenant


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Contenido por defecto del landing.
#
# Cualquier campo nuevo que añadas al frontend del landing debe tener su
# default aquí, así un tenant que nunca editó el CMS sigue viendo algo
# coherente. La función `get_landing_content` hace deep-merge sobre estos
# defaults, así que campos faltantes en el JSON guardado se rellenan
# automáticamente.
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONTENT: Dict[str, Any] = {
    # ─── Header / Navegación ─────────────────────────────────────────────
    "nav": {
        "items": [
            {"label": "Producto", "anchor": "#features"},
            {"label": "Cómo funciona", "anchor": "#flow"},
            {"label": "Integraciones", "anchor": "#integrations"},
            {"label": "Precios", "anchor": "#pricing"},
            {"label": "Recursos", "anchor": "#resources"},
            {"label": "Empresa", "anchor": "#company"},
            {"label": "Contacto", "anchor": "#contact"},
        ],
        "cta_label": "Solicitar demo",
        "login_label": "Iniciar sesión",
    },

    # ─── Hero ────────────────────────────────────────────────────────────
    "hero": {
        "badge": "AI MEETING ASSISTANT",
        "title_lead": "De conversación a claridad.",
        "title_highlight_prefix": "De claridad a ",
        "title_highlight_word": "impacto.",
        # Legacy combined title (kept para compat con código que aún lo lea).
        "title": "De conversación a claridad. De claridad a impacto.",
        "subtitle": (
            "Acten transforma conversaciones habladas en decisiones, tareas y "
            "entregables profesionales. Escucha, interpreta, organiza y da "
            "seguimiento para que tu equipo ejecute lo que importa."
        ),
        "cta_primary_label": "Solicitar demo",
        "cta_primary_anchor": "#contact",
        "cta_secondary_label": "Ver cómo funciona",
        "cta_secondary_anchor": "#flow",
        # 4 atributos chip bajo los CTAs del hero.
        "attributes": [
            {"label": "IA avanzada", "icon": "sparkles"},
            {"label": "Multilenguaje", "icon": "globe"},
            {"label": "Seguro y privado", "icon": "shield"},
            {"label": "Enterprise ready", "icon": "building"},
        ],
        # Mockup (decorativo, ilustrativo — no editable fino).
        "mockup_eyebrow": "Q2 Product Roadmap",
        "mockup_title": "Reunión de planificación · Q2",
        "mockup_meta": "16 may, 2024 · 10:00 a. m. · 45 min · 6 participantes",
    },

    # ─── Trust logos (clientes / empresas) ──────────────────────────────
    "trust": {
        "eyebrow": "Equipos de alto rendimiento ya confían en Acten",
        "logos": ["Microsoft", "Google", "Siemens", "BBVA", "Santander", "Deloitte"],
    },

    # ─── Capacidades (features grid) ────────────────────────────────────
    "features": {
        "eyebrow": "UN ASISTENTE, TODO EL CICLO",
        "title_lead": "Todo lo que tu equipo necesita, ",
        "title_highlight": "automáticamente.",
        "title": "Todo lo que tu equipo necesita, automáticamente.",
        "subtitle": (
            "Acten cierra el ciclo completo de tus reuniones: captura, "
            "interpreta, organiza, documenta, asigna y da seguimiento."
        ),
        "link_label": "Conocer todas las funciones",
        "link_anchor": "#flow",
        "items": [
            {
                "title": "Captura e interpreta",
                "description": "Transcripción multilenguaje con identificación de hablantes, temas y contexto.",
                "icon": "capture",
            },
            {
                "title": "Decisiones y riesgos",
                "description": "Extracción automática de decisiones, riesgos y bloqueos con responsable y severidad.",
                "icon": "decisions",
            },
            {
                "title": "Tareas y responsables",
                "description": "Acciones concretas con responsable, fecha límite y prioridad — listas para asignar.",
                "icon": "tasks",
            },
            {
                "title": "Documentos profesionales",
                "description": "Actas en Word y PDF con tu marca, tipografía y plantilla custom por proyecto.",
                "icon": "docs",
            },
            {
                "title": "Correos personalizados",
                "description": "Envío automático a cada responsable con sus tareas, contexto y fechas. Sin reenvíos manuales.",
                "icon": "email",
            },
            {
                "title": "Integraciones nativas",
                "description": "Trello, Jira, ClickUp, Azure DevOps, Slack y calendarios. Acten sincroniza sin esfuerzo.",
                "icon": "integrations",
            },
        ],
    },

    # ─── Flujo de 4 pasos ────────────────────────────────────────────────
    "flow": {
        "eyebrow": "DEL DICHO AL HECHO",
        "title_lead": "Un flujo inteligente que convierte reuniones en ",
        "title_highlight": "resultados.",
        "title": "Un flujo inteligente que convierte reuniones en resultados.",
        "subtitle": (
            "Acten procesa, estructura y distribuye la información para que "
            "nada se pierda y todo se ejecute."
        ),
        "link_label": "Ver flujo completo",
        "link_anchor": "#contact",
        "steps": [
            {
                "title": "Captura",
                "description": "Conecta Fireflies, Meet, Teams o sube grabaciones. Acten transcribe automáticamente.",
                "icon": "mic",
            },
            {
                "title": "Inteligencia",
                "description": "La IA analiza el contexto, identifica decisiones, riesgos y compromisos.",
                "icon": "brain",
            },
            {
                "title": "Estructura",
                "description": "Genera acta profesional, lista de tareas y correos personalizados por responsable.",
                "icon": "list",
            },
            {
                "title": "Acción",
                "description": "Despacha a tus plataformas: Trello, Jira, ClickUp, Azure. Seguimiento automático.",
                "icon": "send",
            },
        ],
    },

    # ─── Integraciones ──────────────────────────────────────────────────
    "integrations": {
        "eyebrow": "INTEGRACIONES QUE POTENCIAN TU EQUIPO",
        "title": "Conecta Acten con las herramientas que ya usas.",
        "subtitle": "",
        "see_all_label": "Ver todas",
        "see_all_anchor": "#integrations",
        "items": [
            "Jira", "Trello", "ClickUp", "Azure DevOps",
            "Google Calendar", "Microsoft Calendar", "Fireflies",
        ],
    },

    # ─── Testimonios ─────────────────────────────────────────────────────
    # Las personas que aparecen en esta sección son REALES del sistema —
    # se traen de /api/public/landing/people (owners de tareas + project
    # contacts) con avatar si existe. Aquí solo se administra el copy de
    # la sección y las frases rotativas que se asignan determinísticamente
    # a cada card (por índice). `items` queda como legacy para no romper
    # tenants que ya guardaron contenido, pero el frontend lo ignora.
    "testimonials": {
        "eyebrow": "EMPRESAS QUE YA TRANSFORMARON SUS REUNIONES",
        "title": "Más claridad. Más acción. Mejores resultados.",
        # Pool de frases rotativas. La card N usa quotes[N % len(quotes)].
        # Mantener mínimo 3 para que las 3 cards visibles tengan frases
        # distintas.
        "quotes": [
            "Acten transformó la forma en que mi equipo ejecuta sus decisiones.",
            "Pasamos de reuniones que terminan en olvido a tareas que sí se ejecutan.",
            "La precisión y el seguimiento automático elevaron nuestra disciplina.",
            "Las actas profesionales y la asignación de tareas son indispensables ya.",
        ],
        # Legacy — testimonios estáticos. El frontend ya no los usa porque
        # ahora muestra personas reales del sistema. Conservados solo para
        # backwards-compat con tenants que guardaron este JSON.
        "items": [],
    },

    # ─── Precios ─────────────────────────────────────────────────────────
    "pricing": {
        "eyebrow": "Precios",
        "title": "Un plan para cada tamaño de equipo.",
        "subtitle": (
            "Sin contratos largos. Todos los planes incluyen actas profesionales, "
            "tareas asignadas y correos personalizados. Escalas cuando lo necesitas."
        ),
        "plans": [
            {
                "name": "Starter",
                "price": "$49",
                "billing": "USD / mes",
                "description": "Para equipos pequeños que empiezan a estructurar sus reuniones.",
                "features": [
                    "Hasta 20 reuniones / mes",
                    "5 usuarios incluidos",
                    "Actas profesionales en Word y PDF",
                    "Tareas asignadas por responsable",
                    "Correo a cada participante",
                    "Soporte por email",
                ],
                "cta_label": "Empezar prueba",
                "cta_anchor": "#contact",
                "featured": False,
            },
            {
                "name": "Business",
                "price": "$149",
                "billing": "USD / mes",
                "description": "Para equipos en crecimiento con integraciones a sus plataformas.",
                "features": [
                    "Hasta 100 reuniones / mes",
                    "Usuarios ilimitados",
                    "Todo lo de Starter",
                    "Integraciones: Jira, Trello, ClickUp, Azure DevOps",
                    "Plantillas custom por proyecto",
                    "Pregúntale a la IA (RAG)",
                    "Soporte prioritario",
                ],
                "cta_label": "Solicitar demo",
                "cta_anchor": "#contact",
                "featured": True,
            },
            {
                "name": "Enterprise",
                "price": "A medida",
                "billing": "Contrato anual",
                "description": "Para organizaciones con seguridad, gobierno de datos y onboarding dedicado.",
                "features": [
                    "Reuniones ilimitadas",
                    "Multi-tenant white-label",
                    "SSO / SAML",
                    "Auditoría SOC 2 / GDPR",
                    "Onboarding dedicado",
                    "SLA de soporte 24/7",
                ],
                "cta_label": "Hablar con ventas",
                "cta_anchor": "#contact",
                "featured": False,
            },
        ],
        "footnote": "Todos los planes incluyen actualizaciones de IA sin costo adicional.",
    },

    # ─── Recursos ────────────────────────────────────────────────────────
    "resources": {
        "eyebrow": "Recursos",
        "title": "Aprende a sacarle todo el jugo a tus reuniones.",
        "subtitle": (
            "Guías, casos de uso y mejores prácticas para que tu equipo "
            "deje de perder decisiones."
        ),
        "items": [
            {
                "category": "Guía",
                "title": "Cómo escribir actas que sí se ejecutan",
                "description": "El framework de 6 pasos que usan los equipos más rápidos para que cada reunión termine con acción.",
                "url": "#",
                "icon": "guide",
            },
            {
                "category": "Caso de uso",
                "title": "Colpensiones: 4 horas semanales recuperadas",
                "description": "Cómo el equipo de Producto pasó de actas manuales a actas automáticas con seguimiento.",
                "url": "#",
                "icon": "case",
            },
            {
                "category": "Video",
                "title": "Demo en 3 minutos",
                "description": "Mira cómo Acten convierte una reunión real en un acta profesional con tareas asignadas.",
                "url": "#",
                "icon": "video",
            },
            {
                "category": "Blog",
                "title": "5 errores frecuentes al hacer seguimiento de tareas",
                "description": "Diagnóstico y solución a los patrones que hacen que las decisiones se pierdan entre reunión y reunión.",
                "url": "#",
                "icon": "blog",
            },
        ],
    },

    # ─── Empresa ─────────────────────────────────────────────────────────
    "company": {
        "eyebrow": "Quiénes somos",
        "title": "Construimos la capa de inteligencia que les faltaba a tus reuniones.",
        "subtitle": "",
        "story": (
            "Acten nació de una frustración compartida: las decisiones más importantes "
            "de las empresas se toman en reuniones, pero terminan dispersas en chats, "
            "documentos perdidos y memorias frágiles. Creemos que la IA puede cerrar "
            "esa brecha — no reemplazando la conversación humana, sino capturándola, "
            "estructurándola y convirtiéndola en acción concreta."
        ),
        "mission": (
            "Hacer que cada reunión empresarial termine con decisiones claras, "
            "responsables asignados y trazabilidad completa — sin trabajo manual."
        ),
        "values": [
            {
                "title": "Claridad sobre velocidad",
                "description": "Preferimos un acta clara a una rápida. La inteligencia útil requiere precisión.",
            },
            {
                "title": "Privacidad por diseño",
                "description": "Tus datos viven aislados por tenant. Nunca entrenamos modelos con tu información.",
            },
            {
                "title": "Open en lo que se puede",
                "description": "Documentamos APIs, soportamos webhooks y nos integramos con tu stack actual.",
            },
            {
                "title": "Equipo distribuido",
                "description": "Construimos desde LATAM con foco global. Nuestra zona horaria es la del cliente.",
            },
        ],
        "stats": [
            {"label": "Reuniones procesadas", "value": "12K+"},
            {"label": "Horas ahorradas / mes", "value": "1.8K"},
            {"label": "Tasa de tareas ejecutadas", "value": "94%"},
            {"label": "Idiomas soportados", "value": "11"},
        ],
    },

    # ─── Contacto / Demo ─────────────────────────────────────────────────
    "contact": {
        "eyebrow": "Hablemos",
        "title": "Agenda una demo o cuéntanos qué necesitas.",
        "subtitle": (
            "Te respondemos en menos de 24h hábiles con una demo personalizada "
            "para tu equipo."
        ),
        "email": "hola@acten.app",
        "phone": "+57 300 000 0000",
        "address": "Bogotá, Colombia · Remoto LATAM",
        "form_name_label": "Nombre",
        "form_email_label": "Correo corporativo",
        "form_company_label": "Empresa",
        "form_role_label": "Cargo (opcional)",
        "form_message_label": "¿En qué te podemos ayudar?",
        "form_cta_label": "Enviar mensaje",
        "form_success": "¡Mensaje recibido! Te contactamos en menos de 24h hábiles.",
        "form_error": "No pudimos enviar tu mensaje. Escríbenos directo a hola@acten.app.",
    },

    # ─── CTA final ───────────────────────────────────────────────────────
    "final_cta": {
        "eyebrow": "",
        "title_lead": "Convierte cada reunión en una ",
        "title_highlight": "ventaja competitiva.",
        "title": "Convierte cada reunión en una ventaja competitiva.",
        "subtitle": (
            "Solicita una demo personalizada y descubre cómo Acten puede "
            "transformar la productividad de tu equipo."
        ),
        "cta_label": "Solicitar demo",
        "cta_anchor": "#contact",
        "secondary_label": "Hablar con ventas",
        "secondary_anchor": "#contact",
        # Panel derecho con estados — visual demostrativo
        "status_items": [
            {"label": "Decisión tomada", "tone": "success"},
            {"label": "Tarea asignada", "tone": "success"},
            {"label": "Riesgo identificado", "tone": "warning"},
            {"label": "Documento generado", "tone": "success"},
        ],
    },

    # ─── Footer ──────────────────────────────────────────────────────────
    "footer": {
        "tagline": (
            "El asistente inteligente de reuniones que transforma conversaciones "
            "en claridad, decisiones y acción."
        ),
        "newsletter_title": "Suscríbete a nuestro newsletter",
        "newsletter_subtitle": (
            "Recibe novedades y mejores prácticas para equipos de alto rendimiento."
        ),
        "newsletter_placeholder": "tu@email.com",
        "newsletter_cta": "Suscribirme",
        "newsletter_success": "¡Gracias! Te avisamos cuando salga algo bueno.",
        "social_links": [
            {"label": "LinkedIn", "icon": "linkedin", "url": "https://linkedin.com/company/acten"},
            {"label": "X", "icon": "x", "url": "https://x.com/acten_app"},
            {"label": "YouTube", "icon": "youtube", "url": "https://youtube.com/@acten"},
            {"label": "GitHub", "icon": "github", "url": "https://github.com/acten"},
        ],
        # Columnas del footer — solo enlaces a secciones REALES del landing,
        # rutas existentes (/privacy, /terms) o el formulario de contacto.
        # Si añades una nueva sección o página, agrega su link aquí (o desde
        # el CMS admin). Anchors a secciones inexistentes se filtran en runtime
        # con isValidLink() del componente.
        "columns": [
            {
                "title": "Producto",
                "links": [
                    {"label": "Capacidades", "url": "#features"},
                    {"label": "Cómo funciona", "url": "#flow"},
                    {"label": "Integraciones", "url": "#integrations"},
                    {"label": "Solicitar demo", "url": "#contact"},
                ],
            },
            {
                "title": "Empresa",
                "links": [
                    {"label": "Quiénes somos", "url": "#testimonials"},
                    {"label": "Contacto", "url": "#contact"},
                    {"label": "Iniciar sesión", "url": "/login"},
                ],
            },
            {
                "title": "Legal",
                "links": [
                    {"label": "Privacidad", "url": "/privacy"},
                    {"label": "Términos", "url": "/terms"},
                ],
            },
        ],
        "legal_links": [
            {"label": "Privacidad", "url": "/privacy"},
            {"label": "Términos", "url": "/terms"},
            {"label": "Seguridad", "url": "/privacy"},
            {"label": "Cookies", "url": "/privacy"},
        ],
        "language_label": "Español (ES)",
        "copyright": "© 2024 Acten.ai. Todos los derechos reservados.",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `overrides` sobre `base` recursivamente.

    - dicts se mergean recursivamente
    - listas y valores escalares se REEMPLAZAN (no concatenan) — es lo que
      queremos para listas como features/testimonials: si el admin quita uno,
      el array nuevo es la verdad.
    """
    out = copy.deepcopy(base)
    for key, val in overrides.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _safe_load(raw: str | None) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("landing_content_json corrupto, usando defaults.")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# API pública del servicio
# ─────────────────────────────────────────────────────────────────────────────

def get_landing_content(db: Session, tenant_id: int) -> Dict[str, Any]:
    """Devuelve el contenido del landing del tenant, merged sobre defaults.

    Si el tenant no existe o no tiene contenido editado, devuelve los
    defaults — así el frontend siempre recibe una estructura completa.
    """
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        return copy.deepcopy(DEFAULT_CONTENT)
    overrides = _safe_load(tenant.landing_content_json)
    return _deep_merge(DEFAULT_CONTENT, overrides)


def update_landing_content(
    db: Session, tenant_id: int, patch: Dict[str, Any]
) -> Dict[str, Any]:
    """Persiste un patch parcial sobre el contenido actual.

    El patch sigue la misma estructura que el contenido — solo las secciones
    presentes se actualizan, las demás quedan intactas. Útil para guardar
    sección por sección desde el admin sin enviar el JSON completo.
    """
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} no existe.")

    current = _safe_load(tenant.landing_content_json)
    merged = _deep_merge(current, patch)
    tenant.landing_content_json = json.dumps(merged, ensure_ascii=False)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    return _deep_merge(DEFAULT_CONTENT, merged)


def reset_landing_content(db: Session, tenant_id: int) -> Dict[str, Any]:
    """Reset duro: borra el JSON guardado y vuelve a los defaults."""
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} no existe.")
    tenant.landing_content_json = "{}"
    db.add(tenant)
    db.commit()
    return copy.deepcopy(DEFAULT_CONTENT)
