const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const nodes=new Map(),timers=new Map();let timerId=0,calls=0,resolveRead,rejectRead;
function element(id){
 if(!nodes.has(id))nodes.set(id,{hidden:false,textContent:'',style:{},attrs:{},classList:{toggle(){}},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];}});
 return nodes.get(id);
}
const ctx={console,Number,String,Date,encodeURIComponent,
 venture:{epoch:1,open:true,ready:true,settings:{model:'gpt-5.6-terra'}},
 document:{hidden:false},$:element,
 setTimeout:(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;},clearTimeout:id=>timers.delete(id),
 ventureJSON:()=>{calls++;return new Promise((resolve,reject)=>{resolveRead=resolve;rejectRead=reject;});},
 ventureError:error=>error.message};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('web/venture-funding.js','utf8'),ctx);
(async()=>{
 const first=ctx.ventureLoadFunding(),second=ctx.ventureLoadFunding();
 assert.equal(calls,1,'overlapping refreshes share one request');
 resolveRead({fraction:.975,status:'available',liveMeterVersion:1});await Promise.all([first,second]);
 assert.equal(element('ventureFundingPercent').textContent,'97.5% remaining');
 assert.equal(element('ventureBattery').attrs['aria-valuenow'],'97.5');
 assert.equal(element('ventureBatteryFill').style.width,'97.5%');
 assert.equal([...timers.values()][0].ms,1000);
 assert.equal(ctx.ventureFundingPercentage(.999998),'>99.999% remaining');
 assert.equal(ctx.ventureFundingPercentage(.0000001),'<0.001% remaining');
 assert.equal(ctx.ventureFundingPercentage(0),'0.0% remaining');
 assert.equal(ctx.ventureFundingPercentage(1),'100.0% remaining');

 let read=ctx.ventureLoadFunding();rejectRead(new Error('Offline'));await read;
 assert.equal(element('ventureFundingPercent').textContent,'97.5% remaining','offline does not erase the last reading');
 assert.equal(element('ventureFundingStatus').textContent,'Not updating');

 read=ctx.ventureLoadFunding();ctx.venture.epoch++;ctx.venture.funding=null;
 resolveRead({fraction:.1,status:'low'});await read;
 assert.equal(ctx.venture.funding,null,'previous account response cannot populate a new account');

 read=ctx.ventureLoadFunding();ctx.venture.fundingSerial++;
 ctx.venture.funding={fraction:1,status:'available'};
 resolveRead({fraction:.5,status:'available'});await read;
 assert.equal(ctx.venture.funding.fraction,1,'a refresh started before calibration cannot overwrite it');

 element('ventureAccount').hidden=true;ctx.ventureScheduleFundingRefresh();assert.equal(timers.size,0);
 element('ventureAccount').hidden=false;ctx.document.hidden=true;ctx.ventureScheduleFundingRefresh();assert.equal(timers.size,0);
 ctx.document.hidden=false;ctx.venture.open=false;ctx.ventureScheduleFundingRefresh();assert.equal(timers.size,0);
 ctx.venture.funding=null;ctx.venturePaintFunding();
 assert.equal(element('ventureBatteryFill').style.width,'0%','unknown balance never looks full');
 assert.equal(element('ventureBattery').attrs['aria-valuenow'],undefined);
 console.log('Venture live funding: precision, polling, offline and account/calibration races passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
