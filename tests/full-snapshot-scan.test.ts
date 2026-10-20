import test from 'node:test';
import assert from 'node:assert/strict';
import {DataHttpAdapter} from '../src/adapters/data-http.js';
import {ToolExecutor,type ToolContext} from '../src/tools/executor.js';

const context=():ToolContext=>({sessionId:'full-scan',operationId:'op',agent:'b',dataset:'fixed',deadline:Date.now()+30000,calls:0});
function mock(expected:number,mode:'ok'|'missing'|'duplicate'|'cursor'='ok'){
  let page=0,saved=0;
  const adapter=new DataHttpAdapter('http://127.0.0.1:8089',new ToolExecutor(),async()=>`page-${++saved}`);
  adapter.request=async(path)=>{
    if(path.endsWith('capabilities'))return {canonical_assessment_scan:'/api/v1/listings/assessment/search',listing_snapshot:{expected_counts:{properties:expected}}};
    assert.equal(path,'/api/v1/listings/assessment/search');const start=page++*1000,count=Math.min(1000,2501-start),has_more=start+count<2501;
    return {items:Array.from({length:count},(_,i)=>({entity_id:'Listing:'+(mode==='duplicate'?1:start+i+1),label:'Listing',properties:{listing_id:start+i+1,price:null,latitude:null}})),page_size:1000,has_more:mode==='missing'?false:has_more,next_cursor:mode==='missing'?null:has_more?(mode==='cursor'?'same':String(page)):null,source:{store:'postgresql',role:'canonical_listings'},warnings:[]};
  };
  return adapter;
}
test('canonical scan exceeds the old 2000 limit and preserves unknowns with resolvable page evidence',async()=>{
  const result=await mock(2501).assessment(context());assert.equal(result.items.length,2501);assert.equal(result.truncated,false);assert.equal(result.refs.length,3);
  assert.equal(result.rowRefs.get('Listing:2501'),'page-3');assert.equal(result.items[2500].properties.price,null);
});
test('a missing page, duplicated entity or repeated cursor never becomes complete full-snapshot coverage',async()=>{
  await assert.rejects(()=>mock(2501,'missing').assessment(context()),/FULL_DATASET_COUNT_MISMATCH/);
  await assert.rejects(()=>mock(2501,'duplicate').assessment(context()),/DATA_DUPLICATE_ENTITY/);
  await assert.rejects(()=>mock(2501,'cursor').assessment(context()),/DATA_REPEATED_CURSOR/);
});
