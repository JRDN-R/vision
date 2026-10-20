(() => {
 'use strict';
 // A normal link works even when a workplace browser isolates the HTTPS tab.
 // Never wait for window.opener/postMessage or claim that the app has loaded.
 document.getElementById('open').addEventListener('click',()=>{
  document.getElementById('status').textContent='In the new tab, choose this Vision-Standalone.html file if asked. You can close this tab.';
 });
})();
