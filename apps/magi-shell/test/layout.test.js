import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../client.js', import.meta.url), 'utf8');
const css = source.match(/const css = `([\s\S]*?)`;/)[1];
const rule = (selector) => {
  const start = css.indexOf(`\n      ${selector} {`);
  assert.notEqual(start, -1, `missing CSS rule: ${selector}`);
  return css.slice(start).split('}')[0];
};

// Source contracts, not mock DOM, pixel rendering, or browser assertions.
test('MAGI replaces only scoped sidebar actions and hidden-sidebar chrome, not conversation layout', () => {
  assert.match(source, /register\(\{ name: 'sidebar', priority: -10 \}, Sidebar\)/);
  assert.match(source, /register\(\{ name: 'shell\.leading', priority: -10 \}, Leading\)/);
  assert.doesNotMatch(source, /name: '(?:root|main|shell\.overlay)'/);
  assert.doesNotMatch(source, /children:\s*\{\s*'sidebar\./, 'never redeclare native sidebar child slots');
  assert.doesNotMatch(css, /position:fixed|100vh|100vw/);
  assert.doesNotMatch(rule('.magi-wb-sidebar'), /grid-template-columns/, 'no replacement conversation grid; brand-local grid is allowed');
  assert.doesNotMatch(source, /magi-wb-(?:layout|windowbar|content|toolbar|context|current|status)\b/);
  assert.match(rule('.magi-wb-browser'), /flex:1; min-width:0; min-height:0/);
});

test('Bot browser keeps two-line contacts, rounded color blocks, search and compact rail support', () => {
  assert.match(rule('.magi-wb-node'), /min-height:60px/);
  assert.match(rule('.magi-wb-node'), /border:0/);
  assert.match(rule('.magi-wb-avatar'), /width:36px; height:36px; border-radius:10px/);
  assert.match(rule('.magi-wb-node-copy'), /flex-direction:column/);
  assert.match(rule('.magi-wb-node-name'), /font-weight:600/);
  assert.match(rule('.magi-wb-node-preview'), /text-overflow:ellipsis/);
  assert.match(rule('.magi-wb-nodes'), /flex:1; min-height:0; overflow:auto/);
  assert.match(css, /\.magi-wb-search:focus-within/);
  assert.match(css, /\.magi-wb-browser\[data-wide=false\][^}]*display:none/);
  assert.doesNotMatch(source, /magi-wb-(?:return|native-avatar)\b/);
});

test('branding keeps the native seat and scopes explicit MAGI proportions to its own header', () => {
  assert.doesNotMatch(source, /renderSlot\(/, 'replacement has no child-slot declaration or authorization');
  assert.match(source, /function OfficialMark\(/);
  assert.match(source, /MIT License — Copyright \(c\) 2026 DeepSeek/);
  assert.match(source, /viewBox: '0 0 23\.16 17\.04'/);
  assert.match(rule('.magi-wb-mark'), /display:inline-flex; align-items:center; justify-content:center; flex-shrink:0; width:24px; height:24px/, 'center the proportional official SVG inside its 24px wrapper');
  assert.match(rule('.magi-wb-wordmark'), /font-size:20px; line-height:28px/);
  assert.match(rule('.magi-wb-brand .magi-wb-badge'), /font-size:9px; line-height:16px/);
  assert.match(rule('.magi-wb-brand .magi-wb-badge'), /color:var\(--dsw-alias-label-secondary\)/);
  assert.match(rule('.magi-wb-sidebar'), /padding:6px var\(--dsh-sidebar-inline-padding\)/);
  assert.match(rule('.magi-wb-sidebar'), /--dsh-sidebar-inline-padding:12px/);
  assert.match(rule('.magi-wb-chrome'), /height:52px[^}]*margin:-6px calc\(-1 \* var\(--dsh-sidebar-inline-padding\)\) 0; padding:0 12px 2px/);
  assert.match(rule('.magi-wb-new'), /height:38px; gap:0; margin:0 2px 12px; padding:8px 12px; border:\.5px solid var\(--dsw-alias-border-l3\)/);
  assert.match(rule('.magi-wb-new'), /font-size:14px; font-weight:500; line-height:22px/);
  assert.match(rule('.magi-wb-toggle'), /width:28px; height:28px; padding:0/);
  assert.match(rule('.magi-wb-leading'), /gap:8px/);
  assert.match(rule('html[data-platform=darwin] .magi-wb-new[data-compact=false]'), /background:#ffffff8c/, 'native macOS translucent fill belongs only to the expanded surface, never leading/rail icon buttons');
  assert.match(rule('.magi-wb-new[data-compact=true]'), /width:36px; height:36px/);
  assert.match(rule('.magi-wb-sidebar[data-collapsed=true] .magi-wb-toggle'), /width:36px; height:36px/);
  assert.match(source, /@deepseek-ai\/dsh-client-ui-sidebar@0\.2\.1-rc\.3/);
  assert.doesNotMatch(source, /require\('@deepseek-ai\/dsh-client/);
  assert.match(source, /name: 'sidebar\.brand\.name'/);
  assert.match(rule('.magi-wb-brand'), /display:flex; align-items:center; gap:8px; flex:1/);
  assert.doesNotMatch(rule('.magi-wb-brand'), /grid-template-columns|width:184px/);
  assert.doesNotMatch(rule('.magi-wb-wordmark'), /overflow:hidden/);
  assert.match(rule('.magi-wb-wordmark'), /flex-shrink:0/);
  assert.match(rule('.magi-wb-sidebar-top'), /height:60px; gap:8px; padding:8px 0 8px 4px; margin-bottom:4px/);
  assert.match(rule('html[data-platform=darwin] .magi-wb-chrome + .magi-wb-sidebar-top'), /margin-top:-12px/);
  assert.match(rule('.magi-wb-wordmark'), /white-space:nowrap/);
  assert.match(rule('.magi-wb-badge'), /border:1px solid var\(--dsw-alias-border-l2\)/);
  assert.match(rule('.magi-wb-brand .magi-wb-switch'), /width:32px; height:32px; margin-left:auto/);
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
  assert.doesNotMatch(css.replace(/#(?:ffffff8c|ffffff4d|ffffff26|fff3)\b/g, ''), /#[0-9a-f]{3,8}\b|rgba?\(/i, 'only the version-matched native macOS New Session translucent fills may use literals');
});
