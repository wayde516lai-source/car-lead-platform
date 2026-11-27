const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('static/app.js','utf8');
let tableRows,requests=[],handler,rendered=0;
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const detail={media:'易车',buyer:'测试主体',project:'MEMBER',demand_no:'001',daily_quantity:100,remaining:90,status:'ACTIVE',block_reasons:['DISPATCH_PAUSED'],shared_scope:true,scope_note:'多城市共享',end_at:null};
const data={items:[{brand:'<品牌>',model:'车型一',city:'上海',media_count:2,demand_count:2,daily_quantity:180,common_quantity:80,remaining:170,unique_reference:90,inventory:1,suggested:null,details:[detail],nearest_end:null}],total:1};
const context=vm.createContext({URLSearchParams,esc,state:{q:'车型一',page:1,size:20},reasons:{DISPATCH_PAUSED:'全局分发已暂停'},
 api:async route=>{requests.push(route);return data},heading:()=>'',filters:(statuses,controls,placeholder)=>'<form>'+placeholder+controls+'</form>',pagination:()=>'',opt:()=>'',fmt:esc,badge:esc,
 table:(headers,rows)=>{if(headers.includes('城市'))tableRows=rows;return rows.flat().join('')},
 document:{addEventListener:(name,fn)=>handler=fn},render:async()=>rendered++});
vm.runInContext(source.slice(source.indexOf("let overlapProject="),source.indexOf('async function autoPanel(')),context);
(async()=>{
 const html=await context.demandOverlap();
 for(const value of ['&lt;品牌&gt;','车型一','上海','180','80','待核算','001','易车','测试主体','全局分发已暂停','多城市共享'])assert(html.includes(value),value);
 assert(!html.includes('<品牌>'));
 assert(requests[0].includes('overlap_only=1'));
 await handler({target:{id:'overlap-project',value:'MEMBER'}});
 assert.equal(rendered,1);
 await context.demandOverlap();
 assert(requests.at(-1).includes('project=MEMBER'));
 assert(tableRows.length===1);
 assert(source.includes("['demandOverlap','⊞','需求重合分析']"));
 const container={innerHTML:''};
 context.$=()=>container;
 context.state.view='demandOverlap';
 let release;
 context.api=()=>new Promise(resolve=>release=resolve);
 const pending=context.demandOverlap();
 assert(container.innerHTML.includes('正在分析需求重合'));
 assert(container.innerHTML.includes('搜索品牌、车型或城市'));
 release(data);
 await pending;
 context.api=async()=>{throw new Error('范围过大')};
 const failed=await context.demandOverlap();
 assert(failed.includes('搜索品牌、车型或城市'));
 assert(failed.includes('overlap-project'));
 assert(failed.includes('范围过大'));
 assert(failed.includes('当前分析未完成'));
 console.log('PASS: 重合主表、明细、单位、未知补充量、拦截原因、筛选和转义');
})().catch(error=>{console.error(error);process.exitCode=1});
