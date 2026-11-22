import { build } from 'esbuild';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
const root=import.meta.dirname;
const src=resolve(root,'src');
const output=resolve(root,'../realtime/assets');mkdirSync(output,{recursive:true});
const bundle=await build({metafile:true,entryPoints:[resolve(src,'main.tsx')],outfile:resolve(output,'component-ui.js'),bundle:true,minify:true,format:'iife',target:['es2020'],jsx:'automatic',legalComments:'eof',define:{'process.env.NODE_ENV':'"production"'}});
execFileSync(resolve(root,'node_modules/.bin/tailwindcss'),['-i',resolve(src,'styles.css'),'-o',resolve(output,'component-ui.css'),'--minify'],{cwd:root,stdio:'inherit'});
writeFileSync(resolve(output,'component-ui-inputs.json'),JSON.stringify(Object.keys(bundle.metafile.inputs),null,2));
const dependencyNotices=new Map();
for(const input of Object.keys(bundle.metafile.inputs)){
 if(!input.includes('node_modules/'))continue;
 let dir=dirname(resolve(root,input));
 while(dir.startsWith(resolve(root,'node_modules'))&&!existsSync(join(dir,'package.json')))dir=dirname(dir);
 if(!existsSync(join(dir,'package.json')))continue;
 const pkg=JSON.parse(readFileSync(join(dir,'package.json'),'utf8'));
 const file=['LICENSE','LICENSE.md','LICENSE.txt','license','LICENSE-MIT','COPYING'].map(x=>join(dir,x)).find(existsSync);
 const notice=file?readFileSync(file,'utf8'):`${pkg.name} ${pkg.version} — ${pkg.license}; ${pkg.homepage||''}`;
 const names=dependencyNotices.get(notice)||new Set();names.add(`${pkg.name}@${pkg.version}`);dependencyNotices.set(notice,names);
}
const licenses=['shadcn','ai-elements'].map(name=>'\n'+name+'\n'+readFileSync(resolve(src,'vendor',name,'LICENSE'),'utf8')).join('\n')+'\n'+[...dependencyNotices].map(([license,names])=>[...names].sort().join(', ')+'\n'+license).join('\n\n');
writeFileSync(resolve(output,'component-ui-LICENSES.txt'),licenses);
// Also retain notices inside the single-file production artifact.
writeFileSync(resolve(output,'component-ui.js'),readFileSync(resolve(output,'component-ui.js'),'utf8')+'\n/*! '+licenses.replaceAll('*/','* /')+' */\n');
console.log('Built self-contained SearchFlow React components');
