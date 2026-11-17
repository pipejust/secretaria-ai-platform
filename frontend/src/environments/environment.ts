export const environment = {
    production: false,
    apiUrl: 'http://localhost:8000', // Local backend
    // Cloudflare Turnstile site key (público). En dev se puede dejar vacío:
    // el componente usa la key oficial de prueba de Cloudflare (siempre pasa).
    turnstileSiteKey: ''
};
