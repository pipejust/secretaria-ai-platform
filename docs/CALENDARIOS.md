# Calendarios

Cómo funciona el módulo en Acten, qué se puede conectar y qué falta.
Está portado del módulo de la plataforma Altum, donde ya lleva tiempo
funcionando; lo que aquí se dice como «costó averiguarlo» viene de allá.

---

## 1. La idea

Un calendario no es una vista, es una **pertenencia**. Cada cosa que se
pinta —un evento, un festivo, una tarea, una sesión— pertenece a
exactamente un calendario, y cada calendario tiene nombre, color y una
casilla que lo enciende y lo apaga. Es el modelo de Google Calendar y el
de Apple, y no es estético: es lo que permite decir «hoy no quiero ver
las tareas» sin perder nada.

Hay dos familias:

| | Derivados | Guardados |
|---|---|---|
| Ejemplos | Sesiones, Tareas, Festivos | Mi calendario, uno de equipo, uno suscrito, uno de Google |
| ¿Tienen fila? | No | Sí, en `calendar` |
| ¿De dónde salen? | Se calculan de otros datos | Se crearon o se conectaron |
| Clave | `sys:festivos` | un uuid |
| ¿Se puede escribir? | No | Depende del permiso |

Los derivados no se guardan **a propósito**. Los festivos de Colombia se
calculan con la Ley Emiliani; las tareas y las sesiones ya están en sus
tablas. Duplicarlas garantiza que un día no coincidan. De ellos solo se
guarda lo de cada persona: si lo ve y de qué color.

Por eso todo se identifica con una **clave de texto**, no con un id:
vale para las dos familias y la preferencia de cada quien cuelga de ahí.

## 2. Las tablas

```
calendar          un calendario guardado
  key uuid · name · color · origin · timezone
  origin: propio | equipo | proyecto | suscrito | google | microsoft | zoho
  owner_user_id · project_id · is_default · read_only
  ics_url · account_id · external_id · sync_token · last_synced_at · sync_error

calendarshare     con quién se comparte (user_id en blanco = toda la empresa)
calendarpref      la casilla y el color, por (usuario, clave)
calendaraccount   una cuenta de fuera. Llave: (usuario, proveedor, CORREO)
calendarentry     un evento creado en Acten (con external_uid si tiene copia allá)
externalevent     lo traído de fuera, en caché
```

**Por qué `externalevent` y no todo junto.** Lo que viene de fuera no es
nuestro. Mezclarlo haría que editar aquí pareciera posible, y al
desconectar la cuenta quedarían huérfanos eventos que ya no existen en
ningún sitio. Se guarda aparte, se pinta igual y se va entero con su
calendario.

## 3. Permisos

Cuatro niveles, cada uno incluye al anterior:

| Permiso | Qué puede |
|---|---|
| `ocupado` | Ve que la hora está tomada, no de qué |
| `ver` | Ve el evento entero |
| `editar` | Además crea y cambia eventos dentro |
| `gestionar` | Además comparte el calendario y lo puede borrar |

`ocupado` existe por un motivo concreto: permite cuadrar una reunión con
alguien sin enterarse de a qué médico va.

Reglas que no se negocian:

- El administrador de la empresa gestiona todos. Partir esto dejaría
  calendarios que nadie puede arreglar cuando quien los hizo se va.
- El dueño gestiona el suyo.
- **El permiso filtra los datos, no solo los botones.** Un calendario en
  `ocupado` devuelve la franja sin el título. Es el punto donde esto
  deja de ser cosmético.
- **La lista es de quien mira; la potestad es otra cosa.** Al
  administrador no se le llena la barra lateral con la agenda personal
  de todo el equipo aunque pueda gestionarlas.

## 4. El API

Todo cuelga de `/api/v1/calendars`.

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/calendars` | La lista de quien pregunta, con permiso, color y casilla resueltos |
| POST | `/calendars` | Crea uno propio (o de equipo, si administra) |
| PATCH | `/calendars/{clave}` | Nombre, descripción, color. Requiere `gestionar` |
| DELETE | `/calendars/{clave}` | Se lo lleva; sus eventos propios pasan al de por defecto |
| PUT | `/calendars/{clave}/preferencia` | La casilla y el color de quien pide. Vale para `sys:...` |
| GET/POST | `/calendars/{clave}/compartido` | Con quién está compartido / compartir |
| DELETE | `/calendars/{clave}/compartido/{user_id}` | Dejar de compartir (`0` = el de empresa) |
| POST | `/calendars/suscribir` | Añade uno de fuera por su dirección `.ics` |
| POST | `/calendars/{clave}/sincronizar` · `/calendars/sincronizar` | Releer ahora |
| GET | `/calendars/eventos` | La agenda: `desde`, `hasta`, `calendarios` |
| POST/PATCH/DELETE | `/calendars/eventos[/{id}]` | Crear, cambiar, mover y borrar |
| GET | `/calendars/cuentas` · DELETE `/calendars/cuentas/{id}` | Cuentas conectadas |
| GET | `/calendars/{proveedor}/estado` · `/conectar` · `/callback` | El viaje de OAuth |
| GET/PUT | `/calendars/config/proveedores[/{proveedor}]` | Credenciales (administrador) |
| POST | `/calendars/config/proveedores/{proveedor}/comprobar` | Si el proveedor reconoce la app |

Cada entrada de la agenda trae `calendar`, la clave de a cuál pertenece.
**La pantalla apaga y enciende con eso, sin volver a preguntar**: pedir
la agenda otra vez por cada clic haría que el calendario parpadeara.

## 5. Qué se puede conectar

### 5.1 Por dirección `.ics` — sin claves, sin cuentas

Es como se suscriben entre sí Google, Apple y Outlook. Cero
configuración, funciona hoy; a cambio es solo lectura y se refresca por
sondeo (cada 30 minutos).

- Google → Configuración del calendario → Integrar calendario →
  «Dirección secreta en formato iCal».
- iCloud → Compartir calendario → Calendario público (`webcal://`, se
  traduce solo).
