import streamlit as st
import sqlite3
import calendar
from datetime import date
from io import BytesIO
import pandas as pd
import holidays
from ortools.sat.python import cp_model

# ============================================================
# 基本設定
# ============================================================
st.set_page_config(page_title="シフト生成システム", layout="wide")

DATABASE_NAME = "shift_system.db"

# ============================================================
# 勤務コード
# ============================================================
OFF = 0
PAID = 1
DAY = 2
LEADER = 3
HALF = 4
EVENING = 5
NIGHT = 6

SHIFT_NAMES = {
    OFF: "公休",
    PAID: "有休",
    DAY: "日勤",
    LEADER: "リーダー",
    HALF: "半日",
    EVENING: "準夜",
    NIGHT: "深夜",
}

SHIFT_SHORT_NAMES = {
    "指定なし": "―",
    "公休": "公",
    "有休": "有",
    "日勤": "日",
    "リーダー": "L",
    "半日": "半",
    "準夜": "準",
    "深夜": "深",
}

REQUEST_OPTIONS = ["指定なし", "公休", "有休", "日勤", "リーダー", "半日", "準夜", "深夜"]

REQUEST_CODE_MAP = {
    "公休": OFF,
    "有休": PAID,
    "日勤": DAY,
    "リーダー": LEADER,
    "半日": HALF,
    "準夜": EVENING,
    "深夜": NIGHT,
}


# ============================================================
# データベース接続
# ============================================================
def get_connection():
    connection = sqlite3.connect(DATABASE_NAME, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


# ============================================================
# データベース初期化
# ============================================================
def init_database():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            employment_type TEXT,
            gender TEXT,
            experience_years INTEGER DEFAULT 0,
            group_name TEXT DEFAULT '指定なし',
            can_leader INTEGER DEFAULT 0,
            max_consecutive_days INTEGER DEFAULT 5
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS employee_shift_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            shift_type TEXT,
            min_count INTEGER DEFAULT 0,
            max_count INTEGER DEFAULT 31,
            UNIQUE(employee_id, shift_type)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            year INTEGER,
            month INTEGER,
            day INTEGER,
            request_type TEXT,
            UNIQUE(employee_id, year, month, day)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS staffing_conditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_type TEXT,
            group_name TEXT,
            shift_type TEXT,
            required_count INTEGER DEFAULT 0,
            UNIQUE(condition_type, group_name, shift_type)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS experience_conditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_type TEXT,
            group_name TEXT,
            min_experience INTEGER,
            required_count INTEGER DEFAULT 0,
            UNIQUE(condition_type, group_name, min_experience)
        )
        """
    )

    connection.commit()
    connection.close()


# ============================================================
# 社員取得・追加・更新・削除
# ============================================================
def get_employees():
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM employees ORDER BY id")
    employees = cursor.fetchall()
    connection.close()
    return employees


def add_employee(name, employment_type, gender, experience_years, group_name, can_leader, max_consecutive_days):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        INSERT INTO employees
            (name, employment_type, gender, experience_years, group_name, can_leader, max_consecutive_days)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name, employment_type, gender, experience_years, group_name, int(can_leader), max_consecutive_days),
    )
    connection.commit()
    connection.close()


def update_employee(employee_id, name, employment_type, gender, experience_years, group_name, can_leader, max_consecutive_days):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        UPDATE employees
        SET name = ?, employment_type = ?, gender = ?, experience_years = ?,
            group_name = ?, can_leader = ?, max_consecutive_days = ?
        WHERE id = ?
        """,
        (name, employment_type, gender, experience_years, group_name, int(can_leader), max_consecutive_days, employee_id),
    )
    connection.commit()
    connection.close()


def delete_employee(employee_id):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("DELETE FROM employee_shift_limits WHERE employee_id = ?", (employee_id,))
    cursor.execute("DELETE FROM requests WHERE employee_id = ?", (employee_id,))
    cursor.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
    connection.commit()
    connection.close()


# ============================================================
# 希望
# ============================================================
def get_requests(employee_id, year, month):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT * FROM requests WHERE employee_id = ? AND year = ? AND month = ? ORDER BY day",
        (employee_id, year, month),
    )
    requests = cursor.fetchall()
    connection.close()
    return requests


def save_request(employee_id, year, month, day, request_type):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        INSERT INTO requests (employee_id, year, month, day, request_type)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(employee_id, year, month, day)
        DO UPDATE SET request_type = excluded.request_type
        """,
        (employee_id, year, month, day, request_type),
    )
    connection.commit()
    connection.close()


def delete_request(employee_id, year, month, day):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "DELETE FROM requests WHERE employee_id = ? AND year = ? AND month = ? AND day = ?",
        (employee_id, year, month, day),
    )
    connection.commit()
    connection.close()


# ============================================================
# 個人勤務条件
# ============================================================
def get_shift_limits(employee_id):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM employee_shift_limits WHERE employee_id = ?", (employee_id,))
    limits = cursor.fetchall()
    connection.close()
    return limits


def save_shift_limit(employee_id, shift_type, min_count, max_count):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        INSERT INTO employee_shift_limits (employee_id, shift_type, min_count, max_count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(employee_id, shift_type)
        DO UPDATE SET min_count = excluded.min_count, max_count = excluded.max_count
        """,
        (employee_id, shift_type, min_count, max_count),
    )
    connection.commit()
    connection.close()


