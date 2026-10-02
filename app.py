import streamlit as st
import requests
import pandas as pd
import io
import time
import base64
import re
from datetime import datetime, date, timedelta
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import landscape
from reportlab.lib.units import mm


# ============================================================
# OZON FBS API + НОВЫЕ ЭТИКЕТКИ 2026
#
# Список:
#   POST /v4/posting/fbs/list
#
# Этикетки:
#   POST /v3/posting/fbs/package-label/create
#   POST /v2/posting/fbs/package-label/get
#
# Новая миграция Ozon:
#   старые методы этикеток больше не используем.
# ============================================================


API_URL = "https://api-seller.ozon.ru"

LABEL_CREATE_URL = (
    API_URL + "/v3/posting/fbs/package-label/create"
)

LABEL_GET_URL = (
    API_URL + "/v2/posting/fbs/package-label/get"
)

FBS_LIST_URL = (
    API_URL + "/v4/posting/fbs/list"
)


# ============================================================
# СТАТУСЫ
# ============================================================

STATUS_RU = {
    "awaiting_packaging": "Ожидает упаковки",
    "awaiting_deliver": "Ожидает передачи",
    "acceptance_in_progress": "Идёт приёмка",
    "cancelled": "Отменено",
    "delivered": "Доставлено",
    "delivering": "В доставке",
    "driver_pickup": "Курьер забирает",
    "sent_by_seller": "Отправлено продавцом",
    "arbitration": "Арбитраж",
    "awaiting_registration": "Ожидает регистрации",
    "not_in_stock": "Нет товара",
}


# Статусы, которые по умолчанию считаем текущей сборкой
ASSEMBLY_STATUSES = [
    "awaiting_packaging",
    "awaiting_deliver",
]


# Можно дополнительно проверять уже находящиеся на приёмке
CHECK_LABEL_STATUSES = [
    "awaiting_packaging",
    "awaiting_deliver",
    "acceptance_in_progress",
]


# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(
    page_title="Ozon FBS API + этикетки",
    page_icon="📦",
    layout="wide",
)


st.title("📦 Ozon FBS — API + новые этикетки")

