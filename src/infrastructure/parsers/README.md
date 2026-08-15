# Parser adapter conventions

Each file format has one `DocumentParser` adapter. Adapters return ordered structured elements, warnings and OCR-required pages; they never create retrieval chunks directly. Parser selection is centralized in `ParserRegistry`.
