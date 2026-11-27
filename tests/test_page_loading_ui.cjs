const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('static/app.js','utf8');
const calls=[];
const context=vm.createContext({AbortController,state:{user:{csrf:'fixture'}},
 fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({data:{items:[]}})}}});
vm.runInContext(source.slice(source.indexOf('let pageRequestsController='),source.indexOf('// 空状态')),context);
vm.runInContext("pageRequestsController=new AbortController()",context);
vm.runInContext(source.slice(source.indexOf('async function allActiveDemands('),source.indexOf('function procurementProjects(')),context);
(async()=>{
 await context.api('fixture');
 await context.api('matches/dispatch','POST',{});
 assert(calls[0].options.signal);
 assert(!calls[1].options.signal);
 await context.allActiveDemands();
 assert.equal(calls.at(-1).url,'/api/v1/matches/demands');
 assert.equal(calls.length,3);
 const rendered=[];
 Object.assign(context,{api:async route=>{rendered.push(route);return {metrics:{sent:0,acked:0,stored:0,valid:0,invalid:0,pending:0},groups:[],multisale:0}},heading:()=>'',has:()=>false,stat:()=>'',pct:()=>'',table:()=>'',channel:()=>'',esc:String});
 const start=source.indexOf('async function reports('),end=source.indexOf('\nasync function ',start+1);
 vm.runInContext(source.slice(start,end),context);
 await context.reports();
 assert.deepEqual(rendered,['reports/funnel?section=delivery']);
 console.log('PASS: 离页请求只取消GET、写入不取消、匹配需求单次轻量加载及报表独立加载');
})().catch(error=>{console.error(error);process.exitCode=1});
