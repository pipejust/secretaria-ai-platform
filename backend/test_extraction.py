import asyncio
import json
from services.groq_service import GroqService

transcript_test = """
Raul Andres Gutierrez Villarraga: Lo que pasa es que hay algunos tipos de certificado.
Lady Edith Ardila Ramirez: ¿Quién tiene algún micrófono por allá con otros sonidos?
Lady Edith Ardila Ramirez: Bueno, Raúl, ¿Me escuch?
Lady Edith Ardila Ramirez: Listo, pues no ha subido Tatiana, pero empecemos nosotras.
Lady Edith Ardila Ramirez: Ah, bueno, ya llegó don Haría.
Lady Edith Ardila Ramirez: Buen día Don Harold,
Harold Garzón 1: buen día Lady, buen día a todos.
Harold Garzón 1: ¿Cómo están?
Lady Edith Ardila Ramirez: ¿Bien?
Lady Edith Ardila Ramirez: Sí señor, muchas gracias.
Lady Edith Ardila Ramirez: Todo muy bien.
Lady Edith Ardila Ramirez: Entonces, ahora sí Raúl, entonces le presento acá dos compañeros que están apoyando, bueno, principalmente Cristian es el que está apoyando, Angie, Tatiana, con los temas de la app móvil, ¿Cierto?
Lady Edith Ardila Ramirez: Abrimos este espacio precisamente para que revisáramos los temas que hay pendientes de los requerimientos de desarrollo sobre la móvil y digamos que en este caso algunos que nos están generando como bloqueantes también para poder avanzar en los temas que hoy están estancados en pruebas o lo que entonces le contamos Raúl, nosotros hace algunos días, algunos, algún tiempo atrás estuvimos con usted revisando los requerimientos que hoy tenemos pendientes de la certificación funcional, que básicamente era lo que correspondía al tema del banner informativo que fuera administrable y al requerimiento que estábamos validando toda la funcionalidad de la móvil por la implementación de mejoras de refacción en asociación a todo el tema de consumo de servicios.
Lady Edith Ardila Ramirez: Entonces, ¿Qué pasó ahí?
Lady Edith Ardila Ramirez: Entiendo que con usted Raúl, terminamos todo el tema de pruebas funcionales y nos quedamos como pendientes de la certificación funcional.
Lady Edith Ardila Ramirez: Pasó en este tiempo mientras usted estaba, digamos, completando esa tarea, revisando internamente con Juan Sebastián, digamos que estábamos haciendo una tarea adicional y era validar como tal la integridad.
Lady Edith Ardila Ramirez: Juan Sebastián tenía algunas, digamos que algunas preocupaciones frente a la versión que nos había entregado Nexura, teniendo en cuenta que con este refactoring, no sé si recuerda, pero ya llevamos un buen tiempo y hemos desplegado varias versiones.
Lady Edith Ardila Ramirez: Entonces, ¿Qué hicimos o que se hizo con Juan que venimos apoyando acá también con el equipo?
Lady Edith Ardila Ramirez: Se hizo, se tomó un backup de la base de producción, de la base de datos de producción, se restauró en el ambiente de pruebas, se ejecutó completamente la catalogación de la versión, digamos que ahí se revisó efectivamente que estuviera completamente la instalación de todos los objetos y componentes, se hicieron algunas reconfiguraciones y finalmente, posterior a esa tarea, lo que Nexura nos estaba apoyando, digamos que le solicitamos aquí apoyo, era hacer una validación completa también nuevamente de la aplicación con base en lo que habíamos validado con usted en su momento.
Lady Edith Ardila Ramirez: Entonces trámites por la parte del administrador, que digamos que esa parte no recuerdo si la alcanzamos a validar en las en las sesiones, pero digamos que acá se hizo la tarea de hacer la validación con el administrador y ya se chequeó efectivamente que todos los trámites están funcionando correctamente.
Lady Edith Ardila Ramirez: Ahora, de esas validaciones que hicimos en ese alcance acá con el equipo de Nexura para lo de la validación de la versión, solo nos quedó un tema digamos por fuera que es el que le estaba pidiendo ayuda y no hemos podido o no pudimos hacer la validación de lo que corresponde a registro de los usuarios dentro de la móvil, o sea lo que es registro de usuarios dentro del administrador chuleado, pero los registros de usuarios dentro de la móvil, ese no lo pudimos completar porque pues ahí hay una dependencia de cara que es un registro en Certicámara para completar esa validación.
Lady Edith Ardila Ramirez: Digamos que esa es la actividad que hicimos sobre esa versión.
Lady Edith Ardila Ramirez: Y en ese entendido que le estamos contando lo que nosotros estamos trabajando, quisiéramos preguntarle respecto a esa certificación que nos hace falta de parte funcionalmente qué le hace falta a usted o qué requiere usted de nosotros para terminar esa por
Christian Muñoz: ejemplo el de
Raul Andres Gutierrez Villarraga: viernes o el jueves, ella me explicó lo que me estaba diciendo, pero son dos cosas, incluso le recordé a ella que habíamos dejado que el registro iba a ser para que tomaran apuntes y lo enviaran como una mejora de porque si estaba fallando en
Raul Andres Gutierrez Villarraga: producción, sí es un pasaporte, eso se habló en la u sesión que tuvimos
Raul Andres Gutierrez Villarraga: del tema de de las pruebas.
Raul Andres Gutierrez Villarraga: Ahora bien Tatiana, también de acuerdo a lo que tocaría volver a hacer por todo lo que ya hicimos, básicamente volver a lo mismo.
Lady Edith Ardila Ramirez: Raúl realmente lo dejamos a su consideración,
Lady Edith Ardila Ramirez: o sea, sí podemos hacer una sesión
Lady Edith Ardila Ramirez: si quiere y hacemos un recorrido, digamos que le comentaba que para tranquilidad de todos aquí con Exura ya hicimos la
Lady Edith Ardila Ramirez: tarea y recorrimos lo mismo, las mismas funcionalidades que hicimos con usted, de hecho
Lady Edith Ardila Ramirez: dejamos algunas sesiones grabadas, pero si le parece que abramos un espacio y hagamos un recorrido lo podemos hacer.
Lady Edith Ardila Ramirez: Entonces sería que coordináramos
Raul Andres Gutierrez Villarraga: fácil, envíenme un documento con los print screen de que está funcionando, ya con eso nos vamos.
Lady Edith Ardila Ramirez: Listo.
Raul Andres Gutierrez Villarraga: De las funcionalidades que hicimos recorrido, yo lo reviso y sobre eso digamos que quedaría subsanado eso para no depender de mi tiempo, un documento, un PDF con las pantallas de funcionalidad, certificados, pantalla, puso esta cédula y sacó el certificado así y ya con ese documento ya quedaría como soporte para no depender de mí y no alargar las pruebas.
Raul Andres Gutierrez Villarraga: Y pues si ustedes ya hicieron eso y pues me envían el.
Raul Andres Gutierrez Villarraga: Con eso yo tranquilo.
Lady Edith Ardila Ramirez: Listo, entonces Cristian, ¿Cuándo le podemos entregar esas evidencias a Raúl?
Christian Muñoz: ¿Tendría que yo confirmar aquí con Andrés cuánto tiempo le podría tomar eso?
Christian Muñoz: ¿Cuánto César me puede ayudar César para revisar toda la aplicación, toda la funcionalidad, que todo esté bien y documentarlo, ¿Cuánto crees tú que podría, cuánto estimas que podría tomar eso?
Lady Edith Ardila Ramirez: Pero digamos de las pruebas que hicimos la semana pasada no dejamos evidencias, toca volverlo a hacer.
Christian Muñoz: Correcto, si tenemos la grabación y capturas podemos hacer una revisión, sin embargo, pues yo creo que porque digamos que habían propósitos diferentes, si bien podríamos usar eso, podría digamos pedirle otra vez a ellos que revisaran bien y una vez más se encargan de que todo estuviera perfecto.
Lady Edith Ardila Ramirez: Listo, entonces usted me confirma si le parece durante el día de hoy cuándo le podemos entregar esa información a Raúl, pues teniendo en cuenta lo que usted considere, si lo vuelven a ejecutar o si tenemos de los soportes que ya ejecutamos la semana pasada.
Christian Muñoz: Listo, correcto, sí, voy a confirmar y entonces te dejo saber en el día.
Lady Edith Ardila Ramirez: Listo, esa es la primera parte.
Lady Edith Ardila Ramirez: Entonces teniendo en cuenta la información o
Lady Edith Ardila Ramirez: lo que nos confirma acá Cristian con
Lady Edith Ardila Ramirez: el equipo de Nexura, digamos que podemos en estas condiciones entregarle el documento evidencias y que usted nos ayude a documentar lo que corresponde a la LM y a generar el documento de certificación.
Lady Edith Ardila Ramirez: Raúl entonces en esas condiciones de lo que tenemos el estado, lo que tenemos hoy en CUA es netamente refactoring.
Lady Edith Ardila Ramirez: Ahora después de esa versión, Nexura tiene cinco versiones adicionales que ya entregó desarrolladas ahora en el febrero, pero pues como estamos haciendo esta labor con José Bastián, no las hemos desplegado en QA.
Lady Edith Ardila Ramirez: Entonces para pruebas, una vez completemos este proceso de certificar lo que ya tenemos por cerrar, entonces tenemos que arrancar con pruebas.
Lady Edith Ardila Ramirez: Inicialmente Acá tengo relacionado dos requerimientos que trabajaríamos con usted Raúl, que sería lo que está documentado en el PM 12873,
Lady Edith Ardila Ramirez: que es un tema de Que es
Lady Edith Ardila Ramirez: el tema este de que nosotros conocemos, no sé si usted lo recuerda, como degradado de servicios, y fue a raíz de una indisponibilidad que hubo el año pasado con el tema de nómina y la aplicación móvil quedó indisponible cuando se generó algún en ese servicio.
Lady Edith Ardila Ramirez: Y digamos, lo que buscábamos era que controlar los temas de mantenimiento.
Lady Edith Ardila Ramirez: Es decir, que si en algún momento,
Lady Edith Ardila Ramirez: como Semana Santa o como lo que pasa en fin de año, como lo que pasó el año pasado para mesa 14, se genera indisponibilidad del servicio, pudiéramos dejar o podamos dejar un mensaje de indisponibilidad a los ciudadanos y no que les esté generando el error de que no pueden acceder simplemente.
Lady Edith Ardila Ramirez: Simplemente a los ciudadanos es básicamente generarles el mensaje por temas de mantenimiento.
Lady Edith Ardila Ramirez: Entonces ese lo tenemos para instalar una vez tengamos la certificación y para empezar a coordinar pruebas con usted.
Lady Edith Ardila Ramirez: Lo mismo con usted.
Lady Edith Ardila Ramirez: Segundo.
Lady Edith Ardila Ramirez: El segundo desarrollo que ya está listo, el de consulta, documento, respuesta, creo que lo tiene presente, que fue uno de los casos que nos envió el año pasado, octubre noviembre, de los casos del PPM 33812.
Lady Edith Ardila Ramirez: El de consulta, documento, respuesta también estaría para instalar una vez tengamos la certificación y para empezar a aprobar.
Lady Edith Ardila Ramirez: Y los tres adicionales para completar los cinco son los trámites de BEPS.
Lady Edith Ardila Ramirez: Entonces está el de uno de vinculación, uno de viabilidad y uno de estados de cuenta, que también necesitamos avanzar con las pruebas.
Lady Edith Ardila Ramirez: En ese caso, los DEB ya los estamos trabajando con Dianita Barcas, ella ya tiene lista los planes de pruebas y la documentación NLM, pues estamos pendientes de cerrar este tema para producción, para poderle instalar esas versiones y que ella arranque a documentar.
Lady Edith Ardila Ramirez: Entonces, los que validaremos con usted sería esos dos, el de consulta documento de respuesta y el de la.
Lady Edith Ardila Ramirez: Y el de la notificación de cuando haya indisponibilidad de los servicios.
Lady Edith Ardila Ramirez: De acuerdo.
Lady Edith Ardila Ramirez: Y los validaríamos una vez tengamos esa certificación que usted no entrega.
Lady Edith Ardila Ramirez: Podemos pasar a Cuba esos dos.
Lady Edith Ardila Ramirez: Y por último, lo que teníamos en nuestra lista, Raúl, era del mismo PPM 33812, el tema de doble asesoría que estaba pendiente para definir ahora en febrero si ya teníamos los servicios y que el equipo de móvil empezara a implementar entonces queríamos preguntarle si en ese frente de doble asesoría podríamos ya avanzar en esa definición o en esa implementación, o usted qué novedad nos tiene ese frente.
Raul Andres Gutierrez Villarraga: No, ese todavía está demorado.
Raul Andres Gutierrez Villarraga: Fabra contrató nada.
Raul Andres Gutierrez Villarraga: Hacerle un otro sí al contrato de visa allí para hacer unos desarrollos.
Raul Andres Gutierrez Villarraga: Entonces se quedaría quieto todavía no podremos.
Lady Edith Ardila Ramirez: Ahí pendiente.
Lady Edith Ardila Ramirez: Entonces en ese orden de ideas le confirmaría ahora más tarde.
Lady Edith Ardila Ramirez: La grabación de lo que validamos para que usted nos ayude a documentar la certificación.
Lady Edith Ardila Ramirez: ¿Usted más o menos cuánto tiempo se demora con esa documentación en ALM como para coordinar ese despliegue?
Raul Andres Gutierrez Villarraga: No, pues sería que me la entregaran dos días después del máximo.
Raul Andres Gutierrez Villarraga: Sí, listo, listo.
Lady Edith Ardila Ramirez: Entonces ya coordinaríamos acá con Exura lo de las evidencias.
Lady Edith Ardila Ramirez: No sé si usted tiene en su.
Lady Edith Ardila Ramirez: Tengamos pendiente con ustedes lo que tiene que ver con desarrollos de la app.
Raul Andres Gutierrez Villarraga: No, por ahora no.
Lady Edith Ardila Ramirez: Listo, listo.
Lady Edith Ardila Ramirez: Eso era lo que teníamos, entonces fue más sencillo de lo que podemos.
Lady Edith Ardila Ramirez: De lo que pensamos.
Lady Edith Ardila Ramirez: Entonces Angiecita, Harold y Cristian, quedamos atentos para que en el transcurso de hoy nos puedan indicar en qué momento tenemos esa documentación, entregársela a Raúl y cerrar la certificación de estos dos primeros, que es lo que nos.
Lady Edith Ardila Ramirez: Lo que nos.
Lady Edith Ardila Ramirez: Lo que nos debe priorizar en este momento.
TATIANA ARANGO: OK, déjame quedar el compromiso.
TATIANA ARANGO: ¿Te enviamos la documentación con las pruebas que se realizaron, Cierto?
Lady Edith Ardila Ramirez: Que le documentemos en un PDF, en un Word, todas las evidencias de las pruebas.
Lady Edith Ardila Ramirez: Lo que hicimos la semana pasada no es trámite.
Lady Edith Ardila Ramirez: Con qué cédula se consultó cuál fue el resultado, La validación que hicimos tanto de lo de la.
Lady Edith Ardila Ramirez: Como del administrador.
Lady Edith Ardila Ramirez: OK, eso se lo entregamos a Raúl, él nos decía también si tenemos las grabaciones.
Lady Edith Ardila Ramirez: Yo tengo la grabación del viernes, no sé si tú tienes la de la anterior.
Lady Edith Ardila Ramirez: ¿Tú tienes una grabación anterior?
TATIANA ARANGO: Yo tengo varias, pero eso hacía parte del proceso de certificación de refactor en su momento.
Lady Edith Ardila Ramirez: No, de las de la semana pasada.
TATIANA ARANGO: No, no la tengo.
Lady Edith Ardila Ramirez: Listo, entonces yo le entrego la que validamos el viernes y ustedes documentan todas las evidencias.
Lady Edith Ardila Ramirez: Vale, Ojalá sea rápidamente y ya Raúl en dos días nos ayuda a validar y a documentar en ALM para generar y generar la certeza.
Christian Muñoz: Ya me confirmaron por interno que para mañana podríamos tener listo los documentos.
Christian Muñoz: El documento.
Lady Edith Ardila Ramirez: Listo, perfecto.
Lady Edith Ardila Ramirez: Mañana se lo entregamos a Raúl, adelante.
Harold Garzón 1: Y es que no me quedó claro el tema de los usuarios que
Lady Edith Ardila Ramirez: se
Harold Garzón 1: estaban gestionando con Raúl quedamos con eso.
Lady Edith Ardila Ramirez: Lo que pasa es que la opción de registro, la opción de registro, pues
Lady Edith Ardila Ramirez: como precisamente nos faltaba esa partecita, esa partecita a hoy lo que le entiendo a Raúl y que ya habían validado con Angie, es que esa parte no está funcionando en esta versión de producción.
Lady Edith Ardila Ramirez: Entonces hay que hacerle un ajuste, muy
Lady Edith Ardila Ramirez: seguramente en la siguiente versión ya debe venir ese ajuste.
Lady Edith Ardila Ramirez: Bueno, lo miramos ya en las siguientes pruebas si hay que hacer algún ajuste adicional.
Lady Edith Ardila Ramirez: Entonces, por ahora, por ahora lo vamos
Lady Edith Ardila Ramirez: a cerrar sin esa parte de registro de usuarios.
Lady Edith Ardila Ramirez: Ahora, de lo que viene, vamos a
Lady Edith Ardila Ramirez: eso lo vamos a revisar en el siguiente seguimiento de los temas que quedan pendientes, que por ahí no salieron otras
Lady Edith Ardila Ramirez: cositas, pero eso ya fue en este
Lady Edith Ardila Ramirez: tema técnico, que el nombre de la base de datos lo cambiaron, revisaron por.
Lady Edith Ardila Ramirez: Salieron cositas, identificaron, pero la idea es que estos temas que vengan o que nos queden, los pudiéramos completar en la versión de degradado, esas otras cositas que
Lady Edith Ardila Ramirez: han encontrado por ahí, pero ya las
Lady Edith Ardila Ramirez: miramos a detalle en el siguiente seguimiento.
Harold Garzón 1: Bien, gracias.
Lady Edith Ardila Ramirez: Porque no tengo todavía tampoco esa estimación,
Lady Edith Ardila Ramirez: por ahí está dando vueltas.
Lady Edith Ardila Ramirez: Listo, muchas gracias Raúl, muchas gracias equipo.
Lady Edith Ardila Ramirez: Y quedamos atentos entonces, Cristian, al documento de mañana.
Lady Edith Ardila Ramirez: Angiecita, se lo entregamos a Raúl y tendremos esta semana la certificación.
Raul Andres Gutierrez Villarraga: Listo, muchas gracias.
Raul Andres Gutierrez Villarraga: Bueno, que estén bien.
"""

async def run_test():
    svc = GroqService()
    try:
        res = await svc.process_transcript(transcript_test)
        
        # Test unwrapping feature built earlier
        def _unwrap(val):
            if isinstance(val, dict):
                if "value" in val: return val["value"]
                if "items" in val: return val["items"]
            return val
            
        print("====== SUMMARY ======")
        print(_unwrap(res.get("summary")))
        print("\n====== ACTION ITEMS ======")
        action_items = res.get("action_items", [])
        if not action_items or len(action_items) == 0:
            print("ERROR: Groq returned empty action items!")
            print(json.dumps(res, indent=2))
        else:
            print(f"SUCCESS! Found {len(action_items)} action items:")
            for item in action_items:
                print(f" - [{item.get('owner_name')}] {item.get('title')}: {item.get('description')}")
                
    except Exception as e:
        print(e)
        import traceback
        traceback.print_exc()
        
if __name__ == "__main__":
    asyncio.run(run_test())
