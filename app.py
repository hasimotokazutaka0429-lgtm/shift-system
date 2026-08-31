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

st.set_page_config(
    page_title="シフト生成システム",
    layout="wide"
)

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
    NIGHT: "深夜"
}


SHIFT_SHORT_NAMES = {
    "指定なし": "―",
    "公休": "公",
    "有休": "有",
    "日勤": "日",
    "リーダー": "L",
    "半日": "半",
    "準夜": "準",
    "深夜": "深"
}


REQUEST_OPTIONS = [
    "指定なし",
    "公休",
    "有休",
    "日勤",
    "リーダー",
    "半日",
    "準夜",
    "深夜"
]


SHIFT_LIMIT_TYPES = [
    "日勤",
    "リーダー",
    "半日",
    "準夜"
]


SHIFT_CODE = {
    "日勤": DAY,
    "リーダー": LEADER,
    "半日": HALF,
    "準夜": EVENING,
    "深夜": NIGHT
}


WEEKDAY_NAMES = [
    "月",
    "火",
    "水",
    "木",
    "金",
    "土",
    "日"
]


# ============================================================
# SQLite接続
# ============================================================

def get_connection():

    connection = sqlite3.connect(
        DATABASE_NAME,
        check_same_thread=False
    )

    connection.row_factory = sqlite3.Row

    return connection


# ============================================================
# DBの列確認
# ============================================================

def get_columns(cursor, table_name):

    cursor.execute(
        f"PRAGMA table_info({table_name})"
    )

    return [
        row["name"]
        for row in cursor.fetchall()
    ]


# ============================================================
# データベース初期化・自動移行
# ============================================================

def init_database():

    connection = get_connection()
    cursor = connection.cursor()

    # --------------------------------------------------------
    # 社員テーブル
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            name TEXT NOT NULL,

            employment_type TEXT,

            gender TEXT,

            experience_years INTEGER DEFAULT 0,

            group_name TEXT DEFAULT 'A',

            can_leader INTEGER DEFAULT 0,

            max_consecutive_days INTEGER DEFAULT 5

        )
        """
    )

    # --------------------------------------------------------
    # 古いDBに列がない場合の自動移行
    # --------------------------------------------------------

    employee_columns = get_columns(
        cursor,
        "employees"
    )

    if "employment_type" not in employee_columns:
        cursor.execute(
            """
            ALTER TABLE employees
            ADD COLUMN employment_type TEXT
            """
        )

    if "gender" not in employee_columns:
        cursor.execute(
            """
            ALTER TABLE employees
            ADD COLUMN gender TEXT
            """
        )

    if "experience_years" not in employee_columns:
        cursor.execute(
            """
            ALTER TABLE employees
            ADD COLUMN experience_years INTEGER DEFAULT 0
            """
        )

    if "group_name" not in employee_columns:
        cursor.execute(
            """
            ALTER TABLE employees
            ADD COLUMN group_name TEXT DEFAULT 'A'
            """
        )

    if "can_leader" not in employee_columns:
        cursor.execute(
            """
            ALTER TABLE employees
            ADD COLUMN can_leader INTEGER DEFAULT 0
            """
        )

    if "max_consecutive_days" not in employee_columns:
        cursor.execute(
            """
            ALTER TABLE employees
            ADD COLUMN max_consecutive_days INTEGER DEFAULT 5
            """
        )

    # --------------------------------------------------------
    # 個人勤務条件
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS employee_shift_limits (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            employee_id INTEGER,

            shift_type TEXT,

            min_count INTEGER DEFAULT 0,

            max_count INTEGER DEFAULT 31,

            UNIQUE(
                employee_id,
                shift_type
            )
        )
        """
    )

    # --------------------------------------------------------
    # 希望
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            employee_id INTEGER,

            year INTEGER,

            month INTEGER,

            day INTEGER,

            request_type TEXT,

            UNIQUE(
                employee_id,
                year,
                month,
                day
            )
        )
        """
    )

    # --------------------------------------------------------
    # 新しい人数条件テーブル
    #
    # scope_type:
    #   全体
    #   雇用形態
    #   性別
    #   経験年数
    #
    # scope_value:
    #   正社員
    #   準社員
    #   男性
    #   女性
    #   3年以上
    #   5年以上
    #   など
    # --------------------------------------------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS staffing_conditions_v2 (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            condition_type TEXT NOT NULL,

            group_name TEXT NOT NULL,

            shift_type TEXT NOT NULL,

            scope_type TEXT NOT NULL DEFAULT '全体',

            scope_value TEXT NOT NULL DEFAULT '',

            required_count INTEGER DEFAULT 0,

            UNIQUE(
                condition_type,
                group_name,
                shift_type,
                scope_type,
                scope_value
            )
        )
        """
    )

    # --------------------------------------------------------
    # 旧人数条件テーブルがある場合
    # データを新テーブルへ移行
    # --------------------------------------------------------

    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        AND name='staffing_conditions'
        """
    )

    old_table_exists = cursor.fetchone() is not None

    if old_table_exists:

        cursor.execute(
            """
            SELECT
                condition_type,
                group_name,
                shift_type,
                required_count
            FROM staffing_conditions
            """
        )

        old_conditions = cursor.fetchall()

        for condition in old_conditions:

            cursor.execute(
                """
                INSERT OR IGNORE INTO staffing_conditions_v2 (

                    condition_type,
                    group_name,
                    shift_type,
                    scope_type,
                    scope_value,
                    required_count

                )

                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    condition["condition_type"],
                    condition["group_name"],
                    condition["shift_type"],
                    "全体",
                    "",
                    condition["required_count"]
                )
            )

    connection.commit()
    connection.close()


