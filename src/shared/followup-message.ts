/** Pure shared presentation: used by both the browser preview and server copy/send. */
export type ViewingSlot={starts_at:string;ends_at:string;time_zone:string};
const zone='Asia/Singapore';
const dateFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:zone,year:'numeric',month:'long',day:'numeric',weekday:'short'});
const clockFormat=new Intl.DateTimeFormat('en-US',{timeZone:zone,hour:'numeric',minute:'2-digit',hour12:true});
const dayFormat=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'});
const date=(d:Date)=>{const p=dateFormat.formatToParts(d),get=(t:string)=>p.find(x=>x.type===t)?.value||'';return `${get('year')}年${get('month')}月${get('day')}日（${get('weekday')}）`;};
const clock=(d:Date)=>clockFormat.format(d).replace(/\s+/g,' ');
export function formatViewingTime(slot:ViewingSlot){
  const start=new Date(slot.starts_at),end=new Date(slot.ends_at);
  if(!Number.isFinite(start.getTime())||!Number.isFinite(end.getTime()))return '';
  return (dayFormat.format(start)===dayFormat.format(end)?`${date(start)} ${clock(start)}–${clock(end)}`:`${date(start)} ${clock(start)} 至 ${date(end)} ${clock(end)}`)+'（新加坡时间）';
}
/** Remove known application commands, never echo router text as the customer's message. */
export function customerNotes(text=''){
  return text
    .replace(/(?:请|麻烦)?(?:帮我)?(?:为|给)?\s*房源\s*ID\s*\d+\s*生成(?:看房询问|联系核实|联系|看房)?草稿/gi,'')
    .replace(/逐项询问尚未核实的条件[。！]?/g,'')
    .replace(/(?:请|麻烦)?(?:帮我)?(?:为|给)\s*第?[一二三四五六七八九十\d]+套(?:房源|房子)?\s*(?:生成|起草)(?:看房询问|联系核实|联系|看房)?草稿/g,'')
    .replace(/此前已经咨询[，,]?\s*现希望跟进回复和未核实事项[。！]?/g,'')
    .replace(/^(?:请|麻烦)?(?:帮我|为我)?\s*(?:生成|起草|写)(?:一[份条封个])?(?:联系经纪人|看房询问|联系核实|看房|咨询|联系)?(?:的)?(?:消息|草稿|信息)[，,。：:\s]*/,'')
    .replace(/^[\s，,。；;：:]+|[\s，,；;：:]+$/g,'').trim();
}
export function notesFromFollowupQuery(query:string){
  const rest=customerNotes(query)
    .replace(/^(?:请|麻烦)?(?:帮我)?(?:联系|咨询|约看|预约看房|我(?:想要|想|要)?(?:看|了解|咨询|联系))\s*(?:房源\s*ID\s*\d+|第?[一二三四五六七八九十\d]+套|这(?:个房子|套房子|套|个)|那(?:个房子|套))\s*[，,。：:\s]*/i,'')
    .replace(/^(?:帮我|请帮我)?(?:问问|问一下|问下|询问一下|顺便问下|顺便问一下)[，,。：:\s]*/,'');
  // Selection/routing alone has no customer-authored negotiation terms.
  return /^(?:我想看房|我要看房|我想看这套|我要看这套|我想看这个房子|我要看这个房子|联系经纪人|帮我联系经纪人)[。！!\s]*$/.test(rest)?'':rest;
}
export const isLegacyGeneratedMessage=(body:string)=>body.includes('快照月租 SGD')&&body.includes('数据来自历史快照，尚未核实当前可租状态。谢谢。');
export function legacyDraftNotes(body:string){const match=body.match(/我的咨询与协商诉求（待双方协商）：([\s\S]*?)\n数据来自历史快照/);return match?notesFromFollowupQuery(match[1]):'';}
export function composeCustomerMessage(body:string,notes='',slots:ViewingSlot|null=null,templated=false){
  if(templated)return body;
  const cleaned=customerNotes(notes),parts=[body.trim()];
  if(cleaned&&!body.includes(cleaned))parts.push('协商诉求：'+cleaned);
  if(slots){const time=formatViewingTime(slots);if(time)parts.push(`我想在 ${time} 去看房，请问你方便吗？`);}
  if(parts.length===1)return body;
  const thanks=/(?:\s*谢谢[。！!]?\s*)$/;
  if(thanks.test(parts[0])){parts[0]=parts[0].replace(thanks,'').trim();parts.push('谢谢！');}
  return parts.filter(Boolean).join('\n\n');
}
