import streamlit as st
import requests
import pandas as pd
import re
import time
import os

from io import BytesIO

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import mm

from pypdf import PdfReader, PdfWriter


# ============================================================
# НАСТРОЙКИ
# ============================================================

APP_TITLE = "Ozon FBS — отправления и этикетки"

INFO_LABEL_WIDTH = 58 * mm
INFO_LABEL_HEIGHT = 40 * mm

FONT_NAME = "Roboto"

# Максимальное ожидание формирования PDF
MAX_WAIT_SECONDS = 600

# Интервалы между проверками
POLL_INTERVALS = [
    3,
    5,
    8,
    10,
    15,
    20,
    25,
    30,
]

# Только эти статусы считаем отправлениями "к сборке"
BUILD_STATUSES = {
    "awaiting_packaging",
    "awaiting_deliver",
}

# ============================================================
# РУССКИЕ СТАТУСЫ
# ============================================================

STATUS_RU = {
    "acceptance_in_progress": "Идёт приёмка",
    "awaiting_deliver": "Ожидает передачи",
    "awaiting_packaging": "Ожидает упаковки",
    "cancelled": "Отменено",
    "delivered": "Доставлено",
    "delivering": "Доставляется",
}


def status_ru(status):
    if not status:
        return ""

    status = str(status).strip()

    return STATUS_RU.get(
        status,
        status
    )


# ============================================================
# ШРИФТЫ
# ============================================================

def register_fonts():

    candidates = [
        "Roboto_Full_Final.ttf",
        "Roboto-Regular.ttf",
        "OzonFont_Fix.ttf",
    ]

    for font_file in candidates:

        if not os.path.exists(font_file):
            continue

        try:

            pdfmetrics.registerFont(
                TTFont(
                    FONT_NAME,
                    font_file
                )
            )

            return font_file

        except Exception:
            pass

    return None


FONT_FILE = register_fonts()


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================

def normalize_text(value):

    if value is None:
        return ""

    try:

        if pd.isna(value):
            return ""

    except Exception:
        pass

    value = str(value)

    value = value.replace(
        "\xa0",
        " "
    )

    value = value.replace(
        "\u200b",
        ""
    )

    value = value.replace(
        "\ufeff",
        ""
    )

    value = re.sub(
        r"[ \t]+",
        " ",
        value
    )

    return value.strip()


def normalize_order(value):

    if value is None:
        return ""

    value = str(value).strip()

    value = value.replace(
        "\xa0",
        ""
    )

    value = value.replace(
        " ",
        ""
    )

    value = value.replace(
        "–",
        "-"
    )

    value = value.replace(
        "—",
        "-"
    )

    return value


def clean_number(value):

    if value is None:
        return ""

    try:

        if pd.isna(value):
            return ""

    except Exception:
        pass

    value = str(value).strip()

    value = value.replace(
        "\xa0",
        ""
    )

    value = value.replace(
        " ",
        ""
    )

    value = value.replace(
        ",",
        "."
    )

    return value


# ============================================================
# ПОИСК КОЛОНКИ
# ============================================================

def find_column(
    df,
    variants
):

    if df is None:
        return None

    normalized_columns = {}

    for col in df.columns:

        key = normalize_text(
            col
        ).lower()

        normalized_columns[key] = col

    # Сначала точное совпадение

    for variant in variants:

        key = normalize_text(
            variant
        ).lower()

        if key in normalized_columns:
            return normalized_columns[key]

    # Потом частичное

    for col in df.columns:

        col_norm = normalize_text(
            col
        ).lower()

        for variant in variants:

            variant_norm = normalize_text(
                variant
            ).lower()

            if variant_norm in col_norm:
                return col

    return None


# ============================================================
# ЧТЕНИЕ EXCEL / CSV
# ============================================================

def read_api_file(
    uploaded_file
):

    if uploaded_file is None:
        return None

    filename = uploaded_file.name.lower()

    try:

        if filename.endswith(".csv"):

            raw = uploaded_file.getvalue()

            for encoding in [
                "utf-8-sig",
                "utf-8",
                "cp1251",
            ]:

                try:

                    text = raw.decode(
                        encoding
                    )

                    from io import StringIO

                    return pd.read_csv(
                        StringIO(text),
                        sep=None,
                        engine="python"
                    )

                except Exception:
                    continue

            raise ValueError(
                "Не удалось прочитать CSV"
            )

        if filename.endswith(
            (".xlsx", ".xls")
        ):

            return pd.read_excel(
                uploaded_file
            )

        raise ValueError(
            "Поддерживаются XLSX, XLS и CSV"
        )

    except Exception as e:

        raise ValueError(
            f"Ошибка чтения файла: {e}"
        )


