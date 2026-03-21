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
  protected readonly title = signal('Notiva');

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
      // Configure Title
      const currentTitle = this.titleService.getTitle();
      this.metaService.updateTag({ property: 'og:title', content: currentTitle });
      this.metaService.updateTag({ property: 'twitter:title', content: currentTitle });

      // Configure Description
      if (event['description']) {
        this.metaService.updateTag({ name: 'description', content: event['description'] });
        this.metaService.updateTag({ property: 'og:description', content: event['description'] });
        this.metaService.updateTag({ property: 'twitter:description', content: event['description'] });
      } else {
        this.metaService.removeTag("name='description'");
        this.metaService.removeTag("property='og:description'");
        this.metaService.removeTag("property='twitter:description'");
      }

      // Configure Keywords
      if (event['keywords']) {
        this.metaService.updateTag({ name: 'keywords', content: event['keywords'] });
      } else {
        this.metaService.removeTag("name='keywords'");
      }

      // Configure Robots
      if (event['robots']) {
        this.metaService.updateTag({ name: 'robots', content: event['robots'] });
      } else {
        this.metaService.updateTag({ name: 'robots', content: 'index, follow' }); // Default fallback
      }
    });
  }
}
