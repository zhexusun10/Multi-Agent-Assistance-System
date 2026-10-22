import {readFileSync} from 'node:fs';
import {parseEnv} from 'node:util';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
// Local project credentials take precedence over unrelated parent-shell defaults.
try{Object.assign(process.env,parseEnv(readFileSync(resolve(root,'.env'),'utf8')));}catch(error){if(error.code!=='ENOENT')throw error;}
const target=process.argv[2];if(!target)throw Error('Usage: node [--import tsx] scripts/with-project-env.mjs <entry> [arguments]');
const path=resolve(root,target);process.argv=[process.argv[0],path,...process.argv.slice(3)];await import(pathToFileURL(path).href);
