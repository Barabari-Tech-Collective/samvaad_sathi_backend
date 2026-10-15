from io import BytesIO
import json
import zipfile
from docx import Document
from fastapi import HTTPException, UploadFile
import fitz  # PyMuPDF

from src.config.manager import settings

ALLOWED_FILE_TYPES = [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]

MAX_FILE_SIZE_MB = 10


async def validate_resume_file(file: UploadFile):
    if file.content_type not in ALLOWED_FILE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Only PDF and DOCX files are allowed",
        )

    contents = await file.read()
    file_size_mb = len(contents) / (1024 * 1024)

    if file_size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"File size exceeds {MAX_FILE_SIZE_MB} MB limit",
        )

    await file.seek(0)


from typing import Any, Union


async def extract_resume_text(file: UploadFile) -> Union[str, dict]:
    await validate_resume_file(file)
    filename = (file.filename or "").lower()

    try:
        if filename.endswith(".pdf"):
            return await extract_pdf_text_spatial(file)
        elif filename.endswith(".docx"):
            return await extract_docx_text(file)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file format")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Resume parsing failed: {str(e)}",
        )


async def extract_pdf_text_spatial(file: UploadFile) -> dict:
    """
    Spatial PDF Parser using PyMuPDF Bounding Boxes.
    Extracts text blocks and maps clickable link rects directly to overlapping text spans,
    inlining URLs into their exact sentence positions.
    """
    try:
        contents = await file.read()
        pdf_document = fitz.open(stream=contents, filetype="pdf")

        if pdf_document.needs_pass:
            pdf_document.close()
            raise HTTPException(
                status_code=400,
                detail="The PDF is password-protected. Upload an unlocked copy for ATS processing.",
            )

        reconstructed_lines = []
        document_map = []
        all_unique_urls = set()

        for page_num in range(len(pdf_document)):
            page = pdf_document.load_page(page_num)

            # 1. Fetch raw page dict (Blocks -> Lines -> Spans with bbox)
            page_dict = page.get_text("dict")

            # 2. Fetch interactive PDF annotations (links with bounding rects)
            page_links = page.get_links()
            valid_links = []
            for link in page_links:
                if "uri" in link and link["uri"].strip():
                    valid_links.append(
                        {
                            "uri": link["uri"].strip(),
                            "rect": fitz.Rect(link["from"]),
                        }
                    )
                    all_unique_urls.add(link["uri"].strip())

            # 3. Iterate through text blocks spatially
            blocks = page_dict.get("blocks", [])
            for b in blocks:
                if b.get("type") == 0:  # Text block
                    for line in b.get("lines", []):
                        line_text_parts = []
                        line_bbox = line.get("bbox")

                        for span in line.get("spans", []):
                            span_text = span.get("text", "")
                            span_rect = fitz.Rect(span.get("bbox"))

                            matched_url = None
                            # Check spatial intersection between link annotation rect and text span rect
                            for l in valid_links:
                                # Overlap check: intersect or containment
                                if (
                                    span_rect.intersects(l["rect"])
                                    or l["rect"].contains(span_rect)
                                    or span_rect.contains(l["rect"])
                                ):
                                    matched_url = l["uri"]
                                    break

                            if matched_url and matched_url not in span_text:
                                # Inline attachment directly at text placement
                                line_text_parts.append(f"{span_text} [{matched_url}]")
                            else:
                                line_text_parts.append(span_text)

                        full_line = " ".join(line_text_parts).strip()
                        if full_line:
                            reconstructed_lines.append(full_line)

                            # Record in spatial document map
                            document_map.append(
                                {
                                    "page": page_num + 1,
                                    "text": full_line,
                                    "bbox": line_bbox,
                                    "y_top": line_bbox[1] if line_bbox else 0,
                                }
                            )

        try:
            raw_text_output = "\n".join(reconstructed_lines)

            # Fallback to standard text extraction if spatial dict fails (e.g. Canva resumes)
            if not raw_text_output.strip():
                fallback_text = []
                for page_num in range(len(pdf_document)):
                    fallback_text.append(
                        pdf_document.load_page(page_num).get_text("text")
                    )
                raw_text_output = "\n".join(fallback_text)

            # Optional OCR for image-only PDFs. PyMuPDF delegates to the
            # system Tesseract installation; deployments opt in explicitly.
            if not raw_text_output.strip() and settings.BULK_ATS_OCR_ENABLED:
                ocr_text = []
                for page_num in range(len(pdf_document)):
                    page = pdf_document.load_page(page_num)
                    text_page = page.get_textpage_ocr(
                        language=settings.BULK_ATS_OCR_LANGUAGE,
                        dpi=settings.BULK_ATS_OCR_DPI,
                        full=True,
                    )
                    ocr_text.append(page.get_text("text", textpage=text_page))
                raw_text_output = "\n".join(ocr_text)

        finally:
            pdf_document.close()
            await file.seek(0)

        if not raw_text_output.strip():
            ocr_hint = (
                "OCR was enabled but could not recover text; review the original manually."
                if settings.BULK_ATS_OCR_ENABLED
                else "Enable BULK_ATS_OCR_ENABLED with Tesseract installed, or review the original manually."
            )
            raise HTTPException(
                status_code=400,
                detail=f"No readable text found in this image-only or malformed PDF. {ocr_hint}",
            )

        # Append structured spatial metadata block at bottom for downstream parser contexts
        if all_unique_urls:
            raw_text_output += "\n\n----- SPATIALLY VERIFIED EMBEDDED LINKS -----\n"
            raw_text_output += "\n".join(list(all_unique_urls))

        return {
            "text": raw_text_output.strip(),
            "documentMap": document_map,
            "embeddedLinks": list(all_unique_urls),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Spatial PDF parsing failed: {str(e)}"
        )


async def extract_docx_text(file: UploadFile) -> str:
    """
    Extract text from DOCX using python-docx.
    """
    try:
        contents = await file.read()
        try:
            with zipfile.ZipFile(BytesIO(contents)) as archive:
                infos = archive.infolist()
                if "word/document.xml" not in {info.filename for info in infos}:
                    raise HTTPException(
                        status_code=400,
                        detail="Malformed DOCX: document.xml is missing",
                    )
                max_uncompressed = (
                    settings.BULK_ATS_DOCX_MAX_UNCOMPRESSED_MB * 1024 * 1024
                )
                if (
                    len(infos) > 5000
                    or sum(info.file_size for info in infos) > max_uncompressed
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="DOCX expands beyond the safe processing limit",
                    )
                if any(info.flag_bits & 0x1 for info in infos):
                    raise HTTPException(
                        status_code=400, detail="Encrypted DOCX files are not supported"
                    )
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Malformed DOCX file")
        docx_file = BytesIO(contents)
        document = Document(docx_file)

        extracted_text = ""
        for paragraph in document.paragraphs:
            extracted_text += paragraph.text + "\n"

        await file.seek(0)

        if not extracted_text.strip():
            raise HTTPException(
                status_code=400,
                detail="No text found in DOCX resume",
            )

        return extracted_text.strip()
        # return {
        #     "text": extracted_text.strip(),
        #     "documentMap": document_map,
        #     "embeddedLinks": list(all_unique_urls)
        # }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"DOCX parsing failed: {str(e)}",
        )
