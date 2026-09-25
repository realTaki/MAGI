/**
 * Argument parsing shared by the builtin tools.
 *
 * Every helper throws a message the model can act on ("limit must be an
 * integer from 1 to 20") instead of quietly coercing a bad value.
 */

export function stringArg(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  if (typeof value !== "string" || !value) throw new Error(`${key} must be a non-empty string`);
  return value;
}

export function integerArg(args: Record<string, unknown>, key: string): number {
  const value = args[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) throw new Error(`${key} must be a positive integer`);
  return value;
}

export function nonNegativeIntegerArg(args: Record<string, unknown>, key: string): number {
  const value = args[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) throw new Error(`${key} must be a non-negative integer`);
  return value;
}

export function boundedInteger(value: unknown, key: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) throw new Error(`${key} must be an integer from ${min} to ${max}`);
  return value;
}

export function optionalBoundedInteger(value: unknown, fallback: number, key: string, min: number, max: number): number {
  return value === undefined ? fallback : boundedInteger(value, key, min, max);
}
