import asyncio
from services.groq_service import GroqService

transcript = """Felipe Cortés: ¿Me escuchas?
Felipe Cortés: ¿Aló?
Felipe Cortés: ¿Ahorita sí te veo OK, me cerré y abrí por si algo, Listo, esta
Felipe Cortés: sesión va a durar muy poco para
Felipe Cortés: que veas más o menos cómo funciona y luego volvemos a entrar a otra
Felipe Cortés: porque lo que te voy a mostrar aquí es la plataforma de inteligencia artificial para reuniones
Felipe Cortés: entonces te voy a mostrar pero aquí grabamos pantalla también?
Felipe Cortés: No pues solamente para que la conozcas y me gustaría.
Felipe Cortés: ¿Bueno si querés grabamos pantalla para que puedas ver, para que luego lo puedas recrear
Felipe Cortés: Arranca grabar?
Felipe Cortés: Ok, listo, listo, ¿Es esta plataforma, se
Felipe Cortés: llama Secretaria y por ahí hay que
Felipe Cortés: buscar el nombre, está en un servidor, esta está en un servidor free, o sea que cuando vos inicias sesión posiblemente
Felipe Cortés: va a tardar la primera vez posiblemente mientras el servidor se reactiva, Eso pasa en un minuto, después de que pasan 50 segundos sin activación ella se va
Felipe Cortés: a volver apagado pero entonces por ejemplo
Felipe Cortés: aquí nosotros tenemos que esperar 50 segundos
Felipe Cortés: o un minuto para que se reactive
Felipe Cortés: en ese momento ya el login va a ser más rápido, va a seguir
Felipe Cortés: probando hasta que ya lo coja de
Felipe Cortés: una,
Felipe Cortés: esperemos un momento, ya luego para que funcione de una pues me toca
Felipe Cortés: apagar para el servidor, pero por ahora este está aquí, voy actualizando ya en
Felipe Cortés: cualquier momento debería iniciar sesión de un Entonces qué hace la plataforma?
Felipe Cortés: La plataforma
Felipe Cortés: utiliza Fireflies, saca toda la
Felipe Cortés: información de la transcripción de Fireflies y genera otra transcripción, un summary, genera unas tareas, genera una.
Felipe Cortés: ¿Cómo se llama?
Felipe Cortés: Una.
Felipe Cortés: Si es que esto subió mal, está apuntando a local, pero bueno ponemos local
Felipe Cortés: para que lo puedas ver funcionando por aquí tenía la versión local, creo que aquí está, mira es este, voy a cerrar sesión para que lo veas, actualizo y listo, inicio sesión Entonces mira, aquí
Felipe Cortés: van a salir todas las sesiones que se han hecho, mira, además cogió esta y cogió esta que vos hiciste,
Felipe Cortés: hay
Felipe Cortés: subida manual de una transcripción, tú subes un audio y te genera la transcripción entonces uno puede crear aquí proyectos, yo
Felipe Cortés: creé este proyecto colpensiones donde tiene un
Felipe Cortés: nombre en la descripción y aparte de eso tiene unos contactos, en este caso está este te va a crear la voz, por ejemplo.
Felipe Cortés: Yo te tengo como scrum, Cualquier cosa
Felipe Cortés: más 57, ¿Cuál es tu número?
Felipe Cortés: 305-488-1346 arriba dices correo dice que
Felipe Cortés: dice.
Felipe Cortés: Y tu correo es.
Felipe Cortés: Guardamos los cambios y aparte hay unas rutas, las rutas son de las plataformas, están conectadas Trilogy a ClickUp y Azure y cada una tiene su propio proceso.
Felipe Cortés: Él te explica, si yo voy aquí, él te dice dónde encontrar los datos y demás para otros proyectos.
Felipe Cortés: Listo, ahora vamos a la planilla.
Felipe Cortés: Yo a la planilla le subo un archivo de Word de fondo, en este
Felipe Cortés: caso tiene un archivo de Word, le
Felipe Cortés: pongo unas variables de cómo quiero que se vean las cosas y unos colores.
Felipe Cortés: Si tú eres este payasado de colores ya vas a entender por qué.
Felipe Cortés: Era para probar.
Felipe Cortés: Bueno, ya si, unos usuarios, unos roles.
Felipe Cortés: Y aquí está la configuración del Firefly, del resell, que es el que envía los correos y de los otros.
Felipe Cortés: Ah, bueno, esto es para que si después de un minuto, no, de una hora, nadie hace nada, él automáticamente envía todos los correos y tareas.
Felipe Cortés: Azure, Trello, Atlas, Jira y ClickUp.
Felipe Cortés: Ahora vamos a lo carnudo.
Felipe Cortés: Entonces aquí está la lista de reuniones, por ejemplo, la que tuviste hoy.
Felipe Cortés: Yo voy aquí al detalle de la
Felipe Cortés: sesión y el detalle de la sesión,
Felipe Cortés: él ya me detecta de una ¿Que proyectos?
Felipe Cortés: Colpensiones, porque hablan de Colpensiones.
Felipe Cortés: Coge la transcripción
Felipe Cortés: que hace
Felipe Cortés: Fireflies y me genera un resumen ejecutivo diferente al de Fireflies, unas decisiones clave, unos riesgos
Felipe Cortés: identificados, unos acuerdos y me genera unas tareas.
Felipe Cortés: Tres horas después, maricón, me genera unas tareas con nombre propio.
Felipe Cortés: Pues no está el correo todavía configurado.
Felipe Cortés: No está el correo, él lo intuye y este es el único usuario que había.
Felipe Cortés: Entonces él lo intuye y por ejemplo, en el caso de esta, yo aquí
Felipe Cortés: le puedo decir, selecciono este usuario o esta tarea sola o la selecciono todas
Felipe Cortés: y puedo enviar correos y enviar a la plataforma correas.
Felipe Cortés: Es que solo llega el correo con información y la plataforma pues que ya sube las tareas.
Felipe Cortés: Y aquí en las sesiones, bueno, allá también se puede.
Felipe Cortés: Yo puedo aquí generar un documento, el documento de esa sesión, que es como
Felipe Cortés: el acta de la reunión.
Felipe Cortés: Y como puedes ver, obviamente es un
Felipe Cortés: tema, estilos y otras cosas.
Felipe Cortés: Tiene el archivo de fondo que estaba, los datos del acta de la reunión,
Felipe Cortés: los asistentes, estuvo Harold,
Felipe Cortés: reunión ejecutiva, temas y puntos de discusión, riesgo científico, decisiones
Felipe Cortés: clave, acuerdos y las tareas tuviste los
Felipe Cortés: colores que le puse era para poder distal, o sea, tener la diferencia entre cada cosa.
Felipe Cortés: Son con fechas y todo, Mira, entonces esas tareas se pueden poner con fechas
Felipe Cortés: en las plataformas y también envían a los correos, a los correos se envía con un segundo,
Felipe Cortés: Se envía para añadir
Felipe Cortés: al calendario la fecha, los datos de tu tarea, la fecha límite, el resumen ejecutivo, la decisión es clave, riesgos y un PDF con esa información.
Felipe Cortés: Entonces, en este momento se está grabando uno.
Felipe Cortés: Entonces la idea es que podamos poner rápido unas tareas.
Felipe Cortés: Dame un segundo, ¿Donde está la sesión que como es web, se pierde?
Felipe Cortés: Aquí está.
Felipe Cortés: Entonces, este sería para el proyecto de Colpensiones.
Felipe Cortés: Entonces yo, Felipe Cortés, por ejemplo, tengo
Felipe Cortés: que enviar un correo el 22 de marzo, un correo con la información de
Felipe Cortés: esta sesión, y cristian el 23 tiene
Felipe Cortés: que constatar que todo esté correcto, reenviarme
Felipe Cortés: un correo y enviarme unas tareas.
Felipe Cortés: Entonces, esas serían las tareas que se van a hacer y con eso finalizamos la sesión.
Felipe Cortés: Entonces voy a cerrar la sesión y ya te envío otro enlace.
Felipe Cortés: Dale.
Felipe Cortés: Y bajar de grabar también."""

async def main():
    service = GroqService()
    result = await service.process_transcript(transcript)
    import json
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
