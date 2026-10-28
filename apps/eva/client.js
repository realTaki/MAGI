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
    // This is a business notification, never a human prompt or model output.
    // Only durable native input records are evidence; content is model-only.
    const dmKind = 'magi-user-dm';
    function dmSource(message) {
      const source = message?.source;
      return source?.kind === 'eva-user-dm' && typeof source.peer === 'string' && source.peer.length > 0
        && typeof source.messageId === 'string' && source.messageId.length > 0
        && typeof source.text === 'string' ? source : null;
    }
    function dmMessage(event) {
      if (event.type === 'user/message') return dmSource(event.data) ? event.data : null;
      // Agent.inject inserts one message per splice. Do not claim this event:
      // native inbox Definitions must also observe insertions and claims.
      if (event.type === 'agent/inbox/spliced' && event.data.target === 'next-step') {
        return event.data.inserted?.find((message) => dmSource(message)) ?? null;
      }
      return null;
    }
    function dmState(event) {
      const source = dmSource(dmMessage(event));
      return source ? { text: source.text, peer: source.peer, messageId: source.messageId, seq: event.seq } : undefined;
    }
    const dmDefinition = {
      kind: dmKind, target: 'chat',
      match(event) {
        const source = dmSource(dmMessage(event));
        return source ? { id: JSON.stringify([source.peer, source.messageId]), role: 'start',
          ...(event.type === 'user/message' ? { exclusive: true } : {}) } : null;
      },
      start: (_context, match) => dmState(match.event),
      update(context, match) {
        const next = dmState(match.event);
        return next ? { ...next, seq: context.state.seq } : context.state;
      },
      buildViewNode(context) {
        if (!context.state) return null;
        return { key: context.key, kind: dmKind, id: context.id, target: 'chat',
          anchorSeq: context.state.seq,
          location: context.start?.location ?? context.matches[0]?.location ?? { kind: 'unresolved' },
          visibility: 'visible', groupTree: 'input', data: context.state };
      },
    };
    function UserDmNotification({ node }) {
      // React text children, not Markdown/HTML: preserve even hostile raw bodies.
      return h('div', { 'data-magi-user-dm': '', style: {
        alignSelf: 'flex-start', width: '100%', minWidth: 0, padding: '8px 0',
        whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', textAlign: 'start',
        color: 'var(--dsw-alias-label-primary)', font: 'inherit',
      } }, node.data.text);
    }
    return {
      name: 'eva', inject: ['slots', 'locale', 'uiWorkspace', 'sessions', 'workspaces', 'uiConversation'],
      apply(ctx) {
        if (ctx.uiConversation) {
          ctx.effect(() => ctx.uiConversation.events.register(dmDefinition));
          ctx.effect(() => ctx.slots.inject('conversation.chat.node', () => ctx.slots.register(
            { name: 'conversation.chat.node', key: dmKind }, UserDmNotification)));
        }
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
        // The native main owner unmounts its selected keyed entry on navigation.
        // Own anchors only measure their own center-panel element; a persistent
        // root overlay retains the real frame DOM, never moves it between seats.
        let nodeSnapshot = new Map(), deckSnapshot = { anchor: null, frames: [] };
        const nodeListeners = new Set(), deckListeners = new Set(), frames = new Map();
        let serial = 0;
        const subscribeNodes = (listener) => { nodeListeners.add(listener); return () => nodeListeners.delete(listener); };
        const subscribeDeck = (listener) => { deckListeners.add(listener); return () => deckListeners.delete(listener); };
        const nodeIdentity = (node) => JSON.stringify([node.status, node.mainSessionId, node.workspace, node.instanceId]);
        // Browser monotonic clocks only. Missing clocks mean unknown, never 0ms.
        const now = () => globalThis.performance?.now?.() ?? null;
        const elapsed = (start) => {
          const end = now();
          return Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, Math.round((end - start) * 10) / 10) : null;
        };
        function notePhase(timing, phase) {
          if (!timing || frames.get(timing.handle)?.timing !== timing) return;
          timing.state = phase;
          if (phase === 'opening' || phase === 'preparing') return;
          if (timing.firstOutcome === null) {
            timing.firstOutcome = phase;
            if (phase === 'ready') timing.initialChildReadyMs = elapsed(timing.selectedAt);
          } else if (phase === 'ready' && timing.firstOutcome !== 'ready' && timing.recoveredReadyMs === null) {
            timing.recoveredReadyMs = elapsed(timing.selectedAt);
          }
        }
        function timingSnapshot() {
          return {
            retainedCount: frames.size, pageLimit: null,
            // Limit diagnostic output only, never the number of native pages.
            frames: [...frames.values()].slice(-50).map(({ key, timing }) => ({
              handle: timing.handle, generation: key, state: timing.state,
              visits: timing.visits, openRequests: timing.openRequests,
              firstOutcome: timing.firstOutcome, initialPreparationMs: timing.initialPreparationMs,
              initialChildReadyMs: timing.initialChildReadyMs, recoveredReadyMs: timing.recoveredReadyMs,
              backgroundDuringInitialOpen: timing.backgroundDuringInitialOpen,
              readyOnLastVisit: timing.readyOnLastVisit, anchorToAnimationFrameMs: timing.anchorToAnimationFrameMs,
            })),
            memory: { status: 'not-measured', perFrameBytes: null,
              secureContext: globalThis.isSecureContext === true,
              crossOriginIsolated: globalThis.crossOriginIsolated === true,
              applicationMemoryApiAvailable: typeof globalThis.performance?.measureUserAgentSpecificMemory === 'function' },
          };
        }
        const publishDeck = (anchor = deckSnapshot.anchor) => {
          deckSnapshot = { anchor, frames: [...frames.values()] };
          for (const listener of deckListeners) listener();
        };
        function retainFrame(handle, element) {
          const token = {}, node = nodeSnapshot.get(handle);
          if (!node || nodeState(node) !== 'online') return () => {};
          let frame = frames.get(handle);
          if (!frame) {
            frame = { key: ++serial, node, timing: {
              handle, selectedAt: now(), state: 'opening', visits: 0, openRequests: 0,
              firstOutcome: null, initialPreparationMs: null, initialChildReadyMs: null, recoveredReadyMs: null,
              backgroundDuringInitialOpen: document.hidden, readyOnLastVisit: false, anchorToAnimationFrameMs: null,
            } };
            frames.set(handle, frame);
          }
          // A visit never evicts another Bot. Keep each opened native page for
          // this parent-page lifetime; inventory removal/restart owns disposal.
          let disposed = false, scheduled;
          const visitedAt = now();
          frame.timing.visits++;
          frame.timing.readyOnLastVisit = frame.timing.state === 'ready';
          frame.timing.anchorToAnimationFrameMs = null;
          // This is scheduling latency from our owned anchor, NOT pixel paint
          // or time from the user's click. Cancel it when that anchor leaves.
          const visitPaint = requestAnimationFrame(() => {
            if (!disposed && frames.get(handle) === frame) frame.timing.anchorToAnimationFrameMs = elapsed(visitedAt);
          });
          const measure = () => {
            if (disposed) return;
            const bounds = element.getBoundingClientRect();
            const rect = { left: bounds.left, top: bounds.top, width: bounds.width, height: bounds.height };
            frame.rect = rect;
            const previous = deckSnapshot.anchor;
            if (previous?.token === token && Object.keys(rect).every((key) => rect[key] === previous.rect[key])) return;
            publishDeck({ token, handle, rect });
          };
          const schedule = () => { if (!disposed) scheduled ??= requestAnimationFrame(() => { scheduled = undefined; measure(); }); };
          const observer = new ResizeObserver(schedule);
          observer.observe(element);
          window.addEventListener('resize', schedule);
          window.addEventListener('scroll', schedule, true);
          // Publish even when the geometry matches the previous anchor: its new
          // owner token must become visible to the persistent deck.
          measure();
          return () => {
            disposed = true; observer.disconnect(); cancelAnimationFrame(scheduled); cancelAnimationFrame(visitPaint);
            window.removeEventListener('resize', schedule);
            window.removeEventListener('scroll', schedule, true);
            if (deckSnapshot.anchor?.token === token) publishDeck(null);
          };
        }
        function MainAnchor({ handle }) {
          const nodes = React.useSyncExternalStore(subscribeNodes, () => nodeSnapshot);
          const node = nodes.get(handle), anchor = React.useRef(null);
          React.useLayoutEffect(() => node && nodeState(node) === 'online' && anchor.current
            ? retainFrame(handle, anchor.current) : undefined, [node]);
          if (!node) return null;
          if (nodeState(node) !== 'online') return h(MainConversation, node);
          return h('div', { ref: anchor, 'data-eva-panel-anchor': handle, 'aria-hidden': true,
            style: { width: '100%', height: '100%', minWidth: 0, minHeight: 0 } });
        }
        function FrameDeck({ usePanelInfo }) {
          const activePanelId = usePanelInfo((info) => info.activePanelId);
          const snapshot = React.useSyncExternalStore(subscribeDeck, () => deckSnapshot);
          return h(React.Fragment, null,
            h('style', null, '[data-rightbar-fullscreen] [data-eva-retained-panel] { visibility: hidden !important; pointer-events: none !important; }'),
            snapshot.frames.map((entry) => {
              const { handle } = entry.node, anchor = snapshot.anchor;
              const active = activePanelId === `eva:${handle}` && anchor?.handle === handle && anchor.rect.width > 0 && anchor.rect.height > 0;
              const rect = active ? anchor.rect : entry.rect ?? { left: 0, top: 0, width: 1, height: 1 };
              return h('section', { key: entry.key, 'data-eva-retained-panel': handle,
                'aria-hidden': !active, inert: !active,
                style: { position: 'fixed', ...rect, overflow: 'hidden',
                  clipPath: 'polygon(0 var(--dsh-frame-top-clearance, 0px), var(--dsh-frame-leading-clearance, 0px) var(--dsh-frame-top-clearance, 0px), var(--dsh-frame-leading-clearance, 0px) 0, 100% 0, 100% 100%, 0 100%)',
                  visibility: active ? 'visible' : 'hidden',
                  pointerEvents: active ? 'auto' : 'none', background: 'var(--dsw-alias-bg-base)' } },
                h(MainConversation, { ...entry.node, active, timing: entry.timing }));
            }));
        }
        // Keep the native child mounted and laid out, but do not expose its
        // login/default-view/navigation paints before its own readiness signal.
        function ConversationFrame({ src, title, sessionId, onError, error = false, onRetry, active = true, timing }) {
          const frame = React.useRef(null);
          const [ready, setReady] = React.useState(false);
          React.useEffect(() => {
            let disposed = false, pending = !ready || error, deadline;
            const origin = new URL(src).origin;
            const probe = () => frame.current?.contentWindow?.postMessage({ type: 'eva:main-state-request', sessionId }, origin);
            const wait = () => {
              pending = true;
              clearTimeout(deadline);
              // Background tabs can pause the child's paint callbacks. Only
              // count a visible wait, not time spent away from this page.
              if (active && !document.hidden) deadline = setTimeout(() => {
                if (!disposed && pending && active && !document.hidden) {
                  pending = false; notePhase(timing, 'timeout'); onError(true);
                }
              }, 30000);
            };
            const visibility = () => {
              if (disposed) return;
              if (!pending) return;
              wait();
              if (active && !document.hidden) probe();
            };
            const receive = (event) => {
              if (disposed || event.source !== frame.current?.contentWindow || event.origin !== origin) return;
              const message = event.data;
              if (message?.type !== 'eva:main-state' || message.sessionId !== sessionId) return;
              if (message.state === 'ready' || message.state === 'failed') {
                notePhase(timing, message.state === 'ready' ? 'ready' : 'child-failed');
                pending = false;
                clearTimeout(deadline);
                onError(false);
                setReady(true); // A child failure has its own explicit retry UI.
              } else if (message.state === 'opening') {
                notePhase(timing, 'opening');
                setReady(false); wait();
              }
            };
            window.addEventListener('message', receive);
            document.addEventListener('visibilitychange', visibility);
            if (pending) wait();
            // Also recover a signal sent before this passive effect attached.
            if (pending && active) probe();
            return () => {
              disposed = true; pending = false; clearTimeout(deadline);
              window.removeEventListener('message', receive);
              document.removeEventListener('visibilitychange', visibility);
            };
          }, [src, sessionId, onError, active, ready, error, timing]);
          const exposed = ready && !error;
          return h('section', { 'aria-busy': !ready && !error, style: { position: 'relative', width: '100%', height: '100%', background: 'var(--dsw-alias-bg-base)' } },
            h('iframe', {
              ref: frame, src, title, referrerPolicy: 'no-referrer',
              sandbox: 'allow-scripts allow-same-origin allow-forms allow-modals allow-downloads allow-popups',
              allow: 'clipboard-write', onError: () => { notePhase(timing, 'frame-error'); onError(true); },
              'aria-hidden': !exposed, inert: !exposed, tabIndex: exposed ? 0 : -1,
              // Do not use display:none: the child must render to become ready.
              style: { display: 'block', width: '100%', height: '100%', border: 0,
                opacity: exposed ? 1 : 0, pointerEvents: exposed ? 'auto' : 'none', background: 'var(--dsw-alias-bg-base)' },
            }),
            !exposed && h('div', { role: error ? 'alert' : 'status', style: { position: 'absolute', inset: 0, display: 'grid', placeContent: 'center', gap: 12, padding: 24, color: 'var(--dsw-alias-label-primary)' } },
              h('p', { style: { margin: 0 } }, t(error ? 'error' : 'opening')),
              error && h('button', { type: 'button', onClick: onRetry }, t('retry'))));
        }
        // The parent stays on its own Host. Only an online node opens a frame.
        function MainConversation(node) {
          const { handle, workspace, mainSessionId, active = true, timing } = node;
          const state = nodeState(node);
          const online = state === 'online';
          React.useSyncExternalStore((listener) => ctx.locale.subscribe(listener), () => ctx.locale.getSnapshot());
          const wake = React.useSyncExternalStore(subscribeWake, () => wakeStates.get(handle) ?? idleWake);
          const canWake = state === 'offline' || state === 'failed';
          const [src, setSrc] = React.useState('');
          const [error, setError] = React.useState(false);
          const [attempt, setAttempt] = React.useState(0);
          const [preparedAttempt, setPreparedAttempt] = React.useState(0);
          React.useEffect(() => {
            if (!timing) return;
            // Include time away while authentication is still pending, before
            // ConversationFrame exists; otherwise a background open looks fast.
            const observe = () => {
              if (timing.firstOutcome === null && (!active || document.hidden)) timing.backgroundDuringInitialOpen = true;
            };
            observe(); document.addEventListener('visibilitychange', observe);
            return () => document.removeEventListener('visibilitychange', observe);
          }, [active, timing]);
          React.useEffect(() => {
            setSrc(''); setError(false);
            if (!online) return;
            const controller = new AbortController(), prepareStarted = now();
            if (timing) { timing.openRequests++; notePhase(timing, 'preparing'); }
            async function open() {
              try {
                // Prepare through authenticated transport; iframe navigation
                // cannot carry the parent's IPC / Authorization credential.
                const response = await send(`api/eva/open?handle=${encodeURIComponent(handle)}`, { method: 'POST', signal: controller.signal, cache: 'no-store' });
                if (!response.ok) throw new Error('EVA unavailable');
                const { url } = await response.json();
                if (!controller.signal.aborted) {
                  if (timing?.openRequests === 1) timing.initialPreparationMs = elapsed(prepareStarted);
                  notePhase(timing, 'opening'); setSrc(url); setPreparedAttempt(attempt);
                }
              } catch {
                if (!controller.signal.aborted) { notePhase(timing, 'prepare-error'); setError(true); }
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
          // A timeout/iframe error covers the existing page, never destroys it.
          // A valid child-ready message can recover it; only explicit retry
          // prepares navigation again and intentionally rebuilds the frame.
          // launchUrl is stable within a Host generation. The explicit epoch
          // prevents React batching/same-URL responses from retaining old ready
          // state, and never remounts a stale URL while retry preparation waits.
          if (src && preparedAttempt === attempt) return h(ConversationFrame, {
            key: `${preparedAttempt}:${src}`, src, title: `${handle.toUpperCase()} — ${t('conversation')}`,
            sessionId: mainSessionId, onError: setError, error, active, timing,
            onRetry: () => setAttempt((value) => value + 1),
          });
          return h('section', { style: { width: '100%', height: '100%', display: 'grid', placeContent: 'center', gap: 12,
            padding: 24, background: 'var(--dsw-alias-bg-base)', color: 'var(--dsw-alias-label-primary)' }, 'aria-label': handle },
            h('p', { role: error ? 'alert' : 'status', style: { margin: 0 } }, error ? `${handle.toUpperCase()} — ${t('error')}` : t('opening')),
            error && h('button', { type: 'button', onClick: () => setAttempt((value) => value + 1) }, t('retry')));
        }
        ctx.effect(() => {
          const controller = new AbortController();
          const entries = new Map();
          let timer, offEmbed, offDeck, metricsAttached = false;
          function syncNodes(nodes) {
            const next = new Map(nodes.map((node) => {
              const previous = nodeSnapshot.get(node.handle);
              return [node.handle, previous && nodeIdentity(previous) === nodeIdentity(node) ? previous : node];
            }));
            const changed = next.size !== nodeSnapshot.size || [...next].some(([handle, node]) => nodeSnapshot.get(handle) !== node);
            if (changed) {
              nodeSnapshot = next;
              let invalidated = false;
              for (const [handle, frame] of frames) {
                const node = next.get(handle);
                if (!node || nodeState(node) !== 'online' || nodeIdentity(node) !== nodeIdentity(frame.node)) {
                  frames.delete(handle); invalidated = true;
                }
              }
              if (invalidated) publishDeck();
              for (const listener of nodeListeners) listener();
            }
            const handles = new Set(nodes.map((node) => node.handle));
            for (const [handle, entry] of entries) {
              if (!handles.has(handle)) { entry.dispose(); entries.delete(handle); }
            }
            for (const node of nodes) {
              if (entries.has(node.handle)) continue;
              const id = `eva:${node.handle}`;
              // Node navigation lives in MAGI workbench now. Keep existing main
              // panel addresses compatible, but never add legacy sidebar rows.
              const disposeMain = ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: id }, () => h(MainAnchor, { handle: node.handle })));
              entries.set(node.handle, { dispose: disposeMain });
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
                    if (!embedded) return;
                    let disposed = false, painted = state !== 'ready', firstPaint, secondPaint;
                    const report = (value = state) => {
                      // No URLs, tokens or cookies cross this channel. Parent
                      // validates our exact WindowProxy, origin and main ID.
                      if (!disposed) window.parent.postMessage({ type: 'eva:main-state', sessionId: view.mainSessionId, state: value }, '*');
                    };
                    const receive = (event) => {
                      if (event.source === window.parent && event.data?.type === 'eva:main-state-request' && event.data.sessionId === view.mainSessionId && painted) report();
                    };
                    const leaving = () => report('opening');
                    window.addEventListener('message', receive);
                    window.addEventListener('pagehide', leaving);
                    if (painted) report();
                    else firstPaint = requestAnimationFrame(() => {
                      secondPaint = requestAnimationFrame(() => { painted = true; report(); });
                    });
                    return () => {
                      disposed = true; cancelAnimationFrame(firstPaint); cancelAnimationFrame(secondPaint);
                      window.removeEventListener('message', receive); window.removeEventListener('pagehide', leaving);
                    };
                  }, [state]);
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
              if (view.mode === 'parent') {
                // Diagnostics are optional and never gate native chat/child boot.
                // The read-only query copies existing counters; it cannot open,
                // refresh, navigate, probe a child, or initiate memory sampling.
                if (!metricsAttached && typeof ctx.inject === 'function') {
                  metricsAttached = true;
                  ctx.inject(['cordisInspect'], (scope) => {
                    scope.effect(() => scope.cordisInspect.register({
                      manifest: { id: 'EVAFrameTiming', description: 'Sanitized EVA page retention and parent-clock lifecycle timings.', methods: [{
                        name: 'snapshot', description: 'Read retained count and up to 50 frame summaries; timings may be null. Anchor-to-rAF is scheduling, not paint/click latency. Memory is unmeasured, not estimated.',
                        inputSchema: { type: 'object', properties: {}, additionalProperties: false },
                        outputSchema: { type: 'object', description: 'retainedCount, pageLimit(null), frames(last 50; no URLs/credentials/session text), memory(capabilities only, perFrameBytes null).' },
                      }] },
                      query(method) {
                        if (method !== 'snapshot') throw new Error('Unknown EVA timing query');
                        return timingSnapshot();
                      },
                    }));
                  });
                }
                if (!offDeck) offDeck = ctx.slots.inject('shell.overlay', () => ctx.slots.register({ name: 'shell.overlay', id: 'eva:retained-frames' }, (props) => h(FrameDeck, props)));
                syncNodes(view.nodes);
              }
            } catch (error) {
              // Retain entries on transient failure, but surface setup errors.
              if (!controller.signal.aborted) console.error('EVA view activation failed:', error);
            }
            if (!controller.signal.aborted) timer = setTimeout(refresh, 3000);
          }
          void refresh();
          return () => {
            controller.abort(); clearTimeout(timer); offEmbed?.(); offDeck?.();
            for (const entry of entries.values()) entry.dispose();
            entries.clear(); frames.clear(); nodeSnapshot = new Map(); publishDeck(null);
            for (const listener of nodeListeners) listener();
          };
        });
      },
    };
  },
});
