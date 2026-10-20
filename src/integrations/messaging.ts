import { BusinessError } from '../stores/domain.js';

export type Channel='whatsapp'|'telegram';
export type Recipient={name:string;email?:string;verified:boolean;channel?:Channel;address?:string;opt_in?:boolean};
export type Template={name:string;language:string;parameters:string[]};
export type SendMessage={recipient:Recipient;body:string;template?:Template;lastInboundAt?:string|null};
export type Accepted={message_id:string;status:'ACCEPTED'};
export class DeliveryError extends Error{
  constructor(public outcome:'FAILED'|'UNKNOWN',public code:string){super(code);}
}
export type ApprovedTemplate={name:string;language:string;body:string;footer:string;variables:number};
const present=(e:NodeJS.ProcessEnv,keys:string[])=>keys.every(k=>!!e[k]?.trim());
export function normalizeAddress(channel:Channel,address:string){
  const value=address.trim();
  if(channel==='whatsapp'){
    if(!/^\+?[1-9]\d{8,14}$/.test(value))throw new BusinessError('INVALID_CONTACT','WhatsApp 号码须包含国家代码，例如 +6591234567；新加坡本地8位号码请明确加65。',422);
    return value.replace(/^\+/,'');
  }
  if(!/^-?\d{1,18}$/.test(value))throw new BusinessError('INVALID_CONTACT','Telegram 请填写对方与机器人建立联系后取得的数字 chat_id，不能用电话号码替代。',422);
  return value;
}
export function renderTemplate(t:ApprovedTemplate,parameters:string[]){
  if(parameters.length!==t.variables)throw new BusinessError('TEMPLATE_PARAMETERS','模板参数数量不匹配。',422);
  return t.body.replace(/\{\{(\d+)\}\}/g,(_,n)=>parameters[Number(n)-1])+(t.footer?'\n'+t.footer:'');
}
/** Fixed official origins, bounded requests and no retries: a timeout may follow a successful send. */
export class MessagingGateway{
  constructor(readonly env:NodeJS.ProcessEnv=process.env,private transport:typeof fetch=fetch){}
  capabilities(){return {mode:this.env.EXTERNAL_ACTION_MODE==='live'?'live':'simulation',channels:{
    whatsapp:{configured:present(this.env,['WHATSAPP_ACCESS_TOKEN','WHATSAPP_PHONE_NUMBER_ID','WHATSAPP_GRAPH_API_VERSION']),templates_configured:!!this.env.WHATSAPP_BUSINESS_ACCOUNT_ID,webhook_configured:present(this.env,['WHATSAPP_APP_SECRET','WHATSAPP_VERIFY_TOKEN']),note:'号码本身不能证明开通了 WhatsApp。首次主动联系须使用获批模板及收件人许可。'},
    telegram:{configured:!!this.env.TELEGRAM_BOT_TOKEN,webhook_configured:!!this.env.TELEGRAM_WEBHOOK_SECRET,note:'对方须先联系机器人；请使用其数字 chat_id。平台受理不代表已读。'}
  }};}
  private version(){const v=this.env.WHATSAPP_GRAPH_API_VERSION||'';if(!/^v\d+\.\d+$/.test(v))throw new BusinessError('PROVIDER_CONFIG','请按 Meta 控制台配置 Graph API 版本。');return v;}
  private async request(url:string,options:RequestInit){
    let response:Response;
    try{response=await this.transport(url,{...options,redirect:'error',signal:AbortSignal.timeout(15000)});}catch{throw new DeliveryError('UNKNOWN','NETWORK_RESULT_UNKNOWN');}
    let data:any;try{data=await response.json();}catch{throw new DeliveryError(response.ok||response.status>=500?'UNKNOWN':'FAILED','INVALID_PROVIDER_RESPONSE');}
    if(!response.ok||data.ok===false)throw new DeliveryError(response.status>=500?'UNKNOWN':'FAILED',`PROVIDER_${response.status}_${String(data.error?.code??data.error_code??'REJECTED').replace(/[^\w-]/g,'').slice(0,40)}`);
    return data;
  }
  async templates():Promise<ApprovedTemplate[]>{
    if(!present(this.env,['WHATSAPP_ACCESS_TOKEN','WHATSAPP_BUSINESS_ACCOUNT_ID']))throw new BusinessError('TEMPLATE_CONFIG','请配置 WhatsApp Business Account ID 和访问令牌，以读取获批模板。');
    const id=this.env.WHATSAPP_BUSINESS_ACCOUNT_ID!;if(!/^\d+$/.test(id))throw new BusinessError('PROVIDER_CONFIG','WhatsApp Business Account ID 格式错误。');
    const data=await this.request(`https://graph.facebook.com/${this.version()}/${id}/message_templates?fields=name,status,language,components&limit=100`,{headers:{authorization:`Bearer ${this.env.WHATSAPP_ACCESS_TOKEN}`}});
    return (Array.isArray(data.data)?data.data:[]).flatMap((t:any)=>{
      if(t.status!=='APPROVED'||!Array.isArray(t.components)||t.components.some((c:any)=>!['BODY','FOOTER'].includes(c.type)))return [];
      const body=t.components.find((c:any)=>c.type==='BODY')?.text;if(typeof body!=='string')return [];
      const vars=[...body.matchAll(/\{\{(\d+)\}\}/g)].map(m=>Number(m[1])),n=Math.max(0,...vars);
      if(/\{\{[^\d]/.test(body)||n>20||Array.from({length:n},(_,i)=>i+1).some(i=>!vars.includes(i)))return [];
      return [{name:t.name,language:t.language,body,footer:t.components.find((c:any)=>c.type==='FOOTER')?.text||'',variables:n}];
    });
  }
  async telegramUpdates(offset:number){
    if(!this.env.TELEGRAM_BOT_TOKEN)throw new BusinessError('PROVIDER_NOT_CONFIGURED','Telegram Token 尚未配置。');
    const data=await this.request(`https://api.telegram.org/bot${this.env.TELEGRAM_BOT_TOKEN}/getUpdates`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({offset,timeout:0,limit:100,allowed_updates:['message']})});return Array.isArray(data.result)?data.result:[];
  }
  async validate(message:SendMessage,requireLive=true){
    const {recipient:r}=message;
    if(!r.verified||!r.opt_in||!r.channel||!r.address)throw new BusinessError('CONTACT_UNVERIFIED','请核实联系人、选择 WhatsApp 或 Telegram，并确认对方允许你通过此渠道联系。');
    normalizeAddress(r.channel,r.address);
    if(!message.body.trim())throw new BusinessError('EMPTY_MESSAGE','消息正文不能为空。');
    if(!requireLive)return;
    const caps=this.capabilities();if(caps.mode!=='live')throw new BusinessError('LIVE_DISABLED','当前为模拟模式；配置平台凭证并开启 live 后才能真实发送。');
    if(!caps.channels[r.channel].configured)throw new BusinessError('PROVIDER_NOT_CONFIGURED',`${r.channel} 凭证尚未配置，请参阅项目申请与配置指南。`);
    if(r.channel==='telegram'){
      if(message.body.length>4096)throw new BusinessError('MESSAGE_TOO_LONG','Telegram 单条消息上限为 4096 字符，请缩短草稿。',422);
      if(message.template)throw new BusinessError('INVALID_TEMPLATE','Telegram 不使用 WhatsApp 模板。');
    }else if(message.template){
      const t=(await this.templates()).find(t=>t.name===message.template!.name&&t.language===message.template!.language);
      if(!t||renderTemplate(t,message.template.parameters)!==message.body)throw new BusinessError('TEMPLATE_MISMATCH','模板已变更、尚未获批或正文与模板参数不一致，请重新预览和审批。');
    }else{
      if(message.body.length>4096)throw new BusinessError('MESSAGE_TOO_LONG','WhatsApp 文本过长，请缩短草稿。',422);
      const age=Date.now()-Date.parse(message.lastInboundAt||'');
      if(!Number.isFinite(age)||age<0||age>=24*3600000)throw new BusinessError('WHATSAPP_TEMPLATE_REQUIRED','没有服务端记录的 24 小时内来信。首次或窗口外联系须选用获批模板。');
    }
  }
  async send(message:SendMessage):Promise<Accepted>{
    await this.validate(message);
    const r=message.recipient,to=normalizeAddress(r.channel!,r.address!);
    let data:any,id:unknown;
    if(r.channel==='telegram'){
      data=await this.request(`https://api.telegram.org/bot${this.env.TELEGRAM_BOT_TOKEN}/sendMessage`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({chat_id:to,text:message.body})});
      id=data.result?.message_id;
    }else{
      const phone=this.env.WHATSAPP_PHONE_NUMBER_ID!;if(!/^\d+$/.test(phone))throw new BusinessError('PROVIDER_CONFIG','WhatsApp Phone Number ID 格式错误。');
      const template=message.template;
      const payload=template?{type:'template',template:{name:template.name,language:{code:template.language},components:template.parameters.length?[{type:'body',parameters:template.parameters.map(text=>({type:'text',text}))}]:[]}}:{type:'text',text:{preview_url:false,body:message.body}};
      data=await this.request(`https://graph.facebook.com/${this.version()}/${phone}/messages`,{method:'POST',headers:{authorization:`Bearer ${this.env.WHATSAPP_ACCESS_TOKEN}`,'content-type':'application/json'},body:JSON.stringify({messaging_product:'whatsapp',recipient_type:'individual',to,...payload})});
      id=data.messages?.[0]?.id;
    }
    if(typeof id!=='string'&&typeof id!=='number')throw new DeliveryError('UNKNOWN','MISSING_MESSAGE_ID');
    return {message_id:String(id),status:'ACCEPTED'};
  }
}
