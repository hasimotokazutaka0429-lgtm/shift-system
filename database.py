import os

import streamlit as st
from supabase import create_client


# ============================================================
# Supabase接続
# ============================================================
SUPABASE_URL = os.environ.get("https://aikmcawgwfrvcactbtxu.supabase.co")
SUPABASE_KEY = os.environ.get("sb_publishable_5rCCiy44SCzNy9O_GooSYA_g2Q3BQn1")

# ローカルのStreamlitでは .streamlit/secrets.toml から取得できるようにする。
if not SUPABASE_URL:
    SUPABASE_URL = st.secrets.get("SUPABASE_URL")

if not SUPABASE_KEY:
    SUPABASE_KEY = st.secrets.get("SUPABASE_KEY")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL が設定されていません。")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY が設定されていません。")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ============================================================
# 社員
# ============================================================
def get_employees():
    response = supabase.table("employees").select("*").order("id").execute()
    return response.data


def add_employee(name, employment_type, gender, experience_years, group_name, can_leader, max_consecutive_days):
    data = {
        "name": name,
        "employment_type": employment_type,
        "gender": gender,
        "experience_years": experience_years,
        "group_name": group_name,
        "can_leader": int(can_leader),
        "max_consecutive_days": max_consecutive_days,
    }

    response = supabase.table("employees").insert(data).select("id").execute()
    return response.data[0]["id"]


def update_employee(employee_id, name, employment_type, gender, experience_years, group_name, can_leader, max_consecutive_days):
    data = {
        "name": name,
        "employment_type": employment_type,
        "gender": gender,
        "experience_years": experience_years,
        "group_name": group_name,
        "can_leader": int(can_leader),
        "max_consecutive_days": max_consecutive_days,
    }

    supabase.table("employees").update(data).eq("id", employee_id).execute()


def delete_employee(employee_id):
    # DB側にも外部キーがありますが、明示的に関連データを削除して
    # SQLite版と同じ動作を保つ。
    for table in [
        "employee_shift_limits",
        "requests",
        "generated_shifts",
        "initial_carryover_settings",
    ]:
        supabase.table(table).delete().eq("employee_id", employee_id).execute()

    supabase.table("employees").delete().eq("id", employee_id).execute()


# ============================================================
# 希望
# ============================================================
def get_requests(employee_id, year, month):
    response = (
        supabase.table("requests")
        .select("*")
        .eq("employee_id", employee_id)
        .eq("year", year)
        .eq("month", month)
        .order("day")
        .execute()
    )
    return response.data


def save_request(employee_id, year, month, day, request_type):
    data = {
        "employee_id": employee_id,
        "year": year,
        "month": month,
        "day": day,
        "request_type": request_type,
    }

    supabase.table("requests").upsert(
        data,
        on_conflict="employee_id,year,month,day",
    ).execute()


def delete_request(employee_id, year, month, day):
    (
        supabase.table("requests")
        .delete()
        .eq("employee_id", employee_id)
        .eq("year", year)
        .eq("month", month)
        .eq("day", day)
        .execute()
    )


# ============================================================
# 個人勤務条件
# ============================================================
def get_shift_limits(employee_id):
    response = (
        supabase.table("employee_shift_limits")
        .select("*")
        .eq("employee_id", employee_id)
        .execute()
    )
    return response.data


def save_shift_limit(employee_id, shift_type, min_count, max_count):
    data = {
        "employee_id": employee_id,
        "shift_type": shift_type,
        "min_count": min_count,
        "max_count": max_count,
    }

    supabase.table("employee_shift_limits").upsert(
        data,
        on_conflict="employee_id,shift_type",
    ).execute()


# ============================================================
# 人数条件
# ============================================================
def get_staffing_conditions():
    response = supabase.table("staffing_conditions").select("*").execute()
    return response.data


def save_staffing_condition(condition_type, group_name, shift_type, required_count):
    data = {
        "condition_type": condition_type,
        "group_name": group_name,
        "shift_type": shift_type,
        "required_count": required_count,
    }

    supabase.table("staffing_conditions").upsert(
        data,
        on_conflict="condition_type,group_name,shift_type",
    ).execute()


# ============================================================
# 経験年数条件
# ============================================================
def get_experience_conditions():
    response = (
        supabase.table("experience_conditions")
        .select("*")
        .order("min_experience")
        .execute()
    )
    return response.data


def save_experience_condition(condition_type, group_name, min_experience, required_count):
    data = {
        "condition_type": condition_type,
        "group_name": group_name,
        "min_experience": min_experience,
        "required_count": required_count,
    }

    supabase.table("experience_conditions").upsert(
        data,
        on_conflict="condition_type,group_name,min_experience",
    ).execute()


# ============================================================
# 生成済みシフト
# ============================================================
def save_generated_shifts(employees, year, month, result, days_in_month):
    (
        supabase.table("generated_shifts")
        .delete()
        .eq("year", year)
        .eq("month", month)
        .execute()
    )

    rows = []

    for employee in employees:
        name = employee["name"]
        if name not in result:
            continue

        for day_index, shift_name in enumerate(result[name]):
            if day_index >= days_in_month:
                break

            rows.append(
                {
                    "employee_id": employee["id"],
                    "year": year,
                    "month": month,
                    "day": day_index + 1,
                    "shift_type": shift_name,
                }
            )

    if rows:
        supabase.table("generated_shifts").insert(rows).execute()


# ============================================================
# 開始前（前月末）の勤務状態
# ============================================================
def get_initial_carryover_setting(employee_id):
    response = (
        supabase.table("initial_carryover_settings")
        .select("last_shift_type")
        .eq("employee_id", employee_id)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]["last_shift_type"]


def save_initial_carryover_setting(employee_id, last_shift_type):
    data = {
        "employee_id": employee_id,
        "last_shift_type": last_shift_type,
    }

    supabase.table("initial_carryover_settings").upsert(
        data,
        on_conflict="employee_id",
    ).execute()


def has_month_shift_data(year, month):
    response = (
        supabase.table("generated_shifts")
        .select("id")
        .eq("year", year)
        .eq("month", month)
        .limit(1)
        .execute()
    )

    return len(response.data) > 0


def get_last_day_shift(employee_id, year, month):
    import calendar

    days_in_month = calendar.monthrange(year, month)[1]

    response = (
        supabase.table("generated_shifts")
        .select("shift_type")
        .eq("employee_id", employee_id)
        .eq("year", year)
        .eq("month", month)
        .eq("day", days_in_month)
        .limit(1)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]["shift_type"]
