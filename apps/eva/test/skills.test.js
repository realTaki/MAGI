import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { registerEvaSkill } from '../skill.js';

const document = new URL('../skills/eva-nodes/SKILL.md', import.meta.url);

test('EVA skill registers from its package and follows the optional service scope lifecycle', () => {
  let activate;
  const active = new Map();
  registerEvaSkill({ inject(dependencies, callback) {
    assert.deepEqual(dependencies, ['skills']); activate = callback;
  } });
  assert.equal(active.size, 0, 'no registration without the optional service');
  for (let reload = 0; reload < 2; reload++) {
    const cleanups = [];
    activate({ effect(callback) { cleanups.push(callback()); }, skills: { register(skill) {
      assert.equal(active.size, 0);
      active.set(skill.name, skill);
      return () => { assert.equal(active.delete(skill.name), true); };
    } } });
    const skill = active.get('magi-eva-nodes');
    assert.equal(skill.path, fileURLToPath(document));
    assert.deepEqual(skill.resourceBase, { kind: 'directory', path: fileURLToPath(new URL('../skills/eva-nodes/', import.meta.url)) });
    assert.equal(skill.source, 'runtime');
    assert.equal(skill.content, readFileSync(document, 'utf8'));
    assert.ok(skill.content.split(/\r?\n/).includes(`description: ${skill.description}`));
    for (const off of cleanups.reverse()) off();
    assert.equal(active.size, 0, 'service removal or plugin unload removes the catalog contribution');
  }
});

test('EVA packages the operational skill without changing hard dependencies or child composition', () => {
  const read = name => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8');
  const pkg = JSON.parse(read('package.json'));
  assert.ok(pkg.files.includes('skill.js'));
  assert.ok(pkg.files.includes('skills/'));
  const entry = read('index.js');
  assert.match(entry, /import \{ registerEvaSkill \} from '\.\/skill\.js'/);
  assert.match(entry, /if \(isEvaChild\(ctx\.profileContext\.home\)\) return;\s+registerEvaSkill\(ctx\);/);
  assert.doesNotMatch(entry.match(/export const inject = .*;/)[0], /skills/);
  assert.doesNotMatch(read('child.js'), /registerEvaSkill/);
  const content = readFileSync(document, 'utf8');
  for (const phrase of ['one-shot approval', 'Every unnamed `start`', '`not-started`', 'main-Session mappings', 'credential store', 'not an OS security boundary']) assert.ok(content.includes(phrase), phrase);
  assert.doesNotMatch(content, /load_skill|@magi\/bus|McpWorker/);
});
