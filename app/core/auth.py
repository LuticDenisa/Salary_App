import os
import time
import jwt
from functools import wraps
from flask import request, jsonify, g, current_app
from app.database.models import Employee

def _secret() -> str:
    return current_app.config.get("SECRET_KEY", "dev-secret")

def _ttl_minutes() -> int:
    try:
        return int(current_app.config.get("TOKEN_TTL_MIN", 120))
    except Exception:
        return 120

def generate_token(employee: Employee) -> str:
    now = int(time.time())
    payload = {
        "sub": str(employee.emp_id),
        "role": employee.role,
        "iat": now,
        "exp": now + _ttl_minutes() * 60,
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")

def _decode_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=["HS256"], leeway=10)

def current_user():
    return getattr(g, "current_user", None)

def manager_required(require_match_with_param: bool = True):
    """
    verifica:
        - exista Bearer Token
        - tokenul este valid
        - user cu rol de manager
    Ataseaza userul pe g.current_user
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return jsonify({"error": "Missing Bearer token"}), 401
            token = auth.split(" ", 1)[1].strip()

            try:
                data = _decode_token(token)
            except jwt.ExpiredSignatureError:
                return jsonify({"error": "Token has expired"}), 401
            except jwt.InvalidSignatureError:
                return jsonify({"error": "Invalid token", "detail": "signature failed"}), 401
            except jwt.DecodeError:
                return jsonify({"error": "Invalid token", "detail": "malformed"}), 401
            except jwt.InvalidTokenError as e:
                return jsonify({"error": "Invalid token", "detail": str(e)}), 401

            emp_id = int(data.get("sub"))
            emp = Employee.query.filter_by(emp_id=emp_id, is_active=True).first()
            if not emp:
                return jsonify({"error": "User not found or inactive"}), 401
            if emp.role != "MANAGER":
                return jsonify({"error": "Manager role required"}), 403

            if require_match_with_param:
                body = request.get_json(silent=True) or {}
                param = request.args.get("manager_id") or body.get("manager_id")
                if param is not None and int(param) != emp.emp_id:
                    return jsonify({"error": "Manager can only access their own data"}), 403

            g.current_user = emp
            return fn(*args, **kwargs)
        return wrapper
    return decorator


