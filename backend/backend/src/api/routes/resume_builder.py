from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession
import io

# Core architectural dependency inject hooks matched to your layout
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session

from src.models.db.user import User
from src.repository.crud.resume_builder import ResumeBuilderRepository
from src.services.resume.static_templates import (
    DEFAULT_TEMPLATE_METADATA, DEFAULT_TEMPLATE_DETAIL
)
from src.models.schemas.resume_builder import (
    TemplateCompactResponse, TemplateDetailResponse,
    CreateResumeFromTemplateRequest, UpdateResumeDataRequest, ResumeInstanceResponse
)
from src.repository.crud.ai_resume_analysis import AIResumeAnalysisCRUDRepository
from src.services.ai_resume.template_service import generate_structured_resume_data

router = APIRouter(prefix="/resume-builder", tags=["Resume Builder V2"])

@router.get("/templates", response_model=list[TemplateCompactResponse])
async def get_all_templates(current_user: User = Depends(get_current_user)):
    return [DEFAULT_TEMPLATE_METADATA]

@router.get("/templates/{template_id}", response_model=TemplateDetailResponse)
async def get_template_detail(template_id: str, current_user: User = Depends(get_current_user)):
    if template_id != DEFAULT_TEMPLATE_METADATA["templateId"]:
        raise HTTPException(status_code=404, detail="Template context specification not matching active baseline.")
    return DEFAULT_TEMPLATE_DETAIL

@router.post("/from-template", response_model=ResumeInstanceResponse)
async def create_resume_from_template(
    payload: CreateResumeFromTemplateRequest,
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user)
):
    if payload.templateId != DEFAULT_TEMPLATE_METADATA["templateId"]:
        raise HTTPException(status_code=400, detail="Invalid Template target pointer configuration.")
        
    repo = ResumeBuilderRepository(session)
    initial_boilerplate = DEFAULT_TEMPLATE_DETAIL["sampleData"]
    
    if payload.analysisId:
        analysis_repo = AIResumeAnalysisCRUDRepository(session)
        analysis_record = await analysis_repo.get_by_analysis_id(payload.analysisId)
        if analysis_record and analysis_record.user_id == current_user.id:
            structured_data = await generate_structured_resume_data(
                resume_text=analysis_record.extracted_resume_text,
                analysis_result=analysis_record.analysis_result,
            )
            initial_boilerplate = structured_data
    
    new_resume = await repo.create_resume_instance(
        user_id=current_user.id,
        template_id=payload.templateId,
        initial_data=initial_boilerplate
    )
    
    return ResumeInstanceResponse(
        resumeId=new_resume.id,
        userId=new_resume.user_id,
        templateId=new_resume.template_id,
        data=new_resume.resume_data,
        status=new_resume.status,
        updatedAt=new_resume.updated_at
    )

@router.get("/{resume_id}", response_model=ResumeInstanceResponse)
async def get_resume_draft(
    resume_id: int,
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user)
):
    repo = ResumeBuilderRepository(session)
    resume = await repo.get_resume_by_id_and_user(resume_id, current_user.id)
    if not resume:
        raise HTTPException(status_code=404, detail="Target entity dataset missing from record tracking parameters.")
    return ResumeInstanceResponse(
        resumeId=resume.id,
        userId=resume.user_id,
        templateId=resume.template_id,
        data=resume.resume_data,
        status=resume.status,
        updatedAt=resume.updated_at
    )

@router.put("/{resume_id}")
async def update_resume_content(
    resume_id: int,
    payload: UpdateResumeDataRequest,
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user)
):
    repo = ResumeBuilderRepository(session)
    clean_json_payload = payload.data.dict()
    
    updated_resume = await repo.update_resume_data(resume_id, current_user.id, clean_json_payload)
    if not updated_resume:
        raise HTTPException(status_code=404, detail="Update transaction denied or execution failure.")
    return {"resumeId": updated_resume.id, "status": "UPDATED"}

