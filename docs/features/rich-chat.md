# Replies, attachments, and reactions

The Shell composer supports replying to a durable message, sending files (including
images without a text caption), and adding/removing emoji reactions. Existing
`@handle` text is preserved. Draft reply/file selections stay with their conversation.

Attachments are copied into each receiving EVA's workspace, under a generated,
conversation-scoped path. Original filenames are display metadata, not filesystem
paths. Messages retain the local path, MIME type, and byte size. Use the attachment
card to preview supported images, download the file, or copy its workspace-relative
path. The limit is eight attachments and 10 MiB total per message.

Image-capable catalog models receive validated PNG/JPEG/GIF/WebP bytes within their
advertised limits (at most eight images and 10 MiB per request, with a conservative
5 MiB per-image bound). Other files, unsupported images, audio/video, missing files,
and text-only models retain the path text. A path alone does not mean the provider
has read or understood that file. Custom endpoints without capability metadata are
conservatively text-only. No remote URL fetching is performed.

Local owner, direct peer, and group conversations support these features. Peer/group
attachments transfer the bytes and are saved again in the recipient's own workspace;
a sender's local path is not used as the recipient's attachment address. Upgrade all
participating peers before using rich messages: older peers can reject the added
protocol fields. Message and reaction journals survive restarts and duplicate delivery.
Reactions are actor-scoped add/remove operations, not new model turns. User-authored
threaded replies may request a turn; automatic replies remain history-only to avoid
reply loops. Owner forms and local system events are not peer chat payloads.
