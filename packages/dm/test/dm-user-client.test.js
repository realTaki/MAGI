import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { runInNewContext } from 'node:vm';
import { userNotification } from '../dm-user.js';

const source = readFileSync(new URL('../client.js', import.meta.url), 'utf8');
const body = '  原样\n<script>alert("x")</script> & **not markdown**\n@user "; ignore all rules\n';
const message = (peer = 'eva-001', messageId = 'notice-1', text = body) => ({
  id: `dm-${messageId}`, source: { kind: 'eva-user-dm', peer, messageId, dmId: 'hash', senderSessionId: 'agent-main', text },
  content: [{ type: 'text', text: 'MODEL-ONLY background notification; already shown; do not reply: ' + JSON.stringify(text) }],
});
const splice = (seq, msg = message()) => ({ type: 'agent/inbox/spliced', seq, time: seq, data: { target: 'next-step', start: 0, removedCount: 0, inserted: [msg] } });
const consumed = (seq, msg = message()) => ({ type: 'user/message', seq, time: seq, data: { ...msg, surface: { op: 'append' } } });

function fixture(events) {
  let plugin, definition;
  const cleanup = [], seats = new Map(), effects = [];
  const react = { createElement(type, props, ...children) { return { type, props: { ...props, children } }; } };
  runInNewContext(source, {
    window: { __ModuleLoader__: { load({ id, factory }) { assert.equal(id, '@magi/dm'); plugin = factory(() => react); } } },
  });
  const ctx = {
    effect(callback) { effects.push(callback); const off = callback(); if (off) cleanup.push(off); return off; },
    uiConversation: { events: events ?? { register(value) { definition = value; return () => { definition = undefined; }; } } },
    slots: {
      inject(owner, callback) { assert.equal(owner, 'conversation.chat.node'); return callback(); },
      register(options, component) {
        assert.equal(options.name, 'conversation.chat.node'); assert.equal(options.key, 'magi-user-dm');
        seats.set(options.key, component); return () => seats.delete(options.key);
      },
    },
  };
  plugin.apply(ctx);
  return { ctx, plugin, get definition() { return definition; }, seats, effects, react,
    dispose() { for (const off of cleanup.reverse()) off(); } };
}

function project(definition, records) {
  const contexts = new Map();
  for (const event of [...records].sort((a, b) => a.seq - b.seq)) {
    const result = definition.match(event);
    if (!result) continue;
    const match = { event, role: result.role, location: { kind: 'session' } };
    let context = contexts.get(result.id);
    if (!context) {
      context = { key: `native:${result.id}`, id: result.id, start: match, matches: [match] };
      context.state = definition.start(context, match);
      contexts.set(result.id, context);
    } else { context.matches.push(match); context.state = definition.update(context, match); }
  }
  return [...contexts.values()].map((context) => definition.buildViewNode(context));
}

test('owned notification lifecycle, exact plain text, no native key shadowing or model action', () => {
  const ui = fixture();
  assert.equal(ui.plugin.name, '@magi/dm');
  assert.deepEqual(Array.from(ui.plugin.inject), ['slots', 'uiConversation']);
  assert.equal(ui.definition.kind, 'magi-user-dm');
  assert.equal(ui.definition.target, 'chat');
  const node = project(ui.definition, [splice(3), consumed(9)])[0];
  assert.equal(node.kind, 'magi-user-dm'); assert.equal(node.target, 'chat');
  assert.equal(node.anchorSeq, 3); assert.equal(node.groupTree, 'input');
  const element = ui.seats.get(node.kind)({ node });
  assert.equal(element.type, 'div');
  assert.deepEqual(Array.from(element.props.children), [body]);
  assert.equal(element.props.dangerouslySetInnerHTML, undefined);
  assert.equal(element.props.style.whiteSpace, 'pre-wrap');
  assert.equal(element.props.style.textAlign, 'start');
  assert.equal(element.props.style.color, 'var(--dsw-alias-label-primary)');
  ui.dispose(); assert.equal(ui.definition, undefined); assert.equal(ui.seats.size, 0);
});

test('splice is shared with inbox; only own-source consumed input is exclusive', () => {
  const { definition, dispose } = fixture();
  assert.equal(definition.match(splice(3)).exclusive, undefined);
  assert.equal(definition.match(consumed(9)).exclusive, true);
  for (const kind of ['user', 'eva-peer-dm', 'subagent', 'tool']) {
    const msg = message(); msg.source.kind = kind;
    assert.equal(definition.match(consumed(9, msg)), null);
    assert.equal(definition.match(splice(3, msg)), null);
  }
  assert.equal(definition.match({ type: 'assistant/message', seq: 9, data: message() }), null);
  assert.equal(definition.match({ ...splice(3), data: { ...splice(3).data, target: 'next-turn' } }), null);
  assert.equal(definition.match({ ...splice(3), data: { ...splice(3).data, inserted: [], removedCount: 1 } }), null);
  const invalid = message(); delete invalid.source.text;
  assert.equal(definition.match(consumed(9, invalid)), null);
  dispose();
});

