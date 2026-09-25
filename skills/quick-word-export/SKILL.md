---
name: quick-word-export
description: Quickly package existing prose, teaching notes, lists, or simple tables into a plain professional DOCX. Use for routine Word exports. Do not use for polished external documents, exact templates, tracked changes, comments, forms, complex layout, or native Google Docs import.
---

# Quick Word Export

This is the workspace default for routine Word output.

1. Reuse the user's content or prepare a clean UTF-8 plain-text source with a title, numbered headings, short paragraphs, and simple lists.
2. Keep styling restrained: readable Chinese system font, no cover page, decorative furniture, template selection, or generated imagery.
3. Convert once with `scripts/export_simple_word.sh SOURCE_TEXT OUTPUT_DOCX`.
4. Validate that the DOCX exists, is a valid ZIP/OOXML package, and reports as an Office Open XML document. Do not render pages unless conversion fails or the user explicitly requests visual QA.
5. Deliver only the DOCX. Temporary text is not a user-facing artifact.

Escalate to the comprehensive Documents workflow only for polished/formal production or features that this converter cannot preserve reliably.
