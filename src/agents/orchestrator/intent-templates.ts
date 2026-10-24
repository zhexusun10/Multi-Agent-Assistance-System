/** EchoMind _TEMPLATES shape: exactly three examples per intent; first feeds LLM few-shot. */
export const INTENT_TEMPLATES={
  search:["I want to find a rental home that suits me","Help me shortlist some homes",'Find a home to rent'],
  property_search:["I want to rent near Clementi with a monthly budget of SGD 2500","Find a room with a private bathroom within five kilometres of NUS",'Find a condo near NUS under SGD 2000 per month'],
  requirement_update:["Change the budget to 2000 and keep the other requirements","Remove the private-bathroom requirement and recommend again",'Change my budget to 2500 and search again'],
  knowledge:["I would like some general rental advice","Explain the rental rules",'Explain Singapore rental rules'],
  rental_knowledge:["What is the minimum HDB rental period in Singapore?","How is stamp duty calculated for a condo lease, and how are deposits usually agreed?",'What are the minimum rental periods for HDB and private homes?'],
  explanation:["Explain the recommendations","I would like to know why these homes were selected",'Explain the recommendations'],
  recommendation_explanation:["Why recommend the first home rather than the second?","Compare the first two homes’ advantages, trade-offs and items to confirm",'Why did you recommend the first property?'],
  followup:["I would like to follow up on this home","Help with the next steps based on our earlier conversation",'Help me follow up on this property'],
  contact_salesperson:["Draft an enquiry asking the first home’s agent whether the deposit can be reduced","I am interested in Gold Coast Condo; prepare an enquiry draft",'Draft a message to the salesperson about listing 23583402'],
  arrange_viewing:["I would like to view the first home; help arrange tomorrow afternoon","Draft a viewing enquiry for listing 23583402",'Arrange a viewing for the first recommended property'],
  recommendation_summary:["Generate a summary document of the current recommended homes","Generate a comparison report for the first two homes",'Generate a document summarising the recommendations'],
  conversation_summary:["Summarise today’s conversation with the agent in a document","Summarise today’s conversations and follow-up records",'Summarise today’s conversation and salesperson replies'],
  followup_status:["Has the message about the first home been sent?","Has the agent been contacted, and have they replied?",'Has my message to the salesperson been sent?'],
  other:["What is the weather like today?","Write a program unrelated to renting",'Tell me a joke unrelated to housing'],
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