# ============================================================
# 人数条件
# ============================================================
def get_staffing_conditions():
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM staffing_conditions")
    conditions = cursor.fetchall()
    connection.close()
    return conditions


def save_staffing_condition(condition_type, group_name, shift_type, required_count):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        INSERT INTO staffing_conditions (condition_type, group_name, shift_type, required_count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(condition_type, group_name, shift_type)
        DO UPDATE SET required_count = excluded.required_count
        """,
        (condition_type, group_name, shift_type, required_count),
    )
    connection.commit()
    connection.close()


# ============================================================
# 経験年数条件
# ============================================================
def get_experience_conditions():
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM experience_conditions ORDER BY min_experience")
    conditions = cursor.fetchall()
    connection.close()
    return conditions


def save_experience_condition(condition_type, group_name, min_experience, required_count):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        INSERT INTO experience_conditions (condition_type, group_name, min_experience, required_count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(condition_type, group_name, min_experience)
        DO UPDATE SET required_count = excluded.required_count
        """,
        (condition_type, group_name, min_experience, required_count),
    )
    connection.commit()
    connection.close()


# ============================================================
# 日付タイプ
# ============================================================
def get_day_type(year, month, day):
    target_date = date(year, month, day)
    japanese_holidays = holidays.Japan()

    if target_date.weekday() == 6 or target_date in japanese_holidays:
        return "日祝"
    if target_date.weekday() == 2:
        return "水曜"
    if target_date.weekday() == 5:
        return "土曜"
    return "通常"


# ============================================================
# 条件辞書
# ============================================================
def create_condition_dictionary():
    conditions = get_staffing_conditions()
    result = {}
    for condition in conditions:
        key = (condition["condition_type"], condition["group_name"], condition["shift_type"])
        result[key] = condition["required_count"]
    return result


def create_experience_dictionary():
    conditions = get_experience_conditions()
    result = {}
    for condition in conditions:
        key = (condition["condition_type"], condition["group_name"], condition["min_experience"])
        result[key] = condition["required_count"]
    return result


def get_group_employee_indexes(employees, group_name):
    if group_name == "全体":
        return list(range(len(employees)))
    return [index for index, employee in enumerate(employees) if employee["group_name"] == group_name]


