/* Local launcher for the full central services; real routes, gateway and broker. */
const fs = require('fs');
const path = require('path');
const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const db = new URL(config.SPRING_DATASOURCE_URL);
if (db.hostname !== '127.0.0.1' || db.port !== '15432' || db.pathname !== '/samvaad_local' || config.REDIS_PORT !== '16379') throw new Error('Dedicated local resources required');
Object.assign(process.env, config);
const root = process.argv[3];
const service = process.argv[4];
if (!['auth-service','sampark-saathi-service'].includes(service)) throw new Error('Unknown service');
const base = path.join(root, service);
require(path.join(base,'node_modules/dotenv')).config = () => ({parsed:{}});
require(path.join(base,'node_modules/ts-node')).register({project:path.join(base,'tsconfig.json'),transpileOnly:true});
require(path.join(base,'node_modules/reflect-metadata'));
// Bind the unmodified service application to loopback; auth additionally serves
// TLS for backend identity verification without disabling certificate validation.
const express = require(path.join(base,'node_modules/express'));
const listen = express.application.listen;
express.application.listen = function(port, callback) {
  if(service==='auth-service') require('https').createServer({key:fs.readFileSync(config.LOCAL_TLS_KEY),cert:fs.readFileSync(config.LOCAL_TLS_CERT)},this).listen(8441,'127.0.0.1');
  return listen.call(this,port,'127.0.0.1',callback);
};
async function migrate() {
  const source = require(path.join(base,'src/config/dataSource.ts')).AppDataSource;
  await source.initialize(); await source.runMigrations();
  if(service==='auth-service') {
    const bcrypt=require(path.join(base,'node_modules/bcryptjs'));
    const hash=await bcrypt.hash(config.LOCAL_TEST_PASSWORD,10);
    for(const [name,role] of [['student','STUDENT'],['facilitator','FACILITATOR'],['admin','ADMIN'],['superadmin','SUPER_ADMIN']]) {
      await source.query(`INSERT INTO barabari_edtech_auth.users_identity(unique_id,first_name,last_name,email,password_hash,is_active,is_deleted,email_verified,student_profile_saved,role_id)
      SELECT $1,$2,'Local',$3,$4,true,false,true,true,role_id FROM barabari_edtech_auth.roles WHERE role_code=$5 ON CONFLICT(unique_id) DO UPDATE SET email=EXCLUDED.email`,['local-'+name,name,name+'@example.com',hash,role]);
      await source.query(`INSERT INTO barabari_edtech_auth.product_permissions(user_id,product_id) SELECT u.user_id,p.product_id FROM barabari_edtech_auth.users_identity u CROSS JOIN barabari_edtech_auth.products p WHERE u.email=$1 ON CONFLICT DO NOTHING`,[name+'@example.com']);
    }
  } else {
    await source.query(`INSERT INTO "barabari-edtech".college(college_id,unique_id,name,city,is_active) VALUES(1,'local-college','Local Test College','Local',true) ON CONFLICT DO NOTHING`);
    await source.query(`INSERT INTO "barabari-edtech".courses(course_id,course_unique_id,course_name) VALUES(1,'local-course','Software Engineering'),(2,'local-course-2','Communication') ON CONFLICT DO NOTHING`);
    await source.query(`INSERT INTO "barabari-edtech".students(student_id,unique_id,first_name,last_name,email,college_id,is_active) VALUES(1,'local-student','Student','Local','student@example.com',1,true) ON CONFLICT DO NOTHING`);
    await source.query(`UPDATE "barabari-edtech".students SET email='student@example.com' WHERE unique_id='local-student'`);
    await source.query(`INSERT INTO "barabari-edtech".student_course_enrollments(student_id,course_id,active) SELECT 1,1,true WHERE NOT EXISTS(SELECT 1 FROM "barabari-edtech".student_course_enrollments WHERE student_id=1 AND course_id=1)`);
    for(const [table,column] of [['college','college_id'],['courses','course_id'],['students','student_id']]) await source.query(`SELECT setval(pg_get_serial_sequence('"barabari-edtech".${table}',$1),GREATEST((SELECT max(${column}) FROM "barabari-edtech".${table}),1))`,[column]);
  }
  await source.destroy();
}
if(process.argv[5]==='migrate') migrate().then(()=>process.exit(0)).catch(e=>{console.error(e);process.exit(1);});
else require(path.join(base,'src/index.ts'));
