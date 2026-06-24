import fitz #pymupdf
from docx import Document
from fastapi import UploadFile, HTTPException
from io import BytesIO


# Allowed MIME types
ALLOWED_FILE_TYPES = [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]

# Max file size (5 MB example)
MAX_FILE_SIZE_MB = 5


async def validate_resume_file(file: UploadFile):
    """
    Validate uploaded resume file.

    Checks:
    1. File type
    2. File size
    """

    # Validate content type
    if file.content_type not in ALLOWED_FILE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Only PDF and DOCX files are allowed",
        )

    # Read file bytes temporarily
    contents = await file.read()

    # Validate file size
    file_size_mb = len(contents) / (1024 * 1024)

    if file_size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"File size exceeds {MAX_FILE_SIZE_MB} MB limit",
        )

    # IMPORTANT:
    # Reset pointer after reading
    # otherwise future reads become empty
    await file.seek(0)


async def extract_resume_text(file: UploadFile) -> str:
    """
    Main resume text extraction function.

    Detects file type and routes
    to proper parser.
    """

    await validate_resume_file(file)

    filename = file.filename.lower()

    try:
        # PDF parsing
        if filename.endswith(".pdf"):
            return await extract_pdf_text(file)

        # DOCX parsing
        elif filename.endswith(".docx"):
            return await extract_docx_text(file)

        else:
            raise HTTPException(
                status_code=400,
                detail="Unsupported file format",
            )

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Resume parsing failed: {str(e)}",
        )


async def extract_pdf_text(file: UploadFile) -> str:
    """
    Extract text from PDF using PyMuPDF.
    """

    try:
        # Read uploaded file bytes
        contents = await file.read()

        # Open PDF from memory
        pdf_document = fitz.open(
            stream=contents,
            filetype="pdf",
        )

        extracted_text = ""

        # Loop through all pages
        for page_number in range(len(pdf_document)):
            page = pdf_document.load_page(page_number)

            # Extract text from page
            extracted_text += page.get_text()
            
            # Extract hidden hyperlinks (annotations) from the page
            for link in page.get_links():
                if "uri" in link:
                    extracted_text += " " + link["uri"] + " "

        pdf_document.close()

        # Reset file pointer
        await file.seek(0)

        if not extracted_text.strip():
            raise HTTPException(
                status_code=400,
                detail="No text found in PDF resume",
            )

        return extracted_text.strip()

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"PDF parsing failed: {str(e)}",
        )


async def extract_docx_text(file: UploadFile) -> str:
    """
    Extract text from DOCX using python-docx.
    """

    try:
        # Read uploaded bytes
        contents = await file.read()

        # Create in-memory file
        docx_file = BytesIO(contents)

        # Load document
        document = Document(docx_file)

        extracted_text = ""

        # Extract paragraphs
        for paragraph in document.paragraphs:
            extracted_text += paragraph.text + "\n"

        # Reset file pointer
        await file.seek(0)

        if not extracted_text.strip():
            raise HTTPException(
                status_code=400,
                detail="No text found in DOCX resume",
            )

        return extracted_text.strip()

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"DOCX parsing failed: {str(e)}",
        )