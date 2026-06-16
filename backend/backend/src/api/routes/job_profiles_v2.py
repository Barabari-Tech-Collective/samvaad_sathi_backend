import fastapi
from fastapi import File, UploadFile
from typing import List, Optional
import logging
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.models.schemas.job_profile import (
    JobProfileSummaryResponse, 
    JobProfileResponse, 
    JobProfileCreateV2,
    JobProfileListResponse,
    JobProfileActivityResponse,
    JobProfileUploadResponse,
    JobProfileExtractSkillsRequest,
    JobProfileExtractSkillsResponse,
    JobProfileGenerateQuestionsRequest,
    JobProfileGenerateQuestionsResponse,
    JobProfileGeneratedQuestionItem,
    JobProfileQuestionsListResponse,
    JobProfileQuestionItem,
    JobProfileQuestionLevelCounts,
    JobProfileAddQuestionRequest,
    JobProfileAddQuestionResponse,
    JobProfileUpdateQuestionRequest,
    JobProfileUpdateQuestionResponse,
    JobProfileRegenerateQuestionResponse,
    JobProfileDeleteQuestionResponse,
    JobProfileDeleteResponse,
    JobProfileReviewResponse,
    JobProfileReviewRoleDetails,
    JobProfileReviewJdSummary,
    JobProfileReviewPreviewQuestion,
    JobProfileReviewLevelInfo,
    JobProfileReviewQuestionSummary,
    JobProfileSubmitResponse
)
from src.services.file_processor import validate_file
from src.services.skills_extractor import extract_skills_from_text
from src.repository.crud.job_profile import JobProfileCRUDRepository
from src.services.llm import generate_interview_questions_with_llm
from src.services.syllabus_service import syllabus_service

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/v2", tags=["job-profiles-v2"])

