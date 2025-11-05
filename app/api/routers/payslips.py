import os
from datetime import date, timedelta
from flask import Blueprint, request, jsonify, current_app
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
import pikepdf
import glob
import smtplib
from email.message import EmailMessage
import shutil
import time


from app import db
from app.database.models import Employee

from app.core.auth import manager_required, current_user

from app.core.logging import get_logger
log = get_logger("payslips")

bp = Blueprint("payslips", __name__, url_prefix="/")


# /createPdfForEmployees

# -- helpers ---
def month_bounds(d: date) -> tuple[date, date]:
    m0 = d.replace(day=1)
    if m0.month == 12:
        m1 = date(m0.year + 1, 1, 1) - timedelta(days=1)
    else:
        m1 = date(m0.year, m0.month + 1, 1) - timedelta(days=1)
    return m0, m1

def fetch_manager_or_404(manager_id: int) -> Employee:
    mngr = Employee.query.filter_by(emp_id=manager_id, role="MANAGER", is_active=True).first()
    if not mngr:
        raise ValueError("Inexistent or inactive manager_id")
    return mngr

def generate_payslip_pdf(employee: Employee, salary: float, bonuses: float, vacation_days: int, output_path: str):
    """ Genereaza PDF simplu cu datele angajului """
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # title
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, height - 80, "Payslip - current monthh")

    # employee details
    c.setFont("Helvetica", 12)
    y = height - 120
    c.drawString(50, y, f"Employee Name: {employee.first_name} {employee.last_name}")
    c.drawString(50, y - 20, f"CNP: {employee.cnp}")
    c.drawString(50, y - 40, f"Email: {employee.email}")
    c.drawString(50, y - 60, f"Grade: {employee.grade}")
    c.drawString(50, y - 80, f"Data angajării: {employee.hire_date}")

    # salary details
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y - 120, "Detalii salariale")
    c.setFont("Helvetica", 12)
    c.drawString(50, y - 140, f"Salariu de bază: {employee.base_salary:.2f} RON")
    c.drawString(50, y - 160, f"Bonusuri (luna curentă): {bonuses:.2f} RON")
    c.drawString(50, y - 180, f"Zile concediu: {vacation_days}")
    c.drawString(50, y - 200, f"Salariu total de plată: {salary:.2f} RON")

    c.showPage()
    c.save()

    # salvez pdf ul
    pdf_data = buffer.getvalue()
    with open(output_path, "wb") as f:
        f.write(pdf_data)

    # parola PDF cu CNP 
    with pikepdf.open(output_path, allow_overwriting_input=True) as pdf:
        pdf.save(
            output_path,
            encryption=pikepdf.Encryption(
                owner=employee.cnp,
                user=employee.cnp,
                R=4  # AES-128 encryption
            ),
        )

# --- endpoint ---
@bp.route("/createPdfForEmployees", methods=["POST", "GET"])
@manager_required()
def create_pdf_for_employees():
    try:
        # manager autentificat (din JWT)
        mngr = current_user()
        manager_id = mngr.emp_id

        today = date.today()
        m0, m1 = month_bounds(today)

        # bonuses
        bonuses_sql = db.text("""
            SELECT emp_id, COALESCE(SUM(amount), 0) AS bonus_total
            FROM bonuses
            WHERE effective_month = :m0
            GROUP BY emp_id
        """)
        bonus_rows = {
            row.emp_id: float(row.bonus_total)
            for row in db.session.execute(bonuses_sql, {"m0": m0}).fetchall()
        }

        # vacation days
        vacations_sql = db.text("""
            SELECT emp_id,
                   SUM(GREATEST(0, LEAST(end_date, :m1) - GREATEST(start_date, :m0) + 1)) AS vac_days
            FROM vacations
            WHERE end_date >= :m0 AND start_date <= :m1
            GROUP BY emp_id
        """)
        vac_rows = {
            row.emp_id: int(row.vac_days)
            for row in db.session.execute(vacations_sql, {"m0": m0, "m1": m1}).fetchall()
        }

        # angajatii managerului din token
        employees = (
            Employee.query
            .filter_by(manager_id=manager_id, is_active=True)
            .order_by(Employee.emp_id.asc())
            .all()
        )

        # folderul PDF-urilor
        pdf_dir = os.path.join(
            os.getcwd(),
            "archive",
            today.strftime("%Y-%m"),
            f"manager_{manager_id}",
            "pdfs"
        )
        os.makedirs(pdf_dir, exist_ok=True)

        generated = []
        for e in employees:
            bonuses = bonus_rows.get(e.emp_id, 0.0)
            vacation_days = vac_rows.get(e.emp_id, 0)
            salary_to_pay = float(e.base_salary) + float(bonuses)

            pdf_name = f"{e.first_name}_{e.last_name}_{today.strftime('%Y_%m')}.pdf"
            pdf_path = os.path.join(pdf_dir, pdf_name)

            generate_payslip_pdf(e, salary_to_pay, bonuses, vacation_days, pdf_path)
            generated.append(pdf_path)

        return jsonify({
            "status": "ok",
            "manager_id": manager_id,
            "generated_files": generated
        }), 200

    except Exception as e:
        current_app.logger.exception("Error in createPdfForEmployees")
        return jsonify({
            "error": "Internal error",
            "detail": str(e)
        }), 500



