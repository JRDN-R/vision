/* Funding reads are independent of the selected conversation, so this also
 * refreshes while another device or another conversation is consuming credits.
 */
function ventureFundingVisible(){return venture.open&&venture.ready&&!document.hidden&&!$('ventureAccount').hidden;}
function ventureStopFundingRefresh(){clearTimeout(venture.fundingTimer);venture.fundingTimer=null;}
function ventureScheduleFundingRefresh(){ventureStopFundingRefresh();if(ventureFundingVisible())venture.fundingTimer=setTimeout(()=>void ventureLoadFunding(),1000);}
async function ventureLoadFunding(){
 const epoch=venture.epoch;
 if(venture.fundingSaving)return;
 if(venture.fundingRequest?.epoch===epoch)return venture.fundingRequest.promise;
 const serial=venture.fundingSerial=(venture.fundingSerial||0)+1;
 const request={epoch,promise:null};venture.fundingRequest=request;
 request.promise=(async()=>{
  try{
   const data=await ventureJSON('/venture/funding?model='+encodeURIComponent(venture.settings.model));
   if(epoch===venture.epoch&&serial===venture.fundingSerial){venture.funding=data;venture.fundingError='';venturePaintFunding();}
  }catch(error){
   if(epoch===venture.epoch&&serial===venture.fundingSerial){venture.fundingError=ventureError(error);venturePaintFunding();}
  }finally{
   if(venture.fundingRequest===request){venture.fundingRequest=null;ventureScheduleFundingRefresh();}
  }
 })();
 return request.promise;
}
function ventureFundingPercentage(fraction){
 const percent=fraction*100;
 if(percent>0&&percent<.001)return '<0.001% remaining';
 if(percent<100&&percent>99.999)return '>99.999% remaining';
 return percent.toLocaleString('en-US',{minimumFractionDigits:1,maximumFractionDigits:3})+'% remaining';
}
function venturePaintFunding(){
 const data=venture.funding,fraction=Number.isFinite(data?.fraction)?Math.max(0,Math.min(1,data.fraction)):null;
 const battery=$('ventureBattery'),fill=$('ventureBatteryFill'),label=fraction===null?'Balance unavailable':ventureFundingPercentage(fraction);
 battery.classList.toggle('unknown',fraction===null);battery.classList.toggle('empty',fraction===0);
 battery.classList.toggle('partial',data?.coverage==='partial');
 fill.style.width=(fraction===null?0:fraction*100)+'%';
 fill.style.background=fraction===null?'#58605b':fraction<=.2?'#ed7975':fraction<.55?'#d6c16c':'var(--accent)';
 if(fraction===null)battery.removeAttribute('aria-valuenow');else battery.setAttribute('aria-valuenow',String(fraction*100));
 battery.setAttribute('aria-valuetext',(data?.activeRuns?'Live estimate: ':'Estimated balance: ')+label+(venture.fundingError?' (not updating)':''));
 $('ventureFundingPercent').textContent=label;
 $('ventureFundingStatus').textContent=venture.fundingError?'Not updating':data?.status==='exhausted'?'Credits exhausted':data?.activeRuns?'Live estimate':data?.coverage==='partial'&&fraction!==null?'Partial estimate':({available:'Available',low:'Running low','estimated-empty':'Estimate depleted',uncalibrated:'Not calibrated','no-key':'No saved API key'})[data?.status]||'Unavailable';
 const usage=data?.usage,parts=[];
 if(usage?.models?.length)parts.push(usage.models.map(model=>typeof ventureModelIdentity==='function'?ventureModelIdentity(model).name:model).join(', '));
 if(Number.isFinite(usage?.inputTokens))parts.push(usage.inputTokens.toLocaleString()+' input');
 if(Number.isFinite(usage?.outputTokens))parts.push(usage.outputTokens.toLocaleString()+' output');
 if(usage?.cachedTokens)parts.push(usage.cachedTokens.toLocaleString()+' cached');
 if(usage?.reasoningTokens)parts.push(usage.reasoningTokens.toLocaleString()+' reasoning (included in output)');
 const detail=$('ventureFundingUsage');detail.hidden=!parts.length;detail.textContent=(usage?.scope==='active'?'In progress: ':'Last response: ')+parts.join(' · ')+(usage?.kind==='live-estimate'?' · provisional tokens':'');
 $('ventureFundingUpdated').textContent=venture.fundingError?'Could not refresh. Showing the last estimate. '+venture.fundingError:data?.updatedAt?'Updated '+new Date(data.updatedAt*1000).toLocaleString():data?.status==='no-key'?'Save your API key, then set a starting balance.':'Set a starting balance to activate the meter.';
 $('ventureFundingCoverage').textContent=(data?.issues?.length?data.issues.join(' ')+' ':'')+(data&&!data.liveMeterVersion?'Update FUPCJ Server to enable live token accounting. ':'')+'Tracks Vision activity only. Other apps, credit expiry, refunds, or unreported charges can change the official balance.';
}

