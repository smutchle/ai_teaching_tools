from pypdf import PdfReader

MAX_CHARS_PER_CHUNK = 40_000


def extract_text_from_pdf(file_obj):
    file_obj.seek(0)
    reader = PdfReader(file_obj)
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text.strip())
    return "\n\n".join(parts)


def extract_text_from_text(file_obj):
    file_obj.seek(0)
    content = file_obj.read()
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return content


def extract_text(file_obj, filename):
    fname = filename.lower()
    if fname.endswith(".pdf"):
        return extract_text_from_pdf(file_obj)
    return extract_text_from_text(file_obj)


def chunk_text(text, max_chars=MAX_CHARS_PER_CHUNK):
    if len(text) <= max_chars:
        return [text]

    chunks = []
    current_parts = []
    current_len = 0

    for paragraph in text.split("\n\n"):
        para_len = len(paragraph) + 2
        if current_len + para_len > max_chars and current_parts:
            chunks.append("\n\n".join(current_parts))
            current_parts = [paragraph]
            current_len = para_len
        else:
            current_parts.append(paragraph)
            current_len += para_len

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


def process_files(uploaded_files):
    all_text_parts = []
    for uf in uploaded_files:
        try:
            text = extract_text(uf, uf.name)
            if text.strip():
                all_text_parts.append(text)
        except Exception as e:
            all_text_parts.append(f"[Could not extract text from {uf.name}: {e}]")

    combined = "\n\n---\n\n".join(all_text_parts)
    return chunk_text(combined)
