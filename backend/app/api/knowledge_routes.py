"""Administrator-only TXT knowledge storage and browsing endpoints."""

from hashlib import sha256
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.exc import IntegrityError

from ..auth import AdminUserDependency, AuthRepositoryDependency, request_ip
from ..knowledge import (
    MAX_TXT_BYTES,
    InvalidTextFile,
    KnowledgeRepositoryDependency,
    clean_uploaded_filename,
    decode_stored_txt,
    detect_txt_encoding,
)
from ..models import KnowledgeDocument
from ..schemas import (
    KnowledgeDocumentContentResponse,
    KnowledgeDocumentListResponse,
    KnowledgeDocumentResponse,
)

router = APIRouter(prefix="/api/v1/admin/knowledge", tags=["admin-knowledge"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="知识库资料不存在")


@router.get("/documents", response_model=KnowledgeDocumentListResponse)
async def list_documents(
    _admin: AdminUserDependency,
    repository: KnowledgeRepositoryDependency,
    search: str = Query(default="", max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
) -> KnowledgeDocumentListResponse:
    documents, total = await repository.list_documents(
        search=search.strip(),
        offset=offset,
        limit=limit,
    )
    return KnowledgeDocumentListResponse(
        items=[KnowledgeDocumentResponse.model_validate(document) for document in documents],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/documents",
    response_model=KnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    request: Request,
    admin: AdminUserDependency,
    auth_repository: AuthRepositoryDependency,
    repository: KnowledgeRepositoryDependency,
    file: Annotated[UploadFile, File(description="TXT 原文件")],
    title: Annotated[str | None, Form()] = None,
) -> KnowledgeDocumentResponse:
    try:
        original_filename = clean_uploaded_filename(file.filename)
        data = await file.read(MAX_TXT_BYTES + 1)
    except InvalidTextFile as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()

    if len(data) > MAX_TXT_BYTES:
        raise HTTPException(status_code=413, detail="TXT 文件不能超过 10MB")

    try:
        encoding, _decoded = detect_txt_encoding(data)
    except InvalidTextFile as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    document_title = (title or original_filename[:-4]).strip()
    if not document_title:
        raise HTTPException(status_code=422, detail="书名不能为空")
    if len(document_title) > 200:
        raise HTTPException(status_code=422, detail="书名不能超过 200 个字符")

    digest = sha256(data).hexdigest()
    existing = await repository.get_by_sha256(digest)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"该文件已上传：{existing.title}")

    document = KnowledgeDocument(
        title=document_title,
        original_filename=original_filename,
        encoding=encoding,
        byte_size=len(data),
        sha256=digest,
        file_data=data,
    )
    try:
        await repository.add(document)
        await auth_repository.add_audit_log(
            actor_user_id=admin.id,
            target_user_id=None,
            action="admin.knowledge_uploaded",
            details={"document_id": str(document.id), "byte_size": document.byte_size},
            ip_address=request_ip(request),
        )
    except IntegrityError as exc:
        await repository.session.rollback()
        raise HTTPException(status_code=409, detail="该文件已经上传") from exc
    await repository.session.refresh(document)
    return KnowledgeDocumentResponse.model_validate(document)


@router.get(
    "/documents/{document_id}/content",
    response_model=KnowledgeDocumentContentResponse,
)
async def get_document_content(
    document_id: UUID,
    _admin: AdminUserDependency,
    repository: KnowledgeRepositoryDependency,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50_000, ge=1, le=100_000),
) -> KnowledgeDocumentContentResponse:
    document = await repository.get_document(document_id, include_data=True)
    if document is None:
        raise _not_found()
    try:
        text = decode_stored_txt(document.file_data, document.encoding)
    except InvalidTextFile as exc:
        raise HTTPException(status_code=500, detail="存储的 TXT 文件无法读取") from exc
    content = text[offset : offset + limit]
    return KnowledgeDocumentContentResponse(
        document_id=document.id,
        content=content,
        offset=offset,
        limit=limit,
        next_offset=offset + len(content),
        total_characters=len(text),
        has_more=offset + len(content) < len(text),
    )


@router.get("/documents/{document_id}/download")
async def download_document(
    document_id: UUID,
    _admin: AdminUserDependency,
    repository: KnowledgeRepositoryDependency,
) -> Response:
    document = await repository.get_document(document_id, include_data=True)
    if document is None:
        raise _not_found()
    encoded_filename = quote(document.original_filename, safe="")
    return Response(
        content=document.file_data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    request: Request,
    admin: AdminUserDependency,
    auth_repository: AuthRepositoryDependency,
    repository: KnowledgeRepositoryDependency,
) -> Response:
    document = await repository.get_document(document_id)
    if document is None:
        raise _not_found()
    await repository.delete(document)
    await auth_repository.add_audit_log(
        actor_user_id=admin.id,
        target_user_id=None,
        action="admin.knowledge_deleted",
        details={"document_id": str(document.id), "sha256": document.sha256},
        ip_address=request_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
