import type {ExplanationOutput} from './contracts.js';
type Example={id:string;tags:string[];question:string;context:string;output:ExplanationOutput};
const answer=(paragraphs:[string,string[]][],ids:string[]=[]):ExplanationOutput=>({version:'2.0',focus_listing_ids:ids,paragraphs:paragraphs.map(([text,support_refs])=>({text,support_refs}))});
export const EXPLANATION_EXAMPLES:Example[]=[
  {id:'bathroom-count-not-privacy',tags:['1B1B','一室一卫','卫生间数量'],question:'为什么推荐这几套房子？',context:'用户要求：月租2500以内、1间卧室、1个卫生间、裕廊东5公里内；希望MRT或公交1公里内。记录月租1600至1900、1间卧室符合，卫生间数量、裕廊东距离和站点距离未知，列表未评分。没有专用卫浴要求。refs：requirements,layout:known,bath-count:unknown,location:unknown,transit:unknown,ranking:method,availability:current。',output:answer([
    ['您的要求：\n• 月租不超过2,500新币。\n• 1间卧室、1个卫生间。\n• 裕廊东5公里内。\n• 最好在1公里内有MRT或公交站。',['requirements']],
    ['这些房源符合的部分：\n• 预算：记录月租在1,600至1,900新币之间，低于您的上限。\n• 卧室：记录均为1间，符合您对卧室数量的要求。',['layout:known']],
    ['还需要了解的部分：\n• 卫生间数量：还没有足够资料确认是否为1个。\n• 到裕廊东的距离：暂时不能确认是否在5公里内。\n• 附近交通：暂时不能确认1公里内是否有MRT或公交站。',['bath-count:unknown','location:unknown','transit:unknown']],
    ['为什么这样排序：目前资料不足以判断哪套更合适，前后顺序仅供浏览。',['ranking:method']],
    ['注意：还不能确认这几套房源全部符合您的要求；当前租金和是否仍可租，也需要联系经纪人了解。',['bath-count:unknown','location:unknown','availability:current']]])},
  {id:'mixed-confirmed-pending',tags:['这几套','整体','一组'],question:'这组推荐是怎么考虑的？',context:'同一列表包含资料能确认的匹配项和缺少学校距离的待确认项。有资料的房源符合预算和学校范围，排序唯一实际依据是靠近学校。待确认的没有评分、原顺序展示。所有MRT或公交偏好未知。refs：selection,ranking:method,transit:unknown,availability:current。',output:answer([
    ['您的要求：\n• 月租在预算内。\n• 在学校附近，并且希望越近越好。\n• 最好靠近MRT或公交站。',['selection','transit:unknown']],
    ['这些房源符合的部分：\n• 预算：已有租金记录都在预算内。\n• 学校距离：有距离资料的房源符合范围要求；其余房源还缺距离资料。',['selection']],
    ['还需要了解的部分：\n• 部分房源到学校的距离。\n• 各套房源附近的MRT或公交情况。',['selection','transit:unknown']],
    ['为什么这样排序：有距离资料的房源按离学校的直线距离由近到远展示；缺距离资料的房源暂时不能比较远近。',['ranking:method']],
    ['注意：还不能确认列表中每套房源都符合您的全部要求，当前租金和是否仍可租也需要联系确认。',['selection','availability:current']]])},
  {id:'whole-list-distance',tags:['为什么','几套','推荐','理由'],question:'怎么选出来这几间的？',context:'要求月租不超过2400、学校4公里内且更近优先、希望MRT1公里内。记录月租1800至2200、到学校直线900至1600米均符合。唯一实际排序因素为靠近学校。MRT距离未知。refs：requirements,selection,ranking:method,transit:unknown,availability:current。',output:answer([
    ['您的要求：\n• 月租不超过2,400新币。\n• 学校4公里内，优先离学校更近的。\n• 最好在1公里内有MRT站。',['requirements']],
    ['这些房源符合的部分：\n• 预算：记录月租1,800至2,200新币，都在预算内。\n• 学校距离：直线约900至1,600米，都在您要求的4公里内。',['selection']],
    ['还需要了解的部分：\n• MRT距离：还没有足够资料确认各套是否在站点1公里内。',['transit:unknown']],
    ['为什么这样排序：符合预算和距离范围后，按离学校的直线距离由近到远展示，所以离学校更近的排在前面。',['ranking:method']],
    ['注意：附近MRT的情况还需要了解，当前价格和是否仍可租请联系经纪人确认。',['transit:unknown','availability:current']]])},
  {id:'budget-not-price-sort',tags:['便宜','价格','低','租金','贵'],question:'为什么没把最便宜的放最前面？',context:'必须预算2600。排序仅靠近学校；价格是筛选门槛。fact refs：ranking:method,availability:current。',output:answer([
    ['预算用于先筛掉超支的房子；符合预算后，这次主要看离学校的直线距离，所以排在前面的未必是租金最低的。',['ranking:method']],
    ['记录的租金和现在是否可租，还需要向经纪人确认。',['availability:current']]])},
  {id:'two-listing-comparison',tags:['比较','前两','第一','第二','对比'],question:'前两间差别在哪？',context:'第1套Palm House，月租2100、学校直线900米；第2套Fern Court，月租1850、学校直线1600米。排序仅靠近学校。refs：p1,p2,d1,d2,ranking:method,availability:current。',output:answer([
    ['Palm House 离学校更近，直线约900米；Fern Court 约1.6公里，但每月租金更低。',['p1','p2','d1','d2']],
    ['这次排序更重视离学校近，所以 Palm House 在前。你可以在通勤距离和月租之间取舍，当前价格及是否可租仍需确认。',['ranking:method','availability:current']]])},
  {id:'close-distance-comparison',tags:['前两套有什么区别','距离差','差几米'],question:'解释学校距离如何影响排序，以及前两套有什么区别。',context:'第一套Lake House距学校直线812米，月租1100；第二套Orchid Court直线820米，月租500。必须预算2000、学校5公里内，唯一排序偏好更近。两套直线差约8米，租金差600；没有实际路线。refs：requirements,p1,p2,d1,d2,comparison,ranking:method,availability:current。',output:answer([
    ['前两套都在您2,000新币的预算和学校5公里范围内。\n• Lake House：记录月租1,100新币，到学校直线约812米。\n• Orchid Court：记录月租500新币，到学校直线约820米。',['requirements','p1','p2','d1','d2']],
    ['这次按离学校的直线距离由近到远排列，所以Lake House在前。两套直线距离只差约8米；Orchid Court每月租金低600新币，您可以结合价格考虑。实际走多远、通勤有何差别还需要了解路线。',['ranking:method','comparison']],
    ['注意：当前价格和是否仍可租，请联系经纪人确认。',['availability:current']]])},
  {id:'unknown-hard-condition',tags:['独卫','卫浴','待确认','待核实','符合'],question:'推荐了就说明肯定有独卫吗？',context:'房源满足预算，独卫必须条件未知。待确认列表没有排序分。refs：bath:unknown,price:known,ranking:method,availability:current。',output:answer([
    ['还不能确定。这些房子的记录租金符合预算，但还没有足够资料确认是否配有专用卫生间。',['bath:unknown','price:known']],
    ['目前也没有足够依据判断哪套更适合；可以先问清独卫情况及现在是否仍可租。',['ranking:method','availability:current']]])},
  {id:'multiple-preferences',tags:['排序','面积','多个','综合','公园'],question:'是在按离学校近远排序吗？',context:'必须预算、学校范围。排序共同考虑学校距离、公园距离、面积偏好，没有唯一主因。refs：ranking:method,availability:current。',output:answer([
    ['这次会一起考虑离学校和公园的直线距离，以及你希望的面积大小。符合必须要求后，综合这些偏好来排，所以离学校最近的也不一定排第一。',['ranking:method']],
    ['当前是否还可租，需要再联系确认。',['availability:current']]])},
  {id:'multiple-preferences-english',tags:['two preferences','both preferences','trade-off'],question:'Why were these homes selected and ordered this way?',context:'Hard limits: rent3000/month and university5km straight-line. Soft: closer university and rent1800/month or less. Display order: 1 Garden View, rent1700, distance1600m; 2 Maple House, rent2000, distance900m. Both factors determine order. FIRST IS FARTHER BUT CHEAPER; SECOND IS CLOSER BUT EXCEEDS SOFT RENT TARGET, still within hard budget. refs requirements,rent1,rent2,distance1,distance2,ranking:method,availability:current.',output:answer([
    ['Your requirements:\n• Rent up to S$3,000 per month.\n• Within 5km of the university by straight-line distance.\n• Prefer a closer home and rent at or below S$1,800.',['requirements']],
    ['What fits:\n• Both homes are within the hard budget and distance limits.\n• Garden View is recorded at S$1,700 and 1,600m from the university.\n• Maple House is recorded at S$2,000 and 900m from the university.',['rent1','rent2','distance1','distance2']],
    ['Why this order: both preferences are considered together. Garden View meets your lower-rent preference but is farther away; Maple House is closer but above the preferred rent level. The first home comes ahead in the combined result, not because it is closer. Neither home wins on every preference.',['ranking:method','rent1','rent2','distance1','distance2']],
    ['Note: please confirm current rent and availability with the agent.',['availability:current']]])},
  {id:'behavior-is-secondary',tags:['浏览','兴趣','看过','习惯'],question:'我之前点开的房子会影响这个结果吗？',context:'本轮实际使用已有浏览记录：房型和面积；明确偏好为主要，浏览为辅助。refs：ranking:method,availability:current。',output:answer([
    ['会有一些参考作用。这次先满足你的必须要求，再主要考虑你明确说出的偏好；之前浏览过的房型和面积，也作为辅助线索。它们不会替代你说清楚的要求。',['ranking:method']],
    ['是否仍可租还需要联系经纪人确认。',['availability:current']]])},
  {id:'behavior-ranking',tags:['浏览兴趣','第一套放在前面'],question:'为什么第一套放在前面？',context:'用户只要求预算3000以内，没有明确软偏好。两套租金1900、1600均在预算内。实际排序用了此前浏览记录中的房型和面积；推荐方compare-interest:1:2明确第一套整体上更符合这些浏览兴趣。没有原始参考面积值，不得猜偏爱大房或每项都更像。refs requirement:rent,rent:both,ranking:method,ranking:behavior,compare-interest:1:2,availability:current。',output:answer([
    ['两套的记录月租都在您3,000新币的预算内。',['requirement:rent','rent:both']],
    ['这次您没有明确提出其他排序偏好，所以参考了此前浏览房源的房型、面积等兴趣线索。推荐方的整体匹配结果显示，第一套更贴近这些浏览线索，因此放在前面；这不代表您已经明确要求某种房型或面积。',['ranking:method','ranking:behavior','compare-interest:1:2']],
    ['注意：记录的价格和现在是否仍可租，需要联系经纪人确认。',['availability:current']]])},
  {id:'stable-order',tags:['第一','最好','优势','分数','高'],question:'第一套是不是明显最好？',context:'没有软偏好也没有可用浏览记录；固定次序，不是更优。refs：ranking:method,availability:current。',output:answer([
    ['目前还不能这样判断。这几套符合已有的必须要求，但你还没有给出足够的偏好来区分它们，所以现在沿用固定展示顺序。第一套排在前面，不代表更适合你。',['ranking:method']],
    ['当前是否仍可租，还需要联系确认。',['availability:current']]])},
  {id:'empty-results',tags:['没有','找不到','无房'],question:'怎么这次没有推荐？',context:'本次范围内无可展示结果，不能推断整个市场；不自动改条件。refs：ranking:method,results:empty。',output:answer([
    ['按你目前的要求，这次还没有找到能展示的房源。我们可以先讨论你愿意调整预算还是距离范围，再重新找。',['ranking:method','results:empty']]])},
  {id:'english-explanation',tags:['why','recommend','these','properties'],question:'Why did you choose these properties?',context:'Requirements: monthly rent up to 2400; within 4km of university, nearer preferred; transit within 1km preferred. Recorded rent1800-2200, university straight-line900-1600m. Only university distance ranks; transit unknown. refs: requirements,selection,ranking:method,transit:unknown,availability:current.',output:answer([
    ['Your requirements:\n• Rent up to S$2,400 per month.\n• Within 4km of the university, preferably closer.\n• Ideally within 1km of public transport.',['requirements']],
    ['What fits:\n• Budget: recorded rents are S$1,800–2,200 per month.\n• Location: straight-line distances of about 900–1,600m are within your limit.',['selection']],
    ['Still to check:\n• Whether public transport is within 1km of each home.',['transit:unknown']],
    ['Why this order: among homes within your limits, shorter straight-line distances to the university come first.',['ranking:method']],
    ['Note: nearby transport remains unconfirmed. Please check current rent and availability with the agent.',['transit:unknown','availability:current']]])},
  {id:'route-is-not-line',tags:['步行','分钟','走路','路线'],question:'直线800米就十分钟能走到吗？',context:'只知直线800米，没有实际步行路线。距离为排序参考。refs：distance:known,ranking:method,availability:current。',output:answer([
    ['还不能换算成步行时间。现在知道的是直线约800米，实际走多远、要多久，还得看道路和出入口。这次只是参考直线距离，优先展示更近的选项。',['distance:known','ranking:method']],
    ['现在是否仍可租，也需要联系确认。',['availability:current']]])},
];
EXPLANATION_EXAMPLES.push({id:'distance-tradeoff-with-unranked',tags:['越近越好','一间','1个房间'],question:'改成离学校越近越好，只要一间卧室。',context:'预算5000、1卧室、D05必须。两项排序偏好：学校更近、超市800米内。入选排名：1 Grove Flat，学校2500米、超市60米；2 Elm Flat，学校2300米、超市140米；两项贡献综合后第1在前。补充展示Meadow Flat缺卧室信息，学校2550米、超市57米，not_ranked；已知距离不代表参与排名。预算仅筛选不奖励排名。学校偏好comparison_only，无硬半径。refs requirements,known,missing,ranking,comparison,availability。',output:answer([
  ['您的要求：\n• 月租不超过5,000新币。\n• 一间卧室，位于D05。\n• 离学校越近越好，也希望超市在800米内。',['requirements']],
  ['这些房源符合的部分：Grove Flat和Elm Flat的卧室、预算和邮区记录符合，附近超市的直线距离分别约60米和140米，都在您希望的范围内。',['known']],
  ['还需要了解的部分：Meadow Flat缺少卧室数量资料。它有学校和超市距离记录，但还不能确认是否满足一间卧室，因此只是补充展示，没有进入本次排名。',['missing']],
  ['为什么这样排序：这次同时考虑学校和超市的距离。Grove Flat到超市更近，Elm Flat到学校更近；综合这两项偏好后Grove Flat在前，并不是它每一项都最近。',['ranking','comparison']],
  ['注意：部分房源的卧室数量还需了解；当前租金和是否仍可租，请联系确认。',['missing','availability']],
])});
const genericTags=new Set(['为什么','几套','推荐','理由','why','recommend','these','properties']);
export function selectExplanationExamples(query:string,count=2,contextId?:string){return EXPLANATION_EXAMPLES.map((e,i)=>({e,i,score:(e.id===contextId?100:0)+e.tags.reduce((n,t)=>n+(query.toLowerCase().includes(t.toLowerCase())?(genericTags.has(t)?1:4+Math.min(t.length,12)/12):0),0)})).sort((a,b)=>b.score-a.score||a.i-b.i).slice(0,count).map(x=>x.e);}
