import { fileURLToPath } from 'node:url';
import { EvaRuntime } from '../runtime.js';

const base = process.argv[2];
const manager = new EvaRuntime({ executable: process.execPath, cli: fileURLToPath(new URL('./fake-cli.js', import.meta.url)), runtimeDir: '/unused', supportDir: '/unused' }, { homeRoot: base });
process.once('SIGTERM', () => { void manager.dispose().finally(() => process.exit(0)); });
const result = await manager.start(base, 'tree');
const node = [...manager.nodes.values()][0];
process.send({ pid: result.pid, leafPid: node.ready.leafPid });
