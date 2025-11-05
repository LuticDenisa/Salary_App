import csv
import os
from datetime import date, datetime, timedelta
from io import StringIO
from flask import Blueprint, request, jsonify, current_app
import glob
import smtplib
from email.message import EmailMessage
import shutil
import time

from app import db
from app.database.models import Employee

from app.core.auth import manager_required, current_user

from app.core.logging import get_logger
log = get_logger("payroll")

bp = Blueprint("payroll", __name__, url_prefix="/")

# /createAggregatedEmployeeData

# --- helpers ---
def month_bounds(d: date) -> tuple[date, date]:
    # m0 - prima zi din luna
    # m1 - ultima zi din luna
    m0 = d.replace(day=1)
    if m0.month == 12:
        m1 = date(m0.year + 1, 1, 1) - timedelta(days=1)
    else:
        m1 = date(m0.year, m0.month + 1, 1) - timedelta(days=1)
    return m0, m1

def business_days_in_month(d: date, holidays: set[date] | None = None) -> int:
    """numara zilele lucratoare (luni - vineri) din luna lui d (nu se tine cont de sarbatori legale)"""
    holidays = holidays or set()
    m0, m1 = month_bounds(d)
    days = 0
    cur = m0
    while cur <= m1:
        if cur.weekday() < 5 and cur not in holidays:  # 0 = luni, 4 = vineri
            days += 1
        cur += timedelta(days=1)
    return days

def fetch_manager_or_404(manager_id: int) -> Employee:
    mngr = Employee.query.filter_by(emp_id=manager_id, role="MANAGER", is_active=True).first()
    if not mngr:
        raise ValueError("Inexistent or inactive manager_id")
    return mngr

# --- endpoint ---
@bp.route("/createAggregatedEmployeeData", methods=["POST", "GET"])
@manager_required()
def create_aggregated_employee_data():
    """
    Genereaza CSV cu:
      - Employee name
      - Salary to be paid for the current month (base_salary + bonuses)
      - Number of working days during the month
      - Number of vacation days taken
      - Additional bonuses (if any)
    Salvez CSV în folderul archive/YYYY-MM/manager_<id>/aggregated_YYYY_MM.csv
    """
    # manager autentificat (din token)
    try:
        mgr = current_user() 
        manager_id = mgr.emp_id

        # luna curentă
        today = date.today()
        m0, m1 = month_bounds(today)
        working_days_month = business_days_in_month(today)

        # bonusuri & concedii (luna curenta)
        bonuses_sql = db.text("""
            SELECT emp_id, COALESCE(SUM(amount), 0) AS bonus_total
            FROM bonuses
            WHERE effective_month = :m0
            GROUP BY emp_id
        """)
        bonus_rows = {row.emp_id: float(row.bonus_total)
                      for row in db.session.execute(bonuses_sql, {"m0": m0}).fetchall()}

        vacations_sql = db.text("""
            SELECT emp_id,
                   SUM(GREATEST(0, LEAST(end_date, :m1) - GREATEST(start_date, :m0) + 1)) AS vac_days
            FROM vacations
            WHERE end_date >= :m0 AND start_date <= :m1
            GROUP BY emp_id
        """)
        vac_rows = {row.emp_id: int(row.vac_days)
                    for row in db.session.execute(vacations_sql, {"m0": m0, "m1": m1}).fetchall()}

        # angajatii manager autentif
        employees = (
            Employee.query
            .filter_by(manager_id=manager_id, is_active=True)
            .order_by(Employee.emp_id.asc())
            .all()
        )

        # CSV
        out = StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "Employee name",
            "Salary to be paid (current month)",
            "Working days in month",
            "Vacation days (taken)",
            "Additional bonuses (current month)"
        ])

        rows_count = 0
        for e in employees:
            bonus_total = bonus_rows.get(e.emp_id, 0.0)
            vacation_days = vac_rows.get(e.emp_id, 0)
            salary_to_pay = float(e.base_salary) + float(bonus_total)

            writer.writerow([
                f"{e.first_name} {e.last_name}",
                f"{salary_to_pay:.2f}",
                working_days_month,
                vacation_days,
                f"{bonus_total:.2f}"
            ])
            rows_count += 1

        csv_content = out.getvalue()

        # arhivare
        ym = today.strftime("%Y-%m")
        y_m = today.strftime("%Y_%m")
        base_dir = os.path.join(os.getcwd(), "archive", ym, f"manager_{manager_id}")
        os.makedirs(base_dir, exist_ok=True)
        file_path = os.path.join(base_dir, f"aggregated_{y_m}.csv")

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            f.write(csv_content)

        return jsonify({
            "status": "ok",
            "manager_id": manager_id,
            "period": {"month_start": m0.isoformat(), "month_end": m1.isoformat()},
            "working_days_in_month": working_days_month,
            "rows": rows_count,
            "file_path": file_path
        }), 200

    except Exception as e:
        current_app.logger.exception("Error to createAggregatedEmployeeData")
        return jsonify({"error": "Internal error.", "detail": str(e)}), 500
    

