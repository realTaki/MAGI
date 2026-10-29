window.__ModuleLoader__.load({
  id: '@magi/dm',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
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
      name: '@magi/dm', inject: ['slots', 'uiConversation'],
      apply(ctx) {
        if (ctx.uiConversation) {
          ctx.effect(() => ctx.uiConversation.events.register(dmDefinition));
          ctx.effect(() => ctx.slots.inject('conversation.chat.node', () => ctx.slots.register(
            { name: 'conversation.chat.node', key: dmKind }, UserDmNotification)));
        }
      },
    };
  },
});
