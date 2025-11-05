import os
from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv

from app.core.logging import setup_logging, get_logger
from app.core.http_logging import install_http_logging


db = SQLAlchemy()
migrate = Migrate()

def create_app():
    load_dotenv()

    setup_logging("INFO")
    log = get_logger("bootstrap")


    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")
    app.config["TOKEN_TTL_MIN"] = int(os.getenv("TOKEN_TTL_MIN", "120"))

    db.init_app(app)
    migrate.init_app(app, db)

    from app.database import models

    from app.api.routers.auth import bp as auth_bp
    from app.api.routers.payroll import bp as payroll_bp
    from app.api.routers.payslips import bp as payslips_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(payroll_bp)
    app.register_blueprint(payslips_bp)

    install_http_logging(app)

    @app.route("/")
    def index():
        return "Slip Salary App - Connected"
    
    @app.errorhandler(401)
    def _unauth(e):
        return jsonify({"error": "Unauthorized"}), 401

    @app.errorhandler(403)
    def _forbidden(e):
        return jsonify({"error": "Forbidden"}), 403

    log.info("App started successfully",
             db=bool(app.config["SQLALCHEMY_DATABASE_URI"]))

    return app
