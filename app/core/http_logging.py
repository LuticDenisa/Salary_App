import time
from flask import request
from .logging import get_logger

log = get_logger("http")

def install_http_logging(app):
    @app.before_request
    def _t0():
        request._t0 = time.time()

    @app.after_request
    def _log(resp):
        try:
            dur = int((time.time() - getattr(request, "_t0", time.time())) * 1000)
            log.info("http_request",
                     method=request.method,
                     path=request.path,
                     status=resp.status_code,
                     duration_ms=dur,
                     manager_id=request.args.get("manager_id"))
        except Exception:
            pass
        return resp
