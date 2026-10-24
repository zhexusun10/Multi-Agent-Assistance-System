import {RequirementRefinementUI} from './requirement-refinement.js?v=20261024-budget';
import {ListingDetailsUI} from './listing-details.js';
import {FollowupUI} from './followup.js';
import {renderMatches,rentElement} from './property-presentation.js?v=20261024-official-geo';
import {ListingNavigator} from './listing-navigator.js';
import {BehaviorOutbox} from './behavior-outbox.js?v=20261023-behavior2';
import {ViewTracker} from './view-tracker.js?v=20261023-behavior2';
import {PropertyDetailView} from './property-detail-view.js?v=20261022-soft-preferences';
import {SessionSync,readSse as sse} from './session-sync.js?v=20261024-presentation';
import {SessionHistoryUI} from './session-history.js?v=20261023-behavior';
import {displayedCandidates} from './recommendation-display.js?v=20261024-top5';
const $=id=>document.getElementById(id);
const el=(tag,className,text)=>{const e=document.createElement(tag);if(className)e.className=className;if(text!==undefined)e.textContent=text;return e;};
let owner=localStorage.getItem('rental.owner');if(!owner){owner=crypto.randomUUID()+crypto.randomUUID();localStorage.setItem('rental.owner',owner);}
let session=localStorage.getItem('rental.session'),state=null,config=null,cursor=0,streamController=null,queryController=null,busy=false,refreshTimer=null,activeAction=null,pendingActionSelection=null;
let sessions=JSON.parse(localStorage.getItem('rental.sessions')||'[]');
let recommendationRenderKey=null,messageRenderSession=null,renderedMessages=[];
const labels={price:'月租',bedrooms:'卧室',bathrooms:'卫浴',floor_area_sqft:'面积',land_area_sqft:'土地面积',build_year:'建成年份',district:'邮区',region_code:'区域',postal_code:'邮编',property_type:'房型',place_proximity:'地点距离',furnishing:'家具',rental_scope:'租赁范围',lease_months:'租期',commute_time:'通勤',current_availability:'当前可租'};
const statusLabels={DRAFT:'草稿待补充',PENDING_REVIEW:'等待审核',APPROVED:'当前修订已批准',SIMULATED:'已模拟执行',CONFIRMED:'已记录双方同意（模拟）',CANCELLED:'已取消',UNKNOWN:'执行结果待核对'};
const headers=()=>({'Authorization':'Bearer '+owner,'Content-Type':'application/json'});
function toast(text){$('toast').textContent=text;$('toast').classList.remove('hidden');setTimeout(()=>$('toast').classList.add('hidden'),4500);}
async function api(path,body,options={}){const r=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',cache:'no-store',headers:headers(),body:body===undefined?undefined:JSON.stringify(body),...options});if(!r.ok){let m;try{m=await r.json();}catch{m={detail:'服务暂不可用'};}throw Error(typeof m.detail==='string'?m.detail:JSON.stringify(m.detail||m));}return r.json();}
function safeUrl(value){try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:null;}catch{return null;}}
function remember(title){if(!sessions.some(s=>s.id===session))sessions.unshift({id:session,title:title||'新的租房对话'});else if(title){const s=sessions.find(s=>s.id===session);if(s.title==='新的租房对话')s.title=title;}sessions=sessions.slice(0,30);localStorage.setItem('rental.sessions',JSON.stringify(sessions));renderSessions();}
const sessionHistory=new SessionHistoryUI({root:$('session-list'),el,onSelect:id=>switchSession(id),onDelete:deleteSession});
function renderSessions(){sessionHistory.render(sessions,session);}
async function deleteSession(id){try{
  const current=id===session;
  if(current){propertyDetail.close();queryController?.abort();await Promise.race([tracker.flush(),new Promise(resolve=>setTimeout(resolve,1800))]);}
  await api(`sessions/${id}`,undefined,{method:'DELETE'});
  await behaviorOutbox.removeSession(id);sessions=sessions.filter(s=>s.id!==id);localStorage.setItem('rental.sessions',JSON.stringify(sessions));
  if(current){streamController?.abort();busy=false;$('send').disabled=false;localStorage.removeItem('rental.session');session=null;state=null;cursor=0;messageRenderSession=null;renderedMessages=[];recommendationRenderKey=null;
    if(sessions.length){try{await switchSession(sessions[0].id);}catch{await newSession();}}else await newSession();
  }else renderSessions();
  toast('对话已删除');
}catch(e){toast(e.message);}}
async function switchSession(id){propertyDetail.close();await Promise.race([tracker.flush(),new Promise(resolve=>setTimeout(resolve,1800))]);pendingActionSelection=null;streamController?.abort();session=id;localStorage.setItem('rental.session',id);await refresh();cursor=state.last_event_id;remember();listen();}
async function newSession(){try{propertyDetail.close();pendingActionSelection=null;streamController?.abort();state=await api('sessions',{});session=state.session_id;cursor=0;localStorage.setItem('rental.session',session);remember();render();listen();$('query').focus();}catch(e){toast(e.message);}}
function message(role,text,knowledge,refinementId){const row=el('div','message '+role);row.append(el('span','message-avatar',role==='user'?'你':'⌂'));const content=el('div','message-content');content.append(el('div','message-name',role==='user'?'你':'栖居 · 协作助手'),el('div','message-text',text));if(knowledge?.citations?.length){const details=el('details','knowledge-sources');details.append(el('summary','',`查看资料摘录与来源（${knowledge.citations.length}）`));for(const [i,c] of knowledge.citations.entries()){const section=el('section','knowledge-source');section.append(el('strong','',`[${i+1}] ${c.title} · ${c.heading}`),el('blockquote','',c.quote),el('small','muted',`资料整理复核：${c.checked_at} · ${c.scope==='system_help'?'项目使用说明':'官方资料整理与项目建议，请核对适用范围'}`));for(const source of c.sources){if(source.url.startsWith('project://'))section.append(el('div','muted',source.title));else if(safeUrl(source.url)){const a=el('a','',source.title);a.href=source.url;a.target='_blank';a.rel='noopener noreferrer';section.append(a);}}details.append(section);}content.append(details);}if(refinementId)refinementUI.mount(refinementId,content);row.append(content);return row;}
function renderMessages(){
  const box=$('messages'),keys=state.messages.map(m=>JSON.stringify(m));
  const sameSession=messageRenderSession===session;
  if(sameSession&&keys.length===renderedMessages.length&&keys.every((key,i)=>key===renderedMessages[i]))return;
  const follow=!sameSession||box.scrollHeight-box.scrollTop-box.clientHeight<100||busy,oldTop=box.scrollTop;
  const appendOnly=sameSession&&renderedMessages.length>0&&keys.length>=renderedMessages.length&&renderedMessages.every((key,i)=>keys[i]===key);
  if(!appendOnly)box.replaceChildren();
  if(!keys.length)box.append(message('assistant','你好，我会先整理你的租房条件，再核查房源、解释结果，并协助准备跟进草稿。\n\n可以从预算、卧室数量或邮区开始。说“预算改为4500”时，其他已确认的条件会保留。'));
  else for(const m of state.messages.slice(appendOnly?renderedMessages.length:0))box.append(message(m.role,m.text,m.knowledge,m.refinement_id));
  messageRenderSession=session;renderedMessages=keys;
  // A complete answer arrives at once after validation. Smooth scrolling plus
  // replacing the old DOM could leave it below the viewport until another send.
  if(follow){
    const last=box.lastElementChild;
    const longAnswer=state.messages.at(-1)?.role==='assistant'&&last&&last.offsetHeight>box.clientHeight;
    const top=longAnswer?box.scrollTop+last.getBoundingClientRect().top-box.getBoundingClientRect().top:box.scrollHeight;
    box.scrollTo({top,behavior:'instant'});
  }
  else if(!appendOnly)box.scrollTo({top:oldTop,behavior:'instant'});
}
const amenityLabels={private_bathroom:'独立卫浴（专用，不与他人共用）',master_room:'主卧房型',common_room:'普通房房型',swimming_pool:'游泳池',gym:'健身房',balcony:'阳台',parking:'停车位',elevator:'电梯',wifi:'网络',washer:'洗衣机',refrigerator:'冰箱',security:'保安',pet_policy:'允许养宠物',cooking_allowed:'允许做饭',air_conditioning:'空调',no_live_in_landlord:'不与房东同住'};
const valueLabels={fully_furnished:'家具齐全',partially_furnished:'部分家具',unfurnished:'无家具',whole_unit:'整租',room:'单间'};
function formatCriterion(c){if(c.field==='amenities')return `${c.strength==='hard'?'必须':'偏好'} · ${amenityLabels[c.value]||c.value}`;if(c.field==='place_proximity'&&c.strength==='soft'&&c.distance_preference!=='bounded')return `偏好 · 靠近 ${c.target?.name||c.target?.category||c.target?.place_id}（${/^URA:/.test(c.target?.place_id||'')?'区域内优先，区外更近优先':'更近优先'}）`;const op={eq:'＝',lte:'≤',gte:'≥',between:'范围',in:'包含'}[c.operator];const v=c.field==='amenities'&&c.value==='private_bathroom'?'独立卫浴':Array.isArray(c.value)?c.value.join('–'):valueLabels[c.value]||c.value;return `${c.strength==='hard'?'必须':'偏好'} · ${labels[c.field]||c.field} ${op} ${v}${c.unit?' '+c.unit:''}${c.target?' · '+(c.target.name||c.target.category||c.target.place_id):''}${c.distance_type?' · '+(c.distance_type==='straight_line'?'直线':c.distance_type==='walking'?'步行（待核实）':c.distance_type):''}${c.transport_mode?' · '+({transit:'公共交通',driving:'开车',walking:'步行',unspecified:'交通方式未指定'}[c.transport_mode]||c.transport_mode):''}`;}
function renderCriteria(){$('version').textContent=state.requirements_version?'需求版本 '+state.requirements_version:'尚未设置';$('criteria').replaceChildren();for(const c of state.requirements.criteria)$('criteria').append(el('span','criterion '+(c.strength==='soft'?'soft':''),formatCriterion(c)));if(!state.requirements.criteria.length)$('criteria').append(el('span','empty-criteria','你提出的条件会整理在这里。'));
  if(state.pending?.clarifications?.length)$('criteria').append(el('span','criterion soft','有 '+state.pending.clarifications.length+' 个问题待澄清；尚未写入正式需求。'));
}
function empty(container,icon,text){container.append(el('div','empty-state'));const box=container.lastChild;box.append(el('span','empty-icon',icon),el('div','',text));}
function button(text,handler){const b=el('button','',text);b.type='button';b.onclick=handler;return b;}
function showDetail(title,content){$('details-dialog').dataset.readingListingId=title.endsWith('已收录照片')?(propertyDetail.current?.id||''):'';$('details-title').textContent=title;$('details-body').replaceChildren(typeof content==='string'?el('div','',content):content);$('details-dialog').showModal();tracker.tick();}

