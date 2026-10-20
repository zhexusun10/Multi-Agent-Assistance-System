import test from 'node:test';
import assert from 'node:assert/strict';
import {KnowledgeService,groundedClaims,type KnowledgeChunk} from '../src/domain/knowledge.js';
import {parseLocationIntents} from '../src/domain/geo-intake.js';
const c:KnowledgeChunk={chunk_id:'hdb:1',document_id:'hdb',title:'组屋',heading:'租期',text:'HDB 卧室出租的最低租期为六个月。游客不能当作合资格租客。',scope:'policy_guidance',revision:'1',checked_at:'2026-01-01',review_after:'2027-01-01',source_ref:'knowledge/rental/hdb.md',sources:[{title:'HDB',url:'https://www.hdb.gov.sg/'}],rrf_score:.03,vector_rank:1,bm25_rank:1,cosine_similarity:.8};
const claim=(text:string,quote=c.text,chunk_id=c.chunk_id)=>({supported:true,claims:[{text,evidence:[{chunk_id,quote}]}]});
const recall={status:'ok',chunks:[c],generation:'v1',vector_enabled:true,bm25_enabled:true,degraded:[]};
test('answer grounding rejects invented IDs, quotes, unsupported numbers and unapproved origins',()=>{
 assert.equal(groundedClaims(claim('HDB最低租期6个月'),[c]).length,1);
 assert.equal(groundedClaims(claim('HDB最低租期3个月'),[c]).length,0);
 assert.equal(groundedClaims(claim('至少六个月','不存在的原文摘录句子'),[c]).length,0);
 assert.equal(groundedClaims(claim('至少六个月',c.text,'evil'),[c]).length,0);
 assert.equal(groundedClaims(claim('至少六个月'),[{...c,sources:[{title:'假网站',url:'https://www.hdb.gov.sg.evil.test'}]}]).length,0);
 const geo={...c,text:'区域内距离记为零；不是距车站零米。'};
 assert.equal(groundedClaims(claim('区域内记为0米，不是距车站0米',geo.text),[geo]).length,1);
});
test('service distinguishes outage, expired source and extractive model fallback',async()=>{
 const unavailable=await new KnowledgeService('hybrid_rrf',undefined,{request:async()=>{throw Error('offline');}}).query('租期');assert.equal(unavailable.status,'unavailable');assert.equal(unavailable.citations.length,0);
 const expired=await new KnowledgeService('hybrid_rrf',undefined,{request:async()=>({...recall,chunks:[{...c,review_after:'2020-01-01'}]})}).query('租期');assert.equal(expired.status,'empty');
 const excerpt=await new KnowledgeService('hybrid_rrf',undefined,{request:async()=>recall}).query('租期');assert.equal(excerpt.method,'source_excerpt');assert.equal(excerpt.citations[0].quote,c.text);
});
test('semantic verification can reject a syntactically valid quote, and generation change invalidates answer cache',async()=>{
 let calls=0,generation='v1';const model={invoke:async()=>{calls++;return {text:JSON.stringify(calls%3===1?{queries:[]}:calls%3===2?claim('游客可以租组屋'): {supported_indices:[]})};}} as any;
 const service=new KnowledgeService('hybrid_rrf',model,{request:async()=>({...recall,generation})});
 assert.equal((await service.query('游客可以租吗')).status,'unsupported');
 const first=calls;await service.query('游客可以租吗');assert.equal(calls,first);
 generation='v2';await service.query('游客可以租吗');assert(calls>first);
});
test('a failed verbatim quote is repaired once and revalidated rather than silently losing the core answer',async()=>{
 let stage=0;const model={invoke:async()=>({text:JSON.stringify([{queries:[]},claim('HDB最低租期6个月','HDB 卧室出租的最低租期为六个月的。'),claim('HDB最低租期6个月','HDB 卧室出租的最低租期为六个月。'),{supported_indices:[0]}][stage++])})} as any;
 const r=await new KnowledgeService('hybrid_rrf',model,{request:async()=>recall}).query('组屋租多久');
 assert.equal(r.status,'ok');assert.equal(r.citations[0].quote,'HDB 卧室出租的最低租期为六个月。');assert.match(r.answer,/6个月/);assert.equal(stage,4);
});
test('a Chinese named place with a numeric radius does not require the word nearby or an English suffix',()=>{
 const r=parseLocationIntents('我要租新加坡国立大学三公里内的房子，预算2000');assert.equal(r[0].name,'新加坡国立大学');assert.equal(r[0].radius_m,3000);assert.equal(r[0].strength,'hard');
 assert.equal(parseLocationIntents('面积至少500平方米的房子').length,0);
 assert.equal(parseLocationIntents('最好超市直线距离800米以内').length,0);
});