# ============================================================
# 社員取得
# ============================================================

def get_employees():

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM employees
        ORDER BY id
        """
    )

    employees = cursor.fetchall()

    connection.close()

    return employees


# ============================================================
# 社員追加
# ============================================================

def add_employee(
    name,
    employment_type,
    gender,
    experience_years,
    group_name,
    can_leader,
    max_consecutive_days
):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO employees (

            name,
            employment_type,
            gender,
            experience_years,
            group_name,
            can_leader,
            max_consecutive_days

        )

        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            employment_type,
            gender,
            experience_years,
            group_name,
            int(can_leader),
            max_consecutive_days
        )
    )

    connection.commit()
    connection.close()


# ============================================================
# 社員更新
# ============================================================

def update_employee(

    employee_id,
    name,
    employment_type,
    gender,
    experience_years,
    group_name,
    can_leader,
    max_consecutive_days

):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE employees

        SET

            name = ?,
            employment_type = ?,
            gender = ?,
            experience_years = ?,
            group_name = ?,
            can_leader = ?,
            max_consecutive_days = ?

        WHERE id = ?
        """,
        (
            name,
            employment_type,
            gender,
            experience_years,
            group_name,
            int(can_leader),
            max_consecutive_days,
            employee_id
        )
    )

    connection.commit()
    connection.close()


# ============================================================
# 社員削除
# ============================================================

def delete_employee(employee_id):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM employee_shift_limits
        WHERE employee_id = ?
        """,
        (employee_id,)
    )

    cursor.execute(
        """
        DELETE FROM requests
        WHERE employee_id = ?
        """,
        (employee_id,)
    )

    cursor.execute(
        """
        DELETE FROM employees
        WHERE id = ?
        """,
        (employee_id,)
    )

    connection.commit()
    connection.close()


# ============================================================
# 希望取得
# ============================================================

def get_requests(
    employee_id,
    year,
    month
):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM requests

        WHERE
            employee_id = ?
            AND year = ?
            AND month = ?

        ORDER BY day
        """,
        (
            employee_id,
            year,
            month
        )
    )

    requests = cursor.fetchall()

    connection.close()

    return requests


# ============================================================
# 希望保存
# ============================================================

def save_request(
    employee_id,
    year,
    month,
    day,
    request_type
):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO requests (

            employee_id,
            year,
            month,
            day,
            request_type

        )

        VALUES (?, ?, ?, ?, ?)

        ON CONFLICT(
            employee_id,
            year,
            month,
            day
        )

        DO UPDATE SET
            request_type = excluded.request_type
        """,
        (
            employee_id,
            year,
            month,
            day,
            request_type
        )
    )

    connection.commit()
    connection.close()


# ============================================================
# 希望削除
# ============================================================

def delete_request(
    employee_id,
    year,
    month,
    day
):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM requests

        WHERE
            employee_id = ?
            AND year = ?
            AND month = ?
            AND day = ?
        """,
        (
            employee_id,
            year,
            month,
            day
        )
    )

    connection.commit()
    connection.close()


# ============================================================
# 個人勤務条件取得
# ============================================================

def get_shift_limits(employee_id):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM employee_shift_limits

        WHERE employee_id = ?
        """,
        (employee_id,)
    )

    limits = cursor.fetchall()

    connection.close()

    return limits


# ============================================================
# 個人勤務条件保存
# ============================================================

def save_shift_limit(
    employee_id,
    shift_type,
    min_count,
    max_count
):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO employee_shift_limits (

            employee_id,
            shift_type,
            min_count,
            max_count

        )

        VALUES (?, ?, ?, ?)

        ON CONFLICT(
            employee_id,
            shift_type
        )

        DO UPDATE SET

            min_count = excluded.min_count,
            max_count = excluded.max_count
        """,
        (
            employee_id,
            shift_type,
            min_count,
            max_count
        )
    )

    connection.commit()
    connection.close()


# ============================================================
# 人数条件取得
# ============================================================

