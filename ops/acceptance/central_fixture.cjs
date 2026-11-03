/* Disposable local integration fixture. Real controllers/migrations; no production entrypoint. */
const path = require('path');
const fs = require('fs');
const https = require('https');
const root = process.env.SAMPARK_ACCEPTANCE_CHECKOUT;
if (!root || process.env.APP_PROFILE !== 'acceptance_fixture') throw new Error('Explicit fixture checkout/profile required');
const db = new URL(process.env.SPRING_DATASOURCE_URL || '');
if (db.hostname !== '127.0.0.1' || !/^\/ss_acceptance_[a-z0-9_]+$/.test(db.pathname)) throw new Error('Disposable loopback database required');
const auth = p => path.join(root, 'auth-service', p);
const core = p => path.join(root, 'sampark-saathi-service', p);
// Never load either checkout's saved environment files into a fixture process.
require(auth('node_modules/dotenv')).config = () => ({parsed: {}});
require(core('node_modules/dotenv')).config = () => ({parsed: {}});
require(auth('node_modules/ts-node')).register({project: auth('tsconfig.json'), transpileOnly: true});
require(auth('node_modules/reflect-metadata'));
const express = require(auth('node_modules/express'));
const bcrypt = require(auth('node_modules/bcryptjs'));
async function main() {
  const a = require(auth('src/config/dataSource.ts')).AppDataSource;
  const c = require(core('src/config/dataSource.ts')).AppDataSource;
  await a.initialize(); await a.runMigrations();
  await c.initialize(); await c.runMigrations();
  const passwordHash = await bcrypt.hash(process.env.ACCEPTANCE_PASSWORD, 10);
  for (const [uid, email] of [['fixture-facilitator-a','facilitator-a@example.test'], ['fixture-facilitator-b','facilitator-b@example.test']]) {
    await a.query(`INSERT INTO barabari_edtech_auth.users_identity
      (unique_id,first_name,last_name,email,password_hash,is_active,is_deleted,email_verified,role_id)
      SELECT $1,'Fixture','Staff',$2,$3,true,false,true,role_id FROM barabari_edtech_auth.roles WHERE role_code='FACILITATOR'`, [uid,email,passwordHash]);
    await a.query(`INSERT INTO barabari_edtech_auth.product_permissions(user_id,product_id)
      SELECT u.user_id,p.product_id FROM barabari_edtech_auth.users_identity u CROSS JOIN barabari_edtech_auth.products p
      WHERE u.unique_id=$1 AND p.product_unique_id=$2`,[uid,process.env.SAMPARK_PRODUCT_UNIQUE_ID]);
  }
  await c.query(`INSERT INTO "barabari-edtech".college(college_id,name,city,is_active) VALUES(1,'College A','Fixture',true),(2,'College B','Fixture',true)`);
  await c.query(`INSERT INTO "barabari-edtech".courses(course_id,course_unique_id,course_name) VALUES(1,'fixture-course-a','Course A'),(2,'fixture-course-b','Course B')`);
  for (let i=1;i<=3;i++) {
    await c.query(`INSERT INTO "barabari-edtech".students(student_id,unique_id,first_name,last_name,email,college_id,is_active)
      VALUES($1,$2,'Fixture','Student',$3,$4,true)`,[i,'fixture-student-'+i,'student-'+i+'@example.test',i===2?2:1]);
    await c.query(`INSERT INTO "barabari-edtech".student_course_enrollments(student_id,course_id,active) VALUES($1,1,true)`,[i]);
  }
  const app = express();
  app.use(express.json({limit:'64kb'}));
  app.use(require(auth('node_modules/cookie-parser'))());
  app.use('/barabari-auth/api/auth/public',require(auth('src/controller/publicController.ts')).publicRouter);
  app.use('/barabari-auth/api/auth/internal',require(auth('src/controller/productIdentityController.ts')).productIdentityRouter);
  app.use('/sampark/internal/samvaad-enrollment-snapshots',require(core('src/controller/samvaadEnrollmentSyncController.ts')).enrollmentSyncRouter((sql,params)=>c.query(sql,params),process.env.SAMPARK_ENROLLMENT_SYNC_KEY));
  app.use(require(auth('src/exception/errorHandler.ts')).errorHandler);
  const server=https.createServer({key:fs.readFileSync(process.env.ACCEPTANCE_TLS_KEY),cert:fs.readFileSync(process.env.ACCEPTANCE_TLS_CERT)},app);
  await new Promise(resolve=>server.listen(Number(process.env.ACCEPTANCE_CENTRAL_PORT),'127.0.0.1',resolve));
  fs.writeFileSync(process.env.ACCEPTANCE_READY_FILE,'ready');
  const stop=()=>server.close(async()=>{await a.destroy();await c.destroy();require(auth('src/config/redis.ts')).redis.disconnect();process.exit(0);});
  process.on('SIGTERM',stop);process.on('SIGINT',stop);
}
main().catch(()=>{console.error('Local central fixture failed; inspect private fixture log');process.exit(1);});
