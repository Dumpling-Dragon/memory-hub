// Run with Node and Playwright installed. All API responses are synthetic.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
(async () => {
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://127.0.0.1/**', async route => {
      const url = new URL(route.request().url());
      const pathname = url.pathname;
      if (pathname === '/api/ask') return route.abort('failed');
      if (pathname === '/api/sync') return route.fulfill({status:500, contentType:'text/plain', body:'fixture failure'});
      let data;
      if (pathname === '/api/status') data={sources:[]};
      if (pathname === '/api/agent-prompt') data={prompt:'fixture actual configuration'};
      if (pathname === '/api/search') {
        const q = url.searchParams.get('q');
        if (q === 'old') await new Promise(resolve=>setTimeout(resolve,300));
        data={results:q ? [{id:'fixture', source:'fixture', title:q, body:'synthetic body', excerpt:'synthetic excerpt'}] : []};
      }
      if (data) return route.fulfill({contentType:'application/json', body:JSON.stringify(data)});
      const file = pathname === '/' ? 'index.html' : path.basename(pathname);
      const ext = path.extname(file);
      return route.fulfill({contentType:ext === '.js' ? 'application/javascript' : ext === '.css' ? 'text/css' : 'text/html', body:fs.readFileSync(path.join(__dirname,'../memory_hub/static',file))});
    });
    await page.goto('http://127.0.0.1/');
    await page.waitForFunction(()=>document.querySelector('#agent-prompt-text').value.includes('actual configuration'));
    await page.locator('#query').fill('old');
    await page.locator('#search').click();
    await page.locator('#query').fill('new');
    await page.locator('#search').click();
    await page.waitForTimeout(450);
    assert.equal(await page.locator('#results h3').first().textContent(), 'new');
    await page.locator('#ask').click();
    await page.waitForFunction(()=>!document.querySelector('#ask').disabled);
    assert.ok(await page.locator('#answer').textContent());
    await page.locator('#sync').click();
    await page.waitForFunction(()=>!document.querySelector('#sync').disabled);
    assert.match(await page.locator('#count').textContent(), /500/);
    assert.deepEqual(errors, []);
    console.log('Frontend: stale search, failed ask/sync recovery and live prompt passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
