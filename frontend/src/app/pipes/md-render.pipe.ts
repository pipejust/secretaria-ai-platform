import { Pipe, PipeTransform } from '@angular/core';

/**
 * Markdown → HTML para el SUBSET que aparece en los outputs de IA del
 * workspace: headers (#/##/###), **bold**, *italic*, listas (- / * /
 * 1. 2.), código inline (`code`), saltos de línea (real o literal `\n`),
 * y párrafos. Cero dependencias externas — los outputs del backend usan
 * un Markdown muy acotado y este renderer cubre el 100% sin riesgo de
 * inyección porque:
 *
 *   1. Escapamos HTML del input ANTES de aplicar la transformación.
 *   2. Sólo emitimos tags whitelisted (h2-h4, p, strong, em, ul, ol, li,
 *      br, code) — Angular DomSanitizer hace un segundo pase defensivo.
 *
 * No reemplazo libs como `marked` porque (a) la regla del proyecto es
 * "no introducir dependencias innecesarias" y (b) el subset es chico.
 */
@Pipe({ name: 'mdRender', standalone: true })
export class MdRenderPipe implements PipeTransform {
    transform(raw: string | null | undefined): string {
        if (raw == null) return '';
        const text = String(raw);
        if (!text.trim()) return '';

        // 1) Escape HTML entities en el texto crudo.
        let src = this._escapeHtml(text);

        // 2) Normalizar saltos: el backend a veces guarda "\n" literal
        //    (caracteres backslash + n) en vez de un newline real. Los
        //    convertimos a newline real para procesar listas/párrafos
        //    correctamente.
        src = src.replace(/\\n/g, '\n').replace(/\r\n/g, '\n');

        // 3) Convertir headers (## y ### y #) — al inicio de línea.
        src = src.replace(/^\s*###\s+(.+)$/gm, '<h4 class="md-h4">$1</h4>');
        src = src.replace(/^\s*##\s+(.+)$/gm,  '<h3 class="md-h3">$1</h3>');
        src = src.replace(/^\s*#\s+(.+)$/gm,   '<h2 class="md-h2">$1</h2>');

        // 4) Inline: **bold** y *italic* y `code`.
        //    bold primero (doble asterisco), luego italic (simple).
        src = src.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>');
        src = src.replace(/(^|[^\*])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');
        src = src.replace(/`([^`\n]+?)`/g, '<code class="md-code">$1</code>');

        // 5) Listas: agrupar líneas consecutivas que empiezan con "- "
        //    o "* " o "1. " en <ul>/<ol>.
        src = this._wrapLists(src);

        // 6) Párrafos: dividir por dobles newlines y envolver el resto
        //    en <p>. Single newline dentro de un párrafo = <br>.
        src = this._wrapParagraphs(src);

        return src;
    }

    /** Escapa caracteres especiales para evitar XSS. */
    private _escapeHtml(s: string): string {
        return s
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /** Detecta runs de líneas con bullet/número y los envuelve en <ul>/<ol>. */
    private _wrapLists(src: string): string {
        const lines = src.split('\n');
        const out: string[] = [];
        let buffer: string[] = [];
        let listType: 'ul' | 'ol' | null = null;

        const flush = () => {
            if (!buffer.length || !listType) return;
            out.push(`<${listType} class="md-list">${buffer.join('')}</${listType}>`);
            buffer = [];
            listType = null;
        };

        for (const line of lines) {
            const ul = /^\s*[-*]\s+(.+)$/.exec(line);
            const ol = /^\s*\d+\.\s+(.+)$/.exec(line);
            if (ul) {
                if (listType && listType !== 'ul') flush();
                listType = 'ul';
                buffer.push(`<li>${ul[1]}</li>`);
            } else if (ol) {
                if (listType && listType !== 'ol') flush();
                listType = 'ol';
                buffer.push(`<li>${ol[1]}</li>`);
            } else {
                flush();
                out.push(line);
            }
        }
        flush();
        return out.join('\n');
    }

    /** Agrupa líneas plain en <p>, deja headers/listas intactos. */
    private _wrapParagraphs(src: string): string {
        // Divide en bloques separados por línea(s) en blanco.
        const blocks = src.split(/\n\s*\n/);
        return blocks
            .map((block) => {
                const trimmed = block.trim();
                if (!trimmed) return '';
                // Si el bloque YA es un tag block-level (h*, ul, ol),
                // no lo envolvemos. Detectamos por el primer tag.
                if (/^<(h[234]|ul|ol)/i.test(trimmed)) {
                    return trimmed;
                }
                // Single newlines dentro de un párrafo → <br>.
                return `<p class="md-p">${trimmed.replace(/\n/g, '<br>')}</p>`;
            })
            .filter(Boolean)
            .join('\n');
    }
}
