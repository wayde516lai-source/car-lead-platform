const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/app.js', 'utf8');
const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
let tableResult;
const data = {headers: ['需求编号', '开始时间', '其他信息备注', '其他信息备注（渠道编辑）', '更新日期', '下单量'],
  items: [{id: 'fixture', partner_id: 'buyer', values: {'需求编号': '001', '开始时间': '2026-11-20', '下单量': '12'}, execution_days: 6, match_status: 'ACTIVE'}],
  source_ids: [], filter_options: {}, counts: {ACTIVE: 1}, total: 1, summary: {brand_count: 1, city_count: 1, order_quantities: {}}, has_more: false};
const context = vm.createContext({
  esc, URLSearchParams, demandSyncSources: [], demandSyncView: {page: 1, size: 20}, state: {catalog: {partners: []}},
  api: async route => route === 'demand-sync/sources' ? [] : data,
  entryPicker: () => '', has: () => false, partner: () => '测试合作商', opt: () => '', badge: esc, fmt: esc,
  modelCell: esc, regionCell: esc, orderQuantityText: () => '12 条/日',
  table: (headers, rows) => {tableResult = {headers, rows}; return '';}
});
vm.runInContext(source.slice(source.indexOf('function dailyDemandRanking('), source.indexOf('async function dashboard(')), context);
vm.runInContext(source.slice(source.indexOf('async function demandSyncPanel('), source.indexOf('async function demandSourceDeleteForm(')), context);
(async () => {
  await context.demandSyncPanel();
  assert(tableResult.headers.includes('执行天数'));
  assert(!tableResult.headers.some(header => header.startsWith('其他信息备注') || header === '更新日期'));
  assert(tableResult.rows[0].includes('6 天'));
  const chart = context.dailyDemandRanking([{daily_quantity: 12, brand: '<测试品牌>', model: '车型一', city: '上海', demand_no: '001'}]);
  assert(chart.includes('每日下单量前十需求'));
  for(const text of ['&lt;测试品牌&gt;', '车型一', '上海', '条/日', 'width:100%'])assert(chart.includes(text));
  assert(!chart.includes('<测试品牌>'));
  assert(context.dailyDemandRanking([]).includes('暂无可排名'));
  assert(!source.includes('执行中的采购需求'));
  console.log('PASS: 隐藏备注/更新日期、执行天数、每日排行标签/柱形/空状态及转义');
})().catch(error => {console.error(error);process.exitCode = 1;});
