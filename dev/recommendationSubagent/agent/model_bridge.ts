import { createModel } from '../../../src/model.js';
import { AIMessage, HumanMessage, SystemMessage, ToolMessage } from '@langchain/core/messages';
let input = '';
for await (const chunk of process.stdin) input += chunk;
try {
  const payload = JSON.parse(input);
  const model = await createModel();
  const messages = payload.messages.map((m: any) => {
    if (m.role === 'system') return new SystemMessage(m.content);
    if (m.role === 'tool') return new ToolMessage({content:m.content,tool_call_id:m.tool_call_id});
    if (m.role === 'assistant') return new AIMessage({content:m.content,tool_calls:m.tool_calls});
    return new HumanMessage(m.content);
  });
  const bound = model.bindTools(payload.tools.map((t:any)=>({type:'function',function:t})));
  const result = await bound.invoke(messages, {signal: AbortSignal.timeout(60000)});
  process.stdout.write(JSON.stringify({content:result.content,tool_calls:result.tool_calls ?? [],usage:result.usage_metadata ?? null}));
} catch {
  // Provider exceptions can contain endpoint credentials; never forward raw errors.
  process.stderr.write('Model invocation failed\n');
  process.exitCode = 1;
}