- Outlook → Configuración → Calendarios compartidos → Publicar calendario.

**El cuidado que no es opcional:** la URL la escribe una persona y la
pide nuestro servidor. Sin filtro eso es una puerta a `localhost`, a la
base de datos y al servicio de metadatos de la nube. Se resuelve el
nombre y se miran **todas** las direcciones, no se siguen redirecciones a
ciegas, y se corta por tamaño (10 MB) y por tiempo.

### 5.2 Google, Microsoft y Zoho — con la cuenta

Lo que ve quien lo usa: pulsa «Conectar», entra con su cuenta, acepta.

Lo que hay debajo: la plataforma tiene que estar **registrada como
aplicación**, una sola vez, con un identificador y un secreto. No es una
clave por persona; es lo mismo que hace cualquier app que ofrece «entrar
con Google». Se pega en **Configuración → Integraciones → Calendarios**,
donde además está escrita la dirección de retorno exacta que hay que
pegar en la consola del proveedor.

El paso a paso por proveedor está en el documento «Calendarios — Paso a
paso para conectar Google, Microsoft y Zoho».

Permisos que se piden, y ni uno más:

```
Google      openid email · calendar.events · calendar.calendarlist.readonly
Microsoft   openid email profile offline_access · Calendars.ReadWrite
Zoho        ZohoCalendar.calendar.READ · ZohoCalendar.event.ALL · email profile
```

No se pide `calendar` a secas en Google: ése además deja crear y borrar
calendarios enteros y no se usa. Un permiso que no se usa solo sirve para
que la pantalla de consentimiento dé más miedo del necesario.

### 5.3 Apple — lo que no existe

Apple **no tiene OAuth de calendario**. «Iniciar sesión con Apple» sirve
para identificarse, no da acceso a la agenda. La vía real es la dirección
`.ics` pública (5.1).

## 6. Escribir en el calendario de fuera

Elegir un calendario conectado como destino crea el evento **allá**: es
lo que hace que la hora quede ocupada para quien mire esa agenda desde su
móvil, que es justo para lo que se conectó la cuenta.

- Editarlo aquí lo cambia allá, en vez de crear un segundo.
- Al releer, la copia que viene de fuera se descarta (lleva nuestra
  marca), así que no aparece por duplicado.
- Borrarlo aquí lo borra allá; moverlo a otro calendario lo quita del de
  origen. Sin eso, moverlo a la agenda de Acten lo dejaría ocupando esa
  hora en Google para siempre.

**Quién puede escribir lo dice el proveedor, no nosotros:** el
`accessRole` de Google, el `canEdit` de Microsoft. Un calendario donde la
persona solo lee se marca de solo lectura aunque nuestro permiso diga
otra cosa, porque la que no puede es ella.

Si el proveedor rechaza el evento, no se pierde nada aquí **pero tampoco
se traga**: el motivo queda en `external_error` y se enseña. Un evento
que aquí se ve normal y allá no existe manda a la gente a una hora que
para el resto está libre.

## 7. Lo que falta, dicho

- **De fuera hacia dentro no hay conflicto resuelto.** Si alguien cambia
  en Google un evento que salió de aquí, la próxima lectura lo descarta.
  Se descarta a propósito —es la única forma de no duplicarlo— pero
  significa que el lado que manda es este. Decidir qué gana cuando los
  dos cambian es una decisión de negocio, no de código.
- **Sondeo, no avisos.** Google y Graph ofrecen webhooks que evitarían
  preguntar cada media hora; piden una URL pública verificada y renovar
  la suscripción cada pocos días. Con este volumen, sondear cuesta menos.
