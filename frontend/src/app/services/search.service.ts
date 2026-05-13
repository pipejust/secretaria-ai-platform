import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { environment } from '../../environments/environment';

export interface SearchHit {
    type: 'meeting' | 'project' | 'task' | 'person';
    id: number;
    title: string;
    subtitle: string;
    link_to: string;
    meta?: Record<string, unknown>;
}

export interface SearchGroup {
    label: string;
    items: SearchHit[];
}

export interface SearchResponse {
    query: string;
    groups: SearchGroup[];
    total: number;
}

/**
 * Wrapper sobre /api/search?q=. El componente de UI hace el debounce
 * — este service sólo hace el HTTP.
 */
@Injectable({ providedIn: 'root' })
export class SearchService {
    private readonly http = inject(HttpClient);

    /** Lanza una query global. Si q tiene <2 chars, no llama al backend
     *  (ahorra el round-trip y respeta la regla del endpoint). */
    search(q: string): Observable<SearchResponse> {
        const term = (q || '').trim();
        if (term.length < 2) {
            return of({ query: q, groups: [], total: 0 });
        }
        const params = new URLSearchParams({ q: term }).toString();
        return this.http
            .get<SearchResponse>(`${environment.apiUrl}/api/search?${params}`)
            .pipe(
                catchError(() => of({ query: q, groups: [], total: 0 })),
            );
    }
}
