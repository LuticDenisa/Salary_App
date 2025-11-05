from flask import Blueprint, request, jsonify, current_app
from app.database.models import Employee
from app.core.auth import generate_token, manager_required, current_user
import hashlib
import jwt

bp = Blueprint("auth", __name__, url_prefix="/auth")

@bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    cnp = (data.get("cnp") or "").strip()

    if not email or not cnp:
        return jsonify({"error": "email and cnp are required"}), 400

    # folosesc email + cnp ca parola
    user = Employee.query.filter_by(email=email, cnp=cnp, is_active=True).first()
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401
    
    token = generate_token(user)
    expires_in = int(current_app.config.get("TOKEN_TTL_MIN", 120)) * 60

    return jsonify({
        "access_token": token,
        "emp_id": user.emp_id,
        "role": user.role,
        "expires_in": expires_in,
        "name": f"{user.first_name} {user.last_name}",
    }), 200


@bp.route("/debug-jwt", methods=["GET"])
def debug_jwt():
    # extrage Authorization
    auth = request.headers.get("Authorization", "")
    has_bearer = auth.startswith("Bearer ")
    token = auth.split(" ", 1)[1].strip() if has_bearer else ""

    # ia secretul efectiv folosit ACUM de app
    secret = str(current_app.config.get("SECRET_KEY", "dev-secret"))
    secret_sha = hashlib.sha256(secret.encode()).hexdigest()

    info = {
        "has_authorization_header": bool(auth),
        "has_bearer_prefix": has_bearer,
        "token_length": len(token),
        "token_head": token[:12],
        "token_tail": token[-12:],
        "secret_len": len(secret),
        "secret_sha256_prefix": secret_sha[:16],
        "token_ttl_min": current_app.config.get("TOKEN_TTL_MIN"),
    }

    if token:
        try:
            payload = jwt.decode(token, secret, algorithms=["HS256"], leeway=10)
            info["decode_ok"] = True
            info["payload"] = payload
        except Exception as e:
            info["decode_ok"] = False
            info["error"] = str(e)

    return info, 200