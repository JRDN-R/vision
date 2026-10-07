/* Source of truth is vision-pc/model-parameters.json, injected by the builder. */
const VENTURE_PARAMETER_REGISTRY = /* VISION_PARAMETERS */ {};
function ventureParameterProfile(model=venture.settings.model){return VENTURE_PARAMETER_REGISTRY.models?.[model]||null;}
function ventureParameterLimit(model){return ventureParameterProfile(model)?.maxOutput||(/^(?:gpt-6(?:[.-]|$)|gpt-5\.6(?:[.-]|$))/.test(String(model||''))?128000:64000);}
function ventureNormalizeParameterChoice(){
 const p=ventureParameterProfile(),o=venture.settings.runOptions;
 if(!p?.pro)o.mode='auto';
 if(!(p?.efforts||['auto']).includes(o.effort))o.effort='auto';
 if(!p?.verbosity)o.verbosity='auto';
}
function venturePaintParameterDetails(){
 const p=ventureParameterProfile();
 const showDial=(id,show)=>{const input=$(id);for(const element of [document.querySelector('label[for="'+id+'"]'),input,input?.nextElementSibling])if(element)element.hidden=!show;};
 showDial('ventureEffort',!!p&&p.efforts.length>1);
 showDial('ventureVerbosity',!!p?.verbosity);
 let note=$('ventureParameterDetails');if(!note){note=document.createElement('p');note.id='ventureParameterDetails';note.className='venture-fine-print';$('ventureMaxTokens').after(note);}
 note.textContent=p?'Provider maximum: '+p.maxOutput.toLocaleString()+' output tokens. Context window: '+p.context.toLocaleString()+'. Vision uses a 512 minimum and 16,000 starting budget; these are app settings, not provider defaults. Reasoning shares the output budget.':
 'Parameter capabilities have not been verified for this exact model ID. The slider shows a Vision safety cap, not a confirmed API maximum. Only model-default reasoning controls are shown.';
}