# ============================================================
# シフト生成前の条件チェック
# ============================================================
def check_generation_conditions(employees, year, month):
    problems = []
    days_in_month = calendar.monthrange(year, month)[1]
    conditions = create_condition_dictionary()
    experience_conditions = create_experience_dictionary()

    if len(employees) == 0:
        problems.append("社員が1人も登録されていません。")
        return problems

    # 個人条件 min > max
    for employee in employees:
        limits = get_shift_limits(employee["id"])
        for limit in limits:
            if limit["min_count"] > limit["max_count"]:
                problems.append(
                    f"{employee['name']}さんの{limit['shift_type']}について、"
                    f"最低回数が最大回数を超えています。"
                )

    for day in range(1, days_in_month + 1):
        day_type = get_day_type(year, month, day)

        for group_name in ["A", "B", "全体"]:
            indexes = get_group_employee_indexes(employees, group_name)
            employee_count = len(indexes)

            # 日勤
            required_day = conditions.get((day_type, group_name, "日勤"), 0)
            if required_day > employee_count:
                problems.append(
                    f"{day}日（{day_type}）のグループ{group_name}で、"
                    f"日勤が{required_day}人必要ですが、対象社員は{employee_count}人しかいません。"
                )

            # リーダー
            required_leader = conditions.get((day_type, group_name, "リーダー"), 0)
            capable_leaders = sum(1 for index in indexes if employees[index]["can_leader"] == 1)
            if required_leader > capable_leaders:
                problems.append(
                    f"{day}日（{day_type}）のグループ{group_name}で、"
                    f"リーダーが{required_leader}人必要ですが、リーダー可能者は{capable_leaders}人しかいません。"
                )

            # 半日
            required_half = conditions.get((day_type, group_name, "半日"), 0)
            if required_half > 0 and day_type not in ["水曜", "土曜"]:
                problems.append(
                    f"{day}日（{day_type}）に半日勤務{required_half}人が設定されていますが、"
                    f"半日は水曜・土曜のみ可能です。"
                )
            if required_half > employee_count:
                problems.append(
                    f"{day}日（{day_type}）のグループ{group_name}で、"
                    f"半日が{required_half}人必要ですが、対象社員は{employee_count}人です。"
                )

            # 準夜
            required_evening = conditions.get((day_type, group_name, "準夜"), 0)
            if required_evening > 0 and day >= days_in_month:
                problems.append(
                    f"{day}日（{day_type}）に準夜{required_evening}人が必要ですが、"
                    f"準夜は翌日の深夜勤務が必要なため、月末には設定できません。"
                )

            # 深夜
            required_night = conditions.get((day_type, group_name, "深夜"), 0)
            if required_night > 0 and day >= days_in_month:
                problems.append(
                    f"{day}日（{day_type}）に深夜{required_night}人が必要ですが、"
                    f"深夜の翌日は公休が必要なため、月末には設定できません。"
                )
            # ★修正: 1日目は前日の準夜が存在しないため深夜は不可能（従来ここが未チェックだった）
            if required_night > 0 and day == 1:
                problems.append(
                    f"1日（{day_type}）に深夜{required_night}人の条件が設定されていますが、"
                    f"前日の準夜勤務が存在しないため、1日に深夜勤務を割り当てることはできません。"
                )

            # 経験年数
            for key in list(experience_conditions.keys()):
                condition_day_type, condition_group, min_experience = key
                if condition_day_type != day_type or condition_group != group_name:
                    continue
                required_experience = experience_conditions[key]
                eligible_count = sum(1 for index in indexes if employees[index]["experience_years"] >= min_experience)
                if required_experience > eligible_count:
                    problems.append(
                        f"{day}日（{day_type}）のグループ{group_name}で、"
                        f"経験{min_experience}年以上が{required_experience}人必要ですが、"
                        f"該当者は{eligible_count}人です。"
                    )

    # 希望勤務と基本ルール
    for employee in employees:
        requests = get_requests(employee["id"], year, month)
        request_dictionary = {request["day"]: request["request_type"] for request in requests}

        for day, request_type in request_dictionary.items():
            if request_type == "半日":
                day_type = get_day_type(year, month, day)
                if day_type not in ["水曜", "土曜"]:
                    problems.append(
                        f"{employee['name']}さんの{day}日の半日希望は、水曜・土曜以外なので設定できません。"
                    )
            if request_type == "準夜":
                if day >= days_in_month:
                    problems.append(
                        f"{employee['name']}さんの{day}日の準夜希望は、翌日の深夜勤務が必要なため設定できません。"
                    )
            if request_type == "深夜":
                if day >= days_in_month:
                    problems.append(
                        f"{employee['name']}さんの{day}日の深夜希望は、翌日公休が必要なため設定できません。"
                    )
                if day == 1:
                    problems.append(
                        f"{employee['name']}さんの1日の深夜希望は、前日の準夜勤務が必要なため設定できません。"
                    )

    return problems