def get_staffing_conditions():

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM staffing_conditions_v2

        ORDER BY
            condition_type,
            group_name,
            shift_type,
            scope_type,
            scope_value
        """
    )

    conditions = cursor.fetchall()

    connection.close()

    return conditions


# ============================================================
# 人数条件保存
# ============================================================

def save_staffing_condition(

    condition_type,
    group_name,
    shift_type,
    scope_type,
    scope_value,
    required_count

):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO staffing_conditions_v2 (

            condition_type,
            group_name,
            shift_type,
            scope_type,
            scope_value,
            required_count

        )

        VALUES (?, ?, ?, ?, ?, ?)

        ON CONFLICT(
            condition_type,
            group_name,
            shift_type,
            scope_type,
            scope_value
        )

        DO UPDATE SET

            required_count =
                excluded.required_count
        """,
        (
            condition_type,
            group_name,
            shift_type,
            scope_type,
            scope_value,
            required_count
        )
    )

    connection.commit()
    connection.close()


# ============================================================
# 条件辞書
# ============================================================

def create_condition_dictionary():

    conditions = get_staffing_conditions()

    result = {}

    for condition in conditions:

        key = (
            condition["condition_type"],
            condition["group_name"],
            condition["shift_type"],
            condition["scope_type"],
            condition["scope_value"]
        )

        result[key] = condition["required_count"]

    return result


# ============================================================
# 日付タイプ
# ============================================================

def get_day_type(
    year,
    month,
    day
):

    target_date = date(
        year,
        month,
        day
    )

    japanese_holidays = holidays.Japan()

    # 日曜・祝日
    if (
        target_date.weekday() == 6
        or target_date in japanese_holidays
    ):
        return "日祝"

    # 水曜
    if target_date.weekday() == 2:
        return "水曜"

    # 土曜
    if target_date.weekday() == 5:
        return "土曜"

    return "通常"


# ============================================================
# 指定された条件に該当する社員
# ============================================================

def employee_matches_scope(
    employee,
    scope_type,
    scope_value
):

    if scope_type == "全体":
        return True

    if scope_type == "雇用形態":
        return (
            employee["employment_type"]
            == scope_value
        )

    if scope_type == "性別":
        return (
            employee["gender"]
            == scope_value
        )

    if scope_type == "経験年数":

        # 例:
        # 3年以上
        # 5年以上

        try:

            minimum_years = int(
                scope_value.replace(
                    "年以上",
                    ""
                )
            )

            return (
                employee["experience_years"]
                >= minimum_years
            )

        except ValueError:

            return False

    return False


# ============================================================
# シフト生成
# ============================================================

