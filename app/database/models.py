from datetime import date
from app import db as orm  # alias, ca să nu se confunde cu pachetul app.db

class Employee(orm.Model):
    __tablename__ = "employees"

    emp_id = orm.Column(orm.Integer, primary_key=True)
    first_name = orm.Column(orm.String(50), nullable=False)
    last_name = orm.Column(orm.String(50), nullable=False)
    cnp = orm.Column(orm.String(13), nullable=False, unique=True)
    email = orm.Column(orm.String(120), nullable=False, unique=True)
    role = orm.Column(orm.String(16), nullable=False)  # 'EMPLOYEE' | 'MANAGER' | 'ADMIN'
    grade = orm.Column(orm.String(20))
    base_salary = orm.Column(orm.Numeric(12, 2), nullable=False, default=0)
    manager_id = orm.Column(orm.Integer, orm.ForeignKey("employees.emp_id"), nullable=True)
    hire_date = orm.Column(orm.Date, nullable=False, default=date.today)
    is_active = orm.Column(orm.Boolean, nullable=False, default=True)

    manager = orm.relationship("Employee", remote_side=[emp_id], backref="reports")

class Bonus(orm.Model):
    __tablename__ = "bonuses"

    bonus_id = orm.Column(orm.Integer, primary_key=True)
    emp_id = orm.Column(orm.Integer, orm.ForeignKey("employees.emp_id", ondelete="CASCADE"), nullable=False)
    name = orm.Column(orm.String(100), nullable=False)
    amount = orm.Column(orm.Numeric(12, 2), nullable=False)
    effective_month = orm.Column(orm.Date, nullable=False)
    created_at = orm.Column(orm.DateTime, nullable=False, server_default=orm.func.now())

    employee = orm.relationship("Employee", backref="bonuses")

class Vacation(orm.Model):
    __tablename__ = "vacations"

    vac_id = orm.Column(orm.Integer, primary_key=True)
    emp_id = orm.Column(orm.Integer, orm.ForeignKey("employees.emp_id", ondelete="CASCADE"), nullable=False)
    start_date = orm.Column(orm.Date, nullable=False)
    end_date = orm.Column(orm.Date, nullable=False)
    type = orm.Column(orm.String(12), nullable=False)  # 'PAID' | 'UNPAID'
    created_at = orm.Column(orm.DateTime, nullable=False, server_default=orm.func.now())

    employee = orm.relationship("Employee", backref="vacations")