# ============================================================
# OR-Tools シフト生成
# 生成できない場合は、原因となっている条件を特定して返す
# （OR-ToolsのAddAssumptions / SufficientAssumptionsForInfeasibilityを利用）
# ============================================================
def generate_shift(employees, year, month):
    days_in_month = calendar.monthrange(year, month)[1]
    model = cp_model.CpModel()
    employee_count = len(employees)
    shift_types = [OFF, PAID, DAY, LEADER, HALF, EVENING, NIGHT]

    # --------------------------------------------------------
    # 変数
    # --------------------------------------------------------
    shifts = {}
    for e in range(employee_count):
        for d in range(days_in_month):
            for s in shift_types:
                shifts[e, d, s] = model.NewBoolVar(f"shift_{e}_{d}_{s}")

    # ========================================================
    # 1日1勤務（構造上のハード制約）
    # ========================================================
    for e in range(employee_count):
        for d in range(days_in_month):
            model.AddExactlyOne(shifts[e, d, s] for s in shift_types)

    # ========================================================
    # 半日は水曜・土曜のみ（構造上のハード制約）
    # ========================================================
    for d in range(days_in_month):
        day_number = d + 1
        day_type = get_day_type(year, month, day_number)
        if day_type not in ["水曜", "土曜"]:
            for e in range(employee_count):
                model.Add(shifts[e, d, HALF] == 0)

    # ========================================================
    # リーダー可能者のみ（構造上のハード制約）
    # ========================================================
    for e, employee in enumerate(employees):
        if employee["can_leader"] == 0:
            for d in range(days_in_month):
                model.Add(shifts[e, d, LEADER] == 0)

    # ========================================================
    # 準夜 → 深夜 → 公休（構造上のハード制約）
    # ========================================================
    for e in range(employee_count):
        for d in range(days_in_month):
            if d + 1 >= days_in_month:
                model.Add(shifts[e, d, EVENING] == 0)
            else:
                model.Add(shifts[e, d, EVENING] == shifts[e, d + 1, NIGHT])

            if d == 0:
                model.Add(shifts[e, d, NIGHT] == 0)
            else:
                model.Add(shifts[e, d, NIGHT] == shifts[e, d - 1, EVENING])

            if d + 1 < days_in_month:
                model.AddImplication(shifts[e, d, NIGHT], shifts[e, d + 1, OFF])
            else:
                model.Add(shifts[e, d, NIGHT] == 0)

    # --------------------------------------------------------
    # ここから先は「ユーザーが設定した条件」。
    # 矛盾の原因を特定できるよう、それぞれに assumption（目印）を付けて
    # OnlyEnforceIf で紐付ける。
    # --------------------------------------------------------
    assumption_literals = []
    assumption_descriptions = {}

    def add_assumption(description):
        indicator = model.NewBoolVar(f"assume_{len(assumption_literals)}")
        assumption_literals.append(indicator)
        assumption_descriptions[indicator.Index()] = description
        return indicator

    # ========================================================
    # 希望条件
    # ========================================================
    for e, employee in enumerate(employees):
        requests = get_requests(employee["id"], year, month)
        for request in requests:
            d = request["day"] - 1
            request_type = request["request_type"]
            code = REQUEST_CODE_MAP.get(request_type)
            if code is None:
                continue
            indicator = add_assumption(
                f"{employee['name']}さんの{request['day']}日の希望（{request_type}）"
            )
            model.Add(shifts[e, d, code] == 1).OnlyEnforceIf(indicator)

    # ========================================================
    # 個人勤務回数
    # ========================================================
    for e, employee in enumerate(employees):
        limits = get_shift_limits(employee["id"])
        limit_dictionary = {limit["shift_type"]: (limit["min_count"], limit["max_count"]) for limit in limits}

        shift_code = {"日勤": DAY, "リーダー": LEADER, "半日": HALF, "準夜": EVENING}
        for shift_name, code in shift_code.items():
            if shift_name not in limit_dictionary:
                continue
            minimum, maximum = limit_dictionary[shift_name]
            total_count = sum(shifts[e, d, code] for d in range(days_in_month))

            min_indicator = add_assumption(
                f"{employee['name']}さんの{shift_name}の最低回数条件（{minimum}回以上）"
            )
            model.Add(total_count >= minimum).OnlyEnforceIf(min_indicator)

            max_indicator = add_assumption(
                f"{employee['name']}さんの{shift_name}の最大回数条件（{maximum}回以下）"
            )
            model.Add(total_count <= maximum).OnlyEnforceIf(max_indicator)

    # ========================================================
    # 最大連勤
    # ========================================================
    for e, employee in enumerate(employees):
        max_consecutive = employee["max_consecutive_days"]
        work_codes = [DAY, LEADER, HALF, EVENING, NIGHT]
        if days_in_month - max_consecutive <= 0:
            continue

        indicator = add_assumption(
            f"{employee['name']}さんの最大連勤日数条件（{max_consecutive}日以内）"
        )
        for start_day in range(days_in_month - max_consecutive):
            work_list = []
            for d in range(start_day, start_day + max_consecutive + 1):
                work_list.append(sum(shifts[e, d, code] for code in work_codes))
            model.Add(sum(work_list) <= max_consecutive).OnlyEnforceIf(indicator)

    # ========================================================
    # 人数条件
    # ========================================================
    conditions = create_condition_dictionary()
    for d in range(days_in_month):
        day_number = d + 1
        condition_type = get_day_type(year, month, day_number)

        for group_name in ["A", "B", "全体"]:
            group_employees = get_group_employee_indexes(employees, group_name)

            required_day = conditions.get((condition_type, group_name, "日勤"), 0)
            if required_day > 0:
                indicator = add_assumption(
                    f"{day_number}日（{condition_type}）のグループ{group_name}の日勤人数条件（{required_day}人以上）"
                )
                model.Add(
                    sum(shifts[i, d, DAY] + shifts[i, d, LEADER] for i in group_employees) >= required_day
                ).OnlyEnforceIf(indicator)

            required_leader = conditions.get((condition_type, group_name, "リーダー"), 0)
            if required_leader > 0:
                indicator = add_assumption(
                    f"{day_number}日（{condition_type}）のグループ{group_name}のリーダー人数条件（{required_leader}人以上）"
                )
                model.Add(
                    sum(shifts[i, d, LEADER] for i in group_employees) >= required_leader
                ).OnlyEnforceIf(indicator)

            required_half = conditions.get((condition_type, group_name, "半日"), 0)
            if required_half > 0:
                indicator = add_assumption(
                    f"{day_number}日（{condition_type}）のグループ{group_name}の半日人数条件（{required_half}人以上）"
                )
                model.Add(
                    sum(shifts[i, d, HALF] for i in group_employees) >= required_half
                ).OnlyEnforceIf(indicator)

            required_evening = conditions.get((condition_type, group_name, "準夜"), 0)
            if required_evening > 0:
                indicator = add_assumption(
                    f"{day_number}日（{condition_type}）のグループ{group_name}の準夜人数条件（{required_evening}人以上）"
                )
                model.Add(
                    sum(shifts[i, d, EVENING] for i in group_employees) >= required_evening
                ).OnlyEnforceIf(indicator)

            required_night = conditions.get((condition_type, group_name, "深夜"), 0)
            if required_night > 0:
                indicator = add_assumption(
                    f"{day_number}日（{condition_type}）のグループ{group_name}の深夜人数条件（{required_night}人以上）"
                )
                model.Add(
                    sum(shifts[i, d, NIGHT] for i in group_employees) >= required_night
                ).OnlyEnforceIf(indicator)

    # ========================================================
    # 経験年数条件
    # ========================================================
    experience_conditions = create_experience_dictionary()
    for d in range(days_in_month):
        day_number = d + 1
        day_type = get_day_type(year, month, day_number)

        for group_name in ["A", "B", "全体"]:
            indexes = get_group_employee_indexes(employees, group_name)

            for key, required_count in experience_conditions.items():
                condition_day_type, condition_group, min_experience = key
                if condition_day_type != day_type or condition_group != group_name:
                    continue
                if required_count <= 0:
                    continue

                qualified_employees = [i for i in indexes if employees[i]["experience_years"] >= min_experience]
                indicator = add_assumption(
                    f"{day_number}日（{day_type}）のグループ{group_name}で"
                    f"経験{min_experience}年以上が{required_count}人以上勤務する条件"
                )
                for_employee = []
                for i in qualified_employees:
                    for_employee.append(
                        shifts[i, d, DAY] + shifts[i, d, LEADER] + shifts[i, d, HALF]
                        + shifts[i, d, EVENING] + shifts[i, d, NIGHT]
                    )
                model.Add(sum(for_employee) >= required_count).OnlyEnforceIf(indicator)

    # ========================================================
    # 求解
    # ========================================================
    model.AddAssumptions(assumption_literals)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        result = {}
        for e, employee in enumerate(employees):
            result[employee["name"]] = []
            for d in range(days_in_month):
                for s in shift_types:
                    if solver.Value(shifts[e, d, s]) == 1:
                        result[employee["name"]].append(SHIFT_NAMES[s])
                        break
        return result, []

    if status == cp_model.INFEASIBLE:
        conflicting_indexes = solver.SufficientAssumptionsForInfeasibility()
        causes = [assumption_descriptions[index] for index in conflicting_indexes if index in assumption_descriptions]
        # 重複を除きつつ順序維持
        seen = set()
        unique_causes = []
        for cause in causes:
            if cause not in seen:
                seen.add(cause)
                unique_causes.append(cause)
        return None, unique_causes

    # タイムアウト等でどちらとも判定できなかった場合
    return None, []


