/** Authenticated, framework-free API client. Never put bearer tokens in URLs. */
export const DEFAULT_BACKEND = 'https://desktop-vjt2br2.tail385c9d.ts.net';
export function createTitleEyesClient({getToken, backend = DEFAULT_BACKEND, fetchImpl = globalThis.fetch} = {}) {
  if (typeof getToken !== 'function') throw new TypeError('Provide getToken() for your Vision account.');
  const base = backend.replace(/\/$/, '') + '/api/title-eyes/jobs';
  const idPath = id => { if (!/^[a-f0-9]{24}$/.test(id)) throw new Error('Invalid title request.'); return '/' + id; };
  async function request(path, {method = 'GET', body, signal} = {}) {
    const controller = new AbortController(), abort = () => controller.abort();
    if (signal?.aborted) abort(); else signal?.addEventListener('abort', abort, {once:true});
    const timer = setTimeout(abort, 45000);
    try {
      const token = await getToken();
      if (!token) throw new Error('Sign in with your Vision account first.');
      const response = await fetchImpl(base + path, {method, body, signal:controller.signal,
        headers:{Authorization:'Bearer ' + token, Accept:'application/json'}, credentials:'omit', cache:'no-store', referrerPolicy:'no-referrer'});
      const value = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(response.status === 404 && method === 'POST' ? 'FUPCJ Server needs the title-eyes update. Run the server updater, then try again.' :
          typeof value.error === 'string' ? value.error : 'The title request could not finish.');
        error.status = response.status; throw error;
      }
      return value;
    } catch (error) {
      if (error.name === 'AbortError' && !signal?.aborted) throw new Error('FUPCJ Server did not reply in time. Reconnect to check your request.');
      if (error instanceof TypeError) throw new Error('Cannot reach FUPCJ Server. Make sure it is online, then try again.');
      throw error;
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
  }
  const get = (id, options) => request(idPath(id), options);
  const remove = (id, options = {}) => request(idPath(id), {...options, method:'DELETE'});
  async function submit(image, {requestId = crypto.randomUUID(), signal} = {}) {
    if (!(image instanceof Blob) || !image.size || image.size > 10 * 1024 * 1024) throw new Error('Choose an image smaller than 10 MB.');
    const form = new FormData(); form.append('image', image, image.name || 'slide.jpg'); form.append('requestId', requestId);
    return request('', {method:'POST', body:form, signal});
  }
  async function extract(image, {signal, onProgress = () => {}, onAccepted = () => {}, ...options} = {}) {
    let job = await submit(image, {...options, signal}); onAccepted(job);
    while (job.status === 'queued' || job.status === 'processing') {
      onProgress(job);
      await new Promise((resolve, reject) => {
        const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
        const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, 2000);
        if (signal?.aborted) abort(); else signal?.addEventListener('abort', abort, {once:true});
      });
      job = await get(job.id, {signal});
    }
    if (job.status !== 'complete') throw new Error(job.error || 'The title could not be read.');
    return job;
  }
  return {submit, get, remove, extract};
}
