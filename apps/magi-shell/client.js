// MAGI is navigation inside the existing Desktop Client, not a second Client.
window.__ModuleLoader__.load({
  id: '@local/magi-shell',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const dicts = {
      en: {
        enter: 'Switch to MAGI workbench', back: 'Switch back to DSH', workbench: 'MAGI workbench',
        mainBot: 'Main Bot', mode: 'Bot mode', nodes: 'My Bots', search: 'Search Bots',
        noMatch: 'No matching Bots', clear: 'Clear search', empty: 'No Bots yet',
        loading: 'Loading Bots…', refresh: 'Refresh', retry: 'Retry',
        listError: 'Unable to refresh all Bots. Previously loaded entries may be out of date.',
        navigationError: 'Unable to select this conversation. Retry or switch back to DSH.',
        online: 'Online', offline: 'Offline', starting: 'Starting', stopping: 'Stopping', failed: 'Failed',
        conversation: 'Main conversation',
      },
      zh: {
        enter: '切换到 MAGI 工作台', back: '切换回 DSH', workbench: 'MAGI 工作台',
        mainBot: '主机器人', mode: 'Bot 模式', nodes: '我的 Bots', search: '搜索 Bot',
        noMatch: '没有匹配的 Bot', clear: '清除搜索', empty: '还没有 Bot',
        loading: '正在加载 Bots…', refresh: '刷新', retry: '重试',
        listError: '部分 Bot 刷新失败，已显示的状态可能不是最新。',
        navigationError: '无法选中此会话，请重试或切回 DSH 检查。',
        online: '在线', offline: '离线', starting: '启动中', stopping: '停止中', failed: '异常退出',
        conversation: '主会话',
      },
    };
    const send = (path, init) => (globalThis.__DSH_TRANSPORT__?.fetch ?? globalThis.fetch.bind(globalThis))(path, init);
    const stateOf = (node) => ({ running: 'online', starting: 'starting', stopping: 'stopping', failed: 'failed' })[node.status] ?? 'offline';
    const colorOf = (state) => `var(--dsw-alias-state-${({ online: 'success', starting: 'warn', stopping: 'warn', failed: 'error' })[state] ?? 'idle'}-primary)`;
    const handlePattern = /^[a-z][a-z0-9-]{0,31}$/;
    const identityOf = (node) => node.kind === 'parent' ? `parent:${node.handle}` : node.handle;
    async function readNodes(signal) {
      let nodes = [], mainBot, evaMissing = false, evaUnavailable = false, parentUnavailable = false;
      // EVA is optional: its absence or failure must not hide this Host's Main Bot.
      try {
        const response = await send('api/eva/view', { signal, cache: 'no-store' });
        if (response.status === 404) evaMissing = true;
        else {
          if (!response.ok) throw new Error('EVA inventory unavailable');
          const view = await response.json();
          if (view.mode === 'child') return null;
          if (view.mode !== 'parent' || !Array.isArray(view.nodes)) throw new Error('Invalid EVA inventory');
          const seen = new Set();
          nodes = view.nodes.filter((node) => {
            if (!node || typeof node.handle !== 'string' || !handlePattern.test(node.handle) || seen.has(node.handle)) return false;
            seen.add(node.handle); return true;
          }).map(({ handle, status, workspace, mainSessionId }) => ({ handle, status, workspace, mainSessionId }))
            .sort((a, b) => a.handle.localeCompare(b.handle));
        }
      } catch (error) {
        if (signal?.aborted) throw error;
        evaUnavailable = true;
      }
      signal?.throwIfAborted();
      try {
        const main = await send('api/eva/parent-main', { signal, cache: 'no-store' });
        if (main.status !== 404) {
          if (!main.ok) throw new Error('Main Bot unavailable');
          const parent = await main.json();
          const bot = parent?.bot;
          if (parent?.mode !== 'parent' || bot?.kind !== 'parent' || bot.handle !== 'dsh-main' || bot.status !== 'running'
            || typeof bot.mainSessionId !== 'string' || !bot.mainSessionId || typeof bot.workspace !== 'string' || !bot.workspace) throw new Error('Invalid Main Bot');
          mainBot = { handle: 'dsh-main', kind: 'parent', status: 'running', workspace: bot.workspace, mainSessionId: bot.mainSessionId };
        }
      } catch (error) {
        if (signal?.aborted) throw error;
        parentUnavailable = true;
      }
      if (!mainBot && (evaUnavailable || evaMissing)) throw new Error('Bot inventory unavailable');
      return { nodes: mainBot ? [mainBot, ...nodes] : nodes, error: evaUnavailable || parentUnavailable, evaUnavailable, parentUnavailable };
    }
    function SwitchIcon({ size = 20 }) {
      return h('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true },
        h('path', { d: 'M4 8h15m-4-4 4 4-4 4M20 16H5m4-4-4 4 4 4' }));
    }
    function RefreshIcon() {
      return h('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true },
        h('path', { d: 'M20 11a8 8 0 1 0-2.3 6.7M20 4v7h-7' }));
    }
    function SearchIcon() {
      return h('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, 'aria-hidden': true },
        h('circle', { cx: 10.5, cy: 10.5, r: 6.5 }), h('path', { d: 'm16 16 4 4' }));
    }
    function BotIcon() {
      return h('svg', { width: 22, height: 22, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', 'aria-hidden': true },
        h('path', { d: 'M12 3v3M5 11H3m18 0h-2' }), h('rect', { x: 5, y: 6, width: 14, height: 14, rx: 5 }), h('path', { d: 'M9 11v2m6-2v2m-5 3h4' }));
    }
    const avatarColor = (handle) => {
      const hash = Array.from(handle).reduce((value, letter) => (value * 31 + letter.charCodeAt(0)) >>> 0, 0);
      return `var(--dsw-alias-${['brand-primary', 'state-success-primary', 'state-warn-primary', 'label-secondary'][hash % 4]})`;
    };
    const css = `
      .magi-wb-brand { display:flex; align-items:center; gap:8px; min-width:0; width:100%; color:var(--dsw-alias-label-primary); }
      .magi-wb-wordmark { font-weight:750; letter-spacing:-.04em; white-space:nowrap; }
      .magi-wb-badge { padding:2px 5px; border:1px solid var(--dsw-alias-border-l2); border-radius:4px; font-size:10px; font-weight:650; letter-spacing:.06em; line-height:1.4; }
      .magi-wb-switch { box-sizing:border-box; display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px; padding:6px; border:0; border-radius:8px; background:transparent; color:var(--dsw-alias-label-secondary); cursor:pointer; flex-shrink:0; -webkit-app-region:no-drag; }
      .magi-wb-switch svg { display:block; flex-shrink:0; }
      .magi-wb-brand .magi-wb-switch { margin-left:auto; }
      .magi-wb-switch:hover, .magi-wb-node:hover { background:var(--dsw-alias-bg-layer-2); color:var(--dsw-alias-label-primary); }
      .magi-wb-switch:focus-visible, .magi-wb-node:focus-visible { outline:2px solid var(--dsw-alias-brand-primary); outline-offset:2px; }
      .magi-wb-switch:disabled { opacity:.45; cursor:default; }
      .magi-wb-browser { --magi-wb-rail:var(--dsw-alias-bg-base); display:flex; flex-direction:column; flex:1; min-width:0; min-height:0; height:100%; padding:8px; font-size:13px; color:var(--dsw-alias-label-primary); box-sizing:border-box; }
      .magi-wb-browser *, .magi-wb-browser *::before, .magi-wb-browser *::after { box-sizing:border-box; }
      .magi-wb-search { display:flex; align-items:center; gap:8px; min-height:36px; flex-shrink:0; padding:0 10px; border:1px solid transparent; border-radius:10px; background:color-mix(in srgb,var(--dsw-alias-label-primary) 5%,transparent); color:var(--dsw-alias-label-secondary); }
      .magi-wb-search:focus-within { border-color:var(--dsw-alias-brand-primary); }
      .magi-wb-search > svg { flex-shrink:0; }
      .magi-wb-search input { width:100%; min-width:0; padding:0; border:0; outline:0; background:transparent; color:var(--dsw-alias-label-primary); font:inherit; }
      .magi-wb-search input::placeholder { color:var(--dsw-alias-label-secondary); }
      .magi-wb-search input::-webkit-search-cancel-button { -webkit-appearance:none; }
      .magi-wb-search .magi-wb-switch { width:24px; height:24px; padding:2px; font-size:18px; }
      .magi-wb-heading { display:flex; align-items:center; gap:8px; min-height:28px; flex-shrink:0; margin:14px 4px 8px; color:var(--dsw-alias-label-secondary); font-size:12px; }
      .magi-wb-count { margin-left:auto; font-variant-numeric:tabular-nums; }
      .magi-wb-heading .magi-wb-switch { width:28px; height:28px; padding:6px; }
      .magi-wb-nodes { flex:1; min-height:0; overflow:auto; }
      .magi-wb-node { display:flex; align-items:center; gap:12px; width:100%; min-height:60px; margin:2px 0; padding:6px 8px; border:0; border-radius:10px; background:transparent; color:var(--dsw-alias-label-primary); font:inherit; text-align:left; cursor:pointer; }
      .magi-wb-node[aria-current=true] { background:color-mix(in srgb,var(--dsw-alias-label-primary) 6%,transparent); }
      .magi-wb-avatar { position:relative; display:flex; align-items:center; justify-content:center; flex-shrink:0; width:36px; height:36px; border-radius:50%; background:color-mix(in srgb,currentColor 14%,transparent); }
      .magi-wb-node-copy { display:flex; flex-direction:column; gap:4px; flex:1; min-width:0; }
      .magi-wb-node-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; font-weight:600; line-height:18px; }
      .magi-wb-node-preview { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; line-height:16px; color:var(--dsw-alias-label-secondary); }
      .magi-wb-dot { position:absolute; right:-1px; bottom:-1px; width:9px; height:9px; border:2px solid var(--magi-wb-rail); border-radius:50%; }
      .magi-wb-no-match { margin:20px 8px; line-height:1.6; color:var(--dsw-alias-label-secondary); }
      .magi-wb-error { padding:8px; color:var(--dsw-alias-state-error-primary); font-size:12px; }
      .magi-wb-browser[data-wide=false] { padding:4px 2px; }
      .magi-wb-browser[data-wide=false] .magi-wb-search, .magi-wb-browser[data-wide=false] .magi-wb-heading, .magi-wb-browser[data-wide=false] .magi-wb-node-copy { display:none; }
      .magi-wb-browser[data-wide=false] .magi-wb-node { justify-content:center; min-height:48px; padding:4px 0; }
    `;
    return {
      name: 'magi-shell', inject: ['slots', 'locale', 'uiWorkspace', 'layout'],
      apply(ctx) {
        // Children keep EVA's own conversation-only Client; never install a second navigation shell.
        if (window.parent !== window) return;
        for (const [language, dict] of Object.entries(dicts)) ctx.effect(() => ctx.locale.register('magi-shell', language, dict));
        const t = ctx.locale.bind('magi-shell');
        const nameOf = (node) => node.kind === 'parent' ? t('mainBot') : node.handle.toUpperCase();
        let snapshot = { open: false, nodes: [], selected: '', error: false, loading: false, navError: false, failedTarget: '' };
        let disposed = false, installed = false, childHost = false, controller, timer, releaseBrowser;
        const listeners = new Set();
        const subscribe = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
        const getSnapshot = () => snapshot;
        function publish(patch) {
          if (disposed) return;
          snapshot = { ...snapshot, ...patch };
          for (const fn of listeners) fn();
        }
        function pause() { clearTimeout(timer); controller?.abort(); controller = undefined; }
        function select(node) {
          if (!node || disposed || !snapshot.open) return;
          try {
            if (node.kind === 'parent') {
              // Native navigation owns history, migration, drafts, retention and error UI.
              // Never create/fork/connect a Session here, nor wait on a second Client.
              ctx.uiWorkspace.openSession(node.mainSessionId);
            } else {
              // EVA already owns this registered main panel and its cross-Host transport.
              ctx.layout.selectPanel(`eva:${node.handle}`);
            }
            publish({ selected: identityOf(node), navError: false, failedTarget: '' });
          } catch {
            // Missing EVA panel during initial registration, or refused native navigation:
            // keep the current native view and offer an explicit retry, never a loading curtain.
            publish({ navError: true, failedTarget: identityOf(node) });
          }
        }
        function close() {
          pause(); releaseBrowser?.(); releaseBrowser = undefined;
          publish({ open: false, loading: false, navError: false, failedTarget: '' });
          ctx.layout.selectPanel(null);
        }
        function open(event) {
          event?.preventDefault(); event?.stopPropagation();
          if (snapshot.open) return;
          publish({ open: true });
          // Override only browsing while in MAGI mode. The native sidebar, settings,
          // root, main/conversation slots and existing Client instance stay in place.
          releaseBrowser = ctx.slots.inject('sidebar.workspaces', () => ctx.slots.register({ name: 'sidebar.workspaces', priority: -10 }, BotBrowser));
          select(snapshot.nodes.find((node) => identityOf(node) === snapshot.selected) ?? snapshot.nodes[0]);
          void refresh();
        }
        function useLocale() { React.useSyncExternalStore((fn) => ctx.locale.subscribe(fn), () => ctx.locale.getSnapshot()); }
        function Switch() {
          const view = React.useSyncExternalStore(subscribe, getSnapshot); useLocale();
          return h('button', { type: 'button', className: 'magi-wb-switch', title: t(view.open ? 'back' : 'enter'), 'aria-label': t(view.open ? 'back' : 'enter'),
            onPointerDown: (event) => event.stopPropagation(), onClick: view.open ? (event) => { event?.preventDefault(); event?.stopPropagation(); close(); } : open }, h(SwitchIcon));
        }
        function Brand() {
          const view = React.useSyncExternalStore(subscribe, getSnapshot); useLocale();
          return h(React.Fragment, null, h('style', null, css),
            h('span', { className: 'magi-wb-brand' }, h('span', { className: 'magi-wb-wordmark' }, view.open ? 'MAGI' : 'deepseek'),
              h('span', { className: 'magi-wb-badge' }, view.open ? 'BOT' : 'HARNESS'), h(Switch)));
        }
        function BotBrowser({ wide = true, expandSidebar = () => {} } = {}) {
          const view = React.useSyncExternalStore(subscribe, getSnapshot); useLocale();
          const [query, setQuery] = React.useState('');
          const visible = view.nodes.filter((node) => `${node.handle} ${nameOf(node)}`.toLowerCase().includes(query.trim().toLowerCase()));
          return h('section', { className: 'magi-wb-browser', 'data-wide': wide ? 'true' : 'false', 'aria-label': t('workbench') }, h('style', null, css),
            h('div', { className: 'magi-wb-search', role: 'search', 'aria-label': t('search') }, h(SearchIcon),
              h('input', { type: 'search', value: query, placeholder: t('search'), 'aria-label': t('search'), autoComplete: 'off', spellCheck: false, onChange: (event) => setQuery(event.target.value) }),
              query && h('button', { type: 'button', className: 'magi-wb-switch', title: t('clear'), 'aria-label': t('clear'), onClick: () => setQuery('') }, '×')),
            h('div', { className: 'magi-wb-heading' }, h('span', null, t('nodes')),
              h('span', { className: 'magi-wb-count' }, query.trim() ? `${visible.length} / ${view.nodes.length}` : view.nodes.length),
              h('button', { type: 'button', className: 'magi-wb-switch', disabled: view.loading, onClick: () => void refresh(), title: t('refresh'), 'aria-label': t('refresh') }, h(RefreshIcon))),
            view.error && h('div', { className: 'magi-wb-error', role: 'alert' }, t('listError')),
            view.navError && h('div', { className: 'magi-wb-error', role: 'alert' }, t('navigationError'),
              h('button', { type: 'button', onClick: () => select(view.nodes.find((node) => identityOf(node) === view.failedTarget)) }, t('retry'))),
            h('nav', { className: 'magi-wb-nodes', 'aria-label': t('nodes') },
              visible.map((node) => h('button', { key: identityOf(node), type: 'button', className: 'magi-wb-node', 'aria-current': identityOf(node) === view.selected ? 'true' : undefined,
                title: `${nameOf(node)} — ${t(stateOf(node))}`, 'aria-label': `${nameOf(node)} — ${t(stateOf(node))}`, onClick: () => { if (!wide) expandSidebar(); select(node); } },
                h('span', { className: 'magi-wb-avatar', style: { color: avatarColor(node.handle) }, 'aria-hidden': true }, h(BotIcon),
                  h('span', { className: 'magi-wb-dot', style: { background: colorOf(stateOf(node)) } })),
                h('span', { className: 'magi-wb-node-copy' }, h('span', { className: 'magi-wb-node-name' }, nameOf(node)),
                  h('span', { className: 'magi-wb-node-preview' }, `${t(stateOf(node))} · ${t('conversation')}`)))),
              visible.length === 0 && h('p', { className: 'magi-wb-no-match', role: 'status' }, t(query.trim() ? 'noMatch' : view.loading ? 'loading' : 'empty'))));
        }
        async function refresh() {
          pause();
          const request = new AbortController(); controller = request;
          publish({ loading: true });
          try {
            const result = await readNodes(request.signal);
            if (disposed || request.signal.aborted) return;
            if (result === null) { childHost = true; return; }
            const previousMain = result.parentUnavailable && snapshot.nodes.find((node) => node.kind === 'parent');
            const nodes = previousMain ? [previousMain, ...result.nodes] : [...result.nodes];
            if (result.evaUnavailable) nodes.push(...snapshot.nodes.filter((node) => node.kind !== 'parent'));
            publish({ nodes, error: result.error });
            // An inventory refresh must never keep reopening the native conversation.
            if (snapshot.open && !snapshot.selected && !snapshot.navError) select(nodes[0]);
            if (!installed) {
              installed = true;
              ctx.slots.inject('sidebar.brand.name', () => ctx.slots.register({ name: 'sidebar.brand.name', priority: -10 }, Brand));
              ctx.slots.inject('sidebar.footer.action', () => ctx.slots.register({ name: 'sidebar.footer.action', id: 'magi-shell-switch', order: 100, label: () => t('enter') }, ({ wide } = {}) => wide ? null : h(React.Fragment, null, h('style', null, css), h(Switch))));
            }
          } catch { if (!disposed && !request.signal.aborted) publish({ error: true }); }
          finally {
            if (!disposed && !request.signal.aborted) {
              publish({ loading: false });
              if (!childHost && (snapshot.open || !installed)) timer = setTimeout(() => void refresh(), 3000);
            }
          }
        }
        ctx.effect(() => { void refresh(); return () => { disposed = true; pause(); releaseBrowser?.(); releaseBrowser = undefined; listeners.clear(); }; });
      },
    };
  },
});
