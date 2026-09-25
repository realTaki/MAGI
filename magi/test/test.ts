import assert from "node:assert/strict";

export { afterEach, describe, test } from "node:test";
export { setTimeout as sleep } from "node:timers/promises";

type ArrayContaining = { readonly values: readonly unknown[] };

type Matchers = {
  toBe(expected: unknown): void;
  toBeDefined(): void;
  toBeFalse(): void;
  toBeGreaterThan(expected: number): void;
  toBeNull(): void;
  toBeTrue(): void;
  toBeTruthy(): void;
  toBeUndefined(): void;
  toContain(expected: unknown): void;
  toEqual(expected: unknown): void;
  toHaveLength(expected: number): void;
  toMatchObject(expected: Record<string, unknown>): void;
  toStartWith(expected: string): void;
  toThrow(expected?: string | RegExp): void;
};

function isArrayContaining(value: unknown): value is ArrayContaining {
  return typeof value === "object" && value !== null && "values" in value;
}

function matches(actual: unknown, expected: unknown): boolean {
  if (Object.is(actual, expected)) return true;
  if (typeof actual !== "object" || actual === null || typeof expected !== "object" || expected === null) return false;
  if (Array.isArray(actual) || Array.isArray(expected)) return Array.isArray(actual) && Array.isArray(expected) && actual.length === expected.length && actual.every((item, index) => matches(item, expected[index]));
  return Object.entries(expected).every(([key, value]) => matches((actual as Record<string, unknown>)[key], value));
}

function matcher(actual: unknown, inverse = false): Matchers {
  const check = (condition: boolean, detail: string): void => {
    if (inverse ? condition : !condition) assert.fail(detail);
  };
  return {
    toBe(expected) { check(Object.is(actual, expected), `expected ${String(actual)} ${inverse ? "not " : ""}to be ${String(expected)}`); },
    toBeDefined() { check(actual !== undefined, `expected value ${inverse ? "not " : ""}to be defined`); },
    toBeFalse() { check(actual === false, `expected ${String(actual)} ${inverse ? "not " : ""}to be false`); },
    toBeGreaterThan(expected) { check(typeof actual === "number" && actual > expected, `expected ${String(actual)} ${inverse ? "not " : ""}to be greater than ${expected}`); },
    toBeNull() { check(actual === null, `expected ${String(actual)} ${inverse ? "not " : ""}to be null`); },
    toBeTrue() { check(actual === true, `expected ${String(actual)} ${inverse ? "not " : ""}to be true`); },
    toBeTruthy() { check(Boolean(actual), `expected ${String(actual)} ${inverse ? "not " : ""}to be truthy`); },
    toBeUndefined() { check(actual === undefined, `expected ${String(actual)} ${inverse ? "not " : ""}to be undefined`); },
    toContain(expected) {
      const condition = typeof actual === "string"
        ? actual.includes(String(expected))
        : Array.isArray(actual) && actual.some((item) => matches(item, expected));
      check(condition, `expected ${String(actual)} ${inverse ? "not " : ""}to contain ${String(expected)}`);
    },
    toEqual(expected) {
      const condition = isArrayContaining(expected)
        ? Array.isArray(actual) && expected.values.every((item) => actual.some((actualItem) => matches(actualItem, item)))
        : matches(actual, expected);
      check(condition, `expected values ${inverse ? "not " : ""}to be deeply equal`);
    },
    toHaveLength(expected) { check((actual as { length?: unknown })?.length === expected, `expected length ${inverse ? "not " : ""}to be ${expected}`); },
    toMatchObject(expected) { check(matches(actual, expected), `expected value ${inverse ? "not " : ""}to match object`); },
    toStartWith(expected) { check(typeof actual === "string" && actual.startsWith(expected), `expected ${String(actual)} ${inverse ? "not " : ""}to start with ${expected}`); },
    toThrow(expected) {
      if (typeof actual !== "function") assert.fail("toThrow expects a function");
      const fn = actual as () => unknown;
      const pattern = typeof expected === "string" ? new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) : expected;
      if (inverse) assert.doesNotThrow(fn, pattern);
      else assert.throws(fn, pattern);
    },
  };
}

type Expect = ((actual: unknown, message?: string) => Matchers & { readonly not: Matchers; readonly rejects: { toThrow(expected?: string | RegExp): Promise<void> } }) & {
  arrayContaining(values: readonly unknown[]): ArrayContaining;
};

export const expect: Expect = ((actual: unknown, _message?: string) => {
  return {
    ...matcher(actual),
    not: matcher(actual, true),
    rejects: {
      async toThrow(expected?: string | RegExp): Promise<void> {
        const pattern = typeof expected === "string" ? new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) : expected;
        if (pattern === undefined) await assert.rejects(actual as Promise<unknown>);
        else await assert.rejects(actual as Promise<unknown>, pattern);
      },
    },
  };
}) as Expect;

expect.arrayContaining = (values: readonly unknown[]): ArrayContaining => ({ values });
