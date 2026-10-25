window.__ModuleLoader__.load({
  id: '@local/eva',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const dicts = {
      en: { opening: 'Opening main conversation…', conversation: 'Main conversation', error: 'Unable to open this EVA.', retry: 'Retry', online: 'Online', offline: 'Offline', starting: 'Starting', stopping: 'Stopping', failed: 'Failed', offlineHint: 'This node is not running under this parent Host. Its data is retained. Ask the assistant to start this EVA when needed.', waiting: 'Waiting for the node status to update…', workspace: 'Data directory' },
      zh: { opening: '正在打开主会话…', conversation: '主会话', error: '无法打开此 EVA。', retry: '重试', online: '在线', offline: '离线', starting: '启动中', stopping: '停止中', failed: '异常退出', offlineHint: '此节点未在当前父实例中运行，数据仍保留。需要使用时，可以让助手启动这个 EVA。', waiting: '正在等待节点状态更新…', workspace: '数据目录' },
    };
    const send = (path, init) => (globalThis.__DSH_TRANSPORT__?.fetch ?? globalThis.fetch.bind(globalThis))(path, init);
    async function readView(signal) {
      const response = await send('api/eva/view', { signal, cache: 'no-store' });
      if (!response.ok) throw new Error('EVA view unavailable');
      return response.json();
    }
    function Icon({ size = 18 }) {
      return h('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, 'aria-hidden': true },
        h('path', { d: 'M12 3 21 19H3L12 3Z' }),
        h('circle', { cx: 12, cy: 12, r: 2.3 }), h('path', { d: 'M12 14.3V19M10 10.8 7 9M14 10.8 17 9' }));
    }
    return {
      name: 'eva', inject: ['slots', 'locale', 'uiWorkspace', 'sessions'],
      apply(ctx) {
        for (const [locale, dict] of Object.entries(dicts)) ctx.effect(() => ctx.locale.register('eva', locale, dict));
        const t = ctx.locale.bind('eva');
        function nodeState(node) {
          if (node.status === 'running') return node.mainSessionId ? 'online' : 'starting';
          return ['starting', 'stopping', 'failed'].includes(node.status) ? node.status : 'offline';
        }
        // The parent stays on its own Host. Only an online node opens a frame.
        function MainConversation(node) {
          const { handle, workspace, mainSessionId } = node;
          const state = nodeState(node);
          const online = state === 'online';
          React.useSyncExternalStore((listener) => ctx.locale.subscribe(listener), () => ctx.locale.getSnapshot());
          const [src, setSrc] = React.useState('');
          const [error, setError] = React.useState(false);
          const [attempt, setAttempt] = React.useState(0);
          React.useEffect(() => {
            setSrc(''); setError(false);
            if (!online) return;
            const controller = new AbortController();
            async function open() {
              try {
                // Prepare through authenticated transport; iframe navigation
                // cannot carry the parent's IPC / Authorization credential.
                const response = await send(`api/eva/open?handle=${encodeURIComponent(handle)}`, { method: 'POST', signal: controller.signal, cache: 'no-store' });
                if (!response.ok) throw new Error('EVA unavailable');
                const { url } = await response.json();
                if (!controller.signal.aborted) setSrc(url);
              } catch {
                if (!controller.signal.aborted) setError(true);
              }
            }
            void open();
            return () => controller.abort();
          }, [handle, online, mainSessionId, attempt]);
          if (!online) return h('section', { style: { padding: 24, color: 'var(--dsw-alias-label-primary)' }, 'aria-label': handle },
            h('h2', null, handle.toUpperCase()),
            h('p', { role: 'status' }, t(state)),
            h('p', null, t(['starting', 'stopping'].includes(state) ? 'waiting' : 'offlineHint')),
            workspace && h('p', null, `${t('workspace')}: `, h('code', { style: { overflowWrap: 'anywhere' } }, workspace)));
          if (src && !error) return h('iframe', {
            key: src, src, title: `${handle.toUpperCase()} — ${t('conversation')}`,
            referrerPolicy: 'no-referrer',
            sandbox: 'allow-scripts allow-same-origin allow-forms allow-modals allow-downloads allow-popups',
            allow: 'clipboard-write', onError: () => setError(true),
            style: { display: 'block', width: '100%', height: '100%', border: 0, background: 'var(--dsw-alias-bg-base)' },
          });
          return h('section', { style: { padding: 24, color: 'var(--dsw-alias-label-primary)' }, 'aria-label': handle },
            h('p', { role: error ? 'alert' : 'status' }, `${handle.toUpperCase()} — ${t(error ? 'error' : 'opening')}`),
            error && h('button', { type: 'button', onClick: () => setAttempt((value) => value + 1) }, t('retry')));
        }
        ctx.effect(() => {
          const controller = new AbortController();
          const entries = new Map();
          let timer, offList, offSidebar;
          function syncNodes(nodes) {
            const handles = new Set(nodes.map((node) => node.handle));
            for (const [handle, entry] of entries) {
              if (!handles.has(handle)) { entry.dispose(); entries.delete(handle); }
            }
            for (const node of nodes) {
              const signature = JSON.stringify([node.status, node.mainSessionId, node.workspace]);
              const existing = entries.get(node.handle);
              if (existing?.signature === signature) continue;
              existing?.dispose(); entries.delete(node.handle);
              const id = `eva:${node.handle}`;
              const state = nodeState(node);
              const disposeMain = ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: id }, () => h(MainConversation, node)));
              let disposeEntry;
              try {
                disposeEntry = ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({ name: 'sidebar.panellist', id, order: 20,
                  label: () => `${node.handle.toUpperCase()} · ${t(state)}` },
                  (props) => h('span', { style: { display: 'inline-flex', opacity: state === 'online' ? 1 : 0.5 } }, h(Icon, props))));
              } catch (error) { disposeMain(); throw error; }
              entries.set(node.handle, { signature, dispose() { disposeEntry(); disposeMain(); } });
            }
          }
          async function refresh() {
            try {
              const view = await readView(controller.signal);
              if (controller.signal.aborted) return;
              if (view.mode === 'child') {
                const embedded = window.parent !== window;
                // Login/reload can clear the one-shot main-session URL marker.
                // Framed children must remain conversation-only without it.
                if (!embedded && location.hash !== '#eva-main') return;
                // Single slots elect one occupant; do not tie the shipped sidebar.
                if (embedded) offSidebar = ctx.slots.inject('sidebar', () => ctx.slots.register({ name: 'sidebar', priority: 100 }, () => null));
                let scheduled = false;
                const open = () => {
                  if (scheduled || controller.signal.aborted || ctx.sessions.list.getSnapshot().phase !== 'ready') return;
                  scheduled = true;
                  queueMicrotask(() => {
                    if (controller.signal.aborted) return;
                    ctx.uiWorkspace.openSession(view.mainSessionId);
                    history.replaceState(null, '', location.pathname + location.search);
                    offList?.();
                  });
                };
                offList = ctx.sessions.list.subscribe(open);
                open();
                return;
              }
              if (view.mode === 'parent') syncNodes(view.nodes);
            } catch (error) {
              // Retain entries on transient failure, but surface setup errors.
              if (!controller.signal.aborted) console.error('EVA view activation failed:', error);
            }
            if (!controller.signal.aborted) timer = setTimeout(refresh, 3000);
          }
          void refresh();
          return () => {
            controller.abort(); clearTimeout(timer); offList?.(); offSidebar?.();
            for (const entry of entries.values()) entry.dispose();
            entries.clear();
          };
        });
      },
    };
  },
});
