const puppeteer = require('puppeteer');
(async () => {
  const browser = await puppeteer.launch({headless: 'new'});
  const page = await browser.newPage();
  page.on('console', msg => console.log('PAGE LOG:', msg.text()));
  
  await page.goto('http://localhost:4200/login');
  await page.type('input[type="email"]', 'admin@admin.com');
  await page.type('input[type="password"]', 'admin123');
  await page.click('button[type="submit"]');
  
  await page.waitForNavigation();
  await page.goto('http://localhost:4200/admin/templates');
  
  // Wait for Angular to render
  await new Promise(r => setTimeout(r, 2000));
  
  // Inject mock data to show the modal button
  await page.evaluate(() => {
    const el = document.querySelector('app-templates');
    const component = ng.getComponent(el);
    component.templates = [{ id: 999, name: 'Mock Template', project: {name: 'Test Project'}, mapping_config: '[]' }];
    component.cdr.detectChanges();
  });
  
  await new Promise(r => setTimeout(r, 500));
  
  // Click wrench to open modal
  await page.evaluate(() => {
     document.querySelector('.btn-outline').click();
  });
  
  await new Promise(r => setTimeout(r, 1000));
  
  // Evaluate the length of elements
  const data = await page.evaluate(() => {
     const dbg = document.body.innerHTML.match(/DEBUG:.*</);
     const dragBoxes = document.querySelectorAll('.drag-box').length;
     const dropListHtml = document.querySelector('.drag-list-container')?.innerHTML;
     return { debugText: dbg ? dbg[0] : 'not found', dragBoxes, dropListHtml };
  });
  
  console.log(JSON.stringify(data, null, 2));
  await browser.close();
})();