# ============================================================
# НОРМАЛИЗАЦИЯ API-ТАБЛИЦЫ
# ============================================================

def prepare_api_dataframe(
    df
):

    if df is None:
        return pd.DataFrame()

    df = df.copy()

    # --------------------------------------------------------
    # Поиск колонок
    # --------------------------------------------------------

    order_col = find_column(
        df,
        [
            "номер отправления",
            "номер отправки",
            "номер заказа",
            "posting number",
            "posting_number",
            "posting",
            "отправление",
        ]
    )

    status_col = find_column(
        df,
        [
            "статус",
            "status",
            "status_alias",
            "статус отправления",
        ]
    )

    article_col = find_column(
        df,
        [
            "артикул",
            "артикул продавца",
            "артикул товара",
            "offer_id",
            "offer id",
            "offer",
        ]
    )

    sku_col = find_column(
        df,
        [
            "sku",
            "SKU",
            "идентификатор товара",
            "product_id",
        ]
    )

    name_col = find_column(
        df,
        [
            "название",
            "наименование",
            "название товара",
            "товар",
            "product name",
        ]
    )

    quantity_col = find_column(
        df,
        [
            "количество",
            "кол-во",
            "колво",
            "qty",
            "quantity",
        ]
    )

    scanit_col = find_column(
        df,
        [
            "scanit",
            "ScanIt",
            "штрихкод",
            "штрихкод этикетки",
            "barcode",
        ]
    )

    # --------------------------------------------------------
    # Номер отправления обязателен
    # --------------------------------------------------------

    if not order_col:

        raise ValueError(
            "В API-файле не найдена колонка "
            "номера отправления."
        )

    result = []

    for _, row in df.iterrows():

        order = normalize_order(
            row.get(
                order_col,
                ""
            )
        )

        if not order:
            continue

        status = ""

        if status_col:

            status = normalize_text(
                row.get(
                    status_col,
                    ""
                )
            )

        article = ""

        if article_col:

            article = normalize_text(
                row.get(
                    article_col,
                    ""
                )
            )

        sku = ""

        if sku_col:

            sku = normalize_text(
                row.get(
                    sku_col,
                    ""
                )
            )

        name = ""

        if name_col:

            name = normalize_text(
                row.get(
                    name_col,
                    ""
                )
            )

        quantity = ""

        if quantity_col:

            quantity = clean_number(
                row.get(
                    quantity_col,
                    ""
                )
            )

        scanit = ""

        if scanit_col:

            scanit = normalize_text(
                row.get(
                    scanit_col,
                    ""
                )
            )

        result.append({

            "Отправление":
                order,

            "Статус API":
                status,

            "Статус":
                status_ru(
                    status
                ),

            "Артикул":
                article,

            "SKU":
                sku,

            "Количество":
                quantity,

            "Название":
                name,

            "scanit":
                scanit,

            "_raw":
                row.to_dict(),

        })

    return pd.DataFrame(
        result
    )


# ============================================================
# УНИКАЛЬНЫЕ ОТПРАВЛЕНИЯ
# ============================================================

def make_unique_postings(
    df
):

    if df.empty:
        return df

    # Если в выгрузке несколько строк одного posting,
    # сохраняем все товары отдельно, но список отправлений
    # для создания этикеток должен быть уникальным.

    return df.copy()


def get_unique_posting_numbers(
    df
):

    if df.empty:
        return []

    result = []

    for value in df[
        "Отправление"
    ].tolist():

        order = normalize_order(
            value
        )

        if order and order not in result:

            result.append(
                order
            )

    return result


# ============================================================
# Ozon API
# ============================================================

def ozon_headers(
    client_id,
    api_key
):

    return {
        "Client-Id":
            str(client_id).strip(),

        "Api-Key":
            str(api_key).strip(),

        "Content-Type":
            "application/json",
    }


