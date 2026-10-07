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
 window.addEventListener('focus',()=>void ventureResumeFunding());
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)void ventureResumeFunding();});
}
