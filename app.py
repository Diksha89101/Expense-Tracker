import csv
import logging
import math
import os
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from urllib.parse import urlencode

from bson.errors import InvalidId
from bson.objectid import ObjectId
from dotenv import load_dotenv
from flask import Flask, Response, flash, redirect, render_template, request, session, url_for
from flask_mail import Mail, Message
from flask_wtf.csrf import CSRFProtect
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is missing. Set a strong secret in your environment or .env file.")

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
app.config["WTF_CSRF_ENABLED"] = True
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", "localhost")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["JSON_SORT_KEYS"] = False

csrf = CSRFProtect(app)
serializer = URLSafeTimedSerializer(SECRET_KEY)
mail = Mail(app)

@app.context_processor
def inject_template_utilities():
    return {"now": datetime.now}


logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
db = client["expense_tracker"]
expenses_collection = db["expenses"]
users_collection = db["users"]
income_collection = db["income"]

def ensure_mongo_index(collection, keys, **options):
    try:
        collection.create_index(keys, **options)  # type: ignore[call-overload]
    except PyMongoError:
        app.logger.exception("Could not create a MongoDB index for %s", collection.name)


try:
    client.admin.command("ping")
except PyMongoError:
    app.logger.warning("MongoDB is unavailable or indexes could not be created; continuing with app startup.")
else:
    startup_indexes = (
        (expenses_collection, [("username", 1), ("date", -1)], {}),
        (expenses_collection, [("username", 1), ("category", 1)], {}),
        (users_collection, "username", {"unique": True}),
        (users_collection, "email", {"unique": True}),
        (income_collection, "username", {"unique": True}),
    )
    for collection, keys, options in startup_indexes:
        ensure_mongo_index(collection, keys, **options)

EXPENSE_CATEGORIES = [
    "Food",
    "Groceries",
    "Rent / EMI",
    "Bills",
    "Insurance",
    "Internet & Wi-Fi",
    "Gas Cylinder",
    "Transportation",
    "Healthcare & Medicines",
    "Education & Books",
    "Children's Expenses",
    "Clothing & Footwear",
    "Home Maintenance & Repairs",
    "Furniture & Appliances",
    "Cleaning Supplies",
    "Kitchen Expenses",
    "Entertainment",
    "Family Functions & Celebrations",
    "Gifts & Donations",
    "Miscellaneous",
]
CURRENCIES = {"INR", "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "CNY"}
SORTS = {
    "date_new": ("date", -1),
    "date_old": ("date", 1),
    "amount_high": ("amount", -1),
    "amount_low": ("amount", 1),
    "category": ("category", 1),
    "title_asc": ("title", 1),
    "title_desc": ("title", -1),
}


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "username" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


@app.errorhandler(404)
def not_found(error):
    del error
    return render_template("404.html", authenticated="username" in session), 404


@app.errorhandler(500)
def server_error(error):
    del error
    app.logger.exception("Unhandled application error")
    return render_template("error.html"), 500


def get_expense_object_id(value):
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def parse_positive_amount(value):
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) and amount > 0 else None


def as_decimal(value):
    return Decimal(str(value or 0))


