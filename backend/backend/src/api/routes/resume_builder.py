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
        
        html_content = f"""
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                @page {{ size: letter; margin: 0.6in; }}
                body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #111111; line-height: 1.4; font-size: 11pt; }}
                .center {{ text-align: center; }}
                .name {{ font-size: 22pt; font-weight: bold; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 2px; }}
                .contact-line {{ font-size: 10pt; color: #444444; margin-bottom: 15px; }}
                .section-header {{ font-size: 12pt; font-weight: bold; text-transform: uppercase; border-bottom: 1px solid #222222; margin-top: 18px; margin-bottom: 6px; letter-spacing: 0.5px; }}
                .item-title {{ font-weight: bold; font-size: 11pt; }}
                .item-subtitle {{ font-style: italic; color: #333333; }}
                .flex-row {{ margin-bottom: 2px; }}
                .right {{ float: right; font-weight: normal; font-style: normal; font-size: 10pt; color: #444444; }}
                ul {{ margin: 4px 0 8px 18px; padding: 0; }}
                li {{ margin-bottom: 3px; text-align: justify; }}
                .skills-container {{ margin-top: 5px; }}
            </style>
        </head>
        <body>
            <div class="center">
                <div class="name">{header.get('fullName', 'Your Name')}</div>
                <div class="contact-line">
                    {header.get('email', '')} &bull; {header.get('phone', '')}
                    {f" &bull; <a href='https://{header.get('linkedin') or ''}'>LinkedIn</a>" if header.get('linkedin') else ''}
                    {f" &bull; <a href='https://{header.get('github') or ''}'>GitHub</a>" if header.get('github') else ''}
                </div>
            </div>

            {"<div class='section-header'>Professional Summary</div><p style='margin: 0; text-align: justify;'>" + data.get('summary') + "</p>" if data.get('summary') else ''}

            <div class="section-header">Skills</div>
            <div class="skills-container">
                <strong>Technical Capabilities:</strong> {", ".join(data.get('skills', []))}
            </div>
        </div>
        """

        if data.get('experience'):
            html_content += "<div class='section-header'>Professional Experience</div>"
            for exp in data['experience']:
                highlights_li = "".join([f"<li>{item}</li>" for item in exp.get('highlights', []) if item])
                html_content += f"""
                <div class="flex-row">
                    <span class="right">{exp.get('duration', '')}</span>
                    <span class="item-title">{exp.get('company', '')}</span>
                </div>
                <div class="item-subtitle" style="margin-bottom: 4px;">{exp.get('role', '')}</div>
                <ul>{highlights_li}</ul>
                """

        if data.get('projects'):
            html_content += "<div class='section-header'>Key Projects</div>"
            for proj in data['projects']:
                html_content += f"""
                <div class="flex-row">
                    <span class="item-title">{proj.get('title', '')}</span>
                </div>
                <p style="margin: 2px 0 8px 0; text-align: justify;">{proj.get('description', '')}</p>
                """

        if data.get('education'):
            html_content += "<div class='section-header'>Education</div>"
            for edu in data['education']:
                html_content += f"""
                <div class="flex-row">
                    <span class="right">{edu.get('year', '')}</span>
                    <span class="item-title">{edu.get('institution', '')}</span>
                </div>
                <div class="item-subtitle">{edu.get('degree', '')}</div>
                <div style="height: 5px;"></div>
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