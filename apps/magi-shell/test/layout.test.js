import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../client.js', import.meta.url), 'utf8');
const css = source.match(/const css = `([\s\S]*?)`;/)[1];
const rule = (selector) => {
  const start = css.indexOf(`${selector} {`);
  assert.notEqual(start, -1, `missing CSS rule: ${selector}`);
  return css.slice(start).split('}')[0];
};

// Source contracts, not mock DOM, pixel rendering, or browser assertions.
test('MAGI fits inside the native browsing region without owning a second layout', () => {
  assert.match(source, /register\(\{ name: 'sidebar\.workspaces', priority: -10 \}, BotBrowser\)/);
  assert.doesNotMatch(source, /name: '(?:root|sidebar|main|shell\.overlay)'/);
  assert.doesNotMatch(css, /grid-template-columns|position:fixed|100vh|100vw/);
  assert.doesNotMatch(source, /magi-wb-(?:layout|windowbar|content|toolbar|context|current|status)\b/);
  assert.match(rule('.magi-wb-browser'), /flex:1; min-width:0; min-height:0/);
});

test('Bot browser keeps two-line contacts, circular avatars, search and compact rail support', () => {
  assert.match(rule('.magi-wb-node'), /min-height:60px/);
  assert.match(rule('.magi-wb-node'), /border:0/);
  assert.match(rule('.magi-wb-avatar'), /width:36px; height:36px; border-radius:50%/);
  assert.match(rule('.magi-wb-node-copy'), /flex-direction:column/);
  assert.match(rule('.magi-wb-node-name'), /font-weight:600/);
  assert.match(rule('.magi-wb-node-preview'), /text-overflow:ellipsis/);
  assert.match(rule('.magi-wb-nodes'), /flex:1; min-height:0; overflow:auto/);
  assert.match(css, /\.magi-wb-search:focus-within/);
  assert.match(css, /\.magi-wb-browser\[data-wide=false\][^}]*display:none/);
  assert.doesNotMatch(source, /magi-wb-(?:return|native-avatar)\b/);
});

test('branding uses the native brand seat with one-line wordmark, badge and adjacent switch', () => {
  assert.match(source, /name: 'sidebar\.brand\.name'/);
  assert.match(rule('.magi-wb-brand'), /display:flex; align-items:center/);
  assert.match(rule('.magi-wb-wordmark'), /white-space:nowrap/);
  assert.match(rule('.magi-wb-badge'), /border:1px solid var\(--dsw-alias-border-l2\)/);
  assert.match(rule('.magi-wb-brand .magi-wb-switch'), /margin-left:auto/);
  assert.match(source, /view\.open \? 'MAGI' : 'deepseek'/);
  assert.match(source, /view\.open \? 'BOT' : 'HARNESS'/);
  assert.match(source, /name: 'sidebar\.footer\.action'/);
});

test('native conversation loading is not covered by a MAGI curtain or animation', () => {
  assert.doesNotMatch(source, /magi-wb-(?:loading|frame|overlay)|iframe|role: 'dialog'/);
  assert.match(source, /className: 'magi-wb-no-match', role: 'status'/);
  assert.match(source, /className: 'magi-wb-error', role: 'alert'/);
  assert.match(css, /:focus-visible/);
  assert.doesNotMatch(css, /animation:|@keyframes/);
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b|rgba?\(/i, 'use theme colors in both modes');
});
