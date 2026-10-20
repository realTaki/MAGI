import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Bus, SHELL_MAGI_HANDLE, messageDelivery, MAGI_CONTACT_ID } from '@magi/bus';
import { createLocalApi } from '@magi/webapp/api.js';
import { createOwnerApi } from '@magi/launcher/composition/owner.js';
const attachment = { name: 'example.txt', mime_type: 'text/plain', data_base64: Buffer.from('durable bytes').toString('base64') };
function context(bus: Bus, home: string) { return { bus, paths: { home, checkout: process.cwd() }, tools: { node: process.execPath, npm: import.meta.filename, git:'git', env:{} }, command:async()=>'', spawn:()=>{throw new Error('unexpected spawn');}, emit(){},openExternal(){},copy(){} }; }
test('Shell bridge reuses existing peer history for replies, download, and reactions after restart', async () => {
 const home=mkdtempSync(join(tmpdir(),'magi-rich-api-')); const workspace=join(home,'shell'); let bus=new Bus(SHELL_MAGI_HANDLE,workspace); let api=createLocalApi(context(bus,home));
 try {
  const contact=bus.contacts.forPeer({principal:'a'.repeat(64),endpoint:'https://peer.example'}); bus.contacts.savePeer(contact.id);
  const room=bus.chats.forPeer(contact.peer_id!); const existingJob=messageDelivery.send(bus,room.id,'existing @peer.magi history',MAGI_CONTACT_ID);
  const existing=bus.board('MessageDeliveryJob').peekInput(existingJob)!.message_id;
  const events=await api['peer.events']({id:String(contact.id)}); assert.equal(events.length,1);
  await api['peer.send']({id:String(contact.id),content:'reply',reply_to:events[0].id,attachments:[attachment]});
  const sent=(await api['peer.events']({id:String(contact.id)}))[1]; assert.equal(sent.reply_to,events[0].id); assert.equal(bus.chats.list('peer').length,1);
  assert.equal((await api['peer.attachment']({id:String(contact.id),message_id:sent.id,attachment_id:sent.attachments[0].id})).data_base64,attachment.data_base64);
  await api['peer.react']({id:String(contact.id),message_id:existing,emoji:'👍'});
  assert.equal((await api['peer.events']({id:String(contact.id)}))[0].reactions[0].mine,true);
  await assert.rejects(api['peer.attachment']({id:'shell',message_id:sent.id,attachment_id:sent.attachments[0].id}),/conversation/);
  await api.dispose(); bus.close(); bus=new Bus(SHELL_MAGI_HANDLE,workspace); api=createLocalApi(context(bus,home));
  const restored=await api['peer.events']({id:String(contact.id)}); assert.equal(restored.length,2); assert.equal(restored[1].reply_to,events[0].id);
  assert.equal((await api['peer.attachment']({id:String(contact.id),message_id:sent.id,attachment_id:sent.attachments[0].id})).data_base64,attachment.data_base64);
  await api['peer.react']({id:String(contact.id),message_id:restored[0].id,emoji:'👍',remove:true});
  assert.equal((await api['peer.events']({id:String(contact.id)}))[0].reactions.length,0);
  const secondRoom=bus.chats.forPeer(contact.peer_id!); messageDelivery.send(bus,secondRoom.id,'another existing conversation',MAGI_CONTACT_ID);
  assert.equal((await api['peer.events']({id:String(contact.id)})).length,3);
  await api['peer.send']({id:String(contact.id),content:'reply to older room',reply_to:sent.id});
  assert.equal(bus.messages.list(room.id).at(-1)?.content,'reply to older room');
  assert.equal((await api['peer.attachment']({id:String(contact.id),message_id:sent.id,attachment_id:sent.attachments[0].id})).data_base64,attachment.data_base64);
 }finally{await api.dispose();bus.close();rmSync(home,{recursive:true,force:true});}
});
test('local owner API keeps attachments, replies, reactions local and rejects structured mixing',()=>{
 const home=mkdtempSync(join(tmpdir(),'magi-rich-owner-')); const bus=new Bus('@local.magi',home);
 try{
  const api=createOwnerApi(bus);api['owner.chat']({content:'first'});const first=api['owner.events']()[0];
  api['owner.chat']({content:'',reply_to:first.id,attachments:[attachment]}); const second=api['owner.events']()[1];
  assert.equal(second.reply_to,first.id);assert.equal(api['owner.attachment']({message_id:second.id,attachment_id:second.attachments![0].id}).data_base64,attachment.data_base64);
  api['owner.react']({message_id:second.id,emoji:'🔥'});assert.equal(api['owner.events']()[1].reactions[0].mine,true);
  assert.throws(()=>api['owner.chat']({content:'',attachments:[attachment],metadata:{card_reject:{id:'card'}}}),/structured/);
  assert.equal(bus.chats.list('peer').length,0);
 }finally{bus.close();rmSync(home,{recursive:true,force:true});}
});
