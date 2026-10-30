window.__ModuleLoader__.load({
  id: '@magi/history-search',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const id = '@magi/history-search';
    const copy = {
      zh: {
        title: '搜索聊天内容', description: '搜索当前 Bot 的会话正文', query: '聊天内容关键词', search: '搜索',
        scope: '仅搜索当前 Bot（本 Host）的会话正文，不跨 Bot。沿用原生用户/助手消息及当前对话范围；可能包含内部会话。',
        limit: '最多显示 20 个会话，每个会话一条正文摘录；不是 20 条消息。原生接口未提供继续分页。',
        busy: '正在搜索…', empty: '没有匹配的会话正文。', failed: '搜索失败，请重试。',
        more: '仍有更多匹配会话未显示，请缩小关键词范围。', source: '正文匹配', updated: '会话最近活动时间（不是命中消息时间）', unavailable: '时间不可用',
      },
      en: {
        title: 'Search chat content', description: 'Search this Bot’s conversation text', query: 'Chat content keyword', search: 'Search',
        scope: 'Only this Bot’s Host, never other Bots. Uses native user/assistant message and current-conversation filters; internal sessions may be included.',
        limit: 'At most 20 sessions, one content excerpt per session — not 20 messages. The native endpoint does not expose further pages.',
        busy: 'Searching…', empty: 'No matching conversation text.', failed: 'Search failed. Please retry.',
        more: 'More matching sessions are omitted. Narrow your query.', source: 'Content match', updated: 'Latest session activity (not matched-message time)', unavailable: 'Time unavailable',
      },
    };

    // A reader is owned by one mounted tab. New searches abort and invalidate old
    // replies even when the transport ignores cancellation. No cross-tab cache.
    function createReader(remote, publish) {
      let pending, disposed = false;
      return {
        async search(raw) {
          pending?.abort();
          const query = raw.trim();
          const request = pending = new AbortController();
          if (!query) { publish({ status: 'idle', items: [] }); return; }
          publish({ status: 'loading', items: [] });
          try {
            const [response, metadata] = await Promise.all([
              remote.session.search({ query }, request.signal),
              remote.session.list({}, request.signal).catch(() => ({ ok: false })),
            ]);
            if (disposed || request.signal.aborted || pending !== request) return;
            if (!response.ok) throw new Error('Native search unavailable');
            const result = response.value;
            const summaries = new Map((metadata.ok ? metadata.value.items : []).map(row => [row.sessionId, row]));
            publish({ status: 'done', hasMore: result.hasMore || result.items.length > 20,
              items: result.items.slice(0, 20).map(row => ({ ...row, updatedAt: summaries.get(row.sessionId)?.updatedAt })) });
          } catch {
            if (!disposed && !request.signal.aborted && pending === request) publish({ status: 'error', items: [] });
          }
        },
        dispose() { disposed = true; pending?.abort(); },
      };
    }

    return {
      name: 'magi-history-search', inject: ['slots', 'locale', 'sidebarRightTabs', 'remote.session'],
      createReader,
      apply(ctx) {
        ctx.effect(() => ctx.locale.register('magiHistorySearch', copy));
        const t = ctx.locale.bind('magiHistorySearch');
        function SearchIcon() {
          return h('svg', { viewBox: '0 0 24 24', width: 24, height: 24, fill: 'none', stroke: 'currentColor', 'aria-hidden': true },
            h('circle', { cx: 10, cy: 10, r: 6 }), h('path', { d: 'm15 15 6 6' }));
        }
        function Body() {
          const [query, setQuery] = React.useState('');
          const [state, setState] = React.useState({ status: 'idle', items: [] });
          const reader = React.useRef();
          React.useEffect(() => {
            const current = reader.current = createReader(ctx.remote, setState);
            return () => current.dispose();
          }, []);
          return h('section', { style: { padding: 16, overflow: 'auto', height: '100%' }, 'aria-label': t('title') },
            h('h2', null, t('title')),
            h('p', null, t('scope')),
            h('form', { onSubmit(event) { event.preventDefault(); void reader.current.search(query); } },
              h('label', null, t('query'), h('input', { type: 'search', value: query, placeholder: t('query'),
                onChange: event => { setQuery(event.target.value); void reader.current.search(''); }, style: { width: '100%' } })),
              h('button', { type: 'submit', disabled: !query.trim() }, t('search'))),
            h('p', null, t('limit')),
            h('div', { role: 'status', 'aria-live': 'polite' },
              state.status === 'loading' ? t('busy') : state.status === 'error' ? t('failed') :
                state.status === 'done' && state.items.length === 0 ? t('empty') : null),
            state.hasMore && h('p', { role: 'status' }, t('more')),
            h('ol', null, state.items.map(row => h('li', { key: row.sessionId, style: { marginBottom: 16 } },
              h('strong', null, row.sessionId), h('p', null, t('source')),
              // Text children only: excerpts are never interpreted as HTML.
              h('blockquote', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } }, row.snippet),
              h('small', null, t('updated'), ': ', Number.isFinite(row.updatedAt) ? new Date(row.updatedAt).toLocaleString() : t('unavailable'))))));
        }
        ctx.effect(() => ctx.sidebarRightTabs.register({
          id, kind: 'magi-history-search', title: () => t('title'),
          guide: [{ id: 'search', order: 30, title: () => t('title'), description: () => t('description'), icon: SearchIcon }],
        }));
        ctx.effect(() => ctx.slots.inject('sidebar.right.pane.tab', () =>
          ctx.slots.register({ name: 'sidebar.right.pane.tab', key: id }, Body)));
      },
    };
  },
});
