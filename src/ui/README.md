# UI conventions

`api_client.py` is the only backend integration point. Streamlit pages must not import application services, retrieval implementations, model SDKs, SQLite, or evaluation internals.