# ============================================================
# ПОЛУЧЕНИЕ ОТПРАВЛЕНИЙ
#
# Здесь поддерживаем уже подготовленный Excel,
# который пользователь получает из API.
# ============================================================

def filter_build_postings(
    df
):

    if df.empty:
        return df

    return df[
        df["Статус API"].isin(
            BUILD_STATUSES
        )
    ].copy()


# ============================================================
# СОЗДАНИЕ ЗАДАНИЯ НА ЭТИКЕТКИ
# ============================================================

def create_label_task(
    client_id,
    api_key,
    posting_numbers
):

    url = (
        "https://api-seller.ozon.ru"
        "/v3/posting/fbs/package-label/create"
    )

    # Убираем дубли

    unique_numbers = []

    for posting in posting_numbers:

        posting = normalize_order(
            posting
        )

        if (
            posting
            and posting not in unique_numbers
        ):

            unique_numbers.append(
                posting
            )

    if not unique_numbers:

        raise ValueError(
            "Нет отправлений для формирования этикеток."
        )

    payload = {
        "posting_numbers":
            unique_numbers
    }

    response = requests.post(
        url,
        headers=ozon_headers(
            client_id,
            api_key
        ),
        json=payload,
        timeout=60
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Ozon API HTTP "
            f"{response.status_code}: "
            f"{response.text}"
        )

    data = response.json()

    task_id = (
        data.get("result")
        or data.get("task_id")
        or data.get("id")
    )

    if isinstance(
        task_id,
        dict
    ):

        task_id = (
            task_id.get("task_id")
            or task_id.get("id")
        )

    if not task_id:

        raise RuntimeError(
            "Ozon не вернул ID задания.\n\n"
            + str(data)
        )

    return str(
        task_id
    ), data


# ============================================================
# ПРОВЕРКА ЗАДАНИЯ
# ============================================================

def get_label_task_status(
    client_id,
    api_key,
    task_id
):

    url = (
        "https://api-seller.ozon.ru"
        "/v2/posting/fbs/package-label/get"
    )

    # В зависимости от версии ответа Ozon
    # идентификатор может называться task_id.

    payload = {
        "task_id":
            int(task_id)
            if str(task_id).isdigit()
            else task_id
    }

    response = requests.post(
        url,
        headers=ozon_headers(
            client_id,
            api_key
        ),
        json=payload,
        timeout=60
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Ozon API HTTP "
            f"{response.status_code}: "
            f"{response.text}"
        )

    return response.json()


# ============================================================
# ПОЛУЧЕНИЕ PDF
# ============================================================

def extract_pdf_bytes(
    data
):

    if not data:
        return None

    # Возможные варианты ответа Ozon

    candidates = [
        data.get("result"),
        data.get("file"),
        data.get("pdf"),
        data.get("content"),
    ]

    for candidate in candidates:

        if isinstance(
            candidate,
            str
        ):

            # Base64

            import base64

            try:

                decoded = base64.b64decode(
                    candidate,
                    validate=True
                )

                if decoded.startswith(
                    b"%PDF"
                ):

                    return decoded

            except Exception:
                pass

    return None


# ============================================================
# СКАЧИВАНИЕ PDF ПО URL
# ============================================================

def download_pdf_from_url(
    url
):

    if not url:
        return None

    try:

        response = requests.get(
            url,
            timeout=120
        )

        if response.status_code == 200:

            content = response.content

            if content.startswith(
                b"%PDF"
            ):

                return content

    except Exception:
        pass

    return None


# ============================================================
# ПОИСК URL PDF В ОТВЕТЕ
# ============================================================

def find_pdf_url(
    data
):

    if not isinstance(
        data,
        dict
    ):
        return None

    keys = [
        "url",
        "file_url",
        "pdf_url",
        "download_url",
    ]

    for key in keys:

        value = data.get(
            key
        )

        if isinstance(
            value,
            str
        ) and value.startswith(
            "http"
        ):

            return value

    for value in data.values():

        if isinstance(
            value,
            dict
        ):

            result = find_pdf_url(
                value
            )

            if result:
                return result

        elif isinstance(
            value,
            list
        ):

            for item in value:

                if isinstance(
                    item,
                    dict
                ):

                    result = find_pdf_url(
                        item
                    )

                    if result:
                        return result

    return None


