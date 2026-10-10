// Same Firebase app name and local persistence as web/auth.js. That module is
// board-bound; this small adapter deliberately does not load the Vision board.
export const FIREBASE_CONFIG = Object.freeze({apiKey:'AIzaSyDh1AHhi41cAXcSFnvFkfeZWmxc8gI0zSg',authDomain:'visionboard-api.firebaseapp.com',projectId:'visionboard-api',appId:'1:150865729216:web:554423d0c7602d3a47bf25'});
export const BACKEND = 'https://desktop-vjt2br2.tail385c9d.ts.net';
let sdk, auth, loading, onUser = () => {}, epoch = 0, currentUser = null;
const requests = new Set();
export function user() { return currentUser; }
export function accountEpoch() { return epoch; }
function changed(next) {
  if (currentUser?.uid === next?.uid && (currentUser || next)) { currentUser = next; return; }
  epoch++;
  for (const request of requests) request.abort();
  requests.clear();
  currentUser = next;
  onUser(next);
}
export async function initAuth(callback) {
  onUser = callback;
  if (!loading) loading = (async () => {
    const bundled = window.VisionFirebaseSDK;
    if (!bundled) throw new Error('Sign-in could not load. Refresh the page to try again.');
    sdk = bundled.auth;
    const app = bundled.app.getApps().find(item => item.name === 'vision-account-login') || bundled.app.initializeApp(FIREBASE_CONFIG, 'vision-account-login');
    // Match Vision's durable session policy without initializing the popup helper
    // during page load. Login popups are opened only by an explicit user action.
    auth = sdk.initializeAuth(app, {persistence:[sdk.indexedDBLocalPersistence,sdk.browserLocalPersistence]});
    sdk.useDeviceLanguage(auth);
    await auth.authStateReady();
    changed(auth.currentUser);
    sdk.onAuthStateChanged(auth, changed);
    return auth;
  })().catch(error => { loading = null; throw error; });
  return loading;
}
export async function signOut() { if (auth) await sdk.signOut(auth); }
export function authError(error) {
  return ({'auth/popup-blocked':'Allow the sign-in popup, then try again.','auth/popup-closed-by-user':'Sign-in was closed. Try again when ready.','auth/cancelled-popup-request':'A sign-in window is already open.','auth/invalid-credential':'Email or password is incorrect.','auth/wrong-password':'Email or password is incorrect.','auth/user-not-found':'Email or password is incorrect.','auth/email-already-in-use':'This email already has a Vision account. Sign in instead.','auth/invalid-email':'Enter a valid email address.','auth/weak-password':'Use a password with at least 6 characters.','auth/network-request-failed':'Check your connection and try again.','auth/too-many-requests':'Too many attempts. Wait a little and try again.'})[error?.code] || error?.message || 'Sign-in could not finish.';
}
export async function request(path, {method = 'GET', body, timeout = 25000, binary = false} = {}) {
  const sharedProfile = method === 'GET' && ['/venture/profile', '/venture/profile/avatar'].includes(path);
  if ((!/^\/vortex(?:\/|$)/.test(path) && !sharedProfile) || path.includes('..')) throw new Error('Invalid media request.');
  const identity = currentUser, startEpoch = epoch;
  if (!identity?.uid) throw new Error('Sign in to use Vortex.');
  const controller = new AbortController();
  requests.add(controller);
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const token = await identity.getIdToken();
    if (epoch !== startEpoch) throw new Error('The signed-in account changed.');
    const headers = {Authorization:'Bearer ' + token, Accept:binary ? '*/*' : 'application/json'};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(BACKEND + '/api' + path, {method, headers, body:body === undefined ? undefined : JSON.stringify(body), signal:controller.signal, credentials:'omit', cache:'no-store', referrerPolicy:'no-referrer'});
    if (epoch !== startEpoch) throw new Error('The signed-in account changed.');
    if (binary && response.ok) {
      const value = await response.blob();
      if (epoch !== startEpoch) throw new Error('The signed-in account changed.');
      return value;
    }
    const value = await response.json().catch(() => ({}));
    if (epoch !== startEpoch) throw new Error('The signed-in account changed.');
    if (!response.ok) {
      const message = typeof value.error === 'string' ? value.error : value.error?.message || value.message;
      const error = new Error(message || (response.status === 404 ? 'Vortex needs the latest FUPCJ Server update.' : response.status === 401 ? 'Your session could not be verified. Sign in again.' : 'The media request could not finish.'));
      error.status = response.status;
      throw error;
    }
    return value;
  } catch (error) {
    if (epoch !== startEpoch) throw new Error('The signed-in account changed.');
    if (error.name === 'AbortError') throw new Error('FUPCJ Server did not reply in time. Accepted jobs may still be running; refresh to reconnect.');
    if (error instanceof TypeError) throw new Error('Cannot reach FUPCJ Server. Check your connection and make sure the server is online.');
    throw error;
  } finally { clearTimeout(timer); requests.delete(controller); }
}
