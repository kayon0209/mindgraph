# API conventions

Routes validate HTTP input and delegate to application services. They do not access SQLite, files, model SDKs, or retrievers directly. API errors are sanitized through centralized handlers.
