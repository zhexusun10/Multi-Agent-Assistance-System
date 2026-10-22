import {z} from 'zod';

// A score scale is not a user-specified radius and never excludes a candidate.
export const DEFAULT_PROXIMITY_SCALE_M=1000;
export const PLACE_CATEGORIES=['MRTStation','BusStop','PublicTransport','Supermarket','School','Park','FoodCourt','Mall','Market','Hospital','Clinic','Library'] as const;
export const locationIntentSchema=z.object({name:z.string().min(1).max(150),quote:z.string().min(1),radius_m:z.number().nonnegative().max(1000000).optional(),range_m:z.tuple([z.number().nonnegative(),z.number().nonnegative()]).optional(),operator:z.enum(['lte','gte','between']).optional(),weight:z.number().positive().optional(),strength:z.enum(['hard','soft']),distance_type:z.enum(['straight_line','walking','driving','transit']),explicit_station:z.boolean(),prefer_closer:z.boolean().optional(),category:z.enum(PLACE_CATEGORIES).optional()}).strict();
export type LocationIntent=z.infer<typeof locationIntentSchema>;
export const CATEGORY_NAMES:[RegExp,string][]=[[/^(?:超市|supermarket)$/i,'Supermarket'],[/^(?:学校|school)$/i,'School'],[/^(?:公园|park)$/i,'Park'],[/^(?:食阁|food court)$/i,'FoodCourt'],[/^(?:商场|mall)$/i,'Mall']];
const digits:Record<string,number>={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'半':0.5};
/** A listing is the subject of a proximity relation, never its geographic destination. */
export function isListingReference(name:string){return /^(?:(?:这套|那套|这|那|该|所租的|我租的|我要租的|想租的)\s*)?(?:房子|房屋|房源|住房|住宅|房间|单间|公寓|房|property|listing|home|house|apartment|room)$/i.test(name.trim());}
export function isGenericMrt(name:string){return /^(?:(?:最近的?|任意的?|任一|一个|一座|附近的)|(?:the|an?|any|nearest)\s+)?(?:MRT(?:\s*(?:station|站))?|地铁站?|捷运站)$/i.test(name.trim());}
export function parseLocationIntents(query:string):LocationIntent[]{
  const intents:LocationIntent[]=[];
  for(const clause of query.split(/[，,。；;\n]/).map(s=>s.trim()).filter(Boolean)){
    let name='',tail='',bareDistance=false;
    // Resolve the destination on the other side of "房子...附近有MRT", before the general nearby rule.
    const subject=clause.replace(/^(?:(?:要求|必须|希望|最好|我想要|我想租|我想找)\s*)+/,'');
    const relative=subject.match(/^(?:(?:这套|那套|该|所租的|我租的)\s*)?(?:房子|房屋|房源|住房|住宅|房间|单间|公寓)(.*?)(?:附近|周边|靠近|距离|离|距|内有|有)\s*(?:有|要有)?\s*(.+)$/i);
    const nearby=clause.match(/(.+?)(?:附近|周边|\s+nearby\b)(.*)/i),near=clause.match(/(?:靠近|接近|\bnear\s+|\baround\s+)(.+)/i);
    const implicit=subject.match(/^(?:附近|周边)\s*(?:有|要有)\s*(.+)$/i);
    if(relative){name=relative[2];tail=relative[1];}
    else if(implicit){name=implicit[1];}
    else if(nearby){name=nearby[1];tail=nearby[2];}
    else if(near){name=near[1];}
    else if(!/面积|平方米|平方英尺|sqft|sqm/i.test(clause)){const specific=clause.match(/(?:到|离|距|to\s+)?\s*(.+?)\s*(\d+(?:\.\d+)?|[一二两三四五六七八九十半])\s*(公里|千米|km|米|m\b)/i);if(specific){name=specific[1];tail=specific[2]+specific[3];bareDistance=true;}}
    if(!name)continue;
    name=name.replace(/^(?:(?:我(?:想|要|希望)?(?:找|租|住在)?|帮我找|想找|找|租|住在|最好|希望|优先|要求|必须|就是|位置(?:改为|是|为)?|到|离|距)\s*)+/,'').trim();
    if(!name)continue;
    if(bareDistance&&CATEGORY_NAMES.some(([pattern])=>pattern.test(name.replace(/(?:(?:直线|步行|开车)(?:距离)?|距离)$/,'').trim())))continue;
    const combined=name+' '+tail;
    const distance=combined.match(/(\d+(?:\.\d+)?|[一二两三四五六七八九十半])\s*(公里|千米|km|米|metres?|meters?|m\b)/i);
    if(distance)name=name.slice(0,distance.index).trim();
    if(!name||isListingReference(name))continue;
    const radius=distance?(digits[distance[1]]??Number(distance[1]))*(/公里|千米|km/i.test(distance[2])?1000:1):undefined;
    const distance_type=/步行|walking/i.test(clause)?'walking':/开车|driving/i.test(clause)?'driving':'straight_line';
    intents.push(locationIntentSchema.parse({name,quote:clause,...(radius?{radius_m:radius}:{}),strength:!radius||/最好|希望|优先|偏好|prefer|ideally/i.test(clause)?'soft':'hard',distance_type,explicit_station:/MRT|LRT|地铁站|地铁/i.test(name),...(isGenericMrt(name)?{category:'MRTStation'}:{})}));
  }
  return intents;
}