# /sendAggregatedEmployeeData

# --- helpers ---
def _find_latest_csv_for_manager(manager_id: int) -> str | None:
    """cauta cel mai recent fisier CSV generat pt manager_id
    archive/YYYY-MM/manager_<id>/aggregated_YYYY_MM.csv
    """
    archive_root = os.path.join(os.getcwd(), "archive")
    pattern = os.path.join(archive_root, "*", f"manager_{manager_id}", "aggregated_*.csv")
    candidates = glob.glob(pattern)
    if not candidates:
        return None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True) # cel mai recent
    return candidates[0]

def _send_email_with_attachment(
        to_email: str, 
        subject: str, 
        body_text: str, 
        attachment_path: str,
        from_email: str,
        smtp_host: str,
        smtp_port: int,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True
):
    
    """
    construieste mesaj cu atasament si trimite prin smtp
    """
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)

    with open(attachment_path, "rb") as f:
        data = f.read()
    msg.add_attachment(data, maintype="text", subtype="csv", filename=os.path.basename(attachment_path))

    if use_tls:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            if username and password:
                server.login(username, password)
            server.send_message(msg)


def _archive_sent_file(file_path: str) -> str:
    folder = os.path.dirname(file_path)
    sent_dir = os.path.join(folder, "sent")
    os.makedirs(sent_dir, exist_ok=True)

    base = os.path.basename(file_path)
    dest = os.path.join(sent_dir, base)

    if os.path.exists(dest):
        name, ext = os.path.splitext(base)
        dest = os.path.join(sent_dir, f"{name}_{int(time.time())}{ext}")

    shutil.move(file_path, dest)
    return dest


# --- endpoint ---
@bp.route("/sendAggregatedEmployeeData", methods=["POST", "GET"])
@manager_required()
def send_aggregated_employee_data():
    # managerul autentificat (din JWT)
    manager = current_user()
    manager_id = manager.emp_id

    # caut cel mai recent CSV pt acest manager
    csv_path = _find_latest_csv_for_manager(manager_id)
    if not csv_path:
        return jsonify({"error": "No aggregated CSV found for manager"}), 404

    # setari SMTP
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("FROM_EMAIL", smtp_user)
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

    # subiect + continut
    subject = f"Aggregated Employee Data for Manager {manager.first_name} {manager.last_name}"
    body_text = (
        f"Hello {manager.first_name}!\n\n"
        f"Please find attached the aggregated employee data for your team.\n"
        f"Generated file: {os.path.basename(csv_path)}\n\n"
        f"Best regards,\n"
        f"Slip Salary App"
    )

    # trimitere e-mail
    _send_email_with_attachment(
        to_email=manager.email,
        subject=subject,
        body_text=body_text,
        attachment_path=csv_path,
        from_email=from_email,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        username=smtp_user,
        password=smtp_pass,
        use_tls=use_tls,
    )

    log.info("csv_send", to=manager.email, file=os.path.basename(csv_path))

    # arhivare dupa trimitere
    archived_path = _archive_sent_file(csv_path)

    return jsonify({
        "status": "sent",
        "to": manager.email,
        "file": csv_path,
        "archived_to": archived_path
    }), 200


    


