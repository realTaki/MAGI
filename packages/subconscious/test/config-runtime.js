import { registerHooks } from 'node:module';

export let schemaRuntime = 'native';
try { await import('@deepseek-ai/schemastery'); }
catch (error) {
  if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
  schemaRuntime = 'source-derived public-contract fixture (not live Host projection)';
  registerHooks({ resolve(specifier, context, nextResolve) {
    if (specifier === '@deepseek-ai/schemastery') return {
      url: new URL('./schema-fixture.js', import.meta.url).href, shortCircuit: true,
    };
    return nextResolve(specifier, context);
  } });
}
export const { Config, apply, inject, name, nativeConfigAvailable } = await import('../index.js');
