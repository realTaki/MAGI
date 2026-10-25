// Owner-only, authenticated routes supplied by DSH Connection. Never publish child
// login credentials in status JSON, tool output, or errors; only an explicit user
// navigation receives them. POST supports desktop's fetch-only IPC carrier.
export const VIEW_PATH = '/api/eva/view';
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

export function registerParentView(ctx, manager) {
  ctx.connection.fetch.register({ path: VIEW_PATH, methods: ['GET'], requestBody: 'buffered',
    async fetch() {
      const { nodes } = await manager.status();
      return Response.json({ mode: 'parent', nodes: nodes.map(({ handle, status, workspace, mainSessionId }) =>
        ({ handle, status, workspace, mainSessionId })) }, { headers });
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
