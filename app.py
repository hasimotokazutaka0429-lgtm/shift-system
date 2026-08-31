
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


WORK_SHIFTS = [

    DAY,
    LEADER,
    HALF,
    EVENING,
    NIGHT

]


# ============================================================
# データベース接続
# ============================================================

def get_connection():

    connection = sqlite3.connect(
        DATABASE_NAME,
        check_same_thread=False
    )

    connection.row_factory = sqlite3.Row

    return connection


# ============================================================
# データベース初期化
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

            group_name TEXT DEFAULT '指定なし',

            can_leader INTEGER DEFAULT 0,

            max_consecutive_days INTEGER DEFAULT 5

        )

        """
    )


    # --------------------------------------------------------
    # 社員ごとの勤務回数条件
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
    # 希望テーブル
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
    # シフト人数条件
    # --------------------------------------------------------

    cursor.execute(
        """

        CREATE TABLE IF NOT EXISTS staffing_conditions (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            condition_type TEXT,

            group_name TEXT,

            shift_type TEXT,

            required_count INTEGER DEFAULT 0,

            UNIQUE(
                condition_type,
                group_name,
                shift_type
            )

        )

        """
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
# 勤務回数条件取得
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
# 勤務回数条件保存
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

        FROM staffing_conditions

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
    required_count

):

    connection = get_connection()

    cursor = connection.cursor()


    cursor.execute(
        """

        INSERT INTO staffing_conditions (

            condition_type,
            group_name,
            shift_type,
            required_count

        )

        VALUES (?, ?, ?, ?)

        ON CONFLICT(

            condition_type,
            group_name,
            shift_type

        )

        DO UPDATE SET

            required_count = excluded.required_count

        """,
        (

            condition_type,
            group_name,
            shift_type,
            required_count

        )
    )


    connection.commit()

    connection.close()


# ============================================================
# 日付タイプ取得
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


    # 日曜日・祝日

    if (
        target_date.weekday() == 6
        or target_date in japanese_holidays
    ):

        return "日祝"


    # 水曜日

    if target_date.weekday() == 2:

        return "水曜"


    # 土曜日

    if target_date.weekday() == 5:

        return "土曜"


    return "通常"


# ============================================================
# 人数条件辞書作成
# ============================================================

def create_condition_dictionary():

    conditions = get_staffing_conditions()

    result = {}


    for condition in conditions:

        key = (

            condition["condition_type"],
            condition["group_name"],
            condition["shift_type"]

        )


        result[key] = condition[
            "required_count"
        ]


    return result


# ============================================================
# OR-Tools シフト生成
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


    employee_count = len(
        employees
    )


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

                for shift_type
                in shift_types

            )


    # ========================================================
    # 半日は水曜・土曜のみ
    # ========================================================

    for day_index in range(
        days_in_month
    ):

        day_number = day_index + 1


        day_type = get_day_type(

            year,
            month,
            day_number

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
                    ]

                    == 0

                )


    # ========================================================
    # リーダー勤務可能者のみ
    # ========================================================

    for employee_index, employee in enumerate(
        employees
    ):

        if employee["can_leader"] == 0:

            for day_index in range(
                days_in_month
            ):

                model.Add(

                    shifts[
                        employee_index,
                        day_index,
                        LEADER
                    ]

                    == 0

                )


    # ========================================================
    # 準夜 → 翌日深夜 → 翌日公休
    # ========================================================

    for employee_index in range(
        employee_count
    ):


        # 準夜は月末禁止
        model.Add(

            shifts[
                employee_index,
                days_in_month - 1,
                EVENING
            ]

            == 0

        )


        # 深夜は1日目禁止
        model.Add(

            shifts[
                employee_index,
                0,
                NIGHT
            ]

            == 0

        )


        for day_index in range(
            days_in_month - 1
        ):


            # 準夜なら翌日は深夜

            model.AddImplication(

                shifts[
                    employee_index,
                    day_index,
                    EVENING
                ],

                shifts[
                    employee_index,
                    day_index + 1,
                    NIGHT
                ]

            )


            # 深夜なら前日は準夜

            if day_index + 1 < days_in_month:

                model.AddImplication(

                    shifts[
                        employee_index,
                        day_index + 1,
                        NIGHT
                    ],

                    shifts[
                        employee_index,
                        day_index,
                        EVENING
                    ]

                )


        # 深夜翌日は公休

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

    request_code_map = {

        "公休": OFF,
        "有休": PAID,
        "日勤": DAY,
        "リーダー": LEADER,
        "半日": HALF,
        "準夜": EVENING,
        "深夜": NIGHT

    }


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


            if request_type in request_code_map:

                model.Add(

                    shifts[
                        employee_index,
                        day_index,
                        request_code_map[
                            request_type
                        ]
                    ]

                    == 1

                )


    # ========================================================
    # 個人ごとの勤務回数
    # ========================================================

    shift_name_to_code = {

        "日勤": DAY,
        "リーダー": LEADER,
        "半日": HALF,
        "準夜": EVENING

    }


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


        for shift_type in [

            "日勤",
            "リーダー",
            "半日",
            "準夜"

        ]:


            if shift_type in limit_dictionary:

                min_count, max_count = (

                    limit_dictionary[
                        shift_type
                    ]

                )


                shift_code = shift_name_to_code[
                    shift_type
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
                    total_count >= min_count
                )


                model.Add(
                    total_count <= max_count
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

                start_day
                + max_consecutive
                + 1

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
    # グループ別・全体人数条件
    # ========================================================

    conditions = create_condition_dictionary()


    for day_index in range(
        days_in_month
    ):


        day_number = day_index + 1


        condition_type = get_day_type(

            year,
            month,
            day_number

        )


        # ----------------------------------------------------
        # A / B / 全体
        # ----------------------------------------------------

        for group_name in [

            "A",
            "B",
            "全体"

        ]:


            # ====================================================
            # 対象社員を取得
            # ====================================================

            if group_name == "全体":


                # 全社員を対象
                # 「指定なし」も含む

                group_employees = [

                    index

                    for index, employee in enumerate(
                        employees
                    )

                ]


            else:


                # AまたはBに所属する社員のみ

                group_employees = [

                    index

                    for index, employee in enumerate(
                        employees
                    )

                    if employee[
                        "group_name"
                    ] == group_name

                ]


            # ====================================================
            # 日勤人数
            # ====================================================

            required_day = conditions.get(

                (
                    condition_type,
                    group_name,
                    "日勤"
                ),

                0

            )


            # リーダーは日勤人数にも含める

            model.Add(

                sum(

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

                    for employee_index
                    in group_employees

                )

                >= required_day

            )


            # ====================================================
            # リーダー人数
            # ====================================================

            required_leader = conditions.get(

                (
                    condition_type,
                    group_name,
                    "リーダー"
                ),

                0

            )


            model.Add(

                sum(

                    shifts[
                        employee_index,
                        day_index,
                        LEADER
                    ]

                    for employee_index
                    in group_employees

                )

                >= required_leader

            )


            # ====================================================
            # 半日人数
            # ====================================================

            required_half = conditions.get(

                (
                    condition_type,
                    group_name,
                    "半日"
                ),

                0

            )


            model.Add(

                sum(

                    shifts[
                        employee_index,
                        day_index,
                        HALF
                    ]

                    for employee_index
                    in group_employees

                )

                >= required_half

            )


            # ====================================================
            # 準夜人数
            # ====================================================

            required_evening = conditions.get(

                (
                    condition_type,
                    group_name,
                    "準夜"
                ),

                0

            )


            model.Add(

                sum(

                    shifts[
                        employee_index,
                        day_index,
                        EVENING
                    ]

                    for employee_index
                    in group_employees

                )

                >= required_evening

            )


            # ====================================================
            # 深夜人数
            # ====================================================

            required_night = conditions.get(

                (
                    condition_type,
                    group_name,
                    "深夜"
                ),

                0

            )


            model.Add(

                sum(

                    shifts[
                        employee_index,
                        day_index,
                        NIGHT
                    ]

                    for employee_index
                    in group_employees

                )

                >= required_night

            )


    # ========================================================
    # 求解
    # ========================================================

    solver = cp_model.CpSolver()


    solver.parameters.max_time_in_seconds = 60


    status = solver.Solve(
        model
    )


    # ========================================================
    # 解なし
    # ========================================================

    if status not in [

        cp_model.OPTIMAL,
        cp_model.FEASIBLE

    ]:

        return None


    # ========================================================
    # 結果作成
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
# Excel出力
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


    weekday_names = [

        "月",
        "火",
        "水",
        "木",
        "金",
        "土",
        "日"

    ]


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

            f"{day}("
            f"{weekday_names[weekday]}"
            f")"

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
# データベース初期化
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


        col1, col2, col3 = st.columns(
            3
        )


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

                    "指定なし",
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


            if name == "":

                st.error(
                    "名前を入力してください。"
                )


            else:

                add_employee(

                    name,
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


    group_options = [

        "指定なし",
        "A",
        "B"

    ]


    for employee in employees:


        with st.expander(

            employee["name"]

        ):


            edit_name = st.text_input(

                "名前",

                employee["name"],

                key=f"name_"
                f"{employee['id']}"

            )


            current_group = employee[
                "group_name"
            ]


            # 古いDBとの互換性

            if current_group not in group_options:

                current_group = "指定なし"


            edit_group = st.selectbox(

                "グループ",

                group_options,

                index=group_options.index(
                    current_group
                ),

                key=f"group_"
                f"{employee['id']}"

            )


            edit_leader = st.checkbox(

                "リーダー可能",

                value=bool(

                    employee[
                        "can_leader"
                    ]

                ),

                key=f"leader_"
                f"{employee['id']}"

            )


            edit_max_consecutive = st.number_input(

                "最大連勤",

                min_value=1,
                max_value=31,

                value=employee[
                    "max_consecutive_days"
                ],

                key=f"max_"
                f"{employee['id']}"

            )


            col1, col2 = st.columns(
                2
            )


            with col1:


                if st.button(

                    "更新",

                    key=f"update_"
                    f"{employee['id']}"

                ):


                    update_employee(

                        employee["id"],

                        edit_name,

                        employee[
                            "employment_type"
                        ],

                        employee[
                            "gender"
                        ],

                        employee[
                            "experience_years"
                        ],

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

                    key=f"delete_"
                    f"{employee['id']}"

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

            employee["name"]:
            employee

            for employee in employees

        }


        selected_name = st.selectbox(

            "社員",

            list(
                employee_names.keys()
            )

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


        shift_limit_types = [

            "日勤",
            "リーダー",
            "半日",
            "準夜"

        ]


        with st.form(
            "shift_limits_form"
        ):


            values = {}


            for shift_type in shift_limit_types:


                current = limit_dictionary.get(

                    shift_type,

                    (0, 31)

                )


                col1, col2 = st.columns(
                    2
                )


                with col1:

                    minimum = st.number_input(

                        f"{shift_type} 最低回数",

                        min_value=0,
                        max_value=31,

                        value=current[0],

                        key=f"min_"
                        f"{shift_type}"

                    )


                with col2:

                    maximum = st.number_input(

                        f"{shift_type} 最大回数",

                        min_value=0,
                        max_value=31,

                        value=current[1],

                        key=f"max_"
                        f"{shift_type}"

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


                for shift_type, value in values.items():

                    minimum, maximum = value


                    if minimum > maximum:

                        st.error(

                            f"{shift_type}の最低回数が"
                            f"最大回数を超えています。"

                        )

                        break


                else:


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
# 希望入力
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


        col1, col2 = st.columns(
            2
        )


        with col1:

            selected_year = st.number_input(

                "対象年",

                min_value=2026,
                max_value=2100,
                value=2026

            )


        with col2:

            selected_month = st.number_input(

                "対象月",

                min_value=1,
                max_value=12,
                value=9

            )


        days_in_month = calendar.monthrange(

            selected_year,
            selected_month

        )[1]


        rows = []


        weekday_names = [

            "月",
            "火",
            "水",
            "木",
            "金",
            "土",
            "日"

        ]


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

                "社員名":
                employee["name"]

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
                    f"({weekday_names[weekday]})"

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


            rows.append(
                row
            )


        dataframe = pd.DataFrame(
            rows
        )


        st.write(

            "記号："
            "― 指定なし / "
            "公 公休 / "
            "有 有休 / "
            "日 日勤 / "
            "L リーダー / "
            "半 半日 / "
            "準 準夜 / "
            "深 深夜"

        )


        column_config = {


            "社員名":

            st.column_config.TextColumn(

                "社員名",

                disabled=True,

                width="medium"

            )

        }


        for column in dataframe.columns[1:]:


            column_config[
                column
            ] = st.column_config.SelectboxColumn(

                column,

                options=list(
                    SHIFT_SHORT_NAMES.values()
                ),

                width="medium"

            )


        edited_dataframe = st.data_editor(

            dataframe,

            column_config=column_config,

            hide_index=True,

            use_container_width=True,

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
                        f"({weekday_names[weekday]})"

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


# ============================================================
# 人数条件
# ============================================================

elif menu == "人数条件":

    st.header(
        "日別・グループ別・全体人数条件"
    )


    st.write(
        "リーダーは日勤人数にも含まれます。"
    )


    st.write(
        "全体には、グループA・B・指定なしの全社員が含まれます。"
    )


    condition_types = [

        "通常",
        "水曜",
        "土曜",
        "日祝"

    ]


    groups = [

        "A",
        "B",
        "全体"

    ]


    shifts = [

        "日勤",
        "リーダー",
        "半日",
        "準夜",
        "深夜"

    ]


    existing_conditions = create_condition_dictionary()


    for condition_type in condition_types:


        st.subheader(
            f"{condition_type}の条件"
        )


        for group_name in groups:


            if group_name == "全体":

                st.write(
                    "全体（全社員）"
                )

            else:

                st.write(
                    f"グループ{group_name}"
                )


            columns = st.columns(
                len(shifts)
            )


            values = {}


            for index, shift_type in enumerate(
                shifts
            ):


                key = (

                    condition_type,
                    group_name,
                    shift_type

                )


                default_value = existing_conditions.get(

                    key,

                    0

                )


                with columns[index]:


                    values[
                        shift_type
                    ] = st.number_input(

                        shift_type,

                        min_value=0,
                        max_value=100,

                        value=default_value,

                        key=(
                            f"{condition_type}_"
                            f"{group_name}_"
                            f"{shift_type}"
                        )

                    )


            if st.button(

                (
                    f"{condition_type} "
                    f"{group_name} 保存"
                ),

                key=(
                    f"save_"
                    f"{condition_type}_"
                    f"{group_name}"
                )

            ):


                for shift_type, value in values.items():

                    save_staffing_condition(

                        condition_type,
                        group_name,
                        shift_type,
                        value

                    )


                st.success(
                    "保存しました。"
                )


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


        col1, col2 = st.columns(
            2
        )


        with col1:

            selected_year = st.number_input(

                "生成する年",

                min_value=2026,
                max_value=2100,
                value=2026,

                key="generate_year"

            )


        with col2:

            selected_month = st.number_input(

                "生成する月",

                min_value=1,
                max_value=12,
                value=9,

                key="generate_month"

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


            else:


                st.success(
                    "シフトを生成しました。"
                )


                days_in_month = calendar.monthrange(

                    selected_year,
                    selected_month

                )[1]


                columns = []


                weekday_names = [

                    "月",
                    "火",
                    "水",
                    "木",
                    "金",
                    "土",
                    "日"

                ]


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

                        f"{day}("
                        f"{weekday_names[weekday]}"
                        f")"

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

