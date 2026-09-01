import streamlit as st
import sqlite3
import calendar
import os
import shutil
from datetime import date
from io import BytesIO
import pandas as pd
import holidays
from ortools.sat.python import cp_model

# ============================================================
# 基本設定
# ============================================================
st.set_page_config(page_title="シフト生成システム", layout="wide")

# ------------------------------------------------------------
# DBファイルの場所。
# 環境変数 SHIFT_DB_PATH が設定されていれば、そちらを使う
# （自前サーバーやDockerで永続ボリュームをマウントしている場合に指定する）。
# 未設定の場合はアプリと同じ場所の shift_system.db を使う
# （Streamlit Community Cloud等、リポジトリからデプロイする環境では
#   再デプロイのたびに消えるため、下の「データのバックアップ」メニューから
#   こまめにバックアップ/復元してください）。
# ------------------------------------------------------------
DATABASE_NAME = os.environ.get("SHIFT_DB_PATH", "shift_system.db")

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
    "指定なし": "無",
    "公休": "公",
    "有休": "有",
    "日勤": "ー",
    "リーダー": "R",
    "半日": "半",
    "準夜": "△",
    "深夜": "〇",
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
            max_count INTEGER DEFAULT NULL,
            UNIQUE(condition_type, group_name, shift_type)
        )
        """
    )
    # 既存DB向けの簡易マイグレーション（max_countカラムが無い場合のみ追加する）
    try:
        cursor.execute("ALTER TABLE staffing_conditions ADD COLUMN max_count INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass

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

    # --------------------------------------------------------
    # 生成済みシフト（月またぎで準夜→深夜の継続を判定するために保存する）
    # --------------------------------------------------------
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS generated_shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            year INTEGER,
            month INTEGER,
            day INTEGER,
            shift_type TEXT,
            UNIQUE(employee_id, year, month, day)
        )
        """
    )

    # --------------------------------------------------------
    # 開始前（前月末）の勤務状態
    #
    # システムで初めてシフトを生成する月や、前月分を生成していない月では
    # generated_shifts に前月末のデータがない。その場合に使う、
    # 社員ごとの手動設定（「準夜」なら1日目は深夜が確定、
    # 「深夜」なら1日目は公休が確定）。
    # --------------------------------------------------------
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS initial_carryover_settings (
            employee_id INTEGER PRIMARY KEY,
            last_shift_type TEXT
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
    new_employee_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return new_employee_id


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
    cursor.execute("DELETE FROM initial_carryover_settings WHERE employee_id = ?", (employee_id,))
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


def save_staffing_condition(condition_type, group_name, shift_type, required_count, max_count=None):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        INSERT INTO staffing_conditions (condition_type, group_name, shift_type, required_count, max_count)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(condition_type, group_name, shift_type)
        DO UPDATE SET required_count = excluded.required_count, max_count = excluded.max_count
        """,
        (condition_type, group_name, shift_type, required_count, max_count),
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


def create_condition_max_dictionary():
    """人数条件の上限値の辞書。上限が設定されていない組み合わせはキーに含まれない"""
    conditions = get_staffing_conditions()
    result = {}
    for condition in conditions:
        if condition["max_count"] is None:
            continue
        key = (condition["condition_type"], condition["group_name"], condition["shift_type"])
        result[key] = condition["max_count"]
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
# 生成済みシフトの保存・取得（月またぎの準夜→深夜継続に使用）
# ============================================================
def save_generated_shifts(employees, year, month, result, days_in_month):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "DELETE FROM generated_shifts WHERE year = ? AND month = ?",
        (year, month),
    )
    for employee in employees:
        name = employee["name"]
        if name not in result:
            continue
        for day_index, shift_name in enumerate(result[name]):
            if day_index >= days_in_month:
                break
            day = day_index + 1
            cursor.execute(
                """
                INSERT INTO generated_shifts (employee_id, year, month, day, shift_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (employee["id"], year, month, day, shift_name),
            )
    connection.commit()
    connection.close()


def get_previous_month(year, month):
    if month == 1:
        return year - 1, 12
    return year, month - 1


# ============================================================
# 生成済みシフトの削除
# ============================================================
def delete_generated_shifts_for_month(year, month):
    """指定した年月の生成済みシフトのみを削除する"""
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "DELETE FROM generated_shifts WHERE year = ? AND month = ?",
        (year, month),
    )
    deleted_count = cursor.rowcount
    connection.commit()
    connection.close()
    return deleted_count


