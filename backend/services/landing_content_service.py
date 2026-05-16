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
        "badge": "Plataforma IA para reuniones empresariales",
        "title": "Convierte cada reunión en una decisión, un responsable y una acción.",
        "subtitle": (
            "Acten transcribe, interpreta y estructura tus reuniones en actas "
            "profesionales con tareas asignadas a cada responsable — listas para "
            "ejecutar en Jira, Trello, ClickUp o Azure DevOps."
        ),
        "cta_primary_label": "Solicitar demo",
        "cta_primary_anchor": "#contact",
        "cta_secondary_label": "Ver cómo funciona",
        "cta_secondary_anchor": "#flow",
        "mockup_eyebrow": "ACTA · Reunión Mar 12 · 10:30",
        "mockup_title": "Seguimiento Plataforma — Sprint 14",
        "mockup_meta": "5 decisiones · 8 tareas · 3 riesgos",
    },

    # ─── Trust logos (clientes / empresas) ──────────────────────────────
    "trust": {
        "eyebrow": "Equipos que ya organizan sus reuniones con Acten",
        "logos": ["Colpensiones", "Softnexus", "Nexura", "Acten", "BBVA", "Deloitte"],
    },

    # ─── Capacidades (features grid) ────────────────────────────────────
    "features": {
        "eyebrow": "Capacidades",
        "title": "Toda la inteligencia de tus reuniones, en un solo flujo.",
        "subtitle": (
            "Acten reemplaza el copy-paste manual entre transcriptor, acta, "
            "tareas y correos. La IA lee la reunión y entrega resultados "
            "ejecutables."
        ),
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
        "eyebrow": "Cómo funciona",
        "title": "De grabación a ejecución, en cuatro pasos.",
        "subtitle": (
            "El equipo solo aporta la reunión. Acten se encarga del resto: "
            "transcribe, analiza, estructura y reparte."
        ),
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
        "eyebrow": "Integraciones",
        "title": "Conecta Acten con las herramientas que ya usas.",
        "subtitle": (
            "No te pedimos cambiar tu stack. Acten despacha las tareas a "
            "donde tu equipo ya trabaja."
        ),
        "items": [
            "Jira", "Trello", "ClickUp", "Azure DevOps",
            "Microsoft 365", "Google Workspace", "Slack", "Fireflies",
        ],
    },

    # ─── Testimonios ─────────────────────────────────────────────────────
    "testimonials": {
        "eyebrow": "Quienes ya usan Acten",
        "title": "Equipos que ahorraron horas y ganaron trazabilidad.",
        "items": [
            {
                "quote": "Acten redujo en 4 horas semanales el trabajo de seguimiento de mi equipo. Las actas salen solas y los responsables saben qué hacer.",
                "name": "Lady Edith Ardila",
                "role": "Líder de Producto",
                "company": "Colpensiones",
                "initials": "LA",
            },
            {
                "quote": "Pasamos de tener decisiones perdidas en chats a un acta formal con trazabilidad. Lo más útil: la integración con Azure DevOps.",
                "name": "Felipe Cortés",
                "role": "CTO",
                "company": "Acten",
                "initials": "FC",
            },
            {
                "quote": "La IA capta hasta los detalles que se nos escapan. El resumen ejecutivo es exactamente lo que necesita la dirección.",
                "name": "Christian Muñoz",
                "role": "SCRUM Master",
                "company": "Softnexus",
                "initials": "CM",
            },
        ],
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
        "eyebrow": "Empieza hoy",
        "title": "Tu próxima reunión puede terminar con acciones claras.",
        "subtitle": "Solicita una demo y deja que Acten haga el seguimiento.",
        "cta_label": "Solicitar demo",
        "cta_anchor": "#contact",
        "secondary_label": "Ver precios",
        "secondary_anchor": "#pricing",
    },

    # ─── Footer ──────────────────────────────────────────────────────────
    "footer": {
        "tagline": "Plataforma IA de actas y tareas para reuniones empresariales.",
        "newsletter_title": "Recibe las novedades",
        "newsletter_subtitle": "Una vez al mes. Sin spam.",
        "newsletter_cta": "Suscribir",
        "newsletter_success": "¡Gracias! Te avisamos cuando salga algo bueno.",
        "columns": [
            {
                "title": "Producto",
                "links": [
                    {"label": "Capacidades", "url": "#features"},
                    {"label": "Cómo funciona", "url": "#flow"},
                    {"label": "Integraciones", "url": "#integrations"},
                    {"label": "Precios", "url": "#pricing"},
                ],
            },
            {
                "title": "Recursos",
                "links": [
                    {"label": "Blog", "url": "#resources"},
                    {"label": "Casos de uso", "url": "#resources"},
                    {"label": "Guías", "url": "#resources"},
                    {"label": "Documentación API", "url": "#"},
                ],
            },
            {
                "title": "Empresa",
                "links": [
                    {"label": "Quiénes somos", "url": "#company"},
                    {"label": "Misión", "url": "#company"},
                    {"label": "Carreras", "url": "#"},
                    {"label": "Contacto", "url": "#contact"},
                ],
            },
            {
                "title": "Legal",
                "links": [
                    {"label": "Términos", "url": "/terms"},
                    {"label": "Privacidad", "url": "/privacy"},
                    {"label": "Cookies", "url": "/privacy"},
                ],
            },
        ],
        "copyright": "© Acten. Todos los derechos reservados.",
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
