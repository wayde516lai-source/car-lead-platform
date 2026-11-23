// 登录页初始化：普通账号输入与显式演示入口分离，无网络请求。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const loginFunction = source.split('\n').find(line => line.startsWith('function login()'));
for (const mode of [false, true]) {
  const dom = new JSDOM(`<head><meta name="demo-mode" content="${mode}"></head><body><div id="app"></div></body>`, { runScripts: 'outside-only' });
  dom.window.eval(`const state={};const roles={admin:'管理员'};const $=s=>document.querySelector(s);const opt=(v,t)=>'<option value="'+v+'">'+t+'</option>';${loginFunction};login();`);
  const doc = dom.window.document;
  assert.equal(doc.querySelector('[name=username]').tagName, mode ? 'SELECT' : 'INPUT');
  assert.equal(Boolean(doc.querySelector('[name=password]').value), mode);
  assert.equal(doc.body.textContent.includes('共用测试密码'), mode);
  dom.window.close();
}
console.log('PASS: 普通登录无默认密码且支持自定义账号；显式演示模式保留演示入口。');
