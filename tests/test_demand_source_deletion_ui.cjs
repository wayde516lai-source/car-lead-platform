// 隔离验证来源删除交互与项目筛选，不连接业务服务或修改真实数据库。
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const calls = [];
const feedback = { hidden: true, textContent: '' };
let modalBody = '';
let closed = false;
let failure = false;
let refreshed = 0;
let editable = true;
const sources = [{ id: 'wrong-link', name: '错误来源', partner_id: 'buyer', media_id: 'media',
  sale_project: 'KA', project_count: 0, version: 7, url: 'https://docs.qq.com/sheet/DTEST?tab=A',
  enabled: 0, last_error: '首行缺少需求编号' }];
const projects = { source_ids: ['wrong-link'], headers: [], items: [], total: 0, counts: {},
  filter_options: { project: [{value: 'MEMBER', label: '会员'}, {value: 'KA', label: 'KA'}] },
  summary: { brand_count: 0, city_count: 0, order_quantities: {} } };
const context = vm.createContext({
  URLSearchParams, demandSyncSources: [], demandSyncView: {page: 3, size: 20, filters: {project: 'KA'}},
  state: {catalog: {partners: []}},
  api: async (route, method = 'GET', body) => {
    calls.push({route, method, body});
    if (method === 'DELETE') {
      if (failure) throw new Error('来源已修改，请刷新');
      return {deleted: true};
    }
    return route === 'demand-sync/sources' ? sources : projects;
  },
  has: () => editable, partner: String, esc: String, fmt: () => '—',
  entryPicker: (id, name, label) => `<label>${label}<input name="${name}"></label>`,
  opt: (value, label, selected) => `<option value="${value}" ${value === selected ? 'selected' : ''}>${label}</option>`,
  table: () => '', orderQuantityText: () => '0',
  modal: (title, body) => {modalBody = body;}, toast: () => {},
  $: selector => selector === '#dialog' ? {close: () => {closed = true;}} : feedback,
  render: async () => {refreshed++;},
});
vm.runInContext(source.slice(source.indexOf('async function demandSyncPanel()'),
  source.indexOf('function setDemandSourceProjects(')), context);

(async () => {
  const html = await context.demandSyncPanel();
  assert(html.includes('name="project"'));
  assert(html.includes('value="KA" selected'));
  assert(calls.some(call => call.route.includes('project=KA')));
  assert(html.includes('data-action="demand-sync-delete"'));
  assert(html.includes('首行缺少需求编号'));
  editable = false;
  assert(!(await context.demandSyncPanel()).includes('data-action="demand-sync-delete"'));
  editable = true;
  await context.demandSourceDeleteForm('wrong-link');
  assert(modalBody.includes('错误来源'));
  assert(modalBody.includes('0 条当前需求'));
  assert(modalBody.includes('data-version="7"'));
  assert(modalBody.includes('已发送记录和历史保留'));

  // 使用真实点击分支，检查传给DELETE的确为当前按钮版本，错误在弹窗内可见。
  let click;
  context.document = {addEventListener: (event, handler) => {if (event === 'click') click = handler;}};
  vm.runInContext(source.slice(source.indexOf("document.addEventListener('click',async e=>"),
    source.indexOf("document.addEventListener('submit',async e=>")), context);
  const button = {disabled: false, dataset: {action: 'demand-sync-delete-confirm', id: 'wrong-link', version: '7'}};
  failure = true;
  await click({target: {closest: () => button}});
  assert.equal(feedback.hidden, false);
  assert(feedback.textContent.includes('来源已修改'));
  assert.equal(closed, false);
  assert.equal(button.disabled, false);
  failure = false;
  await click({target: {closest: () => button}});
  const request = calls.find(call => call.method === 'DELETE');
  assert.equal(request.route, 'demand-sync/sources/wrong-link');
  assert.equal(request.body.version, 7);
  assert.equal(closed, true);
  assert.equal(context.demandSyncView.page, 1);
  assert.equal(context.demandSyncView.filters.project, 'KA');
  assert.equal(refreshed, 1);
  console.log('PASS: 项目筛选、失败来源删除入口、范围确认、版本提交、错误可见及删除后刷新');
})().catch(error => {console.error(error); process.exitCode = 1;});
