// Owner-only, authenticated routes supplied by DSH Connection. Never publish child
// login credentials in status JSON, tool output, or errors; only an explicit user
// navigation receives them. POST open supports desktop's fetch-only IPC carrier.
import { createHash } from 'node:crypto';
import { validateName } from './runtime.js';
import { safePreview } from './message-preview.js';

export const VIEW_PATH = '/api/eva/view';
export const WAKE_PATH = '/api/eva/wake';
export const CREATE_PATH = '/api/eva/create';
export const OPEN_PATH = '/api/eva/open';
export const EMBED_PATH = '/eva/embed';

// file:// desktop parents are cross-site to the loopback child. Their frames
// cannot use the native SameSite=Strict cookie. Mint a partitioned cookie ONLY
// on this explicit embed login; keep native signature, lifetime and HttpOnly.
// Secure cookies are supported on Chromium's trustworthy loopback origins.
export function registerEmbeddedLogin(ctx) {
  ctx.effect(() => ctx.webServer.register({ kind: 'exact', path: EMBED_PATH,
    handler(req, res) {
      const url = new URL(req.url, 'http://dsh.invalid');
      url.pathname = '/';
      const allowed = ctx.connection.authorizeIndex({ method: req.method, url: url.pathname + url.search, headers: req.headers }, {
        writeHead(status, nativeHeaders = {}) {
          const out = { ...nativeHeaders };
          if (status === 303) out.location = '/#eva-main';
          const cookie = out['set-cookie'];
          if (cookie) out['set-cookie'] = cookie.replace('; SameSite=Strict', '; SameSite=None')
            + (cookie.includes('; Secure') ? '' : '; Secure') + '; Partitioned';
          res.writeHead(status, out);
        },
        end(body) { res.end(body); },
      });
      if (allowed) { res.writeHead(303, { ...headers, Location: '/#eva-main' }); res.end(); }
    },
  }));
}
const headers = { 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' };
const safeNode = ({ handle, status, workspace, mainSessionId }) => ({ handle, status, workspace, mainSessionId });
// A page cache must notice a stop+restart even between inventory polls. Publish
// an opaque process generation, never the origin, PID or credential-bearing URL.
function instanceMetadata({ handle, status, pid, origin }) {
  if (status !== 'running' || !Number.isSafeInteger(pid) || pid <= 0 || typeof origin !== 'string' || !origin) return {};
  return { instanceId: createHash('sha256').update(JSON.stringify([handle, pid, origin])).digest('hex').slice(0, 24) };
}
const wakeFailure = (status, code, error) => Response.json({ code, error }, { status, headers });

export function registerParentView(ctx, manager) {
  // Exact Fetch routes receive no Peer. Re-admit credentials and require the
  // actual browserPeer, not merely an owner principal (API owners are distinct).
  function browserRejection(request) {
    try {
      const admission = ctx.connection.admit({ method: request.method, url: request.url,
        headers: Object.fromEntries(request.headers) });
      if (ctx.connection.browserPeer && admission.peer === ctx.connection.browserPeer) return;
    } catch { /* Fail closed, including credential-less desktop transports. */ }
    return wakeFailure(403, 'forbidden', 'Use the signed-in parent Host web page. Agent callers must use the EVA tool.');
  }
  let creating;
  ctx.connection.fetch.register({ path: CREATE_PATH, methods: ['POST'], requestBody: 'buffered',
    async fetch(request) {
      const rejection = browserRejection(request);
      if (rejection) return rejection;
      if (new URL(request.url).search) return wakeFailure(400, 'invalid', 'New Bots use an automatically allocated EVA handle.');
      // A double click or concurrent browser call shares one allocation. Never
      // tie process ownership to a fetch abort: manager.dispose owns shutdown.
      if (!creating) {
        creating = Promise.resolve().then(() => manager.start(undefined)).then(safeNode);
        creating.finally(() => { creating = undefined; }).catch(() => {});
      }
      try { return Response.json(await creating, { headers }); }
      catch { return wakeFailure(503, 'start-failed', 'EVA could not start. Check the runtime and retry explicitly.'); }
    },
  });
  ctx.connection.fetch.register({ path: WAKE_PATH, methods: ['POST'], requestBody: 'buffered',
    async fetch(request) {
      const rejection = browserRejection(request);
      if (rejection) return rejection;
      const handles = new URL(request.url).searchParams.getAll('handle');
      const handle = handles[0];
      try {
        if (handles.length !== 1) throw new Error('Explicit handle required.');
        validateName(handle);
      } catch { return wakeFailure(400, 'invalid', 'Choose one existing EVA handle.'); }
      try {
        // Bare status(handle) describes unknown handles as not-started too.
        // Only current inventory establishes a known persisted identity.
        const { nodes } = await manager.status();
        const node = nodes.find((node) => node.handle === handle);
        if (!node) return wakeFailure(404, 'unknown', 'This EVA is no longer known. Refresh the EVA list.');
        if (node.status === 'stopping') return wakeFailure(409, 'busy', 'EVA is stopping. Wait before waking it again.');
        // Share the tool's fixed-handle domain operation and pending lock.
        // Do not pass request/navigation abort: plugin unload retains startup
        // ownership through its existing manager.dispose() registration.
        return Response.json(safeNode(await manager.start(undefined, handle)), { headers });
      } catch {
        // A stop may race inventory. Never expose raw child failures or URLs.
        try {
          const { nodes } = await manager.status();
          if (nodes.some((node) => node.handle === handle && node.status === 'stopping')) {
            return wakeFailure(409, 'busy', 'EVA is stopping. Wait before waking it again.');
          }
        } catch { /* The fixed failure below covers unavailable inventory too. */ }
        return wakeFailure(503, 'start-failed', 'EVA could not start. Retry after checking that the node is not in use and the runtime is available.');
      }
    },
  });
  ctx.connection.fetch.register({ path: VIEW_PATH, methods: ['GET'], requestBody: 'buffered',
    async fetch() {
      const { nodes } = await manager.status();
      return Response.json({ mode: 'parent', nodes: nodes.map((node) =>
        ({ ...safeNode(node), ...instanceMetadata(node), preview: safePreview(node.preview) })) }, { headers });
    },
  });
  ctx.connection.fetch.register({ path: OPEN_PATH, methods: ['GET', 'POST'], requestBody: 'buffered',
    async fetch(request) {
      const handle = new URL(request.url).searchParams.get('handle');
      try {
        const url = manager.launchUrl(handle);
        if (!url) return new Response('EVA is not running.', { status: 409, headers });
        if (request.method === 'POST') {
          const embedded = new URL(url);
          embedded.pathname = EMBED_PATH;
          return Response.json({ url: embedded.href }, { headers });
        }
        return new Response(null, { status: 303, headers: { ...headers, Location: url } });
      } catch { return new Response('Unknown EVA handle.', { status: 404, headers }); }
    },
  });
}

export function registerChildView(ctx, readMainSession) {
  ctx.connection.fetch.register({ path: VIEW_PATH, methods: ['GET'], requestBody: 'buffered',
    async fetch() {
      const mainSessionId = readMainSession();
      if (!mainSessionId) return new Response('EVA main conversation is initializing.', { status: 503, headers });
      return Response.json({ mode: 'child', mainSessionId }, { headers });
    },
  });
}
