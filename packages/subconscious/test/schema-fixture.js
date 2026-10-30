// Bounded test substitute only when the actual native peer cannot be imported.
// Source: @deepseek-ai/schemastery 3.18.5 lib/index.mjs:24–68, 136–149,
// 518–555 and defineMethod. This checks the actual public graph/Standard Schema
// boundary, not live Host projection, and must never be used by production code.
import { z } from 'zod';

function node(type, fields = {}, meta = {}) {
  const schema = value => {
    if (value === undefined) {
      if ('default' in schema.meta) value = structuredClone(schema.meta.default);
      else return undefined;
    }
    let result;
    if (type === 'transform') {
      result = schema.callback(schema.inner(value));
      // Native transform validates both result and adapted input (strict Zod
      // must still see unknown object keys).
      schema.callback(value);
    } else if (type === 'object') {
      result = { ...value };
      for (const [key, field] of Object.entries(schema.dict)) {
        const item = field(value[key]);
        if (item !== undefined || key in value) result[key] = item;
      }
    } else {
      let validator = z[type]();
      if (type === 'number' && schema.meta.step === 1) validator = validator.int();
      if (schema.meta.min !== undefined) validator = validator.min(schema.meta.min);
      if (schema.meta.max !== undefined) validator = validator.max(schema.meta.max);
      result = validator.parse(value);
    }
    return result;
  };
  Object.assign(schema, { type, ...fields, meta });
  schema[Symbol.for('schemastery')] = true;
  schema['~standard'] = { version: 1, vendor: 'schemastery', validate(value) {
    try { return { value: schema(value) }; }
    catch (error) { return { issues: [{ message: error.message }] }; }
  } };
  for (const key of ['min', 'max', 'step', 'default']) {
    schema[key] = value => node(type, fields, { ...schema.meta, [key]: value });
  }
  return schema;
}
export default {
  boolean: () => node('boolean'), string: () => node('string'), number: () => node('number'),
  object: dict => node('object', { dict }),
  transform: (inner, callback) => node('transform', { inner, callback }),
};
