import {pointToPolygonDistance} from '@turf/point-to-polygon-distance';
import type {Polygon,MultiPolygon} from 'geojson';
import type {Entity} from '../adapters/data-http.js';

export function geodesic(lat1:number,lon1:number,lat2:number,lon2:number){const rad=(v:number)=>v*Math.PI/180,a=Math.sin(rad(lat2-lat1)/2)**2+Math.cos(rad(lat1))*Math.cos(rad(lat2))*Math.sin(rad(lon2-lon1)/2)**2;return 6371008.8*2*Math.atan2(Math.sqrt(Math.min(1,a)),Math.sqrt(Math.max(0,1-a)));}
export function distanceToEntity(lat:number,lon:number,entity:Entity):number|null{
  const p=entity.properties;
  if(p.geometry){const signed=pointToPolygonDistance([lon,lat],p.geometry as Polygon|MultiPolygon,{units:'meters',method:'geodesic'});return Math.max(0,signed);}
  const points=Array.isArray(p.reference_points)?p.reference_points:[p];
  const distances=points.filter(v=>typeof v.latitude==='number'&&typeof v.longitude==='number').map(v=>geodesic(lat,lon,v.latitude,v.longitude));
  return distances.length?Math.min(...distances):null;
}
export const proximityUtility=(distance:number,pivot:number)=>pivot/(pivot+distance);