def generate_shift(
    employees,
    year,
    month
):

    days_in_month = calendar.monthrange(
        year,
        month
    )[1]

    model = cp_model.CpModel()

    employee_count = len(employees)

    shift_types = [
        OFF,
        PAID,
        DAY,
        LEADER,
        HALF,
        EVENING,
        NIGHT
    ]

    # ========================================================
    # 変数
    # ========================================================

    shifts = {}

    for employee_index in range(
        employee_count
    ):

        for day_index in range(
            days_in_month
        ):

            for shift_type in shift_types:

                shifts[
                    employee_index,
                    day_index,
                    shift_type
                ] = model.NewBoolVar(

                    f"shift_"
                    f"{employee_index}_"
                    f"{day_index}_"
                    f"{shift_type}"
                )

    # ========================================================
    # 1日1勤務
    # ========================================================

    for employee_index in range(
        employee_count
    ):

        for day_index in range(
            days_in_month
        ):

            model.AddExactlyOne(

                shifts[
                    employee_index,
                    day_index,
                    shift_type
                ]

                for shift_type in shift_types
            )

    # ========================================================
    # 半日は水曜・土曜のみ
    # ========================================================

    for day_index in range(
        days_in_month
    ):

        day_type = get_day_type(
            year,
            month,
            day_index + 1
        )

        if day_type not in [
            "水曜",
            "土曜"
        ]:

            for employee_index in range(
                employee_count
            ):

                model.Add(
                    shifts[
                        employee_index,
                        day_index,
                        HALF
                    ] == 0
                )

    # ========================================================
    # リーダー可能者のみリーダー
    # ========================================================

    for employee_index, employee in enumerate(
        employees
    ):

        if not employee["can_leader"]:

            for day_index in range(
                days_in_month
            ):

                model.Add(
                    shifts[
                        employee_index,
                        day_index,
                        LEADER
                    ] == 0
                )

    # ========================================================
    # 準夜 → 翌日深夜 → 翌々日公休
    # ========================================================

    for employee_index in range(
        employee_count
    ):

        # 月末1日前までしか準夜にできない
        for day_index in range(
            days_in_month
        ):

            if day_index >= days_in_month - 1:

                model.Add(
                    shifts[
                        employee_index,
                        day_index,
                        EVENING
                    ] == 0
                )

        # 準夜の翌日は必ず深夜
        for day_index in range(
            days_in_month - 1
        ):

            model.Add(

                shifts[
                    employee_index,
                    day_index,
                    EVENING
                ]

                ==
                shifts[
                    employee_index,
                    day_index + 1,
                    NIGHT
                ]
            )

        # 深夜の翌日は必ず公休
        for day_index in range(
            days_in_month - 1
        ):

            model.AddImplication(

                shifts[
                    employee_index,
                    day_index,
                    NIGHT
                ],

                shifts[
                    employee_index,
                    day_index + 1,
                    OFF
                ]
            )

    # ========================================================
    # 希望条件
    # ========================================================

    for employee_index, employee in enumerate(
        employees
    ):

        requests = get_requests(
            employee["id"],
            year,
            month
        )

        for request in requests:

            day_index = request["day"] - 1

            request_type = request[
                "request_type"
            ]

            if day_index < 0:
                continue

            if day_index >= days_in_month:
                continue

            if request_type == "公休":

                model.Add(
                    shifts[
                        employee_index,
                        day_index,
                        OFF
                    ] == 1
                )

            elif request_type == "有休":

                model.Add(
                    shifts[
                        employee_index,
                        day_index,
                        PAID
                    ] == 1
                )

            elif request_type == "日勤":

                model.Add(
                    shifts[
                        employee_index,
                        day_index,
                        DAY
                    ] == 1
                )

            elif request_type == "リーダー":

                model.Add(
                    shifts[
                        employee_index,
                        day_index,
                        LEADER
                    ] == 1
                )

            elif request_type == "半日":

                model.Add(
                    shifts[
                        employee_index,
                        day_index,
                        HALF
                    ] == 1
                )

            elif request_type == "準夜":

                model.Add(
                    shifts[
                        employee_index,
                        day_index,
                        EVENING
                    ] == 1
                )

            elif request_type == "深夜":

                model.Add(
                    shifts[
                        employee_index,
                        day_index,
                        NIGHT
                    ] == 1
                )

    # ========================================================
    # 個人勤務回数
    # ========================================================

    for employee_index, employee in enumerate(
        employees
    ):

        limits = get_shift_limits(
            employee["id"]
        )

        limit_dictionary = {}

        for limit in limits:

            limit_dictionary[
                limit["shift_type"]
            ] = (
                limit["min_count"],
                limit["max_count"]
            )

        for shift_type_name in SHIFT_LIMIT_TYPES:

            if shift_type_name not in limit_dictionary:
                continue

            minimum, maximum = (
                limit_dictionary[
                    shift_type_name
                ]
            )

            shift_code = SHIFT_CODE[
                shift_type_name
            ]

            total_count = sum(

                shifts[
                    employee_index,
                    day_index,
                    shift_code
                ]

                for day_index in range(
                    days_in_month
                )
            )

            model.Add(
                total_count >= minimum
            )

            model.Add(
                total_count <= maximum
            )

    # ========================================================
    # 最大連勤
    # ========================================================

    for employee_index, employee in enumerate(
        employees
    ):

        max_consecutive = employee[
            "max_consecutive_days"
        ]

        for start_day in range(
            days_in_month
            - max_consecutive
        ):

            work_list = []

            for day_index in range(
                start_day,
                start_day + max_consecutive + 1
            ):

                work_list.append(

                    shifts[
                        employee_index,
                        day_index,
                        DAY
                    ]

                    +

                    shifts[
                        employee_index,
                        day_index,
                        LEADER
                    ]

                    +

                    shifts[
                        employee_index,
                        day_index,
                        HALF
                    ]

                    +

                    shifts[
                        employee_index,
                        day_index,
                        EVENING
                    ]

                    +

                    shifts[
                        employee_index,
                        day_index,
                        NIGHT
                    ]
                )

            model.Add(
                sum(work_list)
                <= max_consecutive
            )

    # ========================================================
    # 人数条件
    # ========================================================

    conditions = create_condition_dictionary()

    condition_types = [
        "通常",
        "水曜",
        "土曜",
        "日祝"
    ]

    groups = [
        "A",
        "B"
    ]

    for day_index in range(
        days_in_month
    ):

        day_type = get_day_type(
            year,
            month,
            day_index + 1
        )

        for group_name in groups:

            group_employees = [

                index

                for index, employee in enumerate(
                    employees
                )

                if employee["group_name"]
                == group_name
            ]

            # ------------------------------------------------
            # 全体条件
            # ------------------------------------------------

            for shift_name in [
                "日勤",
                "リーダー",
                "半日",
                "準夜",
                "深夜"
            ]:

                required_count = conditions.get(

                    (
                        day_type,
                        group_name,
                        shift_name,
                        "全体",
                        ""
                    ),

                    0
                )

                if shift_name == "日勤":

                    # リーダーは日勤に含む
                    total = sum(

                        shifts[
                            index,
                            day_index,
                            DAY
                        ]

                        +

                        shifts[
                            index,
                            day_index,
                            LEADER
                        ]

                        for index
                        in group_employees
                    )

                else:

                    shift_code = SHIFT_CODE[
                        shift_name
                    ]

                    total = sum(

                        shifts[
                            index,
                            day_index,
                            shift_code
                        ]

                        for index
                        in group_employees
                    )

                model.Add(
                    total >= required_count
                )

            # ------------------------------------------------
            # 雇用形態・性別・経験年数条件
            # ------------------------------------------------

            for shift_name in [
                "日勤",
                "リーダー",
                "半日",
                "準夜",
                "深夜"
            ]:

                for scope_type, scope_values in [

                    (
                        "雇用形態",
                        ["正社員", "準社員"]
                    ),

                    (
                        "性別",
                        ["男性", "女性", "その他"]
                    ),

                    (
                        "経験年数",
                        [
                            "1年以上",
                            "3年以上",
                            "5年以上",
                            "10年以上"
                        ]
                    )

                ]:

                    for scope_value in scope_values:

                        required_count = conditions.get(

                            (
                                day_type,
                                group_name,
                                shift_name,
                                scope_type,
                                scope_value
                            ),

                            0
                        )

                        matching_employees = [

                            index

                            for index in group_employees

                            if employee_matches_scope(
                                employees[index],
                                scope_type,
                                scope_value
                            )
                        ]

                        if shift_name == "日勤":

                            total = sum(

                                shifts[
                                    index,
                                    day_index,
                                    DAY
                                ]

                                +

                                shifts[
                                    index,
                                    day_index,
                                    LEADER
                                ]

                                for index
                                in matching_employees
                            )

                        else:

                            shift_code = SHIFT_CODE[
                                shift_name
                            ]

                            total = sum(

                                shifts[
                                    index,
                                    day_index,
                                    shift_code
                                ]

                                for index
                                in matching_employees
                            )

                        model.Add(
                            total >= required_count
                        )

    # ========================================================
    # 求解
    # ========================================================

    solver = cp_model.CpSolver()

    solver.parameters.max_time_in_seconds = 60

    status = solver.Solve(
        model
    )

    if status not in [
        cp_model.OPTIMAL,
        cp_model.FEASIBLE
    ]:

        return None

    # ========================================================
    # 結果
    # ========================================================

    result = {}

    for employee_index, employee in enumerate(
        employees
    ):

        result[
            employee["name"]
        ] = []

        for day_index in range(
            days_in_month
        ):

            for shift_type in shift_types:

                if solver.Value(

                    shifts[
                        employee_index,
                        day_index,
                        shift_type
                    ]

                ) == 1:

                    result[
                        employee["name"]
                    ].append(

                        SHIFT_NAMES[
                            shift_type
                        ]
                    )

                    break

    return result