- **Zoho no se ha ejercitado contra una cuenta real.** El flujo está
  escrito contra su documentación y probado con sus formatos de fecha y
  su troceado de 31 días. El botón «Comprobar credenciales» dice si Zoho
  reconoce la aplicación; la forma exacta de los datos al listar
  calendarios y eventos solo se confirma conectando una cuenta. Mientras
  tanto, para Zoho el camino sin riesgo es su dirección `.ics`.
- **No se invita por correo a los asistentes** desde el calendario de
  fuera: los avisos los manda Acten. Añadirlos haría que Google y Outlook
  mandaran su propia invitación y llegarían dos.

## 8. Lo que costó averiguar

Esto no está en la documentación de nadie.

**De los proveedores**

- Una app de Google creada como *Externa* **nace en modo Prueba**: solo
  entran las cuentas listadas en «Usuarios de prueba». Cualquier otra ve
  «Acceso bloqueado — no completó el proceso de verificación», un 403 que
  se lee como fallo de configuración y no lo es. Elegir *Interno* para
  evitarlo solo sirve si TODAS las cuentas son del Workspace.
- **Microsoft rota el `refresh_token` en cada renovación.** Si no se
  guarda el nuevo, la conexión se cae sola a los pocos días y sin síntoma
  previo.
- **Microsoft no tiene endpoint para revocar.** Al desconectar hay que
  decir dónde se retira el permiso (`myaccount.microsoft.com`) o se deja
  a la persona creyendo que ya no tienes acceso.
- `offline_access` **a secas no es válido** en Microsoft: tiene que ir
  con `openid` o con un recurso, o contesta `AADSTS70011` quejándose del
  permiso antes de mirar las credenciales.
- Se lee con **`calendarView`, no con `events`**: `calendarView` despliega
  las repeticiones. Con `events`, una reunión semanal sale una sola vez.
- **Zoho vive en centros de datos separados** y los tokens de uno no
  valen en otro. Dice en el retorno dónde vive la persona
  (`accounts-server=`) y exige canjear el código ahí.
- **Zoho rechaza rangos de más de 31 días**: devuelve error, no una lista
  recortada. Traer un año son doce consultas.
- **El correo de la cuenta no siempre llega solo.** Sin `openid` la
  cuenta se queda anónima y dos cuentas de la misma persona no se
  distinguen. Si se conectó sin ese permiso no se arregla solo: hay que
  reconectar, y eso se dice en pantalla.

**De diseño**

- **Comprobar las credenciales sin conectar nada:** se pide un token con
  un permiso deliberadamente inválido. Si el proveedor se queja del
  PERMISO, la aplicación está bien; si se queja de la APLICACIÓN, las
  credenciales están mal.
- **`select_account` + `consent` al entrar.** Sin lo primero, los
  proveedores reutilizan en silencio la sesión abierta y la segunda
  cuenta de una persona no se puede conectar nunca. Sin lo segundo, una
  reconexión no devuelve `refresh_token`.
- **El `state` firmado lleva a dónde volver**, y al volver se comprueba
  que sea un camino interno: un destino que se acepta tal como llega es
  una redirección abierta.
- **Validar dominios por «empieza por» no vale.** `accounts.zoho.evil.com`
  empieza por `accounts.zoho.`. Lista exacta, más aún donde se manda el
  secreto de la aplicación.
- **En el navegador, un evento de día entero se corre.**
  `2026-08-07T00:00:00+00:00` pasado a hora local en Bogotá es el 6 a las
  19:00: los festivos salían todos un día antes. Y al revés, mandar la
  hora tecleada con una «Z» detrás la declara UTC y la corre cinco horas.

## 9. Archivos

```
backend/services/calendars.py           quién ve qué, permisos, la lista, la agenda
backend/services/calendar_providers.py  la única tabla que sabe cuál es cuál
backend/services/calendar_ics.py        leer .ics con seguridad + repeticiones
backend/services/calendar_google.py     OAuth, lectura y escritura
backend/services/calendar_microsoft.py  lo mismo contra Graph
backend/services/calendar_zoho.py       lo mismo contra Zoho (31 días + centro)
backend/services/calendar_sync.py       traer de fuera y refrescar tokens
backend/services/calendar_write.py      crear, cambiar, mover y borrar allá
backend/services/calendar_check.py      comprobar credenciales sin conectar
backend/services/calendar_config.py     dónde viven las credenciales
backend/services/calendar_crypto.py     Fernet
backend/services/festivos_co.py         Ley Emiliani
backend/routers/calendars.py            el contrato ejecutable de la sección 4
frontend/src/app/services/calendars.service.ts
frontend/src/app/components/calendar/
```

Los tres proveedores viven en archivos separados a propósito. Lo que
cambia entre ellos no son tres URLs: son las formas de los datos —Graph
llama `subject` al título y mete las fechas en un objeto con la zona
aparte; Zoho manda las suyas como texto `yyyyMMddTHHmmssZ`—, y unificarlos
produce el típico módulo donde cada línea empieza con un `if`.
`calendar_providers.py` es lo único que hay que tocar para añadir un
cuarto.
