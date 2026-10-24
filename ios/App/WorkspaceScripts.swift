import Foundation

// Keep the original web workspace and its action handlers. Only these native
// affordances are added; the normal website is not redesigned or modified.
enum WorkspaceScripts {
    static let bridge = #"""
    (() => {
      if (window.top !== window || window.VisionNative) return;
      const post = body => window.webkit.messageHandlers.vision.postMessage(body);
      Object.defineProperty(window, 'VisionNative', {value: Object.freeze({
        isNative: true,
        signIn: () => post({type:'signIn'}),
        openDownloads: () => post({type:'downloads'}),
        openSettings: () => post({type:'settings'}),
        reload: () => post({type:'reload'}),
        adoptVortexJob: jobID => post({type:'vortexJob',jobID})
      }), configurable:false, writable:false});
    })();
    """#

    static let sidebar = #"""
    (() => {
      if (window.top !== window || !window.VisionNative?.isNative) return;
      if (window.__visionNativeSidebar) { window.__visionNativeSidebar(); return; }
      const icons = {
        downloads:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
        settings:'<path d="M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6"/>',
        reload:'<path d="M20 7v5h-5M20 12a8 8 0 1 0-2 5"/>'
      };
      const actions = [
        ['downloads','iPhone downloads',() => window.VisionNative.openDownloads()],
        ['settings','iPhone settings',() => window.VisionNative.openSettings()],
        ['reload','Reload workspace',() => window.VisionNative.reload()]
      ];
      const add = (container, prefix, className) => {
        if (!container) return;
        for (const [key,label,action] of actions) {
          const id = prefix+'-'+key;
          if (document.getElementById(id)) continue;
          const button = document.createElement('button');
          button.id=id; button.type='button'; button.className=className;
          button.setAttribute('data-vision-native-action',key);
          button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'+icons[key]+'</svg><span class="workspace-nav-label">'+label+'</span>';
          button.addEventListener('click',event => {
            event.preventDefault();
            // Use each workspace's existing dismissal behavior, not a new menu.
            if (typeof window.navigationCloseMenu === 'function') window.navigationCloseMenu(false);
            else {
              const toggle=document.getElementById('workspaceMenuButton')||document.getElementById('menuButton');
              if(toggle?.getAttribute('aria-expanded')==='true') toggle.click();
            }
            Promise.resolve(action()).catch(() => {});
          });
          container.appendChild(button);
        }
      };
      const install = () => {
        const installer=document.getElementById('downloadAppBtn');
        if(installer) installer.hidden=true;
        add(document.querySelector('#workspaceMenu .top-actions'),'vision-iphone','');
        add(document.querySelector('#ventureSidebar .workspace-menu-footer'),'venture-iphone','workspace-news-link');
        add(document.querySelector('#navigation .nav-footer'),'vortex-iphone','text-button');
        if (!document.getElementById('vision-native-controls-style') && document.head) {
          const style=document.createElement('style'); style.id='vision-native-controls-style';
          style.textContent='[data-vision-native-action] svg{width:24px;height:24px;flex-shrink:0}#ventureSidebar [data-vision-native-action],#navigation [data-vision-native-action]{display:flex;align-items:center;gap:12px;width:100%;text-align:left}#ventureSidebar [data-vision-native-action]{padding:12px 0}#navigation .nav-footer{flex-wrap:wrap}#navigation [data-vision-native-action]{flex-basis:100%}';
          document.head.appendChild(style);
        }
      };
      window.__visionNativeSidebar=install;
      install();
      // The portable workspace creates its sidebars during JS initialization.
      // Debounce mutations, and disconnect while inserting our own controls.
      let scheduled=false;
      const observer=new MutationObserver(() => {
        if (scheduled) return; scheduled=true;
        queueMicrotask(() => {
          scheduled=false; observer.disconnect(); install();
          observer.observe(document.documentElement,{childList:true,subtree:true});
        });
      });
      observer.observe(document.documentElement,{childList:true,subtree:true});
      window.addEventListener('pagehide',()=>observer.disconnect(),{once:true});
      window.addEventListener('pageshow',()=>{ install(); observer.observe(document.documentElement,{childList:true,subtree:true}); });
    })();
    """#
}
