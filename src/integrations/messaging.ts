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
    if(!/^\+?[1-9]\d{8,14}$/.test(value))throw new BusinessError('INVALID_CONTACT',"WhatsApp numbers need a country code, for example +6591234567. Add 65 explicitly to Singapore eight-digit numbers.",422);
    return value.replace(/^\+/,'');
  }
  if(!/^-?\d{1,18}$/.test(value))throw new BusinessError('INVALID_CONTACT',"Enter the numeric Telegram chat_id obtained after the recipient contacts the bot, not a phone number.",422);
  return value;
}
export function renderTemplate(t:ApprovedTemplate,parameters:string[]){
  if(parameters.length!==t.variables)throw new BusinessError('TEMPLATE_PARAMETERS',"The template parameter count does not match.",422);
  return t.body.replace(/\{\{(\d+)\}\}/g,(_,n)=>parameters[Number(n)-1])+(t.footer?'\n'+t.footer:'');
}
/** Fixed official origins, bounded requests and no retries: a timeout may follow a successful send. */
export class MessagingGateway{
  constructor(readonly env:NodeJS.ProcessEnv=process.env,private transport:typeof fetch=fetch){}
  capabilities(){return {mode:this.env.EXTERNAL_ACTION_MODE==='live'?'live':'simulation',channels:{
    whatsapp:{configured:present(this.env,['WHATSAPP_ACCESS_TOKEN','WHATSAPP_PHONE_NUMBER_ID','WHATSAPP_GRAPH_API_VERSION']),templates_configured:!!this.env.WHATSAPP_BUSINESS_ACCOUNT_ID,webhook_configured:present(this.env,['WHATSAPP_APP_SECRET','WHATSAPP_VERIFY_TOKEN']),note:"A number does not prove WhatsApp access. First contact needs an approved template and recipient consent."},
    telegram:{configured:!!this.env.TELEGRAM_BOT_TOKEN,webhook_configured:!!this.env.TELEGRAM_WEBHOOK_SECRET,note:"The recipient must contact the bot first; use their numeric chat_id. Platform acceptance does not mean read."}
  }};}
  private version(){const v=this.env.WHATSAPP_GRAPH_API_VERSION||'';if(!/^v\d+\.\d+$/.test(v))throw new BusinessError('PROVIDER_CONFIG',"Configure the Graph API version shown in the Meta console.");return v;}
  private async request(url:string,options:RequestInit){
    let response:Response;
    try{response=await this.transport(url,{...options,redirect:'error',signal:AbortSignal.timeout(15000)});}catch{throw new DeliveryError('UNKNOWN','NETWORK_RESULT_UNKNOWN');}
    let data:any;try{data=await response.json();}catch{throw new DeliveryError(response.ok||response.status>=500?'UNKNOWN':'FAILED','INVALID_PROVIDER_RESPONSE');}
    if(!response.ok||data.ok===false)throw new DeliveryError(response.status>=500?'UNKNOWN':'FAILED',`PROVIDER_${response.status}_${String(data.error?.code??data.error_code??'REJECTED').replace(/[^\w-]/g,'').slice(0,40)}`);
    return data;
  }
  async templates():Promise<ApprovedTemplate[]>{
    if(!present(this.env,['WHATSAPP_ACCESS_TOKEN','WHATSAPP_BUSINESS_ACCOUNT_ID']))throw new BusinessError('TEMPLATE_CONFIG',"Configure the WhatsApp Business Account ID and access token to load approved templates.");
    const id=this.env.WHATSAPP_BUSINESS_ACCOUNT_ID!;if(!/^\d+$/.test(id))throw new BusinessError('PROVIDER_CONFIG',"Invalid WhatsApp Business Account ID format.");
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
    if(!this.env.TELEGRAM_BOT_TOKEN)throw new BusinessError('PROVIDER_NOT_CONFIGURED',"Telegram token is not configured.");
    const data=await this.request(`https://api.telegram.org/bot${this.env.TELEGRAM_BOT_TOKEN}/getUpdates`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({offset,timeout:0,limit:100,allowed_updates:['message']})});return Array.isArray(data.result)?data.result:[];
  }
  async validate(message:SendMessage,requireLive=true){
    const {recipient:r}=message;
    if(!r.verified||!r.opt_in||!r.channel||!r.address)throw new BusinessError('CONTACT_UNVERIFIED',"Verify the contact, select WhatsApp or Telegram and confirm permission to contact them on that channel.");
    normalizeAddress(r.channel,r.address);
    if(!message.body.trim())throw new BusinessError('EMPTY_MESSAGE',"The message cannot be empty.");
    if(!requireLive)return;
    const caps=this.capabilities();if(caps.mode!=='live')throw new BusinessError('LIVE_DISABLED',"Simulation mode is active. Configure platform credentials and live mode before real sending.");
    if(!caps.channels[r.channel].configured)throw new BusinessError('PROVIDER_NOT_CONFIGURED',`${r.channel} credentials are not configured. See the project setup guide.`);
    if(r.channel==='telegram'){
      if(message.body.length>4096)throw new BusinessError('MESSAGE_TOO_LONG',"Telegram messages are limited to 4096 characters. Shorten the draft.",422);
      if(message.template)throw new BusinessError('INVALID_TEMPLATE',"Telegram does not use WhatsApp templates.");
    }else if(message.template){
      const t=(await this.templates()).find(t=>t.name===message.template!.name&&t.language===message.template!.language);
      if(!t||renderTemplate(t,message.template.parameters)!==message.body)throw new BusinessError('TEMPLATE_MISMATCH',"The template changed, is unapproved or does not match its parameters. Preview and approve it again.");
    }else{
      if(message.body.length>4096)throw new BusinessError('MESSAGE_TOO_LONG',"The WhatsApp message is too long. Shorten the draft.",422);
      const age=Date.now()-Date.parse(message.lastInboundAt||'');
      if(!Number.isFinite(age)||age<0||age>=24*3600000)throw new BusinessError('WHATSAPP_TEMPLATE_REQUIRED',"No incoming message within 24 hours is recorded by the server. First contact or an expired window requires an approved template.");
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
      const phone=this.env.WHATSAPP_PHONE_NUMBER_ID!;if(!/^\d+$/.test(phone))throw new BusinessError('PROVIDER_CONFIG',"Invalid WhatsApp Phone Number ID format.");
      const template=message.template;
      const payload=template?{type:'template',template:{name:template.name,language:{code:template.language},components:template.parameters.length?[{type:'body',parameters:template.parameters.map(text=>({type:'text',text}))}]:[]}}:{type:'text',text:{preview_url:false,body:message.body}};
      data=await this.request(`https://graph.facebook.com/${this.version()}/${phone}/messages`,{method:'POST',headers:{authorization:`Bearer ${this.env.WHATSAPP_ACCESS_TOKEN}`,'content-type':'application/json'},body:JSON.stringify({messaging_product:'whatsapp',recipient_type:'individual',to,...payload})});
      id=data.messages?.[0]?.id;
    }
    if(typeof id!=='string'&&typeof id!=='number')throw new DeliveryError('UNKNOWN','MISSING_MESSAGE_ID');
    return {message_id:String(id),status:'ACCEPTED'};
  }
}
