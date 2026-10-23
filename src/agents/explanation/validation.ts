import {explanationOutputSchema,type ExplanationBundle,type ExplanationOutput} from './contracts.js';
import {EXPLANATION_PARAMETERS} from './prompt.js';
export function validateExplanation(raw:unknown,bundle:ExplanationBundle):ExplanationOutput{
  const output=explanationOutputSchema.parse(raw),text=output.paragraphs.map(p=>p.text).join('\n\n');
  if(!/[\u3400-\u9fff]/.test(bundle.question)&&/[a-z]{2}/i.test(bundle.question)&&(text.match(/[\u3400-\u9fff]/g)||[]).length>8)throw Error('STYLE: The actual question is in English. Rewrite the entire answer in English, even if repair feedback or source facts are Chinese.');
  if(text.length>EXPLANATION_PARAMETERS.max_characters)throw Error('STYLE: response too long');
  if(/[\u3400-\u9fff]/.test(text)&&text.length>EXPLANATION_PARAMETERS.max_chinese_characters)throw Error('STYLE: Chinese explanation is too long');
  if(!/[\u3400-\u9fff]/.test(text)&&(text.match(/\b[A-Za-z]+\b/g)||[]).length>EXPLANATION_PARAMETERS.max_english_words)throw Error('STYLE: English explanation is too long');
  if(/native:|[0-9a-f]{8}-[0-9a-f]{4}-|criterion_id|evidence_id|support_refs|combined_score|Exact criterion|destination unresolved|原生|融合匹配分|显式偏好|隐式|权重|证据覆盖|快照|候选池|已评估\s*\d|\d+\s*套(?:候选|通过)|```|^\s*[#{|]/im.test(text))throw Error('STYLE: internal diagnostics or report formatting in user prose');
  if(/待确认选项|待核实候选|没有可比较的排序依据|(?:未|没有|尚未)评分/.test(text))throw Error('STYLE: 不用生硬的候选/选项/评分标签；直接说这几套房源缺哪些资料，排序不足仅一句说明。');
  if(bundle.facts.some(f=>f.text.includes('直线距离，不能推断步行时间'))){
    for(const sentence of text.split(/[。；\n]/)){
      const claim=/(?:步行|通勤)(?:感受|体验|时间|路程)?[^。；\n]{0,22}(?:相同|一样|差不多|没有明显(?:区别|差别)|更快|更方便|更轻松|基本一致)|\b(?:commut\w*|walk\w*)\b.{0,50}\b(?:similar|same|faster|easier|more convenient|negligible|no meaningful difference)\b/i.exec(sentence);
      if(claim&&!/不能|无法|不代表|尚不清楚|未必|cannot|can't|does not|do not|not infer|unknown/i.test(sentence.slice(0,claim.index)))throw Error('GROUNDING: 只有直线距离，没有实际路线证据；即使用“可能”也不能推断步行/通勤体验相同、更快或更方便。仅说明直线差值，实际路线还需了解。');
    }
  }
  const ids=bundle.listings.map(l=>l.listing_id),positions=output.focus_listing_ids.map(id=>ids.indexOf(id));
  if(new Set(output.focus_listing_ids).size!==positions.length||positions.some((n,i)=>n<0||(i>0&&n<positions[i-1])))throw Error('FOCUS: unknown/reordered listing selection');
  if(ids.some(id=>new RegExp('\\b'+id+'\\b').test(text)))throw Error('STYLE: listing ID in prose');
  const guide=bundle.decision_support;
  if(guide){
    if(JSON.stringify(output.focus_listing_ids)!==JSON.stringify(guide.target_listing_ids))throw Error('FOCUS: cover exactly the target homes in display order');
    const blocks=output.paragraphs.filter(p=>p.listing_id);
    if(JSON.stringify(blocks.map(p=>p.listing_id))!==JSON.stringify(guide.target_listing_ids))throw Error('COVERAGE: one paragraph per target home, each bound to listing_id, in display order');
    for(const p of blocks){
      const profile=guide.profiles.find(l=>l.listing_id===p.listing_id)!;
      const refs=[...profile.known_refs,...profile.extra_refs,...profile.highlight_refs,...profile.caution_refs];
      if(!p.support_refs.some(r=>refs.includes(r)))throw Error('GROUNDING: each home needs its own factual support');
      if(guide.mode==='guide'&&profile.additional_refs.length&&!p.support_refs.some(r=>profile.additional_refs.includes(r)))throw Error('COVERAGE: introduce at least one available additional decision fact for this home, beyond repeating requirements');
      if(profile.caution_refs.some(r=>!p.support_refs.includes(r)))throw Error('COVERAGE: preserve this home\'s important unknown or conflicting facts');
      for(const ref of p.support_refs){
        const f=bundle.facts.find(f=>f.ref===ref);
        if(f?.listing_ids.length&&!f.listing_ids.includes(p.listing_id!))throw Error('GROUNDING: another home\'s facts cannot support this listing paragraph');
      }
    }
    if(/您的要求[：:]|这些房源符合的部分[：:]/.test(text))throw Error('STYLE: use per-home decision guidance, not the old requirement recap template');
    // Straight-line/annotation evidence cannot support route convenience claims.
    if(/交通(?:十分|非常|很)?便利|交通方便|通勤方便|出行(?:十分|非常|很)?方便|步行方便|四通八达|walk(?:ing)?\s+distance|easy\s+commut/i.test(text))throw Error('GROUNDING: use named transport, measured distance and its metric; do not guarantee route convenience');
    if(/卧室面积|bedroom (?:area|size)/i.test(text)&&!/(?:不是|不代表|不能|不保证|not|cannot)/i.test(text))throw Error('GROUNDING: listing floor area is not verified bedroom area');
  }
  const factMap=new Map(bundle.facts.map(f=>[f.ref,f])),used=new Set<string>();
  for(const p of output.paragraphs){
    if(p.support_refs.some(ref=>!factMap.has(ref)))throw Error('GROUNDING: unknown support reference');
    p.support_refs.forEach(ref=>used.add(ref));
    const closure=new Set(p.support_refs),pending=[...closure];
    while(pending.length){const ref=pending.pop()!;for(const parent of factMap.get(ref)?.derived_from||[]){if(!factMap.has(parent))throw Error('GROUNDING: broken derived fact');if(!closure.has(parent)){closure.add(parent);pending.push(parent);}}}
    const supporting=[...closure].map(ref=>factMap.get(ref)!);
    const allowed=supporting.flatMap(f=>f.numbers);
    const literals=supporting.flatMap(f=>f.literals||[]);
    // A cited observation belongs to its grouped user requirement (e.g. the
    // same school's 5km limit + nearer preference), rather than another target.
    for(const requirement of bundle.requirements)if(requirement.results.some(r=>r.fact_refs.some(ref=>closure.has(ref)))){
      const fact=factMap.get(requirement.ref)!;allowed.push(...fact.numbers);literals.push(...fact.literals||[]);
    }
    // Only deterministic arithmetic over the same known measurement is allowed.
    // Unknown observations and unrelated facts cannot enter this closure.
    for(const fact of bundle.facts)if(fact.derived_from?.length&&fact.derived_from.every(ref=>closure.has(ref)))allowed.push(...fact.numbers);
    // Singapore listing names often contain block/street numbers. An abbreviated
    // "941" is a title reference, not a new rent/distance, when this paragraph
    // cites facts for that listing. The semantic reviewer still checks its use.
    for(const l of bundle.listings)if(supporting.some(f=>f.listing_ids.includes(l.listing_id))){
      allowed.push(...[...l.title.matchAll(/\d+(?:\.\d+)?/g)].map(m=>Number(m[0])));
      literals.push(l.title);
    }
    let prose=p.text.replace(/^\s*\d+[.)、]\s*/gm,''); // List indices are layout, not housing facts.
    if(guide&&p.listing_id){
      const position=guide.profiles.find(l=>l.listing_id===p.listing_id)!.position;
      prose=prose.replace(/^\s*第\s*(\d+)\s*套/,(_whole,n)=>{
        if(Number(n)!==position)throw Error('FOCUS: listing heading position disagrees with its ID');return '';
      });
    }
    // Mask only whole, exactly cited string values. Do not whitelist all district
    // codes, borrow another paragraph's values, or make their digits quantities.
    for(const literal of [...new Set(literals)].sort((a,b)=>b.length-a.length)){
      let escaped=literal.replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replace(/\s+/g,'\\s+');
      if(/\d[年月日]/.test(literal))escaped=escaped.replace(/([年月日])/g,'\\s*$1\\s*');
      prose=prose.replace(new RegExp('(?<![A-Za-z0-9])'+escaped+'(?![A-Za-z0-9])','gi'),' ');
    }
    // A wrong/uncited code must fail even if its digits happen to match a price,
    // count, ordinal or another fact. Unit-bearing quantities such as 850m and
    // 5km are deliberately left to the numerical grounding below.
    const uncited=[...prose.matchAll(/\b[A-Za-z]+\d+[A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*\b|\b\d{4}-\d{2}-\d{2}\b/g)].map(m=>m[0]);
    if(uncited.length)throw Error('GROUNDING: identifiers '+uncited.join(',')+' need exact literal evidence in this paragraph refs '+p.support_refs.join(','));
    const numbers=[...prose.replaceAll(',','').matchAll(/\d+(?:\.\d+)?/g)].map(m=>Number(m[0]));
    const missing=numbers.filter(n=>!allowed.some(a=>Math.abs(a-n)<=Math.max(.5,Math.abs(a)*.025)));
    if(missing.length){const hints=missing.map(n=>({number:n,matching_refs:bundle.facts.filter(f=>f.numbers.some(a=>Math.abs(a-n)<=Math.max(.5,Math.abs(a)*.025))).slice(0,8).map(f=>f.ref)}));throw Error('GROUNDING: numbers '+missing.join(',')+' are not supported by this paragraph refs '+p.support_refs.join(',')+'; cite correct facts or remove unnecessary figures. Reference candidates (verify meaning): '+JSON.stringify(hints));}
  }
  if(bundle.required_refs.some(ref=>!used.has(ref)))throw Error('COVERAGE: include the required current facts and availability, plus ranking only when requested');
  return output;
}
