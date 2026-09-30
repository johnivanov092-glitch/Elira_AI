/* Actual browser click/download oracle driver; never implements the feature. */
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const root = __dirname;
  const batch = Number(process.argv[2] || '1');
  const mode = process.argv[3] || 'download';
  const label = process.argv[4] || `batch-${batch}`;
  if (!/^[a-z0-9-]+$/.test(label)) throw new Error('Unsafe evidence label');
  const config = JSON.parse(fs.readFileSync(path.join(root, 'config.json'), 'utf8'));
  if (!path.isAbsolute(config.browser_executable || '') || !fs.statSync(config.browser_executable).isFile()) {
    throw new Error('Configured acceptance browser executable is unavailable');
  }
  for (const key of ['TEMP', 'TMP', 'TMPDIR']) process.env[key] = config.temp;
  const { chromium } = require(config.playwright_module || 'playwright');
  const fixture = JSON.parse(fs.readFileSync(path.join(root, `fixtures-${batch}.json`), 'utf8'));
  const allowed = new Set([`http://127.0.0.1:${config.ui_port}`, `http://127.0.0.1:${config.backend_port}`]);
  const folder = path.join(root, 'browser', label);
  fs.mkdirSync(folder, { recursive: true });
  const report = { scope: 'real browser UI, not native Tauri', batch, label, requests: [], failures: [], console: [], offlineAssets: [] };
  const offlineFontStylesheet = 'https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap';
  let observing = true;
  const browser = await chromium.launch({ headless: true, executablePath: config.browser_executable });
  try {
    const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1440, height: 960 }, locale: 'ru-RU' });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (['data:', 'blob:', 'about:'].includes(url.protocol) || allowed.has(url.origin)) return route.continue();
      if (route.request().url() === offlineFontStylesheet && route.request().resourceType() === 'stylesheet') {
        report.offlineAssets.push(route.request().url());
        return route.fulfill({ status: 200, contentType: 'text/css', body: '/* Offline acceptance uses the installed fallback font. */' });
      }
      report.failures.push(`blocked unexpected origin ${url.origin}`);
      return route.abort();
    });
    const page = await context.newPage();
    page.on('response', response => {
      const url = new URL(response.url());
      if (url.pathname.includes('export') || url.pathname.includes('archive') || response.status() >= 400) {
        report.requests.push({ url: response.url(), status: response.status(), method: response.request().method() });
      }
      if (response.status() >= 400) report.failures.push(`HTTP ${response.status()} ${response.url()}`);
    });
    page.on('requestfailed', request => {
      if (observing) report.failures.push(`request failed ${request.url()}: ${request.failure()?.errorText || 'unknown'}`);
    });
    page.on('pageerror', error => report.failures.push(error.message));
    page.on('console', message => {
      if (message.type() === 'error') {
        report.console.push(message.text());
        report.failures.push(`console error: ${message.text()}`);
      }
    });
    await page.goto(`http://127.0.0.1:${config.ui_port}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.getByText(fixture.folder_name, { exact: true }).waitFor({ timeout: 20000 });
    report.buttons = await page.getByRole('button').evaluateAll(elements => elements.map(el => ({ text: el.textContent, label: el.getAttribute('aria-label'), title: el.getAttribute('title') })));
    if (mode !== 'inspect') {
      const folderText = page.getByText(fixture.folder_name, { exact: true });
      let owner = folderText;
      let button;
      for (let depth = 0; depth < 6; depth++) {
        const candidate = owner.getByRole('button', { name: /Скачать архив/i });
        if (await candidate.count() === 1) { button = candidate; break; }
        owner = owner.locator('..');
      }
      if (!button) throw new Error('No unique archive button found in selected folder; inspect observed DOM before adapting selector');
      await button.scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(folder, 'before-click.png'), fullPage: true });
      report.clicked = await button.evaluate(el => ({ text: el.textContent, label: el.getAttribute('aria-label'), title: el.getAttribute('title') }));
      const downloadPromise = page.waitForEvent('download', { timeout: 15000 });
      await button.click();
      try {
        const download = await downloadPromise;
        report.download = path.join(folder, 'download.zip');
        await download.saveAs(report.download);
        report.suggestedFilename = download.suggestedFilename();
        report.downloadFailure = await download.failure();
      } catch (error) {
        report.downloadError = error.message;
      }
    }
    report.text = (await page.locator('body').innerText()).slice(0, 25000);
    await page.screenshot({ path: path.join(folder, 'after-click.png'), fullPage: true });
    report.ok = mode === 'inspect' ? report.failures.length === 0 : Boolean(report.download && !report.downloadFailure && !report.downloadError && report.failures.length === 0);
  } catch (error) {
    report.ok = false;
    report.error = error.stack;
  } finally {
    observing = false;
    await browser.close();
    fs.writeFileSync(path.join(folder, 'result.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
  }
  console.log(JSON.stringify({ ok: report.ok, download: report.download, evidence: folder, error: report.error || report.downloadError, failures: report.failures }));
  if (!report.ok) process.exitCode = 1;
}
main().catch(error => { console.error(error); process.exitCode = 1; });
