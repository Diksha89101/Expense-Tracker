import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from bson import ObjectId
from werkzeug.security import check_password_hash

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-enough-entropy-for-suite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as expense_app
from app import app, build_budget_insights, calculate_dashboard_metrics, parse_positive_amount


class FakeCursor:
    def __init__(self, documents):
        self.documents = list(documents)

    def sort(self, key, direction=1):
        if isinstance(key, list):
            for field, order in reversed(key):
                self.sort(field, order)
        else:
            self.documents.sort(key=lambda doc: doc.get(key), reverse=direction < 0)
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __iter__(self):
        return iter(self.documents)


def matches(document, query):
    for key, condition in query.items():
        if key == "$and":
            if not all(matches(document, clause) for clause in condition):
                return False
            continue
        if key == "$or":
            if not any(matches(document, clause) for clause in condition):
                return False
            continue

        value = document.get(key)
        if isinstance(condition, dict):
            if "$regex" in condition:
                flags = re.IGNORECASE if condition.get("$options") == "i" else 0
                if not re.search(condition["$regex"], str(value or ""), flags):
                    return False
            for operator, expected in condition.items():
                if operator == "$gte" and (value is None or value < expected):
                    return False
                if operator == "$lte" and (value is None or value > expected):
                    return False
                if operator == "$eq" and value != expected:
                    return False
        elif value != condition:
            return False
    return True


class FakeCollection:
    def __init__(self):
        self.documents = []
        self.name = "fake"

    def find(self, query=None):
        query = query or {}
        return FakeCursor([dict(doc) for doc in self.documents if matches(doc, query)])

    def find_one(self, query=None):
        query = query or {}
        for document in self.documents:
            if matches(document, query):
                return dict(document)
        return None

    def count_documents(self, query):
        return sum(matches(doc, query) for doc in self.documents)

    def insert_one(self, document):
        inserted = dict(document)
        inserted["_id"] = ObjectId()
        self.documents.append(inserted)
        return type("InsertResult", (), {"inserted_id": inserted["_id"]})()

    def delete_one(self, query):
        for index, document in enumerate(self.documents):
            if matches(document, query):
                del self.documents[index]
                return type("DeleteResult", (), {"deleted_count": 1})()
        return type("DeleteResult", (), {"deleted_count": 0})()

    def update_one(self, query, update, upsert=False):
        for document in self.documents:
            if matches(document, query):
                document.update(update.get("$set", {}))
                return type("UpdateResult", (), {"matched_count": 1})()
        if upsert:
            self.insert_one({**query, **update.get("$set", {})})
        return type("UpdateResult", (), {"matched_count": 0})()


@pytest.fixture(autouse=True)
def fake_database(monkeypatch):
    expenses = FakeCollection()
    users = FakeCollection()
    income = FakeCollection()
    monkeypatch.setattr(expense_app, "expenses_collection", expenses)
    monkeypatch.setattr(expense_app, "users_collection", users)
    monkeypatch.setattr(expense_app, "income_collection", income)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SECRET_KEY="test-secret-key-with-enough-entropy-for-suite")
    yield {"expenses": expenses, "users": users, "income": income}
    app.config.update(TESTING=False, WTF_CSRF_ENABLED=True)


def logged_in_client(username="sam"):
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["username"] = username
    return client


def test_auth_pages_render_and_include_back_button():
    client = app.test_client()
    assert client.get("/login").status_code == 200
    assert client.get("/register").status_code == 200
    assert b"Back" in client.get("/login").data
    assert b"Back" in client.get("/register").data


def test_registration_and_login_authenticate_user(fake_database):
    client = app.test_client()
    response = client.post("/register", data={
        "username": "sam",
        "email": "sam@example.test",
        "password": "secure1",
    })
    assert response.status_code == 302
    user = fake_database["users"].find_one({"username": "sam"})
    assert user is not None
    assert check_password_hash(user["password"], "secure1")

    client.get("/logout")
    response = client.post("/login", data={"username": "sam", "password": "secure1"})
    assert response.status_code == 302
    with client.session_transaction() as browser_session:
        assert browser_session["username"] == "sam"


