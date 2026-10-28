// Test-only native service probe. Fixed synthetic keys only; replies are booleans.
export const inject = ['credentials'];
export function apply(ctx) {
  if (process.env.EVA_CREDENTIAL_TEST !== '1') throw new Error('Isolated credential test required.');
  const grant = 'eva-sharing-test/grant';
  const counter = 'eva-sharing-test/counter';
  const waits = new Set();
  async function waitFor(revision, key = grant, field = 'revision') {
    return new Promise((done, fail) => {
      let settled = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer); off(); waits.delete(cancel);
        error ? fail(error) : done();
      };
      const cancel = () => finish(new Error('disposed'));
      const check = async (changedKey) => {
        if (changedKey !== key || settled) return;
        try {
          const record = await ctx.credentials.readRecord(key);
          if (record?.payload?.[field] === revision && (!!record === (revision !== undefined))) finish();
        } catch (error) { finish(error); }
      };
      const off = ctx.on('credentials/record-updated', check);
      const timer = setTimeout(() => finish(new Error('watch timeout')), 10000);
      waits.add(cancel);
      void check(key);
    });
  }
  const onMessage = (message) => {
    if (message?.type !== 'eva-credential-test') return;
    void (async () => {
      switch (message.op) {
        case 'set':
          await ctx.credentials.modifyRecord(grant, async () => ({ kind: 'grant', payload: { revision: message.revision } })); break;
        case 'wait': await waitFor(message.revision); break;
        case 'delete': await ctx.credentials.deleteRecord(grant); break;
        case 'increment':
          for (let n = 0; n < 10; n++) await ctx.credentials.modifyRecord(counter, async (current) => ({
            kind: 'grant', payload: { count: (current?.payload?.count ?? 0) + 1 },
          }));
          break;
        case 'check-counter': await waitFor(20, counter, 'count'); break;
        default: throw new Error('unsupported test operation');
      }
      if (process.connected) process.send({ type: 'eva-credential-test-result', id: message.id, ok: true });
    })().catch(() => {
      if (process.connected) process.send({ type: 'eva-credential-test-result', id: message.id, ok: false });
    });
  };
  ctx.effect(() => {
    process.on('message', onMessage);
    return () => { process.off('message', onMessage); for (const cancel of waits) cancel(); };
  });
}
