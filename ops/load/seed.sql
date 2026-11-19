-- Disposable load-test data: 2,000 students in 20 colleges, 10 interviews each,
-- 5 answers per interview and a scored report for finished ones.
INSERT INTO "user" (email, password_hash, name, university, created_at, is_onboarded)
SELECT 'seed' || g || '@samvaad-load.org', 'x', 'Seed ' || g, 'College ' || (g % 20),
       now() - (g % 120) * interval '1 day', true
FROM generate_series(1, 2000) g
ON CONFLICT DO NOTHING;

INSERT INTO interview (user_id, track, status, created_at, difficulty, interview_mode)
SELECT u.id,
       (ARRAY['Data analyst','Customer support','Full Stack Developer','Sales'])[1 + (i % 4)],
       CASE WHEN i % 5 = 0 THEN 'active' ELSE 'completed' END,
       now() - ((u.id + i * 7) % 90) * interval '1 day',
       (ARRAY['easy','medium','hard'])[1 + (i % 3)],
       CASE WHEN i % 3 = 0 THEN 'assessment' ELSE 'practice' END
FROM "user" u CROSS JOIN generate_series(1, 10) i
WHERE u.email LIKE 'seed%@samvaad-load.org';

INSERT INTO question_attempt (interview_id, question_text, created_at, transcription_status, attempt_state)
SELECT iv.id, 'Seed question ' || q, iv.created_at, 'completed', 'submitted'
FROM interview iv CROSS JOIN generate_series(1, 5) q;

INSERT INTO summary_report (interview_id, report_json, created_at)
SELECT iv.id,
       jsonb_build_object('overallScoreSummary', jsonb_build_object(
         'knowledgeCompetence', jsonb_build_object('averagePct', 40 + (iv.id % 55)),
         'speechStructure', jsonb_build_object('averagePct', 35 + (iv.id % 60)))),
       iv.created_at
FROM interview iv WHERE iv.status = 'completed';
ANALYZE;
