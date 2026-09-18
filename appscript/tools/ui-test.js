// ui-test.js — run UI.html's own filter and empty-state logic outside a browser.
//
//     node appscript/tools/ui-test.js
//
// ── WHY ─────────────────────────────────────────────────────────────────────────────────────
// A "mine" filter was left on and persisted with no visible control after a redesign removed
// its button. The queue said 2 and the list said "Nothing here." — the count and the list
// disagreed and nothing on screen explained why. Neither the .gs tests nor the e2e could catch
// it, because the bug lived entirely in the browser half.
//
// The rule this pins: any filter that can empty the list must be visible, and an empty list
// must say WHY it is empty and offer the way out.
const fs=require('fs'), vm=require('vm');
const path=require('path');
const html=fs.readFileSync(path.resolve(__dirname,'..','UI.html'),'utf8');
const code=html.match(/<script>([\s\S]*)<\/script>/)[1];

// minimal DOM so the module's top-level call does not explode
const els={};
function mk(id){ return els[id]||(els[id]={id,innerHTML:'',textContent:'',className:'',style:{},value:'',
  classList:{contains:()=>false},disabled:false,contains:()=>false}); }
const sandbox={
  console,
  document:{ getElementById:mk, addEventListener(){}, activeElement:{tagName:'BODY'},
             querySelectorAll:()=>[], querySelector:()=>null },
  window:{ addEventListener(){} },
  google:{ script:{ run:new Proxy({},{ get:(t,p)=>{
    if(p==='withSuccessHandler'||p==='withFailureHandler') return ()=>sandbox.google.script.run;
    return ()=>{}; } }) } },
  setTimeout, clearTimeout, setInterval:()=>{}, Date,
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

// the screenshot: Open=2 (both unassigned), Resolved=3 (all mine), mine filter ON
sandbox.DATA={
  me:{email:'pranav.akella@meesho.com',name:'pranav.akella',filter:''},
  counts:{'NEEDS A CATEGORY':0,'Open':2,'Being worked on':0,'Resolved':3,'Withheld':0},
  mine:3, unassigned:2, total:5, capped:false, health:[], roster:[], dispositions:[],
  tickets:[
    {key:'a',ref:'A',group:'Open',title:'one',dc_code:'NQS',raiser:'x',intent_label:'',assigned_to:'',occurrence_count:1,flags:0,last_raised_at:''},
    {key:'b',ref:'B',group:'Open',title:'two',dc_code:'',raiser:'y',intent_label:'',assigned_to:'',occurrence_count:1,flags:0,last_raised_at:''},
  ]
};
sandbox.VIEW={group:'Open',mine:true,q:''};
sandbox.LOADING=false;

let fails=0;
const ok=(label,cond,extra)=>{ console.log(`  ${cond?'PASS':'FAIL'}  ${label}${extra?'   '+extra:''}`); if(!cond) fails++; };

sandbox.renderList();
const hidden=els['list'].innerHTML.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim();
ok('a hidden filter explains itself', /none of them are assigned to you/.test(hidden), hidden);
ok('and offers the way out', /Show all 2/.test(hidden));

sandbox.VIEW.mine=false;
sandbox.renderList();
ok('clearing it shows every ticket', (els['list'].innerHTML.match(/class="t"/g)||[]).length===2);

sandbox.VIEW.q='zzz';
sandbox.renderList();
const miss=els['list'].innerHTML.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim();
ok('a search miss says so too', /none match/.test(miss), miss);

sandbox.VIEW.q=''; sandbox.DATA.tickets=[]; sandbox.DATA.counts={};
sandbox.renderList();
ok('a genuinely empty group just says so',
   /Nothing here/.test(els['list'].innerHTML.replace(/<[^>]+>/g,' ')));

process.exitCode = fails ? 1 : 0;
console.log(fails ? `\n  ${fails} FAILED` : '\n  all UI checks pass');
