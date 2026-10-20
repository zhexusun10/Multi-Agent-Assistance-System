/** EchoMind _TEMPLATES shape: exactly three examples per intent; first feeds LLM few-shot. */
export const INTENT_TEMPLATES={
  search:['我想找一套适合我的出租房','帮我筛选一些房源','Find a home to rent'],
  property_search:['我要租金文泰附近预算2500新币的房子','找新加坡国立大学五公里内有独立卫浴的单间','Find a condo near NUS under SGD 2000 per month'],
  requirement_update:['预算改为2000，其他条件不变','不要独立卫浴这个条件了，重新推荐','Change my budget to 2500 and search again'],
  knowledge:['我想了解一些租房常识','请解释一下租房规则','Explain Singapore rental rules'],
  rental_knowledge:['新加坡HDB最短可以租多久','公寓租约的印花税怎么算，押金一般怎么约定','What are the minimum rental periods for HDB and private homes?'],
  explanation:['请解释推荐结果','我想知道选择这些房子的理由','Explain the recommendations'],
  recommendation_explanation:['为什么推荐第一套而不是第二套','帮我比较前两套的优缺点和待核实项','Why did you recommend the first property?'],
  followup:['我想继续跟进这套房子','请根据之前的沟通处理后续事项','Help me follow up on this property'],
  contact_salesperson:['帮我问第一套的经纪人押金能不能少一点','我想咨询Gold Coast Condo，帮我生成联系草稿','Draft a message to the salesperson about listing 23583402'],
  arrange_viewing:['我想看第一套，帮我约明天下午看房','请为房源23583402起草看房询问','Arrange a viewing for the first recommended property'],
  recommendation_summary:['把当前推荐的房源整理成总结文档','生成前两套房源的对比报告','Generate a document summarising the recommendations'],
  conversation_summary:['把今天我和经纪人的对话生成总结文档','整理今天的会话和跟进记录','Summarise today’s conversation and salesperson replies'],
  followup_status:['第一套的消息发送了吗','经纪人联系了吗，回复了吗','Has my message to the salesperson been sent?'],
  other:['今天天气怎么样','帮我写一个和租房无关的程序','Tell me a joke unrelated to housing'],
} as const;
export type IntentCategory=keyof typeof INTENT_TEMPLATES;
export const INTENT_GROUPS:Record<IntentCategory,string>={search:'search',property_search:'search',requirement_update:'search',knowledge:'knowledge',rental_knowledge:'knowledge',explanation:'explanation',recommendation_explanation:'explanation',followup:'followup',contact_salesperson:'followup',arrange_viewing:'followup',recommendation_summary:'followup',conversation_summary:'followup',followup_status:'followup',other:'other'};
export const GENERIC_INTENTS=new Set<IntentCategory>(['search','knowledge','explanation','followup']);
export const SPECIFIC_INTENTS=new Set<IntentCategory>(['property_search','requirement_update','rental_knowledge','recommendation_explanation','contact_salesperson','arrange_viewing','recommendation_summary','conversation_summary','followup_status']);
export const SPECIFIC_PATTERNS:Partial<Record<IntentCategory,string[]>>={
  contact_salesperson:['经纪人','联系草稿','联系核实草稿','联系核实','咨询草稿','salesperson','draft a message'],
  arrange_viewing:['看房询问','约看房','预约看房','我想看第一套','我要看这套','我想看这套','viewing'],
  conversation_summary:['当天会话','今天的会话','今天的对话','经纪人的对话','会话总结','跟进总结','daily summary'],
  recommendation_summary:['推荐总结','推荐的总结','推荐的房源整理','房源对比报告','总结文档','报告'],
  followup_status:['发送了吗','联系了吗','回复了吗','消息状态','has my message'],
  requirement_update:['预算改','预算调整','取消条件','不要独立卫浴','重新推荐','change my budget'],
  property_search:['找房','找租房','我要租','帮我找','推荐房源','find a condo'],
  recommendation_explanation:['为什么推荐','推荐理由','比较前两套','对比房源','why did you recommend'],
  rental_knowledge:['租房规则','印花税','最短租期','押金','HDB规定','租赁政策','stamp duty','rental rules'],
};
export const GENERIC_PATTERNS:Partial<Record<IntentCategory,string[]>>={search:['租房','预算','房源','rent'],knowledge:['是什么','怎么使用','什么规则','知识','geodesic'],explanation:['解释','理由','比较','对比','explain'],followup:['联系','跟进','草稿','总结','follow up']};
export const INTENT_PARAMETERS={llm_weight:0.7,embedding_weight:0.2,pattern_weight:0.1,llm_without_embedding_weight:0.85,pattern_without_embedding_weight:0.15,confidence_threshold:0.5,refinement_pattern_threshold:0.5,refinement_score_ceiling:0.8,history_messages:3,examples_per_intent:1,templates_per_intent:3,max_tokens:256,temperature:0.1,llm_timeout_ms:8000,cache_max:1000,cache_prune:500,cache_ttl_ms:300000} as const;