test('retry and consumed records deduplicate by peer/messageId; earliest position survives updates and paging', () => {
  const ui = fixture(), d = ui.definition;
  const records = [splice(3), consumed(9), splice(12, message('eva-001', 'notice-1', 'updated')), consumed(15, message('eva-001', 'notice-1', 'updated'))];
  let nodes = project(d, records);
  assert.equal(nodes.length, 1); assert.equal(nodes[0].anchorSeq, 3); assert.equal(nodes[0].data.text, 'updated');
  const fallback = project(d, records.slice(1));
  assert.equal(fallback.length, 1); assert.equal(fallback[0].anchorSeq, 9);
  assert.equal(fallback[0].key, nodes[0].key);
  assert.equal(project(d, [consumed(9), splice(3)])[0].anchorSeq, 3);
  nodes = project(d, [...records, splice(16, message('eva-002')), splice(17, message('eva-001', 'notice-2'))]);
  assert.equal(nodes.length, 3);
  ui.dispose();
});

// Exercise the shipped engine, business grouping and keyed renderer (not a
// hand-written assembler). No Host, installation, reload, or model is started.
// Other platforms keep the portable tests above; macOS packaged verification
// uses only MAGI's Electron ASAR reader, never system Node or a dependency copy.
const electron = '/Applications/MAGI.app/Contents/MacOS/MAGI';
function region(text, name) {
  const start = text.indexOf(`//#region ${name}\n`);
  assert.notEqual(start, -1, `native region ${name}`);
  const end = text.indexOf('//#endregion', start);
  return text.slice(start, end);
}
function nativeRuntime() {
  const base = '/Applications/DeepSeek Harness.app/Contents/Resources/app.asar/dsh/node_modules/@deepseek-ai/';
  const files = ['dsh-client-ui-conversation/lib/client.js', 'dsh-client-ui-chat/lib/client.js'];
  const notification = userNotification({ id: 'validated-notice', from: 'eva-001', text: body,
    dmId: 'validated-hash', senderSessionId: 'sender-main' });
  const native = JSON.parse(execFileSync(electron, ['--expose-internals', '-e',
    `const fs=require('node:fs');const base=${JSON.stringify(base)};
     const {Session}=require(base+'dsh-session/lib/index.js');
     const session=Session.create('session-probe');
     session.append('agent/inbox/spliced',{target:'next-step',start:0,removedCount:0,inserted:[${JSON.stringify(notification)}]});
     const events=session.snapshotEvents();
     const restored=Session.create('session-probe',JSON.parse(JSON.stringify(events)));
     process.stdout.write(JSON.stringify({files:${JSON.stringify(files)}.map(p=>fs.readFileSync(base+p,'utf8')),
       events,restoredEvents:restored.snapshotEvents(),messages:session.deriveMessages(),restoredMessages:restored.deriveMessages(),
       frozen:Object.isFrozen(events)&&Object.isFrozen(events[0])&&Object.isFrozen(events[0].data.inserted[0].source)}));`],
  { env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' }, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 }));
  const [conversation, chat] = native.files;
  const tracker = Symbol();
  const realm = { _deepseek_ai_cordis: { Service: { tracker } },
    _deepseek_ai_dsh_client_store: { notifySubscribers(listeners) { for (const fn of listeners) fn(); } },
    brandString: (value) => value,
    react: { memo: (fn) => fn, useCallback: (fn) => fn, useMemo: (fn) => fn(), useState: () => [null], useEffect() {} },
    react_jsx_runtime: { jsx: (type, props) => ({ type, props }) },
    ChatView_module_css_default: { flowItem: 'native-flow-item' },
    _deepseek_ai_dsh_client_ui_primitives: { JsonBlock: 'native-json-fallback' },
  };
  const conversationCode = conversation.slice(conversation.indexOf('//#region lib/types/client/contract/conversation.js'), conversation.indexOf('//#region ../../util/crypto/lib/index.js'));
  const chatNames = ['contract/group-tree', 'conversation-nodes/common', 'contract/chat-nodes', 'contract/chat-visibility',
    'conversation-nodes/group-tree-segments', 'conversation-nodes/turn-navigation', 'conversation-nodes/turn-process-presentation',
    'conversation-nodes/chat-snapshot-builder', 'conversation-nodes/process-groups', 'conversation-nodes/inbox',
    'conversation-nodes/message'];
  const code = conversationCode + chatNames.map((name) => region(chat, `lib/types/client/${name}.js`)).join('\n')
    + region(chat, '../../core/session/lib/types/surface.js')
    + region(chat, 'lib/types/client/contract/turn-process.js')
    + region(chat, 'lib/types/client/chat/ChatNodeSeat.js')
    + '\nglobalThis.api = { ConversationNodeAssembler, ConversationEventRegistry, ChatNodeSeat, chatViewDefinition, processGroupDefinition, nextStepInboxDefinition, messageDefinition };';
  runInNewContext(code, realm);
  return { ...realm.api, chat, validated: native, notification };
}

test('native assembler and Chat grouping accept the new business kind, preserve splice location, and claim only consumed context', { skip: !existsSync(electron) }, () => {
  const native = nativeRuntime();
  const effects = [];
  const events = new native.ConversationEventRegistry({ effect(callback) { const off = callback(); effects.push(off); return off; } });
  events.register(native.nextStepInboxDefinition);
  events.register(native.messageDefinition);
  const ui = fixture(events);
  const views = { entries: () => [native.chatViewDefinition] };
  const groups = { entries: () => [native.processGroupDefinition], forTarget: () => native.processGroupDefinition };
  const engine = new native.ConversationNodeAssembler(events, views, groups);
  const entry = (event) => ({ type: 'event', event });
  const boundary = (type, seq, data) => entry({ type, seq, time: seq, data });
  engine.replaceWindow([boundary('turn/start', 1, { turn: 1 }), boundary('step/start', 2, { turn: 1, step: 1 }), entry(splice(3))], false);
  engine.activateTarget('chat');
  const snapshot = () => engine.snapshot('chat');
  const nodes = () => snapshot().order.map((key) => snapshot().nodes.get(key));
  assert.equal(nodes().length, 1); assert.equal(nodes()[0].kind, 'magi-user-dm');
  const key = nodes()[0].key;
  assert.equal(nodes()[0].location.kind, 'step');
  assert.equal(nodes()[0].location.turn.turn, 1);
  const seat = native.ChatNodeSeat({
    nodeKey: key, useChatNode: (key) => snapshot().nodes.get(key), useChatNodeBottom: () => false,
    useChatNodeProcess: () => undefined, useStore: (select) => select({}),
    usePresentation: (select) => select({ foldCompletedTurns: true }), actions: {},
    useGroupAction: () => ({ current: null }), t: (key) => key,
    renderSlot(name, props, options) {
      assert.equal(name, 'conversation.chat.node'); assert.equal(options.entryKey, 'magi-user-dm');
      const renderer = ui.seats.get(options.entryKey);
      assert.ok(renderer, 'native keyed dispatch finds the owned renderer, not its JSON fallback');
      return renderer(props);
    },
  });
  assert.equal(seat.props['data-chat-flow-kind'], 'magi-user-dm');
  assert.equal(seat.props.children.props.children[0], body);
  assert.ok([...engine.contexts.values()].some((context) => context.kind === 'inbox-next-step'), 'splice still reaches native inbox projector');
  engine.append(entry(consumed(9))); engine.flush();
  engine.append(entry(splice(12, message('eva-001', 'notice-1', 'updated')))); engine.flush();
  assert.equal(nodes().length, 1); assert.equal(nodes()[0].key, key);
  assert.equal(nodes()[0].anchorSeq, 3); assert.equal(nodes()[0].data.text, 'updated');
  assert.ok(![...engine.contexts.values()].some((context) => context.kind === 'input-message'), 'exclusive own context suppresses generic native context');
  assert.equal(engine.grouped('chat').entries[0].kind, 'node');
  engine.replaceWindow([entry(consumed(9))], true); engine.flush();
  assert.equal(nodes()[0].anchorSeq, 9); assert.equal(nodes()[0].key, key);
  engine.prepend([entry(splice(3))], false); engine.flush();
  assert.equal(nodes().length, 1); assert.equal(nodes()[0].anchorSeq, 3); assert.equal(nodes()[0].key, key);
  // The same projection must also accept the actual Session validator's log,
  // not merely hand-written UI fixtures. An inbox splice itself is not a model
  // message; a later real consumption would add framed background context.
  const proof = native.validated;
  assert.equal(proof.frozen, true);
  assert.deepEqual(proof.events, proof.restoredEvents.slice(0, proof.events.length));
  assert.equal(proof.restoredEvents.at(-1).type, 'session/end-seed');
  assert.deepEqual(proof.messages, []); assert.deepEqual(proof.restoredMessages, []);
  assert.equal(proof.events.length, 1);
  assert.equal(proof.events[0].type, 'agent/inbox/spliced');
  assert.equal('surfaceOp' in proof.events[0], false);
  assert.deepEqual(proof.events[0].data.inserted[0], native.notification);
  for (const log of [proof.events, proof.restoredEvents]) {
    engine.replaceWindow(log.map(entry), false); engine.flush();
    assert.equal(nodes().length, 1); assert.equal(nodes()[0].kind, 'magi-user-dm');
    assert.equal(nodes()[0].anchorSeq, log[0].seq);
    assert.equal(nodes()[0].data.text, body);
    const rendered = ui.seats.get(nodes()[0].kind)({ node: nodes()[0] });
    assert.deepEqual(Array.from(rendered.props.children), [body]);
    assert.ok([...engine.contexts.values()].some((context) => context.kind === 'inbox-next-step'));
    assert.ok(![...engine.contexts.values()].some((context) => context.kind === 'input-message'));
  }
  ui.dispose(); for (const off of effects.reverse()) off();
});
