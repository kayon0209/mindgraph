# Streamlit page conventions

Page modules are direct scripts registered by `streamlit_app.py`. They use only `st.session_state.api_client` for backend access and keep expensive operations behind explicit submit actions.
