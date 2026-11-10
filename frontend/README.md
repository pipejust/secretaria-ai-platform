# Frontend de Acten

Angular 21, TypeScript y Vitest. Requiere Node `^20.19.0`, `^22.12.0` o `>=24.0.0`.

```bash
npm ci
npm start
npm test -- --watch=false
npm run build -- --configuration production
```

La UI de desarrollo abre en `http://localhost:4200` y consulta `http://localhost:8000`.
El build directo de producción usa `https://api.acten.app`. Para otra instalación,
construir con el Dockerfile y su argumento de compilación:

```bash
docker build --build-arg NG_APP_API_URL=https://api.tudominio.com -t acten-frontend .
```

La URL queda incorporada al bundle; una variable runtime de Nginx no la cambia.
Nginx sirve el SPA con fallback a `index.html`, conserva los encabezados de seguridad
en las rutas que tienen caché propia y permite el micrófono al mismo origen.
La política no concede permisos del navegador: la persona debe autorizar la grabación.

El resultado está en `dist/frontend/browser`. No hay un framework e2e configurado;
las pruebas disponibles son unitarias con Vitest.
