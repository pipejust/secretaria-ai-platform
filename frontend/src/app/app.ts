import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { Meta, Title } from '@angular/platform-browser';
import { Subject } from 'rxjs';
import { filter, map, mergeMap, takeUntil } from 'rxjs/operators';
import { ToastComponent } from './components/toast/toast.component';

@Component({
    selector: 'app-root',
    imports: [RouterOutlet, ToastComponent],
    templateUrl: './app.html',
    styleUrl: './app.css',
})
export class App implements OnInit, OnDestroy {
    protected readonly title = signal('Notiva');

    private readonly destroy$ = new Subject<void>();

    constructor(
        private router: Router,
        private activatedRoute: ActivatedRoute,
        private titleService: Title,
        private metaService: Meta,
    ) {}

    ngOnInit(): void {
        this.router.events
            .pipe(
                filter((event) => event instanceof NavigationEnd),
                map(() => this.activatedRoute),
                map((route) => {
                    while (route.firstChild) route = route.firstChild;
                    return route;
                }),
                filter((route) => route.outlet === 'primary'),
                mergeMap((route) => route.data),
                takeUntil(this.destroy$),
            )
            .subscribe((event) => {
                const currentTitle = this.titleService.getTitle();
                this.metaService.updateTag({ property: 'og:title', content: currentTitle });
                this.metaService.updateTag({ property: 'twitter:title', content: currentTitle });

                if (event['description']) {
                    this.metaService.updateTag({ name: 'description', content: event['description'] });
                    this.metaService.updateTag({ property: 'og:description', content: event['description'] });
                    this.metaService.updateTag({ property: 'twitter:description', content: event['description'] });
                } else {
                    this.metaService.removeTag("name='description'");
                    this.metaService.removeTag("property='og:description'");
                    this.metaService.removeTag("property='twitter:description'");
                }

                if (event['keywords']) {
                    this.metaService.updateTag({ name: 'keywords', content: event['keywords'] });
                } else {
                    this.metaService.removeTag("name='keywords'");
                }

                if (event['robots']) {
                    this.metaService.updateTag({ name: 'robots', content: event['robots'] });
                } else {
                    this.metaService.updateTag({ name: 'robots', content: 'index, follow' });
                }
            });
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }
}