# ============================================================
# Excel作成
# ============================================================

def create_excel(
    result,
    year,
    month
):

    days_in_month = calendar.monthrange(
        year,
        month
    )[1]

    columns = []

    for day in range(
        1,
        days_in_month + 1
    ):

        weekday = calendar.weekday(
            year,
            month,
            day
        )

        columns.append(
            f"{day}({WEEKDAY_NAMES[weekday]})"
        )

    dataframe = pd.DataFrame(
        result
    ).T

    dataframe.columns = columns

    dataframe.index.name = "社員名"

    output = BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        dataframe.to_excel(
            writer,
            sheet_name="シフト"
        )

    return output.getvalue()


# ============================================================
# DB初期化
# ============================================================

init_database()


# ============================================================
# タイトル
# ============================================================

st.title(
    "シフト自動生成システム"
)


# ============================================================
# メニュー
# ============================================================

menu = st.sidebar.radio(

    "メニュー",

    [
        "社員管理",
        "個人勤務条件",
        "希望休・希望勤務",
        "人数条件",
        "シフト生成"
    ]
)


# ============================================================
# 社員管理
# ============================================================

if menu == "社員管理":

    st.header(
        "社員管理"
    )

    with st.form(
        "add_employee_form"
    ):

        name = st.text_input(
            "名前"
        )

        col1, col2, col3 = st.columns(3)

        with col1:

            employment_type = st.selectbox(
                "雇用形態",
                [
                    "正社員",
                    "準社員"
                ]
            )

        with col2:

            gender = st.selectbox(
                "性別",
                [
                    "男性",
                    "女性",
                    "その他"
                ]
            )

        with col3:

            group_name = st.selectbox(
                "所属グループ",
                [
                    "A",
                    "B"
                ]
            )

        experience_years = st.number_input(
            "経験年数",
            min_value=0,
            max_value=50,
            value=0
        )

        can_leader = st.checkbox(
            "リーダー可能"
        )

        max_consecutive_days = st.number_input(
            "最大連勤日数",
            min_value=1,
            max_value=31,
            value=5
        )

        submitted = st.form_submit_button(
            "社員を追加"
        )

        if submitted:

            if name.strip() == "":

                st.error(
                    "名前を入力してください。"
                )

            else:

                add_employee(

                    name.strip(),
                    employment_type,
                    gender,
                    experience_years,
                    group_name,
                    can_leader,
                    max_consecutive_days

                )

                st.success(
                    f"{name}さんを登録しました。"
                )

                st.rerun()

    st.divider()

    st.subheader(
        "登録済み社員"
    )

    employees = get_employees()

    for employee in employees:

        with st.expander(
            employee["name"]
        ):

            edit_name = st.text_input(
                "名前",
                employee["name"],
                key=f"name_{employee['id']}"
            )

            edit_employment_type = st.selectbox(
                "雇用形態",
                [
                    "正社員",
                    "準社員"
                ],
                index=[
                    "正社員",
                    "準社員"
                ].index(
                    employee["employment_type"]
                )
                if employee["employment_type"]
                in ["正社員", "準社員"]
                else 0,
                key=f"employment_{employee['id']}"
            )

            edit_gender = st.selectbox(
                "性別",
                [
                    "男性",
                    "女性",
                    "その他"
                ],
                index=[
                    "男性",
                    "女性",
                    "その他"
                ].index(
                    employee["gender"]
                )
                if employee["gender"]
                in ["男性", "女性", "その他"]
                else 0,
                key=f"gender_{employee['id']}"
            )

            edit_experience = st.number_input(
                "経験年数",
                min_value=0,
                max_value=50,
                value=employee["experience_years"] or 0,
                key=f"experience_{employee['id']}"
            )

            edit_group = st.selectbox(
                "グループ",
                [
                    "A",
                    "B"
                ],
                index=[
                    "A",
                    "B"
                ].index(
                    employee["group_name"]
                )
                if employee["group_name"]
                in ["A", "B"]
                else 0,
                key=f"group_{employee['id']}"
            )

            edit_leader = st.checkbox(
                "リーダー可能",
                value=bool(
                    employee["can_leader"]
                ),
                key=f"leader_{employee['id']}"
            )

            edit_max_consecutive = st.number_input(
                "最大連勤",
                min_value=1,
                max_value=31,
                value=employee[
                    "max_consecutive_days"
                ] or 5,
                key=f"max_{employee['id']}"
            )

            col1, col2 = st.columns(2)

            with col1:

                if st.button(
                    "更新",
                    key=f"update_{employee['id']}"
                ):

                    update_employee(

                        employee["id"],
                        edit_name,
                        edit_employment_type,
                        edit_gender,
                        edit_experience,
                        edit_group,
                        edit_leader,
                        edit_max_consecutive

                    )

                    st.success(
                        "更新しました。"
                    )

                    st.rerun()

            with col2:

                if st.button(
                    "削除",
                    key=f"delete_{employee['id']}"
                ):

                    delete_employee(
                        employee["id"]
                    )

                    st.rerun()


