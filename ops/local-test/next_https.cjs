// Run compiled Next applications locally with certificate verification intact.
const fs=require('fs'),https=require('https'),path=require('path');
const state=path.resolve(__dirname,'../../../samvaad-local-test');
// Node loads additional trust roots at process startup, not when process.env is
// changed later. Re-exec once so direct local launches keep TLS verification on.
const ca=path.join(state,'cert.pem');
if(process.env.NODE_EXTRA_CA_CERTS!==ca){
 const result=require('child_process').spawnSync(process.execPath,process.argv.slice(1),{
  stdio:'inherit',env:{...process.env,NODE_EXTRA_CA_CERTS:ca},
 });
 if(result.error)throw result.error;
 process.exit(result.status??1);
}
const kind=process.argv[2];if(!['student','dashboard'].includes(kind))throw Error('Unknown local app');
Object.assign(process.env,JSON.parse(fs.readFileSync(state+'/'+kind+'-session-env.json')),{NODE_ENV:'production'});
const dir=path.resolve(state,'..',kind==='student'?'samvaad-saathi-frontend':'sampark-saathi frontend');
const next=require(path.join(dir,'node_modules/next'));
const app=next({dev:false,dir});const handler=app.getRequestHandler();
app.prepare().then(()=>{
 for(const port of kind==='student'?[3440]:[3441,3442])https.createServer({key:fs.readFileSync(state+'/key.pem'),cert:fs.readFileSync(state+'/cert.pem')},(req,res)=>{req.headers['x-forwarded-proto']='https';req.headers['x-forwarded-host']=req.headers.host;return handler(req,res);}).listen(port,'127.0.0.1');
 console.log(kind+' compiled HTTPS app ready');
});
