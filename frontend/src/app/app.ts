import { Component, signal, OnInit } from '@angular/core';
import { RouterOutlet, Router, NavigationEnd, ActivatedRoute } from '@angular/router';
import { Title, Meta } from '@angular/platform-browser';
import { filter, map, mergeMap } from 'rxjs/operators';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App implements OnInit {
  protected readonly title = signal('Secretaria AI');

  constructor(
    private router: Router,
    private activatedRoute: ActivatedRoute,
    private titleService: Title,
    private metaService: Meta
  ) {}

  ngOnInit() {
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map(() => this.activatedRoute),
      map((route) => {
        while (route.firstChild) route = route.firstChild;
        return route;
      }),
      filter((route) => route.outlet === 'primary'),
      mergeMap((route) => route.data)
    ).subscribe((event) => {
      // Angular 14+ automatically sets the <title> tag via the Route config natively.
      // Here, we reactively swap the Meta Descriptions per view for world-class SEO:
      if (event['description']) {
        this.metaService.updateTag({ name: 'description', content: event['description'] });
        this.metaService.updateTag({ property: 'og:description', content: event['description'] });
        this.metaService.updateTag({ property: 'twitter:description', content: event['description'] });
      }
    });
  }
}
