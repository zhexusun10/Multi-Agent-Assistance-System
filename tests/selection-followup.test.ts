import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {ActionService,actionHash} from '../src/domain/actions.js';
import {displayedCandidates,resolveListingSelection} from '../src/domain/selection.js';
import {criterion} from '../src/domain/requirements.js';
import {planIntent} from '../src/domain/intents.js';
import {hash,type DomainState} from '../src/stores/domain.js';

async function fixture(){const rec=JSON.parse(await readFile('tests/fixtures/recommendation-response.json','utf8')),candidate=structuredClone(rec.recommendations[0]);candidate.listing.listing_id='990001';const bath=criterion('amenities','eq','private_bathroom');candidate.criterion_results.push({criterion_id:bath.criterion_id,status:'unknown',observed_value:null,evidence_ids:['e-private']});rec.recommendations=[];rec.unverified_candidates=[candidate];const s={session_id:'test-session',requirements_version:rec.requirements_version,latest_request_id:rec.request_id,recommendation:rec,requirements:{listing_type:'RENT',criteria:[bath]}} as DomainState;return {s,rec,candidate};}
test('unverified candidates can create inquiries with explicit evidence-linked questions, without passing unknown conditions',async()=>{
 const {s,candidate}=await fixture(),a=new ActionService(null as never).draft(s,[candidate.listing.listing_id],'VIEWING');assert.equal(a.verification_context?.[0].group,'unverified');assert((a.body).toLowerCase().includes("private bathroom"));assert((a.body).toLowerCase().includes("does the room have a private bathroom"));assert(!(a.body).toLowerCase().includes("no evidence"));assert(!(a.body).toLowerCase().includes("not a confirmed booking"));assert.deepEqual(a.verification_context?.[0].unresolved[0].evidence_ids,['e-private']);assert.equal(candidate.criterion_results.at(-1).status,'unknown');assert.equal(a.recipient.verified,false);assert.equal(a.status,'DRAFT');assert.equal(a.execution,null);
 const previous=actionHash(a);a.verification_context![0].unresolved[0].requirement='changed';assert.notEqual(actionHash(a),previous);
});
test('current membership, source version and hard failures still guard follow-up drafting',async()=>{
 const {s,candidate}=await fixture(),actions=new ActionService(null as never);assert.throws(()=>actions.draft(s,['not-visible'],'MESSAGE'),/current verified home|marked for confirmation/i);assert.throws(()=>actions.draft({...s,latest_request_id:'old'},[candidate.listing.listing_id],'MESSAGE'),/current list/i);candidate.criterion_results.at(-1).status='fail';assert.throws(()=>actions.draft(s,[candidate.listing.listing_id],'MESSAGE'),/fails a hard requirement/i);
});
test('selection uses stable IDs or the same global numbering as both visible groups',async()=>{
 const {rec,candidate}=await fixture(),verified=structuredClone(candidate);verified.listing.listing_id='990002';verified.rank=1;rec.recommendations=[verified];assert.deepEqual(displayedCandidates(rec).map(c=>c.listing.listing_id),['990002','990001']);assert.equal(resolveListingSelection(rec,'我要看第2套').listing.listing_id,'990001');assert.equal(resolveListingSelection(rec,'请联系待核实候选第1套').listing.listing_id,'990001');assert.equal(resolveListingSelection(rec,'请为房源ID 990001 生成看房询问草稿').listing.listing_id,'990001');assert.throws(()=>resolveListingSelection(rec,'我要看这套'),/Select Contact agent/i);assert.throws(()=>resolveListingSelection(rec,'房源ID 12345678'),/not in the current list/i);
});
test('colloquial interest routes to viewing; legacy action hashes remain compatible',async()=>{
 const {s,candidate}=await fixture();assert.equal(planIntent({intent:'arrange_viewing',intent_group:'followup',confidence:.9,requested_tasks:['viewing']}).kind,'viewing');const a=new ActionService(null as never).draft(s,[candidate.listing.listing_id],'MESSAGE');delete a.verification_context;assert.equal(actionHash(a),hash({source_request_id:a.source_request_id,requirements_version:a.requirements_version,listing_ids:a.listing_ids,action_type:a.action_type,subject:a.subject,body:a.body,recipient:a.recipient,slots:a.slots,next_followup_at:a.next_followup_at}));
});