# ============================================================
# ОЖИДАНИЕ ЗАДАНИЯ
# ============================================================

def wait_for_label_task(
    client_id,
    api_key,
    task_id,
    max_wait=MAX_WAIT_SECONDS
):

    start = time.time()

    attempt = 0

    last_data = None

    while True:

        elapsed = int(
            time.time() - start
        )

        if elapsed >= max_wait:

            return {
                "timeout": True,
                "data": last_data,
            }

        try:

            data = get_label_task_status(
                client_id,
                api_key,
                task_id
            )

            last_data = data

            # ------------------------------------------------
            # Находим статус
            # ------------------------------------------------

            status = ""

            if isinstance(
                data,
                dict
            ):

                result = data.get(
                    "result"
                )

                if isinstance(
                    result,
                    dict
                ):

                    status = (
                        result.get("code")
                        or result.get("status")
                        or ""
                    )

                status = (
                    status
                    or data.get("code")
                    or data.get("status")
                    or ""
                )

            status = str(
                status
            ).lower()

            # ------------------------------------------------
            # ГОТОВО
            # ------------------------------------------------

            if status in {
                "completed",
                "complete",
                "done",
                "success",
            }:

                return {
                    "timeout": False,
                    "completed": True,
                    "data": data,
                }

            # ------------------------------------------------
            # ОШИБКА
            # ------------------------------------------------

            if status in {
                "failed",
                "error",
                "cancelled",
            }:

                return {
                    "timeout": False,
                    "completed": False,
                    "failed": True,
                    "data": data,
                }

        except Exception as e:

            last_data = {
                "error":
                    str(e)
            }

        # ----------------------------------------------------
        # Ждём
        # ----------------------------------------------------

        if attempt < len(
            POLL_INTERVALS
        ):

            sleep_seconds = (
                POLL_INTERVALS[attempt]
            )

        else:

            sleep_seconds = 30

        attempt += 1

        remaining = max(
            0,
            max_wait - (
                time.time() - start
            )
        )

        sleep_seconds = min(
            sleep_seconds,
            remaining
        )

        if sleep_seconds <= 0:
            break

        time.sleep(
            sleep_seconds
        )

    return {
        "timeout": True,
        "data": last_data,
    }


# ============================================================
# РАЗБОР РЕЗУЛЬТАТА Ozon
# ============================================================

def parse_label_result(
    task_data
):

    if not task_data:
        return {
            "printed": [],
            "unprinted": [],
            "total": 0,
        }

    result = task_data.get(
        "result"
    )

    if not isinstance(
        result,
        dict
    ):

        result = task_data

    printed_count = result.get(
        "printed_postings_count"
    )

    postings_count = result.get(
        "postings_count"
    )

    unprinted = result.get(
        "unprinted_postings",
        []
    )

    if not isinstance(
        unprinted,
        list
    ):
        unprinted = []

    unprinted_map = {}

    for item in unprinted:

        if not isinstance(
            item,
            dict
        ):
            continue

        posting = normalize_order(
            item.get(
                "posting_number",
                ""
            )
        )

        message = normalize_text(
            item.get(
                "message",
                ""
            )
        )

        if posting:

            unprinted_map[
                posting
            ] = message

    return {
        "printed_count":
            printed_count
            if printed_count is not None
            else 0,

        "postings_count":
            postings_count
            if postings_count is not None
            else 0,

        "unprinted":
            unprinted_map,

        "raw":
            result,
    }


# ============================================================
# ИНФОРМАЦИОННАЯ ЭТИКЕТКА
# ============================================================

def wrap_text(
    text,
    max_chars
):

    text = normalize_text(
        text
    )

    if not text:
        return []

    words = text.split()

    lines = []

    current = ""

    for word in words:

        if not current:

            current = word

        elif len(
            current
            + " "
            + word
        ) <= max_chars:

            current += (
                " "
                + word
            )

        else:

            lines.append(
                current
            )

            current = word

    if current:
        lines.append(
            current
        )

    return lines


