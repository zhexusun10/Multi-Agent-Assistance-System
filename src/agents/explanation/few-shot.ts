import type {ExplanationOutput} from './contracts.js';
type Example={id:string;tags:string[];question:string;context:string;output:ExplanationOutput};
const answer=(items:[string,string,string[]][],note='记录的租金和现在是否仍可租，请联系经纪人确认。'):ExplanationOutput=>({version:'2.0',focus_listing_ids:items.map(x=>x[0]),paragraphs:[
  {listing_id:null,text:'以下信息可能帮助您更好地比较这几套房子：',support_refs:['guide:purpose']},
  ...items.map(([listing_id,text,support_refs])=>({listing_id,text,support_refs})),
  {listing_id:null,text:note,support_refs:['availability:current']},
]});
export const EXPLANATION_EXAMPLES:Example[]=[
 {id:'whole-list-distance',tags:['为什么','几套','推荐','理由','怎么选'],question:'这几套怎么选？',context:'本轮1 Orchard Home，月租2200，挂牌面积700sqft，具名超市Fresh Mart直线250米；2 Lake Home月租2400，挂牌面积900sqft，具名Lake MRT挂牌标注600米。用户只提预算2600。面积和配套为未主动要求的补充资料。真实排名不因介绍改变。refs rent:1,area:1,market:1,rent:2,area:2,mrt:2,price-diff,area-diff。',output:answer([
  ['example-a','第1套 Orchard Home：记录月租2,200新币，比Lake Home每月少200新币，如果您更看重每月租金，可以重点比较。附近Fresh Mart超市直线约250米，日常采购多一个选择。',['rent:1','price-diff','market:1']],
  ['example-b','第2套 Lake Home：挂牌面积900平方英尺，比Orchard Home多200平方英尺，可以进一步了解空间安排。挂牌标注Lake MRT约600米，也为选择公共交通提供参考；实际出入口和步行路线还需了解。',['area:2','area-diff','mrt:2']],
 ])},
 {id:'bathroom-count-not-privacy',tags:['1B1B','卫生间','独卫'],question:'介绍一下这两套房',context:'1 Maple Home月租1800，挂牌记1个卫生间但推荐未验证用户的1卫必须条件；2 Cedar Home月租1900，挂牌面积650sqft；没有独立使用权证据。refs rent:1,bath:1,unverified:1,area:2。',output:answer([
  ['example-a','第1套 Maple Home：记录月租1,800新币，可以作为控制月租的选择。挂牌写有1个卫生间，不过这项要求在本轮推荐中还没验证，需要向经纪人进一步确认；数量本身也不能证明是专用卫生间。',['rent:1','bath:1','unverified:1']],
  ['example-b','第2套 Cedar Home：挂牌面积650平方英尺，可以进一步了解格局与使用范围；这不是单独卧室面积的保证。',['area:2']],
 ])},
 {id:'mixed-confirmed-pending',tags:['资料','未知','一组'],question:'这组房有什么特点？',context:'1 Grove月租2000、2卧室，超市Local Mart直线300米，预算与卧室已符合。2 Meadow月租1800，卧室未知，公园River Park直线500米，不在已验证排名。refs rent:1,market:1,rent:2,park:2,beds:unknown。',output:answer([
  ['example-a','第1套 Grove：记录的两间卧室和月租2,000新币符合您的条件；另外，Local Mart超市直线约300米，日常采购多一个选择。',['rent:1','market:1']],
  ['example-b','第2套 Meadow：记录月租1,800新币，附近River Park直线约500米，可作为户外活动地点考虑。不过卧室数量还缺资料，现在不能确认是否符合您的户型要求。',['rent:2','park:2','beds:unknown']],
 ])},
 {id:'stable-order',tags:['没偏好','都符合','区别'],question:'都符合预算，该怎么选？',context:'固定顺序并非价格排序。1 Green租1800面积500sqft，2 Blue租2000面积700sqft，额外字段为面积；不能说综合最佳。refs area:1,area:2,rent:1,rent:2,price-diff,area-diff。',output:answer([
  ['example-a','第1套 Green：记录月租1,800新币，比Blue每月少200新币；挂牌面积500平方英尺。如果您更想控制月租，可以先了解这套的格局。',['rent:1','price-diff','area:1']],
  ['example-b','第2套 Blue：挂牌面积700平方英尺，比Green多200平方英尺，但记录月租也高一些。可以结合实际租住范围，比较是否值得增加这部分预算。',['area:2','area-diff','rent:2']],
 ])},
 {id:'distance-tradeoff-with-unranked',tags:['越近越好','学校','超市','一间'],question:'改成离学校近一点，一间卧室',context:'1 Grove学校直线2500米、超市Local Mart60米；2 Elm学校2300米、超市Fresh Mart140米。两项综合贡献令Grove在前，不改变顺序。预算仅筛选。refs school:1,market:1,school:2,market:2,school-diff。',output:answer([
  ['example-a','第1套 Grove：Local Mart超市直线约60米，采购配套是一个值得考虑的特点；到学校直线约2.5公里，您可以结合实际路线再权衡。',['school:1','market:1']],
  ['example-b','第2套 Elm：到学校直线约2.3公里，比Grove更近一些；Fresh Mart超市直线约140米，也可作为采购选择。如果您更在意学校距离，可以重点比较这套。',['school:2','school-diff','market:2']],
 ])},
 {id:'multiple-preferences',tags:['价格','便宜','面积','贵'],question:'为什么第一套不一定最便宜？',context:'用户明确问排名。1 Garden租1700学校1600米、面积700sqft；2 Maple租2000学校900米、面积800sqft。距离与软租金共同排序，Garden先。refs rent:1,area:1,school:1,rent:2,area:2,school:2,ranking:method。',output:answer([
  ['example-a','第1套 Garden：月租记录1,700新币，挂牌面积700平方英尺；到学校直线约1.6公里。它的月租较低，适合您优先比较预算取舍。',['rent:1','area:1','school:1']],
  ['example-b','第2套 Maple：学校直线约900米，挂牌面积800平方英尺；月租记录为2,000新币。它在学校距离方面更有吸引力，但月租较高。这次原顺序综合了租金偏好和学校距离，并非只按其中一项。',['school:2','area:2','rent:2','ranking:method']],
 ])},
 {id:'multiple-preferences-english',tags:['why','choose','compare','English'],question:'How should I choose between these homes?',context:'1 Maple recorded rent2200,listing area700sqft,Fresh Mart250m straight-line;2 Lake rent2400,listing area900sqft,Lake MRT600m platform reported. Area/transit not requested. refs price-diff,market:1,area:2,mrt:2。',output:{version:'2.0',focus_listing_ids:['example-a','example-b'],paragraphs:[
  {listing_id:null,text:'These details may help you compare the homes:',support_refs:['guide:purpose']},
  {listing_id:'example-a',text:'1. Maple: Its recorded monthly rent is S$200 lower than Lake. Fresh Mart is about 250 metres away in a straight line, offering a nearby option for groceries.',support_refs:['price-diff','market:1']},
  {listing_id:'example-b',text:'2. Lake: The listed floor area is 900 sq ft. The listing reports Lake MRT at about 600 metres, a useful public transport reference; the actual walking route remains unverified.',support_refs:['area:2','mrt:2']},
  {listing_id:null,text:'Please confirm current rent, availability and actual routes with the agent.',support_refs:['availability:current']},
 ]}},
 {id:'empty-results',tags:['没有房','空'],question:'这次没有房源吗？',context:'本轮没有可展示房源，不能推断全市场无房，不能自动改条件。refs results:empty。',output:{version:'2.0',focus_listing_ids:[],paragraphs:[{listing_id:null,text:'这次条件下暂时没有能展示的房源。您愿意先调整预算、范围还是户型要求？',support_refs:['results:empty']}]}},
 {id:'behavior-ranking',tags:['浏览','兴趣'],question:'这些房子各有什么优势？',context:'房型与面积浏览线索参与原排序，但介绍可讲已有特点，不猜用户明确偏好。1 Pine面积800sqft；2 Oak租金2100，超市Corner Mart直线200米。refs area:1,rent:2,market:2。',output:answer([
  ['example-a','第1套 Pine：挂牌面积800平方英尺，可以作为比较空间安排的一个参考，具体租住范围还需了解。',['area:1']],
  ['example-b','第2套 Oak：记录月租2,100新币，Corner Mart超市直线约200米，日常采购多一个选择。',['rent:2','market:2']],
 ])},
];
const genericTags=new Set(['为什么','几套','推荐','理由','why']);
export function selectExplanationExamples(query:string,count=2,contextId?:string){return EXPLANATION_EXAMPLES.map((e,i)=>({e,i,score:(e.id===contextId?100:0)+e.tags.reduce((n,t)=>n+(query.toLowerCase().includes(t.toLowerCase())?(genericTags.has(t)?1:4+Math.min(t.length,12)/12):0),0)})).sort((a,b)=>b.score-a.score||a.i-b.i).slice(0,count).map(x=>x.e);}
