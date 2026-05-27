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
        # One-page landing: todos los items son scroll-anchors a secciones
        # que existen dentro de landing.component.html (#features, #flow,
        # #integrations, #pricing, #trust, #contact).
        # "Empresa" apunta a #trust ("Equipos de alto rendimiento que ya
        # confían en Acten") porque es lo más cercano a una sección de
        # casos de éxito en la home.
        "items": [
            {"label": {"es": "Producto", "ca": "Producte", "en": "Product"}, "anchor": "#features"},
            {"label": {"es": "Cómo funciona", "ca": "Com funciona", "en": "How it works"}, "anchor": "#flow"},
            {"label": {"es": "Integraciones", "ca": "Integracions", "en": "Integrations"}, "anchor": "#integrations"},
            {"label": {"es": "Precios", "ca": "Preus", "en": "Pricing"}, "anchor": "#pricing"},
            {"label": {"es": "Empresa", "ca": "Empresa", "en": "Company"}, "anchor": "#trust"},
            {"label": {"es": "Contacto", "ca": "Contacte", "en": "Contact"}, "anchor": "#contact"},
        ],
        "cta_label": {"es": "Solicitar demo", "ca": "Demana demo", "en": "Request a demo"},
        "login_label": {"es": "Iniciar sesión", "ca": "Inicia sessió", "en": "Log in"},
    },

    # ─── Hero ────────────────────────────────────────────────────────────
    "hero": {
        "badge": {
            "es": "AI MEETING ASSISTANT",
            "ca": "AI MEETING ASSISTANT",
            "en": "AI MEETING ASSISTANT",
        },
        "title_lead": {
            "es": "De conversación a claridad.",
            "ca": "De conversa a claredat.",
            "en": "From conversation to clarity.",
        },
        "title_highlight_prefix": {
            "es": "De claridad a ",
            "ca": "De claredat a ",
            "en": "From clarity to ",
        },
        "title_highlight_word": {
            "es": "impacto.",
            "ca": "impacte.",
            "en": "impact.",
        },
        # Legacy combined title (kept para compat con código que aún lo lea).
        "title": {
            "es": "De conversación a claridad. De claridad a impacto.",
            "ca": "De conversa a claredat. De claredat a impacte.",
            "en": "From conversation to clarity. From clarity to impact.",
        },
        "subtitle": {
            "es": "Acten transforma conversaciones habladas en decisiones, tareas y entregables profesionales. Escucha, interpreta, organiza y da seguimiento para que tu equipo ejecute lo que importa.",
            "ca": "Acten transforma converses parlades en decisions, tasques i lliuraments professionals. Escolta, interpreta, organitza i fa seguiment perquè el teu equip executi el que importa.",
            "en": "Acten turns spoken conversations into decisions, tasks and professional deliverables. It listens, interprets, organizes and follows up so your team executes what matters.",
        },
        "cta_primary_label": {
            "es": "Solicitar demo",
            "ca": "Demana demo",
            "en": "Request a demo",
        },
        "cta_primary_anchor": "#contact",
        "cta_secondary_label": {
            "es": "Ver cómo funciona",
            "ca": "Veure com funciona",
            "en": "See how it works",
        },
        "cta_secondary_anchor": "#flow",
        # 4 atributos chip bajo los CTAs del hero.
        "attributes": [
            {"label": {"es": "IA avanzada", "ca": "IA avançada", "en": "Advanced AI"}, "icon": "sparkles"},
            {"label": {"es": "Multilenguaje", "ca": "Multilingüe", "en": "Multilingual"}, "icon": "globe"},
            {"label": {"es": "Seguro y privado", "ca": "Segur i privat", "en": "Secure & private"}, "icon": "shield"},
            {"label": {"es": "Enterprise ready", "ca": "Enterprise ready", "en": "Enterprise ready"}, "icon": "building"},
        ],
        # Mockup (decorativo, ilustrativo — no editable fino).
        "mockup_eyebrow": "Q2 Product Roadmap",
        "mockup_title": {
            "es": "Reunión de planificación · Q2",
            "ca": "Reunió de planificació · Q2",
            "en": "Planning meeting · Q2",
        },
        "mockup_meta": "16 may, 2024 · 10:00 a. m. · 45 min · 6 participantes",
    },

    # ─── Trust logos (clientes / empresas) ──────────────────────────────
    "trust": {
        "eyebrow": {
            "es": "Equipos de alto rendimiento ya confían en Acten",
            "ca": "Equips d'alt rendiment ja confien en Acten",
            "en": "High-performance teams already trust Acten",
        },
        "logos": ["Microsoft", "Google", "Siemens", "BBVA", "Santander", "Deloitte"],
    },

    # ─── Capacidades (features grid) ────────────────────────────────────
    "features": {
        "eyebrow": {
            "es": "UN ASISTENTE, TODO EL CICLO",
            "ca": "UN ASSISTENT, TOT EL CICLE",
            "en": "ONE ASSISTANT, FULL CYCLE",
        },
        "title_lead": {
            "es": "Todo lo que tu equipo necesita, ",
            "ca": "Tot el que el teu equip necessita, ",
            "en": "Everything your team needs, ",
        },
        "title_highlight": {
            "es": "automáticamente.",
            "ca": "automàticament.",
            "en": "automatically.",
        },
        "title": {
            "es": "Todo lo que tu equipo necesita, automáticamente.",
            "ca": "Tot el que el teu equip necessita, automàticament.",
            "en": "Everything your team needs, automatically.",
        },
        "subtitle": {
            "es": "Acten cierra el ciclo completo de tus reuniones: captura, interpreta, organiza, documenta, asigna y da seguimiento.",
            "ca": "Acten tanca el cicle complet de les teves reunions: captura, interpreta, organitza, documenta, assigna i fa seguiment.",
            "en": "Acten closes the full meeting cycle: capture, interpret, organize, document, assign and follow up.",
        },
        "link_label": {
            "es": "Conocer todas las funciones",
            "ca": "Conèixer totes les funcions",
            "en": "Explore all features",
        },
        "link_anchor": "#flow",
        "items": [
            {
                "title": {"es": "Captura e interpreta", "ca": "Captura i interpreta", "en": "Capture and interpret"},
                "description": {
                    "es": "Transcripción multilenguaje con identificación de hablantes, temas y contexto.",
                    "ca": "Transcripció multilingüe amb identificació de parlants, temes i context.",
                    "en": "Multilingual transcription with speaker, topic and context identification.",
                },
                "icon": "capture",
            },
            {
                "title": {"es": "Decisiones y riesgos", "ca": "Decisions i riscos", "en": "Decisions and risks"},
                "description": {
                    "es": "Extracción automática de decisiones, riesgos y bloqueos con responsable y severidad.",
                    "ca": "Extracció automàtica de decisions, riscos i bloquejos amb responsable i severitat.",
                    "en": "Automatic extraction of decisions, risks and blockers with owner and severity.",
                },
                "icon": "decisions",
            },
            {
                "title": {"es": "Tareas y responsables", "ca": "Tasques i responsables", "en": "Tasks and owners"},
                "description": {
                    "es": "Acciones concretas con responsable, fecha límite y prioridad — listas para asignar.",
                    "ca": "Accions concretes amb responsable, data límit i prioritat — llestes per assignar.",
                    "en": "Concrete actions with owner, due date and priority — ready to assign.",
                },
                "icon": "tasks",
            },
            {
                "title": {"es": "Documentos profesionales", "ca": "Documents professionals", "en": "Professional documents"},
                "description": {
                    "es": "Actas en Word y PDF con tu marca, tipografía y plantilla custom por proyecto.",
                    "ca": "Actes en Word i PDF amb la teva marca, tipografia i plantilla personalitzada per projecte.",
                    "en": "Minutes in Word and PDF with your brand, typography and custom template per project.",
                },
                "icon": "docs",
            },
            {
                "title": {"es": "Correos personalizados", "ca": "Correus personalitzats", "en": "Personalized emails"},
                "description": {
                    "es": "Envío automático a cada responsable con sus tareas, contexto y fechas. Sin reenvíos manuales.",
                    "ca": "Enviament automàtic a cada responsable amb les seves tasques, context i dates. Sense reenviaments manuals.",
                    "en": "Auto-sent to every owner with their tasks, context and dates. No manual forwards.",
                },
                "icon": "email",
            },
            {
                "title": {"es": "Integraciones nativas", "ca": "Integracions natives", "en": "Native integrations"},
                "description": {
                    "es": "Trello, Jira, ClickUp, Azure DevOps, Slack y calendarios. Acten sincroniza sin esfuerzo.",
                    "ca": "Trello, Jira, ClickUp, Azure DevOps, Slack i calendaris. Acten sincronitza sense esforç.",
                    "en": "Trello, Jira, ClickUp, Azure DevOps, Slack and calendars. Acten syncs effortlessly.",
                },
                "icon": "integrations",
            },
        ],
    },

    # ─── Flujo de 4 pasos ────────────────────────────────────────────────
    "flow": {
        "eyebrow": {
            "es": "DEL DICHO AL HECHO",
            "ca": "DEL DIT AL FET",
            "en": "FROM TALK TO ACTION",
        },
        "title_lead": {
            "es": "Un flujo inteligente que convierte reuniones en ",
            "ca": "Un flux intel·ligent que converteix reunions en ",
            "en": "An intelligent flow that turns meetings into ",
        },
        "title_highlight": {
            "es": "resultados.",
            "ca": "resultats.",
            "en": "results.",
        },
        "title": {
            "es": "Un flujo inteligente que convierte reuniones en resultados.",
            "ca": "Un flux intel·ligent que converteix reunions en resultats.",
            "en": "An intelligent flow that turns meetings into results.",
        },
        "subtitle": {
            "es": "Acten procesa, estructura y distribuye la información para que nada se pierda y todo se ejecute.",
            "ca": "Acten processa, estructura i distribueix la informació perquè no es perdi res i tot s'executi.",
            "en": "Acten processes, structures and distributes information so nothing gets lost and everything ships.",
        },
        "link_label": {
            "es": "Ver flujo completo",
            "ca": "Veure flux complet",
            "en": "See full flow",
        },
        "link_anchor": "#contact",
        "steps": [
            {
                "title": {"es": "Captura", "ca": "Captura", "en": "Capture"},
                "description": {
                    "es": "Conecta Fireflies, Meet, Teams o sube grabaciones. Acten transcribe automáticamente.",
                    "ca": "Connecta Fireflies, Meet, Teams o puja gravacions. Acten transcriu automàticament.",
                    "en": "Connect Fireflies, Meet, Teams or upload recordings. Acten transcribes automatically.",
                },
                "icon": "mic",
            },
            {
                "title": {"es": "Inteligencia", "ca": "Intel·ligència", "en": "Intelligence"},
                "description": {
                    "es": "La IA analiza el contexto, identifica decisiones, riesgos y compromisos.",
                    "ca": "La IA analitza el context, identifica decisions, riscos i compromisos.",
                    "en": "The AI analyzes context and identifies decisions, risks and commitments.",
                },
                "icon": "brain",
            },
            {
                "title": {"es": "Estructura", "ca": "Estructura", "en": "Structure"},
                "description": {
                    "es": "Genera acta profesional, lista de tareas y correos personalizados por responsable.",
                    "ca": "Genera acta professional, llista de tasques i correus personalitzats per responsable.",
                    "en": "Generates a professional minute, a task list and personalized emails per owner.",
                },
                "icon": "list",
            },
            {
                "title": {"es": "Acción", "ca": "Acció", "en": "Action"},
                "description": {
                    "es": "Despacha a tus plataformas: Trello, Jira, ClickUp, Azure. Seguimiento automático.",
                    "ca": "Despatxa a les teves plataformes: Trello, Jira, ClickUp, Azure. Seguiment automàtic.",
                    "en": "Dispatches to your platforms: Trello, Jira, ClickUp, Azure. Automatic follow-up.",
                },
                "icon": "send",
            },
        ],
    },

    # ─── Integraciones ──────────────────────────────────────────────────
    "integrations": {
        "eyebrow": {
            "es": "INTEGRACIONES QUE POTENCIAN TU EQUIPO",
            "ca": "INTEGRACIONS QUE POTENCIEN EL TEU EQUIP",
            "en": "INTEGRATIONS THAT EMPOWER YOUR TEAM",
        },
        "title": {
            "es": "Conecta Acten con las herramientas que ya usas.",
            "ca": "Connecta Acten amb les eines que ja utilitzes.",
            "en": "Connect Acten to the tools you already use.",
        },
        "subtitle": {"es": "", "ca": "", "en": ""},
        "see_all_label": {"es": "Ver todas", "ca": "Veure totes", "en": "See all"},
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
        "eyebrow": {
            "es": "EMPRESAS QUE YA TRANSFORMARON SUS REUNIONES",
            "ca": "EMPRESES QUE JA HAN TRANSFORMAT LES SEVES REUNIONS",
            "en": "COMPANIES THAT HAVE ALREADY TRANSFORMED THEIR MEETINGS",
        },
        "title": {
            "es": "Más claridad. Más acción. Mejores resultados.",
            "ca": "Més claredat. Més acció. Millors resultats.",
            "en": "More clarity. More action. Better results.",
        },
        # Pool de frases rotativas. La card N usa quotes[N % len(quotes)].
        # Mantener mínimo 3 para que las 3 cards visibles tengan frases
        # distintas.
        "quotes": [
            {
                "es": "Acten transformó la forma en que mi equipo ejecuta sus decisiones.",
                "ca": "Acten ha transformat la manera com el meu equip executa les seves decisions.",
                "en": "Acten transformed how my team executes their decisions.",
            },
            {
                "es": "Pasamos de reuniones que terminan en olvido a tareas que sí se ejecutan.",
                "ca": "Hem passat de reunions que acaben en l'oblit a tasques que sí s'executen.",
                "en": "We went from forgettable meetings to tasks that actually get done.",
            },
            {
                "es": "La precisión y el seguimiento automático elevaron nuestra disciplina.",
                "ca": "La precisió i el seguiment automàtic han elevat la nostra disciplina.",
                "en": "Precision and automatic follow-up raised our discipline.",
            },
            {
                "es": "Las actas profesionales y la asignación de tareas son indispensables ya.",
                "ca": "Les actes professionals i l'assignació de tasques ja són indispensables.",
                "en": "The professional minutes and task assignment are now indispensable.",
            },
        ],
        # Legacy — testimonios estáticos. El frontend ya no los usa porque
        # ahora muestra personas reales del sistema. Conservados solo para
        # backwards-compat con tenants que guardaron este JSON.
        "items": [],
    },

    # ─── Precios ─────────────────────────────────────────────────────────
    "pricing": {
        "eyebrow": {"es": "Precios", "ca": "Preus", "en": "Pricing"},
        "title": {
            "es": "Un plan para cada tamaño de equipo.",
            "ca": "Un pla per a cada mida d'equip.",
            "en": "A plan for every team size.",
        },
        "subtitle": {
            "es": "Sin contratos largos. Todos los planes incluyen actas profesionales, tareas asignadas y correos personalizados. Escalas cuando lo necesitas.",
            "ca": "Sense contractes llargs. Tots els plans inclouen actes professionals, tasques assignades i correus personalitzats. Escales quan ho necessitis.",
            "en": "No long contracts. Every plan includes professional minutes, assigned tasks and personalized emails. Scale when you need to.",
        },
        "plans": [
            {
                "name": "Starter",
                "price": "$49",
                "billing": {"es": "USD / mes", "ca": "USD / mes", "en": "USD / month"},
                "description": {
                    "es": "Para equipos pequeños que empiezan a estructurar sus reuniones.",
                    "ca": "Per a equips petits que comencen a estructurar les seves reunions.",
                    "en": "For small teams starting to structure their meetings.",
                },
                "features": [
                    {"es": "Hasta 20 reuniones / mes", "ca": "Fins a 20 reunions / mes", "en": "Up to 20 meetings / month"},
                    {"es": "5 usuarios incluidos", "ca": "5 usuaris inclosos", "en": "5 users included"},
                    {"es": "Actas profesionales en Word y PDF", "ca": "Actes professionals en Word i PDF", "en": "Professional minutes in Word and PDF"},
                    {"es": "Tareas asignadas por responsable", "ca": "Tasques assignades per responsable", "en": "Tasks assigned per owner"},
                    {"es": "Correo a cada participante", "ca": "Correu a cada participant", "en": "Email to every participant"},
                    {"es": "Soporte por email", "ca": "Suport per correu", "en": "Email support"},
                ],
                "cta_label": {"es": "Solicitar demo", "ca": "Demana demo", "en": "Request a demo"},
                "cta_anchor": "#contact",
                "featured": False,
            },
            {
                "name": "Business",
                "price": "$149",
                "billing": {"es": "USD / mes", "ca": "USD / mes", "en": "USD / month"},
                "description": {
                    "es": "Para equipos en crecimiento con integraciones a sus plataformas.",
                    "ca": "Per a equips en creixement amb integracions a les seves plataformes.",
                    "en": "For growing teams with integrations to their platforms.",
                },
                "features": [
                    {"es": "Hasta 100 reuniones / mes", "ca": "Fins a 100 reunions / mes", "en": "Up to 100 meetings / month"},
                    {"es": "Usuarios ilimitados", "ca": "Usuaris il·limitats", "en": "Unlimited users"},
                    {"es": "Todo lo de Starter", "ca": "Tot el de Starter", "en": "Everything in Starter"},
                    {"es": "Integraciones: Jira, Trello, ClickUp, Azure DevOps", "ca": "Integracions: Jira, Trello, ClickUp, Azure DevOps", "en": "Integrations: Jira, Trello, ClickUp, Azure DevOps"},
                    {"es": "Plantillas custom por proyecto", "ca": "Plantilles personalitzades per projecte", "en": "Custom templates per project"},
                    {"es": "Pregúntale a la IA (RAG)", "ca": "Pregunta a la IA (RAG)", "en": "Ask the AI (RAG)"},
                    {"es": "Soporte prioritario", "ca": "Suport prioritari", "en": "Priority support"},
                ],
                "cta_label": {"es": "Solicitar demo", "ca": "Demana demo", "en": "Request a demo"},
                "cta_anchor": "#contact",
                "featured": True,
            },
            {
                "name": "Enterprise",
                "price": {"es": "A medida", "ca": "A mida", "en": "Custom"},
                "billing": {"es": "Contrato anual", "ca": "Contracte anual", "en": "Annual contract"},
                "description": {
                    "es": "Para organizaciones con seguridad, gobierno de datos y onboarding dedicado.",
                    "ca": "Per a organitzacions amb seguretat, govern de dades i onboarding dedicat.",
                    "en": "For organizations with security, data governance and dedicated onboarding.",
                },
                "features": [
                    {"es": "Reuniones ilimitadas", "ca": "Reunions il·limitades", "en": "Unlimited meetings"},
                    {"es": "Multi-tenant white-label", "ca": "Multi-tenant white-label", "en": "Multi-tenant white-label"},
                    {"es": "SSO / SAML", "ca": "SSO / SAML", "en": "SSO / SAML"},
                    {"es": "Auditoría SOC 2 / GDPR", "ca": "Auditoria SOC 2 / RGPD", "en": "SOC 2 / GDPR audit"},
                    {"es": "Onboarding dedicado", "ca": "Onboarding dedicat", "en": "Dedicated onboarding"},
                    {"es": "SLA de soporte 24/7", "ca": "SLA de suport 24/7", "en": "24/7 support SLA"},
                ],
                "cta_label": {"es": "Solicitar demo", "ca": "Demana demo", "en": "Request a demo"},
                "cta_anchor": "#contact",
                "featured": False,
            },
        ],
        "footnote": {
            "es": "Todos los planes incluyen actualizaciones de IA sin costo adicional.",
            "ca": "Tots els plans inclouen actualitzacions d'IA sense cost addicional.",
            "en": "Every plan includes AI updates at no additional cost.",
        },
    },

    # ─── Recursos ────────────────────────────────────────────────────────
    "resources": {
        "eyebrow": {"es": "Recursos", "ca": "Recursos", "en": "Resources"},
        "title": {
            "es": "Aprende a sacarle todo el jugo a tus reuniones.",
            "ca": "Aprèn a treure tot el suc a les teves reunions.",
            "en": "Learn how to get the most out of your meetings.",
        },
        "subtitle": {
            "es": "Guías, casos de uso y mejores prácticas para que tu equipo deje de perder decisiones.",
            "ca": "Guies, casos d'ús i bones pràctiques perquè el teu equip deixi de perdre decisions.",
            "en": "Guides, use cases and best practices so your team stops losing decisions.",
        },
        "items": [
            {
                "category": {"es": "Guía", "ca": "Guia", "en": "Guide"},
                "title": {
                    "es": "Cómo escribir actas que sí se ejecutan",
                    "ca": "Com escriure actes que sí s'executen",
                    "en": "How to write minutes that actually get executed",
                },
                "description": {
                    "es": "El framework de 6 pasos que usan los equipos más rápidos para que cada reunión termine con acción.",
                    "ca": "El framework de 6 passos que utilitzen els equips més ràpids perquè cada reunió acabi amb acció.",
                    "en": "The 6-step framework used by the fastest teams to end every meeting with action.",
                },
                "url": "#",
                "icon": "guide",
            },
            {
                "category": {"es": "Caso de uso", "ca": "Cas d'ús", "en": "Use case"},
                "title": {
                    "es": "Colpensiones: 4 horas semanales recuperadas",
                    "ca": "Colpensiones: 4 hores setmanals recuperades",
                    "en": "Colpensiones: 4 hours per week recovered",
                },
                "description": {
                    "es": "Cómo el equipo de Producto pasó de actas manuales a actas automáticas con seguimiento.",
                    "ca": "Com l'equip de Producte va passar d'actes manuals a actes automàtiques amb seguiment.",
                    "en": "How the Product team moved from manual minutes to automated minutes with follow-up.",
                },
                "url": "#",
                "icon": "case",
            },
            {
                "category": {"es": "Video", "ca": "Vídeo", "en": "Video"},
                "title": {
                    "es": "Demo en 3 minutos",
                    "ca": "Demo en 3 minuts",
                    "en": "3-minute demo",
                },
                "description": {
                    "es": "Mira cómo Acten convierte una reunión real en un acta profesional con tareas asignadas.",
                    "ca": "Mira com Acten converteix una reunió real en una acta professional amb tasques assignades.",
                    "en": "See how Acten turns a real meeting into a professional minute with assigned tasks.",
                },
                "url": "#",
                "icon": "video",
            },
            {
                "category": {"es": "Blog", "ca": "Blog", "en": "Blog"},
                "title": {
                    "es": "5 errores frecuentes al hacer seguimiento de tareas",
                    "ca": "5 errors freqüents en fer seguiment de tasques",
                    "en": "5 common mistakes when following up on tasks",
                },
                "description": {
                    "es": "Diagnóstico y solución a los patrones que hacen que las decisiones se pierdan entre reunión y reunión.",
                    "ca": "Diagnòstic i solució als patrons que fan que les decisions es perdin entre reunió i reunió.",
                    "en": "Diagnosis and fix for the patterns that make decisions get lost between meetings.",
                },
                "url": "#",
                "icon": "blog",
            },
        ],
    },

    # ─── Empresa ─────────────────────────────────────────────────────────
    "company": {
        "eyebrow": {"es": "Quiénes somos", "ca": "Qui som", "en": "About us"},
        "title": {
            "es": "Construimos la capa de inteligencia que les faltaba a tus reuniones.",
            "ca": "Construïm la capa d'intel·ligència que els faltava a les teves reunions.",
            "en": "We build the intelligence layer your meetings were missing.",
        },
        "subtitle": {"es": "", "ca": "", "en": ""},
        "story": {
            "es": "Acten nació de una frustración compartida: las decisiones más importantes de las empresas se toman en reuniones, pero terminan dispersas en chats, documentos perdidos y memorias frágiles. Creemos que la IA puede cerrar esa brecha — no reemplazando la conversación humana, sino capturándola, estructurándola y convirtiéndola en acción concreta.",
            "ca": "Acten va néixer d'una frustració compartida: les decisions més importants de les empreses es prenen en reunions, però acaben disperses en xats, documents perduts i memòries fràgils. Creiem que la IA pot tancar aquesta bretxa — no substituint la conversa humana, sinó capturant-la, estructurant-la i convertint-la en acció concreta.",
            "en": "Acten was born from a shared frustration: the most important business decisions are made in meetings, yet they end up scattered across chats, lost documents and fragile memories. We believe AI can close that gap — not by replacing human conversation but by capturing, structuring and turning it into concrete action.",
        },
        "mission": {
            "es": "Hacer que cada reunión empresarial termine con decisiones claras, responsables asignados y trazabilidad completa — sin trabajo manual.",
            "ca": "Aconseguir que cada reunió empresarial acabi amb decisions clares, responsables assignats i traçabilitat completa — sense feina manual.",
            "en": "Make every business meeting end with clear decisions, assigned owners and full traceability — with no manual work.",
        },
        "values": [
            {
                "title": {"es": "Claridad sobre velocidad", "ca": "Claredat sobre velocitat", "en": "Clarity over speed"},
                "description": {
                    "es": "Preferimos un acta clara a una rápida. La inteligencia útil requiere precisión.",
                    "ca": "Preferim una acta clara a una de ràpida. La intel·ligència útil requereix precisió.",
                    "en": "We prefer a clear minute over a fast one. Useful intelligence demands precision.",
                },
            },
            {
                "title": {"es": "Privacidad por diseño", "ca": "Privadesa per disseny", "en": "Privacy by design"},
                "description": {
                    "es": "Tus datos viven aislados por tenant. Nunca entrenamos modelos con tu información.",
                    "ca": "Les teves dades viuen aïllades per tenant. Mai entrenem models amb la teva informació.",
                    "en": "Your data lives isolated per tenant. We never train models with your information.",
                },
            },
            {
                "title": {"es": "Open en lo que se puede", "ca": "Obert en el que es pot", "en": "Open where we can"},
                "description": {
                    "es": "Documentamos APIs, soportamos webhooks y nos integramos con tu stack actual.",
                    "ca": "Documentem APIs, suportem webhooks i ens integrem amb la teva stack actual.",
                    "en": "We document APIs, support webhooks and integrate with your current stack.",
                },
            },
            {
                "title": {"es": "Equipo distribuido", "ca": "Equip distribuït", "en": "Distributed team"},
                "description": {
                    "es": "Construimos desde LATAM con foco global. Nuestra zona horaria es la del cliente.",
                    "ca": "Construïm des de LATAM amb focus global. La nostra zona horària és la del client.",
                    "en": "We build from LATAM with a global focus. Our timezone is the client's.",
                },
            },
        ],
        "stats": [
            {"label": {"es": "Reuniones procesadas", "ca": "Reunions processades", "en": "Meetings processed"}, "value": "12K+"},
            {"label": {"es": "Horas ahorradas / mes", "ca": "Hores estalviades / mes", "en": "Hours saved / month"}, "value": "1.8K"},
            {"label": {"es": "Tasa de tareas ejecutadas", "ca": "Taxa de tasques executades", "en": "Task execution rate"}, "value": "94%"},
            {"label": {"es": "Idiomas soportados", "ca": "Idiomes suportats", "en": "Languages supported"}, "value": "11"},
        ],
    },

    # ─── Contacto / Demo ─────────────────────────────────────────────────
    "contact": {
        "eyebrow": {"es": "Hablemos", "ca": "Parlem", "en": "Let's talk"},
        "title": {
            "es": "Agenda una demo o cuéntanos qué necesitas.",
            "ca": "Agenda una demo o explica'ns què necessites.",
            "en": "Book a demo or tell us what you need.",
        },
        "subtitle": {
            "es": "Te respondemos en menos de 24h hábiles con una demo personalizada para tu equipo.",
            "ca": "Et responem en menys de 24h hàbils amb una demo personalitzada per al teu equip.",
            "en": "We reply within 24 business hours with a personalized demo for your team.",
        },
        "email": "hola@acten.app",
        "phone": "+57 300 000 0000",
        "address": {
            "es": "Bogotá, Colombia · Remoto LATAM",
            "ca": "Bogotà, Colòmbia · Remot LATAM",
            "en": "Bogotá, Colombia · Remote LATAM",
        },
        "form_name_label": {"es": "Nombre", "ca": "Nom", "en": "Name"},
        "form_email_label": {"es": "Correo corporativo", "ca": "Correu corporatiu", "en": "Business email"},
        "form_company_label": {"es": "Empresa", "ca": "Empresa", "en": "Company"},
        "form_role_label": {"es": "Cargo (opcional)", "ca": "Càrrec (opcional)", "en": "Role (optional)"},
        "form_message_label": {
            "es": "¿En qué te podemos ayudar?",
            "ca": "En què et podem ajudar?",
            "en": "How can we help?",
        },
        "form_cta_label": {"es": "Enviar mensaje", "ca": "Enviar missatge", "en": "Send message"},
        "form_success": {
            "es": "¡Mensaje recibido! Te contactamos en menos de 24h hábiles.",
            "ca": "Missatge rebut! Et contactem en menys de 24h hàbils.",
            "en": "Message received! We'll be in touch within 24 business hours.",
        },
        "form_error": {
            "es": "No pudimos enviar tu mensaje. Escríbenos directo a hola@acten.app.",
            "ca": "No hem pogut enviar el teu missatge. Escriu-nos directament a hola@acten.app.",
            "en": "We couldn't send your message. Write us directly at hola@acten.app.",
        },
    },

    # ─── CTA final ───────────────────────────────────────────────────────
    "final_cta": {
        "eyebrow": {"es": "", "ca": "", "en": ""},
        "title_lead": {
            "es": "Convierte cada reunión en una ",
            "ca": "Converteix cada reunió en un ",
            "en": "Turn every meeting into a ",
        },
        "title_highlight": {
            "es": "ventaja competitiva.",
            "ca": "avantatge competitiu.",
            "en": "competitive advantage.",
        },
        "title": {
            "es": "Convierte cada reunión en una ventaja competitiva.",
            "ca": "Converteix cada reunió en un avantatge competitiu.",
            "en": "Turn every meeting into a competitive advantage.",
        },
        "subtitle": {
            "es": "Solicita una demo personalizada y descubre cómo Acten puede transformar la productividad de tu equipo.",
            "ca": "Demana una demo personalitzada i descobreix com Acten pot transformar la productivitat del teu equip.",
            "en": "Request a personalized demo and discover how Acten can transform your team's productivity.",
        },
        "cta_label": {"es": "Solicitar demo", "ca": "Demana demo", "en": "Request a demo"},
        "cta_anchor": "#contact",
        "secondary_label": {"es": "Hablar con ventas", "ca": "Parlar amb vendes", "en": "Talk to sales"},
        "secondary_anchor": "#contact",
        # Panel derecho con estados — visual demostrativo
        "status_items": [
            {"label": {"es": "Decisión tomada", "ca": "Decisió presa", "en": "Decision made"}, "tone": "success"},
            {"label": {"es": "Tarea asignada", "ca": "Tasca assignada", "en": "Task assigned"}, "tone": "success"},
            {"label": {"es": "Riesgo identificado", "ca": "Risc identificat", "en": "Risk identified"}, "tone": "warning"},
            {"label": {"es": "Documento generado", "ca": "Document generat", "en": "Document generated"}, "tone": "success"},
        ],
    },

    # ─────────────────────────────────────────────────────────────────────
    # ONE-PAGE: secciones #pricing, #resources, #company viven inline en
    # landing.component.html. La sección #company renderiza el grid de
    # `case_studies.items` (editable desde el CMS, tab "Casos de éxito").
    #
    # NOTA: product_page / solutions_page / case_study_detail / demo_page /
    # contact_page existen en este DEFAULT_CONTENT por compatibilidad con
    # tenants viejos que los tengan persistidos en landing_content_json,
    # pero la UI actual NO los renderiza (la landing es one-page).
    # ─────────────────────────────────────────────────────────────────────

    # ─── product_page (legacy, no se renderiza) ──────────────────────────
    "product_page": {
        "eyebrow": "Producto",
        "title": "Todo lo que necesitas para transformar reuniones en resultados accionables.",
        "subtitle": (
            "Acten conecta cada etapa de la conversación con su impacto: captura, "
            "interpreta, organiza y da seguimiento. Sin trabajo manual."
        ),
        "blocks": [
            {
                "title": "Captura e inteligencia",
                "items": [
                    "Transcripción multilenguaje",
                    "Identificación de hablantes, temas y contexto",
                    "Detección de momentos clave",
                    "Reconocimiento de intenciones",
                ],
            },
            {
                "title": "Estructura y organización",
                "items": [
                    "Resúmenes ejecutivos",
                    "Decisiones y riesgos",
                    "Tareas con responsables",
                    "Línea de tiempo",
                ],
            },
            {
                "title": "Documentos y comunicación",
                "items": [
                    "Actas profesionales",
                    "Reportes personalizados",
                    "Plantillas reutilizables",
                    "Envío automático por correo",
                ],
            },
            {
                "title": "Seguimiento y control",
                "items": [
                    "Estado de tareas",
                    "Recordatorios automáticos",
                    "Historial completo",
                    "Métricas y analítica",
                ],
            },
        ],
        "cta_title": "Conoce Acten en acción",
        "cta_subtitle": "Pídenos una demo y vemos cómo aplica a tu equipo.",
        "cta_label": "Solicitar demo",
        "cta_url": "/demo",
    },

    # ─── /soluciones ─────────────────────────────────────────────────────
    "solutions_page": {
        "eyebrow": "Soluciones",
        "title": "Acten se adapta a cada equipo, industria y necesidad.",
        "subtitle": "",
        "audiences": [
            {
                "key": "equipos",
                "title": "Equipos de trabajo",
                "description": "Reuniones internas que terminan en decisiones y compromisos claros.",
                "icon": "users",
            },
            {
                "key": "empresas",
                "title": "Empresas",
                "description": "Visibilidad cross-equipo, trazabilidad para liderazgo, governance.",
                "icon": "building",
            },
            {
                "key": "publico",
                "title": "Instituciones públicas",
                "description": "Cumplimiento, auditoría y memoria institucional de cada sesión.",
                "icon": "shield",
            },
        ],
        "use_cases_title": "Casos de uso populares",
        "use_cases": [
            {"title": "Reuniones de proyecto", "description": "Alineación, decisiones y entregables."},
            {"title": "Comités ejecutivos", "description": "Memoria institucional y trazabilidad."},
            {"title": "Ventas y cliente", "description": "Followup automático tras cada llamada."},
            {"title": "Recursos humanos", "description": "Procesos de selección y feedback estructurado."},
            {"title": "Producto / Discovery", "description": "Captura de insights y prioridades."},
            {"title": "Atención al cliente", "description": "Casos resueltos con contexto completo."},
        ],
        "industries_title": "Soluciones para cada industria",
        "industries": [
            {"key": "tech", "title": "Tecnología", "icon": "code"},
            {"key": "fin", "title": "Finanzas", "icon": "chart"},
            {"key": "health", "title": "Salud", "icon": "health"},
            {"key": "edu", "title": "Educación", "icon": "book"},
            {"key": "manu", "title": "Manufactura", "icon": "factory"},
            {"key": "public", "title": "Sector público", "icon": "gov"},
        ],
    },

    # ─── /empresa  ─── (case studies grid) ───────────────────────────────
    "case_studies": {
        "eyebrow": "Empresas",
        "title": "Historias reales que confían en Acten.",
        "subtitle": "",
        "items": [
            {
                "slug": "technova",
                "company": "TechNova",
                "logo_url": "",
                "tagline": "Reuniones de proyecto",
                "summary": "MTPs nuevas llegaban sin seguimiento. Ahora cada acción se ejecuta.",
                "kpis": [
                    {"label": "Usuarios activos", "value": "10K+"},
                    {"label": "Reuniones procesadas", "value": "2.5M+"},
                    {"label": "Satisfacción", "value": "98%"},
                    {"label": "Países", "value": "120+"},
                ],
                "sectors_served": ["Comités ejecutivos", "Ventas y cliente", "Recursos humanos", "Educación"],
            },
            {
                "slug": "buildfast",
                "company": "BuildFast",
                "logo_url": "",
                "tagline": "Toma de decisiones",
                "summary": "Reuniones de equipo con seguimiento real y trazabilidad ejecutiva.",
                "kpis": [],
                "sectors_served": [],
            },
            {
                "slug": "datacore",
                "company": "DataCore",
                "logo_url": "",
                "tagline": "Atención al cliente",
                "summary": "Calls comerciales que terminan con followup automático en CRM.",
                "kpis": [],
                "sectors_served": [],
            },
        ],
    },

    # ─── /casos/:slug — detalle de caso de uso ─────────────────────────
    "case_study_detail": {
        "eyebrow": "Caso de uso",
        # Plantilla genérica — el componente popula con el caso real
        # buscando por slug en case_studies.items. Estos campos son los
        # labels editables.
        "challenge_label": "El desafío",
        "solution_label": "Con Acten",
        "results_label": "Resultados",
        "other_cases_label": "Otros casos de uso",
    },

    # ─── /demo ──────────────────────────────────────────────────────────
    "demo_page": {
        "eyebrow": "Solicita una demo personalizada",
        "title": "Descubre cómo Acten puede transformar la productividad de tu equipo.",
        "subtitle": "",
        "bullets": [
            "Cómo funciona Acten en tiempo real",
            "Cómo se adapta a tu operación",
            "Cómo lo integramos con tus herramientas",
            "Plan de implementación recomendado",
        ],
        "form_name_label": "Nombre completo",
        "form_email_label": "Correo corporativo",
        "form_company_label": "Empresa",
        "form_role_label": "Cargo",
        "form_team_size_label": "Tamaño del equipo",
        "form_team_size_options": ["1-10", "11-50", "51-200", "201-1000", "1000+"],
        "form_use_case_label": "¿Cuál es tu caso de uso principal?",
        "form_message_label": "Mensaje (opcional)",
        "form_cta_label": "Solicitar demo",
        "form_success": "¡Recibimos tu solicitud! Te contactamos en menos de 24h hábiles.",
        "form_error": "No pudimos enviar tu mensaje. Escríbenos directo a hola@acten.app.",
        "privacy_label": "Acepto la Política de Privacidad",
        "privacy_url": "/privacy",
    },

    # ─── /contacto ──────────────────────────────────────────────────────
    "contact_page": {
        "eyebrow": "Hablemos",
        "title": "¿Tienes preguntas o quieres saber más?",
        "subtitle": "Estamos aquí para ayudarte.",
        "channels": [
            {"label": "Ventas",   "value": "ventas@acten.app",    "icon": "mail"},
            {"label": "Soporte",  "value": "soporte@acten.app",   "icon": "support"},
            {"label": "WhatsApp", "value": "+1 (555) 123-4567",   "icon": "whatsapp"},
        ],
        "offices_title": "Oficinas",
        "offices": [
            {"city": "Madrid, España", "address": "Calle de Serrano 123"},
            {"city": "Barcelona, España", "address": "Avenida Diagonal 456"},
            {"city": "Ciudad de México, México", "address": "Av. Reforma 789"},
        ],
        "form_name_label": "Nombre completo",
        "form_email_label": "Correo electrónico",
        "form_company_label": "Empresa",
        "form_message_label": "Mensaje",
        "form_cta_label": "Enviar mensaje",
        "form_success": "¡Mensaje enviado! Te respondemos pronto.",
        "form_error": "No pudimos enviar tu mensaje. Intenta de nuevo o escríbenos a hola@acten.app.",
        "privacy_label": "Acepto la Política de Privacidad",
    },

    # ─── Footer ──────────────────────────────────────────────────────────
    "footer": {
        "tagline": {
            "es": "El asistente inteligente de reuniones que transforma conversaciones en claridad, decisiones y acción.",
            "ca": "L'assistent intel·ligent de reunions que transforma converses en claredat, decisions i acció.",
            "en": "The intelligent meeting assistant that turns conversations into clarity, decisions, and action.",
        },
        "newsletter_title": {
            "es": "Suscríbete a nuestro newsletter",
            "ca": "Subscriu-te al nostre butlletí",
            "en": "Subscribe to our newsletter",
        },
        "newsletter_subtitle": {
            "es": "Recibe novedades y mejores prácticas para equipos de alto rendimiento.",
            "ca": "Rep novetats i bones pràctiques per a equips d'alt rendiment.",
            "en": "Get updates and best practices for high-performance teams.",
        },
        "newsletter_placeholder": "tu@email.com",
        "newsletter_cta": {"es": "Suscribirme", "ca": "Subscriu-me", "en": "Subscribe"},
        "newsletter_success": {
            "es": "¡Gracias! Te avisamos cuando salga algo bueno.",
            "ca": "Gràcies! T'avisarem quan tinguem novetats.",
            "en": "Thanks! We'll let you know when something good drops.",
        },
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
                "title": {"es": "Producto", "ca": "Producte", "en": "Product"},
                "links": [
                    {"label": {"es": "Capacidades", "ca": "Capacitats", "en": "Features"}, "url": "#features"},
                    {"label": {"es": "Cómo funciona", "ca": "Com funciona", "en": "How it works"}, "url": "#flow"},
                    {"label": {"es": "Integraciones", "ca": "Integracions", "en": "Integrations"}, "url": "#integrations"},
                    {"label": {"es": "Solicitar demo", "ca": "Demana demo", "en": "Request a demo"}, "url": "#contact"},
                ],
            },
            {
                "title": {"es": "Empresa", "ca": "Empresa", "en": "Company"},
                "links": [
                    {"label": {"es": "Quiénes somos", "ca": "Qui som", "en": "About us"}, "url": "#testimonials"},
                    {"label": {"es": "Contacto", "ca": "Contacte", "en": "Contact"}, "url": "#contact"},
                    {"label": {"es": "Iniciar sesión", "ca": "Inicia sessió", "en": "Log in"}, "url": "/login"},
                ],
            },
            {
                "title": {"es": "Legal", "ca": "Legal", "en": "Legal"},
                "links": [
                    {"label": {"es": "Privacidad", "ca": "Privadesa", "en": "Privacy"}, "url": "/privacy"},
                    {"label": {"es": "Términos", "ca": "Termes", "en": "Terms"}, "url": "/terms"},
                ],
            },
        ],
        "legal_links": [
            {"label": {"es": "Privacidad", "ca": "Privadesa", "en": "Privacy"}, "url": "/privacy"},
            {"label": {"es": "Términos", "ca": "Termes", "en": "Terms"}, "url": "/terms"},
            {"label": {"es": "Seguridad", "ca": "Seguretat", "en": "Security"}, "url": "/privacy"},
            {"label": {"es": "Cookies", "ca": "Galetes", "en": "Cookies"}, "url": "/privacy"},
        ],
        "language_label": {
            "es": "Español (ES)",
            "ca": "Català (CA)",
            "en": "English (EN)",
        },
        "copyright": {
            "es": "Acten.ai. Todos los derechos reservados.",
            "ca": "Acten.ai. Tots els drets reservats.",
            "en": "Acten.ai. All rights reserved.",
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `overrides` sobre `base` recursivamente, con soporte i18n.

    - dicts se mergean recursivamente
    - listas y valores escalares se REEMPLAZAN (no concatenan) — es lo que
      queremos para listas como features/testimonials: si el admin quita uno,
      el array nuevo es la verdad.

    SOPORTE I18N: si en `base` un campo es i18n dict {es,ca,en} y en
    `overrides` es un string plano (legacy, tenant viejo sin traducciones),
    el string se "promociona" a {es: string} preservando ca/en del default.
    Esto garantiza que tenants antiguos automáticamente reciban las
    traducciones ca/en de los DEFAULTS aunque tengan strings planos
    guardados en su landing_content_json.
    """
    out = copy.deepcopy(base)
    for key, val in overrides.items():
        base_val = out.get(key)
        # i18n promotion: base es dict de idiomas, override es string plano
        if isinstance(val, str) and _is_i18n_dict(base_val):
            promoted = dict(base_val)  # preserva ca/en del default
            promoted["es"] = val        # tenant override solo cambia 'es'
            out[key] = promoted
        elif isinstance(val, dict) and isinstance(base_val, dict):
            out[key] = _deep_merge(base_val, val)
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


# ─────────────────────────────────────────────────────────────────────────────
# i18n helpers — resuelven contenido multilenguaje del CMS.
#
# Cada string del CMS puede estar guardado como:
#   - String plano: "Hola"                        → backward-compat, ES default
#   - Dict idioma:  {"es": "Hola", "ca": "Hola",  → multilenguaje
#                    "en": "Hi"}
#
# `resolve_i18n_in_tree` recorre todo el árbol del content y reemplaza los
# dicts {es,ca,en,…} por la string del idioma pedido. Si el idioma pedido
# no está, cae a 'es' como fallback (siempre presente en defaults).
# ─────────────────────────────────────────────────────────────────────────────

_LANG_KEYS = {"es", "ca", "en"}


def _is_i18n_dict(value: Any) -> bool:
    """True si el value parece un dict de traducciones (las claves coinciden
    con los códigos de idioma soportados y NADA más)."""
    if not isinstance(value, dict) or not value:
        return False
    keys = set(value.keys())
    # Permitimos subconjunto de los idiomas soportados, pero NO claves extras
    # (eso indicaría que es un dict de negocio, no de traducción).
    return keys.issubset(_LANG_KEYS) and all(isinstance(v, str) for v in value.values())


def _resolve_i18n_value(value: Any, lang: str) -> Any:
    """Resuelve un único valor. Si es un i18n dict, devuelve la traducción;
    si es cualquier otra cosa, lo devuelve tal cual (lo procesa el caller
    si es lista/dict anidado)."""
    if _is_i18n_dict(value):
        return value.get(lang) or value.get("es") or next(iter(value.values()), "")
    return value


def resolve_i18n_in_tree(node: Any, lang: str = "es") -> Any:
    """Recorre recursivamente un árbol (dict / list / scalar) y resuelve
    cada i18n dict al idioma pedido. Devuelve una NUEVA estructura (no
    muta el input)."""
    if _is_i18n_dict(node):
        return _resolve_i18n_value(node, lang)
    if isinstance(node, dict):
        return {k: resolve_i18n_in_tree(v, lang) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_i18n_in_tree(item, lang) for item in node]
    return node


def get_landing_content_localized(
    db: Session, tenant_id: int, lang: str = "es",
) -> Dict[str, Any]:
    """Versión localizada del get_landing_content: resuelve i18n dicts a
    un único idioma. Pensado para el endpoint público (visitor)."""
    raw = get_landing_content(db, tenant_id)
    return resolve_i18n_in_tree(raw, lang)


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


def _wrap_strings_with_lang(node: Any, lang: str, base: Any = None) -> Any:
    """Recorre un patch y envuelve cada string en {lang: value}, preservando
    las traducciones que `base` ya tenga en otros idiomas.

    - String suelto en el patch → {lang: value} (o si base[key] ya es i18n
      dict, mergea agregando/actualizando solo la clave lang).
    - Dict: recurse en cada hijo.
    - Lista de objetos (items): recurse en cada item posicionalmente contra
      el base correspondiente.
    - Otros tipos (bool/int/None/lista de strings simples): se devuelven
      tal cual — no son traducibles."""
    if isinstance(node, str):
        if _is_i18n_dict(base):
            # Mantiene las traducciones existentes, solo actualiza la del lang
            updated = dict(base)
            updated[lang] = node
            return updated
        return {lang: node}
    if isinstance(node, dict):
        out: Dict[str, Any] = {}
        base_dict = base if isinstance(base, dict) else {}
        for k, v in node.items():
            out[k] = _wrap_strings_with_lang(v, lang, base_dict.get(k))
        return out
    if isinstance(node, list):
        out_list = []
        base_list = base if isinstance(base, list) else []
        for i, item in enumerate(node):
            sub_base = base_list[i] if i < len(base_list) else None
            out_list.append(_wrap_strings_with_lang(item, lang, sub_base))
        return out_list
    return node


def update_landing_content_i18n(
    db: Session, tenant_id: int, patch: Dict[str, Any], edit_lang: str,
) -> Dict[str, Any]:
    """Variante i18n del update: envuelve strings en {lang: value} sobre el
    contenido existente, luego mergea. Garantiza que editando solo en 'ca'
    no se pisen las traducciones en 'es' y 'en' previamente guardadas.

    Devuelve el contenido LOCALIZADO al edit_lang (para que el form se
    refresque con lo recién guardado en ese idioma)."""
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} no existe.")

    current = _safe_load(tenant.landing_content_json)
    wrapped = _wrap_strings_with_lang(patch, edit_lang, current)
    merged = _deep_merge(current, wrapped)
    tenant.landing_content_json = json.dumps(merged, ensure_ascii=False)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    # Devolvemos el contenido localizado al edit_lang, igual que admin_get_landing.
    full = _deep_merge(DEFAULT_CONTENT, merged)
    return resolve_i18n_in_tree(full, edit_lang)


def reset_landing_content(db: Session, tenant_id: int) -> Dict[str, Any]:
    """Reset duro: borra el JSON guardado y vuelve a los defaults."""
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} no existe.")
    tenant.landing_content_json = "{}"
    db.add(tenant)
    db.commit()
    return copy.deepcopy(DEFAULT_CONTENT)
