// 只使用假API及DOM容器，验证媒体分类、城市栏目、API表单及弹窗错误。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const calls = [];
let modalBody = '', errorMessage = '', closed = false, failSave = false;
const libraries = [
  {id: 'yiche', name: '易车', configured: false, enabled: false, runs: [], schedule_hour: 0, version: 0},
  {id: 'baidu', name: '百度有驾', configured: false, enabled: false, runs: [], schedule_hour: 0, version: 0}
];
const context = vm.createContext({URLSearchParams, document: {querySelector: () => ({close: () => {closed = true;}})}});
vm.runInContext(fs.readFileSync('static/media-catalogs.js', 'utf8'), context);
const esc = value => String(value ?? '').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const app = context.createMediaCatalogUI({
  esc, opt: (v, label) => `<option value="${esc(v)}">${esc(label)}</option>`, badge: esc, fmt: esc,
  has: () => true, table: (headers, rows) => JSON.stringify([headers, rows]), render: async () => {},
  modal: (title, html) => {modalBody = title + html;}, feedback: (form, message) => {errorMessage = message;},
  api: async (route, method = 'GET', body) => {
    calls.push({route, method, body});
    if (method === 'PATCH') {if (failSave) throw new Error('版本已变化'); return {saved: true};}
    if (route === 'media-catalogs/cities' || route === 'media-catalogs/vehicles') return libraries;
    return {items: [], total: 0, has_more: false};
  }
});
(async () => {
  let html = await app.view('cities');
  assert(html.includes('城市库管理'));
  assert(html.includes('媒体分类'));
  assert(html.includes('百度有驾'));
  await app.action('catalog-config');
  assert(modalBody.includes('/api/v1/pullCityList'));
  assert(modalBody.includes('name="token" type="password"'));
  failSave = true;
  const form = {id: 'catalog-config-form', dataset: {kind: 'cities', media: 'yiche', version: '0'}};
  const data = {endpoint: 'http://yiche.asoapple.cn/api/v1/pullCityList', enabled: 'yes', schedule_hour: '8', token: '', clear_token: 'no'};
  await app.submit(form, data);
  assert.equal(errorMessage, '版本已变化');
  assert.equal(closed, false);
  failSave = false;
  await app.submit(form, data);
  assert.equal(closed, true);
  const saved = calls.find(row => row.method === 'PATCH');
  assert.equal(saved.body.schedule_hour, 8);
  assert.equal(saved.body.enabled, true);
  await app.change({id: 'catalog-media', value: 'baidu'});
  await app.action('catalog-config');
  assert(!modalBody.includes('value="http://yiche.asoapple.cn/api/v1/pullCityList"'));
  html = await app.view('vehicles');
  assert(html.includes('车型库管理'));
  assert(html.includes('车系编号'));
  const main = fs.readFileSync('static/app.js', 'utf8');
  assert(main.includes("['mediaCities','⌖','城市库管理']"));
  assert(fs.readFileSync('static/index.html', 'utf8').includes('/static/media-catalogs.js'));
  console.log('Media catalog UI interactions passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
