// Keep the graph in the foreground of a managed terminal. Shell job cleanup can
// otherwise terminate a hidden Start-Process child after its launching test exits.
import {readFile,writeFile} from 'node:fs/promises';
const recordPath=new URL('../data/.runtime/app-processes.json',import.meta.url);
const record=JSON.parse((await readFile(recordPath,'utf8')).replace(/^\uFEFF/,''));
record.processes=[...record.processes.filter(p=>p.name!=='graph'),{
  name:'graph',id:process.pid,started:new Date().toISOString(),executable:process.execPath
}];
await writeFile(recordPath,JSON.stringify(record,null,2));
await import('../dist/server.js');
