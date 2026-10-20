import { useEffect, useRef, useState } from "react";
import { readPeerAttachment, type PeerAttachment } from "./peer";
import { formatFileSize } from "./rich-messages";
import { useT } from "./i18n";

const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"]);

function attachmentBlob(encoded: string, mimeType: string): Blob {
  const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
  return new Blob([bytes], { type: mimeType });
}

function Attachment({ chatId, messageId, attachment }: {
  chatId: string; messageId: string | number; attachment: PeerAttachment;
}) {
  const t = useT();
  const [preview, setPreview] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const downloadLock = useRef(false);
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!IMAGE_TYPES.has(attachment.mime_type)) return;
    let cancelled = false;
    let objectUrl = "";
    setPreview("");
    const load = () => {
      void readPeerAttachment(chatId, messageId, attachment.id).then((result) => {
        // SVG/HTML and arbitrary file/remote URLs are never rendered in the chat.
        if (cancelled || !IMAGE_TYPES.has(result.mime_type)) return;
        objectUrl = URL.createObjectURL(attachmentBlob(result.data_base64, result.mime_type));
        setPreview(objectUrl);
      }).catch(() => { /* The file card and download action remain usable. */ });
    };
    // Long histories must not eagerly load hundreds of full-resolution files.
    const observer = typeof IntersectionObserver === "undefined" ? undefined : new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { observer?.disconnect(); load(); }
    }, { rootMargin: "200px" });
    if (observer && cardRef.current) observer.observe(cardRef.current);
    else load();
    return () => { cancelled = true; observer?.disconnect(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [chatId, messageId, attachment.id, attachment.mime_type]);

  async function download() {
    if (downloadLock.current) return;
    downloadLock.current = true;
    setLoading(true); setError("");
    try {
      const result = await readPeerAttachment(chatId, messageId, attachment.id);
      const url = URL.createObjectURL(attachmentBlob(result.data_base64, "application/octet-stream"));
      const link = document.createElement("a");
      link.href = url;
      link.download = attachment.name;
      document.body.append(link);
      link.click();
      link.remove();
      // Keep the URL alive until the browser has started the download.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch { setError(t("peer.attachmentFailed")); }
    finally { downloadLock.current = false; setLoading(false); }
  }
  async function copyPath() {
    setError("");
    try {
      if (window.magiDesktop?.copyText) await window.magiDesktop.copyText(attachment.path);
      else await navigator.clipboard.writeText(attachment.path);
      setCopied(true);
    } catch { setError(t("peer.copyFailed")); }
  }
  return <div ref={cardRef} className="chat-page__attachment">
    {preview && <img className="chat-page__attachment-image" src={preview} alt={attachment.name} loading="lazy" onError={() => setPreview("")} />}
    <div className="chat-page__attachment-info"><span aria-hidden="true">↧</span><div><strong>{attachment.name}</strong><small>{formatFileSize(attachment.size)} · {attachment.mime_type}</small></div></div>
    <div className="chat-page__attachment-actions"><button type="button" onClick={() => void download()} disabled={loading}>{t(loading ? "peer.attachmentLoading" : "peer.download")}</button>{attachment.path && <button type="button" onClick={() => void copyPath()}>{t(copied ? "peer.pathCopied" : "peer.copyPath")}</button>}</div>
    {attachment.path && <span className="chat-page__attachment-path" title={attachment.path}>{attachment.path}</span>}
    {error && <small role="alert" className="chat-page__attachment-error">{error}</small>}
  </div>;
}

export function MessageAttachments({ chatId, messageId, attachments }: {
  chatId: string; messageId: string | number; attachments: PeerAttachment[];
}) {
  return <div className="chat-page__attachments">{attachments.map((attachment) => <Attachment key={attachment.id} chatId={chatId} messageId={messageId} attachment={attachment} />)}</div>;
}

export function PendingAttachment({ file, onRemove, disabled }: { file: File; onRemove: () => void; disabled: boolean }) {
  const t = useT();
  const [preview, setPreview] = useState("");
  useEffect(() => {
    if (!IMAGE_TYPES.has(file.type)) return;
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  return <div className="chat-page__pending-attachment">
    {preview && <img src={preview} alt="" />}
    <span><strong>{file.name}</strong><small>{formatFileSize(file.size)}</small></span>
    <button type="button" disabled={disabled} onClick={onRemove} aria-label={`${t("peer.removeAttachment")} ${file.name}`}>×</button>
  </div>;
}
