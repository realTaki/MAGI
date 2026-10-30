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
        newBot: 'New Bot', creatingBot: 'Creating Bot…', createError: 'Unable to create a Bot. Check EVA and retry explicitly.',
        expand: 'Expand sidebar', collapse: 'Collapse sidebar',
        listError: 'Unable to refresh all Bots. Previously loaded entries may be out of date.',
        navigationError: 'Unable to select this conversation. Retry or switch back to DSH.',
        online: 'Online', offline: 'Offline', starting: 'Starting', stopping: 'Stopping', failed: 'Failed',
        noMessages: 'No messages yet', previewUnavailable: 'Preview unavailable', attachment: '[Attachment]',
      },
      zh: {
        enter: '切换到 MAGI 工作台', back: '切换回 DSH', workbench: 'MAGI 工作台',
        mainBot: '主机器人', mode: 'Bot 模式', nodes: '我的 Bots', search: '搜索 Bot',
        noMatch: '没有匹配的 Bot', clear: '清除搜索', empty: '还没有 Bot',
        loading: '正在加载 Bots…', refresh: '刷新', retry: '重试',
        newBot: '新 Bot', creatingBot: '正在创建 Bot…', createError: '无法创建 Bot，请检查 EVA 后明确重试。',
        expand: '展开侧边栏', collapse: '收起侧边栏',
        listError: '部分 Bot 刷新失败，已显示的状态可能不是最新。',
        navigationError: '无法选中此会话，请重试或切回 DSH 检查。',
        online: '在线', offline: '离线', starting: '启动中', stopping: '停止中', failed: '异常退出',
        noMessages: '暂无消息', previewUnavailable: '消息预览暂不可用', attachment: '[附件]',
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
          }).map(({ handle, status, workspace, mainSessionId, preview }) => ({ handle, status, workspace, mainSessionId, preview }))
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
          mainBot = { handle: 'dsh-main', kind: 'parent', status: 'running', workspace: bot.workspace, mainSessionId: bot.mainSessionId, preview: bot.preview };
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
    /* Official DeepSeek silhouette, unmodified from:
     * https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/client/ui-primitives/src/FishLogo.tsx
     * MIT License — Copyright (c) 2026 DeepSeek
     * Permission is hereby granted, free of charge, to any person obtaining a copy
     * of this software and associated documentation files (the "Software"), to deal
     * in the Software without restriction, including without limitation the rights
     * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
     * copies of the Software, and to permit persons to whom the Software is
     * furnished to do so, subject to the following conditions:
     * The above copyright notice and this permission notice shall be included in all
     * copies or substantial portions of the Software.
     * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
     * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
     * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
     * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
     * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
     * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
     * SOFTWARE.
     */
    function OfficialMark() {
      return h('svg', { width: 24, height: 24 * 17.04 / 23.16, viewBox: '0 0 23.16 17.04', fill: 'none', 'aria-hidden': true, focusable: false },
        h('path', { fill: 'currentColor', d: 'M22.9168 1.43018C22.6713 1.31018 22.5658 1.53918 22.4223 1.65519C22.3733 1.69269 22.3318 1.74169 22.2903 1.78669C21.9317 2.1697 21.5127 2.42121 20.9657 2.39121C20.1657 2.34621 19.4827 2.59771 18.8787 3.20973C18.7502 2.45521 18.3236 2.0047 17.6746 1.71569C17.3351 1.56568 16.9916 1.41518 16.7536 1.08867C16.5876 0.856163 16.5421 0.597155 16.4591 0.341647C16.4061 0.187643 16.3536 0.0301382 16.1761 0.00363739C15.9836 -0.0263635 15.9081 0.135141 15.8326 0.270145C15.5306 0.822162 15.4136 1.43018 15.4251 2.0462C15.4516 3.43174 16.0366 4.53527 17.1991 5.3203C17.3311 5.4103 17.3651 5.5003 17.3236 5.63181C17.2441 5.90231 17.1501 6.16482 17.0671 6.43533C17.0141 6.60784 16.9351 6.64584 16.7501 6.57033C16.1121 6.30383 15.5611 5.90931 15.074 5.4328C14.2475 4.63328 13.5 3.75075 12.568 3.05973C12.349 2.89822 12.13 2.74822 11.9034 2.60522C10.9524 1.68169 12.028 0.923165 12.277 0.833162C12.5375 0.739159 12.3675 0.41615 11.5259 0.42015C10.6844 0.42365 9.91439 0.705658 8.93286 1.08117C8.78935 1.13767 8.63835 1.17867 8.48384 1.21267C7.59332 1.04367 6.66829 1.00617 5.70226 1.11517C3.88321 1.31768 2.43016 2.1777 1.36213 3.64575C0.0790928 5.4103 -0.222916 7.41536 0.146595 9.50642C0.535106 11.7105 1.66014 13.535 3.38869 14.9616C5.18125 16.4406 7.24581 17.1657 9.60138 17.0266C11.0319 16.9441 12.6245 16.7526 14.421 15.2321C14.874 15.4576 15.3496 15.5476 16.1381 15.6151C16.7456 15.6716 17.3306 15.5851 17.7836 15.4911C18.4931 15.3411 18.4441 14.6841 18.1876 14.5636C16.1081 13.595 16.5646 13.9891 16.1496 13.67C17.2061 12.42 18.8202 10.1979 19.3182 7.17235C19.3672 6.83834 19.4297 6.36783 19.4222 6.09732C19.4182 5.93231 19.4562 5.86831 19.6447 5.84931C20.1657 5.78931 20.6712 5.64681 21.1357 5.3913C22.4833 4.65528 23.0268 3.44624 23.1548 1.9972C23.1738 1.77569 23.1508 1.54668 22.9168 1.43018ZM11.1749 14.4736C9.15936 12.889 8.18184 12.3675 7.77832 12.39C7.40081 12.4125 7.46881 12.8445 7.55182 13.126C7.63882 13.404 7.75182 13.5955 7.91033 13.8396C8.01983 14.0011 8.09533 14.2411 7.80083 14.4216C7.15181 14.8231 6.02327 14.2866 5.97027 14.2601C4.65673 13.4865 3.5587 12.4655 2.78467 11.069C2.03715 9.72493 1.60314 8.28289 1.53164 6.74384C1.51264 6.37233 1.62214 6.24082 1.99215 6.17332C2.47916 6.08332 2.98118 6.06432 3.46769 6.13582C5.52476 6.43633 7.27581 7.35586 8.74385 8.8129C9.58188 9.64243 10.2159 10.634 10.8689 11.6025C11.5634 12.631 12.3105 13.611 13.262 14.4146C13.598 14.6961 13.866 14.9101 14.1225 15.0681C13.349 15.1546 12.058 15.1731 11.1749 14.4746L11.1749 14.4736ZM12.141 8.25988C12.141 8.09488 12.273 7.96338 12.439 7.96338C12.4765 7.96338 12.5105 7.97088 12.541 7.98188C12.5825 7.99688 12.6205 8.01938 12.6505 8.05338C12.7035 8.10588 12.7335 8.18088 12.7335 8.25988C12.7335 8.42489 12.6015 8.55639 12.4355 8.55639C12.2695 8.55639 12.141 8.42489 12.141 8.25988ZM15.1415 9.79893C14.949 9.87793 14.7565 9.94544 14.5715 9.95294C14.2845 9.96794 13.9715 9.85143 13.8015 9.70893C13.5375 9.48742 13.3485 9.36342 13.2695 8.97691C13.2355 8.8119 13.2545 8.55639 13.2845 8.40989C13.3525 8.09438 13.277 7.89187 13.0545 7.70787C12.8735 7.55786 12.643 7.51636 12.39 7.51636C12.2955 7.51636 12.209 7.47486 12.1445 7.44136C12.039 7.38886 11.9519 7.25735 12.035 7.09585C12.0615 7.04335 12.19 6.91584 12.22 6.89334C12.5635 6.69784 12.9595 6.76184 13.326 6.90834C13.6655 7.04735 13.9225 7.30236 14.292 7.66287C14.6695 8.09838 14.7375 8.21838 14.9525 8.54539C15.1225 8.8009 15.277 9.06341 15.3831 9.36392C15.4471 9.55142 15.3641 9.70493 15.1415 9.79893Z' }));
    }
    // Exact MIT-licensed inline artwork: @deepseek-ai/dsh-client-ui-primitives@0.2.1-rc.3
    // https://unpkg.com/@deepseek-ai/dsh-client-ui-primitives@0.2.1-rc.3/lib/index.js
    // Full copyright/permission notice accompanies OfficialMark above and covers these copies.
    // This version renders paths, not sprite symbols. Stay self-contained when native
    // SidebarRoot is unmounted: a plausible #dsw-icon-* href alone draws nothing.
    function SidebarIcon({ size = 16 } = {}) {
      return h('svg', { className: 'magi-wb-panel-icon', width: size, height: size, viewBox: '0 0 16 16', fill: 'none', strokeWidth: 1, 'aria-hidden': true, focusable: false },
        h('path', { d: 'M13.5 1.5H2.5C1.94772 1.5 1.5 1.94772 1.5 2.5V13.5C1.5 14.0523 1.94772 14.5 2.5 14.5H13.5C14.0523 14.5 14.5 14.0523 14.5 13.5V2.5C14.5 1.94772 14.0523 1.5 13.5 1.5Z', stroke: 'currentColor' }),
        h('path', { d: 'M5.5 1.5V14.5', stroke: 'currentColor' }));
    }
    function NewChatIcon({ compact = false, leading = false } = {}) {
      // Native SidebarRoot uses 14px medium expanded, 18px regular rail;
      // HeaderLeadingControls uses 16px regular in the hidden-sidebar seat.
      const size = leading ? 16 : compact ? 18 : 14;
      return h('svg', { width: size, height: size, viewBox: '0 0 16 16', fill: 'none', strokeWidth: compact ? 1 : 1.3, 'aria-hidden': true, focusable: false },
        h('path', { d: 'M2.37091 11.2501C1.58745 9.89288 1.32067 8.29835 1.61969 6.76006C1.91872 5.22177 2.76342 3.8433 3.99826 2.87846C5.2331 1.91362 6.77494 1.42737 8.33988 1.50925C9.90482 1.59113 11.3875 2.23562 12.5149 3.32406C13.6425 4.41269 14.3387 5.87206 14.4754 7.4334C14.612 8.99474 14.18 10.5529 13.2587 11.8209C12.3375 13.0888 10.9891 13.9813 9.46194 14.3337C8.18691 14.628 6.85895 14.5294 5.64989 14.0605C5.1712 13.8748 4.76962 13.4932 4.26534 13.3967C3.67413 13.2835 2.95257 13.5598 2.03794 14.3337', stroke: 'currentColor' }),
        h('path', { d: 'M8 5V11', stroke: 'currentColor' }),
        h('path', { d: 'M5 8H11', stroke: 'currentColor' }));
    }
    function RefreshIcon() {
      return h('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true },
        h('path', { d: 'M20 11a8 8 0 1 0-2.3 6.7M20 4v7h-7' }));
    }
    function SearchIcon() {
      return h('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, 'aria-hidden': true },
        h('circle', { cx: 10.5, cy: 10.5, r: 6.5 }), h('path', { d: 'm16 16 4 4' }));
    }
    const avatarColor = (handle) => {
      const hash = Array.from(handle).reduce((value, letter) => (value * 31 + letter.charCodeAt(0)) >>> 0, 0);
      // Decorative identity colors, independent of the presence dot's semantics.
      return ['#7189ad', '#8f7fac', '#6e9e91', '#b08a70', '#a77d92', '#8a946d'][hash % 6];
    };
    const css = `
      /* MIT © 2026 DeepSeek; full notice accompanies OfficialMark above.
         Presentational baseline: @deepseek-ai/dsh-client-ui-sidebar@0.2.1-rc.3
         https://unpkg.com/@deepseek-ai/dsh-client-ui-sidebar@0.2.1-rc.3/lib/client.js
         Namespaced SidebarRoot/HeaderLeadingControls geometry; Bot actions remain local. */
      .magi-wb-sidebar { --dsh-sidebar-inline-padding:12px; box-sizing:border-box; display:flex; flex-direction:column; height:100%; min-height:0; min-width:0; padding:6px var(--dsh-sidebar-inline-padding); background:var(--dsw-specific-sidebar-fill); color:var(--dsw-alias-label-primary); font-size:14px; }
      html[data-platform=darwin] .magi-wb-sidebar { background:transparent; }
      .magi-wb-sidebar[data-collapsed=true] { padding:18px 10px 6px; }
      .magi-wb-chrome { display:none; align-items:center; justify-content:flex-end; box-sizing:border-box; height:52px; flex:none; margin:-6px calc(-1 * var(--dsh-sidebar-inline-padding)) 0; padding:0 12px 2px; -webkit-app-region:drag; }
      html[data-platform=darwin] .magi-wb-chrome { display:flex; }
      html[data-platform=darwin] .magi-wb-chrome + .magi-wb-sidebar-top { margin-top:-12px; }
      html[data-fullscreen] .magi-wb-chrome { justify-content:flex-start; }
      .magi-wb-sidebar-top { display:flex; align-items:center; justify-content:flex-end; box-sizing:border-box; height:60px; gap:8px; padding:8px 0 8px 4px; margin-bottom:4px; flex:none; overflow:hidden; -webkit-app-region:drag; }
      .magi-wb-sidebar-identity { display:inline-flex; align-items:center; gap:8px; height:24px; flex:1; min-width:0; }
      html[data-platform=darwin] .magi-wb-sidebar-top > .magi-wb-toggle { display:none; }
      .magi-wb-sidebar[data-collapsed=true] .magi-wb-sidebar-top { height:36px; padding:0; margin-bottom:12px; justify-content:flex-start; overflow:visible; }
      .magi-wb-new { box-sizing:border-box; display:flex; align-items:center; justify-content:center; flex:none; align-self:stretch; height:38px; gap:0; margin:0 2px 12px; padding:8px 12px; border:.5px solid var(--dsw-alias-border-l3); border-radius:var(--dsw-radius-md); background:var(--dsw-alias-button-elevated-fill); color:var(--dsw-alias-label-primary); font:inherit; font-size:14px; font-weight:500; line-height:22px; cursor:pointer; position:relative; overflow:hidden; container-type:inline-size; -webkit-app-region:no-drag; }
      .magi-wb-new-mask { flex:1; min-width:0; }
      .magi-wb-new-content { display:flex; align-items:center; justify-content:center; gap:6px; width:100cqw; }
      .magi-wb-new-label { white-space:nowrap; max-width:200px; overflow:hidden; }
      .magi-wb-new svg { display:block; flex:none; }
      .magi-wb-new:hover:not(:disabled) { background:var(--dsw-alias-button-floating-hover); }
      html[data-platform=darwin] .magi-wb-new[data-compact=false] { background:#ffffff8c; }
      html[data-platform=darwin] .magi-wb-new[data-compact=false]:hover:not(:disabled) { background:#ffffff4d; }
      html[data-platform=darwin] [data-ds-dark-theme] .magi-wb-new[data-compact=false] { background:#ffffff26; }
      html[data-platform=darwin] [data-ds-dark-theme] .magi-wb-new[data-compact=false]:hover:not(:disabled) { background:#fff3; }
      .magi-wb-new[data-compact=true] { align-self:flex-start; width:36px; height:36px; margin:0 0 12px; padding:0; border-color:transparent; background:transparent; }
      .magi-wb-new[data-compact=true]:hover:not(:disabled) { background:var(--dsw-alias-interactive-bg-hover); }
      .magi-wb-new:disabled { opacity:.5; cursor:default; }
      .magi-wb-new:focus-visible { outline:2px solid var(--dsw-alias-brand-primary); outline-offset:2px; }
      .magi-wb-leading { display:flex; align-items:center; gap:8px; }
      .magi-wb-toggle { display:inline-flex; align-items:center; justify-content:center; flex:none; box-sizing:border-box; width:28px; height:28px; padding:0; border:0; border-radius:var(--dsw-radius-sm); background:transparent; color:var(--dsw-alias-label-secondary); cursor:pointer; -webkit-app-region:no-drag; }
      .magi-wb-toggle:hover, .magi-wb-leading .magi-wb-new:hover:not(:disabled) { background:var(--dsw-alias-interactive-bg-hover); }
      .magi-wb-sidebar[data-collapsed=true] .magi-wb-toggle { width:36px; height:36px; border-radius:var(--dsw-radius-md); color:var(--dsw-alias-label-primary); }
      .magi-wb-rail-mark { display:inline-flex; align-items:center; justify-content:center; }
      .magi-wb-sidebar[data-collapsed=true] .magi-wb-panel-icon { display:none; }
      .magi-wb-sidebar[data-collapsed=true] .magi-wb-toggle:hover .magi-wb-panel-icon { display:inline; }
      .magi-wb-sidebar[data-collapsed=true] .magi-wb-toggle:hover .magi-wb-rail-mark { display:none; }
      .magi-wb-leading .magi-wb-new, .magi-wb-leading .magi-wb-switch { box-sizing:border-box; width:28px; height:28px; margin:0; padding:0; border:0; border-radius:var(--dsw-radius-sm); background:transparent; color:var(--dsw-alias-label-secondary); }
      .magi-wb-leading .magi-wb-new-content { width:auto; }
      /* Let the identity use its natural width; never clip it to anchor the switch. */
      .magi-wb-brand { display:flex; align-items:center; gap:8px; flex:1; color:var(--dsw-alias-label-primary); }
      .magi-wb-wordmark { flex-shrink:0; font-size:20px; line-height:28px; font-weight:750; letter-spacing:-.035em; white-space:nowrap; }
      .magi-wb-mark { display:inline-flex; align-items:center; justify-content:center; flex-shrink:0; width:24px; height:24px; }
      .magi-wb-brand .magi-wb-badge { box-sizing:border-box; flex-shrink:0; padding:0 5px; font-size:9px; line-height:16px; font-weight:600; letter-spacing:.08em; color:var(--dsw-alias-label-secondary); }
      .magi-wb-badge { padding:2px 5px; border:1px solid var(--dsw-alias-border-l2); border-radius:4px; font-size:10px; font-weight:650; letter-spacing:.06em; line-height:1.4; }
      .magi-wb-switch { box-sizing:border-box; display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px; padding:6px; border:0; border-radius:8px; background:transparent; color:var(--dsw-alias-label-secondary); cursor:pointer; flex-shrink:0; -webkit-app-region:no-drag; }
      .magi-wb-switch svg { display:block; flex-shrink:0; }
      .magi-wb-brand .magi-wb-switch { width:32px; height:32px; margin-left:auto; }
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
      .magi-wb-avatar { position:relative; display:block; flex-shrink:0; width:36px; height:36px; border-radius:10px; }
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
        const previewOf = (node) => node.preview?.state === 'message' && typeof node.preview.text === 'string' && node.preview.text.trim()
          ? Array.from(node.preview.text.replace(/\s+/gu, ' ').trim()).slice(0, 240).join('')
          : t(node.preview?.state === 'empty' ? 'noMessages' : node.preview?.state === 'attachment' ? 'attachment' : 'previewUnavailable');
        let snapshot = { open: false, nodes: [], selected: '', error: false, loading: false, navError: false, failedTarget: '', creating: false, createError: false };
        let disposed = false, installed = false, childHost = false, controller, timer, releaseBrowser, releaseLeading, creationRequest, failedNode;
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
          ctx.layout.beginNavigation();
          try {
            if (node.kind === 'parent') {
              // Native navigation owns history, migration, drafts, retention and error UI.
              // Never create/fork/connect a Session here, nor wait on a second Client.
              ctx.uiWorkspace.openSession(node.mainSessionId);
            } else {
              // EVA already owns this registered main panel and its cross-Host transport.
              ctx.layout.selectPanel(`eva:${node.handle}`);
            }
            failedNode = undefined;
            publish({ selected: identityOf(node), navError: false, failedTarget: '' });
          } catch {
            // Missing EVA panel during initial registration, or refused native navigation:
            // keep the current native view and offer an explicit retry, never a loading curtain.
            failedNode = node;
            publish({ navError: true, failedTarget: identityOf(node) });
          }
        }
        function close() {
          pause(); creationRequest?.abort();
          releaseBrowser?.(); releaseBrowser = undefined;
          releaseLeading?.(); releaseLeading = undefined;
          publish({ open: false, loading: false, navError: false, failedTarget: '', createError: false });
          ctx.layout.selectPanel(null);
        }
        function open(event) {
          event?.preventDefault(); event?.stopPropagation();
          if (snapshot.open) return;
          publish({ open: true });
          // New Session and the panel list have no narrower native action seats.
          // Replace only this navigation column while MAGI is open; the native
          // registration, main/conversation layout and all sessions stay intact.
          releaseBrowser = ctx.slots.inject('sidebar', () => ctx.slots.register({ name: 'sidebar', priority: -10 }, Sidebar));
          releaseLeading = ctx.slots.inject('shell.leading', () => ctx.slots.register({ name: 'shell.leading', priority: -10 }, Leading));
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
            h('span', { className: 'magi-wb-brand', 'data-mode': view.open ? 'magi' : 'native' },
              h('span', { className: 'magi-wb-wordmark' }, view.open ? 'MAGI' : 'deepseek'),
              h('span', { className: 'magi-wb-badge' }, view.open ? 'BOT' : 'HARNESS'), h(Switch)));
        }
        function Sidebar({ collapsed = false, width } = {}) {
          useLocale();
          return h('aside', { className: 'magi-wb-sidebar', 'data-collapsed': collapsed ? 'true' : 'false', style: { width }, 'aria-label': t('workbench') },
            h('style', null, css),
            h('div', { className: 'magi-wb-chrome' }, h(Toggle, { collapsed, rail: true })),
            h('div', { className: 'magi-wb-sidebar-top' }, collapsed ? h(Switch) : h('span', { className: 'magi-wb-sidebar-identity' },
              h('span', { className: 'magi-wb-mark', 'aria-hidden': true }, h(OfficialMark)), h(Brand)), h(Toggle, { collapsed, rail: true })),
            h(NewBot, { compact: collapsed }),
            h(BotBrowser, { wide: !collapsed, expandSidebar: () => ctx.layout.toggleSidebar() }));
        }
        function Toggle({ collapsed = true, rail = false } = {}) {
          useLocale();
          return h('button', { type: 'button', className: 'magi-wb-toggle', title: t(collapsed ? 'expand' : 'collapse'), 'aria-label': t(collapsed ? 'expand' : 'collapse'),
            'aria-expanded': !collapsed, onClick: () => ctx.layout.toggleSidebar() },
          rail && collapsed && h('span', { className: 'magi-wb-rail-mark', 'aria-hidden': true }, h(OfficialMark)), h(SidebarIcon, { size: rail && collapsed ? 18 : 16 }));
        }
        function Leading() {
          return h('div', { className: 'magi-wb-leading', style: { WebkitAppRegion: 'no-drag' } }, h('style', null, css), h(Toggle), h(NewBot, { compact: true, leading: true }), h(Switch));
        }
        function NewBot({ compact = false, leading = false } = {}) {
          const view = React.useSyncExternalStore(subscribe, getSnapshot); useLocale();
          const label = t(view.creating ? 'creatingBot' : 'newBot');
          return h(React.Fragment, null,
            h('button', { type: 'button', className: 'magi-wb-new', 'data-compact': compact ? 'true' : 'false', title: label, 'aria-label': label, 'aria-busy': view.creating, disabled: view.creating,
              onClick: () => void createBot() }, h('span', { className: 'magi-wb-new-mask' }, h('span', { className: 'magi-wb-new-content' },
                h(NewChatIcon, { compact, leading }), !compact && h('span', { className: 'magi-wb-new-label' }, label)))),
            view.createError && h('div', { className: 'magi-wb-error', role: 'alert' }, t('createError'),
              h('button', { type: 'button', disabled: view.creating, onClick: () => void createBot() }, t('retry'))));
        }
        async function createBot() {
          if (disposed || !snapshot.open || creationRequest) return;
          const request = new AbortController(); creationRequest = request;
          const navigation = ctx.layout.beginNavigation();
          publish({ creating: true, createError: false, navError: false, failedTarget: '' });
          let node;
          try {
            // Only an explicit click sends a POST. Request cancellation never
            // cancels the Host manager's independent child lifecycle operation.
            const response = await send('api/eva/create', { method: 'POST', signal: request.signal, cache: 'no-store' });
            if (!response.ok) throw new Error('Creation failed');
            const result = await response.json();
            if (disposed || request.signal.aborted || !snapshot.open) return;
            if (!result || typeof result.handle !== 'string' || !handlePattern.test(result.handle)) throw new Error('Invalid Bot');
            node = { handle: result.handle, status: result.status, workspace: result.workspace, mainSessionId: result.mainSessionId };
            await refresh();
            if (disposed || request.signal.aborted || !snapshot.open) return;
            if (!snapshot.nodes.some((item) => identityOf(item) === node.handle)) publish({ nodes: [...snapshot.nodes, node] });
            // selectPanel admits only registered panels. A registration race
            // keeps the current view and exposes the existing same-Bot retry.
            if (!disposed && !request.signal.aborted && !navigation.aborted && snapshot.open) select(node);
          } catch {
            if (!disposed && !request.signal.aborted && snapshot.open) {
              // Once allocated, retry only that panel. Never allocate another
              // node because its panel registration/navigation was delayed.
              if (node && !navigation.aborted) { failedNode = node; publish({ navError: true, failedTarget: node.handle }); }
              else if (!node) publish({ createError: true });
            }
          } finally {
            if (creationRequest === request) { creationRequest = undefined; publish({ creating: false }); }
          }
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
              h('button', { type: 'button', onClick: () => select(view.nodes.find((node) => identityOf(node) === view.failedTarget) ?? failedNode) }, t('retry'))),
            h('nav', { className: 'magi-wb-nodes', 'aria-label': t('nodes') },
              visible.map((node) => h('button', { key: identityOf(node), type: 'button', className: 'magi-wb-node', 'aria-current': identityOf(node) === view.selected ? 'true' : undefined,
                title: `${nameOf(node)} — ${previewOf(node)} — ${t(stateOf(node))}`, 'aria-label': `${nameOf(node)} — ${previewOf(node)} — ${t(stateOf(node))}`, onClick: () => { if (!wide) expandSidebar(); select(node); } },
                h('span', { className: 'magi-wb-avatar', style: { background: avatarColor(identityOf(node)) }, 'aria-hidden': true },
                  h('span', { className: 'magi-wb-dot', style: { background: colorOf(stateOf(node)) } })),
                h('span', { className: 'magi-wb-node-copy' }, h('span', { className: 'magi-wb-node-name' }, nameOf(node)),
                  h('span', { className: 'magi-wb-node-preview' }, previewOf(node))))),
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
        ctx.effect(() => { void refresh(); return () => { disposed = true; pause(); creationRequest?.abort(); releaseBrowser?.(); releaseBrowser = undefined; releaseLeading?.(); releaseLeading = undefined; listeners.clear(); }; });
      },
    };
  },
});
