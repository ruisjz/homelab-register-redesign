import hmac, secrets, threading, time
from http import cookies
from .config import AUTH_USERNAME, AUTH_PASSWORD, SESSION_TTL, COOKIE_SECURE

COOKIE = "hr_session"
_active = {}
# P13: o servidor atende pedidos em várias threads; o dicionário de sessões é partilhado.
_lock = threading.RLock()


def _cleanup_expired(now=None):
    now = time.time() if now is None else now
    with _lock:
        stale = [token for token, exp in _active.items() if exp <= now]
        for token in stale:
            _active.pop(token, None)


def _same(a, b):
    """Comparação em tempo constante, em bytes UTF-8, para aceitar acentos (P10)."""
    return hmac.compare_digest(
        str(a).encode("utf-8", "surrogatepass"), str(b).encode("utf-8", "surrogatepass")
    )


def valid(u, p):
    # As duas comparações correm sempre, para o tempo de resposta não revelar
    # se o utilizador existe.
    user_ok = _same(u, AUTH_USERNAME)
    password_ok = _same(p, AUTH_PASSWORD)
    return user_ok and password_ok


def create():
    _cleanup_expired()
    t = secrets.token_urlsafe(32)
    with _lock:
        _active[t] = time.time() + SESSION_TTL
    return t


def destroy(t):
    if t:
        with _lock:
            _active.pop(t, None)


def current(headers):
    _cleanup_expired()
    jar = cookies.SimpleCookie()
    try:
        jar.load(headers.get("Cookie", ""))
    except cookies.CookieError:
        return None
    m = jar.get(COOKIE)
    if not m:
        return None
    with _lock:
        exp = _active.get(m.value)
        if exp is None:
            return None
        if exp <= time.time():
            _active.pop(m.value, None)
            return None
    return m.value


def cookie(handler, token):
    jar = cookies.SimpleCookie()
    jar[COOKIE] = token
    jar[COOKIE]["path"] = "/"
    jar[COOKIE]["max-age"] = str(SESSION_TTL)
    jar[COOKIE]["httponly"] = True
    jar[COOKIE]["samesite"] = "Lax"
    if COOKIE_SECURE:
        jar[COOKIE]["secure"] = True
    handler.send_header("Set-Cookie", jar[COOKIE].OutputString())


def clear(handler):
    jar = cookies.SimpleCookie()
    jar[COOKIE] = ""
    jar[COOKIE]["path"] = "/"
    jar[COOKIE]["max-age"] = "0"
    jar[COOKIE]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    jar[COOKIE]["httponly"] = True
    jar[COOKIE]["samesite"] = "Lax"
    if COOKIE_SECURE:
        jar[COOKIE]["secure"] = True
    handler.send_header("Set-Cookie", jar[COOKIE].OutputString())
