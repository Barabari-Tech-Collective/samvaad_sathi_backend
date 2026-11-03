// Separate staff host/port, forwarding only to the local dashboard TLS listener.
const https=require('https'),fs=require('fs');
const state=require('path').resolve(__dirname,'../../../samvaad-local-test');
const cert=fs.readFileSync(state+'/cert.pem');
https.createServer({key:fs.readFileSync(state+'/key.pem'),cert},(req,res)=>{
 const upstream=https.request({hostname:'127.0.0.1',port:3441,path:req.url,method:req.method,headers:{...req.headers,host:'127.0.0.1:3442','x-forwarded-host':'127.0.0.1:3442','x-forwarded-proto':'https'},ca:cert},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res);});
 upstream.on('error',()=>{res.writeHead(502);res.end('Local dashboard unavailable');});req.pipe(upstream);
}).listen(3442,'127.0.0.1');