@router.get(
    path="/job-profiles/recent-activity",
    name="job-profiles:recent-activity",
    response_model=List[JobProfileActivityResponse],
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get recent role-related activity",
)
async def get_recent_activity(
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> List[JobProfileActivityResponse]:
    """
    Returns the latest 5 activities related to job profiles.
    Derived from the job_profile table records.
    """
    activities = await job_profile_repo.get_recent_activity(limit=5)
    return [JobProfileActivityResponse(**a) for a in activities]

@router.get(
    path="/job-profiles/summary",
    name="job-profiles:summary",
    response_model=JobProfileSummaryResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get summary counts for Job Profiles",
)
async def get_job_profiles_summary(
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileSummaryResponse:
    """
    Returns summary counts for the Roles page cards:
    - Total Roles (real count)
    - Pending Review (default 0)
    - Approved (default 0)
    - Rejected (default 0)
    """
    summary_data = await job_profile_repo.get_summary()
    return JobProfileSummaryResponse(**summary_data)

@router.get(
    path="/job-profiles",
    name="job-profiles:list",
    response_model=JobProfileListResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="List all Job Profiles",
)
async def list_job_profiles(
    category: Optional[str] = fastapi.Query(None),
    limit: Optional[int] = fastapi.Query(None),
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileListResponse:
    profiles = await job_profile_repo.list_profiles(category=category, limit=limit)
    return JobProfileListResponse(items=profiles, total=len(profiles))

@router.post(
    path="/job-profiles",
    name="job-profiles:create",
    response_model=JobProfileResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Create a new Job Profile",
)
async def create_job_profile(
    payload: JobProfileCreateV2,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileResponse:
    profile = await job_profile_repo.create_profile(
        job_name=payload.job_name,
        job_description=payload.job_description,
        company_name=payload.company_name,
        experience_level=payload.experience_level,
        skills=payload.skills,
        additional_context=payload.additional_context,
        category=payload.category,
        employment_type=payload.employment_type,
    )
    return JobProfileResponse.model_validate(profile)


@router.get(
    path="/job-profiles/{job_profile_id}/review",
    name="job-profiles:get-review",
    response_model=JobProfileReviewResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get Job Profile Review Summary for Step 5",
)
async def get_job_profile_review(
    job_profile_id: int,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileReviewResponse:
    # 1. Fetch JobProfile record
    profile = await job_profile_repo.get_by_id(job_profile_id=job_profile_id)
    if not profile:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Job profile with ID {job_profile_id} not found"
        )

    # 2. Fetch all linked questions
    questions = await job_profile_repo.get_job_profile_questions(job_profile_id=job_profile_id)

    # 3. Parse competencies from additional_context
    competencies = []
    if profile.additional_context:
        if "," in profile.additional_context:
            competencies = [c.strip() for c in profile.additional_context.split(",") if c.strip()]
        elif "\n" in profile.additional_context:
            competencies = [c.strip() for c in profile.additional_context.split("\n") if c.strip()]
        else:
            competencies = [profile.additional_context.strip()]

    # 4. Map role details
    role_details = JobProfileReviewRoleDetails(
        role_name=profile.job_name,
        company_name=profile.company_name,
        category=profile.category,
        experience_level=profile.experience_level,
        employment_type=profile.employment_type,
        description=profile.job_description
    )

    # 5. Map JD summary
    jd_summary = JobProfileReviewJdSummary(
        extracted_skills=profile.skills or [],
        competencies=competencies
    )

    # 6. Map levels static titles and descriptions
    LEVEL_METADATA = {
        1: {
            "title": "General Fundamentals",
            "description": "Basic concepts and foundational knowledge questions."
        },
        2: {
            "title": "Project & Resume Based",
            "description": "Questions based on resume projects and practical implementation."
        },
        3: {
            "title": "Production & Scenario Based",
            "description": "Production-level debugging and real-world problem-solving questions."
        },
        4: {
            "title": "Advanced / Pressure Scenarios",
            "description": "High-pressure and advanced real-world interview situations."
        }
    }

    levels_list = []
    for lvl in [1, 2, 3, 4]:
        meta = LEVEL_METADATA[lvl]
        lvl_questions = [q for q in questions if q.level == lvl]
        
        # Get first 3 questions as preview questions
        preview = [
            JobProfileReviewPreviewQuestion(question_id=q.id, question=q.question_text)
            for q in lvl_questions[:3]
        ]
        
        levels_list.append(JobProfileReviewLevelInfo(
            level=lvl,
            title=meta["title"],
            description=meta["description"],
            question_count=len(lvl_questions),
            preview_questions=preview
        ))

    # 7. Calculate totals
    total_questions = len(questions)
    total_levels = sum(1 for lvl in [1, 2, 3, 4] if any(q.level == lvl for q in questions))

    question_summary = JobProfileReviewQuestionSummary(
        total_questions=total_questions,
        total_levels=total_levels,
        levels=levels_list
    )

    return JobProfileReviewResponse(
        job_profile_id=job_profile_id,
        role_details=role_details,
        jd_summary=jd_summary,
        question_summary=question_summary,
        status="draft"
    )


@router.post(
    path="/job-profiles/{job_profile_id}/submit",
    name="job-profiles:submit",
    response_model=JobProfileSubmitResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Submit a Job Profile for review",
)
async def submit_job_profile(
    job_profile_id: int,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileSubmitResponse:
    # 1. Fetch JobProfile record
    profile = await job_profile_repo.get_by_id(job_profile_id=job_profile_id)
    if not profile:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Job profile not found"
        )

    # 2. Fetch all linked questions
    questions = await job_profile_repo.get_job_profile_questions(job_profile_id=job_profile_id)

    # 3. Validate generated questions exist
    if not questions:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="Cannot submit role without generated questions"
        )

    # 4. Submit the profile (updates status to 'under_review' and saves current timestamp)
    updated_profile = await job_profile_repo.submit_profile(job_profile_id=job_profile_id)

    # 5. Calculate counts
    total_questions = len(questions)
    total_levels = sum(1 for lvl in [1, 2, 3, 4] if any(q.level == lvl for q in questions))

    return JobProfileSubmitResponse(
        job_profile_id=updated_profile.id,
        job_name=updated_profile.job_name,
        status=updated_profile.status,
        submitted_at=updated_profile.submitted_at,
        total_questions=total_questions,
        total_levels=total_levels,
        message="Role submitted successfully"
    )





@router.delete(
    path="/job-profiles/{job_profile_id}",
    name="job-profiles:delete",
    response_model=JobProfileDeleteResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Delete a shared job profile",
)
async def delete_job_profile(
    job_profile_id: int,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileDeleteResponse:
    deleted = await job_profile_repo.delete_profile(profile_id=job_profile_id)
    if not deleted:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Job profile with ID {job_profile_id} not found"
        )
    return JobProfileDeleteResponse(deleted=True, job_profile_id=job_profile_id)



@router.post(
    path="/job-profiles/upload/job-description",
    name="job-profiles:upload-jd",
    response_model=JobProfileUploadResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Upload Job Description file",
)
async def upload_job_description(
    file: UploadFile = File(..., description="PDF or DOC/DOCX file (max 10MB)"),
    current_user=fastapi.Depends(get_current_user),
) -> JobProfileUploadResponse:
    """
    Validates and processes the uploaded Job Description file entirely in memory.
    No file is written to disk and no metadata is persisted.
    """
    extension, size = await validate_file(file)
    return JobProfileUploadResponse(
        success=True,
        original_file_name=file.filename or "",
        file_type=extension.replace(".", ""),
        file_size=size,
    )


@router.post(
    path="/job-profiles/upload/knowledge-questions",
    name="job-profiles:upload-knowledge",
    response_model=JobProfileUploadResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Upload Knowledge Set Questions file",
)
async def upload_knowledge_questions(
    file: UploadFile = File(..., description="PDF, DOC/DOCX, or TXT file (max 10MB)"),
    current_user=fastapi.Depends(get_current_user),
) -> JobProfileUploadResponse:
    """
    Validates and processes the uploaded Knowledge Questions file entirely in memory.
    Extracts text and parses it into topic-wise and level-wise questions.
    """
    import io
    import re
    import datetime
    import zipfile
    import xml.etree.ElementTree as ET
    import PyPDF2
    
    # 1. Existing validation
    extension, size = await validate_file(file)
    
    uploaded_at = datetime.datetime.now(datetime.timezone.utc)
    
    # 2. Extract text and parse
    topics_detected = []
    total_questions = 0
    topics_list = []
    
    try:
        await file.seek(0)
        content = await file.read()
        
        extracted_text = ""
        ext = extension.lower()
        if ext == ".txt":
            extracted_text = content.decode("utf-8", errors="ignore")
        elif ext == ".pdf":
            try:
                pdf_reader = PyPDF2.PdfReader(io.BytesIO(content))
                texts = []
                for page in pdf_reader.pages:
                    try:
                        page_text = page.extract_text() or ""
                        if page_text.strip():
                            texts.append(page_text)
                    except Exception:
                        continue
                extracted_text = "\n".join(texts)
            except Exception as e:
                logger.error(f"Error parsing PDF file: {e}")
                extracted_text = ""
        elif ext == ".docx":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    xml_content = z.read("word/document.xml")
                    root = ET.fromstring(xml_content)
                    texts = []
                    for elem in root.iter():
                        if elem.tag.endswith('}t'):
                            if elem.text:
                                texts.append(elem.text)
                    extracted_text = " ".join(texts)
            except Exception as e:
                logger.error(f"Error parsing DOCX file: {e}")
                extracted_text = ""
        elif ext == ".doc":
            extracted_text = content.decode("utf-8", errors="ignore")
            
        # Parse text if we extracted anything
        if extracted_text.strip():
            lines = [line.strip() for line in extracted_text.split("\n")]
            
            # Clean unwanted lines
            filtered_lines = []
            unwanted_substrings = [
                "Barabari Tech Collective",
                "Expanded Interview Knowledge Base",
                "Engineering Interview Guidelines",
                "Copyright",
                "Confidential",
                "Page",
                "v2.0 May 2026",
                "Assessment Level",
                "Sample Questions / Topics",
                "Document Prepared By",
                "Sharath Nair",
                "Engineering Manager"
            ]
            
            for line in lines:
                clean = line.strip()
                if not clean:
                    continue
                if clean in ("•", "-", "*"):
                    continue
                
                is_unwanted = False
                for sub in unwanted_substrings:
                    if sub.lower() in clean.lower():
                        is_unwanted = True
                        break
                if not is_unwanted:
                    filtered_lines.append(clean)
                    
            def is_question_start_prefix(clean_line: str) -> bool:
                question_prefixes = [
                    "What", "Explain", "How", "When", "Why", "Write", "Discuss",
                    "Advanced CSS", "Advanced TypeScript", "JWT vs", "Microservices vs", "gRPC vs"
                ]
                for prefix in question_prefixes:
                    if clean_line.lower().startswith(prefix.lower()):
                        if len(clean_line) == len(prefix) or clean_line[len(prefix)].isspace() or clean_line[len(prefix)] in (",", ";", ":", "-", "."):
                            return True
                return False

            topics_map = {}
            # Structure: (topic_name, candidate_type) -> { level_num -> [questions] }
            
            current_candidate_type = "Freshers"
            current_topic = "Frontend"
            current_level = 1
            
            last_question_key = None
            
            for line in filtered_lines:
                clean_for_header = re.sub(r'^[#*\-\s\+•]+', '', line).strip()
                
                # Detect candidate type
                if "part 1" in line.lower() and "freshers" in line.lower():
                    current_candidate_type = "Freshers"
                    continue
                elif "part 2" in line.lower() and "experienced" in line.lower():
                    current_candidate_type = "Experienced"
                    continue
                    
                # Detect topic header
                is_topic_hdr = False
                for vt in ["Frontend", "Backend (Java)", "Node.js"]:
                    if clean_for_header.lower() == vt.lower():
                        current_topic = vt
                        is_topic_hdr = True
                        break
                if is_topic_hdr:
                    continue
                    
                # Detect level header
                level_match = re.match(r'^level\s*([1-4])', clean_for_header, re.IGNORECASE)
                if level_match:
                    current_level = int(level_match.group(1))
                    continue
                    
                # Check if section label to ignore
                is_section_lbl = False
                for lbl in ["Easy & Fundamentals", "Resume & Project Based", "Production Based", "Advanced"]:
                    if clean_for_header.lower() == lbl.lower():
                        is_section_lbl = True
                        break
                if is_section_lbl:
                    continue
                    
                # Clean question text of leading numbers, bullet symbols
                question_text = re.sub(r'^\d+[\.\)]\s*', '', line)
                question_text = re.sub(r'^[#*\-\s\+•]+', '', question_text).strip()
                
                if not question_text:
                    continue
                    
                key = (current_topic, current_candidate_type, current_level)
                topic_key = (current_topic, current_candidate_type)
                
                # Determine if it starts a new question or merges
                starts_new = False
                starts_with_prefix = is_question_start_prefix(question_text)
                ends_with_qmark = question_text.endswith("?")
                
                if starts_with_prefix:
                    starts_new = True
                elif ends_with_qmark:
                    if topic_key in topics_map and current_level in topics_map[topic_key] and topics_map[topic_key][current_level]:
                        prev_q = topics_map[topic_key][current_level][-1]
                        if prev_q.endswith("?"):
                            starts_new = True
                    else:
                        starts_new = True
                        
                if starts_new or last_question_key is None or key != last_question_key:
                    if topic_key not in topics_map:
                        topics_map[topic_key] = {}
                    if current_level not in topics_map[topic_key]:
                        topics_map[topic_key][current_level] = []
                    topics_map[topic_key][current_level].append(question_text)
                    last_question_key = key
                else:
                    if topic_key in topics_map and current_level in topics_map[topic_key]:
                        prev_questions = topics_map[topic_key][current_level]
                        if prev_questions:
                            prev_questions[-1] = prev_questions[-1] + " " + question_text
                        else:
                            prev_questions.append(question_text)
                    else:
                        if topic_key not in topics_map:
                            topics_map[topic_key] = {}
                        if current_level not in topics_map[topic_key]:
                            topics_map[topic_key][current_level] = []
                        topics_map[topic_key][current_level].append(question_text)
                        last_question_key = key

            # Construct the output structure
            candidates_order = ["Freshers", "Experienced"]
            topics_order = ["Frontend", "Backend (Java)", "Node.js"]
            
            topics_detected_set = set()
            
            for cand in candidates_order:
                for top in topics_order:
                    key = (top, cand)
                    if key in topics_map:
                        levels_dict = topics_map[key]
                        levels_res = []
                        topic_question_count = 0
                        
                        for lvl_num in sorted(levels_dict.keys()):
                            q_list = levels_dict[lvl_num]
                            q_list_clean = [q.strip() for q in q_list if q.strip()]
                            if not q_list_clean:
                                continue
                            lvl_count = len(q_list_clean)
                            topic_question_count += lvl_count
                            levels_res.append({
                                "level": lvl_num,
                                "questionCount": lvl_count,
                                "questions": q_list_clean
                            })
                            
                        if topic_question_count > 0:
                            total_questions += topic_question_count
                            topics_detected_set.add(top)
                            topics_list.append({
                                "topicName": top,
                                "candidateType": cand,
                                "questionCount": topic_question_count,
                                "levels": levels_res
                            })
                            
            topics_detected = [t for t in topics_order if t in topics_detected_set]
            for t in topics_detected_set:
                if t not in topics_detected:
                    topics_detected.append(t)
                    
    except Exception as e:
        logger.error(f"Error parsing uploaded questions file: {e}")
        topics_detected = []
        total_questions = 0
        topics_list = []
        
    return JobProfileUploadResponse(
        success=True,
        original_file_name=file.filename or "",
        file_type=extension.replace(".", ""),
        file_size=size,
        uploaded_at=uploaded_at,
        topics_detected=topics_detected,
        total_questions=total_questions,
        topics=topics_list
    )

@router.post(
    path="/job-profiles/extract-skills",
    name="job-profiles:extract-skills",
    response_model=JobProfileExtractSkillsResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Extract skills from job description text",
)
async def extract_skills(
    payload: JobProfileExtractSkillsRequest,
    current_user=fastapi.Depends(get_current_user),
) -> JobProfileExtractSkillsResponse:
    """
    Extracts skills from the provided job description text.
    Currently uses keyword-based matching against a predefined skills list.
    """
    if not payload.job_description.strip():
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="job_description cannot be empty"
        )
        
    extracted_skills = extract_skills_from_text(payload.job_description)
    return JobProfileExtractSkillsResponse(skills=extracted_skills)


@router.post(
    path="/job-profiles/{job_profile_id}/questions/generate",
    name="job-profiles:generate-questions",
    response_model=JobProfileGenerateQuestionsResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Generate AI interview questions for a Job Profile",
)
async def generate_questions_v2(
    job_profile_id: int,
    payload: JobProfileGenerateQuestionsRequest,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileGenerateQuestionsResponse:
    # 1. Fetch job profile details & validate existence
    profile = await job_profile_repo.get_by_id(job_profile_id=job_profile_id)
    if profile is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Job profile with ID {job_profile_id} not found"
        )

    # 2. Level to difficulty map
    level_map = {
        1: "easy",
        2: "medium",
        3: "hard",
        4: "expert"
    }

    # 3. Validate levels and counts
    total_requested = 0
    for l in payload.levels:
        if l.level not in level_map:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid level {l.level}. Level must be 1, 2, 3, or 4."
            )
        if l.count < 0:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid count {l.count} for level {l.level}. Count cannot be negative."
            )
        total_requested += l.count

    if total_requested == 0:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="Total question count must be greater than zero."
        )

    # 4. Generate questions using existing LLM system
    generated_questions_data = []

    skills_list = profile.skills or []
    track = profile.job_name
    context_text = profile.job_description

    for l in payload.levels:
        if l.count == 0:
            continue

        difficulty = level_map[l.level]
        
        # Prepare syllabus and question ratio using existing syllabus service
        role = syllabus_service._role_manager.derive_role(track)
        topic_bank = syllabus_service.get_topics_for_role(role=role, difficulty=difficulty)
        
        topics = {
            "tech": topic_bank.tech,
            "tech_allied": topic_bank.tech_allied,
            "behavioral": topic_bank.behavioral,
            "archetypes": topic_bank.archetypes,
            "depth_guidelines": topic_bank.depth_guidelines,
        }
        
        # Extract tech-allied topics from job description
        topics["tech_allied"] = syllabus_service.extract_tech_allied_from_resume(
            resume_text=context_text,
            skills=skills_list,
            fallback_topics=topics.get("tech_allied", []),
        )
        
        question_ratio = syllabus_service.compute_question_ratio(
            years_experience=None,
            has_resume_text=bool(context_text),
            has_skills=bool(skills_list),
        )
        
        ratio = {
            "tech": question_ratio.tech,
            "tech_allied": question_ratio.tech_allied,
            "behavioral": question_ratio.behavioral,
        }
        
        influence = {
            "target_role": role,
            "difficulty": difficulty,
            "skills": skills_list,
            "experience_level": profile.experience_level,
        }
        if payload.knowledge_reference_context:
            influence["knowledge_reference_context"] = payload.knowledge_reference_context

        # Senior instruction: Make multiple smaller LLM calls to prevent large call failures (e.g. batch size of 5)
        remaining = l.count
        batch_size = 5
        level_generated_items = []

        while remaining > 0:
            current_batch = min(remaining, batch_size)
            current_influence = dict(influence)
            if level_generated_items:
                current_influence["exclude_questions"] = [
                    item["text"] for item in level_generated_items
                ]

            logger.info(
                f"Generating batch of {current_batch} questions (remaining: {remaining}) for Job Profile {job_profile_id} at Level {l.level} ({difficulty})"
            )

            questions_list, error, latency_ms, llm_model, structured_items = await generate_interview_questions_with_llm(
                track=track,
                context_text=context_text,
                count=current_batch,
                difficulty=difficulty,
                syllabus_topics=topics,
                ratio=ratio,
                influence=current_influence,
            )

            if error or not structured_items:
                logger.error(f"Failed to generate questions batch for Level {l.level}: {error}")
                raise fastapi.HTTPException(
                    status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to generate questions for Level {l.level}: {error or 'No questions generated'}"
                )

            level_generated_items.extend(structured_items)
            remaining -= current_batch

        for item in level_generated_items:
            generated_questions_data.append({
                "job_profile_id": profile.id,
                "question_text": item["text"],
                "level": l.level,
                "difficulty": difficulty,
                "question_type": item.get("category", "theoretical"),
                "is_ai_generated": True,
                "keywords": item.get("keywords") or [],
                "concepts_covered": item.get("concepts_covered") or [],
                "expected_answer": item.get("expected_answer"),
                "example_output": item.get("example_output"),
            })

    # 5. Save generated questions into database
    db_questions = await job_profile_repo.create_job_profile_questions(generated_questions_data)

    # 6. Format and return response
    response_items = [
        JobProfileGeneratedQuestionItem(
            question_id=str(q.id),
            question=q.question_text,
            level=q.level,
            difficulty=q.difficulty,
            type=q.question_type,
            is_ai_generated=q.is_ai_generated,
            keywords=getattr(q, "keywords", []) or [],
            concepts_covered=getattr(q, "concepts_covered", []) or [],
            expected_answer=getattr(q, "expected_answer", None),
            example_output=getattr(q, "example_output", None),
        )
        for q in db_questions
    ]

    return JobProfileGenerateQuestionsResponse(
        job_profile_id=str(profile.id),
        total_questions=len(response_items),
        questions=response_items
    )


