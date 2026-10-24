import test from 'node:test';
import assert from 'node:assert/strict';
import {matchSummary,money,renderMatches} from '../frontend/property-presentation.js';

const place={field:'place_proximity',strength:'hard',value:5000,distance_type:'straight_line',criterion_id:'distance',target:{name:'Nanyang Technological University',place_id:'OSM:way:115105262'}};
const listing={bedrooms:0,floor_area_sqft:null,property_type:'HDB Flat',availability_status:'unknown'};
const format=(c:any)=>'必须 · '+c.field;

test('actual observed distance is visible and shared hard/soft destination appears once',()=>{
  const soft={...place,strength:'soft',criterion_id:'nearby'};
  const view=matchSummary({listing,criterion_results:[{criterion_id:'nearby',status:'pass',observed_value:811.64},{criterion_id:'distance',status:'pass',observed_value:811.64}]},[soft,place],format);
  assert.equal(view.rows.length,1);assert.match(view.rows[0].text,/南洋理工大学 812 米/);assert.match(view.rows[0].text,/5,000 米/);
  assert.deepEqual(view.pending,['卧室数量','室内面积','当前是否可租']);
});
test('missing or unknown distance never becomes zero or a passed match',()=>{
  for(const observed_value of [null,undefined,NaN]){
    const view=matchSummary({listing,criterion_results:[{criterion_id:'distance',status:'unknown',observed_value}]},[place],format);
    assert.equal(view.rows.length,0);assert(view.pending.includes('到南洋理工大学的距离'));
  }
});
test('zero polygon distance means within an area, without claiming zero distance to a station',()=>{
  const c={...place,target:{name:'Clementi',place_id:'URA:CLEMENTI'}};
  const view=matchSummary({listing,criterion_results:[{criterion_id:'distance',status:'pass',observed_value:0}]},[c],format);
  assert.equal(view.rows[0].text,'位于Clementi区域内');
});

test('official Clementi boundary distances remain visible alongside all other matched fields; missing coordinates are explained',()=>{
  const c={...place,strength:'soft',distance_preference:'nearer',target:{name:'CLEMENTI',place_id:'URA:AREA:CLEMENTI'}};
  const candidate={listing:{...listing,bedrooms:1,floor_area_sqft:800},criterion_results:[{criterion_id:'distance',status:'fail',observed_value:6299.645}]};
  const view=matchSummary(candidate,[c],format);
  assert.match(view.rows[0].text,/金文泰区域边界 6,300 米（直线，更近优先）/);assert.equal(view.rows[0].status,'preference');assert(!view.pending.some(t=>t.includes('金文泰')));
  candidate.criterion_results[0]={criterion_id:'distance',status:'unknown',observed_value:null,reason:'原生证据未知：missing valid listing coordinates'} as any;
  const missing=matchSummary(candidate,[c],format);assert(missing.pending.includes('到金文泰的距离（缺少房源坐标）'));
});
test('distinct destinations and failed criteria keep their own measurements and status',()=>{
  const nus={...place,criterion_id:'nus',target:{name:'National University of Singapore',place_id:'OSM:other'}};
  const view=matchSummary({listing,criterion_results:[{criterion_id:'distance',status:'pass',observed_value:811.64},{criterion_id:'nus',status:'fail',observed_value:8000}]},[place,nus],format);
  assert.equal(view.rows.length,2);assert.equal(view.rows[1].status,'fail');assert.match(view.rows[1].text,/8,000 米/);
});
test('rent uses the requested dollar format and missing values remain unknown',()=>{
  assert.equal(money(1100),'$1,100');assert.equal(money(null),'租金待确认');assert.equal(money(NaN),'租金待确认');
});
test('a failed rent comparison is never described as within budget',()=>{
  const c={field:'price',criterion_id:'rent',value:2000,operator:'lte',strength:'hard'};
  const view=matchSummary({listing,criterion_results:[{criterion_id:'rent',status:'fail',observed_value:2500}]},[c],format);
  assert.equal(view.rows[0].status,'fail');assert.match(view.rows[0].text,/超过/);assert(!view.rows[0].text.includes('预算内'));
});
test('an explicit soft radius remains visible when transit facts are unknown',()=>{
  const c={...place,strength:'soft',distance_preference:'bounded',value:1000,target:{name:'MRT或公交站',category:'PublicTransport'}};
  const view=matchSummary({listing,criterion_results:[{criterion_id:'distance',status:'unknown',observed_value:null}]},[c],format);
  assert(view.pending.some(x=>x.includes('MRT或公交站')&&x.includes('1,000 米')));
  const hard={...place,distance_preference:'bounded'};
  const nearer={...place,strength:'soft',criterion_id:'nearer',distance_preference:'nearer'};
  const unknown=matchSummary({listing,criterion_results:[{criterion_id:'distance',status:'unknown'},{criterion_id:'nearer',status:'unknown'}]},[hard,nearer],format);
  assert.equal(unknown.pending.filter(x=>x.includes('南洋理工大学')).length,1);
  assert(unknown.pending.some(x=>x.includes('5,000 米')));
});

test('nearer preference is directional, never a failed radius based on an internal scale',()=>{
  const c={...place,strength:'soft',distance_preference:'nearer',operator:'lte',value:1000};
  const candidate={listing,criterion_results:[{criterion_id:'distance',status:'fail',observed_value:2517.25}]},before=structuredClone(candidate);
  const view=matchSummary(candidate,[c],format);assert.equal(view.rows[0].status,'preference');
  assert.match(view.rows[0].text,/2,517 米.*更近优先/);assert(!view.rows[0].text.includes('1,000'));assert.deepEqual(candidate,before);
  const bounded=matchSummary(candidate,[{...c,distance_preference:'bounded'}],format);
  assert.equal(bounded.rows[0].status,'preference');assert.match(bounded.rows[0].text,/希望 ≤ 1,000 米/);
});

test('all known soft preferences use neutral badges in cards and details, without changing recommendation evidence',()=>{
  const criteria=[
    {...place,strength:'soft',distance_preference:'nearer',operator:'lte',value:1000},
    {...place,strength:'soft',criterion_id:'supermarket',distance_preference:'bounded',operator:'lte',value:800,target:{name:'超市',category:'Supermarket'}},
    {criterion_id:'gym',field:'amenities',value:'gym',strength:'soft'},
    {criterion_id:'rent',field:'price',operator:'lte',value:5000,strength:'hard'},
  ];
  const el=(tag:string,className:string,text?:string)=>({tag,className,text,children:[] as any[],append(...children:any[]){this.children.push(...children);}});
  for(const status of ['pass','fail']){
    const candidate={listing,criterion_results:criteria.map((c,i)=>({criterion_id:c.criterion_id,status,observed_value:[2517.25,60,true,1400][i]}))},before=structuredClone(candidate);
    for(const detail of [false,true]){
      const section=renderMatches({el,candidate,criteria,formatCriterion:format,detail});
      const badges=section.children[1].children;
      assert.equal(badges.length,4);
      for(const badge of badges.slice(1)){
        assert.equal(badge.className,'match-chip preference');
        assert.match(badge.text,/^偏好 · /);
        assert.doesNotMatch(badge.text,/[✓×↗]/);
      }
      assert.equal(badges[0].className,'match-chip '+status);
      assert.match(badges[0].text,status==='pass'?/^✓ /:/^× /);
      assert.match(badges[1].text,/2,517 米.*更近优先/);
      assert.match(badges[2].text,/60 米.*希望 ≤ 800 米/);
    }
    assert.deepEqual(candidate,before);
  }
});
