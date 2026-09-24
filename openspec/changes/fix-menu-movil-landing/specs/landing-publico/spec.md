# Spec Delta

## Purpose

El sitio público de Acten (acten.app): la página que ve alguien que todavía no es
usuario. Cubre su navegación —el encabezado, el menú y las llamadas a la acción— en
teléfonos, tabletas y escritorio, sin requerir sesión.

## ADDED Requirements

### Requirement: Navegación alcanzable en cualquier ancho de pantalla

El landing público SHALL ofrecer acceso a toda su navegación en cualquier ancho
soportado, desde 320 px hasta escritorio. Ningún control del encabezado —el botón
del menú, el selector de idioma o las llamadas a la acción— PUEDE quedar fuera del
área visible ni recortado, porque la página no ofrece desplazamiento horizontal.

Por debajo de 768 px la navegación se concentra en el botón del menú, que abre un
cajón con los enlaces de secciones y las dos llamadas a la acción ("Iniciar sesión"
y "Solicitar demo"). El encabezado conserva únicamente el logo, el selector de
idioma y ese botón.

#### Scenario: El menú se abre en un teléfono
- **WHEN** alguien abre el landing con un ancho de 375 px y toca el botón del menú
- **THEN** el botón se ve completo dentro de la pantalla y el cajón se abre mostrando
  las secciones, "Iniciar sesión" y "Solicitar demo"

#### Scenario: El ancho mínimo soportado sigue siendo usable
- **WHEN** alguien abre el landing con un ancho de 320 px
- **THEN** el borde derecho del botón del menú queda dentro de los 320 px y ningún
  control del encabezado aparece cortado

#### Scenario: El teléfono grande se comporta igual que el pequeño
- **WHEN** alguien abre el landing con un ancho de 430 px
- **THEN** el botón del menú queda dentro de la pantalla y la fila del encabezado no
  desborda su contenedor

#### Scenario: El límite entre móvil y escritorio no muestra las dos navegaciones
- **WHEN** alguien abre el landing con un ancho de 768 px
- **THEN** se ve el botón del menú y no se ve la barra de enlaces de escritorio, sin
  que ambas convivan

#### Scenario: En escritorio la navegación no cambia
- **WHEN** alguien abre el landing con un ancho de 1280 px
- **THEN** ve la barra de enlaces, "Iniciar sesión" y "Solicitar demo" en el
  encabezado, y no ve el botón del menú

#### Scenario: La página nunca se desplaza en horizontal
- **WHEN** alguien abre el landing en cualquier ancho entre 320 px y 1440 px
- **THEN** el ancho del documento no supera el ancho de la ventana

### Requirement: Las llamadas a la acción siguen disponibles en móvil

Al concentrar la navegación en el cajón, el landing SHALL mantener accesibles en
móvil las dos llamadas a la acción, sin perder ninguna: "Solicitar demo" lleva a la
sección de contacto y "Iniciar sesión" al acceso de la aplicación.

#### Scenario: Solicitar demo sigue funcionando desde el cajón
- **WHEN** alguien con un ancho de 375 px abre el menú y toca "Solicitar demo"
- **THEN** el cajón se cierra y la página se desplaza hasta la sección de contacto

#### Scenario: Iniciar sesión sigue funcionando desde el cajón
- **WHEN** alguien con un ancho de 375 px abre el menú y toca "Iniciar sesión"
- **THEN** llega a la pantalla de acceso de la aplicación