@router.get(
    path="/job-profiles/{job_profile_id}/questions",
    name="job-profiles:get-questions",
    response_model=JobProfileQuestionsListResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get all questions (AI and custom) for a job profile",
)
async def get_job_profile_questions_v2(
    job_profile_id: int,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileQuestionsListResponse:
    # 1. Validate job_profile_id exists
    profile = await job_profile_repo.get_by_id(job_profile_id=job_profile_id)
    if not profile:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Job profile with ID {job_profile_id} not found",
        )

    # 2. Fetch all questions linked to job_profile_id
    db_questions = await job_profile_repo.get_job_profile_questions(job_profile_id=job_profile_id)

    # 3. Calculate level counts
    level_counts = {
        "level_1": 0,
        "level_2": 0,
        "level_3": 0,
        "level_4": 0,
    }
    for q in db_questions:
        if q.level == 1:
            level_counts["level_1"] += 1
        elif q.level == 2:
            level_counts["level_2"] += 1
        elif q.level == 3:
            level_counts["level_3"] += 1
        elif q.level == 4:
            level_counts["level_4"] += 1

    # 4. Map questions to response format
    questions_list = [
        JobProfileQuestionItem(
            question_id=str(q.id),
            question=q.question_text,
            level=q.level,
            difficulty=q.difficulty,
            type=q.question_type,
            is_ai_generated=q.is_ai_generated,
            created_at=q.created_at,
            keywords=getattr(q, "keywords", []) or [],
            concepts_covered=getattr(q, "concepts_covered", []) or [],
            expected_answer=getattr(q, "expected_answer", None),
            example_output=getattr(q, "example_output", None),
        )
        for q in db_questions
    ]

    return JobProfileQuestionsListResponse(
        job_profile_id=str(job_profile_id),
        total_questions=len(db_questions),
        level_counts=JobProfileQuestionLevelCounts(**level_counts),
        questions=questions_list,
    )