# ============================================================
# Excel
# ============================================================
def create_excel(result, year, month):
    days_in_month = calendar.monthrange(year, month)[1]
    weekday_names = ["月", "火", "水", "木", "金", "土", "日"]
    columns = []
    for day in range(1, days_in_month + 1):
        weekday = calendar.weekday(year, month, day)
        columns.append(f"{day}({weekday_names[weekday]})")

    dataframe = pd.DataFrame(result).T
    dataframe.columns = columns
    dataframe.index.name = "社員名"

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dataframe.to_excel(writer, sheet_name="シフト")
    return output.getvalue()


# ============================================================
# DB初期化
# ============================================================
init_database()

# ============================================================
# タイトル
# ============================================================
st.title("シフト自動生成システム")

# ============================================================
# メニュー
# ============================================================
menu = st.sidebar.radio(
    "メニュー",
    ["社員管理", "個人勤務条件", "希望休・希望勤務", "人数条件", "シフト生成"],
)

# ============================================================
# 社員管理
# ============================================================
if menu == "社員管理":
    st.header("社員管理")

    with st.form("add_employee_form"):
        name = st.text_input("名前")
        col1, col2, col3 = st.columns(3)
        with col1:
            employment_type = st.selectbox("雇用形態", ["正社員", "準社員"])
        with col2:
            gender = st.selectbox("性別", ["男性", "女性", "その他"])
        with col3:
            group_name = st.selectbox("所属グループ", ["A", "B", "指定なし"])

        experience_years = st.number_input("経験年数", min_value=0, max_value=50, value=0)
        can_leader = st.checkbox("リーダー可能")
        max_consecutive_days = st.number_input("最大連勤日数", min_value=1, max_value=31, value=5)

        submitted = st.form_submit_button("社員を追加")
        if submitted:
            if name.strip() == "":
                st.error("名前を入力してください。")
            else:
                add_employee(name, employment_type, gender, experience_years, group_name, can_leader, max_consecutive_days)
                st.success(f"{name}さんを登録しました。")
                st.rerun()

    st.divider()
    st.subheader("登録済み社員")

    employees = get_employees()
    for employee in employees:
        with st.expander(employee["name"]):
            edit_name = st.text_input("名前", employee["name"], key=f"name_{employee['id']}")
            edit_employment = st.selectbox(
                "雇用形態", ["正社員", "準社員"],
                index=["正社員", "準社員"].index(employee["employment_type"]) if employee["employment_type"] in ["正社員", "準社員"] else 0,
                key=f"employment_{employee['id']}",
            )
            edit_gender = st.selectbox(
                "性別", ["男性", "女性", "その他"],
                index=["男性", "女性", "その他"].index(employee["gender"]) if employee["gender"] in ["男性", "女性", "その他"] else 0,
                key=f"gender_{employee['id']}",
            )
            edit_experience = st.number_input(
                "経験年数", min_value=0, max_value=50, value=int(employee["experience_years"]),
                key=f"experience_{employee['id']}",
            )
            edit_group = st.selectbox(
                "所属グループ", ["A", "B", "指定なし"],
                index=["A", "B", "指定なし"].index(employee["group_name"]) if employee["group_name"] in ["A", "B", "指定なし"] else 2,
                key=f"group_{employee['id']}",
            )
            edit_leader = st.checkbox("リーダー可能", value=bool(employee["can_leader"]), key=f"leader_{employee['id']}")
            edit_max_consecutive = st.number_input(
                "最大連勤", min_value=1, max_value=31, value=int(employee["max_consecutive_days"]),
                key=f"max_{employee['id']}",
            )

            col1, col2 = st.columns(2)
            with col1:
                if st.button("更新", key=f"update_{employee['id']}"):
                    update_employee(
                        employee["id"], edit_name, edit_employment, edit_gender,
                        edit_experience, edit_group, edit_leader, edit_max_consecutive,
                    )
                    st.success("更新しました。")
                    st.rerun()
            with col2:
                if st.button("削除", key=f"delete_{employee['id']}"):
                    delete_employee(employee["id"])
                    st.rerun()

