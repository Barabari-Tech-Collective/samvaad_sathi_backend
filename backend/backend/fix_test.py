import re

with open('tests/unit_tests/test_job_profiles_v2_apis.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_endpoint = '''
@_app.patch(
    path="/api/v2/job-profiles/{job_profile_id}/review",
    response_model=JobProfileResponse,
    status_code=200,
)
async def update_job_profile_review(
    job_profile_id: int,
    payload: JobProfileAdminReviewRequest,
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> JobProfileResponse:
    updated_profile = await job_profile_repo.update_review_status(
        profile_id=job_profile_id,
        status=payload.status,
        admin_comment=payload.adminComment
    )
    if not updated_profile:
        raise fastapi.HTTPException(status_code=404, detail="Not found")
    return JobProfileResponse.model_validate(updated_profile)

'''

pattern = re.compile(r'async def get_job_profile_review\(.*?\).*?:\n.*?return JobProfileReviewResponse.*?\(.*?\)\n', re.DOTALL)
match = pattern.search(content)
if match:
    new_content = content[:match.end()] + '\n' + new_endpoint + content[match.end():]
    with open('tests/unit_tests/test_job_profiles_v2_apis.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('Inserted new endpoint')
else:
    print('Not found')