/* OpenAI checkout is external: a link click is never evidence of a purchase.
 * Returning opens the completed-top-up form automatically, with no opt-in.
 */
function ventureOpenBalance(kind='set') {
 ventureClosePopover('account');venture.balanceKind=kind;venture.balanceConnection=venture.funding?.connectionId;
 $('ventureBalanceHeading').textContent=kind==='add'?'Funds added':'Set current balance';
 $('ventureBalanceHelp').textContent=kind==='add'?'Enter the amount you successfully added on OpenAI. It will be added to your meter when you save. Vision cannot read the payment from the billing page.':'Enter your current OpenAI balance to correct the meter. This replaces the estimate.';
 $('ventureBalanceAmount').min=kind==='add'?'0.01':'0';
 $('ventureBalanceAmount').value=venture.calibration?.kind===kind?venture.calibration.amount:'';
 $('ventureBalanceStatus').textContent='';$('ventureBalanceSubmit').textContent=kind==='add'?'Save added funds':'Set balance';
 $('ventureBalanceDialog').showModal();$('ventureBalanceAmount').focus();
}
function ventureStartFunding() {
 venture.fundingReturn={uid:venture.uid,started:Date.now()};ventureRemember('funding-return',venture.fundingReturn);
 // Also prepare the form behind the billing tab for browsers without focus events.
 const kind=venture.funding?.status==='uncalibrated'?'set':'add';
 ventureOpenBalance(kind);
}
async function ventureResumeFunding() {
 if(document.hidden||!venture.open||!venture.ready||!venture.fundingReturn)return;
 const epoch=venture.epoch,pending=venture.fundingReturn;
 if(pending.uid!==venture.uid){venture.fundingReturn=null;ventureRemember('funding-return',null);return;}
 await ventureLoadFunding();if(epoch!==venture.epoch||!venture.open||venture.fundingReturn!==pending)return;
 if(!$('ventureBalanceDialog').open)ventureOpenBalance(venture.funding?.status==='uncalibrated'?'set':'add');
}
function ventureInstallFunding() {
 $('ventureAddFunding').onclick=ventureStartFunding;
 $('ventureCalibrate').onclick=()=>ventureOpenBalance('set');
 $('ventureBalanceCancel').onclick=()=>$('ventureBalanceDialog').close();
 $('ventureBalanceForm').onsubmit=event=>{event.preventDefault();void ventureSaveBalance();};
 $('ventureBalanceDialog').addEventListener('close',()=>{venture.fundingReturn=null;ventureRemember('funding-return',null);$('ventureBalanceAmount').value='';});
 window.addEventListener('focus',()=>{if(ventureFundingVisible())void ventureLoadFunding();void ventureResumeFunding();});
 window.addEventListener('online',()=>{if(ventureFundingVisible())void ventureLoadFunding();});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)ventureStopFundingRefresh();else{if(ventureFundingVisible())void ventureLoadFunding();void ventureResumeFunding();}});
}
