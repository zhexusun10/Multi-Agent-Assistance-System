import type {Candidate,RecommendationResponse} from '../contracts/recommendation-v1.js';
import {BusinessError} from '../stores/domain.js';

import {displayedCandidates} from '../shared/recommendation-display.js';
export {displayedCandidates,RECOMMENDATION_DISPLAY_LIMIT} from '../shared/recommendation-display.js';
export function resolveListingSelection(rec:RecommendationResponse|null|undefined,query:string):Candidate{
 const explicit=query.match(/房源\s*(?:ID|编号)\s*[:：#]?\s*(\d+)|(?:listing|property)\s*(?:ID|#)\s*[:：]?\s*(\d+)|房源\s*(\d{5,})/i);
 const candidates=displayedCandidates(rec);
 if(explicit){const id=explicit[1]||explicit[2]||explicit[3],item=candidates.find(c=>c.listing.listing_id===id);if(!item)throw new BusinessError('SELECTION_NOT_IN_RESULT','这套房源不在当前列表中，请重新选择。');return item;}
 const ordinal=query.match(/第\s*([一二三四五六七八九十\d]+)\s*套|(?:listing|property)\s*(\d{1,2})\b/i),map:Record<string,number>={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10};
 const pool=/待核实(?:候选|列表|房源)?/.test(query)?candidates.filter(c=>rec?.unverified_candidates.includes(c)):/已核验|已验证/.test(query)?candidates.filter(c=>rec?.recommendations.includes(c)):candidates;
 const n=ordinal?(map[ordinal[1]]??Number(ordinal[1]||ordinal[2])):pool.length===1?1:0;
 const item=Number.isInteger(n)&&n>0?pool[n-1]:null;
 if(!item)throw new BusinessError('SELECTION_REQUIRED','请点击房源卡片的“联系经纪人”，或说“我要看第1套”，也可以提供房源 ID。');return item;
}
