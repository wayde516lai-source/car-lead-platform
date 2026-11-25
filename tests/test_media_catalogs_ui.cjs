// 只使用假API及DOM容器，验证媒体分类、城市栏目、API表单及弹窗错误。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const calls = [];
let vehicleItems = [], tableCalls = [];
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
  has: () => true, table: (headers, rows) => {tableCalls.push({headers, rows}); return JSON.stringify([headers, rows]);}, render: async () => {},
  modal: (title, html) => {modalBody = title + html;}, feedback: (form, message) => {errorMessage = message;},
  api: async (route, method = 'GET', body) => {
    calls.push({route, method, body});
    if (method === 'PATCH') {if (failSave) throw new Error('版本已变化'); return {saved: true};}
    if (route === 'media-catalogs/cities' || route === 'media-catalogs/vehicles') return libraries;
    return {items: route.startsWith('media-catalogs/vehicles/') ? vehicleItems : [], total: vehicleItems.length, has_more: false};
  }
});
(async () => {
  let html = await app.view('cities');
  assert(html.includes('城市库管理'));
  assert(html.includes('媒体分类'));
  assert(html.includes('百度有驾'));
  assert(/data-action="catalog-sync"[^>]*disabled/.test(html));
  libraries[0].configured = true;
  libraries[0].runs = [{state: 'SUCCESS', trigger_kind: 'manual'}];
  for (const kind of ['cities', 'vehicles']) {
    const rendered = await app.view(kind);
    assert(!/data-action="catalog-sync"[^>]*disabled/.test(rendered));
    assert(rendered.includes('每日自动同步已暂停'));
    assert(rendered.includes('同步成功'));
    assert(rendered.includes('catalog-filter-form'));
    await app.action('catalog-sync');
    assert(calls.some(row => row.route === `media-catalogs/${kind}/yiche/sync` && row.method === 'POST'));
  }
  assert(!calls.some(row => row.method === 'PATCH'));
  await app.view('cities');
  await app.action('catalog-config');
  assert(modalBody.includes('每日自动同步'));
  assert(modalBody.includes('暂停（仍可手动同步）'));
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
  const source = {
    id: 99, carId: 173219, carName: '2.0T 测试车款', seriesId: 11146, seriesName: '212 T01',
    factoryId: 31001, factoryName: '测试厂商', brandId: 650, brandName: '212',
    year: 2026, guidePrice: '13.50', displacement: null, power: '150', gearbox: '<手动>',
    saleStatus: 0, memberBrandName: '测试会员品牌'
  };
  vehicleItems = [source];
  tableCalls = [];
  html = await app.view('vehicles');
  const vehicleTable = tableCalls[0];
  assert.equal(vehicleTable.headers.length, 16);
  const keys = ['id', 'carId', 'carName', 'seriesId', 'seriesName', 'factoryId', 'factoryName',
    'brandId', 'brandName', 'year', 'guidePrice', 'displacement', 'power', 'gearbox', 'saleStatus', 'memberBrandName'];
  keys.forEach((key, index) => {
    assert(vehicleTable.headers[index].includes(`>${key}</span>`), `header for ${key}`);
    assert.equal(vehicleTable.rows[0][index], esc(source[key] ?? '—'), `value for ${key}`);
  });
  assert.equal(vehicleTable.rows[0][8], '212');
  assert.equal(vehicleTable.rows[0][14], '0');
  assert(html.includes('catalog-vehicle-table'));
  assert(html.includes('横向滚动'));
  assert(!html.includes('<手动>'));
  tableCalls = [];
  await app.view('cities');
  assert.equal(tableCalls[0].headers.length, 4);
  const main = fs.readFileSync('static/app.js', 'utf8');
  assert(main.includes("['mediaCities','⌖','城市库管理']"));
  assert(fs.readFileSync('static/index.html', 'utf8').includes('/static/media-catalogs.js'));
  console.log('Media catalog UI interactions passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
