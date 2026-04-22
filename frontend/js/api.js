// Tiny fetch wrapper. All requests are same-origin (CloudFront proxies
// /api/* to API Gateway) so the browser replays the basic-auth header
// the user already supplied at the page load. No auth handling here.
//
// Every method throws on non-2xx; callers wrap in try/catch and surface
// the error via toast in common.js.
window.Api = {
  async get(path) {
    const r = await fetch('/api' + path, { credentials: 'same-origin' });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' on GET ' + path);
    return r.json;
  },
  async put(path, body) {
    const r = await fetch('/api' + path, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      credentials: 'same-origin'
    });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' on PUT ' + path);
    return r.json;
  },
  async post(path, body) {
    const r = await fetch('/api' + path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin'
    });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' on POST ' + path);
    return r.json;
  }
};