def test_expense_create_edit_and_delete_use_authenticated_owner(fake_database):
    client = logged_in_client()
    data = {
        "title": "Lunch",
        "amount": "12.35",
        "currency": "USD",
        "category": "Food",
        "date": datetime.now().strftime("%Y-%m-%d"),
    }
    assert client.post("/add", data=data).status_code == 302
    expense = fake_database["expenses"].find_one({"username": "sam"})
    assert expense["amount"] == 12.35
    expense_id = str(expense["_id"])

    updated = {**data, "title": "Dinner", "amount": "18.25"}
    assert client.post(f"/edit/{expense_id}", data=updated).status_code == 302
    assert fake_database["expenses"].find_one({"_id": expense["_id"]})["title"] == "Dinner"

    assert client.post(f"/delete/{expense_id}").status_code == 302
    assert fake_database["expenses"].count_documents({"username": "sam"}) == 0


def test_csrf_rejects_mutating_requests_without_token():
    app.config.update(WTF_CSRF_ENABLED=True, WTF_CSRF_CHECK_DEFAULT=True)
    response = app.test_client().post("/register", data={
        "username": "sam",
        "email": "sam@example.test",
        "password": "secure1",
    })
    assert response.status_code == 400


def test_parse_positive_amount_rejects_invalid_and_non_finite_values():
    assert parse_positive_amount("10.5") == 10.5
    assert parse_positive_amount("0") is None
    assert parse_positive_amount("-5") is None
    assert parse_positive_amount("nan") is None
    assert parse_positive_amount("abc") is None


def test_budget_insight_says_start_tracking_when_empty():
    insights = build_budget_insights([], {}, 0, 0)
    assert insights[0]["title"] == "Start tracking consistently"


def test_dashboard_metrics_round_values_sort_months_and_separate_currencies():
    current_month = datetime.now().strftime("%Y-%m")
    expenses = [
        {"amount": 0.1, "category": "Food", "date": f"{current_month}-01", "currency": "USD"},
        {"amount": 0.2, "category": "Food", "date": f"{current_month}-02", "currency": "USD"},
        {"amount": 5.555, "category": "Rent / EMI", "date": "2024-01-02", "currency": "USD"},
        {"amount": 1000, "category": "Food", "date": f"{current_month}-03", "currency": "INR"},
    ]

    result = calculate_dashboard_metrics(expenses, {"salary": 1, "currency": "USD"})

    assert result["total"] == 5.86
    assert result["monthly_expense"] == 0.3
    assert result["remaining_balance"] == 0.7
    assert result["category_summary"] == {"Food": 0.3, "Rent / EMI": 5.56}
    assert result["months"] == ["2024-01", current_month] if current_month != "2024-01" else result["months"] == ["2024-01"]
    assert result["monthly_values"] == [5.56, 0.3] if current_month != "2024-01" else result["monthly_values"] == [5.86]
    assert result["income_currency"] == "USD"
    assert result["currency_totals"]["INR"] == 1000


def test_search_filter_pagination_and_csv_export(fake_database):
    client = logged_in_client()
    for title, day, amount in [("Rent", "2024-01-01", 20), ("Food", "2024-01-02", 30), ("Rent", "2024-01-03", 40)]:
        fake_database["expenses"].insert_one({
            "username": "sam",
            "title": title,
            "amount": amount,
            "currency": "INR",
            "category": "Housing" if title == "Rent" else "Food",
            "date": day,
        })

    result = client.get("/?search=Rent&start_date=2024-01-01&end_date=2024-01-03&per_page=1")
    assert result.status_code == 200
    assert b"Rent" in result.data
    assert b"Food" not in result.data
    assert b"Page 1 of 2" in result.data

    export = client.get("/export?search=Rent")
    assert export.status_code == 200
    rows = export.get_data(as_text=True).splitlines()
    assert rows[0] == "Title,Amount,Currency,Category,Date"
    assert len(rows) == 3
    assert rows[1].startswith("Rent,20,INR,")


def test_home_requires_authentication():
    assert app.test_client().get("/").status_code == 302
