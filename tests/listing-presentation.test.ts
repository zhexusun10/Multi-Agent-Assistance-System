import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeListingDetails,normalizePhotos,normalizeNearby,propertyImageUrl,ListingPresentationService} from '../src/domain/listing-presentation.js';

const photo=(id:number,url:string,type='PHOTO',order=id)=>({id,image_url:url,image_type:type,caption:'recorded image',source_page:'DETAIL_PAGE',display_order:order});
test('display facts retain unmentioned bathroom/area/address fields without claiming private use or inferring facilities',()=>{
 const raw={listing_id:1,title:'Example',bathrooms:2,bedrooms:0,floor_area_sqft:500,full_address:'370 Pasir Panjang Road',tenure:'Freehold',agent_phone:'not for this API',raw_json:{private_bathroom:true}};
 const facts=normalizeListingDetails(raw);assert.equal(facts.bathrooms,2);assert.equal(facts.bedrooms,0);assert.equal(facts.floor_area_sqft,500);assert.equal(facts.full_address,raw.full_address);assert.equal(facts.tenure,'Freehold');assert.equal(facts.amenities,undefined);assert.equal(facts.agent_phone,undefined);assert.equal(facts.raw_json,undefined);assert.equal(facts.nearest_mrt,null);
 const invalid=normalizeListingDetails({bedrooms:-1,bathrooms:'2',floor_area_sqft:NaN});assert.equal(invalid.bedrooms,null);assert.equal(invalid.bathrooms,null);assert.equal(invalid.floor_area_sqft,null);
});
test('gallery deduplicates thumbnail/preview/photo sizes and keeps floor plans; arbitrary image hosts are rejected',()=>{
 const photos=normalizePhotos([photo(1,'https://sg1-cdn.pgimgs.com/listing/1/UPHO.77.V550/a.jpg','THUMBNAIL'),photo(2,'https://sg1-cdn.pgimgs.com/listing/1/UPHO.77.V800/a.jpg','PREVIEW'),photo(3,'https://sg1-cdn.pgimgs.com/listing/1/UPHO.77.V1600/a.jpg'),photo(4,'https://sg1-cdn.pgimgs.com/listing/1/UPHO.88.V800/a.jpg','FLOOR_PLAN'),photo(5,'https://evil.example/tracker.jpg'),photo(6,'https://sg1-cdn.pgimgs.com/agent.jpg','AVATAR')]);
 assert.equal(photos.length,2);assert(photos[0].url.includes('V1600'));assert.equal(photos[1].kind,'floor_plan');assert.equal(propertyImageUrl('http://sg1-cdn.pgimgs.com/a.jpg'),null);assert.equal(propertyImageUrl('https://sg1-cdn.pgimgs.com.evil.example/a.jpg'),null);assert.equal(propertyImageUrl('https://u:p@sg1-cdn.pgimgs.com/a.jpg'),null);
});
test('surrounding amenities preserve zero distances and separate OSM straight-line evidence from platform MRT annotations',()=>{
 const rows=normalizeNearby([
  {type:'NEAR_PLACE',target:{entity_id:'Place:park',label:'Park',properties:{name:'Park',category:'Park',source_url:'https://www.openstreetmap.org/way/10'}},evidence:{distance_m:0}},
  {type:'NEAR_MRT',target:{entity_id:'MRT:x',label:'MRT',properties:{station_name:'Kent Ridge'}},evidence:{distance_m:700,walking_mins:10}},
  {type:'NEAR_PLACE',target:{entity_id:'Place:bad',label:'School',properties:{name:'Missing distance'}},evidence:{distance_m:null}},
  {type:'NEAR_PLACE',target:{entity_id:'Place:room',label:'CampusBuilding',properties:{name:'Room 1',category:'CampusBuilding'}},evidence:{distance_m:20}},
 ]);assert.equal(rows.length,2);assert.equal(rows[0].distance_m,0);assert.equal(rows[0].metric,'straight_line');assert.equal(rows[1].metric,'platform_reported');assert.equal(rows[1].walking_mins,10);assert(rows.every(p=>p.route_verified===false));
});
test('partial data failure is displayed as unavailable, not an empty/no-facilities fact; duplicate requests share one load',async()=>{
 let calls=0;const service=new ListingPresentationService({async request(path){calls++;if(path.endsWith('/context'))throw Error('Neo4j unavailable');if(path.includes('/images'))return {items:[],has_more:false,next_cursor:null};return {item:{entity_id:'Listing:1',label:'Listing',properties:{listing_id:1,bathrooms:2}}};}},'test-dataset');
 const [a,b]=await Promise.all([service.get('1'),service.get('1')]);assert.equal(calls,3);assert.deepEqual(a,b);assert.equal(a.details!.bathrooms,2);assert.equal(a.sections.nearby,'unavailable');assert(a.warnings.some(x=>x.includes('does not mean no amenities')));assert.equal(a.sections.photos,'available');await service.get('1');assert.equal(calls,3);await assert.rejects(service.get('../secret'));
});