# ============================================================
# 個人勤務条件
# ============================================================

elif menu == "個人勤務条件":

    st.header(
        "個人ごとの勤務回数条件"
    )

    employees = get_employees()

    if len(employees) == 0:

        st.info(
            "先に社員を登録してください。"
        )

    else:

        employee_names = {
            employee["name"]: employee
            for employee in employees
        }

        selected_name = st.selectbox(
            "社員",
            list(employee_names.keys())
        )

        employee = employee_names[
            selected_name
        ]

        limits = get_shift_limits(
            employee["id"]
        )

        limit_dictionary = {

            limit["shift_type"]:
            (
                limit["min_count"],
                limit["max_count"]
            )

            for limit in limits
        }

        with st.form(
            "shift_limits_form"
        ):

            values = {}

            for shift_type in SHIFT_LIMIT_TYPES:

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
                        value=current[0],
                        key=f"min_{shift_type}"
                    )

                with col2:

                    maximum = st.number_input(
                        f"{shift_type} 最大回数",
                        min_value=0,
                        max_value=31,
                        value=current[1],
                        key=f"max_{shift_type}"
                    )

                values[
                    shift_type
                ] = (
                    minimum,
                    maximum
                )

            submitted = st.form_submit_button(
                "保存"
            )

            if submitted:

                error = False

                for shift_type, value in values.items():

                    minimum, maximum = value

                    if minimum > maximum:

                        st.error(
                            f"{shift_type}の最低回数が"
                            "最大回数を超えています。"
                        )

                        error = True

                if not error:

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


