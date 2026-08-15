"use client";

import {
  AlertCircle,
  BookOpenText,
  CheckCircle2,
  Download,
  FileText,
  LibraryBig,
  LoaderCircle,
  RefreshCcw,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { DragEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { AdminShell } from "@/components/admin-shell";
import { useAdminGuard } from "@/hooks/use-admin-guard";
import {
  deleteKnowledgeDocument,
  downloadKnowledgeDocument,
  getKnowledgeDocumentContent,
  listKnowledgeDocuments,
  uploadKnowledgeDocument,
} from "@/lib/api";
import type { KnowledgeDocument } from "@/lib/types";

const CONTENT_PAGE_SIZE = 50_000;
const MAX_FILE_SIZE = 10 * 1024 * 1024;

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function titleFromFilename(filename: string): string {
  return filename.replace(/\.txt$/i, "");
}

export function AdminKnowledge() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { admin, isLoading: isGuardLoading, error: guardError } = useAdminGuard();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [content, setContent] = useState("");
  const [nextOffset, setNextOffset] = useState(0);
  const [totalCharacters, setTotalCharacters] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [search, setSearch] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [isContentLoading, setIsContentLoading] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selectedDocument = useMemo(
    () => documents.find((document) => document.id === selectedId) ?? null,
    [documents, selectedId],
  );
  const visibleBytes = useMemo(
    () => documents.reduce((sum, document) => sum + document.byte_size, 0),
    [documents],
  );

  async function refreshDocuments(searchTerm: string, preferredId = "") {
    const response = await listKnowledgeDocuments(searchTerm);
    setDocuments(response.items);
    setTotal(response.total);
    setSelectedId((current) => {
      if (preferredId && response.items.some((item) => item.id === preferredId)) {
        return preferredId;
      }
      if (response.items.some((item) => item.id === current)) return current;
      return response.items[0]?.id ?? "";
    });
  }

  useEffect(() => {
    if (!admin) return () => undefined;
    let active = true;
    async function load() {
      try {
        const response = await listKnowledgeDocuments();
        if (!active) return;
        setDocuments(response.items);
        setTotal(response.total);
        setSelectedId(response.items[0]?.id ?? "");
      } catch (requestError) {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : "无法读取知识库。");
        }
      } finally {
        if (active) setIsLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [admin]);

  useEffect(() => {
    let active = true;
    setContent("");
    setNextOffset(0);
    setTotalCharacters(0);
    setHasMore(false);
    if (!selectedId) return () => undefined;

    async function loadContent() {
      setIsContentLoading(true);
      try {
        const response = await getKnowledgeDocumentContent(
          selectedId,
          0,
          CONTENT_PAGE_SIZE,
        );
        if (!active) return;
        setContent(response.content);
        setNextOffset(response.next_offset);
        setTotalCharacters(response.total_characters);
        setHasMore(response.has_more);
      } catch (requestError) {
        if (active) {
          setError(requestError instanceof Error ? requestError.message : "正文读取失败。");
        }
      } finally {
        if (active) setIsContentLoading(false);
      }
    }
    void loadContent();
    return () => {
      active = false;
    };
  }, [selectedId]);

  function chooseFile(file: File | null) {
    setError("");
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".txt")) {
      setError("只支持上传 TXT 文件。");
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      setError("TXT 文件不能超过 10MB。");
      return;
    }
    setSelectedFile(file);
    setTitle(titleFromFilename(file.name));
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragging(false);
    chooseFile(event.dataTransfer.files[0] ?? null);
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (!selectedFile) {
      setError("请先选择一个 TXT 文件。");
      return;
    }
    if (!title.trim()) {
      setError("请填写书名。");
      return;
    }
    setIsUploading(true);
    try {
      const created = await uploadKnowledgeDocument(selectedFile, title);
      setSelectedFile(null);
      setTitle("");
      setSearch("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      await refreshDocuments("", created.id);
      setNotice(`《${created.title}》已保存到数据库。`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "TXT 上传失败。");
    } finally {
      setIsUploading(false);
    }
  }

  async function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      await refreshDocuments(search);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "搜索失败。");
    }
  }

  async function handleLoadMore() {
    if (!selectedId || !hasMore || isContentLoading) return;
    setIsContentLoading(true);
    setError("");
    try {
      const response = await getKnowledgeDocumentContent(
        selectedId,
        nextOffset,
        CONTENT_PAGE_SIZE,
      );
      setContent((current) => current + response.content);
      setNextOffset(response.next_offset);
      setTotalCharacters(response.total_characters);
      setHasMore(response.has_more);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "正文读取失败。");
    } finally {
      setIsContentLoading(false);
    }
  }

  async function handleDownload() {
    if (!selectedDocument) return;
    setError("");
    try {
      await downloadKnowledgeDocument(selectedDocument.id, selectedDocument.original_filename);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "文件下载失败。");
    }
  }

  async function handleDelete() {
    if (
      !selectedDocument
      || !window.confirm(`永久删除《${selectedDocument.title}》及其原文件？此操作无法撤销。`)
    ) return;
    setIsDeleting(true);
    setError("");
    setNotice("");
    try {
      await deleteKnowledgeDocument(selectedDocument.id);
      await refreshDocuments(search);
      setNotice(`《${selectedDocument.title}》已删除。`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "资料删除失败。");
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <AdminShell
      admin={admin}
      isLoading={isGuardLoading || isLoading}
      loadingText="正在读取知识库"
      error={guardError}
      pageClassName="knowledge-page"
      layoutClassName="knowledge-layout"
    >
        <section className="admin-heading knowledge-heading">
          <div><p className="eyebrow">KNOWLEDGE STORAGE</p><h1>知识库</h1></div>
          <span>{total} 份资料 · 当前列表 {formatFileSize(visibleBytes)}</span>
        </section>

        <section className="knowledge-upload-card" aria-labelledby="knowledge-upload-title">
          <div className="knowledge-card-title">
            <Upload size={19} />
            <div><h2 id="knowledge-upload-title">上传 TXT</h2><p>原始文件直接保存到数据库，单个文件最大 10MB。</p></div>
          </div>
          <form onSubmit={handleUpload}>
            <label
              className="knowledge-dropzone"
              data-dragging={isDragging || undefined}
              onDragEnter={(event) => { event.preventDefault(); setIsDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={(event) => { event.preventDefault(); setIsDragging(false); }}
              onDrop={handleDrop}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt,text/plain"
                onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
              />
              <FileText size={20} />
              <span>{selectedFile ? selectedFile.name : "选择或拖入 TXT 文件"}</span>
              <small>{selectedFile ? formatFileSize(selectedFile.size) : "UTF-8、UTF-16、GB18030"}</small>
            </label>
            <label className="knowledge-title-field">
              <span>书名</span>
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                maxLength={200}
                placeholder="选择文件后自动填写"
              />
            </label>
            <button type="submit" disabled={isUploading}>
              {isUploading ? <LoaderCircle className="spin" size={15} /> : <Upload size={15} />}
              {isUploading ? "正在保存" : "上传入库"}
            </button>
          </form>
        </section>

        {error && <p className="admin-message is-error" role="alert"><AlertCircle size={16} />{error}</p>}
        {notice && <p className="admin-message is-success" role="status"><CheckCircle2 size={16} />{notice}</p>}

        <section className="knowledge-workspace">
          <aside className="knowledge-sidebar" aria-label="知识库资料列表">
            <form className="knowledge-search" onSubmit={handleSearch}>
              <Search size={15} />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索书名或文件名"
                aria-label="搜索知识库"
              />
              <button type="submit">搜索</button>
            </form>
            <div className="knowledge-list-heading">
              <span>{documents.length} 份结果</span>
              <button type="button" aria-label="刷新资料列表" onClick={() => void refreshDocuments(search)}>
                <RefreshCcw size={13} />
              </button>
            </div>
            <div className="knowledge-document-list">
              {documents.map((document) => (
                <button
                  type="button"
                  key={document.id}
                  data-selected={document.id === selectedId || undefined}
                  onClick={() => setSelectedId(document.id)}
                >
                  <LibraryBig size={16} />
                  <span><strong>{document.title}</strong><small>{formatFileSize(document.byte_size)} · {document.encoding.toUpperCase()}</small></span>
                </button>
              ))}
              {documents.length === 0 && (
                <div className="knowledge-empty"><BookOpenText size={23} /><span>还没有符合条件的资料</span></div>
              )}
            </div>
          </aside>

          <article className="knowledge-reader">
            {selectedDocument ? (
              <>
                <header>
                  <div>
                    <span className="knowledge-reader-icon"><BookOpenText size={18} /></span>
                    <div><h2>{selectedDocument.title}</h2><small>{selectedDocument.original_filename}</small></div>
                  </div>
                  <div className="knowledge-reader-actions">
                    <button type="button" onClick={() => void handleDownload()}><Download size={14} />下载原文件</button>
                    <button type="button" data-danger="true" disabled={isDeleting} onClick={() => void handleDelete()}><Trash2 size={14} />{isDeleting ? "删除中" : "删除"}</button>
                  </div>
                </header>
                <div className="knowledge-reader-meta">
                  <span>编码 <strong>{selectedDocument.encoding.toUpperCase()}</strong></span>
                  <span>大小 <strong>{formatFileSize(selectedDocument.byte_size)}</strong></span>
                  <span>上传 <strong>{formatDate(selectedDocument.created_at)}</strong></span>
                  <span>正文 <strong>{totalCharacters.toLocaleString("zh-CN")} 字</strong></span>
                </div>
                <div className="knowledge-content">
                  {isContentLoading && !content ? (
                    <div className="knowledge-content-state"><LoaderCircle className="spin" size={20} />正在解码正文</div>
                  ) : (
                    <pre>{content}</pre>
                  )}
                </div>
                {hasMore && (
                  <button className="knowledge-load-more" type="button" disabled={isContentLoading} onClick={() => void handleLoadMore()}>
                    {isContentLoading ? <LoaderCircle className="spin" size={14} /> : <BookOpenText size={14} />}
                    {isContentLoading ? "正在读取" : `继续加载（已读取 ${nextOffset.toLocaleString("zh-CN")} 字）`}
                  </button>
                )}
              </>
            ) : (
              <div className="knowledge-reader-empty"><BookOpenText size={32} /><h2>选择一份资料</h2><p>上传 TXT 后可在这里浏览原文。</p></div>
            )}
          </article>
        </section>
    </AdminShell>
  );
}