@router.post(
    path="/job-profiles/{job_profile_id}/questions",
    name="job-profiles:add-question",
    response_model=JobProfileAddQuestionResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Add a custom question to a job profile",
)
async def add_job_profile_question_v2(
    job_profile_id: int,
    payload: JobProfileAddQuestionRequest,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileAddQuestionResponse:
    # 1. Validate job_profile_id exists
    profile = await job_profile_repo.get_by_id(job_profile_id=job_profile_id)
    if not profile:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Job profile with ID {job_profile_id} not found",
        )

    # 2. Validate question text is not empty
    question_text = payload.question.strip() if payload.question else ""
    if not question_text:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="Question text cannot be empty",
        )

    # 3. Validate level is between 1 and 4
    if payload.level not in [1, 2, 3, 4]:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="Invalid level. Level must be 1, 2, 3, or 4.",
        )

    # 4. Save question in job_profile_question table
    db_question = await job_profile_repo.add_job_profile_question(
        job_profile_id=job_profile_id,
        question_text=question_text,
        level=payload.level,
        difficulty=payload.difficulty,
        question_type=payload.type,
        is_ai_generated=payload.is_ai_generated,
        keywords=payload.keywords or [],
        concepts_covered=payload.concepts_covered or [],
        expected_answer=payload.expected_answer,
        example_output=payload.example_output,
    )

    # 5. Return created question
    return JobProfileAddQuestionResponse(
        question_id=str(db_question.id),
        job_profile_id=str(job_profile_id),
        question=db_question.question_text,
        level=db_question.level,
        difficulty=db_question.difficulty,
        type=db_question.question_type,
        is_ai_generated=db_question.is_ai_generated,
        message="Question added successfully",
        keywords=getattr(db_question, "keywords", []) or [],
        concepts_covered=getattr(db_question, "concepts_covered", []) or [],
        expected_answer=getattr(db_question, "expected_answer", None),
        example_output=getattr(db_question, "example_output", None),
    )