function activateTab(name){propertyDetail.close();document.querySelectorAll('[data-tab]').forEach(x=>{x.classList.toggle('active',x.dataset.tab===name);x.setAttribute('aria-selected',String(x.dataset.tab===name));});for(const id of ['recommendations','actions','calendar','documents'])$(id).classList.toggle('hidden',id!==name);$('recommendation-browser').classList.toggle('hidden',name!=='recommendations');}
function requestFollowup(c,viewing){if(busy)return;pendingActionSelection={listingId:c.listing.listing_id,seen:new Set(state.actions.map(a=>a.id))};sendQuery(`请为房源ID ${c.listing.listing_id} 生成${viewing?'看房询问':'联系核实'}草稿，逐项询问尚未核实的条件。`);}

function listingCard(c,verified,displayRank=c.rank){
  const card=el('article','listing-card');card.dataset.listingId=c.listing.listing_id;card.dataset.sourceRequestId=state.latest_request_id;card.tabIndex=-1;
  const main=el('div','listing-main'),top=el('div','listing-top');top.append(el('span','rank',String(displayRank)),el('h3','listing-title',c.listing.title));main.append(top,rentElement(el,c.listing.price,'listing-price'));card.append(main);
  card.append(renderMatches({el,candidate:c,criteria:state.requirements.criteria,formatCriterion}));
  const actions=el('div','listing-actions'),expand=button('展开详情 ↗',()=>propertyDetail.open(c,verified,expand));expand.classList.add('expand-property');actions.append(expand,button('联系经纪人',()=>requestFollowup(c,true)),button('收藏',async()=>{try{await api('feedback',{session_id:session,source_request_id:state.latest_request_id,event:{event_id:crypto.randomUUID(),listing_id:c.listing.listing_id,occurred_at:new Date().toISOString(),event_type:'save',payload:{}}});toast('已收藏');}catch(e){toast(e.message);}}));
  const url=safeUrl(c.listing.source_url);if(url){const a=el('a','listing-source-button','原始挂牌 ↗');a.href=url;a.target='_blank';a.rel='noopener noreferrer';actions.append(a);}card.append(actions);return card;
}
function renderRecommendations(){
  // Recommendation snapshots are immutable. Unrelated SSE/message updates must
  // not replace a card between pointer-down and click or discard its open details.
  const key=JSON.stringify([session,state.latest_request_id,state.requirements_version,state.active_operation_id,state.recommendation_operation_id,state.recommendation?.request_id,state.presentation_version,config?.dataset]);
  if(key===recommendationRenderKey)return;recommendationRenderKey=key;
  const box=$('recommendations');box.replaceChildren();listingNavigator.setCards([]);const rec=state.recommendation,visible=displayedCandidates(rec);$('result-count').textContent=visible.length;if(!rec){empty(box,'⌂','推荐结果将在这里出现。先告诉我你的预算和租房偏好。');return;}
  if(rec.state.coverage.dataset_version!==config?.dataset){$('result-count').textContent='0';empty(box,'↻','房源数据已更新为完整快照，旧列表保留在会话历史中。请按当前需求重新搜索。');box.append(button('按当前需求重新搜索',()=>sendQuery('按当前需求重新搜索')));return;}
  if(rec.request_id!==state.latest_request_id||rec.requirements_version!==state.requirements_version||state.recommendation_operation_id!==state.active_operation_id){$('result-count').textContent='0';empty(box,'↻','正在按最新需求更新推荐；上一版结果已失效，暂不可据此跟进。');return;}
  for(const [index,c] of visible.entries())box.append(listingCard(c,rec.recommendations.includes(c),index+1));
  if(!visible.length)box.append(el('p','muted','没有找到匹配的房源，可以调整找房条件后再试。'));
  listingNavigator.setCards([...box.querySelectorAll('.listing-card')]);
  observeExposures();
  for(const suggestion of rec.relaxation_suggestions){const c=state.requirements.criteria.find(c=>c.criterion_id===suggestion.criterion_id);if(c?.field==='price')box.append(button(`选择将预算改为 ${suggestion.proposed_value} SGD/月后重搜`,()=>sendQuery(`预算改为${suggestion.proposed_value}新币`)));}
}
const localTime=value=>value?new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Singapore',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value)).replace(' ','T'):'';
const isoTime=value=>value?new Date(value+':00+08:00').toISOString():null;
function editAction(a){followUI.edit(a);}
async function changeAction(a,action,input={}){try{await api(`actions/${a.id}/${action}`,{session_id:session,revision:a.revision,...input});await refresh();toast(action==='execute'?(config.action_mode==='live'?'已加入发送队列，请查看平台结果。':'已保存本地模拟记录，没有真实发送或预订。'):action==='approve'?'已批准当前修订。':'动作已更新。');}catch(e){toast(e.message);}}
function renderActions(){followUI.renderActions();}
async function download(id,format){try{const r=await fetch(`/api/artifacts/${id}?session_id=${encodeURIComponent(session)}&format=${format}`,{headers:headers()});if(!r.ok)throw Error('文档下载失败');const blob=await r.blob(),url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=`rental-summary-${id}.${format==='html'?'html':'md'}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),3000);}catch(e){toast(e.message);}}
function renderDocuments(){const box=$('documents');box.replaceChildren();box.append(button('生成当前推荐总结',()=>sendQuery('生成当前推荐的总结文档')));followUI.dailyControls(box);if(!state.artifacts.length){empty(box,'▤','总结包含完整需求、推荐对比、未知事项、下一步与可追溯来源。');return;}for(const a of [...state.artifacts].reverse()){const card=el('article','doc-card');card.append(el('h3','',a.title||'租房需求与推荐总结'),el('p','muted',`需求版本 ${a.requirements_version} · ${new Date(a.created_at).toLocaleString('zh-CN')}\n${a.source_request_id===state.latest_request_id?'基于当前推荐':'基于历史推荐，保留原始版本'}`),button('下载 Markdown',()=>download(a.id,'md')),document.createTextNode(' '),button('下载 HTML',()=>download(a.id,'html')));box.append(card);}}
function render(){if(!state)return;propertyDetail.sync();renderMessages();refinementUI.sync();renderCriteria();renderRecommendations();renderActions();renderDocuments();if(document.querySelector('[data-tab=calendar]').classList.contains('active'))followUI.renderCalendar();if(pendingActionSelection&&state.actions.some(a=>!pendingActionSelection.seen.has(a.id)&&a.listing_ids.includes(pendingActionSelection.listingId))){pendingActionSelection=null;activateTab('actions');}}
const stateSync=new SessionSync({getSession:()=>session,getState:()=>state,fetchState:id=>api(`state?session_id=${encodeURIComponent(id)}`,undefined,{signal:AbortSignal.timeout(10000)}),applyState:next=>{state=next;render();if(!busy&&!state.pending_operations?.length)$('agent-stage').textContent=state.pending?.clarifications?.length?'等待你补充':'本轮已更新';}});
async function refresh(){await stateSync.refresh();}
function scheduleRefresh(){clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>refresh().catch(e=>toast(e.message)),120);}
async function listen(){
  streamController?.abort();const controller=new AbortController();streamController=controller;const current=session;
  let lastActivity=Date.now(),stalled=false;
  const watchdog=setInterval(()=>{if(Date.now()-lastActivity>45000){stalled=true;controller.abort();}},5000);
  try{
    const response=await fetch(`/api/events?session_id=${encodeURIComponent(current)}&after=${cursor}`,{headers:headers(),cache:'no-store',signal:controller.signal});
    if(current!==session||controller.signal.aborted)return;
    $('connection').textContent='会话已连接';$('connection').classList.add('online');
    // Reconcile on every connection, even when a completion happened offline.
    scheduleRefresh();
    for await(const {id,event} of sse(response,{onActivity:()=>{lastActivity=Date.now();}})){
      if(current!==session||controller.signal.aborted)break;
      if(id!==null){if(id<=cursor)continue;cursor=id;}
      if(event.type==='stage')$('agent-stage').textContent=({a:'需求整理中',b:'候选核查中',c:'解释生成中',d:'跟进准备中',orchestrator:'意图识别中'})[event.payload.agent]||'处理中';
      if(['workflow_result','clarification','action_update','workflow_error'].includes(event.type)){$('agent-stage').textContent=event.type==='clarification'?'等待你补充':'本轮已更新';scheduleRefresh();}
      if(event.type==='workflow_error'){pendingActionSelection=null;toast(event.payload.message);}
    }
  }catch(e){if(current!==session||(e.name==='AbortError'&&!stalled))return;$('connection').textContent='连接中断，正在恢复';$('connection').classList.remove('online');}
  finally{clearInterval(watchdog);}
  if((!controller.signal.aborted||stalled)&&current===session)setTimeout(()=>{if(current===session&&streamController===controller)void listen();},1500);
}
async function sendQuery(text){if(busy)return;if(!text.trim())return;const controller=new AbortController();queryController=controller;busy=true;$('send').disabled=true;$('agent-stage').textContent='正在处理';await tracker.beforeQuery();remember(text.slice(0,24));const requestId=crypto.randomUUID();try{const response=await fetch('/api/query',{method:'POST',headers:headers(),signal:controller.signal,body:JSON.stringify({query:text,session_id:session,request_id:requestId,...(state.latest_request_id?{source_request_id:state.latest_request_id}:{})})});$('query').value='';for await(const {event} of sse(response)){if(event.type==='error')throw Error(event.detail);}await refresh();}catch(e){if(e.name!=='AbortError')toast(e.message);}finally{if(queryController===controller){queryController=null;busy=false;$('send').disabled=false;$('query').focus();}}}
// A healthy-looking stream can lose a completion notification (sleep, proxy,
// reconnect). Pending work therefore reconciles independently of another query.
let lastReconcile=0;
setInterval(()=>{if(document.hidden||!state)return;const interval=busy||state.pending_operations?.length?2000:15000;if(Date.now()-lastReconcile>=interval){lastReconcile=Date.now();void refresh().catch(()=>{});}},1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void refresh().catch(()=>{});});
window.addEventListener('pageshow',()=>{if(state)void refresh().catch(()=>{});});
window.addEventListener('online',()=>{void refresh().catch(()=>{});void listen();});
const listingDetails=new ListingDetailsUI({api,getSession:()=>session,getRequest:()=>state?.latest_request_id,el,button,showDetail,toast});
async function behaviorSend(body,keepalive=false){const r=await fetch('/api/behavior/views',{method:'POST',headers:headers(),body:JSON.stringify(body),keepalive,signal:AbortSignal.timeout(8000)});if(!r.ok){const e=new Error('浏览采集暂不可用');e.status=r.status;throw e;}return r.json();}
const behaviorOutbox=new BehaviorOutbox({send:behaviorSend,scope:owner});
const tracker=new ViewTracker({send:behaviorSend,outbox:behaviorOutbox,onStatus:text=>{$('property-detail').dataset.readingState=text.startsWith('正在记录')?'active':'paused';}});
behaviorOutbox.onResult=(packet,result)=>{if((result.restart_required||(packet.kind==='heartbeat'&&result.completed))&&tracker.segment?.id===packet.segment_id)tracker.finish('lease');};
const listingNavigator=new ListingNavigator($('recommendations'),$('listing-navigator'));
const propertyDetail=new PropertyDetailView({details:listingDetails,tracker,getState:()=>state,getSession:()=>session,el,button,formatCriterion,followup:(c,v)=>requestFollowup(c,v)});
let exposureObserver=null;
function observeExposures(){
  exposureObserver?.disconnect();const snapshot={session_id:session,source_request_id:state.latest_request_id,requirements_version:state.requirements_version,tab_id:tracker.tabId};
  if(!snapshot.source_request_id)return;const emitted=new Set(),timers=new Map();
  exposureObserver=new IntersectionObserver(entries=>{for(const entry of entries){const card=entry.target.closest('[data-listing-id]');if(timers.has(card)){clearTimeout(timers.get(card));timers.delete(card);}if(!entry.isIntersecting||entry.intersectionRatio<.5||emitted.has(card))continue;
    timers.set(card,setTimeout(()=>{timers.delete(card);if(emitted.has(card)||!card.isConnected||document.hidden||propertyDetail.current||snapshot.source_request_id!==state.latest_request_id)return;const a=entry.target.getBoundingClientRect(),b=$('recommendations').getBoundingClientRect();if(Math.max(0,Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top))<Math.min(a.height,b.height)*.5)return;emitted.add(card);void behaviorOutbox.enqueue({...snapshot,event_id:crypto.randomUUID(),kind:'exposure',listing_id:card.dataset.listingId,position:[...$('recommendations').querySelectorAll('.listing-card')].indexOf(card)+1});},1000));
  }}, {root:$('recommendations'),threshold:.5});
  for(const node of $('recommendations').querySelectorAll('.listing-main'))exposureObserver.observe(node);
}
const refinementUI=new RequirementRefinementUI({api,el,scope:owner,getState:()=>state,beforeSubmit:()=>tracker.beforeQuery(),onSubmitted:async result=>{if(result.status==='queued')$('agent-stage').textContent='正在按补充需求推荐';await refresh();}});
const followUI=new FollowupUI({api,getState:()=>state,getSession:()=>session,getConfig:()=>config,refresh,toast,el,button,showDetail,changeAction,activateTab,sendQuery});
$('chat-form').onsubmit=e=>{e.preventDefault();sendQuery($('query').value);};$('query').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();if(!e.isComposing)sendQuery($('query').value);}};
document.querySelectorAll('[data-query]').forEach(b=>b.onclick=()=>sendQuery(b.dataset.query));
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{activateTab(b.dataset.tab);if(b.dataset.tab==='calendar')followUI.renderCalendar();});
document.querySelectorAll('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('new-session').onclick=newSession;
$('help-button').onclick=()=>showDetail('系统能力与限制','需求整理 → 推荐候选核查 → 证据解释 → 跟进草稿。\n\n你可以修改预算，原有卧室与地区条件会保留；取消条件时请明确说明。家具、租期、整租/单间、真实通勤和当前可租状态缺少可核实数据时，会进入待核实候选。\n\n当前使用已安装的历史房源数据，不代表完整市场；可编辑并复制草稿，自行发送后确认，再粘贴经纪人回复，由系统理解并整理下一步；此流程无需平台凭证。平台 API 发送仍需单独配置。看房时间须双方明确同意。知识问答检索经整理的官方租赁资料与系统使用说明，回答可展开来源与摘录；资料不足时会说明。');

$('action-form').onsubmit=e=>followUI.save(e);

try{config=await api('config');if(session){try{await refresh();cursor=state.last_event_id;remember();listen();}catch{await newSession();}}else await newSession();document.body.dataset.ready='true';}catch(e){$('connection').textContent='服务未连接';toast(e.message);}