def round_money(value):
    return float(as_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def normalize_currency(value, default="INR"):
    text = (value if value is not None else default) or default
    if not isinstance(text, str):
        text = str(text)
    currency = text.strip().upper()
    return currency if currency in CURRENCIES else None


def safe_url_args(args):
    filtered = dict(args or {})
    filtered.pop("_external", None)
    filtered.pop("_scheme", None)
    return filtered


def build_url(endpoint, args=None, **path_values):
    base_url = url_for(endpoint, **path_values) if path_values else url_for(endpoint)
    query_args = safe_url_args(args or {})
    if not query_args:
        return base_url
    return f"{base_url}?{urlencode(query_args, doseq=True)}"


def calculate_dashboard_metrics(all_expenses, income_data):
    income_currency = normalize_currency(
        income_data.get("currency") if income_data else None,
        default="INR",
    ) or "INR"
    # Never combine unlike currencies. Legacy records without a currency use the
    # configured income currency so existing records remain visible.
    primary_expenses = [
        expense for expense in all_expenses
        if normalize_currency(expense.get("currency"), default=income_currency) == income_currency
    ]
    currency_totals = {}
    for expense in all_expenses:
        currency = normalize_currency(expense.get("currency"), default=income_currency) or income_currency
        currency_totals[currency] = currency_totals.get(currency, Decimal("0")) + as_decimal(expense.get("amount", 0) or 0)
    currency_totals = {currency: round_money(value) for currency, value in currency_totals.items()}

    current_month = datetime.now().strftime("%Y-%m")
    total = round_money(sum((as_decimal(item.get("amount", 0) or 0) for item in primary_expenses), Decimal("0")))
    monthly_expense = round_money(sum(
        (as_decimal(item.get("amount", 0) or 0) for item in primary_expenses if str(item.get("date", "")).startswith(current_month)),
        Decimal("0"),
    ))

    category_summary = {}
    monthly_summary = {}
    for expense in primary_expenses:
        amount = as_decimal(expense.get("amount", 0) or 0)
        category = expense.get("category", "Other")
        category_summary[category] = category_summary.get(category, Decimal("0")) + amount
        expense_date = str(expense.get("date", ""))
        if re.match(r"^\d{4}-\d{2}", expense_date):
            month = expense_date[:7]
            monthly_summary[month] = monthly_summary.get(month, Decimal("0")) + amount
    category_summary = {category: round_money(value) for category, value in category_summary.items()}
    monthly_summary = {
        month: round_money(monthly_summary[month])
        for month in sorted(monthly_summary)
    }

    total_income = round_money(as_decimal(income_data.get("salary", 0) or 0)) if income_data else 0
    remaining_balance = round_money(total_income - monthly_expense)
    savings_rate = round_money((remaining_balance / total_income) * 100) if total_income > 0 else 0
    budget_utilization = round_money((monthly_expense / total_income) * 100) if total_income > 0 else 0

    if total_income <= 0:
        budget_health = "Income needed"
    elif remaining_balance < 0:
        budget_health = "Over budget"
    elif budget_utilization >= 90:
        budget_health = "Critical"
    elif budget_utilization >= 70:
        budget_health = "Watch"
    else:
        budget_health = "Good"

    if category_summary:
        top_category = max(category_summary.items(), key=lambda item: item[1])[0]
    else:
        top_category = "None"
    months = sorted(monthly_summary)
    return {
        "total": round_money(total),
        "total_transactions": len(all_expenses),
        "monthly_expense": monthly_expense,
        "category_summary": category_summary,
        "labels": list(category_summary.keys()),
        "values": list(category_summary.values()),
        "months": months,
        "monthly_values": [monthly_summary[month] for month in months],
        "monthly_report": monthly_summary,
        "total_income": total_income,
        "income_currency": income_currency,
        "remaining_balance": remaining_balance,
        "savings_rate": savings_rate,
        "budget_utilization": budget_utilization,
        "budget_health": budget_health,
        "top_category": top_category,
        "currency_totals": currency_totals,
    }


def build_budget_insights(all_expenses, category_totals, monthly_expense, total_income, income_currency="INR"):
    insights = []
    if not all_expenses:
        return [{
            "title": "Start tracking consistently",
            "text": "Add a few expenses first and I will spot patterns in your spending.",
            "level": "info",
        }]

    if total_income <= 0:
        insights.append({
            "title": "Add your monthly income",
            "text": "Income helps calculate savings rate, monthly balance, and budget risk.",
            "level": "warning",
        })
    else:
        spending_ratio = monthly_expense / total_income
        savings_rate = max(0, (total_income - monthly_expense) / total_income)
        if spending_ratio >= 0.9:
            insights.append({
                "title": "High monthly spending",
                "text": "This month you have used almost all of your income. Review non-essential categories first.",
                "level": "danger",
            })
        elif spending_ratio >= 0.7:
            insights.append({
                "title": "Watch your budget",
                "text": "Your monthly expenses are above 70% of income. A small weekly limit can protect your balance.",
                "level": "warning",
            })
        else:
            insights.append({
                "title": "Healthy spending pace",
                "text": f"Your estimated savings rate is {savings_rate:.0%}. Keep reviewing it weekly.",
                "level": "success",
            })

    if category_totals:
        top_category = max(category_totals, key=category_totals.get)
        total_spend = sum(category_totals.values())
        share = category_totals[top_category] / total_spend if total_spend else 0
        insights.append({
            "title": f"{top_category} is your biggest category",
            "text": f"It accounts for {share:.0%} of tracked {income_currency} expenses. Set a target if this feels too high.",
            "level": "info",
        })

    recent_expenses = sorted(all_expenses, key=lambda item: item.get("date", ""), reverse=True)[:5]
    recent_by_currency = {}
    for expense in recent_expenses:
        currency = normalize_currency(expense.get("currency"), default=income_currency) or income_currency
        recent_by_currency[currency] = recent_by_currency.get(currency, 0) + float(expense.get("amount", 0) or 0)
    if recent_expenses:
        totals = " · ".join(
            f"{currency} {round(amount, 2):.2f}" for currency, amount in sorted(recent_by_currency.items())
        )
        insights.append({
            "title": "Recent activity check",
            "text": f"Your latest {len(recent_expenses)} entries total {totals}.",
            "level": "info",
        })
    return insights[:4]


def build_expense_query(username, args):
    clauses = [{"username": username}]
    search = (args.get("search") or "").strip()
    if search:
        safe_search = re.escape(search)
        clauses.append({
            "$or": [
                {"title": {"$regex": safe_search, "$options": "i"}},
                {"category": {"$regex": safe_search, "$options": "i"}},
            ]
        })

    if args.get("date"):
        clauses.append({"date": args.get("date")})
    month = args.get("month", "").strip()
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        clauses.append({"date": {"$gte": f"{month}-01", "$lte": f"{month}-31"}})
    for param, operator in (("start_date", "$gte"), ("end_date", "$lte")):
        value = args.get(param, "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            clauses.append({"date": {operator: value}})
    category = (args.get("category") or "").strip()
    if category:
        clauses.append({"category": category})

    amount_filter = {}
    for param, operator in (("min_amount", "$gte"), ("max_amount", "$lte")):
        value = args.get(param, "").strip()
        if value:
            try:
                amount = float(value)
                if math.isfinite(amount) and amount >= 0:
                    amount_filter[operator] = amount
            except ValueError:
                pass
    # Keep the older exact-amount filter working for existing links.
    exact_amount = (args.get("amount") or "").strip()
    if exact_amount and not amount_filter:
        try:
            amount_filter["$eq"] = float(exact_amount)
        except ValueError:
            pass
    if amount_filter:
        clauses.append({"amount": amount_filter})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def get_sort(args):
    return SORTS.get(args.get("sort"), ("date", -1))


def safe_csv_value(value):
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


@app.route("/")
@login_required
def home():
    query = build_expense_query(session["username"], request.args)
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = request.args.get("per_page", 10, type=int) or 10
    per_page = min(max(per_page, 1), 100)
    total_filtered = expenses_collection.count_documents(query)
    page_count = max(1, math.ceil(total_filtered / per_page))
    page = min(page, page_count)
    sort_field, sort_direction = get_sort(request.args)
    expenses = list(
        expenses_collection.find(query)
        .sort(sort_field, sort_direction)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    filtered_expenses = list(expenses_collection.find(query).sort("date", -1))
    income_data = income_collection.find_one({"username": session["username"]})
    metrics = calculate_dashboard_metrics(filtered_expenses, income_data)
    filter_categories = sorted({
        expense.get("category") for expense in filtered_expenses if expense.get("category")
    }) or EXPENSE_CATEGORIES
    ai_insights = build_budget_insights(
        filtered_expenses,
        metrics["category_summary"],
        metrics["monthly_expense"],
        metrics["total_income"],
        metrics["income_currency"],
    )
    return render_template(
        "index.html",
        expenses=expenses,
        total=metrics["total"],
        total_income=metrics["total_income"],
        total_expense=metrics["total"],
        salary=metrics["total_income"],
        remaining_balance=metrics["remaining_balance"],
        total_transactions=metrics["total_transactions"],
        monthly_expense=metrics["monthly_expense"],
        top_category=metrics["top_category"],
        category_summary=metrics["category_summary"],
        categories=EXPENSE_CATEGORIES,
        filter_categories=filter_categories,
        ai_insights=ai_insights,
        savings_rate=metrics["savings_rate"],
        budget_health=metrics["budget_health"],
        labels=metrics["labels"],
        values=metrics["values"],
        months=metrics["months"],
        monthly_values=metrics["monthly_values"],
        monthly_report=metrics["monthly_report"],
        income_currency=metrics["income_currency"],
        budget_utilization=metrics["budget_utilization"],
        currency_totals=metrics["currency_totals"],
        page=page,
        pages=page_count,
        per_page=per_page,
        total_filtered=total_filtered,
        pagination_args=safe_url_args(request.args.to_dict()),
    )


@app.route("/add", methods=["GET", "POST"])
@login_required
def add_expense():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        date_value = request.form.get("date", "").strip()
        category = request.form.get("category", "").strip()
        amount = parse_positive_amount(request.form.get("amount"))
        currency = normalize_currency(request.form.get("currency"))

        if not title or not date_value or not category or amount is None or currency is None:
            flash("Enter a title, valid amount, currency, category, and date.", "danger")
            return redirect(url_for("add_expense"))
        try:
            datetime.strptime(date_value, "%Y-%m-%d")
        except ValueError:
            flash("Please enter a valid expense date.", "danger")
            return redirect(url_for("add_expense"))
        if category not in EXPENSE_CATEGORIES:
            flash("Please select a valid expense category.", "danger")
            return redirect(url_for("add_expense"))

        try:
            expenses_collection.insert_one({
                "username": session["username"],
                "title": title,
                "amount": round(amount, 2),
                "category": category,
                "currency": currency,
                "date": date_value,
            })
        except PyMongoError:
            app.logger.exception("Could not save expense")
            flash("We couldn't save that expense. Please try again.", "danger")
            return redirect(url_for("add_expense"))
        flash("Expense added successfully!", "success")
        return redirect(url_for("home"))
    return render_template("add_exp.html", categories=EXPENSE_CATEGORIES)


@app.route("/delete/<id>", methods=["POST"])
@login_required
def delete_expense(id):
    expense_id = get_expense_object_id(id)
    if not expense_id:
        flash("That expense could not be found.", "warning")
        return redirect(build_url("home", request.args.to_dict()))
    try:
        result = expenses_collection.delete_one({"_id": expense_id, "username": session["username"]})
        if result.deleted_count:
            flash("Expense deleted successfully!", "success")
        else:
            flash("That expense could not be found.", "warning")
    except PyMongoError:
        app.logger.exception("Could not delete expense")
        flash("We couldn't delete that expense. Please try again.", "danger")
    return redirect(build_url("home", request.args.to_dict()))


@app.route("/edit/<id>", methods=["GET", "POST"])
@login_required
def edit_expense(id):
    expense_id = get_expense_object_id(id)
    return_args = safe_url_args(request.args.to_dict())
    if not expense_id:
        flash("That expense could not be found.", "warning")
        return redirect(build_url("home", return_args))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        date_value = request.form.get("date", "").strip()
        category = request.form.get("category", "").strip()
        amount = parse_positive_amount(request.form.get("amount"))
        currency = normalize_currency(request.form.get("currency"))
        if not title or not date_value or not category or amount is None or currency is None:
            flash("Enter a title, valid amount, currency, category, and date.", "danger")
            return redirect(build_url("edit_expense", return_args, id=id))
        try:
            datetime.strptime(date_value, "%Y-%m-%d")
        except ValueError:
            flash("Please enter a valid expense date.", "danger")
            return redirect(build_url("edit_expense", return_args, id=id))
        if category not in EXPENSE_CATEGORIES:
            flash("Please select a valid expense category.", "danger")
            return redirect(build_url("edit_expense", return_args, id=id))
        try:
            result = expenses_collection.update_one(
                {"_id": expense_id, "username": session["username"]},
                {"$set": {
                    "title": title,
                    "amount": round(amount, 2),
                    "category": category,
                    "currency": currency,
                    "date": date_value,
                }},
            )
        except PyMongoError:
            app.logger.exception("Could not update expense")
            flash("We couldn't update that expense. Please try again.", "danger")
            return redirect(build_url("edit_expense", return_args, id=id))
        if not result.matched_count:
            flash("That expense could not be found.", "warning")
        else:
            flash("Expense updated successfully.", "success")
        return redirect(build_url("home", return_args))

    expense = expenses_collection.find_one({"_id": expense_id, "username": session["username"]})
    if not expense:
        flash("That expense could not be found.", "warning")
        return redirect(build_url("home", return_args))
    return render_template("edit.html", expense=expense, categories=EXPENSE_CATEGORIES)


@app.route("/export")
@login_required
def export_csv():
    query = build_expense_query(session["username"], request.args)
    expenses = list(expenses_collection.find(query).sort("date", 1))

    class Echo:
        def write(self, value):
            return value

    def generate():
        writer = csv.writer(Echo())
        yield writer.writerow(["Title", "Amount", "Currency", "Category", "Date"])
        for expense in expenses:
            yield writer.writerow([
                safe_csv_value(expense.get("title")),
                expense.get("amount"),
                expense.get("currency", "INR"),
                safe_csv_value(expense.get("category")),
                expense.get("date"),
            ])

    return Response(
        generate(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=expenses.csv"},
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not username or not email or not password:
            error = "All fields are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            try:
                if users_collection.find_one({"username": username}):
                    error = "User already exists."
                elif users_collection.find_one({"email": email}):
                    error = "Email already registered."
                else:
                    users_collection.insert_one({
                        "username": username,
                        "email": email,
                        "password": generate_password_hash(password),
                    })
                    session["username"] = username
                    return redirect(url_for("home"))
            except PyMongoError:
                app.logger.exception("Could not register user")
                error = "We couldn't create your account. Please try again."
    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        try:
            user = users_collection.find_one({"username": username})
        except PyMongoError:
            app.logger.exception("Could not retrieve login account")
            error = "Sign-in is temporarily unavailable. Please try again."
        else:
            if user and check_password_hash(user["password"], password):
                session.clear()
                session["username"] = username
                return redirect(url_for("home"))
            error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
@app.route("/forgot_pass", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        try:
            user = users_collection.find_one({"email": email})
            if user:
                token = serializer.dumps(email, salt="password-reset")
                reset_link = f"{request.host_url.rstrip('/')}" + url_for("reset_password", token=token)
                msg = Message(
                    "Password Reset",
                    sender=app.config["MAIL_USERNAME"],
                    recipients=[email],
                )
                msg.body = f"Use this link to reset your password:\n\n{reset_link}\n\nThis link expires in 30 minutes."
                try:
                    mail.send(msg)
                except Exception:
                    app.logger.exception("Could not send password reset email")
                    flash("We couldn't send the reset email right now. Please try again later.", "warning")
                    return redirect(url_for("login"))
        except PyMongoError:
            app.logger.exception("Could not process password reset request")
            flash("Password reset is temporarily unavailable. Please try again.", "warning")
            return redirect(url_for("login"))
        flash("If the email exists, a reset link has been sent.", "info")
        return redirect(url_for("login"))
    return render_template("login.html", show_forgot=True)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        email = serializer.loads(token, salt="password-reset", max_age=1800)
    except SignatureExpired:
        flash("That password reset link has expired. Request a new one.", "warning")
        return redirect(url_for("login"))
    except BadSignature:
        flash("That password reset link is invalid.", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if not password or len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return redirect(url_for("reset_password", token=token))
        try:
            result = users_collection.update_one(
                {"email": email},
                {"$set": {"password": generate_password_hash(password)}},
            )
        except PyMongoError:
            app.logger.exception("Could not update password")
            flash("We couldn't update your password. Please try again.", "danger")
            return redirect(url_for("reset_password", token=token))
        if not result.matched_count:
            flash("The account for this reset link no longer exists.", "warning")
            return redirect(url_for("login"))
        flash("Password reset successfully.", "success")
        return redirect(url_for("login"))
    return render_template("reset_pass.html")


@app.route("/add-income", methods=["GET", "POST"])
@login_required
def add_income():
    if request.method == "POST":
        salary = parse_positive_amount(request.form.get("salary"))
        currency = normalize_currency(request.form.get("currency"))
        if salary is None or currency is None:
            flash("Please enter a valid income and currency.", "danger")
            return redirect(url_for("add_income"))
        try:
            income_collection.update_one(
                {"username": session["username"]},
                {"$set": {"salary": round(salary, 2), "currency": currency}},
                upsert=True,
            )
        except PyMongoError:
            app.logger.exception("Could not save income")
            flash("We couldn't save your income. Please try again.", "danger")
            return redirect(url_for("add_income"))
        flash("Income saved successfully!", "success")
        return redirect(url_for("home"))
    return render_template("add_income.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