@router.patch(
    path="/job-profile-questions/{question_id}",
    name="job-profiles:update-question",
    response_model=JobProfileUpdateQuestionResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Update a custom or AI question for a job profile",
)
async def update_job_profile_question_v2(
    question_id: int,
    payload: JobProfileUpdateQuestionRequest,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileUpdateQuestionResponse:
    # 1. Validate question_id exists
    question = await job_profile_repo.get_question_by_id(question_id=question_id)
    if not question:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Question with ID {question_id} not found",
        )

    # 2. Validate question text is not empty if provided
    update_data = {}
    if payload.question is not None:
        question_text = payload.question.strip()
        if not question_text:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail="Question text cannot be empty",
            )
        update_data["question_text"] = question_text

    # 3. Validate level is between 1 and 4 if provided
    if payload.level is not None:
        if payload.level not in [1, 2, 3, 4]:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail="Invalid level. Level must be 1, 2, 3, or 4.",
            )
        update_data["level"] = payload.level

    if payload.difficulty is not None:
        update_data["difficulty"] = payload.difficulty

    if payload.type is not None:
        update_data["question_type"] = payload.type

    if payload.keywords is not None:
        update_data["keywords"] = payload.keywords

    if payload.concepts_covered is not None:
        update_data["concepts_covered"] = payload.concepts_covered

    if payload.expected_answer is not None:
        update_data["expected_answer"] = payload.expected_answer

    if payload.example_output is not None:
        update_data["example_output"] = payload.example_output

    # 4. Save updated question
    updated_question = await job_profile_repo.update_job_profile_question(
        question=question,
        update_data=update_data
    )

    # 5. Return updated response
    return JobProfileUpdateQuestionResponse(
        question_id=str(updated_question.id),
        question=updated_question.question_text,
        level=updated_question.level,
        difficulty=updated_question.difficulty,
        type=updated_question.question_type,
        is_ai_generated=updated_question.is_ai_generated,
        message="Question updated successfully",
        keywords=getattr(updated_question, "keywords", []) or [],
        concepts_covered=getattr(updated_question, "concepts_covered", []) or [],
        expected_answer=getattr(updated_question, "expected_answer", None),
        example_output=getattr(updated_question, "example_output", None),
    )