# ============================================================
# 個人勤務条件
# ============================================================
elif menu == "個人勤務条件":
    st.header("個人ごとの勤務回数条件")

    employees = get_employees()

    if len(employees) == 0:
        st.info("先に社員を登録してください。")

    else:
        employee_names = {
            employee["name"]: employee
            for employee in employees
        }

        selected_name = st.selectbox(
            "社員",
            list(employee_names.keys())
        )

        employee = employee_names[selected_name]

        # ----------------------------------------------------
        # 保存されている勤務条件を取得
        # ----------------------------------------------------
        limits = get_shift_limits(employee["id"])

        limit_dictionary = {
            limit["shift_type"]: (
                limit["min_count"],
                limit["max_count"]
            )
            for limit in limits
        }

        shift_limit_types = [
            "日勤",
            "リーダー",
            "半日",
            "準夜"
        ]

        # ====================================================
        # 現在保存されている条件を表示
        # ====================================================
        st.subheader("現在の勤務条件")

        condition_rows = []

        for shift_type in shift_limit_types:
            minimum, maximum = limit_dictionary.get(
                shift_type,
                (0, 31)
            )

            condition_rows.append({
                "勤務種類": shift_type,
                "最低回数": minimum,
                "最大回数": maximum
            })

        condition_dataframe = pd.DataFrame(condition_rows)

        st.dataframe(
            condition_dataframe,
            use_container_width=True,
            hide_index=True
        )

        st.divider()

        # ====================================================
        # 条件編集
        # ====================================================
        st.subheader("勤務条件を変更")

        with st.form("shift_limits_form"):

            values = {}

            for shift_type in shift_limit_types:

                current = limit_dictionary.get(
                    shift_type,
                    (0, 31)
                )

                col1, col2 = st.columns(2)

                with col1:
                    minimum = st.number_input(
                        f"{shift_type} 最低回数",
                        min_value=0,
                        max_value=31,
                        value=int(current[0]),
                        key=f"min_{employee['id']}_{shift_type}",
                    )

                with col2:
                    maximum = st.number_input(
                        f"{shift_type} 最大回数",
                        min_value=0,
                        max_value=31,
                        value=int(current[1]),
                        key=f"max_{employee['id']}_{shift_type}",
                    )

                values[shift_type] = (
                    minimum,
                    maximum
                )

            submitted = st.form_submit_button("保存")

            if submitted:

                has_error = False

                for shift_type, value in values.items():

                    minimum, maximum = value

                    if minimum > maximum:

                        st.error(
                            f"{shift_type}の最低回数が最大回数を超えています。"
                        )

                        has_error = True

                if not has_error:

                    for shift_type, value in values.items():

                        minimum, maximum = value

                        save_shift_limit(
                            employee["id"],
                            shift_type,
                            minimum,
                            maximum
                        )

                    st.success(
                        "勤務条件を保存しました。"
                    )

                    # 保存後に画面を更新
                    st.rerun()
                    
