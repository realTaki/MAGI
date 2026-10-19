import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { Bus, SHELL_MAGI_HANDLE } from '@magi/bus';
import { AspProcessWorker } from '@magi/webapp';
const root=path.resolve(import.meta.dirname,'../../..');
async function fixture(t) {
 const home=await mkdtemp(path.join(tmpdir(),'magi-asp-start-'));
 const bus=new Bus(SHELL_MAGI_HANDLE,path.join(home,'shell'));
 t.after(async()=>{bus.close();await rm(home,{recursive:true,force:true,maxRetries:5,retryDelay:100});});
 return {home,bus};
}

test('cold ASP starts its actual eva launcher once and stops its owned process', {timeout:15000}, async(t)=>{
 const {home,bus}=await fixture(t);
 const server=net.createServer();await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
 const port=(server.address() as net.AddressInfo).port;await new Promise<void>(r=>server.close(()=>r()));
 const children:ReturnType<typeof spawn>[]=[];
 const worker=new AspProcessWorker(bus,{sourceCheckout:root,node:process.execPath,
  origin:new URL(`http://127.0.0.1:${port}`),env:{...process.env,MAGI_HOME:path.join(home,'profile'),MAGI_NPM:path.join(root,'apps/shell/runtime/npm/node_modules/npm/bin/npm-cli.js')},
  spawnProcess(file,args,options){assert.deepEqual(args,[path.join(root,'apps/eva/eva.mjs'),'--role','asp']);assert.equal(options.env?.MAGI_HOST,'0');const child=spawn(file,args,options);children.push(child);return child;}});
 t.after(async()=>{worker.requestShutdown();await worker.stop();for(const c of children){if(c.exitCode===null&&c.signalCode===null){c.kill('SIGTERM');await new Promise(r=>c.once('exit',r));}}});
 await Promise.all([worker.ensure(root),worker.ensure(root)]);
 assert.equal(children.length,1);assert.equal(await worker.probe(),'ready');assert.equal(worker.owned(),true);
 await worker.ensure(root);assert.equal(children.length,1);
 await worker.stopOwned();assert.equal(await worker.probe(),'unavailable');assert.equal(worker.owned(),false);
});

test('ready external ASP is reused without spawning or claiming ownership',async(t)=>{
 const {bus}=await fixture(t);let spawned=0;
 const worker=new AspProcessWorker(bus,{sourceCheckout:root,node:process.execPath,env:process.env,startProcess:async()=>{spawned++;throw Error('unexpected');}});
 worker.probe=async()=> 'ready';await worker.ensure(root);assert.equal(spawned,0);assert.equal(worker.owned(),false);
});

test('failed readiness stops the supervised launcher and preserves failure',async(t)=>{
 const {bus}=await fixture(t);let stopped=0;
 const worker=new AspProcessWorker(bus,{sourceCheckout:root,node:process.execPath,env:process.env,startProcess:async(spec)=>{
 assert.equal(spec.entry,path.join(root,'apps/eva/eva.mjs'));assert.deepEqual(spec.args,['--role','asp']);
 return {id:'asp',stop(){stopped++;},onExit(){}};}});
 let probes=0;worker.probe=async()=>++probes===1?'unavailable':'incompatible';
 await assert.rejects(worker.ensure(root),/older ASP/);assert.equal(stopped,1);assert.equal(worker.owned(),false);
});

test('shutdown during admission stops a late supervised launcher',async(t)=>{
 const {bus}=await fixture(t);let stopped=0;
 let finish:any;const admitted=new Promise<any>(r=>{finish=r;});
 const worker=new AspProcessWorker(bus,{sourceCheckout:root,node:process.execPath,env:process.env,startProcess:()=>admitted});
 worker.probe=async()=> 'unavailable';const pending=worker.ensure(root);await new Promise(r=>setImmediate(r));worker.requestShutdown();await worker.stop();
 finish({id:'late',stop(){stopped++;},onExit(){}});await assert.rejects(pending,/shutting down/);assert.equal(stopped,1);
});
