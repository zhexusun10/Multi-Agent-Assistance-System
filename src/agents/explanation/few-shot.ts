import type {ExplanationOutput} from './contracts.js';
type Example={id:string;tags:string[];question:string;context:string;output:ExplanationOutput};
const answer=(items:[string,string,string[]][],note='记录的租金和现在是否仍可租，请联系经纪人确认。'):ExplanationOutput=>({version:'2.0',focus_listing_ids:items.map(x=>x[0]),paragraphs:[
  {listing_id:null,text:'以下信息可能帮助您更好地比较这几套房子：',support_refs:['guide:purpose']},
  ...items.map(([listing_id,text,support_refs])=>({listing_id,text,support_refs})),
  {listing_id:null,text:note,support_refs:['availability:current']},
]});
export const EXPLANATION_EXAMPLES:Example[]=[
 {id:'whole-list-outlier',tags:['最低','最便宜','最高','中等','Top5'],question:'这五套各有什么特点？',context:'教学样例：5套均为正式推荐；月租依次1500/2300/2400/2600/2700，房源面积依次400/600/1000/500/700sqft。第1套明显最低租，第3套面积最大，第5套月租最高；第2与第5面积居中。第1超市Fresh Mart直线360m，第2有2个卫生间，第3附近Lake MRT资料距离600m，第4有2卧室，第5公交站Hill Stop直线200m。相应refs rent:1..5,area:1..5,cohort:rent:1..5,cohort:area:1..5,shop:1,bath:2,mrt:3,beds:4,bus:5。',output:answer([
  ['example-a','第1套 Pine：月租1,500新币，是这五套里最低的，能少占一些预算。房源面积400平方英尺，也是其中较小的一套；附近Fresh Mart超市直线约360米，买东西多一个选择。',['rent:1','area:1','cohort:rent:1','cohort:area:1','shop:1']],
  ['example-b','第2套 Grove：有两个卫生间，家里人作息不同时可以把这个条件一起考虑。房源面积600平方英尺，在这组里居中，月租2,300新币，整体也偏低。',['bath:2','area:2','rent:2','cohort:area:2','cohort:rent:2']],
  ['example-c','第3套 Lake：1,000平方英尺，是这几套里面积最大的，想多一些空间可以重点看看。月租2,400新币，处在中间；附近有Lake MRT，资料里写约600米，选公共交通时可以参考。',['area:3','rent:3','cohort:area:3','cohort:rent:3','mrt:3']],
  ['example-d','第4套 Elm：两间卧室适合列入您的户型比较；房源面积500平方英尺，在这组里偏小，月租2,600新币则偏高，值得想想这个空间是否够用。',['beds:4','area:4','rent:4','cohort:area:4','cohort:rent:4']],
  ['example-e','第5套 Hill：附近Hill Stop公交站直线约200米，出行时又多一个选择。月租2,700新币是这五套里最高的，房源面积700平方英尺、处在中间，可以结合公交位置看是否值得这笔预算。',['bus:5','rent:5','area:5','cohort:rent:5','cohort:area:5']],
 ])},
 {id:'whole-list-distance',tags:['为什么','几套','推荐','理由','怎么选'],question:'这几套怎么选？',context:'本轮1 Orchard Home，月租2200，挂牌面积700sqft，具名超市Fresh Mart直线250米；2 Lake Home月租2400，挂牌面积900sqft，具名Lake MRT挂牌标注600米。用户只提预算2600。面积和配套为未主动要求的补充资料。真实排名不因介绍改变。refs rent:1,area:1,market:1,rent:2,area:2,mrt:2,price-diff,area-diff。',output:answer([
  ['example-a','第1套 Orchard Home：月租2,200新币，是这两套里较低的；房源面积700平方英尺，如果想先控制月租，可以重点看看。附近Fresh Mart超市直线约250米，买东西多一个选择。',['rent:1','area:1','price-diff','market:1']],
  ['example-b','第2套 Lake Home：房源面积900平方英尺，在这两套里空间更大，月租2,400新币。附近有Lake MRT，资料里写约600米，选公共交通时可以参考；实际步行路线还要再看看。',['rent:2','area:2','area-diff','mrt:2']],
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
  ['example-a','第1套 Green：月租1,800新币，在这两套里更省预算；房源面积500平方英尺，空间相对小一些，重点看看格局够不够用。',['rent:1','price-diff','area:1']],
  ['example-b','第2套 Blue：700平方英尺是这两套里较大的，月租2,000新币也更高。如果您更想要空间，可以结合实际租住范围考虑这套。',['area:2','area-diff','rent:2']],
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
  {listing_id:'example-a',text:'1. Maple: At S$2,200 per month, this is the lower-rent option, with a listed area of 700 sq ft. Fresh Mart is about 250 metres away in a straight line, giving you another option for groceries.',support_refs:['rent:1','area:1','price-diff','market:1']},
  {listing_id:'example-b',text:'2. Lake: Its listed area of 900 sq ft offers the larger option here, at S$2,400 per month. Nearby Lake MRT is shown at about 600 metres in the property information, useful when considering public transport; check the actual route.',support_refs:['rent:2','area:2','mrt:2']},
  {listing_id:null,text:'Please confirm current rent, availability and actual routes with the agent.',support_refs:['availability:current']},
 ]}},
 {id:'empty-results',tags:['没有房','空'],question:'这次没有房源吗？',context:'本轮没有可展示房源，不能推断全市场无房，不能自动改条件。refs results:empty。',output:{version:'2.0',focus_listing_ids:[],paragraphs:[{listing_id:null,text:'这次条件下暂时没有能展示的房源。您愿意先调整预算、范围还是户型要求？',support_refs:['results:empty']}]}},
 {id:'behavior-ranking',tags:['浏览','兴趣'],question:'这些房子各有什么优势？',context:'房型与面积浏览线索参与原排序，但介绍可讲已有特点，不猜用户明确偏好。1 Pine面积800sqft；2 Oak租金2100，超市Corner Mart直线200米。refs area:1,rent:2,market:2。',output:answer([
  ['example-a','第1套 Pine：挂牌面积800平方英尺，可以作为比较空间安排的一个参考，具体租住范围还需了解。',['area:1']],
  ['example-b','第2套 Oak：记录月租2,100新币，Corner Mart超市直线约200米，日常采购多一个选择。',['rent:2','market:2']],
 ])},
 {id:'similar-layout-price-choice',tags:['比较两套','差额','每平方英尺','PSF','性价比'],question:'比较这两套，空间上值不值得？',context:'教学样例：A和B均为整租、同一面积口径、1卧室1卫生间，A月租2500面积500sqft，B月租2600面积520sqft。面积接近，明确询问两套取舍时可以给100差额。各自每平方英尺月租均5新币，不能声称某套整体性价比更高。refs rent:a,area:a,rent:b,area:b,pair:rent,unit:a,unit:b。',output:answer([
  ['example-a','第1套 A：月租2,500新币，房源面积500平方英尺。两套格局和面积接近，这套每月少100新币，如果想控制总月租，可以先看看它。',['rent:a','area:a','pair:rent']],
  ['example-b','第2套 B：房源面积520平方英尺，月租2,600新币。空间略多一些，月租也略高，可以再结合位置和实际格局来选。',['rent:b','area:b']],
 ])},
 {id:'unit-rent-meaningful-comparison',tags:['每平方英尺','PSF','单位面积','性价比'],question:'这五套在空间和租金上划算吗？',context:'教学样例：五套均明确整租、房源面积口径相同，单位面积月租依次6/4/4.2/4.3/2.4新币/平方英尺/月。A月租3000面积500，B月租4000面积1000，C月租2100面积500，D月租2580面积600，E月租2400面积1000。A最高且明显偏高，E最低且明显偏低，B/C/D居中，不提供其单位租金亮点。refs rent:a..e,area:a..e,cohort:unit:a,cohort:unit:e。',output:answer([
  ['example-a','第1套 A：月租3,000新币，房源面积500平方英尺。摊到每平方英尺，月租约6新币，是这五套里最高的，同样面积要付更多月租；如果更看重空间，这套不太划算，值得结合其他特点再考虑。',['rent:a','area:a','cohort:unit:a']],
  ['example-b','第2套 B：房源面积1,000平方英尺，月租4,000新币，空间较大，月租也要多留一些预算，可以先看看实际格局是否用得上。',['rent:b','area:b']],
  ['example-c','第3套 C：月租2,100新币，房源面积500平方英尺。总月租更容易控制，如果这个面积够用，可以优先了解。',['rent:c','area:c']],
  ['example-d','第4套 D：600平方英尺的房源面积，月租2,580新币，在空间和总月租之间多了一个选择，接下来可以结合位置来比较。',['rent:d','area:d']],
  ['example-e','第5套 E：1,000平方英尺的房源面积，月租2,400新币，摊下来每平方英尺月租约2.4新币，是这五套里最低的。单看空间和月租更划算，想把预算更多用在面积上，可以重点看看这套。',['rent:e','area:e','cohort:unit:e']],
 ])},
];
const genericTags=new Set(['为什么','几套','推荐','理由','why']);
export function selectExplanationExamples(query:string,count=2,contextId?:string){return EXPLANATION_EXAMPLES.map((e,i)=>({e,i,score:(e.id===contextId?100:0)+e.tags.reduce((n,t)=>n+(query.toLowerCase().includes(t.toLowerCase())?(genericTags.has(t)?1:4+Math.min(t.length,12)/12):0),0)})).sort((a,b)=>b.score-a.score||a.i-b.i).slice(0,count).map(x=>x.e);}
