/** Integración mínima con el widget de Wompi (https://checkout.wompi.co/widget.js). */

export interface WompiWidgetResult {
    transaction?: { id: string; status: string; reference: string };
}

export interface WompiWidgetOptions {
    currency: string;
    amountInCents: number;
    reference: string;
    publicKey: string;
    signature: { integrity: string };
    redirectUrl: string;
    customerData?: { email: string };
}

export interface WompiWidgetCheckout {
    open(callback: (result: WompiWidgetResult) => void): void;
}

interface WompiWindow extends Window {
    WidgetCheckout?: new (options: WompiWidgetOptions) => WompiWidgetCheckout;
}

let loading: Promise<void> | null = null;

/** Carga el script una sola vez; las siguientes llamadas reutilizan la promesa. */
export function loadWompiWidget(src: string, doc: Document = document): Promise<void> {
    if ((doc.defaultView as WompiWindow | null)?.WidgetCheckout) { return Promise.resolve(); }
    if (loading) { return loading; }
    loading = new Promise<void>((resolve, reject) => {
        const script = doc.createElement('script');
        script.src = src;
        script.async = true;
        script.onload = () => resolve();
        script.onerror = () => {
            loading = null;
            script.remove();
            reject(new Error(`No se pudo cargar ${src}`));
        };
        doc.head.appendChild(script);
    });
    return loading;
}

export function openWompiWidget(
    options: WompiWidgetOptions,
    onResult: (result: WompiWidgetResult) => void,
    win: Window = window,
): void {
    const ctor = (win as WompiWindow).WidgetCheckout;
    if (!ctor) { throw new Error('El widget de Wompi no está disponible'); }
    new ctor(options).open(onResult);
}