@router.post(
    path="/job-profile-questions/{question_id}/regenerate",
    name="job-profiles:regenerate-question",
    response_model=JobProfileRegenerateQuestionResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Regenerate a single AI question for a job profile",
)
async def regenerate_job_profile_question_v2(
    question_id: int,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileRegenerateQuestionResponse:
    # 1. Validate question_id exists
    question = await job_profile_repo.get_question_by_id(question_id=question_id)
    if not question:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Question with ID {question_id} not found",
        )

    # 2. Fetch linked job profile
    profile = await job_profile_repo.get_by_id(job_profile_id=question.job_profile_id)
    if not profile:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Job profile for this question not found",
        )

    # 3. Extract and map configuration
    track = profile.job_name
    context_text = profile.job_description or ""
    skills_list = profile.skills or []

    level_map = {
        1: "easy",
        2: "medium",
        3: "hard",
        4: "expert"
    }
    difficulty = level_map.get(question.level, "easy")

    # 4. Prepare syllabus and ratio
    role = syllabus_service._role_manager.derive_role(track)
    topic_bank = syllabus_service.get_topics_for_role(role=role, difficulty=difficulty)

    topics = {
        "tech": topic_bank.tech,
        "tech_allied": topic_bank.tech_allied,
        "behavioral": topic_bank.behavioral,
        "archetypes": topic_bank.archetypes,
        "depth_guidelines": topic_bank.depth_guidelines,
    }
    topics["tech_allied"] = syllabus_service.extract_tech_allied_from_resume(
        resume_text=context_text,
        skills=skills_list,
        fallback_topics=topics.get("tech_allied", []),
    )

    question_ratio = syllabus_service.compute_question_ratio(
        years_experience=None,
        has_resume_text=bool(context_text),
        has_skills=bool(skills_list),
    )
    ratio = {
        "tech": question_ratio.tech,
        "tech_allied": question_ratio.tech_allied,
        "behavioral": question_ratio.behavioral,
    }

    influence = {
        "target_role": role,
        "difficulty": difficulty,
        "skills": skills_list,
        "experience_level": profile.experience_level,
    }

    # 5. Generate new question using existing LLM service
    questions_list, error, latency_ms, llm_model, structured_items = await generate_interview_questions_with_llm(
        track=track,
        context_text=context_text,
        count=1,
        difficulty=difficulty,
        syllabus_topics=topics,
        ratio=ratio,
        influence=influence,
    )

    if error or not structured_items:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to regenerate question: {error or 'No question returned from LLM'}"
        )

    new_item = structured_items[0]

    # 6. Replace and update old question text and detail fields
    update_data = {
        "question_text": new_item["text"],
        "question_type": new_item.get("category", "theoretical"),
        "is_ai_generated": True,
        "keywords": new_item.get("keywords") or [],
        "concepts_covered": new_item.get("concepts_covered") or [],
        "expected_answer": new_item.get("expected_answer"),
        "example_output": new_item.get("example_output"),
    }
    updated_question = await job_profile_repo.update_job_profile_question(
        question=question,
        update_data=update_data
    )

    # 7. Return updated response
    return JobProfileRegenerateQuestionResponse(
        question_id=str(updated_question.id),
        question=updated_question.question_text,
        level=updated_question.level,
        difficulty=updated_question.difficulty,
        type=updated_question.question_type,
        is_ai_generated=updated_question.is_ai_generated,
        message="Question regenerated successfully",
        keywords=getattr(updated_question, "keywords", []) or [],
        concepts_covered=getattr(updated_question, "concepts_covered", []) or [],
        expected_answer=getattr(updated_question, "expected_answer", None),
        example_output=getattr(updated_question, "example_output", None),
    )


@router.delete(
    path="/job-profile-questions/{question_id}",
    name="job-profiles:delete-question",
    response_model=JobProfileDeleteQuestionResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Delete a single question for a job profile",
)
async def delete_job_profile_question_v2(
    question_id: int,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileDeleteQuestionResponse:
    # 1. Validate question_id exists
    question = await job_profile_repo.get_question_by_id(question_id=question_id)
    if not question:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Question with ID {question_id} not found",
        )

    # 2. Delete the question
    await job_profile_repo.delete_job_profile_question(question=question)

    # 3. Return success response
    return JobProfileDeleteQuestionResponse(
        message="Question deleted successfully",
        question_id=str(question_id),
    )