# ============================================================
# 希望入力
# ============================================================
elif menu == "希望休・希望勤務":
    st.header("希望休・希望勤務")

    employees = get_employees()
    if len(employees) == 0:
        st.info("先に社員を登録してください。")
    else:
        col1, col2 = st.columns(2)
        with col1:
            selected_year = st.number_input("対象年", min_value=2026, max_value=2100, value=2026)
        with col2:
            selected_month = st.number_input("対象月", min_value=1, max_value=12, value=9)

        days_in_month = calendar.monthrange(selected_year, selected_month)[1]
        weekday_names = ["月", "火", "水", "木", "金", "土", "日"]

        rows = []
        for employee in employees:
            requests = get_requests(employee["id"], selected_year, selected_month)
            request_dictionary = {request["day"]: request["request_type"] for request in requests}

            row = {"社員名": employee["name"]}
            for day in range(1, days_in_month + 1):
                weekday = calendar.weekday(selected_year, selected_month, day)
                column_name = f"{day}日({weekday_names[weekday]})"
                value = request_dictionary.get(day, "指定なし")
                row[column_name] = SHIFT_SHORT_NAMES[value]
            rows.append(row)

        dataframe = pd.DataFrame(rows)

        st.write("記号：― 指定なし / 公 公休 / 有 有休 / 日 日勤 / L リーダー / 半 半日 / 準 準夜 / 深 深夜")

        column_config = {"社員名": st.column_config.TextColumn("社員名", disabled=True, width="medium")}
        for column in dataframe.columns[1:]:
            column_config[column] = st.column_config.SelectboxColumn(
                column, options=list(SHIFT_SHORT_NAMES.values()), width="medium"
            )

        edited_dataframe = st.data_editor(
            dataframe, column_config=column_config, hide_index=True,
            use_container_width=True, num_rows="fixed",
            key=f"requests_{selected_year}_{selected_month}",
        )

        if st.button("全社員の希望を保存", type="primary"):
            reverse_short_names = {value: key for key, value in SHIFT_SHORT_NAMES.items()}
            for row_index, employee in enumerate(employees):
                for day in range(1, days_in_month + 1):
                    weekday = calendar.weekday(selected_year, selected_month, day)
                    column_name = f"{day}日({weekday_names[weekday]})"
                    short_value = edited_dataframe.iloc[row_index][column_name]
                    request_type = reverse_short_names.get(short_value, "指定なし")

                    if request_type == "指定なし":
                        delete_request(employee["id"], selected_year, selected_month, day)
                    else:
                        save_request(employee["id"], selected_year, selected_month, day, request_type)

            st.success("希望を保存しました。")

# ============================================================
# 人数条件
# ============================================================
elif menu == "人数条件":
    st.header("日別・グループ別人数条件")
    st.info("リーダーは日勤人数にも含まれます。また、「全体」はA・B・指定なしをすべて含みます。")

    condition_types = ["通常", "水曜", "土曜", "日祝"]
    groups = ["A", "B", "全体"]
    shifts = ["日勤", "リーダー", "半日", "準夜", "深夜"]

    existing_conditions = create_condition_dictionary()

    # ========================================================
    # 通常の人数条件
    # ========================================================
    st.subheader("勤務人数条件")
    for condition_type in condition_types:
        with st.expander(f"{condition_type}の条件", expanded=False):
            for group_name in groups:
                st.write(f"グループ{group_name}")
                columns = st.columns(len(shifts))
                values = {}
                for index, shift_type in enumerate(shifts):
                    key = (condition_type, group_name, shift_type)
                    default_value = existing_conditions.get(key, 0)
                    with columns[index]:
                        values[shift_type] = st.number_input(
                            shift_type, min_value=0, max_value=100, value=default_value,
                            key=f"{condition_type}_{group_name}_{shift_type}",
                        )

                if st.button(f"{condition_type} グループ{group_name} 保存", key=f"save_{condition_type}_{group_name}"):
                    for shift_type, value in values.items():
                        save_staffing_condition(condition_type, group_name, shift_type, value)
                    st.success("保存しました。")

    # ========================================================
    # 経験年数条件
    # ========================================================
    st.divider()
    st.subheader("経験年数による人数条件")
    st.write(
        "例：「経験3年以上が2人以上必要」という条件を設定できます。"
        "その日の勤務者のうち、指定した経験年数以上の社員が必要人数以上になるようにします。"
    )

    existing_experience = create_experience_dictionary()

    for condition_type in condition_types:
        # ★修正: f-string内に全角コロンが混入して構文エラーになっていた箇所を修正
        with st.expander(f"{condition_type}：経験年数条件", expanded=False):
            for group_name in groups:
                st.write(f"グループ{group_name}")
                col1, col2, col3 = st.columns(3)
                with col1:
                    min_experience = st.number_input(
                        "必要経験年数", min_value=0, max_value=50, value=3,
                        key=f"exp_year_{condition_type}_{group_name}",
                    )
                with col2:
                    default_required = existing_experience.get((condition_type, group_name, min_experience), 0)
                    required_count = st.number_input(
                        "必要人数", min_value=0, max_value=100, value=default_required,
                        key=f"exp_count_{condition_type}_{group_name}",
                    )
                with col3:
                    st.write("")
                    st.write("")

                if st.button("経験条件を保存", key=f"save_exp_{condition_type}_{group_name}"):
                    save_experience_condition(condition_type, group_name, min_experience, required_count)
                    st.success("保存しました。")

    st.divider()
    st.subheader("保存済み経験年数条件")
    experience_conditions = get_experience_conditions()
    if len(experience_conditions) == 0:
        st.info("経験年数条件はまだ登録されていません。")
    else:
        experience_rows = []
        for condition in experience_conditions:
            experience_rows.append({
                "曜日タイプ": condition["condition_type"],
                "グループ": condition["group_name"],
                "経験年数": f"{condition['min_experience']}年以上",
                "必要人数": condition["required_count"],
            })
        st.dataframe(pd.DataFrame(experience_rows), use_container_width=True, hide_index=True)

