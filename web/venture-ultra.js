/* Controls for the installed 27B profile. Hardware tuning remains on the PC. */
const VENTURE_ULTRA_ID='qwen-ultra-local';
const VENTURE_ULTRA_DEFAULTS={contextTokens:8192,thinking:true,performance:'auto',temperature:.6,topP:.95,topK:20,minP:0,repeatPenalty:1,presencePenalty:0,seed:-1};
const VENTURE_ULTRA_DIALS=[['temperature','Temperature',0,2,.05],['topP','Top P',.01,1,.01],['topK','Top K',0,100,1],['minP','Min P',0,1,.01],['repeatPenalty','Repeat penalty',.8,1.5,.01],['presencePenalty','Presence penalty',-2,2,.1]];
function ventureUltraOptions(value){return Object.fromEntries(Object.entries(VENTURE_ULTRA_DEFAULTS).map(([k,v])=>[k,value?.[k]??v]));}
function ventureUltraCapability(){return venture.localCapability?.ultra||null;}
function ventureUltraControls(){return '<section id="ventureUltraControls" class="venture-ultra-controls" hidden><p class="venture-ultra-label">27B · Ultra-Uncensored Heretic · Q4_K_M</p><label class="venture-ultra-check"><input id="ventureUltraThinking" type="checkbox">Multistep thinking</label><p class="venture-fine-print">Thinking shares your output budget and can take longer. “Uncensored” is the publisher’s description; refusals and mistakes can still occur.</p><label for="ventureUltraContext">Context window</label><select id="ventureUltraContext"><option value="4096">4,096 tokens</option><option value="8192">8,192 tokens</option></select><label for="ventureUltraPerformance">Performance</label><select id="ventureUltraPerformance"><option value="auto">Fastest measured profile</option><option value="cpu">Measured CPU profile</option></select><p id="ventureUltraHardware" class="venture-fine-print"></p><details><summary>Sampling controls</summary>'+VENTURE_ULTRA_DIALS.map(([k,label,min,max,step])=>'<label class="venture-dial-label" for="ventureUltra_'+k+'">'+label+'<output id="ventureUltraValue_'+k+'"></output></label><input id="ventureUltra_'+k+'" type="range" min="'+min+'" max="'+max+'" step="'+step+'">').join('')+'<label for="ventureUltraSeed">Seed (−1 = random)</label><input type="number" id="ventureUltraSeed" min="-1" max="2147483647" step="1"></details><p class="venture-fine-print">File reading, code writing and the installed image worker are available through Venture. Web search, code execution and image generation are not integrated with this model.</p></section>';}
function venturePaintUltra(){
 const host=$('ventureUltraControls');if(!host)return;
 host.hidden=venture.settings.model!==VENTURE_ULTRA_ID;if(host.hidden)return;
 const o=ventureUltraOptions(venture.settings.qwenUltra),cap=ventureUltraCapability();
 const contexts=cap?.contexts||[4096,8192];
 $('ventureUltraContext').innerHTML=[...new Set([...contexts,o.contextTokens])].sort((a,b)=>a-b).map(n=>'<option value="'+n+'"'+(!contexts.includes(n)?' disabled':'')+'>'+n.toLocaleString()+' tokens'+(!contexts.includes(n)?' · needs tuning':'')+'</option>').join('');
 $('ventureUltraContext').value=o.contextTokens;$('ventureUltraThinking').checked=o.thinking;$('ventureUltraPerformance').value=o.performance;
 $('ventureUltraSeed').value=o.seed;
 for(const [k] of VENTURE_ULTRA_DIALS){$('ventureUltra_'+k).value=o[k];$('ventureUltraValue_'+k).textContent=o[k];}
 const p=cap?.performance?.[o.performance];
 $('ventureUltraHardware').textContent=p?p.threads+' CPU threads · '+p.gpuLayers+' GPU layers · '+p.benchmark.generationTokensPerSecond.toFixed(2)+' measured tokens/s. Context is limited to the tested server profile.':'Install and tune Ultra on FUPCJ to measure performance and enable it.';
}
function ventureReadUltra(){
 const value={contextTokens:Number($('ventureUltraContext').value),thinking:$('ventureUltraThinking').checked,performance:$('ventureUltraPerformance').value,seed:Number($('ventureUltraSeed').value)};
 for(const [k] of VENTURE_ULTRA_DIALS){value[k]=Number($('ventureUltra_'+k).value);$('ventureUltraValue_'+k).textContent=value[k];}
 return value;
}
function ventureUltraEstimate(){
 const o=ventureUltraOptions(venture.settings.qwenUltra),b=ventureUltraCapability()?.performance?.[o.performance]?.benchmark;
 if(!b||![b.promptTokensPerSecond,b.generationTokensPerSecond].every(n=>Number.isFinite(n)&&n>0)||!Number.isFinite(b.loadSeconds))return null;
 const output=venture.settings.maxOutputTokens,seconds=b.loadSeconds+Math.max(1,o.contextTokens-output-64)/b.promptTokensPerSecond+output/b.generationTokensPerSecond;
 return [Math.max(5,Math.round(seconds*.65)),Math.max(10,Math.round(seconds*1.6))];
}
function ventureUltraCoffee(range){
 if(!Array.isArray(range)||range.length!==2||!range.every(n=>Number.isFinite(n)&&n>=0)||range[1]<range[0])return '';
 const now=Date.now(),clock=n=>new Date(now+n*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
 return 'Grab a coffee — allow about '+ventureLocalDuration(range[0])+'–'+ventureLocalDuration(range[1])+', around '+clock(range[0])+'–'+clock(range[1])+'.';
}
function venturePaintUltraWait(){
 const el=$('ventureUltraWait');if(!el)return;el.hidden=venture.settings.model!==VENTURE_ULTRA_ID;if(el.hidden)return;
 const cap=ventureUltraCapability(),active=venture.runs?.find(ventureActive);
 if(!cap?.enabled){el.textContent='Qwen Ultra needs installation and tuning on FUPCJ before it can run.';return;}
 if(active){el.textContent='Ultra can take a while. Your accepted request keeps running when you leave; its current estimate appears with the response.';return;}
 const range=ventureUltraEstimate();
 el.textContent=(range?ventureUltraCoffee(range)+' Based on the measured speed and full token budget; answers may finish sooner.':'Ultra can take a while. Timing becomes available after the PC benchmark.')+(venture.files?.length||$('ventureIncludeBoard')?.checked?' Images, files and board preparation add time.':'')+' Queueing and other PC work can extend this estimate.';
}