@router.get("/{resume_id}/download")
async def download_resume_pdf(
    resume_id: int,
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user)
):
    repo = ResumeBuilderRepository(session)
    resume = await repo.get_resume_by_id_and_user(resume_id, current_user.id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume targeted dataset reference point not located.")

    try:
        data = resume.resume_data
        header = data.get('header', {})
        
        contact_items = []
        if header.get('email'): contact_items.append(header.get('email'))
        if header.get('phone'): contact_items.append(header.get('phone'))
        if header.get('location'): contact_items.append(header.get('location'))
        if header.get('linkedin'): contact_items.append(header.get('linkedin'))
        if header.get('github'): contact_items.append(header.get('github'))
        contact_str = " &bull; ".join(contact_items)

        html_content = f"""
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                @page {{ size: A4; margin: 0.6in; }}
                body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #111111; line-height: 1.4; font-size: 10pt; }}
                .center {{ text-align: center; }}
                .name {{ font-size: 22pt; font-weight: bold; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 2px; }}
                .title {{ font-size: 11pt; color: #555555; margin-bottom: 4px; }}
                .contact-line {{ font-size: 9pt; color: #444444; margin-bottom: 15px; }}
                .section-header {{ font-size: 11pt; font-weight: bold; text-transform: uppercase; border-bottom: 1px solid #cccccc; margin-top: 15px; margin-bottom: 6px; letter-spacing: 0.5px; padding-bottom: 2px; }}
                .item-title {{ font-weight: bold; font-size: 10pt; display: inline-block; }}
                .item-subtitle {{ font-style: italic; font-size: 10pt; color: #333333; margin-bottom: 2px; }}
                .flex-row {{ margin-bottom: 2px; width: 100%; }}
                .right {{ float: right; font-weight: normal; font-style: normal; font-size: 9pt; color: #555555; }}
                ul {{ margin: 3px 0 8px 18px; padding: 0; }}
                li {{ margin-bottom: 2px; text-align: justify; font-size: 10pt; }}
                .skills-container {{ margin-top: 4px; font-size: 10pt; line-height: 1.5; }}
                p {{ font-size: 10pt; margin: 0; text-align: justify; }}
            </style>
        </head>
        <body>
            <div class="center">
                <div class="name">{header.get('fullName', header.get('name', 'Your Name'))}</div>
                {"<div class='title'>" + header.get('title') + "</div>" if header.get('title') else ""}
                <div class="contact-line">
                    {contact_str}
                </div>
            </div>

            {"<div class='section-header'>Career Objective</div><p style='margin-top: 4px;'>" + data.get('summary') + "</p>" if data.get('summary') else ''}
        """

        if data.get('experience'):
            html_content += "<div class='section-header'>Work Experience</div>"
            for exp in data['experience']:
                bullets = exp.get('highlights') or exp.get('bullets') or []
                highlights_li = "".join([f"<li>{item}</li>" for item in bullets if item])
                html_content += f"""
                <div class="flex-row">
                    <span class="right">{exp.get('duration', '')}</span>
                    <span class="item-title">{exp.get('role', exp.get('title', ''))}</span>
                </div>
                {"<div class='item-subtitle'>" + exp.get('company') + "</div>" if exp.get('company') else ""}
                {"<ul>" + highlights_li + "</ul>" if highlights_li else ""}
                """

        if data.get('projects'):
            html_content += "<div class='section-header'>Projects</div>"
            for proj in data['projects']:
                bullets = proj.get('highlights') or proj.get('bullets') or []
                highlights_li = "".join([f"<li>{item}</li>" for item in bullets if item])
                
                links_html = ""
                if proj.get('github_link') or proj.get('hosted_link'):
                    links_parts = []
                    if proj.get('github_link'):
                        links_parts.append(f"<a href='{proj.get('github_link')}' style='color: #1a73e8; text-decoration: none;'>GitHub</a>")
                    if proj.get('hosted_link'):
                        links_parts.append(f"<a href='{proj.get('hosted_link')}' style='color: #1a73e8; text-decoration: none;'>Live Project</a>")
                    links_html = f"<div class='item-subtitle' style='margin-top: 1px; margin-bottom: 3px;'>{' | '.join(links_parts)}</div>"

                html_content += f"""
                <div class="flex-row">
                    <span class="right">{proj.get('duration', '')}</span>
                    <span class="item-title">{proj.get('title', '')}</span>
                </div>
                {links_html}
                {"<ul>" + highlights_li + "</ul>" if highlights_li else ("<p style='margin-top: 4px;'>" + proj.get('description', '') + "</p>" if proj.get('description') else "")}
                """

        if data.get('skills'):
            html_content += "<div class='section-header'>Skills</div>"
            html_content += f"""
            <div class="skills-container">
                {", ".join(data.get('skills', []))}
            </div>
            """

        if data.get('education'):
            html_content += "<div class='section-header'>Education</div>"
            for edu in data['education']:
                html_content += f"""
                <div class="flex-row">
                    <span class="right">{edu.get('institution', '')}</span>
                    <span class="item-title">{edu.get('degree', '')}</span>
                </div>
                <div style="font-size: 9pt; color: #555555; margin-bottom: 6px;">{edu.get('year', edu.get('duration', ''))}</div>
                """

        html_content += "</body></html>"
        
        from xhtml2pdf import pisa
        pdf_buffer = io.BytesIO()
        pisa_status = pisa.CreatePDF(io.StringIO(html_content), dest=pdf_buffer)
        
        if pisa_status.err:
            raise HTTPException(status_code=500, detail="PDF engine failure processing structured markup text stream.")
            
        pdf_buffer.seek(0)
        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=SamvaadSathi_Resume_{resume_id}.pdf"}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Document transmission process exception intercept: {str(e)}")