def create_info_page(
    order_data
):

    buffer = BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=(
            INFO_LABEL_WIDTH,
            INFO_LABEL_HEIGHT
        )
    )

    left = 3 * mm

    y = (
        INFO_LABEL_HEIGHT
        - 5 * mm
    )

    # --------------------------------------------------------
    # Данные
    # --------------------------------------------------------

    order = normalize_text(
        order_data.get(
            "Отправление",
            ""
        )
    )

    article = normalize_text(
        order_data.get(
            "Артикул",
            ""
        )
    )

    quantity = normalize_text(
        order_data.get(
            "Количество",
            ""
        )
    )

    name = normalize_text(
        order_data.get(
            "Название",
            ""
        )
    )

    scanit = normalize_text(
        order_data.get(
            "scanit",
            ""
        )
    )

    # --------------------------------------------------------
    # Номер отправления
    # --------------------------------------------------------

    c.setFont(
        FONT_NAME,
        8.5
    )

    c.drawString(
        left,
        y,
        f"Отправление: {order}"
    )

    y -= 6 * mm

    # --------------------------------------------------------
    # Артикул
    # --------------------------------------------------------

    c.drawString(
        left,
        y,
        f"Арт: {article or '-'}"
    )

    y -= 6 * mm

    # --------------------------------------------------------
    # КОЛИЧЕСТВО
    #
    # ВАЖНО: 16 кегль
    # --------------------------------------------------------

    c.setFont(
        FONT_NAME,
        16
    )

    c.drawString(
        left,
        y,
        f"КОЛ-ВО: {quantity or '?'}"
    )

    y -= 8 * mm

    # --------------------------------------------------------
    # Название
    # --------------------------------------------------------

    c.setFont(
        FONT_NAME,
        8.5
    )

    c.drawString(
        left,
        y,
        "Название:"
    )

    y -= 4.5 * mm

    name_lines = wrap_text(
        name or "НЕ НАЙДЕНО",
        34
    )

    for line in name_lines[:2]:

        c.drawString(
            left,
            y,
            line
        )

        y -= 4 * mm

    # --------------------------------------------------------
    # scanit
    #
    # Небольшим шрифтом,
    # чтобы он был контрольным идентификатором.
    # --------------------------------------------------------

    if scanit:

        c.setFont(
            FONT_NAME,
            6.5
        )

        y -= 1 * mm

        c.drawString(
            left,
            y,
            f"scanit: {scanit}"
        )

    c.showPage()
    c.save()

    buffer.seek(0)

    return buffer.getvalue()


# ============================================================
# СОЗДАНИЕ PDF ИНФОРМАЦИОННЫХ ЭТИКЕТОК
# ============================================================

def build_info_labels_pdf(
    build_df
):

    writer = PdfWriter()

    for _, row in build_df.iterrows():

        data = row.to_dict()

        pdf_bytes = create_info_page(
            data
        )

        reader = PdfReader(
            BytesIO(pdf_bytes)
        )

        writer.add_page(
            reader.pages[0]
        )

    output = BytesIO()

    writer.write(
        output
    )

    output.seek(0)

    return output.getvalue()


# ============================================================
# EXCEL
# ============================================================

def build_excel(
    df,
    build_postings,
    label_result
):

    output = BytesIO()

    result_df = df.copy()

    unprinted = label_result.get(
        "unprinted",
        {}
    )

    build_set = set(
        build_postings
    )

    # --------------------------------------------------------
    # Функции
    # --------------------------------------------------------

    def is_build(order):

        return (
            normalize_order(order)
            in build_set
        )

    def label_status(row):

        order = normalize_order(
            row.get(
                "Отправление",
                ""
            )
        )

        if not is_build(order):

            return "Не требуется"

        if order in unprinted:

            return "Не сформирована"

        # Если отправление входило
        # в запрос и Ozon его не вернул
        # как unprinted — считаем успешным.

        return "Сформирована"

    def label_reason(row):

        order = normalize_order(
            row.get(
                "Отправление",
                ""
            )
        )

        if not is_build(order):

            return ""

        return unprinted.get(
            order,
            ""
        )

    result_df[
        "К сборке"
    ] = result_df[
        "Отправление"
    ].apply(
        is_build
    )

    result_df[
        "Этикетка"
    ] = result_df.apply(
        label_status,
        axis=1
    )

    result_df[
        "Причина"
    ] = result_df.apply(
        label_reason,
        axis=1
    )

    # --------------------------------------------------------
    # Русские названия уже подготовлены
    # --------------------------------------------------------

    columns = [
        "Отправление",
        "Статус API",
        "Статус",
        "К сборке",
        "Этикетка",
        "Причина",
        "Артикул",
        "SKU",
        "Количество",
        "scanit",
        "Название",
    ]

    columns = [
        col
        for col in columns
        if col in result_df.columns
    ]

    result_df = result_df[
        columns
    ]

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        result_df.to_excel(
            writer,
            index=False,
            sheet_name="Отправления"
        )

        # ----------------------------------------------------
        # Отдельный лист статистики
        # ----------------------------------------------------

        stats = pd.DataFrame([
            {
                "Показатель":
                    "Всего отправлений",
                "Количество":
                    len(
                        result_df[
                            "Отправление"
                        ].unique()
                    ),
            },
            {
                "Показатель":
                    "К сборке",
                "Количество":
                    len(
                        build_set
                    ),
            },
            {
                "Показатель":
                    "Этикетка сформирована",
                "Количество":
                    sum(
                        1
                        for order
                        in build_set
                        if order
                        not in unprinted
                    ),
            },
            {
                "Показатель":
                    "Этикетка не сформирована",
                "Количество":
                    len(
                        unprinted
                    ),
            },
        ])

        stats.to_excel(
            writer,
            index=False,
            sheet_name="Статистика"
        )

    output.seek(0)

    return output.getvalue()


# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(
    page_title=APP_TITLE,
    layout="wide"
)

st.title(
    "📦 Ozon FBS — отправления и этикетки"
)

st.caption(
    "API → отправления → статусы → scanit → этикетки"
)


# ============================================================
# API ДАННЫЕ
# ============================================================

st.subheader(
    "1. Загрузка отправлений"
)

api_file = st.file_uploader(
    "Excel / CSV с отправлениями Ozon API",
    type=[
        "xlsx",
        "xls",
        "csv",
    ],
    key="api_file"
)


# ============================================================
# КЛЮЧИ
# ============================================================

st.subheader(
    "2. Ozon API"
)

col1, col2 = st.columns(2)

with col1:

    client_id = st.text_input(
        "Client-Id",
        type="password"
    )

with col2:

    api_key = st.text_input(
        "Api-Key",
        type="password"
    )


# ============================================================
# КНОПКА АНАЛИЗА
# ============================================================

if st.button(
    "🔎 Загрузить и проверить отправления",
    type="primary",
    use_container_width=True
):

    if not api_file:

        st.error(
            "Загрузите Excel/CSV с отправлениями."
        )

        st.stop()

    try:

        with st.spinner(
            "Читаю отправления..."
        ):

            raw_df = read_api_file(
                api_file
            )

            df = prepare_api_dataframe(
                raw_df
            )

        if df.empty:

            st.error(
                "Не найдено ни одного отправления."
            )

            st.stop()

        # ----------------------------------------------------
        # Уникальные отправления
        # ----------------------------------------------------

        unique_orders = get_unique_posting_numbers(
            df
        )

        build_df = filter_build_postings(
            df
        )

        build_orders = get_unique_posting_numbers(
            build_df
        )

        # ----------------------------------------------------
        # Статистика
        # ----------------------------------------------------

        st.success(
            f"Загружено из API: "
            f"{len(unique_orders)} отправлений"
        )

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Всего",
            len(unique_orders)
        )

        c2.metric(
            "К сборке",
            len(build_orders)
        )

        c3.metric(
            "Идёт приёмка",
            len(
                set(
                    get_unique_posting_numbers(
                        df[
                            df[
                                "Статус API"
                            ]
                            == "acceptance_in_progress"
                        ]
                    )
                )
            )
        )

        c4.metric(
            "Остальные",
            max(
                0,
                len(unique_orders)
                - len(build_orders)
            )
        )

        # ----------------------------------------------------
        # Таблица отправлений к сборке
        # ----------------------------------------------------

        st.subheader(
            "📦 Отправления к сборке"
        )

        st.dataframe(
            build_df[
                [
                    "Отправление",
                    "Статус",
                    "Артикул",
                    "SKU",
                    "Количество",
                    "scanit",
                    "Название",
                ]
            ],
            use_container_width=True,
            height=400
        )

        # ----------------------------------------------------
        # Сохраняем в session_state
        # ----------------------------------------------------

        st.session_state[
            "ozon_df"
        ] = df

        st.session_state[
            "ozon_build_df"
        ] = build_df

        st.session_state[
            "ozon_build_orders"
        ] = build_orders

    except Exception as e:

        st.error(
            f"Ошибка: {e}"
        )

        st.exception(e)