# ============================================================
# 希望休・希望勤務
# ============================================================

elif menu == "希望休・希望勤務":

    st.header(
        "希望休・希望勤務"
    )

    employees = get_employees()

    if len(employees) == 0:

        st.info(
            "先に社員を登録してください。"
        )

    else:

        col1, col2 = st.columns(2)

        with col1:

            selected_year = st.number_input(
                "対象年",
                min_value=2026,
                max_value=2100,
                value=2026,
                step=1
            )

        with col2:

            selected_month = st.number_input(
                "対象月",
                min_value=1,
                max_value=12,
                value=9,
                step=1
            )

        days_in_month = calendar.monthrange(
            selected_year,
            selected_month
        )[1]

        st.subheader(
            f"{selected_year}年"
            f"{selected_month}月の勤務希望"
        )

        st.write(
            "記号：― 指定なし / 公 公休 / 有 有休 / "
            "日 日勤 / L リーダー / 半 半日 / "
            "準 準夜 / 深 深夜"
        )

        # ----------------------------------------------------
        # 表の幅を確保
        # ----------------------------------------------------

        st.markdown(
            """
            <style>

            [data-testid="stDataEditor"] {
                min-width: 1500px;
            }

            </style>
            """,
            unsafe_allow_html=True
        )

        rows = []

        for employee in employees:

            requests = get_requests(
                employee["id"],
                selected_year,
                selected_month
            )

            request_dictionary = {

                request["day"]:
                request["request_type"]

                for request in requests
            }

            row = {
                "社員名": employee["name"]
            }

            for day in range(
                1,
                days_in_month + 1
            ):

                weekday = calendar.weekday(
                    selected_year,
                    selected_month,
                    day
                )

                column_name = (
                    f"{day}日"
                    f"({WEEKDAY_NAMES[weekday]})"
                )

                value = request_dictionary.get(
                    day,
                    "指定なし"
                )

                row[
                    column_name
                ] = SHIFT_SHORT_NAMES[
                    value
                ]

            rows.append(row)

        dataframe = pd.DataFrame(rows)

        column_config = {

            "社員名":
            st.column_config.TextColumn(
                "社員名",
                disabled=True,
                width="medium"
            )
        }

        short_options = list(
            SHIFT_SHORT_NAMES.values()
        )

        for column in dataframe.columns[1:]:

            column_config[
                column
            ] = st.column_config.SelectboxColumn(

                column,

                options=short_options,

                width="medium"
            )

        edited_dataframe = st.data_editor(

            dataframe,

            column_config=column_config,

            hide_index=True,

            use_container_width=False,

            num_rows="fixed",

            key=(
                f"requests_"
                f"{selected_year}_"
                f"{selected_month}"
            )
        )

        if st.button(
            "全社員の希望を保存",
            type="primary"
        ):

            reverse_short_names = {

                value: key

                for key, value
                in SHIFT_SHORT_NAMES.items()
            }

            for row_index, employee in enumerate(
                employees
            ):

                for day in range(
                    1,
                    days_in_month + 1
                ):

                    weekday = calendar.weekday(
                        selected_year,
                        selected_month,
                        day
                    )

                    column_name = (
                        f"{day}日"
                        f"({WEEKDAY_NAMES[weekday]})"
                    )

                    short_value = edited_dataframe.iloc[
                        row_index
                    ][
                        column_name
                    ]

                    request_type = reverse_short_names.get(
                        short_value,
                        "指定なし"
                    )

                    if request_type == "指定なし":

                        delete_request(
                            employee["id"],
                            selected_year,
                            selected_month,
                            day
                        )

                    else:

                        save_request(
                            employee["id"],
                            selected_year,
                            selected_month,
                            day,
                            request_type
                        )

            st.success(
                "希望を保存しました。"
            )

            st.rerun()


# ============================================================
# 人数条件
# ============================================================

