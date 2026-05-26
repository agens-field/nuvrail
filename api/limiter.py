"""
Shared slowapi Limiter instance for Nuvrail API.

Centralised here so api/main.py and route modules can both import it without
circular dependencies.  The app binds it to app.state.limiter in main.py.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