st.caption(
    "Источник данных — Ozon Seller API. "
    "Лист подбора не используется для определения существования отправлений."
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("🔐 Ozon API")

    client_id = st.text_input(
        "Client-Id",
        type="password",
        placeholder="Введите Client-Id",
    )

    api_key = st.text_input(
        "Api-Key",
        type="password",
        placeholder="Введите Api-Key",
    )

    st.divider()

    st.subheader("📅 Период")

    date_from = st.date_input(
        "Дата от",
        value=date.today(),
    )

    date_to = st.date_input(
        "Дата до",
        value=date.today(),
    )

    st.divider()

    st.subheader("📌 Статусы")

    status_awaiting_packaging = st.checkbox(
        "Ожидает упаковки",
        value=True,
    )

    status_awaiting_deliver = st.checkbox(
        "Ожидает передачи",
        value=True,
    )

    status_acceptance = st.checkbox(
        "Идёт приёмка",
        value=False,
        help=(
            "Это уже переданные на приёмку отправления. "
            "Они не считаются новыми заказами на сборку."
        ),
    )

    st.divider()

    include_labels = st.checkbox(
        "🏷 Получить этикетки через API",
        value=True,
    )

    create_info_labels = st.checkbox(
        "📝 Создать информационные этикетки",
        value=True,
    )

    st.divider()

    run_button = st.button(
        "🚀 Загрузить FBS",
        type="primary",
        use_container_width=True,
    )


# ============================================================
# ПРОВЕРКА ДАННЫХ
# ============================================================

if date_from > date_to:
    st.error("Дата «от» не может быть позже даты «до».")
    st.stop()


selected_statuses = []

if status_awaiting_packaging:
    selected_statuses.append("awaiting_packaging")

if status_awaiting_deliver:
    selected_statuses.append("awaiting_deliver")

if status_acceptance:
    selected_statuses.append("acceptance_in_progress")


if not selected_statuses:
    st.warning("Выберите хотя бы один статус.")
    st.stop()


# ============================================================
# API HEADERS
# ============================================================

def get_headers():
    return {
        "Client-Id": str(client_id).strip(),
        "Api-Key": str(api_key).strip(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ============================================================
# ОБЩИЙ POST
# ============================================================

def api_post(url, payload, timeout=60):

    response = requests.post(
        url,
        headers=get_headers(),
        json=payload,
        timeout=timeout,
    )

    if not response.ok:

        try:
            body = response.json()
        except Exception:
            body = response.text

        raise RuntimeError(
            f"HTTP {response.status_code}\n\n{body}"
        )

    return response


# ============================================================
# НОРМАЛИЗАЦИЯ СТАТУСА
# ============================================================

def status_ru(status):

    if not status:
        return ""

    return STATUS_RU.get(
        str(status),
        str(status),
    )


# ============================================================
# ИЗВЛЕЧЕНИЕ СКАНИТ
#
# Ozon постепенно добавляет новые поля в структуру posting.
# Поэтому ищем scanit рекурсивно.
# ============================================================

def find_scanit(obj):

    if obj is None:
        return ""

    if isinstance(obj, dict):

        for key, value in obj.items():

            key_l = str(key).lower()

            if key_l == "scanit":
                if value is not None:
                    return str(value)

            result = find_scanit(value)

            if result:
                return result

    elif isinstance(obj, list):

        for item in obj:

            result = find_scanit(item)

            if result:
                return result

    return ""


# ============================================================
# ФОРМАТИРОВАНИЕ ДАТЫ
# ============================================================

def iso_from_date(d, end_of_day=False):

    if end_of_day:

        return (
            d.strftime("%Y-%m-%d")
            + "T23:59:59.999Z"
        )

    return (
        d.strftime("%Y-%m-%d")
        + "T00:00:00Z"
    )


# ============================================================
# ЗАГРУЗКА FBS
#
# ВАЖНО:
# v4 имеет cursor pagination.
# postings находятся на верхнем уровне ответа.
# ============================================================

def get_fbs_postings(
    date_from,
    date_to,
    statuses,
):

    all_postings = []

    cursor = ""

    page = 0

    progress = st.progress(0)

    status_text = st.empty()

    while True:

        page += 1

        payload = {
            "filter": {
                "since": iso_from_date(date_from),
                "to": iso_from_date(
                    date_to,
                    end_of_day=True,
                ),
                "status": statuses,
            },

            "limit": 100,

            "cursor": cursor,

            "sort_dir": "asc",

            "with": {
                "analytics_data": True,
                "barcodes": True,
                "financial_data": False,
                "legal_info": True,
                "translit": False,
            },
        }

        response = api_post(
            FBS_LIST_URL,
            payload,
        )

        data = response.json()

        # ----------------------------------------------------
        # v4:
        # postings находятся НА ВЕРХНЕМ УРОВНЕ
        # ----------------------------------------------------

        postings = data.get(
            "postings",
            [],
        )

        # На случай изменения/обёртки API
        if not postings:

            result = data.get(
                "result"
            )

            if isinstance(result, dict):

                postings = result.get(
                    "postings",
                    [],
                )

        all_postings.extend(postings)

        has_next = bool(
            data.get("has_next", False)
        )

        if not has_next:

            result = data.get("result")

            if isinstance(result, dict):

                has_next = bool(
                    result.get(
                        "has_next",
                        False,
                    )
                )

        if has_next:

            cursor = data.get(
                "cursor",
                "",
            )

            if not cursor:

                result = data.get("result")

                if isinstance(result, dict):

                    cursor = result.get(
                        "cursor",
                        "",
                    )

            if not cursor:
                break

        else:
            break

        status_text.write(
            f"Страница API: {page} | "
            f"Получено отправлений: {len(all_postings)}"
        )

        progress.progress(
            min(
                0.95,
                0.05 * page,
            )
        )

    progress.progress(1.0)

    status_text.write(
        f"Загружено из API: {len(all_postings)} отправлений"
    )

    return all_postings


# ============================================================
# РАЗВОРАЧИВАНИЕ ТОВАРОВ
# ============================================================

def flatten_postings(postings):

    rows = []

    for posting in postings:

        posting_number = str(
            posting.get(
                "posting_number",
                "",
            )
        )

        status = str(
            posting.get(
                "status",
                "",
            )
        )

        scanit = find_scanit(posting)

        products = posting.get(
            "products",
            [],
        )

        if not products:

            rows.append({
                "Отправление": posting_number,
                "Статус API": status,
                "Статус": status_ru(status),
                "II / scanit": scanit,
                "Артикул продавца": "",
                "SKU": "",
                "Количество": "",
                "Название": "",
                "Дата создания": posting.get(
                    "in_process_at",
                    "",
                ),
                "Дата отгрузки": posting.get(
                    "shipment_date",
                    "",
                ),
                "Склад": (
                    posting
                    .get(
                        "delivery_method",
                        {}
                    )
                    .get(
                        "warehouse",
                        "",
                    )
                ),
            })

            continue

        for product in products:

            rows.append({
                "Отправление": posting_number,

                "Статус API": status,

                "Статус": status_ru(
                    status
                ),

                "II / scanit": scanit,

                "Артикул продавца": product.get(
                    "offer_id",
                    "",
                ),

                "SKU": product.get(
                    "sku",
                    "",
                ),

                "Количество": product.get(
                    "quantity",
                    "",
                ),

                "Название": product.get(
                    "name",
                    "",
                ),

                "Дата создания": posting.get(
                    "in_process_at",
                    "",
                ),

                "Дата отгрузки": posting.get(
                    "shipment_date",
                    "",
                ),

                "Склад": (
                    posting
                    .get(
                        "delivery_method",
                        {}
                    )
                    .get(
                        "warehouse",
                        "",
                    )
                ),
            })

    return pd.DataFrame(rows)


# ============================================================
# УНИКАЛЬНЫЕ ОТПРАВЛЕНИЯ
# ============================================================

def unique_posting_numbers(postings):

    result = []

    seen = set()

    for posting in postings:

        number = str(
            posting.get(
                "posting_number",
                "",
            )
        ).strip()

        if not number:
            continue

        if number in seen:
            continue

        seen.add(number)

        result.append(number)

    return result


# ============================================================
# СОЗДАНИЕ ЗАДАНИЯ НА ЭТИКЕТКИ
#
# НОВЫЙ Ozon API:
#
# POST /v3/posting/fbs/package-label/create
#
# {
#   "posting_numbers": [...]
# }
#
# Ответ:
# {
#   "tasks": [
#       {
#           "task_id": ...,
#           "task_type": "small_label"
#       }
#   ]
# }
# ============================================================

def create_label_tasks(posting_numbers):

    tasks = []

    # Новый API допускает до 1000 отправлений
    batch_size = 1000

    batches = [
        posting_numbers[i:i + batch_size]
        for i in range(
            0,
            len(posting_numbers),
            batch_size,
        )
    ]

    progress = st.progress(0)

    for index, batch in enumerate(
        batches,
        start=1,
    ):

        payload = {
            "posting_numbers": batch
        }

        response = api_post(
            LABEL_CREATE_URL,
            payload,
        )

        data = response.json()

        response_tasks = data.get(
            "tasks",
            [],
        )

        # На случай дополнительной result-обёртки
        if not response_tasks:

            result = data.get(
                "result"
            )

            if isinstance(result, dict):

                response_tasks = result.get(
                    "tasks",
                    [],
                )

                # Иногда task может находиться прямо в result
                if not response_tasks:

                    if result.get("task_id"):

                        response_tasks = [
                            result
                        ]

        for task in response_tasks:

            task_id = task.get(
                "task_id"
            )

            task_type = task.get(
                "task_type",
                "",
            )

            if task_id:

                tasks.append({
                    "task_id": int(task_id),
                    "task_type": task_type,
                    "postings": batch,
                })

        progress.progress(
            index / len(batches)
        )

    return tasks


# ============================================================
# ПОЛУЧЕНИЕ ГОТОВОГО ЗАДАНИЯ
#
# POST /v2/posting/fbs/package-label/get
#
# {
#   "task_id": 123
# }
#
# При готовности:
# result.status = completed
# result.file_url = ...
# ============================================================

def get_label_task(
    task_id,
):

    payload = {
        "task_id": int(task_id)
    }

    response = api_post(
        LABEL_GET_URL,
        payload,
    )

    return response.json()


# ============================================================
# СКАЧИВАНИЕ PDF ПО ССЫЛКЕ OZON
# ============================================================

def download_label_pdf(file_url):

    response = requests.get(
        file_url,
        timeout=120,
    )

    if not response.ok:

        raise RuntimeError(
            "Не удалось скачать PDF этикеток: "
            f"HTTP {response.status_code}"
        )

    content_type = (
        response.headers
        .get(
            "Content-Type",
            "",
        )
        .lower()
    )

    if (
        "pdf" not in content_type
        and not response.content.startswith(
            b"%PDF"
        )
    ):

        raise RuntimeError(
            "Ozon вернул не PDF."
        )

    return response.content


# ============================================================
# ОПРОС ЗАДАНИЙ
# ============================================================

def wait_for_label_tasks(
    tasks,
    max_wait=180,
    interval=3,
):

    if not tasks:
        return [], []

    completed_pdfs = []

    errors = []

    started = time.time()

    pending = {
        int(task["task_id"]): task
        for task in tasks
    }

    progress = st.progress(0)

    status_box = st.empty()

    while pending:

        elapsed = time.time() - started

        if elapsed > max_wait:

            for task_id, task in pending.items():

                errors.append({
                    "task_id": task_id,
                    "status": "timeout",
                    "error": (
                        "Истёк лимит ожидания "
                        f"{max_wait} сек."
                    ),
                })

            break

        for task_id in list(
            pending.keys()
        ):

            task = pending[task_id]

            try:

                data = get_label_task(
                    task_id
                )

            except Exception as e:

                status_box.warning(
                    f"Ошибка проверки task {task_id}: {e}"
                )

                continue

            result = data.get(
                "result",
                data,
            )

            status = str(
                result.get(
                    "status",
                    "",
                )
            ).lower()

            file_url = result.get(
                "file_url",
                "",
            )

            error_text = result.get(
                "error",
                "",
            )

            status_box.write(
                f"Задание {task_id}: "
                f"{status or 'ожидание'}"
            )

            if status in (
                "completed",
                "complete",
            ) and file_url:

                try:

                    pdf_bytes = download_label_pdf(
                        file_url
                    )

                    completed_pdfs.append({
                        "task_id": task_id,
                        "task_type": task.get(
                            "task_type",
                            "",
                        ),
                        "postings": task.get(
                            "postings",
                            [],
                        ),
                        "pdf": pdf_bytes,
                        "file_url": file_url,
                        "status": status,
                    })

                    del pending[task_id]

                except Exception as e:

                    errors.append({
                        "task_id": task_id,
                        "status": "download_error",
                        "error": str(e),
                    })

                    del pending[task_id]

            elif status in (
                "error",
                "failed",
            ):

                errors.append({
                    "task_id": task_id,
                    "status": status,
                    "error": error_text,
                })

                del pending[task_id]

        total = len(tasks)

        done = total - len(pending)

        progress.progress(
            min(
                1.0,
                done / total,
            )
        )

        if pending:

            time.sleep(interval)

    progress.progress(1.0)

    return completed_pdfs, errors


# ============================================================
# ОБЪЕДИНЕНИЕ PDF
# ============================================================

def merge_pdfs(pdf_list):

    writer = PdfWriter()

    for pdf_bytes in pdf_list:

        reader = PdfReader(
            io.BytesIO(pdf_bytes)
        )

        for page in reader.pages:

            writer.add_page(page)

    output = io.BytesIO()

    writer.write(output)

    output.seek(0)

    return output.getvalue()


# ============================================================
# ШРИФТ
# ============================================================

def register_fonts():

    candidates = [
        (
            "Roboto",
            "Roboto-Regular.ttf",
        ),
        (
            "RobotoFull",
            "Roboto_Full_Final.ttf",
        ),
        (
            "OzonFont",
            "OzonFont_Fix.ttf",
        ),
    ]

    registered = {}

    for name, path in candidates:

        try:

            pdfmetrics.registerFont(
                TTFont(
                    name,
                    path,
                )
            )

            registered[name] = True

        except Exception:
            registered[name] = False

    if registered.get("Roboto"):
        return "Roboto"

    if registered.get("RobotoFull"):
        return "RobotoFull"

    if registered.get("OzonFont"):
        return "OzonFont"

    return "Helvetica"


# ============================================================
# СОЗДАНИЕ ИНФОРМАЦИОННЫХ ЭТИКЕТОК
#
# 58 x 40 мм
# КОЛ-ВО = 16 pt
# ============================================================

def create_info_labels(
    postings,
):

    font_name = register_fonts()

    output = io.BytesIO()

    page_width = 58 * mm
    page_height = 40 * mm

    c = canvas.Canvas(
        output,
        pagesize=(
            page_width,
            page_height,
        ),
    )

    for posting in postings:

        posting_number = str(
            posting.get(
                "posting_number",
                "",
            )
        )

        status = str(
            posting.get(
                "status",
                "",
            )
        )

        scanit = find_scanit(
            posting
        )

        products = posting.get(
            "products",
            [],
        )

        # ----------------------------------------------------
        # Для каждого товара
        # ----------------------------------------------------

        if not products:
            products = [{}]

        for product in products:

            offer_id = str(
                product.get(
                    "offer_id",
                    "",
                )
            )

            quantity = product.get(
                "quantity",
                "",
            )

            name = str(
                product.get(
                    "name",
                    "",
                )
            )

            # ------------------------------------------------
            # Заголовок
            # ------------------------------------------------

            y = page_height - 5 * mm

            c.setFont(
                font_name,
                7,
            )

            c.drawString(
                3 * mm,
                y,
                "Отправление:",
            )

            y -= 3.5 * mm

            c.setFont(
                font_name,
                8,
            )

            c.drawString(
                3 * mm,
                y,
                posting_number,
            )

            # ------------------------------------------------
            # II
            # ------------------------------------------------

            y -= 4 * mm

            c.setFont(
                font_name,
                7,
            )

            c.drawString(
                3 * mm,
                y,
                "II:",
            )

            c.setFont(
                font_name,
                8,
            )

            c.drawString(
                10 * mm,
                y,
                scanit or "—",
            )

            # ------------------------------------------------
            # КЛЮЧ
            # ------------------------------------------------

            key = ""

            if scanit:

                clean_scanit = re.sub(
                    r"[^A-Za-z0-9]",
                    "",
                    scanit,
                )

                if len(clean_scanit) >= 4:

                    key = clean_scanit[-4:]

            y -= 4 * mm

            c.setFont(
                font_name,
                7,
            )

            c.drawString(
                3 * mm,
                y,
                "Ключ:",
            )

            c.setFont(
                font_name,
                8,
            )

            c.drawString(
                12 * mm,
                y,
                key or "—",
            )

            # ------------------------------------------------
            # АРТИКУЛ
            # ------------------------------------------------

            y -= 4 * mm

            c.setFont(
                font_name,
                7,
            )

            c.drawString(
                3 * mm,
                y,
                "Арт:",
            )

            c.setFont(
                font_name,
                8,
            )

            c.drawString(
                12 * mm,
                y,
                offer_id,
            )

            # ------------------------------------------------
            # КОЛИЧЕСТВО
            #
            # ВАЖНО:
            # 16 pt
            # ------------------------------------------------

            y -= 7 * mm

            c.setFont(
                font_name,
                16,
            )

            c.drawString(
                3 * mm,
                y,
                f"КОЛ-ВО: {quantity}",
            )

            # ------------------------------------------------
            # Название
            # ------------------------------------------------

            y -= 5 * mm

            c.setFont(
                font_name,
                6,
            )

            # Ограничиваем строку
            title = name[:55]

            c.drawString(
                3 * mm,
                y,
                title,
            )

            # ------------------------------------------------
            # Статус маленьким шрифтом
            # ------------------------------------------------

            y -= 3 * mm

            c.setFont(
                font_name,
                5,
            )

            c.drawString(
                3 * mm,
                y,
                status_ru(status),
            )

            c.showPage()

    c.save()

    output.seek(0)

    return output.getvalue()


# ============================================================
# ТАБЛИЦА СТАТУСОВ ЭТИКЕТОК
# ============================================================

def build_label_status_table(
    postings,
    tasks,
    completed_pdfs,
    errors,
):

    task_by_posting = {}

    for task in tasks:

        for posting in task.get(
            "postings",
            [],
        ):

            task_by_posting.setdefault(
                posting,
                []
            ).append(
                task.get(
                    "task_id",
                    "",
                )
            )

    completed_task_ids = {
        item["task_id"]
        for item in completed_pdfs
    }

    error_by_task = {
        item["task_id"]: item
        for item in errors
    }

    rows = []

    for posting in postings:

        number = str(
            posting.get(
                "posting_number",
                "",
            )
        )

        status = str(
            posting.get(
                "status",
                "",
            )
        )

        scanit = find_scanit(
            posting
        )

        task_ids = task_by_posting.get(
            number,
            [],
        )

        task_id_text = ", ".join(
            str(x)
            for x in task_ids
        )

        if any(
            x in completed_task_ids
            for x in task_ids
        ):

            label_status = "PDF получен"

        elif any(
            x in error_by_task
            for x in task_ids
        ):

            label_status = "Ошибка"

        elif task_ids:

            label_status = "Задание создано"

        else:

            label_status = "Не запрашивались"

        rows.append({
            "Отправление": number,
            "Статус API": status,
            "Статус": status_ru(status),
            "II / scanit": scanit,
            "Task ID": task_id_text,
            "Этикетка": label_status,
        })

    return pd.DataFrame(rows)


# ============================================================
# EXCEL
# ============================================================

def dataframe_to_excel(
    df_orders,
    df_labels,
):

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl",
    ) as writer:

        df_orders.to_excel(
            writer,
            index=False,
            sheet_name="FBS",
        )

        df_labels.to_excel(
            writer,
            index=False,
            sheet_name="Этикетки",
        )

    output.seek(0)

    return output.getvalue()


# ============================================================
# ОСНОВНОЙ ЗАПУСК
# ============================================================

if run_button:

    if not client_id.strip():

        st.error(
            "Введите Client-Id."
        )

        st.stop()

    if not api_key.strip():

        st.error(
            "Введите Api-Key."
        )

        st.stop()

    st.divider()

    st.subheader(
        "1️⃣ Получение отправлений из Ozon API"
    )

    try:

        with st.spinner(
            "Загружаю отправления..."
        ):

            postings = get_fbs_postings(
                date_from,
                date_to,
                selected_statuses,
            )

    except Exception as e:

        st.error(
            "Ошибка Ozon API при загрузке отправлений."
        )

        st.code(
            str(e)
        )

        st.stop()

    # --------------------------------------------------------
    # Убираем дубли
    # --------------------------------------------------------

    unique = {}

    for posting in postings:

        number = str(
            posting.get(
                "posting_number",
                "",
            )
        )

        if number:

            unique[number] = posting

    postings = list(
        unique.values()
    )

    st.success(
        f"Получено уникальных отправлений: {len(postings)}"
    )

    # --------------------------------------------------------
    # Разделяем сборку и приёмку
    # --------------------------------------------------------

    assembly_postings = [
        p
        for p in postings
        if p.get("status")
        in ASSEMBLY_STATUSES
    ]

    acceptance_postings = [
        p
        for p in postings
        if p.get("status")
        == "acceptance_in_progress"
    ]

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Всего",
            len(postings),
        )

    with col2:
        st.metric(
            "К сборке",
            len(assembly_postings),
        )

    with col3:
        st.metric(
            "Идёт приёмка",
            len(acceptance_postings),
        )

    # --------------------------------------------------------
    # Основная таблица
    # --------------------------------------------------------

    df_orders = flatten_postings(
        postings
    )

    st.subheader(
        "📋 Отправления из API"
    )

    st.dataframe(
        df_orders,
        use_container_width=True,
        hide_index=True,
    )

    # --------------------------------------------------------
    # Получение этикеток
    # --------------------------------------------------------

    tasks = []

    completed_pdfs = []

    label_errors = []

    if include_labels:

        st.divider()

        st.subheader(
            "2️⃣ Получение этикеток через новый Ozon API"
        )

        # Для этикеток берём все выбранные отправления,
        # включая acceptance_in_progress.
        #
        # Но cancelled/delivered/delivering здесь никогда
        # не попадут, если их не выбрали в фильтре.
        label_postings = postings

        label_numbers = unique_posting_numbers(
            label_postings
        )

        st.write(
            f"Отправлений для запроса этикеток: "
            f"**{len(label_numbers)}**"
        )

        if label_numbers:

            try:

                with st.spinner(
                    "Создаю задания на формирование этикеток..."
                ):

                    tasks = create_label_tasks(
                        label_numbers
                    )

                st.success(
                    f"Создано заданий: {len(tasks)}"
                )

            except Exception as e:

                st.error(
                    "Ошибка создания задания этикеток."
                )

                st.code(
                    str(e)
                )

            # ------------------------------------------------
            # Ожидание
            # ------------------------------------------------

            if tasks:

                st.write(
                    "⏳ Жду формирования PDF..."
                )

                try:

                    completed_pdfs, label_errors = (
                        wait_for_label_tasks(
                            tasks
                        )
                    )

                except Exception as e:

                    st.error(
                        "Ошибка при получении PDF."
                    )

                    st.code(
                        str(e)
                    )

            # ------------------------------------------------
            # Статус этикеток
            # ------------------------------------------------

            df_label_status = (
                build_label_status_table(
                    postings,
                    tasks,
                    completed_pdfs,
                    label_errors,
                )
            )

            st.subheader(
                "🏷 Статус этикеток"
            )

            st.dataframe(
                df_label_status,
                use_container_width=True,
                hide_index=True,
            )

            # ------------------------------------------------
            # Ошибки
            # ------------------------------------------------

            if label_errors:

                st.warning(
                    f"Ошибок по заданиям: "
                    f"{len(label_errors)}"
                )

                st.dataframe(
                    pd.DataFrame(
                        label_errors
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

            # ------------------------------------------------
            # Объединяем PDF
            # ------------------------------------------------

            if completed_pdfs:

                pdf_list = [
                    item["pdf"]
                    for item in completed_pdfs
                ]

                merged_pdf = merge_pdfs(
                    pdf_list
                )

                st.success(
                    f"Получено PDF-заданий: "
                    f"{len(completed_pdfs)}"
                )

                st.download_button(
                    label=(
                        "⬇️ Скачать этикетки Ozon API"
                    ),
                    data=merged_pdf,
                    file_name=(
                        "OZON_FBS_labels.pdf"
                    ),
                    mime="application/pdf",
                    use_container_width=True,
                )

            else:

                st.warning(
                    "Готовый PDF этикеток пока не получен."
                )

    else:

        df_label_status = pd.DataFrame()

    # --------------------------------------------------------
    # Информационные этикетки
    # --------------------------------------------------------

    if create_info_labels:

        st.divider()

        st.subheader(
            "3️⃣ Информационные этикетки"
        )

        # Для физической сборки используем только:
        # awaiting_packaging
        # awaiting_deliver
        #
        # acceptance_in_progress НЕ включаем.
        assembly_for_labels = [
            p
            for p in postings
            if p.get("status")
            in ASSEMBLY_STATUSES
        ]

        if assembly_for_labels:

            info_pdf = create_info_labels(
                assembly_for_labels
            )

            st.success(
                "Информационные этикетки сформированы."
            )

            st.download_button(
                label=(
                    "⬇️ Скачать информационные этикетки"
                ),
                data=info_pdf,
                file_name=(
                    "OZON_FBS_info_labels.pdf"
                ),
                mime="application/pdf",
                use_container_width=True,
            )

        else:

            st.info(
                "Нет отправлений, которые сейчас "
                "нужно собирать."
            )

    # --------------------------------------------------------
    # Excel
    # --------------------------------------------------------

    st.divider()

    st.subheader(
        "4️⃣ Экспорт данных"
    )

    if not df_orders.empty:

        if not df_label_status.empty:

            df_export_labels = (
                df_label_status
            )

        else:

            df_export_labels = pd.DataFrame(
                columns=[
                    "Отправление",
                    "Статус API",
                    "Статус",
                    "II / scanit",
                    "Task ID",
                    "Этикетка",
                ]
            )

        excel_bytes = dataframe_to_excel(
            df_orders,
            df_export_labels,
        )

        st.download_button(
            label="📊 Скачать Excel",
            data=excel_bytes,
            file_name=(
                "OZON_FBS_API.xlsx"
            ),
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            use_container_width=True,
        )

    # --------------------------------------------------------
    # Итог
    # --------------------------------------------------------

    st.divider()

    st.subheader(
        "📌 Итог"
    )

    c1, c2, c3, c4 = st.columns(4)

    with c1:

        st.metric(
            "Отправлений",
            len(postings),
        )

    with c2:

        st.metric(
            "К сборке",
            len(assembly_postings),
        )

    with c3:

        st.metric(
            "На приёмке",
            len(acceptance_postings),
        )

    with c4:

        st.metric(
            "PDF получено",
            len(completed_pdfs),
        )

    st.info(
        "Важно: отсутствие отправления в листе подбора "
        "не означает, что отправление ошибочное. "
        "Источник истины здесь — Ozon API. "
        "Статус «Идёт приёмка» означает, что отправление "
        "уже находится на этапе приёмки и не относится "
        "к текущей физической сборке."
    )

