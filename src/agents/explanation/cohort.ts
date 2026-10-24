export type CohortObservation={listing_id:string;value:number;ref:string};
export type CohortPosition='lowest'|'highest'|'tied_lowest'|'tied_highest'|'lower'|'middle'|'higher'|'similar';
export type CohortSummary={
  position:CohortPosition;value:number;minimum:number;maximum:number;median:number;
  scope_count:number;known_count:number;peer_count:number;complete:boolean;clustered:boolean;stands_out:boolean;
};

// Editorial salience only: never contributes to recommendation scores.
export const UNIT_RENT_COMPARISON_MIN_DEVIATION=.15;
export function unitRentAssessment(summary:CohortSummary):'higher'|'lower'|null{
  if(summary.clustered||Math.abs(summary.value-summary.median)/summary.median<UNIT_RENT_COMPARISON_MIN_DEVIATION)return null;
  if(['highest','tied_highest','higher'].includes(summary.position))return 'higher';
  if(['lowest','tied_lowest','lower'].includes(summary.position))return 'lower';
  return null;
}

/** Editorial context only. Does not rank homes or award recommendation scores.
 * Missing values are excluded from arithmetic but retained in completeness. */
export function describeCohort(subject:string,scope:string[],observations:CohortObservation[],options:{allowZero?:boolean}={}):CohortSummary|null{
  const known=observations.filter(x=>scope.includes(x.listing_id)&&Number.isFinite(x.value)&&(x.value>0||options.allowZero&&x.value===0));
  if(new Set(known.map(x=>x.listing_id)).size!==known.length)throw Error('COHORT_DUPLICATE_ID');
  const current=known.find(x=>x.listing_id===subject);
  if(!current||known.length<2)return null;
  const sorted=known.map(x=>x.value).sort((a,b)=>a-b),minimum=sorted[0],maximum=sorted.at(-1)!;
  const median=sorted.length%2?sorted[Math.floor(sorted.length/2)]:(sorted[sorted.length/2-1]+sorted[sorted.length/2])/2;
  const below=known.filter(x=>x.value<current.value).length,above=known.filter(x=>x.value>current.value).length,ties=known.length-below-above;
  const clustered=minimum===maximum||median>0&&(maximum-minimum)/median<=.05;
  const percentile=(below+(ties-1)/2)/(known.length-1);
  const position:CohortPosition=minimum===maximum?'similar':below===0?(ties>1?'tied_lowest':'lowest'):above===0?(ties>1?'tied_highest':'highest'):percentile<=.25?'lower':percentile>=.75?'higher':'middle';
  const adjacent=below===0?sorted[ties]:above===0?sorted[sorted.length-ties-1]:null;
  const stands_out=adjacent!==null&&adjacent!==undefined&&Math.abs(current.value-median)/(median||1)>=.2&&Math.abs(current.value-adjacent)/(adjacent||1)>=.1;
  return {position,value:current.value,minimum,maximum,median,scope_count:scope.length,known_count:known.length,peer_count:known.length-1,complete:known.length===scope.length,clustered,stands_out};
}
