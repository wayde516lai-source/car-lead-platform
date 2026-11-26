// 需配合catalog_http_fixture.py：仅访问8879端口的临时测试库，无外网。
const {JSDOM} = require('jsdom');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '..');
const base = 'http://127.0.0.1:8879';
const dom = new JSDOM(fs.readFileSync(root + '/static/index.html', 'utf8'), {
  url: base, runScripts: 'outside-only', pretendToBeVisual: true
});
const w = dom.window;
let cookie = '';
w.HTMLElement.prototype.scrollIntoView = function () {};
w.HTMLDialogElement.prototype.showModal = function () {this.open = true;};
w.HTMLDialogElement.prototype.close = function () {this.open = false;};
w.fetch = async (url, options = {}) => {
  const response = await fetch(new URL(url, base), {...options, headers: {...options.headers, Cookie: cookie}});
  if (response.headers.get('set-cookie')) cookie = response.headers.get('set-cookie').split(';')[0];
  return response;
};
const $ = selector => w.document.querySelector(selector);
async function wait(predicate, label) {
  const start = Date.now();
  while (Date.now() - start < 5000) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error(label + ': ' + $('#main')?.textContent + $('#toast')?.textContent);
}
function submit(selector) {$(selector).dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true}));}
(async () => {
  w.eval(fs.readFileSync(root + '/static/media-catalogs.js', 'utf8'));
  w.eval(fs.readFileSync(root + '/static/app.js', 'utf8').replace(/^await start\(\);?$/m, 'window.__ready=start();'));
  await w.__ready;
  $('[name=username]').value = 'admin';
  $('[name=password]').value = 'Demo2026!';
  submit('#login-form');
  await wait(() => $('#main h1'), 'login');
  $('[data-nav=mediaCities]').click();
  await wait(() => $('#main h1')?.textContent === '城市库管理' && $('#catalog-media'), 'cities');
  const cities = $('#catalog-media');
  cities.value = 'MEDIA_A';
  cities.dispatchEvent(new w.Event('change', {bubbles: true}));
  await wait(() => $('#main table')?.textContent.includes('2501'), 'city code');
  assert(!$('#main table').textContent.includes('99'));
  $('[data-action=catalog-config]').click();
  await wait(() => $('#catalog-config-form'), 'config form');
  assert($('#catalog-config-form [name=endpoint]').value.includes('/pullCityList'));
  $('#catalog-config-form [name=schedule_hour]').value = '8';
  submit('#catalog-config-form');
  await wait(() => !$('#dialog').open && $('#main').textContent.includes('08:00'), 'save config');
  $('[data-nav=mediaVehicles]').click();
  await wait(() => $('#main h1')?.textContent === '车型库管理' && $('#catalog-media'), 'vehicles');
  $('#catalog-media').value = 'MEDIA_A';
  $('#catalog-media').dispatchEvent(new w.Event('change', {bubbles: true}));
  await wait(() => $('#main table')?.textContent.includes('701'), 'vehicle code');
  const vehicleHeaders = [...w.document.querySelectorAll('.catalog-vehicle-table th')];
  const vehicleCells = [...w.document.querySelectorAll('.catalog-vehicle-table tbody tr:first-child td')];
  assert.equal(vehicleHeaders.length, 16);
  assert(vehicleHeaders[0].textContent.includes('id'));
  assert(vehicleHeaders[15].textContent.includes('memberBrandName'));
  assert.equal(vehicleCells[0].textContent, '99');
  assert.equal(vehicleCells[9].textContent, '2026');
  assert.equal(vehicleCells[10].textContent, '13.50');
  assert.equal(vehicleCells[11].textContent, '—');
  assert.equal(vehicleCells[14].textContent, '0');
  assert.equal(vehicleCells[15].textContent, '测试会员品牌');
  $('#catalog-media').value = 'MEDIA_B';
  $('#catalog-media').dispatchEvent(new w.Event('change', {bubbles: true}));
  await wait(() => $('#main').textContent.includes('该媒体尚未配置API'), 'empty other media');
  assert(!$('#main table').textContent.includes('701'));
  await w.newDemand();
  await wait(() => $('#demand-form [name=series_id]')?.textContent.includes('宝马X5'), 'media demand options');
  assert($('#demand-form [name=series_id]').innerHTML.includes('media:MEDIA_A:13:701'));
  $('#demand-form [name=demand_no]').value = 'DOM-MEDIA-CATALOG-' + Date.now();
  $('#demand-form [name=series_id]').value = 'media:MEDIA_A:13:701';
  $('#demand-form [name=city_code]').value = '上海';
  $('#demand-form [name=yc_platFormId]').value = '900001';
  $('#demand-form [name=yc_plat]').value = 'Web';
  submit('#demand-form');
  await wait(() => !$('#dialog').open, 'manual demand from library');
  console.log('PASS: temporary HTTP login, media libraries, API persistence, manual demand catalog choices');
})().catch(error => {console.error(error); process.exitCode = 1;}).finally(() => dom.window.close());
