// Reuse the tested scheduling engine. JSON enters on stdin, never command arguments.
import {openJournal} from './church_journal.mjs';
import {tools,runTool,pcoClient,SafeError} from './church_scheduler.mjs';
const nativeTools=tools.filter(t=>t.name!=='link_vision_account').map(t=>({...t,icons:[{src:'https://desktop-vjt2br2.tail385c9d.ts.net/firstupcjax/logo.svg',mimeType:'image/svg+xml',sizes:['any']}]}));
if(process.argv.includes('--tools')){
 process.stdout.write(JSON.stringify(nativeTools));
}else{
 let raw='';for await(const chunk of process.stdin){raw+=chunk;if(raw.length>32768)throw Error('Request too large');}
 let database;
 try{
  const input=JSON.parse(raw);
  if(!nativeTools.some(t=>t.name===input.name)||['send_scheduling_email','notification_job'].includes(input.name))throw new SafeError('Unsupported scheduling operation.');
  database=openJournal(input.journal);
  const db=database;
  const result=await runTool(input.name,input.arguments,{api:pcoClient({PCO_CLIENT_ID:input.credentials.client_id,PCO_SECRET:input.credentials.secret}),db,actor:input.uid});
  process.stdout.write(JSON.stringify({ok:true,result}));
 }catch(error){process.stdout.write(JSON.stringify({ok:false,error:error instanceof SafeError?error.message:'Scheduling result is uncertain. Check Planning Center before retrying.'}));}
 finally{database?.close();}
}