# ============================================================
# ПОЛУЧЕНИЕ ЭТИКЕТОК
# ============================================================

if (
    "ozon_build_orders"
    in st.session_state
):

    build_orders = st.session_state[
        "ozon_build_orders"
    ]

    build_df = st.session_state[
        "ozon_build_df"
    ]

    df = st.session_state[
        "ozon_df"
    ]

    st.divider()

    st.subheader(
        "🏷️ Получение этикеток через новый Ozon API"
    )

    st.info(
        f"Отправлений для запроса этикеток: "
        f"**{len(build_orders)}**"
    )

    if not client_id or not api_key:

        st.warning(
            "Введите Client-Id и Api-Key."
        )

    else:

        if st.button(
            f"🏷️ Получить этикетки для {len(build_orders)} отправлений",
            type="primary",
            use_container_width=True
        ):

            try:

                # =================================================
                # СОЗДАНИЕ ЗАДАНИЯ
                # =================================================

                with st.spinner(
                    "Создаю задание на этикетки..."
                ):

                    task_id, create_response = (
                        create_label_task(
                            client_id,
                            api_key,
                            build_orders
                        )
                    )

                st.success(
                    f"Создано задание: "
                    f"**{task_id}**"
                )

                # =================================================
                # ОЖИДАНИЕ
                # =================================================

                progress_text = st.empty()

                progress_bar = st.progress(
                    0
                )

                start_time = time.time()

                # Здесь используем собственную проверку,
                # чтобы красиво показывать ожидание.

                result = wait_for_label_task(
                    client_id,
                    api_key,
                    task_id,
                    MAX_WAIT_SECONDS
                )

                elapsed = int(
                    time.time()
                    - start_time
                )

                progress_bar.progress(
                    1.0
                )

                progress_text.empty()

                # =================================================
                # TIMEOUT
                # =================================================

                if result.get(
                    "timeout"
                ):

                    st.warning(
                        f"Задание {task_id} ещё не "
                        f"успело завершиться за "
                        f"{MAX_WAIT_SECONDS} секунд."
                    )

                    st.info(
                        "Само задание Ozon могло продолжить "
                        "формирование после завершения ожидания."
                    )

                    st.session_state[
                        "last_task_id"
                    ] = task_id

                    st.stop()

                task_data = result.get(
                    "data"
                )

                # =================================================
                # РАЗБОР
                # =================================================

                label_result = parse_label_result(
                    task_data
                )

                printed_count = label_result[
                    "printed_count"
                ]

                postings_count = label_result[
                    "postings_count"
                ]

                unprinted = label_result[
                    "unprinted"
                ]

                # =================================================
                # СТАТИСТИКА
                # =================================================

                st.subheader(
                    "Результат формирования"
                )

                c1, c2, c3, c4 = st.columns(4)

                c1.metric(
                    "Запрошено",
                    len(build_orders)
                )

                c2.metric(
                    "Ozon обработал",
                    postings_count
                )

                c3.metric(
                    "Этикеток сформировано",
                    printed_count
                )

                c4.metric(
                    "Не сформировано",
                    len(unprinted)
                )

                # =================================================
                # ПРИЧИНЫ
                # =================================================

                if unprinted:

                    st.warning(
                        f"Ozon не сформировал "
                        f"{len(unprinted)} этикеток."
                    )

                    reason_rows = []

                    for posting, reason in (
                        unprinted.items()
                    ):

                        row = df[
                            df[
                                "Отправление"
                            ]
                            == posting
                        ]

                        status = ""

                        article = ""

                        if not row.empty:

                            status = row.iloc[0][
                                "Статус"
                            ]

                            article = row.iloc[0][
                                "Артикул"
                            ]

                        reason_rows.append({
                            "Отправление":
                                posting,

                            "Статус":
                                status,

                            "Артикул":
                                article,

                            "Причина":
                                reason,
                        })

                    reason_df = pd.DataFrame(
                        reason_rows
                    )

                    st.dataframe(
                        reason_df,
                        use_container_width=True
                    )

                else:

                    st.success(
                        "Ozon сформировал этикетки "
                        "для всех отправлений к сборке."
                    )

                # =================================================
                # PDF
                # =================================================

                pdf_bytes = extract_pdf_bytes(
                    task_data
                )

                if not pdf_bytes:

                    pdf_url = find_pdf_url(
                        task_data
                    )

                    if pdf_url:

                        with st.spinner(
                            "Скачиваю PDF..."
                        ):

                            pdf_bytes = (
                                download_pdf_from_url(
                                    pdf_url
                                )
                            )

                # =================================================
                # ИНФОРМАЦИОННЫЕ ЭТИКЕТКИ
                # =================================================

                st.subheader(
                    "🧾 Информационные этикетки"
                )

                # ВАЖНО:
                # создаём информационные этикетки
                # по ВСЕМ отправлениям к сборке,
                # независимо от того, сформировал ли
                # Ozon физическую этикетку.
                #
                # Это позволяет оператору видеть весь
                # рабочий список.

                info_pdf = build_info_labels_pdf(
                    build_df.drop_duplicates(
                        subset=[
                            "Отправление"
                        ]
                    )
                )

                st.success(
                    f"Сформировано информационных "
                    f"этикеток: {len(build_orders)}"
                )

                st.download_button(
                    "⬇️ Скачать информационные этикетки",
                    data=info_pdf,
                    file_name=(
                        "ozon_info_labels.pdf"
                    ),
                    mime="application/pdf",
                    use_container_width=True
                )

                # =================================================
                # ОРИГИНАЛЬНЫЕ ЭТИКЕТКИ OZON
                # =================================================

                if pdf_bytes:

                    st.success(
                        f"PDF этикеток Ozon получен."
                    )

                    st.download_button(
                        "⬇️ Скачать этикетки Ozon",
                        data=pdf_bytes,
                        file_name=(
                            f"ozon_labels_{task_id}.pdf"
                        ),
                        mime="application/pdf",
                        use_container_width=True
                    )

                else:

                    st.warning(
                        "Ozon завершил задание, "
                        "но PDF не найден в ответе."
                    )

                # =================================================
                # EXCEL
                # =================================================

                excel_bytes = build_excel(
                    df,
                    build_orders,
                    label_result
                )

                st.download_button(
                    "📊 Скачать Excel со всеми отправлениями",
                    data=excel_bytes,
                    file_name=(
                        f"ozon_postings_{task_id}.xlsx"
                    ),
                    mime=(
                        "application/"
                        "vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"
                    ),
                    use_container_width=True
                )

                # =================================================
                # Сохраняем результат
                # =================================================

                st.session_state[
                    "last_label_result"
                ] = label_result

                st.session_state[
                    "last_task_id"
                ] = task_id

                st.session_state[
                    "last_pdf"
                ] = pdf_bytes

                st.session_state[
                    "last_info_pdf"
                ] = info_pdf

            except Exception as e:

                st.error(
                    f"Ошибка получения этикеток: {e}"
                )

                st.exception(e)