# ============================================================
# シフト生成
# ============================================================
elif menu == "シフト生成":
    st.header("シフト自動生成")

    employees = get_employees()
    if len(employees) == 0:
        st.info("先に社員を登録してください。")
    else:
        col1, col2 = st.columns(2)
        with col1:
            selected_year = st.number_input("生成する年", min_value=2026, max_value=2100, value=2026, key="generate_year")
        with col2:
            selected_month = st.number_input("生成する月", min_value=1, max_value=12, value=9, key="generate_month")

        if st.button("シフトを自動生成", type="primary"):
            problems = check_generation_conditions(employees, selected_year, selected_month)

            if len(problems) > 0:
                st.error("シフト生成前の条件チェックで問題が見つかりました。")
                st.subheader("考えられる原因")
                for problem in problems:
                    st.warning(problem)
                st.info("上記を修正してから、もう一度「シフトを自動生成」を押してください。")
            else:
                with st.spinner("シフトを生成しています..."):
                    result, causes = generate_shift(employees, selected_year, selected_month)

                if result is None:
                    st.error("条件をすべて満たすシフトを生成できませんでした。")

                    if causes:
                        # ★追加: OR-Toolsのassumption機能で特定した、実際に矛盾している条件を提示
                        st.subheader("矛盾している具体的な条件")
                        st.write(
                            "以下の条件を同時にすべて満たすことができません。"
                            "いずれかの条件（希望・人数条件・個人回数条件など）を緩めてから、"
                            "もう一度お試しください。"
                        )
                        for cause in causes:
                            st.warning(cause)
                    else:
                        st.subheader("生成できない可能性がある主な原因")
                        st.warning("① 希望休・希望勤務と人数条件が矛盾している")
                        st.warning("② 個人の最低勤務回数と最大連勤条件が矛盾している")
                        st.warning("③ 準夜→深夜→公休の組み合わせによって勤務人数が不足している")
                        st.warning("④ リーダー可能者が不足している")
                        st.warning("⑤ 経験年数条件を満たせる社員が不足している")
                        st.warning("⑥ A・B・全体の人数条件が同時に成立しない")
                        st.info(
                            "特に「希望休を大量に指定した場合」や「最低勤務回数を高く設定した場合」は、"
                            "他の条件と組み合わせた結果として解が存在しなくなることがあります。"
                            "（今回は60秒以内に判定できなかったため、具体的な条件までは特定できませんでした。"
                            "社員数や希望数が多い場合はもう一度お試しください。）"
                        )
                else:
                    st.success("シフトを生成しました。")

                    days_in_month = calendar.monthrange(selected_year, selected_month)[1]
                    weekday_names = ["月", "火", "水", "木", "金", "土", "日"]
                    columns = []
                    for day in range(1, days_in_month + 1):
                        weekday = calendar.weekday(selected_year, selected_month, day)
                        columns.append(f"{day}({weekday_names[weekday]})")

                    dataframe = pd.DataFrame(result).T
                    dataframe.columns = columns
                    st.dataframe(dataframe, use_container_width=True)

                    excel_data = create_excel(result, selected_year, selected_month)
                    st.download_button(
                        "Excelをダウンロード",
                        data=excel_data,
                        file_name=f"shift_{selected_year}_{selected_month}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
