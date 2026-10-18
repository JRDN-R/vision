/* Hosted page routes share the running session so switching cannot interrupt
 * uploads, lose drafts, or reset the board. The inactive page is not painted. */
(() => {
 const views=new Set(['vision','venture']);
 let popping=false;
 function rootURL(){
  const url=new URL(location.href);
  url.pathname=url.pathname.replace(/\/venture(?:\/index\.html)?\/?$/,'/').replace(/\/(?:Vision|index)\.html$/,'/');
  url.search='';url.hash='';return url;
 }
 function chosen(){
  if(/\/venture(?:\/index\.html)?\/?$/.test(location.pathname))return 'venture';
  const view=new URLSearchParams(location.search).get('view');return views.has(view)?view:null;
 }
 function paint(view){
  document.documentElement.dataset.workspacePage=view;
  document.title=view==='venture'?'Venture — by Jordan R.':'Vision — advance AI prompt generator';
  for(const name of ['application-name','apple-mobile-web-app-title'])document.querySelector('meta[name="'+name+'"]')?.setAttribute('content',view==='venture'?'Venture':'Vision');
  const manifest=document.querySelector('link[rel="manifest"]');
  if(manifest&&/^https?:$/.test(location.protocol))manifest.href=new URL(view==='venture'?'venture/manifest.webmanifest':'manifest.webmanifest',rootURL()).href;
 }
 function commit(view){
  if(!views.has(view))return;paint(view);
  if(!/^https?:$/.test(location.protocol)||popping)return;
  const url=rootURL();url.search=location.search;url.hash=location.hash;
  if(view==='venture'){url.pathname+='venture/';url.searchParams.delete('view');}
  else url.searchParams.set('view','vision');
  const method=chosen()===view?'replaceState':'pushState';
  if(url.href!==location.href)window.history[method](null,'',url.href);
 }
 window.VisionWorkspaceRoute={rootURL,chosen,commit};
 paint(chosen()||'vision');
 window.addEventListener('popstate',()=>{
  popping=true;const view=chosen()||'vision';paint(view);
  try{window.dispatchEvent(new CustomEvent('vision-workspace-route',{detail:{view}}));}finally{popping=false;}
 });
})();