elif menu == "人数条件":

    st.header(
        "日別・グループ別人数条件"
    )

    st.info(
        "リーダーは日勤人数にも含まれます。"
        "半日は水曜・土曜のみ設定できます。"
    )

    condition_types = [
        "通常",
        "水曜",
        "土曜",
        "日祝"
    ]

    groups = [
        "A",
        "B"
    ]

    shift_types = [
        "日勤",
        "リーダー",
        "半日",
        "準夜",
        "深夜"
    ]

    scope_type_options = [
        "全体",
        "雇用形態",
        "性別",
        "経験年数"
    ]

    scope_values = {

        "全体": [""],

        "雇用形態": [
            "正社員",
            "準社員"
        ],

        "性別": [
            "男性",
            "女性",
            "その他"
        ],

        "経験年数": [
            "1年以上",
            "3年以上",
            "5年以上",
            "10年以上"
        ]
    }

    existing_conditions = create_condition_dictionary()

    for condition_type in condition_types:

        with st.expander(
            f"{condition_type}の人数条件",
            expanded=True
        ):

            for group_name in groups:

                st.markdown(
                    f"### グループ{group_name}"
                )

                for shift_type in shift_types:

                    st.markdown(
                        f"**{shift_type}**"
                    )

                    columns = st.columns(4)

                    for scope_index, scope_type in enumerate(
                        scope_type_options
                    ):

                        with columns[scope_index]:

                            if scope_type == "全体":

                                scope_value = ""

                                label = "全体"

                            else:

                                values = scope_values[
                                    scope_type
                                ]

                                scope_value = st.selectbox(
                                    scope_type,
                                    values,
                                    key=(
                                        f"scope_"
                                        f"{condition_type}_"
                                        f"{group_name}_"
                                        f"{shift_type}_"
                                        f"{scope_type}"
                                    )
                                )

                                label = (
                                    f"{scope_type}:"
                                    f"{scope_value}"
                                )

                            key = (

                                condition_type,
                                group_name,
                                shift_type,
                                scope_type,
                                scope_value

                            )

                            default_value = existing_conditions.get(
                                key,
                                0
                            )

                            # 全体は専用表示
                            if scope_type == "全体":

                                required_count = st.number_input(
                                    label,
                                    min_value=0,
                                    max_value=100,
                                    value=default_value,
                                    key=(
                                        f"count_"
                                        f"{condition_type}_"
                                        f"{group_name}_"
                                        f"{shift_type}_"
                                        f"all"
                                    )
                                )

                            else:

                                required_count = st.number_input(
                                    f"必要人数",
                                    min_value=0,
                                    max_value=100,
                                    value=default_value,
                                    key=(
                                        f"count_"
                                        f"{condition_type}_"
                                        f"{group_name}_"
                                        f"{shift_type}_"
                                        f"{scope_type}"
                                    )
                                )

                            if st.button(
                                "保存",
                                key=(
                                    f"save_"
                                    f"{condition_type}_"
                                    f"{group_name}_"
                                    f"{shift_type}_"
                                    f"{scope_type}"
                                )
                            ):

                                save_staffing_condition(

                                    condition_type,
                                    group_name,
                                    shift_type,
                                    scope_type,
                                    scope_value,
                                    required_count

                                )

                                st.success(
                                    f"{label}の条件を保存しました。"
                                )

                                st.rerun()


# ============================================================
# シフト生成
# ============================================================

elif menu == "シフト生成":

    st.header(
        "シフト自動生成"
    )

    employees = get_employees()

    if len(employees) == 0:

        st.info(
            "先に社員を登録してください。"
        )

    else:

        col1, col2 = st.columns(2)

        with col1:

            selected_year = st.number_input(
                "生成する年",
                min_value=2026,
                max_value=2100,
                value=2026,
                step=1,
                key="generate_year"
            )

        with col2:

            selected_month = st.number_input(
                "生成する月",
                min_value=1,
                max_value=12,
                value=9,
                step=1,
                key="generate_month"
            )

        days_in_month = calendar.monthrange(
            selected_year,
            selected_month
        )[1]

        st.write(
            f"{selected_year}年"
            f"{selected_month}月："
            f"{days_in_month}日"
        )

        if st.button(
            "シフトを自動生成",
            type="primary"
        ):

            with st.spinner(
                "シフトを生成しています..."
            ):

                result = generate_shift(
                    employees,
                    selected_year,
                    selected_month
                )

            if result is None:

                st.error(
                    "条件を満たすシフトを"
                    "生成できませんでした。"
                )

                st.warning(
                    "人数条件・個人勤務条件・希望勤務・"
                    "最大連勤・準夜→深夜→公休などの"
                    "条件が同時に成立しているか確認してください。"
                )

            else:

                st.success(
                    "シフトを生成しました。"
                )

                columns = []

                for day in range(
                    1,
                    days_in_month + 1
                ):

                    weekday = calendar.weekday(
                        selected_year,
                        selected_month,
                        day
                    )

                    columns.append(
                        f"{day}({WEEKDAY_NAMES[weekday]})"
                    )

                dataframe = pd.DataFrame(
                    result
                ).T

                dataframe.columns = columns

                dataframe.index.name = "社員名"

                st.dataframe(
                    dataframe,
                    use_container_width=True
                )

                # --------------------------------------------
                # Excel
                # --------------------------------------------

                excel_data = create_excel(
                    result,
                    selected_year,
                    selected_month
                )

                st.download_button(

                    "Excelをダウンロード",

                    data=excel_data,

                    file_name=(
                        f"shift_"
                        f"{selected_year}_"
                        f"{selected_month}.xlsx"
                    ),

                    mime=(
                        "application/"
                        "vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet"
                    )
                )
