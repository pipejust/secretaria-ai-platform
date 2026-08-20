"""La única tabla que sabe cuál proveedor es cuál.

Lo que cambia entre Google, Microsoft y Zoho no son tres URLs: son las
formas de los datos —Graph llama `subject` al título y mete las fechas en
un objeto con la zona aparte; Zoho manda las suyas como texto
`yyyyMMddTHHmmssZ` y separa los permisos por comas—. Por eso cada uno
vive en su propio archivo y aquí solo está lo que se puede tabular.

Añadir un cuarto proveedor es tocar este archivo y escribir su módulo.
"""

from __future__ import annotations

from typing import Optional

# Los permisos que se piden, y ni uno más. Un permiso que no se usa solo
# sirve para que la pantalla de consentimiento dé más miedo del necesario.
GOOGLE_SCOPES = (
    "openid email "
    # events —y no `calendar` a secas— porque escribir eventos es lo que
    # hace falta; `calendar` además deja crear y borrar calendarios enteros.
    "https://www.googleapis.com/auth/calendar.events "
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
)

# `offline_access` a secas no es válido en Microsoft: tiene que ir con
# openid o con un recurso, o contesta AADSTS70011 quejándose del permiso
# antes siquiera de mirar las credenciales.
MICROSOFT_SCOPES = "openid email profile offline_access Calendars.ReadWrite"

ZOHO_SCOPES = "ZohoCalendar.calendar.READ,ZohoCalendar.event.ALL,email,profile"


# Zoho no tiene un dominio único: una cuenta creada en Europa vive en
# accounts.zoho.eu, la de India en .in, y los tokens de un centro no
# valen en otro.
#
# La lista es exacta a propósito. Validar por «empieza por» no vale:
# `accounts.zoho.evil.com` empieza por `accounts.zoho.` — y ahí es
# justamente donde se manda el secreto de la aplicación.
ZOHO_CENTROS = {
    "com":    ("accounts.zoho.com",    "www.zohoapis.com"),
    "eu":     ("accounts.zoho.eu",     "www.zohoapis.eu"),
    "in":     ("accounts.zoho.in",     "www.zohoapis.in"),
    "com.au": ("accounts.zoho.com.au", "www.zohoapis.com.au"),
    "jp":     ("accounts.zoho.jp",     "www.zohoapis.jp"),
    "com.cn": ("accounts.zoho.com.cn", "www.zohoapis.com.cn"),
    "ca":     ("accounts.zohocloud.ca", "www.zohoapis.ca"),
}


def zoho_dominios(data_center: str) -> tuple[str, str]:
    """(servidor de cuentas, servidor de API) del centro, o los de `.com`."""
    return ZOHO_CENTROS.get((data_center or "com").strip().lower(), ZOHO_CENTROS["com"])


def zoho_servidor_valido(accounts_server: str) -> Optional[str]:
    """El centro al que corresponde ese `accounts-server=`, o None.

    Zoho dice en el retorno del OAuth en qué servidor de cuentas vive esa
    persona y exige canjear el código ahí. Ignorarlo hace fallar en
    silencio a quien tenga la cuenta en otro centro; aceptarlo tal cual
    llega es mandar el secreto de la aplicación a donde diga un tercero.
    """
    host = (accounts_server or "").strip().lower()
    host = host.replace("https://", "").replace("http://", "").strip("/")
    for centro, (cuentas, _api) in ZOHO_CENTROS.items():
        if host == cuentas:
            return centro
    return None


PROVEEDORES: dict[str, dict] = {
    "google": {
        "etiqueta": "Google Calendar",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v3/userinfo",
        "api_base": "https://www.googleapis.com/calendar/v3",
        "scopes": GOOGLE_SCOPES,
        # Sin `select_account` los proveedores reutilizan en silencio la
        # sesión abierta y la segunda cuenta de una persona no se puede
        # conectar nunca. Sin `consent` una reconexión no devuelve
        # refresh_token y deja la cuenta a medio conectar.
        "extra_auth": {"access_type": "offline", "prompt": "consent select_account"},
        "revoca": "https://oauth2.googleapis.com/revoke",
        "escribe": True,
        "centros": False,
    },
    "microsoft": {
        "etiqueta": "Microsoft 365 / Outlook",
        "auth_tpl": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token_tpl": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/v1.0/me",
        "api_base": "https://graph.microsoft.com/v1.0",
        "scopes": MICROSOFT_SCOPES,
        "extra_auth": {"prompt": "select_account"},
        # Microsoft no ofrece llamada para revocar. Al desconectar se borra
        # lo nuestro y se dice dónde se retira del todo; callarlo dejaría a
        # la persona creyendo que ya no tenemos acceso.
        "revoca": None,
        "revoca_manual": "https://myaccount.microsoft.com → Privacidad → Aplicaciones",
        "escribe": True,
        "centros": False,
    },
    "zoho": {
        "etiqueta": "Zoho Calendar",
        "auth_tpl": "https://{cuentas}/oauth/v2/auth",
        "token_tpl": "https://{cuentas}/oauth/v2/token",
        "userinfo_tpl": "https://{cuentas}/oauth/user/info",
        "api_tpl": "https://{api}/calendar/v1",
        "scopes": ZOHO_SCOPES,
        "extra_auth": {"access_type": "offline", "prompt": "consent"},
        "revoca_tpl": "https://{cuentas}/oauth/v2/token/revoke",
        "escribe": True,
        "centros": True,
        # Su API rechaza rangos mayores: devuelve un error, no una lista
        # recortada. Traer un año son doce vueltas y el código las hace.
        "max_dias_por_consulta": 31,
    },
}

# Cómo se llama la fila de IntegrationSetting con las credenciales de cada uno.
CLAVE_CONFIG = {
    "google": "google_calendar",
    "microsoft": "microsoft_calendar",
    "zoho": "zoho_calendar",
}


def conocido(provider: str) -> bool:
    return provider in PROVEEDORES


def etiqueta(provider: str) -> str:
    return PROVEEDORES.get(provider, {}).get("etiqueta", provider)