def delete_generated_shifts_from_month(year, month):
    """指定した年月「以降」の生成済みシフトをすべて削除する"""
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        DELETE FROM generated_shifts
        WHERE (year > ?) OR (year = ? AND month >= ?)
        """,
        (year, year, month),
    )
    deleted_count = cursor.rowcount
    connection.commit()
    connection.close()
    return deleted_count


def delete_all_generated_shifts():
    """生成済みシフトをすべて削除する"""
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("DELETE FROM generated_shifts")
    deleted_count = cursor.rowcount
    connection.commit()
    connection.close()
    return deleted_count


def get_generated_shift_months():
    """生成済みシフトが存在する年月の一覧を、古い順に返す"""
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT DISTINCT year, month FROM generated_shifts ORDER BY year, month"
    )
    rows = cursor.fetchall()
    connection.close()
    return [(row["year"], row["month"]) for row in rows]


# ============================================================
# 開始前（前月末）の勤務状態（前月データがない場合に使う手動設定）
# ============================================================
def get_initial_carryover_setting(employee_id):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT last_shift_type FROM initial_carryover_settings WHERE employee_id = ?",
        (employee_id,),
    )
    row = cursor.fetchone()
    connection.close()
    return row["last_shift_type"] if row else None


def save_initial_carryover_setting(employee_id, last_shift_type):
    """last_shift_type は '準夜' / '深夜' / None（指定なし）のいずれか"""
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        INSERT INTO initial_carryover_settings (employee_id, last_shift_type)
        VALUES (?, ?)
        ON CONFLICT(employee_id) DO UPDATE SET last_shift_type = excluded.last_shift_type
        """,
        (employee_id, last_shift_type),
    )
    connection.commit()
    connection.close()


def has_month_shift_data(year, month):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS count FROM generated_shifts WHERE year = ? AND month = ?",
        (year, month),
    )
    row = cursor.fetchone()
    connection.close()
    return row["count"] > 0


def get_last_day_shift(employee_id, year, month):
    """指定した年月の、社員の最終日の確定シフトを取得する（無ければNone）"""
    connection = get_connection()
    cursor = connection.cursor()
    days_in_month = calendar.monthrange(year, month)[1]
    cursor.execute(
        """
        SELECT shift_type FROM generated_shifts
        WHERE employee_id = ? AND year = ? AND month = ? AND day = ?
        """,
        (employee_id, year, month, days_in_month),
    )
    row = cursor.fetchone()
    connection.close()
    return row["shift_type"] if row else None


def get_carryover_map(employees, year, month):
    """
    前月末のシフトから、当月1日に確定させるべきシフトを判定する。
      前月末が「準夜」 → 当月1日は「深夜」が確定（"NIGHT"）
      前月末が「深夜」 → 当月1日は「公休」が確定（"OFF"）
      それ以外／前月のデータがない → 継続なし（None）

    前月分がシステムでまだ生成されていない場合（初めて使う月など）は、
    社員ごとに手動設定した「開始前（前月末）の勤務状態」を代わりに使う。

    戻り値: { employee_id: "NIGHT" | "OFF" | None }
    """
    prev_year, prev_month = get_previous_month(year, month)
    carryover = {}
    for employee in employees:
        last_shift = get_last_day_shift(employee["id"], prev_year, prev_month)
        if last_shift is None:
            last_shift = get_initial_carryover_setting(employee["id"])
        if last_shift == "準夜":
            carryover[employee["id"]] = "NIGHT"
        elif last_shift == "深夜":
            carryover[employee["id"]] = "OFF"
        else:
            carryover[employee["id"]] = None
    return carryover


