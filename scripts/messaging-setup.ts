/** Operator-only setup. Reads secrets from .env; never prints tokens or token-bearing URLs. */
import {readFile} from 'node:fs/promises';
import {parseEnv} from 'node:util';
import {MessagingGateway} from '../src/integrations/messaging.js';
const env={...process.env,...parseEnv(await readFile('.env','utf8'))};
const command=process.argv[2]||'status';
async function telegram(method:string,body?:unknown){
  if(!env.TELEGRAM_BOT_TOKEN)throw Error('TELEGRAM_BOT_TOKEN 未配置');
  let response:Response;try{response=await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body||{}),signal:AbortSignal.timeout(15000),redirect:'error'});}catch{throw Error('Telegram 网络请求失败，请检查网络；密钥未输出');}
  const data=await response.json() as any;if(!response.ok||!data.ok)throw Error(`Telegram 拒绝请求，HTTP ${response.status}；检查 Token / webhook 配置`);return data.result;
}
async function whatsapp(path:string,method='GET'){
  if(!env.WHATSAPP_ACCESS_TOKEN||!/^v\d+\.\d+$/.test(env.WHATSAPP_GRAPH_API_VERSION||''))throw Error('请配置 WhatsApp 访问令牌和有效的 Graph API 版本');
  let response:Response;try{response=await fetch(`https://graph.facebook.com/${env.WHATSAPP_GRAPH_API_VERSION}/${path}`,{method,headers:{authorization:`Bearer ${env.WHATSAPP_ACCESS_TOKEN}`},signal:AbortSignal.timeout(15000),redirect:'error'});}catch{throw Error('Meta 网络请求失败，请检查网络；密钥未输出');}
  let data:any;try{data=await response.json();}catch{throw Error('Meta 返回了无效响应');}
  if(!response.ok)throw Error(`Meta 拒绝请求，HTTP ${response.status}，错误代码 ${data.error?.code??'unknown'}；请检查权限、令牌和资产ID`);return data;
}
try{
  if(command==='status')console.log(JSON.stringify(new MessagingGateway(env).capabilities(),null,2));
  else if(command==='telegram-info'){
    const me=await telegram('getMe'),hook=await telegram('getWebhookInfo');console.log(JSON.stringify({bot_username:me.username,bot_link:`https://t.me/${me.username}`,webhook_set:!!hook.url},null,2));
    if(!hook.url){const rows=await telegram('getUpdates',{timeout:0,limit:100,allowed_updates:['message']});const chats=new Map();for(const u of rows)if(u.message?.chat)chats.set(String(u.message.chat.id),{chat_id:String(u.message.chat.id),type:u.message.chat.type,name:u.message.chat.title||u.message.chat.first_name||''});console.log(JSON.stringify({recent_chats:[...chats.values()],note:'让收件人先向机器人发送 /start，再重新运行。未向任何人发送消息。'},null,2));}
    else console.log('已有 webhook，未调用 getUpdates。可从收到的私有来信记录或 Telegram 管理环境核对 chat_id。');
  }else if(command==='telegram-webhook'){
    const base=new URL(env.PUBLIC_BASE_URL||'');if(base.protocol!=='https:'||base.search||base.hash||base.username||base.password)throw Error('PUBLIC_BASE_URL 须为无凭证的 HTTPS 地址');
    if(!/^[A-Za-z0-9_-]{1,256}$/.test(env.TELEGRAM_WEBHOOK_SECRET||''))throw Error('请配置 TELEGRAM_WEBHOOK_SECRET（1到256个字母数字、下划线或连字符）');
    await telegram('setWebhook',{url:base.origin+'/api/webhooks/telegram',secret_token:env.TELEGRAM_WEBHOOK_SECRET,allowed_updates:['message'],drop_pending_updates:false});console.log('Telegram webhook 已配置；请将 TELEGRAM_RECEIVE_MODE 设置为 webhook 并重启。');
  }else if(command==='telegram-delete-webhook'){
    await telegram('deleteWebhook',{drop_pending_updates:false});console.log('Webhook 已移除，保留待处理更新；可改用 polling 模式并重启。');
  }else if(command==='whatsapp-info'||command==='whatsapp-subscribe'){
    const waba=env.WHATSAPP_BUSINESS_ACCOUNT_ID||'';if(!/^\d+$/.test(waba))throw Error('请配置数字格式的 WHATSAPP_BUSINESS_ACCOUNT_ID');
    if(command==='whatsapp-subscribe'){
      const result=await whatsapp(`${waba}/subscribed_apps`,'POST');if(result.success!==true)throw Error('Meta 没有确认订阅成功，请在控制台核对');console.log('当前应用已订阅所配置 WABA；仍需在 Meta 控制台配置回调地址并订阅 messages 字段。未发送任何聊天消息。');
    }else{
      const phones=await whatsapp(`${waba}/phone_numbers?fields=id,display_phone_number,verified_name`),subscriptions=await whatsapp(`${waba}/subscribed_apps`);
      console.log(JSON.stringify({phone_numbers:(phones.data||[]).map((p:any)=>({id:p.id,display_phone_number:p.display_phone_number,verified_name:p.verified_name})),configured_phone_id_found:(phones.data||[]).some((p:any)=>p.id===env.WHATSAPP_PHONE_NUMBER_ID),subscribed_app_count:Array.isArray(subscriptions.data)?subscriptions.data.length:0,note:'这是只读资产检查，不表示真实发送或 webhook 验收通过。'},null,2));
    }
  }else throw Error('用法：status | telegram-info | telegram-webhook | telegram-delete-webhook | whatsapp-info | whatsapp-subscribe');
}catch(e){console.error(e instanceof Error?e.message:'配置检查失败');process.exitCode=1;}
