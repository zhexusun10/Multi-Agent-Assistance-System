import test from 'node:test';
import assert from 'node:assert/strict';
import {MessagingGateway,DeliveryError,normalizeAddress,renderTemplate,type SendMessage} from '../src/integrations/messaging.js';
import {wireBody,actionHash,type Action} from '../src/domain/actions.js';
import {singaporeDate} from '../src/domain/followup.js';
const env={EXTERNAL_ACTION_MODE:'live',TELEGRAM_BOT_TOKEN:'test-token-never-real',WHATSAPP_ACCESS_TOKEN:'fake-wa',WHATSAPP_PHONE_NUMBER_ID:'12345',WHATSAPP_BUSINESS_ACCOUNT_ID:'98765',WHATSAPP_GRAPH_API_VERSION:'v26.0'};
const message=(channel:'telegram'|'whatsapp'):SendMessage=>({recipient:{name:'Test salesperson',verified:true,opt_in:true,channel,address:channel==='telegram'?'123456789':'+6591234567'},body:'请核实独立卫浴；希望月租协商至1800。',lastInboundAt:new Date().toISOString()});
test('Telegram sends exact reviewed text and recipient to official endpoint, never fabricates delivery/read status',async()=>{
  let n=0;const gateway=new MessagingGateway(env,(async(url,options)=>{n++;assert.equal(String(url),'https://api.telegram.org/bottest-token-never-real/sendMessage');const body=JSON.parse(String(options?.body));assert.deepEqual(body,{chat_id:'123456789',text:message('telegram').body});assert.equal(options?.redirect,'error');assert.equal(body.parse_mode,undefined);return Response.json({ok:true,result:{message_id:71}});}) as typeof fetch);
  assert.deepEqual(await gateway.send(message('telegram')),{message_id:'71',status:'ACCEPTED'});assert.equal(n,1);
});
test('WhatsApp free text requires a trusted recent incoming message and uses bearer authentication',async()=>{
  let calls=0;const gateway=new MessagingGateway(env,(async(url,options)=>{calls++;assert.equal(String(url),'https://graph.facebook.com/v26.0/12345/messages');assert.equal((options?.headers as any).authorization,'Bearer fake-wa');const body=JSON.parse(String(options?.body));assert.equal(body.to,'6591234567');assert.equal(body.text.body,message('whatsapp').body);return Response.json({messages:[{id:'wamid.123'}]});}) as typeof fetch);
  for(const date of [null,new Date(Date.now()-25*3600000).toISOString(),new Date(Date.now()+3600000).toISOString()])await assert.rejects(gateway.send({...message('whatsapp'),lastInboundAt:date}),/24 小时/);
  assert.equal(calls,0);assert.equal((await gateway.send(message('whatsapp'))).message_id,'wamid.123');assert.equal(calls,1);
});
test('WhatsApp window outside uses approved text template; rendered preview must exactly match the payload',async()=>{
  const calls:any[]=[];const gateway=new MessagingGateway(env,(async(url,options)=>{calls.push({url,options});if(String(url).includes('/message_templates'))return Response.json({data:[{name:'property_inquiry',language:'en',status:'APPROVED',components:[{type:'BODY',text:'Hello, {{1}}'}]},{name:'not_approved',status:'PENDING',components:[{type:'BODY',text:'x'}]},{name:'image_template',status:'APPROVED',components:[{type:'BODY',text:'x'},{type:'HEADER',format:'IMAGE'}]}]});const b=JSON.parse(String(options?.body));assert.equal(b.template.name,'property_inquiry');assert.equal(b.template.components[0].parameters[0].text,'请核实独立卫浴');return Response.json({messages:[{id:'wamid.template'}]});}) as typeof fetch);
  const templates=await gateway.templates();assert.equal(templates.length,1);const body=renderTemplate(templates[0],['请核实独立卫浴']);assert.equal(body,'Hello, 请核实独立卫浴');
  await assert.rejects(gateway.send({...message('whatsapp'),body:'silently changed',lastInboundAt:null,template:{name:'property_inquiry',language:'en',parameters:['请核实独立卫浴']}}),/不一致/);
  await gateway.send({...message('whatsapp'),body,lastInboundAt:null,template:{name:'property_inquiry',language:'en',parameters:['请核实独立卫浴']}});assert.equal(calls.filter(c=>String(c.url).endsWith('/messages')).length,1);
});
test('provider missing credentials, consent or invalid channel addresses fail before HTTP',async()=>{
  let called=0;const gateway=new MessagingGateway({EXTERNAL_ACTION_MODE:'live'},(async()=>{called++;throw Error('must not call');}) as typeof fetch);
  await assert.rejects(gateway.send(message('telegram')),/凭证/);await assert.rejects(gateway.send({...message('telegram'),recipient:{...message('telegram').recipient,opt_in:false}}),/允许/);
  assert.throws(()=>normalizeAddress('telegram','@some_private_user'),/chat_id/);assert.throws(()=>normalizeAddress('whatsapp','91234567'),/号码/);
  assert.equal(called,0);await assert.rejects(new MessagingGateway({...env,EXTERNAL_ACTION_MODE:'simulation'}).send(message('telegram')),/模拟/);
});
test('definite rejection differs from timeout/unknown response; neither automatically retries',async()=>{
  for(const [status,outcome] of [[400,'FAILED'],[403,'FAILED'],[429,'FAILED'],[500,'UNKNOWN']] as const){let count=0;const g=new MessagingGateway(env,(async()=>{count++;return Response.json({ok:false,error_code:status,description:'do not leak tokens'},{status});}) as typeof fetch);await assert.rejects(g.send(message('telegram')),(e:any)=>e instanceof DeliveryError&&e.outcome===outcome&&!e.message.includes('token'));assert.equal(count,1);}
  let n=0;const g=new MessagingGateway(env,(async()=>{n++;throw Error('private token-bearing URL');}) as typeof fetch);await assert.rejects(g.send(message('telegram')),(e:any)=>e.outcome==='UNKNOWN'&&!e.message.includes('private'));assert.equal(n,1);
  await assert.rejects(new MessagingGateway(env,(async()=>Response.json({ok:true,result:{}})) as typeof fetch).send(message('telegram')),(e:any)=>e.outcome==='UNKNOWN');
});
test('wire body includes negotiation and viewing slots, and those fields invalidate previous approval hashes',()=>{
  const a={source_request_id:'x',requirements_version:1,listing_ids:['1'],action_type:'VIEWING',subject:'询问',body:'请核实房源',recipient:message('telegram').recipient,slots:{starts_at:'2026-12-01T06:00:00.000Z',ends_at:'2026-12-01T07:00:00.000Z',time_zone:'Asia/Singapore'},next_followup_at:null,negotiation_notes:'希望协商到1800'} as Action;
  assert(wireBody(a).includes('1800'));assert(wireBody(a).includes('2026年12月1日'));assert(wireBody(a).includes('2:00 PM–3:00 PM'));assert(!wireBody(a).includes(a.slots!.starts_at));const h=actionHash(a);a.recipient.address='777';assert.notEqual(actionHash(a),h);const h2=actionHash(a);a.negotiation_notes='不同诉求';assert.notEqual(actionHash(a),h2);
  assert.equal(singaporeDate(new Date('2026-10-18T16:01:00Z')),'2026-10-19');
});