# /sendPdfToEmployees

# --- helpers ---
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
    use_tls: bool = True,
    use_ssl: bool = False,
):
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)

    with open(attachment_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=os.path.basename(attachment_path),
        )

    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            if username and password:
                server.login(username, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if use_tls:
                server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(msg)

def _find_pdfs_for_manager(manager_id:int) -> list[str]:
    """ cauta fisiere PDF generate pt manager_id
    archive/YYYY-MM/manager_<id>/pdfs/*.pdf
    """
    archive_root = os.path.join(os.getcwd(), "archive")
    pattern = os.path.join(archive_root, "*", f"manager_{manager_id}", "pdfs", "*.pdf")
    return sorted(glob.glob(pattern))

# mutam fisierele trimise intr un subdirector sent
def _archive_sent_file(path: str) -> str:
    folder = os.path.dirname(path)
    sent_dir = os.path.join(folder, "sent")
    os.makedirs(sent_dir, exist_ok=True)

    base = os.path.basename(path)
    dest = os.path.join(sent_dir, base)

    if os.path.exists(dest):
        name, ext = os.path.splitext(base)
        dest = os.path.join(sent_dir, f"{name}_{int(time.time())}{ext}")
    
    shutil.move(path, dest)
    return dest


# --- endpoint ---
@bp.route("/sendPdfToEmployees", methods=["POST", "GET"])
@manager_required()
def send_pdf_to_employees():
    """
    trimite pe email cate un fisier pdf fiecarui angajat al managerului
    """
    try:
        # managerul autentificat din JWT
        mngr = current_user()
        manager_id = mngr.emp_id

        # caut PDF-urile generate pentru acest manager
        pdf_files = _find_pdfs_for_manager(manager_id)
        if not pdf_files:
            return jsonify({"error": "No PDF files found for manager_id"}), 404
        
        #setari smtp
        smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME")
        smtp_pass = os.getenv("SMTP_PASSWORD")
        from_email = os.getenv("FROM_EMAIL", smtp_user)
        use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
        use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"

        sent = []
        skipped = []

        for pdf_path in pdf_files:
            base = os.path.basename(pdf_path)
            name_part = os.path.splitext(base)[0]
            parts = name_part.split("_")

            if len(parts) < 2:
                skipped.append({"file": base, "reason": "invalid_name"})
                continue

            first_name, last_name = parts[0], parts[1]

            # gaseste angajatul din db cu acest nume + manager_id
            emp = (
                Employee.query
                .filter(
                    Employee.first_name.ilike(first_name),
                    Employee.last_name.ilike(last_name),
                    Employee.manager_id == manager_id,
                    Employee.is_active.is_(True),
                )
                .first()
            )

            if not emp or not emp.email:
                skipped.append({"file": base, "reason": "employee_not_found_or_no_email"})
                continue

            subject = f"Payslip - {date.today():%B %Y}"
            body_text = (
                f"Hello {emp.first_name},\n\n"
                f"Please find attached your payslip for the current month.\n"
                f"The PDF is password-protected with your CNP.\n\n"
                f"Best regards,\n"
                f"Slip Salary App"
            )

            _send_email_with_attachment(
                to_email=emp.email,
                subject=subject,
                body_text=body_text,
                attachment_path=pdf_path,
                from_email=from_email,
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                username=smtp_user,
                password=smtp_pass,
                use_tls=use_tls,
                use_ssl=use_ssl,
            )

            log.info("pdf_sent", to=emp.email, file=base)

            # mut fisierul in sent
            archived_path = _archive_sent_file(pdf_path)

            sent.append({
                "employee": f"{emp.first_name} {emp.last_name}",
                "email": emp.email,
                "file": base,
                "archived_to": archived_path,
            })

        return jsonify({
            "status": "sent",
            "manager_id": manager_id,
            "sent_to": sent,
            "skipped": skipped,
        }), 200

    except Exception as e:
        current_app.logger.exception("Error in sendPdfToEmployees")
        return jsonify({"error": "Internal error", "detail": str(e)}), 500
