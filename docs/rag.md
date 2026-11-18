# Retrieval

`ai/rag/index.py` reads institution-approved `.txt`, `.md`, `.pdf`, and `.docx` files, excludes repository README instructions, normalizes extracted text, chunks it with overlap, hashes each source, and stores chunk metadata and vectors in the configured local Joblib index. Approved material belongs under `knowledge_base/policies/`, `academic_support/`, `mentoring/`, or `resources/`. Scanned/image-only PDFs require OCR before indexing; the pipeline does not fabricate extracted text or institutional policy.

When Sentence Transformers is installed and its configured model can load, normalized model embeddings are used. Otherwise, the index and API report the `hashing-vectorizer-development-fallback`; it is not described as a semantic embedding model. Retrieval ranks cosine similarity and returns source, chunk number, excerpt, and score.

No institutional documents are bundled. A missing index or no matching chunk explicitly reports no source/policy match; the application does not invent institutional guidance.

The current index is a shared corpus without enforceable course/student access-control metadata. It must not be used to answer student-specific tutor requests or other student-facing questions that require course authorization. The recovery tutor therefore disables document retrieval and uses only the authenticated student's verified academic records. Add document-level authorization and scoped retrieval before enabling indexed content for student responses.