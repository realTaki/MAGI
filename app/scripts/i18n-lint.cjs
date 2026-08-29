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
 * - Parse each locale file with ``@babel/parser``'s TypeScript
 *   support via the bundled ``typescript`` compiler (the package is
 *   already a devDependency). The script only relies on the AST
 *   shape, so we never import the locale module at runtime.
 * - Walk the top-level object literal and collect every dotted path
 *   (e.g. ``common.stop``) into a set.
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

/**
 * Walk a TypeScript object literal expression and collect dotted
 * keys. Only handles plain object literals (which is exactly the
 * shape of every locale file: ``export default { ... }``).
 */
function collectKeys(sourceText) {
  const ts = require("typescript");
  const sourceFile = ts.createSourceFile(
    "locale.ts",
    sourceText,
    ts.ScriptTarget.Latest,
    /* setParentNodes */ true,
    ts.ScriptKind.TS,
  );
  let exportDefault;
  for (const stmt of sourceFile.statements) {
    if (
      stmt.kind === ts.SyntaxKind.ExportAssignment &&
      stmt.expression.kind === ts.SyntaxKind.ObjectLiteralExpression
    ) {
      exportDefault = stmt.expression;
      break;
    }
  }
  if (!exportDefault) {
    throw new Error("could not locate `export default { ... }`");
  }

  const keys = new Set();
  function walk(node, prefix) {
    if (!node || !node.properties) return;
    for (const prop of node.properties) {
      // Shorthand: { foo } — won't appear in our locales but guard anyway.
      if (prop.kind === ts.SyntaxKind.ShorthandPropertyAssignment) continue;
      if (prop.kind !== ts.SyntaxKind.PropertyAssignment) continue;
      const name = prop.name;
      const key =
        name.kind === ts.SyntaxKind.Identifier ? name.text : String(name.text);
      const fullKey = prefix ? `${prefix}.${key}` : key;
      if (
        prop.initializer &&
        prop.initializer.kind === ts.SyntaxKind.ObjectLiteralExpression
      ) {
        walk(prop.initializer, fullKey);
      } else {
        keys.add(fullKey);
      }
    }
  }
  walk(exportDefault, "");
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