window.__ModuleLoader__.load({
  id: '@local/eva',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const dicts = {
      en: { opening: 'Opening main conversation…', conversation: 'Main conversation', error: 'Unable to open this EVA.', retry: 'Retry', online: 'Online', offline: 'Offline', starting: 'Starting', stopping: 'Stopping', failed: 'Failed', offlineHint: 'This node is not running under this parent Host. Its data is retained. Wake it to reuse this node and its main conversation.', wake: 'Wake EVA', waking: 'Waking…', wakeError: 'Unable to wake this EVA. It may be in use by another Host or failed to initialize. Retry or ask the assistant to inspect it.', wakeUnknown: 'This EVA is no longer in the inventory. Refresh the page to check its status.', wakeBusy: 'This EVA is stopping. Wait before waking it again.', wakeForbidden: 'Use the signed-in parent Host web page to wake this EVA; this connection cannot verify browser identity.', waiting: 'Waiting for the node status to update…', workspace: 'Data directory' },
      zh: { opening: '正在打开主会话…', conversation: '主会话', error: '无法打开此 EVA。', retry: '重试', online: '在线', offline: '离线', starting: '启动中', stopping: '停止中', failed: '异常退出', offlineHint: '此节点未在当前父实例中运行，数据仍保留。点击唤醒可复用此节点和原主会话。', wake: '唤醒 EVA', waking: '唤醒中…', wakeError: '无法唤醒此 EVA：节点可能被其他父实例占用或初始化失败。可以重试，或让助手检查。', wakeUnknown: '此 EVA 已不在节点列表中，请刷新页面确认状态。', wakeBusy: '此 EVA 正在停止，请稍后再唤醒。', wakeForbidden: '请从已登录的父实例网页唤醒；当前连接无法确认浏览器身份。', waiting: '正在等待节点状态更新…', workspace: '数据目录' },
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
      name: 'eva', inject: ['slots', 'locale', 'uiWorkspace', 'sessions', 'workspaces'],
      apply(ctx) {
        for (const [locale, dict] of Object.entries(dicts)) ctx.effect(() => ctx.locale.register('eva', locale, dict));
        const t = ctx.locale.bind('eva');
        // Status polling can remount a panel. Keep explicit wake requests owned
        // by the plugin, not by that panel, and deduplicate them per handle.
        const wakeController = new AbortController();
        const idleWake = { pending: false, error: null };
        const wakeStates = new Map(), wakeListeners = new Set();
        const subscribeWake = (listener) => { wakeListeners.add(listener); return () => wakeListeners.delete(listener); };
        function setWake(handle, value) {
          if (wakeController.signal.aborted) return;
          if (value === idleWake) wakeStates.delete(handle); else wakeStates.set(handle, value);
          for (const listener of wakeListeners) listener();
        }
        async function wakeNode(handle) {
          if (wakeController.signal.aborted || wakeStates.get(handle)?.pending) return;
          setWake(handle, { pending: true, error: null });
          try {
            const response = await send(`api/eva/wake?handle=${encodeURIComponent(handle)}`, { method: 'POST', signal: wakeController.signal, cache: 'no-store' });
            if (!response.ok) {
              const failure = await response.json().catch(() => ({}));
              const key = new Map([['invalid', 'wakeUnknown'], ['unknown', 'wakeUnknown'], ['busy', 'wakeBusy'], ['forbidden', 'wakeForbidden']]).get(failure?.code) ?? 'wakeError';
              setWake(handle, { pending: false, error: key });
              return;
            }
            setWake(handle, idleWake);
          } catch { setWake(handle, { pending: false, error: 'wakeError' }); }
        }
        ctx.effect(() => () => { wakeController.abort(); wakeStates.clear(); wakeListeners.clear(); });
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
          const wake = React.useSyncExternalStore(subscribeWake, () => wakeStates.get(handle) ?? idleWake);
          const canWake = state === 'offline' || state === 'failed';
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
            workspace && h('p', null, `${t('workspace')}: `, h('code', { style: { overflowWrap: 'anywhere' } }, workspace)),
            (canWake || wake.pending) && h('button', {
              type: 'button', disabled: wake.pending || !canWake, 'aria-busy': wake.pending,
              onClick: () => wakeNode(handle),
              style: { minHeight: 36, padding: '8px 14px', borderRadius: 8, border: '1px solid var(--dsw-alias-border-l2)',
                background: 'var(--dsw-alias-bg-layer-1)', color: 'var(--dsw-alias-label-primary)', font: 'inherit',
                cursor: wake.pending ? 'wait' : 'pointer', opacity: wake.pending ? 0.6 : 1 },
            }, t(wake.pending ? 'waking' : 'wake')),
            wake.error && h('p', { role: 'alert', style: { color: 'var(--dsw-alias-state-error-primary)' } }, t(wake.error)));
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
          let timer, offEmbed;
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
                function ChildFrame() {
                  const [state, setState] = React.useState('opening');
                  const [attempt, setAttempt] = React.useState(0);
                  React.useEffect(() => {
                    let disposed = false, scheduled = false, offSession;
                    const live = () => !disposed && !controller.signal.aborted;
                    const fail = () => { if (live()) setState('failed'); };
                    setState('opening');
                    const open = () => {
                      // Mirror native navigation's two-catalog readiness gate,
                      // after this overlay's effect proves the frame is mounted.
                      if (!live() || scheduled) return;
                      const catalog = ctx.sessions.list.getSnapshot();
                      if (catalog.phase !== 'ready' || ctx.workspaces.list.getSnapshot().phase !== 'ready') return;
                      scheduled = true;
                      // Native main-view discovery scans this catalog, not just
                      // local references. An absent target must not look successful.
                      if (!catalog.byId[view.mainSessionId]) return fail();
                      queueMicrotask(() => {
                        if (!live()) return;
                        try {
                          ctx.uiWorkspace.openSession(view.mainSessionId);
                          const binding = ctx.sessions.binding(view.mainSessionId);
                          if (!binding || !(ctx.sessions.retainInfo(view.mainSessionId).getSnapshot().retainedBy.mainView > 0)) return fail();
                          const check = () => {
                            if (!live()) return;
                            const snapshot = binding.session.getSnapshot();
                            if (snapshot.openState === 'error' || snapshot.removed) return fail();
                            if (snapshot.openState !== 'open') return;
                            if (!ctx.sessions.list.getSnapshot().byId[view.mainSessionId] || !(ctx.sessions.retainInfo(view.mainSessionId).getSnapshot().retainedBy.mainView > 0)) return fail();
                            if (location.hash === '#eva-main') history.replaceState(history.state, '', location.pathname + location.search);
                            setState('ready');
                            offSession?.();
                          };
                          offSession = binding.session.subscribe(check);
                          check();
                        } catch { fail(); }
                      });
                    };
                    const offSessions = ctx.sessions.list.subscribe(open);
                    const offWorkspaces = ctx.workspaces.list.subscribe(open);
                    open();
                    return () => { disposed = true; offSessions(); offWorkspaces(); offSession?.(); };
                  }, [attempt]);
                  // Add a marker, not a sidebar replacement: all native seats stay
                  // mounted. Match the actual frame rather than a Slot wrapper.
                  return h(React.Fragment, null,
                    embedded && h('div', { 'data-eva-embedded-frame': '', hidden: true }, h('style', null, `
                      div:has(> [data-rightbar-col]):has([data-eva-embedded-frame]) > div:first-of-type { display: none; }
                      div:has(> [data-rightbar-col]):has([data-eva-embedded-frame]) > div:has(+ [data-rightbar-col]) { grid-column: 1 / 3; grid-row: 1; }
                      div:has(> [data-rightbar-col]):has([data-eva-embedded-frame]) > [data-side="sidebar"],
                      div:has(> [data-rightbar-col]):has([data-eva-embedded-frame]) > [data-shell-leading] { display: none; }
                    `)),
                    state !== 'ready' && h('div', { role: state === 'failed' ? 'alert' : 'status', style: { position: 'absolute', inset: 0, display: 'grid', placeContent: 'center', gap: 12, padding: 24, pointerEvents: 'auto', background: 'var(--dsw-alias-bg-base)' } },
                      h('p', null, state === 'failed' ? `${t('conversation')} — ${t('error')}` : t('opening')),
                      state === 'failed' && h('button', { onClick: () => setAttempt((value) => value + 1) }, t('retry'))));
                }
                offEmbed = ctx.slots.inject('shell.overlay', () => ctx.slots.register({ name: 'shell.overlay', id: 'eva:embedded-frame' }, ChildFrame));
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
            controller.abort(); clearTimeout(timer); offEmbed?.();
            for (const entry of entries.values()) entry.dispose();
            entries.clear();
          };
        });
      },
    };
  },
});
