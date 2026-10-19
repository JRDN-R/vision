(() => {
 const views=new Set(['vision','venture']);let popping=false;
 function rootURL(){const url=new URL(location.href);url.pathname='/standalone/';url.search='';url.hash='';return url;}
 function chosen(){const view=new URLSearchParams(location.search).get('view');return views.has(view)?view:null;}
 function paint(view){document.documentElement.dataset.workspacePage=view;document.title=view==='venture'?'Venture — Standalone':'Vision — Standalone';}
 function commit(view){
  if(!views.has(view))return;paint(view);if(popping)return;
  const url=rootURL();url.searchParams.set('view',view);
  if(url.href!==location.href)window.history[chosen()===view?'replaceState':'pushState'](null,'',url.href);
 }
 window.VisionWorkspaceRoute={rootURL,chosen,commit};paint(chosen()||'vision');
 window.addEventListener('popstate',()=>{popping=true;const view=chosen()||'vision';paint(view);try{window.dispatchEvent(new CustomEvent('vision-workspace-route',{detail:{view}}));}finally{popping=false;}});
})();
