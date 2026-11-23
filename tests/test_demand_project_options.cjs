// 项目选项只取主体已开通项目，切换主体不能残留旧选项。
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const helper = source.slice(source.indexOf('function setDemandSourceProjects('), source.indexOf('async function demandSyncForm('));
const dom = new JSDOM('<form><select name="partner_id"><option value=""></option><option>A</option><option>B</option><option>C</option><option>D</option></select><select name="sale_project"></select></form>', { runScripts: 'outside-only' });
dom.window.eval(`const state={catalog:{partners:[
 {id:'A',cooperation_project:'MEMBER',cooperation_projects:[{project:'MEMBER'}]},
 {id:'B',cooperation_project:'KA',cooperation_projects:[{project:'KA'}]},
 {id:'C',cooperation_project:'MEMBER',cooperation_projects:[{project:'MEMBER'},{project:'KA'}]},
 {id:'D',cooperation_projects:[]}
]}};window.testState=state;const opt=(v,t,s='')=>'<option value="'+v+'" '+(v===s?'selected':'')+'>'+t+'</option>';${helper}`);
const form = dom.window.document.querySelector('form');
const projectValues = () => Array.from(form.elements.sale_project.options).map(option => option.value).filter(Boolean);
function select(subject, previous = '', auto = true) {
  form.elements.partner_id.value = subject;
  dom.window.setDemandSourceProjects(form, previous, auto);
}
select(''); assert(form.elements.sale_project.disabled); assert.deepEqual(projectValues(), []);
select('A', 'KA'); assert.deepEqual(projectValues(), ['MEMBER']); assert.equal(form.elements.sale_project.value, 'MEMBER');
select('B', 'MEMBER'); assert.deepEqual(projectValues(), ['KA']); assert.equal(form.elements.sale_project.value, 'KA');
select('C', 'KA', false); assert.deepEqual(projectValues(), ['MEMBER', 'KA']); assert.equal(form.elements.sale_project.value, 'KA');
select('A', 'KA', false); assert.equal(form.elements.sale_project.value, '');
select('D'); assert(form.elements.sale_project.disabled); assert.deepEqual(projectValues(), []);
select(''); assert(form.elements.sale_project.disabled); assert.equal(form.elements.sale_project.value, '');
dom.window.testState.catalog.partners[0].cooperation_projects.push({project:'KA'});
select('A', 'KA', false); assert.deepEqual(projectValues(), ['MEMBER', 'KA']); assert.equal(form.elements.sale_project.value, 'KA');
dom.window.close();
console.log('PASS: 未选主体、会员/KA单项、双项、未开通、切换清理、编辑回填、配置更新。');
