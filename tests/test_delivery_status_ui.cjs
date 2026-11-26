const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/app.js', 'utf8');
let rendered;
const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const context = vm.createContext({
  esc, badge: esc, partner: () => '测试媒体', has: () => false,
  table: (headers, rows) => {rendered = {headers, rows}; return rows.flat().join('');}
});
vm.runInContext(source.slice(source.indexOf('function yicheCodeLabel('), source.indexOf('async function deliveries(')), context);
const row = {id: 'fixture-delivery', lead_no: 'fixture', media_id: 'MEDIA_A', receiver_id: 'AGENCY_A', validity_status: 'INVALID', yiche_code: {code: '-99', description: '超量 <script>bad()</script>'}};
context.row = row;
vm.runInContext('deliveryTable([row])', context);
assert(rendered.headers.includes('线索状态'));
assert(!rendered.headers.includes('最终有效'));
assert(!rendered.rows[0][5].includes('Code'));
assert(!rendered.rows[0][5].includes('-99'));
assert(rendered.rows[0][5].includes('超量 &lt;script&gt;bad()&lt;/script&gt;'));
assert(!rendered.rows[0][5].includes('<script>'));
assert(source.includes('沿用现有分发规则；车型比较忽略特殊符号'));
context.row = {...row, yiche_code: {code: '1027', description: '未配置失败原因（code=1027）'}};
vm.runInContext('deliveryTable([row])', context);
assert.equal(rendered.rows[0][5], '<span class="badge red">无效（原因未配置）</span>');
context.row = {...row, validity_status: 'VALID', yiche_code: {code: '2001', description: '有效线索'}};
vm.runInContext('deliveryTable([row])', context);
assert.equal(rendered.rows[0][5], 'VALID');
assert(source.includes('搜索编号、记录或完整手机号'));
console.log('PASS: 线索状态列、无效 Code 原因、转义及车型匹配说明');