# ============================================================
# ПОВТОРНАЯ ПРОВЕРКА ЗАДАНИЯ
# ============================================================

if (
    "last_task_id"
    in st.session_state
):

    task_id = st.session_state[
        "last_task_id"
    ]

    st.divider()

    st.subheader(
        "🔄 Проверка задания Ozon"
    )

    st.caption(
        f"Задание: {task_id}"
    )

    if client_id and api_key:

        if st.button(
            "🔄 Проверить задание ещё раз",
            use_container_width=True
        ):

            try:

                with st.spinner(
                    "Проверяю задание Ozon..."
                ):

                    task_data = (
                        get_label_task_status(
                            client_id,
                            api_key,
                            task_id
                        )
                    )

                label_result = parse_label_result(
                    task_data
                )

                st.success(
                    f"Статус получен. "
                    f"Сформировано: "
                    f"{label_result['printed_count']}"
                )

                if label_result[
                    "unprinted"
                ]:

                    st.dataframe(
                        pd.DataFrame([
                            {
                                "Отправление":
                                    posting,
                                "Причина":
                                    reason,
                            }
                            for posting, reason
                            in label_result[
                                "unprinted"
                            ].items()
                        ]),
                        use_container_width=True
                    )

            except Exception as e:

                st.error(
                    f"Ошибка проверки: {e}"
                )