# ============================================================
# シフト生成前の条件チェック
# ============================================================
def check_generation_conditions(employees, year, month):
    problems = []
    days_in_month = calendar.monthrange(year, month)[1]
    conditions = create_condition_dictionary()
    max_conditions = create_condition_max_dictionary()
    experience_conditions = create_experience_dictionary()

    if len(employees) == 0:
        problems.append("社員が1人も登録されていません。")
        return problems

    # 前月末が準夜／深夜だった社員は、当月1日のシフトが確定する
    carryover = get_carryover_map(employees, year, month)

    # 個人条件 min > max
    for employee in employees:
        limits = get_shift_limits(employee["id"])
        for limit in limits:
            if limit["min_count"] > limit["max_count"]:
                problems.append(
                    f"{employee['name']}さんの{limit['shift_type']}について、"
                    f"最低回数が最大回数を超えています。"
                )

    # ★追加: リーダー人数の上限に関する矛盾チェック（曜日タイプごとに1回）
    for day_type in ["通常", "水曜", "土曜", "日祝"]:
        for group_name in ["A", "B", "全体"]:
            required_leader = conditions.get((day_type, group_name, "リーダー"), 0)
            max_leader = max_conditions.get((day_type, group_name, "リーダー"))
            if max_leader is not None and required_leader > max_leader:
                problems.append(
                    f"{day_type}のグループ{group_name}で、"
                    f"リーダーの最低必要人数（{required_leader}人）が上限（{max_leader}人）を超えています。"
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
            # ★修正: 月末の準夜は翌月1日の深夜として引き継がれるため、以前あった
            # 「月末には準夜を設定できない」という制限は撤廃した。
            required_evening = conditions.get((day_type, group_name, "準夜"), 0)

            # 深夜
            # ★修正: 月末の深夜は翌月1日の公休として引き継がれるため、以前あった
            # 「月末には深夜を設定できない」という制限は撤廃した。
            required_night = conditions.get((day_type, group_name, "深夜"), 0)

            # ★修正: 1日目に深夜を割り当てられるのは、前月末が準夜だった社員だけ
            # （それ以外の社員は1日目に深夜になることは絶対にない）。
            # そのため、1日目の深夜人数条件は「前月末に準夜だった社員の人数」と
            # 正確に比較できる。
            if required_night > 0 and day == 1:
                forced_night_count = sum(
                    1 for index in indexes if carryover.get(employees[index]["id"]) == "NIGHT"
                )
                if required_night > forced_night_count:
                    problems.append(
                        f"1日（{day_type}）のグループ{group_name}で深夜{required_night}人が必要ですが、"
                        f"前月末に準夜勤務だった（＝1日に深夜が確定している）社員は"
                        f"{forced_night_count}人しかいません。"
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
        forced_day1 = carryover.get(employee["id"])

        for day, request_type in request_dictionary.items():
            if request_type == "半日":
                day_type = get_day_type(year, month, day)
                if day_type not in ["水曜", "土曜"]:
                    problems.append(
                        f"{employee['name']}さんの{day}日の半日希望は、水曜・土曜以外なので設定できません。"
                    )
            # ★修正: 準夜・深夜とも、月末は翌月へ引き継がれるため希望として設定可能になった
            if request_type == "深夜" and day == 1:
                # 1日目の深夜が許されるのは、前月末が準夜で確定している社員だけ
                if forced_day1 != "NIGHT":
                    problems.append(
                        f"{employee['name']}さんの1日の深夜希望は、前月末が準夜勤務で確定していないため設定できません"
                        f"（前月のシフトを先に生成するか、社員管理画面で「開始前（前月末）の勤務状態」を"
                        f"「準夜」に設定してください）。"
                    )

        # ★追加: 前月末からの継続で当月1日のシフトが確定している場合、
        # その日に別のシフトが希望されていないか確認する
        day1_request = request_dictionary.get(1)
        if forced_day1 == "NIGHT" and day1_request is not None and day1_request != "深夜":
            problems.append(
                f"{employee['name']}さんは前月末が準夜勤務のため、1日は深夜勤務が確定していますが、"
                f"1日に別の希望（{day1_request}）が設定されています。希望を「深夜」に変更するか、削除してください。"
            )
        if forced_day1 == "OFF" and day1_request is not None and day1_request != "公休":
            problems.append(
                f"{employee['name']}さんは前月末が深夜勤務のため、1日は公休が確定していますが、"
                f"1日に別の希望（{day1_request}）が設定されています。希望を「公休」に変更するか、削除してください。"
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

    # --------------------------------------------------------
    # ここから先は「ユーザーが設定した条件（前月末からの継続を含む）」。
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
    # 準夜 → 深夜 → 公休（月またぎ対応）
    #
    # 月の途中の連結（準夜の翌日は深夜、深夜の翌日は公休）は
    # 構造上のハード制約。
    # 月末の準夜／深夜は「翌月の生成時」に引き継がれるので、ここでは
    # 禁止しない（EVENING/NIGHTを0に固定しない）。
    # 月初(1日目)は、前月末のシフト（carryover）によって深夜が
    # 確定する場合があるので、その場合は assumption 付きで固定する。
    # ========================================================
    carryover = get_carryover_map(employees, year, month)

    for e, employee in enumerate(employees):
        forced_day1 = carryover.get(employee["id"])

        for d in range(days_in_month):
            # 準夜の翌日は深夜（月内のみ。月末は翌月へ引き継ぐため制約しない）
            if d + 1 < days_in_month:
                model.Add(shifts[e, d, EVENING] == shifts[e, d + 1, NIGHT])

            # 深夜は前日が準夜のときのみ
            if d == 0:
                if forced_day1 == "NIGHT":
                    # 前月末が準夜だったので、1日は深夜が確定
                    indicator = add_assumption(
                        f"{employee['name']}さんの1日は深夜勤務"
                        f"（前月末の準夜勤務からの継続）"
                    )
                    model.Add(shifts[e, d, NIGHT] == 1).OnlyEnforceIf(indicator)
                elif forced_day1 == "OFF":
                    # 前月末が深夜だったので、1日は公休が確定（深夜は当然0）
                    indicator = add_assumption(
                        f"{employee['name']}さんの1日は公休"
                        f"（前月末の深夜勤務の翌日のため）"
                    )
                    model.Add(shifts[e, d, OFF] == 1).OnlyEnforceIf(indicator)
                    model.Add(shifts[e, d, NIGHT] == 0)
                else:
                    # 前月のデータがない場合、1日目に深夜は割り当てられない
                    # （前日=前月末の勤務が不明なため。構造上のハード制約）
                    model.Add(shifts[e, d, NIGHT] == 0)
            else:
                model.Add(shifts[e, d, NIGHT] == shifts[e, d - 1, EVENING])

            # 深夜の翌日は公休（月内のみ。月末の深夜は翌月の生成時に公休が確定する）
            if d + 1 < days_in_month:
                model.AddImplication(shifts[e, d, NIGHT], shifts[e, d + 1, OFF])

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

        shift_code = {"日勤": DAY, "リーダー": LEADER, "半日": HALF, "準夜": EVENING, "公休": OFF}
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
    max_conditions = create_condition_max_dictionary()
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

            # ★追加: リーダー人数の上限
            max_leader = max_conditions.get((condition_type, group_name, "リーダー"))
            if max_leader is not None:
                indicator = add_assumption(
                    f"{day_number}日（{condition_type}）のグループ{group_name}のリーダー人数の上限（{max_leader}人以下）"
                )
                model.Add(
                    sum(shifts[i, d, LEADER] for i in group_employees) <= max_leader
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
        # 翌月生成時の「準夜→深夜」「深夜→公休」継続判定に使うため保存する
        save_generated_shifts(employees, year, month, result, days_in_month)
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
    ["社員管理", "個人勤務条件", "希望休・希望勤務", "人数条件", "シフト生成", "データのバックアップ"],
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

        st.write("開始前（前月末）の勤務状態")
        st.caption(
            "システムでシフトを生成するのが初めての月など、前月分のデータが無い場合に使われます。"
            "実際にこの社員が前月末に「準夜」だったなら「準夜」を、"
            "「深夜」だったなら「深夜」を選んでください。それ以外は「指定なし」のままで構いません。"
        )
        initial_status = st.selectbox("前月末の勤務", ["指定なし", "準夜", "深夜"])

        submitted = st.form_submit_button("社員を追加")
        if submitted:
            if name.strip() == "":
                st.error("名前を入力してください。")
            else:
                new_employee_id = add_employee(
                    name, employment_type, gender, experience_years, group_name, can_leader, max_consecutive_days
                )
                save_initial_carryover_setting(
                    new_employee_id, None if initial_status == "指定なし" else initial_status
                )
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

            st.write("開始前（前月末）の勤務状態")
            st.caption(
                "前月分のシフトがシステムでまだ生成されていない場合に使われます。"
                "この社員の実際の前月末の勤務に合わせて設定してください。"
            )
            current_initial_status = get_initial_carryover_setting(employee["id"])
            initial_status_options = ["指定なし", "準夜", "深夜"]
            initial_status_index = (
                initial_status_options.index(current_initial_status)
                if current_initial_status in ["準夜", "深夜"]
                else 0
            )
            edit_initial_status = st.selectbox(
                "前月末の勤務", initial_status_options, index=initial_status_index,
                key=f"initial_status_{employee['id']}",
            )

            col1, col2 = st.columns(2)
            with col1:
                if st.button("更新", key=f"update_{employee['id']}"):
                    update_employee(
                        employee["id"], edit_name, edit_employment, edit_gender,
                        edit_experience, edit_group, edit_leader, edit_max_consecutive,
                    )
                    save_initial_carryover_setting(
                        employee["id"], None if edit_initial_status == "指定なし" else edit_initial_status
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
        employee_names = {employee["name"]: employee for employee in employees}
        selected_name = st.selectbox("社員", list(employee_names.keys()))
        employee = employee_names[selected_name]

        limits = get_shift_limits(employee["id"])
        limit_dictionary = {limit["shift_type"]: (limit["min_count"], limit["max_count"]) for limit in limits}

        # ★修正: 「公休」を追加（休みの上限を設定できるように）
        shift_limit_types = ["日勤", "リーダー", "半日", "準夜", "公休"]

        # ★追加: 現在保存されている設定を一覧表示
        st.subheader("現在の設定")
        current_rows = []
        for shift_type in shift_limit_types:
            minimum, maximum = limit_dictionary.get(shift_type, (0, 31))
            current_rows.append({"勤務種類": shift_type, "最低回数": minimum, "最大回数（上限）": maximum})
        st.dataframe(pd.DataFrame(current_rows), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("設定を変更")

        with st.form("shift_limits_form"):
            values = {}
            for shift_type in shift_limit_types:
                current = limit_dictionary.get(shift_type, (0, 31))
                col1, col2 = st.columns(2)
                with col1:
                    minimum = st.number_input(
                        f"{shift_type} 最低回数", min_value=0, max_value=31, value=current[0],
                        key=f"min_{employee['id']}_{shift_type}",
                    )
                with col2:
                    maximum = st.number_input(
                        f"{shift_type} 最大回数", min_value=0, max_value=31, value=current[1],
                        key=f"max_{employee['id']}_{shift_type}",
                    )
                values[shift_type] = (minimum, maximum)

            submitted = st.form_submit_button("保存")
            if submitted:
                has_error = False
                for shift_type, value in values.items():
                    minimum, maximum = value
                    if minimum > maximum:
                        st.error(f"{shift_type}の最低回数が最大回数を超えています。")
                        has_error = True

                if not has_error:
                    for shift_type, value in values.items():
                        minimum, maximum = value
                        save_shift_limit(employee["id"], shift_type, minimum, maximum)
                    st.success("勤務条件を保存しました。")
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
    existing_max_conditions = create_condition_max_dictionary()
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

                # ★追加: リーダー人数の上限（1日あたり）
                leader_key = (condition_type, group_name, "リーダー")
                existing_leader_max = existing_max_conditions.get(leader_key)
                leader_max_col1, leader_max_col2 = st.columns([1, 2])
                with leader_max_col1:
                    leader_max_enabled = st.checkbox(
                        "リーダー人数の上限を設定する",
                        value=existing_leader_max is not None,
                        key=f"leader_max_enabled_{condition_type}_{group_name}",
                    )
                with leader_max_col2:
                    leader_max_value = st.number_input(
                        "1日あたりのリーダー上限人数",
                        min_value=0, max_value=100,
                        value=existing_leader_max if existing_leader_max is not None else max(1, values["リーダー"]),
                        key=f"leader_max_value_{condition_type}_{group_name}",
                        disabled=not leader_max_enabled,
                    )

                if st.button(f"{condition_type} グループ{group_name} 保存", key=f"save_{condition_type}_{group_name}"):
                    leader_max_to_save = leader_max_value if leader_max_enabled else None
                    for shift_type, value in values.items():
                        if shift_type == "リーダー":
                            save_staffing_condition(condition_type, group_name, shift_type, value, leader_max_to_save)
                        else:
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

        prev_year, prev_month = get_previous_month(selected_year, selected_month)
        if has_month_shift_data(prev_year, prev_month):
            st.info(
                f"{prev_year}年{prev_month}月のシフトが生成済みのため、"
                f"月末が準夜／深夜の社員は、{selected_month}月1日にその続き"
                f"（深夜／公休）が自動的に確定します。"
            )
        else:
            st.info(
                f"{prev_year}年{prev_month}月のシフトはまだ生成されていません。"
                f"社員管理画面で「開始前（前月末）の勤務状態」を設定している社員は、"
                f"その設定に基づいて{selected_month}月1日の勤務が自動的に確定します。"
                f"未設定の社員は、{selected_month}月1日に深夜を割り当てることはできません。"
            )

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

                    # ★修正: 結果表示はSHIFT_SHORT_NAMESの短縮表記で行う
                    # （generated_shifts等に保存されている result 自体はフルネームのまま変更しない）
                    display_result = {
                        name: [SHIFT_SHORT_NAMES.get(shift, shift) for shift in shift_list]
                        for name, shift_list in result.items()
                    }

                    st.write("記号：公 公休 / 有 有休 / 日 日勤 / L リーダー / 半 半日 / 準 準夜 / 深 深夜")

                    dataframe = pd.DataFrame(display_result).T
                    dataframe.columns = columns
                    st.dataframe(dataframe, use_container_width=True)

                    excel_data = create_excel(display_result, selected_year, selected_month)
                    st.download_button(
                        "Excelをダウンロード",
                        data=excel_data,
                        file_name=f"shift_{selected_year}_{selected_month}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )

# ============================================================
# データのバックアップ
# ============================================================
elif menu == "データのバックアップ":
    st.header("データのバックアップ")

    st.warning(
        "Streamlit Community Cloud などGitHub連携でデプロイしている環境では、"
        "コードを変更して再デプロイするたびに、サーバー上のファイル"
        "（社員情報・希望・条件・生成済みシフトなどが入ったデータベースファイル）は"
        "リセットされてしまいます。\n\n"
        "コードを変更する前に、必ず下の「データをダウンロード」でバックアップを取り、"
        "再デプロイ後に「データを復元」でアップロードし直してください。"
    )

    st.subheader("データをダウンロード")
    st.write("現在のデータベースファイルをダウンロードします。コードを変更・再デプロイする前に必ず取得してください。")

    if os.path.exists(DATABASE_NAME):
        with open(DATABASE_NAME, "rb") as db_file:
            db_bytes = db_file.read()
        st.download_button(
            "データをダウンロード（.db）",
            data=db_bytes,
            file_name="shift_system_backup.db",
            mime="application/octet-stream",
        )
    else:
        st.info("まだデータがありません。")

    st.divider()

    st.subheader("データを復元")
    st.write("以前ダウンロードした .db ファイルをアップロードすると、現在のデータを上書きして復元します。")

    uploaded_file = st.file_uploader("バックアップファイル（.db）を選択", type=["db"])
    if uploaded_file is not None:
        st.error(
            "現在のデータはすべて、アップロードしたファイルの内容で上書きされます。"
            "この操作は取り消せません。"
        )
        if st.button("この内容で復元する（上書きされます）", type="primary"):
            with open(DATABASE_NAME, "wb") as db_file:
                db_file.write(uploaded_file.getbuffer())
            st.success("データを復元しました。ページを再読み込みします。")
            st.rerun()

    st.divider()
    st.subheader("恒久的にデータを消さないようにするには")
    st.write(
        "手動でのバックアップ／復元が面倒な場合は、次のような方法で"
        "再デプロイの影響を受けない場所にデータを置くことができます。\n\n"
        "- 自前のサーバーやDockerでこのアプリを動かし、"
        "データベースファイルを永続ボリュームに置く"
        "（環境変数 `SHIFT_DB_PATH` にそのパスを設定すると、このアプリはそこを使います）\n"
        "- Supabase・Neon・Turso などの外部データベースサービスを別途用意し、"
        "そちらにデータを保存するよう改修する"
    )

    st.divider()
    st.subheader("生成済みシフトの削除")
    st.write(
        "生成済みシフトを削除します。社員情報・希望・各種条件は削除されません。\n\n"
        "生成済みシフトは、月またぎの準夜→深夜の継続判定にも使われています。"
        "削除すると、その月について「前月データなし」として扱われるようになる"
        "（＝社員管理で設定した「開始前の勤務状態」、または継続なしの扱いに戻る）点にご注意ください。"
    )

    existing_months = get_generated_shift_months()
    if existing_months:
        month_labels = [f"{year}年{month}月" for year, month in existing_months]
        st.caption("生成済みシフトがある年月：" + " / ".join(month_labels))
    else:
        st.info("現在、生成済みシフトはありません。")

    col1, col2 = st.columns(2)
    with col1:
        delete_target_year = st.number_input(
            "対象年", min_value=2020, max_value=2100, value=2026, key="delete_shift_year"
        )
    with col2:
        delete_target_month = st.number_input(
            "対象月", min_value=1, max_value=12, value=9, key="delete_shift_month"
        )

    st.write("① 指定した月のシフトを削除")
    if st.button(
        f"{delete_target_year}年{delete_target_month}月のシフトを削除する",
        key="delete_month_button",
    ):
        st.session_state["confirm_delete_month"] = True
    if st.session_state.get("confirm_delete_month"):
        st.warning(
            f"{delete_target_year}年{delete_target_month}月の生成済みシフトを削除します。"
            f"この操作は取り消せません。"
        )
        confirm_col1, confirm_col2 = st.columns(2)
        with confirm_col1:
            if st.button("削除を実行する", key="confirm_delete_month_button", type="primary"):
                deleted_count = delete_generated_shifts_for_month(delete_target_year, delete_target_month)
                st.session_state["confirm_delete_month"] = False
                st.success(f"{delete_target_year}年{delete_target_month}月のシフト（{deleted_count}件）を削除しました。")
                st.rerun()
        with confirm_col2:
            if st.button("キャンセル", key="cancel_delete_month_button"):
                st.session_state["confirm_delete_month"] = False
                st.rerun()

    st.divider()

    st.write("② 指定した月以降のシフトをまとめて削除")
    if st.button(
        f"{delete_target_year}年{delete_target_month}月以降のシフトを削除する",
        key="delete_from_month_button",
    ):
        st.session_state["confirm_delete_from_month"] = True
    if st.session_state.get("confirm_delete_from_month"):
        st.warning(
            f"{delete_target_year}年{delete_target_month}月、およびそれ以降に生成された"
            f"シフトをすべて削除します。この操作は取り消せません。"
        )
        confirm_col1, confirm_col2 = st.columns(2)
        with confirm_col1:
            if st.button("削除を実行する", key="confirm_delete_from_month_button", type="primary"):
                deleted_count = delete_generated_shifts_from_month(delete_target_year, delete_target_month)
                st.session_state["confirm_delete_from_month"] = False
                st.success(
                    f"{delete_target_year}年{delete_target_month}月以降のシフト（{deleted_count}件）を削除しました。"
                )
                st.rerun()
        with confirm_col2:
            if st.button("キャンセル", key="cancel_delete_from_month_button"):
                st.session_state["confirm_delete_from_month"] = False
                st.rerun()

    st.divider()

    st.write("③ すべての生成済みシフトを削除")
    if st.button("すべての生成済みシフトを削除する", key="delete_all_button"):
        st.session_state["confirm_delete_all"] = True
    if st.session_state.get("confirm_delete_all"):
        st.warning("生成済みのシフトを全期間分削除します。この操作は取り消せません。")
        confirm_col1, confirm_col2 = st.columns(2)
        with confirm_col1:
            if st.button("削除を実行する", key="confirm_delete_all_button", type="primary"):
                deleted_count = delete_all_generated_shifts()
                st.session_state["confirm_delete_all"] = False
                st.success(f"すべての生成済みシフト（{deleted_count}件）を削除しました。")
                st.rerun()
        with confirm_col2:
            if st.button("キャンセル", key="cancel_delete_all_button"):
                st.session_state["confirm_delete_all"] = False
                st.rerun()
