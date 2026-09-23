#!/usr/bin/env node
/**
 * i18n-lint — enforce key parity across every locale file.
 *
 * The frontend uses :func:`useTranslation` with a flat "dotted" key
 * convention (``common.stop``, ``magic.notConfigured``). When a key is
 * present in ``en.ts`` but missing in ``zh.ts`` (or vice versa) the
 * fallback in the runtime prints the key as the visible text — which
 * is exactly how ``common.stop`` leaked through as a literal label in
 * the operator console. Catching the gap at lint time beats finding
 * it from a bug report.
 *
 * Strategy
 * --------
 * - Read each locale file as text. They are plain ``export default { … }``
 *   data (literals only), so keys are scanned out directly — no TypeScript
 *   compiler API, whose JS surface left the ``typescript`` entry point in 7.x.
 * - Collect every leaf dotted path (e.g. ``common.stop``) into a set.
 * - Diff the sets per file. Any key present in one locale but
 *   absent in another is reported. Exit non-zero if there is any
 *   asymmetry so this can wire into ``npm run lint``.
 *
 * Optional flag ``--allow-missing=<locale>`` skips the named locale
 * (useful when a translation is intentionally pending review).
 */
const fs = require("node:fs");
const path = require("node:path");

const LOCALES_DIR = path.resolve(__dirname, "..", "src", "i18n", "locales");
const REQUIRED_LOCALES = ["zh.ts", "en.ts", "ja.ts"];

function listLocaleFiles() {
  return fs
    .readdirSync(LOCALES_DIR)
    .filter((f) => f.endsWith(".ts") && REQUIRED_LOCALES.includes(f));
}

const IDENT_START = /[A-Za-z_$]/;
const IDENT_PART = /[\w$]/;

/** Index just past the string literal that starts at *start*. */
function skipString(sourceText, start) {
  const quote = sourceText[start];
  let i = start + 1;
  while (i < sourceText.length) {
    if (sourceText[i] === "\\") i += 2;
    else if (sourceText[i] === quote) return i + 1;
    else i += 1;
  }
  return i;
}

/**
 * Collect dotted keys from an object literal written as text. Every
 * locale file is ``export default { … }`` with string values, so the
 * scan only has to know strings, comments, braces and ``key:`` pairs;
 * nested objects keep the parent key as a prefix, as before.
 */
function collectKeys(sourceText) {
  const keys = new Set();
  const path = [];
  let key = null;

  const closeKey = () => {
    if (key !== null) {
      keys.add([...path, key].join("."));
      key = null;
    }
  };

  let i = 0;
  while (i < sourceText.length) {
    const ch = sourceText[i];
    if (ch === '"' || ch === "'" || ch === "`") {
      i = skipString(sourceText, i);
      closeKey();
    } else if (ch === "/" && sourceText[i + 1] === "/") {
      const newline = sourceText.indexOf("\n", i);
      i = newline === -1 ? sourceText.length : newline + 1;
    } else if (ch === "/" && sourceText[i + 1] === "*") {
      const end = sourceText.indexOf("*/", i + 2);
      i = end === -1 ? sourceText.length : end + 2;
    } else if (ch === "{") {
      if (key !== null) {
        path.push(key);
        key = null;
      }
      i += 1;
    } else if (ch === "}") {
      path.pop();
      key = null;
      i += 1;
    } else if (IDENT_START.test(ch)) {
      let end = i + 1;
      while (end < sourceText.length && IDENT_PART.test(sourceText[end])) end += 1;
      let after = end;
      while (after < sourceText.length && /\s/.test(sourceText[after])) after += 1;
      if (sourceText[after] === ":") {
        key = sourceText.slice(i, end);
        i = after + 1;
      } else {
        closeKey();
        i = end;
      }
    } else {
      if (ch === ",") closeKey();
      i += 1;
    }
  }
  return keys;
}

function diffSets(nameA, setA, nameB, setB) {
  const missing = [];
  for (const k of setA) if (!setB.has(k)) missing.push(k);
  return { nameA, nameB, missing };
}

function main() {
  const allowMissing = new Set(
    (process.argv
      .find((a) => a.startsWith("--allow-missing="))
      ?.split("=")[1] ?? "")
      .split(",")
      .filter(Boolean),
  );

  const files = listLocaleFiles();
  if (files.length < 2) {
    console.error(
      `[i18n-lint] need at least 2 locale files under ${LOCALES_DIR}; found ${files.join(", ")}`,
    );
    process.exit(2);
  }

  const keysByFile = new Map();
  for (const f of files) {
    const src = fs.readFileSync(path.join(LOCALES_DIR, f), "utf8");
    keysByFile.set(f, collectKeys(src));
  }

  let hasDiff = false;
  const others = files.filter((f) => !allowMissing.has(f));
  for (let i = 0; i < others.length; i++) {
    for (let j = i + 1; j < others.length; j++) {
      const a = others[i];
      const b = others[j];
      const setA = keysByFile.get(a);
      const setB = keysByFile.get(b);
      const inBNotA = [...setB].filter((k) => !setA.has(k)).sort();
      const inANotB = [...setA].filter((k) => !setB.has(k)).sort();
      if (inBNotA.length || inANotB.length) {
        hasDiff = true;
        if (inBNotA.length) {
          console.error(
            `[i18n-lint] ${b} has ${inBNotA.length} key(s) missing from ${a}:`,
          );
          for (const k of inBNotA) console.error(`    - ${k}`);
        }
        if (inANotB.length) {
          console.error(
            `[i18n-lint] ${a} has ${inANotB.length} key(s) missing from ${b}:`,
          );
          for (const k of inANotB) console.error(`    - ${k}`);
        }
      }
    }
  }

  if (hasDiff) {
    console.error(
      "[i18n-lint] FAIL — locale files are out of sync. Add the missing key(s) to keep parity.",
    );
    process.exit(1);
  }
  console.log(`[i18n-lint] OK — ${files.length} locales have ${keysByFile.get(files[0]).size} keys in common`);
}

main();
