import csv
import io
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from itertools import zip_longest
from sqlite3 import Error as SQLiteError
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import life_ledger_service as life_service

from . import auth


router = APIRouter()
TAIPEI = timezone(timedelta(hours=8))
QUICK_ENTRY_ERROR = "資料無法儲存，請檢查後再試。"
CALENDAR_ERROR = "無法顯示月曆，請返回後重試。"
SEARCH_ERROR = "無法搜尋帳目，請檢查條件後重試。"
SETTINGS_ERROR = "無法更新設定，請檢查後重試。"
HOME_CARDS_ERROR = "無法顯示本月資訊，請稍後重試。"
SETTINGS_SECTIONS = {"budget", "categories", "payments"}
EXPENSE_FIELDS = ("spent_on", "amount", "note", "category", "payment_source_id")
EXPENSE_RETURN_FIELDS = ("return_view", "keyword", "start", "end", "month", "day")
EXPENSE_ERROR = "資料無法儲存，請確認日期、金額、消費項目、分類及付款方式。"
EXPENSE_CONFLICT = "此筆帳目已變動，請重新載入後再操作。"


def taiwan_today() -> date:
    return datetime.now(TAIPEI).date()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    user_id = auth.current_user_id(request.session)
    if user_id:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="home.html",
            context=_quick_entry_context(
                user_id,
                request.session["csrf_token"],
                saved=request.query_params.get("saved") == "1",
            ),
        )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
    )


@router.get("/login")
async def login(request: Request):
    try:
        return await auth.begin_discord_login(request)
    except auth.AuthFailure as exc:
        auth.clear_session(request.session)
        return _error_response(request, str(exc), 503)


@router.get("/auth/discord/callback")
async def discord_callback(request: Request):
    try:
        user_id = await auth.fetch_discord_user_id(request)
        auth.start_session(request.session, user_id)
    except auth.AuthFailure as exc:
        auth.clear_session(request.session)
        return _error_response(request, str(exc), 400)
    return RedirectResponse(url="/", status_code=303)


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request) -> HTMLResponse:
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _error_response(request, "請先使用 Discord 登入。", 403)

    try:
        month_start = _calendar_month(request.query_params.get("month"))
        selected_day = _calendar_day(
            request.query_params.get("day"),
            month_start,
        )
        month_text = month_start.strftime("%Y-%m")
        calendar_rows = life_service.get_calendar_days(user_id, month_text)
        grid_context = _calendar_grid_context(
            month_start, calendar_rows, as_of=taiwan_today(),
        )
        month_expenses = life_service.list_expenses(user_id, month_text)["items"]
        selected_expenses = []
        daily_total_cents = 0
        if selected_day is not None:
            selected_items = [
                item
                for item in month_expenses
                if item["spent_on"] == selected_day.isoformat()
            ]
            daily_total_cents = sum(item["cents"] for item in selected_items)
            selected_expenses = [
                {
                    "amount": _format_twd(item["cents"]),
                    "note": item["note"],
                    "category": item["category"],
                    "payment_source_name": item["payment_source_name"],
                    "edit_url": f"/expenses/{item['id']}/edit?" + urlencode({
                        "return_view": "calendar", "month": month_text, "day": selected_day.isoformat(),
                    }),
                }
                for item in selected_items
            ]
    except (TypeError, ValueError):
        return _error_response(request, CALENDAR_ERROR, 400)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="calendar.html",
        context={
            **grid_context,
            "month_start": month_start,
            "previous_month": (
                month_start - timedelta(days=1)
            ).strftime("%Y-%m"),
            "next_month": _next_month(month_start).strftime("%Y-%m"),
            "can_go_next": (
                _next_month(month_start) <= taiwan_today().replace(day=1)
            ),
            "current_month": taiwan_today().strftime("%Y-%m"),
            "selected_day": selected_day,
            "selected_day_text": (
                selected_day.isoformat() if selected_day is not None else None
            ),
            "selected_expenses": selected_expenses,
            "daily_total": _format_twd(daily_total_cents),
            "deleted": request.query_params.get("deleted") == "1",
        },
    )


@router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request) -> HTMLResponse:
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _error_response(request, "請先使用 Discord 登入。", 403)

    field_names = ("keyword", "start", "end")
    values = {name: request.query_params.get(name, "") for name in field_names}
    submitted = any(name in request.query_params for name in field_names)
    if not submitted:
        return _search_response(request, values)

    try:
        keyword = values["keyword"].strip()
        start_day = _search_date(values["start"])
        end_day = _search_date(values["end"])
        if not keyword and start_day is None:
            raise ValueError
        if start_day is not None and end_day is not None and start_day > end_day:
            raise ValueError
        start = start_day.isoformat() if start_day is not None else None
        if end_day is not None:
            end = end_day.isoformat()
        elif start_day is not None:
            end = taiwan_today().isoformat()
        else:
            end = None
        items = life_service.list_expenses_in_range(user_id, start, end, keyword=keyword)['items']
        return_context = _expense_return_context({"return_view": ["search"],
            **{name: request.query_params.getlist(name) for name in field_names}})
        results = [
            {
                "spent_on": item["spent_on"],
                "amount": _format_twd(item["cents"]),
                "note": item["note"],
                "category": item["category"],
                "payment_source_name": item["payment_source_name"],
                "edit_url": f"/expenses/{item['id']}/edit?" + urlencode(return_context),
            }
            for item in items
        ]
    except (TypeError, ValueError):
        return _search_response(
            request,
            values,
            error=SEARCH_ERROR,
            status_code=400,
        )

    return _search_response(request, values, results=results, searched=True)


COMPARISON_ERROR = '無法比較，日期請使用 YYYY-MM-DD 且須存在，各期間開始不可晚於結束；請確認分類與關鍵字（最多 200 字）。'
COMPARISON_FIELDS = ('a_start', 'a_end', 'b_start', 'b_end', 'category', 'keyword')


@router.get('/compare', response_class=HTMLResponse)
async def comparison_page(request: Request):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _expense_response(request, 'error.html', {'message': '請先使用 Discord 登入。'}, 403)
    as_of = life_service.get_today()
    first = as_of.replace(day=1)
    prior_end = first-timedelta(days=1)
    defaults = dict(a_start=first.isoformat(), a_end=(_next_month(first)-timedelta(days=1)).isoformat(),
                    b_start=prior_end.replace(day=1).isoformat(), b_end=prior_end.isoformat(), category='', keyword='')
    values = {name: request.query_params.get(name, defaults[name]) for name in COMPARISON_FIELDS}
    comparison, chart_data, options, error, status = None, None, [], None, 200
    try:
        if any(name in request.query_params and len(request.query_params.getlist(name)) != 1 for name in COMPARISON_FIELDS):
            raise ValueError()
        days = [_search_date(values[name], allow_future=True) for name in COMPARISON_FIELDS[:4]]
        if any(day is None for day in days) or days[0]>days[1] or days[2]>days[3] or len(values['keyword'])>200:
            raise ValueError()
        result = life_service.get_expense_comparison(
            user_id, *(values[name] for name in COMPARISON_FIELDS[:4]),
            category=values['category'], keyword=values['keyword'], as_of=as_of,
        )
        options = result['category_options']
        comparison = _comparison_display(result)
        chart_data = _comparison_chart_data(result, values)
    except (TypeError, ValueError):
        error, status = COMPARISON_ERROR, 400
    except SQLiteError:
        error, status = '比較暫時無法完成，請稍後重試。', 503
    if error:
        try:
            options = life_service.get_expense_comparison_categories(user_id, as_of=as_of)
        except (TypeError, ValueError, SQLiteError):
            error, status = '比較暫時無法完成，請稍後重試。', 503
    return _expense_response(request, 'comparison.html', {
        'values': values, 'comparison': comparison, 'chart_data': chart_data, 'category_options': options,
        'invalid_category': bool(values['category']) and values['category'] not in {item['name'] for item in options},
        'error': error,
    }, status)


def _comparison_difference(cents):
    if cents is None:
        return None
    return ('+' if cents>0 else '−' if cents<0 else '') + _format_twd(abs(cents)) + ' 元'


def _comparison_display(result):
    display = dict(result)
    for name in ('a', 'b'):
        period = result[name]
        display[name] = {**period, 'amount': _format_twd(period['total_cents']) if period['started'] else None,
                         'daily': [{**point, 'amount': _format_twd(point['cents'])} for point in period['daily']]}
    display['daily_rows'] = [dict(day=i+1, a=a, b=b,
                                 nonzero=bool((a and a['cents']) or (b and b['cents'])))
                             for i, (a, b) in enumerate(zip_longest(display['a']['daily'], display['b']['daily']))]
    display['scale_cents'] = max(result['a']['total_cents'] or 0, result['b']['total_cents'] or 0) or 1
    display['difference_text'] = _comparison_difference(result['difference_cents'])
    percent = result['change_percent']
    display['percent_text'] = _format_amount(abs(percent).quantize(Decimal('.01'), rounding=ROUND_HALF_UP), grouping=False) if percent is not None else None
    display['percent_direction'] = '增加' if percent is not None and percent>0 else '減少' if percent is not None and percent<0 else '相同'
    display['categories'] = [{**row,
        'a_amount': _format_twd(row['a_cents']) if row['a_cents'] is not None else None,
        'b_amount': _format_twd(row['b_cents']) if row['b_cents'] is not None else None,
        'difference_text': _comparison_difference(row['difference_cents']),
    } for row in result['categories']]
    return display


def _comparison_chart_data(result, values):
    # Exact strings survive JSON/JavaScript integer limits; only plot coordinates become Numbers.
    def amount(cents):
        return _format_twd(cents) if cents is not None else None

    def share(percent):
        if percent is None:
            return None
        rounded = percent.quantize(Decimal('.1'), rounding=ROUND_HALF_UP)
        return '<0.1' if percent>0 and rounded==0 else _format_amount(rounded, grouping=False)

    periods = []
    for key in ('a', 'b'):
        period = result[key]
        periods.append({
            'label': '期間 '+key.upper(), 'started': period['started'],
            'start': period['start'], 'end': period['end'],
            'actual_start': period['actual_start'], 'actual_end': period['actual_end'],
            'daily': [{'day': p['day'], 'date': p['date'], 'cents': str(p['cents']), 'amount': amount(p['cents'])}
                      for p in period['daily']],
            'share_order': sorted((i for i, row in enumerate(result['categories']) if row[key+'_cents']),
                                  key=lambda i: (-result['categories'][i][key+'_cents'], result['categories'][i]['category'])),
        })
    categories = [{
        'name': row['category'],
        **{key+'_cents': str(row[key+'_cents']) if row[key+'_cents'] is not None else None for key in ('a', 'b')},
        **{key+'_amount': amount(row[key+'_cents']) for key in ('a', 'b')},
        **{key+'_percent': share(row[key+'_percent']) for key in ('a', 'b')},
    } for row in result['categories']]
    return dict(periods=periods, categories=categories, single_category=bool(values['category']),
                keyword_filtered=bool(values['keyword'].strip()))


@router.get('/export', response_class=HTMLResponse)
async def export_page(request: Request):
    if not auth.current_user_id(request.session):
        response = _error_response(request, '請先使用 Discord 登入。', 403)
        response.headers['Cache-Control'] = 'no-store'
        return response
    today = life_service.get_today()
    return _export_response(request, {'start': today.replace(day=1).isoformat(), 'end': today.isoformat()})


@router.post('/export/csv')
async def export_csv(request: Request):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        response = _error_response(request, '請先使用 Discord 登入。', 403)
        response.headers['Cache-Control'] = 'no-store'
        return response
    form = await _urlencoded_form(request)
    tokens = form.get('csrf_token', [])
    if len(tokens) != 1 or not tokens[0].isascii() or not auth.validate_csrf(request.session, tokens[0]):
        response = _error_response(request, '操作驗證失敗，請重新載入頁面後再試。', 403)
        response.headers['Cache-Control'] = 'no-store'
        return response
    values = {name: form[name][0] if len(form.get(name, [])) == 1 else '' for name in ('start', 'end')}
    try:
        start, end = _search_date(values['start']), _search_date(values['end'])
        if start is None or end is None or start > end:
            raise ValueError
        items = life_service.list_expenses_in_range(user_id, start.isoformat(), end.isoformat())['items']
        if not items:
            return _export_response(request, values, empty=True)
        payload = _expense_csv(items)
    except SQLiteError:
        return _export_response(request, values, error='目前無法匯出，請稍後重試。', status_code=503)
    except (TypeError, ValueError):
        return _export_response(request, values, error='無法匯出帳目，請檢查日期後重試。', status_code=400)
    return Response(payload, media_type='text/csv', headers={
        'Content-Disposition': 'attachment; filename="discordbot-expenses.csv"', 'Cache-Control': 'no-store',
    })


def _export_response(request: Request, values: dict[str, str], *, error: str | None = None,
                     empty: bool = False, status_code: int = 200) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request=request, name='export.html', status_code=status_code, headers={'Cache-Control': 'no-store'},
        context={'values': values, 'csrf_token': request.session['csrf_token'], 'error': error, 'empty': empty},
    )


@router.get("/expenses/{expense_id}/edit", response_class=HTMLResponse)
async def expense_edit_page(request: Request, expense_id: str):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _expense_response(request, "error.html", {"message": "請先使用 Discord 登入。"}, 403)
    try:
        expense = life_service.get_expense(user_id, _expense_integer(expense_id, minimum=1, entry_id=True))
        return_context = _expense_return_context({name: request.query_params.getlist(name) for name in EXPENSE_RETURN_FIELDS})
        context = _expense_edit_context(user_id, expense, request.session["csrf_token"], return_context)
        context["saved"] = request.query_params.get("saved") == "1"
        return _expense_response(request, "expense_edit.html", context)
    except (TypeError, ValueError, SQLiteError) as error:
        return _expense_failure(request, error)


@router.post("/expenses/{expense_id}/edit")
async def expense_update(request: Request, expense_id: str):
    return await _expense_post(request, expense_id)


@router.get("/expenses/{expense_id}/delete", response_class=HTMLResponse)
async def expense_delete_page(request: Request, expense_id: str):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _expense_response(request, "error.html", {"message": "請先使用 Discord 登入。"}, 403)
    try:
        expense = life_service.get_expense(user_id, _expense_integer(expense_id, minimum=1, entry_id=True))
        return_context = _expense_return_context({name: request.query_params.getlist(name) for name in EXPENSE_RETURN_FIELDS})
        revisions = request.query_params.getlist("expected_revision")
        if len(revisions) != 1:
            raise ValueError
        revision = _expense_integer(revisions[0])
        if expense["revision"] != revision:
            raise life_service.ExpenseRevisionConflictError()
        return _expense_response(request, "expense_delete.html", {
            "expense": {name: expense[name] for name in ("id", "spent_on", "note", "category", "payment_source_name")},
            "amount": _format_twd(expense["cents"]), "auto_date": expense["source"] != "manual",
            "csrf_token": request.session["csrf_token"], "revision": revision,
            "return_context": return_context,
            "edit_url": f"/expenses/{expense['id']}/edit?" + urlencode(return_context),
        })
    except (TypeError, ValueError, SQLiteError) as error:
        reload_url = (f"/expenses/{expense['id']}/edit?" + urlencode(return_context)
                      if isinstance(error, life_service.ExpenseRevisionConflictError) else None)
        return _expense_failure(request, error, reload_url=reload_url)


@router.post("/expenses/{expense_id}/delete")
async def expense_delete(request: Request, expense_id: str):
    return await _expense_post(request, expense_id, delete=True)


async def _expense_post(request: Request, expense_id: str, *, delete: bool = False):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _expense_response(request, "error.html", {"message": "請先使用 Discord 登入。"}, 403)
    form = await _urlencoded_form(request)
    tokens = form.get("csrf_token", [])
    if len(tokens) != 1 or not tokens[0].isascii() or not auth.validate_csrf(request.session, tokens[0]):
        return _expense_response(request, "error.html", {"message": "操作驗證失敗，請重新載入頁面後再試。"}, 403)
    try:
        key = _expense_integer(expense_id, minimum=1, entry_id=True)
        expense = life_service.get_expense(user_id, key)
    except (TypeError, ValueError, SQLiteError) as error:
        return _expense_failure(request, error)
    return_context = _expense_return_context(form)
    draft = _expense_draft(form)
    revision = None
    try:
        if len(form.get("expected_revision", [])) != 1:
            raise ValueError
        revision = _expense_integer(form["expected_revision"][0])
        if expense["revision"] != revision:
            raise life_service.ExpenseRevisionConflictError()
        if delete:
            life_service.void_expense(user_id, key, revision)
        else:
            if any(len(form.get(name, [])) != 1 for name in EXPENSE_FIELDS):
                raise ValueError
            values = {name: form[name][0] for name in EXPENSE_FIELDS}
            if (len(values["amount"]) > 30 or len(values["note"]) > 200 or
                    not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", values["spent_on"])):
                raise ValueError
            payment = values["payment_source_id"]
            payment_id = None if payment == "keep" else _expense_integer(payment, minimum=1)
            life_service.update_expense(user_id, key, values["amount"], values["category"],
                                        values["note"], values["spent_on"], payment_id, revision)
    except SQLiteError as error:
        return _expense_failure(request, error)
    except life_service.ExpenseUnavailableError as error:
        return _expense_failure(request, error)
    except (TypeError, ValueError) as error:
        if delete:
            return _expense_failure(request, error, reload_url=f"/expenses/{key}/edit?" + urlencode(return_context))
        # A failed draft keeps its submitted revision, even if a writer changed the row.
        try:
            current = life_service.get_expense(user_id, key)
            conflict = revision is not None and current["revision"] != revision
            context = _expense_edit_context(user_id, current, request.session["csrf_token"],
                                            return_context, draft=draft,
                                            error=EXPENSE_CONFLICT if conflict else EXPENSE_ERROR)
            context["can_submit"] = revision is not None and not conflict
            context["revision"] = revision
            return _expense_response(request, "expense_edit.html", context, 409 if conflict else 400)
        except (TypeError, ValueError, SQLiteError) as error:
            return _expense_failure(request, error)
    location = (_expense_return_url(return_context, deleted=True) if delete else
                f"/expenses/{key}/edit?" + urlencode({**return_context, "saved": "1"}))
    return RedirectResponse(url=location,
                            status_code=303, headers={"Cache-Control": "no-store"})


def _expense_integer(value: str, *, minimum: int = 0, entry_id: bool = False) -> int:
    if not re.fullmatch(r"[0-9]{1,19}", value) or not minimum <= int(value) <= 2**63 - 1:
        if entry_id:
            raise life_service.ExpenseUnavailableError()
        raise ValueError
    return int(value)


def _expense_return_context(values: dict[str, list[str]]) -> dict[str, str]:
    fallback = {"return_view": "search"}
    if any(len(values.get(name, [])) > 1 for name in EXPENSE_RETURN_FIELDS):
        return fallback
    single = {name: (values.get(name) or [""])[0] for name in EXPENSE_RETURN_FIELDS}
    try:
        if single["return_view"] == "calendar":
            month = _calendar_month(single["month"] or None)
            day = _calendar_day(single["day"] or None, month)
            return {"return_view": "calendar", "month": month.strftime("%Y-%m"),
                    **({"day": day.isoformat()} if day is not None else {})}
        if single["return_view"] != "search":
            return fallback
        keyword = single["keyword"].strip()
        start, end = _search_date(single["start"]), _search_date(single["end"])
        if (end is not None and not keyword and start is None) or (start is not None and end is not None and start > end):
            raise ValueError
        return {**fallback, **{name: value for name, value in
                              (("keyword", keyword), ("start", single["start"]), ("end", single["end"])) if value}}
    except (TypeError, ValueError):
        return fallback


def _expense_return_url(context: dict[str, str], *, deleted: bool = False) -> str:
    calendar = context.get("return_view") == "calendar"
    fields = ("month", "day") if calendar else ("keyword", "start", "end")
    query = {name: context[name] for name in fields if name in context}
    if deleted:
        query["deleted"] = "1"
    path = "/calendar" if calendar else "/search"
    return path + ("?" + urlencode(query) if query else "")


def _expense_draft(form: dict[str, list[str]]) -> dict[str, str]:
    limits = {"spent_on": 10, "amount": 30, "note": 200, "category": 20, "payment_source_id": 19}
    return {name: form[name][0] if len(form.get(name, [])) == 1 and len(form[name][0]) <= limits[name] else ""
            for name in EXPENSE_FIELDS}


def _expense_edit_context(user_id: str, expense: dict, csrf_token: str,
                          return_context: dict[str, str], *, draft: dict[str, str] | None = None,
                          error: str | None = None) -> dict:
    categories = life_service.get_categories(user_id)
    sources = life_service.get_payment_sources(user_id, initialize_defaults=False)
    values = draft if draft is not None else {
        "spent_on": expense["spent_on"], "amount": _format_amount(Decimal(expense["cents"]) / 100, grouping=False),
        "note": expense["note"], "category": expense["category"], "payment_source_id": "keep",
    }
    source_names = {str(source["id"]): source["name"] for source in sources}
    allowed_categories = set(categories) | {expense["category"]}
    query = urlencode(return_context)
    return {
        "expense": {name: expense[name] for name in ("id", "spent_on", "category", "payment_source_name")},
        "values": values, "categories": categories, "payment_sources": sources,
        "original_category_inactive": expense["category"] not in categories,
        "invalid_category": values["category"] not in allowed_categories,
        "invalid_payment_source": values["payment_source_id"] not in {"keep", *source_names},
        "reference_category": values["category"] if values["category"] in allowed_categories else "原選擇無法使用",
        "reference_payment": expense["payment_source_name"] if values["payment_source_id"] == "keep" else source_names.get(values["payment_source_id"], "原選擇無法使用"),
        "csrf_token": csrf_token, "revision": expense["revision"], "return_context": return_context,
        "return_url": _expense_return_url(return_context), "edit_url": f"/expenses/{expense['id']}/edit?{query}",
        "delete_url": f"/expenses/{expense['id']}/delete?" + urlencode({**return_context, "expected_revision": expense["revision"]}),
        "auto_date": expense["source"] != "manual", "can_submit": True, "error": error, "saved": False,
    }


def _expense_response(request: Request, template: str, context: dict, status_code: int = 200):
    if template == "error.html":
        context = {"error_heading": "無法完成操作", **context}
    return request.app.state.templates.TemplateResponse(request=request, name=template, context=context,
                                                        status_code=status_code, headers={"Cache-Control": "no-store"})


def _expense_failure(request: Request, error: Exception, *, reload_url: str | None = None):
    if isinstance(error, life_service.ExpenseUnavailableError):
        message, status = "無法開啟此筆帳目，請返回列表重新選取。", 404
    elif isinstance(error, life_service.ExpenseRevisionConflictError):
        message, status = EXPENSE_CONFLICT, 409
    elif isinstance(error, SQLiteError):
        message, status = "操作暫時無法完成，請稍後重新載入再試。", 503
    else:
        message, status = EXPENSE_ERROR, 400
    return _expense_response(request, "error.html", {
        "message": message, "reload_url": reload_url if status == 409 else None,
    }, status)


FIXED_FIELDS = ('name', 'amount', 'category', 'due_day')
FIXED_ERROR = '無法儲存固定支出，請確認消費項目、金額、分類、月份及扣款日。'
FIXED_CONFLICT = '此固定支出已變動，請重新載入後再操作。'


def _fixed_key(value):
    try:
        return _expense_integer(value, minimum=1)
    except ValueError:
        raise life_service.RecurringUnavailableError() from None


def _fixed_failure(request, error):
    if isinstance(error, life_service.RecurringUnavailableError):
        message, status = '無法開啟此固定支出，請返回管理頁重新選取。', 404
    elif isinstance(error, life_service.RecurringRevisionConflictError):
        message, status = FIXED_CONFLICT, 409
    elif isinstance(error, SQLiteError):
        message, status = '操作暫時無法完成，請稍後重試。', 503
    else:
        message, status = FIXED_ERROR, 400
    return _expense_response(request, 'error.html', {'message': message}, status)


def _fixed_display(rule):
    result = {**rule, 'amount': _format_amount(Decimal(rule['cents']) / 100)}
    if rule['pending']:
        pending = rule['pending']
        result['pending'] = {**pending, 'amount': _format_amount(Decimal(pending['cents']) / 100),
                             'month_title': f"{pending['effective_month'][:4]} 年 {pending['effective_month'][5:]} 月"}
    return result


@router.get('/fixed-expenses', response_class=HTMLResponse)
async def fixed_list(request: Request):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _expense_response(request, 'error.html', {'message': '請先使用 Discord 登入。'}, 403)
    try:
        rules = [_fixed_display(rule) for rule in life_service.get_fixed_recurring_rules(user_id)]
    except (TypeError, ValueError, SQLiteError) as error:
        return _fixed_failure(request, error)
    return _expense_response(request, 'fixed_list.html', {
        'rules': rules, 'csrf_token': request.session['csrf_token'],
        'saved': request.query_params.get('saved') == '1', 'stopped': request.query_params.get('stopped') == '1',
        'synced': request.query_params.get('synced') == '1',
    })


@router.get('/fixed-expenses/new', response_class=HTMLResponse)
async def fixed_new_page(request: Request):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _expense_response(request, 'error.html', {'message': '請先使用 Discord 登入。'}, 403)
    try:
        return _fixed_form_response(request, user_id)
    except (TypeError, ValueError, SQLiteError) as error:
        return _fixed_failure(request, error)


@router.post('/fixed-expenses/new')
async def fixed_add(request: Request):
    return await _fixed_post(request)


@router.get('/fixed-expenses/{rule_id}/edit', response_class=HTMLResponse)
async def fixed_edit_page(request: Request, rule_id: str):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _expense_response(request, 'error.html', {'message': '請先使用 Discord 登入。'}, 403)
    try:
        rule = life_service.get_fixed_recurring(user_id, _fixed_key(rule_id))
        if not rule['active']:
            raise life_service.RecurringUnavailableError()
        return _fixed_form_response(request, user_id, rule)
    except (TypeError, ValueError, SQLiteError) as error:
        return _fixed_failure(request, error)


@router.post('/fixed-expenses/{rule_id}/edit')
async def fixed_update(request: Request, rule_id: str):
    return await _fixed_post(request, rule_id)


@router.get('/fixed-expenses/{rule_id}/stop', response_class=HTMLResponse)
async def fixed_stop_page(request: Request, rule_id: str):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _expense_response(request, 'error.html', {'message': '請先使用 Discord 登入。'}, 403)
    try:
        rule = life_service.get_fixed_recurring(user_id, _fixed_key(rule_id))
        if not rule['active']:
            raise life_service.RecurringUnavailableError()
        revisions = request.query_params.getlist('expected_revision')
        if len(revisions) != 1:
            raise ValueError()
        if _expense_integer(revisions[0]) != rule['revision']:
            raise life_service.RecurringRevisionConflictError()
        return _expense_response(request, 'fixed_stop.html', {
            'rule': _fixed_display(rule), 'csrf_token': request.session['csrf_token'],
        })
    except (TypeError, ValueError, SQLiteError) as error:
        return _fixed_failure(request, error)


@router.post('/fixed-expenses/{rule_id}/stop')
async def fixed_stop(request: Request, rule_id: str):
    return await _fixed_post(request, rule_id, stop=True)


@router.post('/fixed-expenses/sync')
async def fixed_sync(request: Request):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _expense_response(request, 'error.html', {'message': '請先使用 Discord 登入。'}, 403)
    form = await _urlencoded_form(request)
    tokens = form.get('csrf_token', [])
    if len(tokens) != 1 or not tokens[0].isascii() or not auth.validate_csrf(request.session, tokens[0]):
        return _expense_response(request, 'error.html', {'message': '操作驗證失敗，請重新載入後再試。'}, 403)
    try:
        life_service.sync_fixed_recurring(user_id)
    except (TypeError, ValueError, SQLiteError) as error:
        return _fixed_failure(request, error)
    return RedirectResponse('/fixed-expenses?synced=1', status_code=303, headers={'Cache-Control': 'no-store'})


async def _fixed_post(request, rule_id=None, *, stop=False):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _expense_response(request, 'error.html', {'message': '請先使用 Discord 登入。'}, 403)
    form = await _urlencoded_form(request)
    tokens = form.get('csrf_token', [])
    if len(tokens) != 1 or not tokens[0].isascii() or not auth.validate_csrf(request.session, tokens[0]):
        return _expense_response(request, 'error.html', {'message': '操作驗證失敗，請重新載入後再試。'}, 403)
    limits = {'name': 100, 'amount': 30, 'category': 20, 'due_day': 2, 'start_month': 7}
    draft = {name: form[name][0] if len(form.get(name, [])) == 1 and len(form[name][0]) <= limit else ''
             for name, limit in limits.items()}
    key, revision = None, None
    try:
        if rule_id is not None:
            key = _fixed_key(rule_id)
            if len(form.get('expected_revision', [])) != 1:
                raise ValueError()
            revision = _expense_integer(form['expected_revision'][0])
        if stop:
            life_service.stop_fixed_recurring(user_id, key, revision)
        else:
            fields = FIXED_FIELDS + (() if key is not None else ('start_month',))
            if any(len(form.get(name, [])) != 1 or not draft[name] for name in fields):
                raise ValueError()
            due_day = _expense_integer(draft['due_day'], minimum=1)
            if key is None:
                life_service.add_fixed_recurring(user_id, draft['name'], draft['amount'], draft['category'], draft['start_month'], due_day)
            else:
                life_service.update_fixed_recurring(user_id, key, draft['name'], draft['amount'], draft['category'], due_day, revision)
    except (TypeError, ValueError, SQLiteError) as error:
        if stop or isinstance(error, (life_service.RecurringUnavailableError, SQLiteError)):
            return _fixed_failure(request, error)
        try:
            rule = life_service.get_fixed_recurring(user_id, key) if key is not None else None
            conflict = rule is not None and revision is not None and rule['revision'] != revision
            return _fixed_form_response(request, user_id, rule, draft=draft, revision=revision,
                                        error=FIXED_CONFLICT if conflict else FIXED_ERROR,
                                        status=409 if conflict else 400)
        except (TypeError, ValueError, SQLiteError) as failure:
            return _fixed_failure(request, failure)
    location = '/fixed-expenses?stopped=1' if stop else (f'/fixed-expenses/{key}/edit?saved=1' if key is not None else '/fixed-expenses?saved=1')
    return RedirectResponse(location, status_code=303, headers={'Cache-Control': 'no-store'})


def _fixed_form_response(request, user_id, rule=None, *, draft=None, revision=None, error=None, status=200):
    months = life_service.get_recurring_start_months()
    categories = life_service.get_categories(user_id)
    target = (rule['pending'] or rule) if rule else None
    values = draft if draft is not None else {
        'name': target['name'] if target else '',
        'amount': _format_amount(Decimal(target['cents']) / 100, grouping=False) if target else '',
        'category': target['category'] if target else (categories[0] if categories else ''),
        'due_day': str(target['due_day']) if target else '1',
        'start_month': rule['start_month'] if rule else months[0],
    }
    current_revision = rule['revision'] if rule else None
    submitted_revision = revision if error else current_revision
    retained = target['category'] if target and target['category'] not in categories else None
    return _expense_response(request, 'fixed_form.html', {
        'rule': rule, 'values': values, 'categories': categories, 'months': months,
        'retained_category': retained, 'invalid_category': values['category'] not in {*categories, retained},
        'csrf_token': request.session['csrf_token'], 'revision': submitted_revision,
        'can_submit': rule is None or (rule['active'] and submitted_revision == current_revision),
        'effective_title': f'{months[1][:4]} 年 {months[1][5:]} 月',
        'error': error, 'saved': request.query_params.get('saved') == '1',
    }, status)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _error_response(request, "請先使用 Discord 登入。", 403)

    open_section = request.query_params.get("section", "budget")
    if open_section not in SETTINGS_SECTIONS:
        open_section = "budget"
    try:
        context = _settings_context(
            user_id,
            request.session["csrf_token"],
            saved=request.query_params.get("saved") == "1",
            open_section=open_section,
        )
    except (TypeError, ValueError):
        return _error_response(request, SETTINGS_ERROR, 400)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=context,
    )


@router.post("/settings/budgets/set")
async def settings_set_budget(request: Request):
    return await _settings_post(
        request,
        ("category", "amount"),
        lambda user_id, values: life_service.set_budget(
            user_id,
            life_service.get_today().strftime("%Y-%m"),
            values["category"],
            values["amount"],
        ),
        section="budget",
    )


@router.post("/settings/budgets/clear")
async def settings_clear_budget(request: Request):
    return await _settings_post(
        request,
        ("category",),
        lambda user_id, values: life_service.clear_budget(
            user_id,
            life_service.get_today().strftime("%Y-%m"),
            values["category"],
        ),
        section="budget",
    )


@router.post("/settings/budgets/use-category-sum")
async def settings_use_category_sum(request: Request):
    return await _settings_post(
        request,
        (),
        lambda user_id, values: life_service.set_total_budget_to_category_sum(
            user_id,
            life_service.get_today().strftime("%Y-%m"),
        ),
        section="budget",
    )


@router.post("/settings/categories/add")
async def settings_add_category(request: Request):
    return await _settings_post(
        request,
        ("name",),
        lambda user_id, values: life_service.add_category(
            user_id,
            values["name"],
        ),
        section="categories",
    )


@router.post("/settings/categories/rename")
async def settings_rename_category(request: Request):
    return await _settings_post(
        request,
        ("old_name", "new_name"),
        lambda user_id, values: life_service.rename_category(
            user_id,
            values["old_name"],
            values["new_name"],
        ),
        section="categories",
    )


@router.post("/settings/categories/disable")
async def settings_disable_category(request: Request):
    return await _settings_post(
        request,
        ("name",),
        lambda user_id, values: life_service.disable_category(
            user_id,
            values["name"],
        ),
        section="categories",
    )


@router.post("/settings/payment-sources/add")
async def settings_add_payment_source(request: Request):
    return await _settings_post(
        request,
        ("name",),
        lambda user_id, values: life_service.add_payment_source(
            user_id,
            values["name"],
        ),
        section="payments",
    )


@router.post("/settings/payment-sources/rename")
async def settings_rename_payment_source(request: Request):
    return await _settings_post(
        request,
        ("payment_source_id", "name"),
        lambda user_id, values: life_service.rename_payment_source(
            user_id,
            int(values["payment_source_id"]),
            values["name"],
        ),
        section="payments",
    )


@router.post("/settings/payment-sources/disable")
async def settings_disable_payment_source(request: Request):
    return await _settings_post(
        request,
        ("payment_source_id",),
        lambda user_id, values: life_service.disable_payment_source(
            user_id,
            int(values["payment_source_id"]),
        ),
        section="payments",
    )


@router.post("/logout")
async def logout(request: Request):
    tokens = []
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type == "application/x-www-form-urlencoded":
        try:
            form = parse_qs(
                (await request.body()).decode("utf-8"),
                keep_blank_values=True,
                max_num_fields=10,
            )
            tokens = form.get("csrf_token", [])
        except (UnicodeDecodeError, ValueError):
            tokens = []

    if len(tokens) != 1 or not auth.validate_csrf(request.session, tokens[0]):
        return _error_response(request, "登出驗證失敗，請返回首頁重試。", 403)

    auth.clear_session(request.session)
    return RedirectResponse(url="/", status_code=303)


@router.post("/expenses")
async def add_expense(request: Request):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _error_response(request, "請先使用 Discord 登入。", 403)

    form = {}
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type == "application/x-www-form-urlencoded":
        try:
            form = parse_qs(
                (await request.body()).decode("utf-8"),
                keep_blank_values=True,
                max_num_fields=12,
            )
        except (UnicodeDecodeError, ValueError):
            form = {}

    tokens = form.get("csrf_token", [])
    if len(tokens) != 1 or not auth.validate_csrf(request.session, tokens[0]):
        return _error_response(request, "儲存驗證失敗，請返回首頁重試。", 403)

    field_names = ("amount", "note", "category", "payment_source_id", "spent_on")
    draft = {
        name: form.get(name, [""])[0]
        for name in field_names
    }
    if any(len(form.get(name, [])) != 1 for name in field_names):
        return _quick_entry_response(request, user_id, draft, QUICK_ENTRY_ERROR, 400)

    try:
        payment_source_id = int(draft["payment_source_id"])
        life_service.add_expense(
            user_id,
            draft["amount"],
            draft["category"],
            draft["note"],
            draft["spent_on"],
            payment_source_id,
        )
    except (TypeError, ValueError):
        return _quick_entry_response(request, user_id, draft, QUICK_ENTRY_ERROR, 400)

    return RedirectResponse(url="/?saved=1", status_code=303)


async def _settings_post(request: Request, field_names, operation, *, section: str):
    user_id = auth.current_user_id(request.session)
    if not user_id:
        return _error_response(request, "請先使用 Discord 登入。", 403)

    form = await _urlencoded_form(request)
    tokens = form.get("csrf_token", [])
    if len(tokens) != 1 or not auth.validate_csrf(request.session, tokens[0]):
        return _settings_response(
            request, user_id, "設定驗證失敗，請返回設定頁重試。", 403,
            open_section=section, draft={"section": section},
        )

    values = {
        name: form[name][0]
        for name in field_names
        if len(form.get(name, [])) == 1
    }
    draft = {"section": section, **values}
    if len(values) != len(field_names):
        return _settings_response(
            request, user_id, SETTINGS_ERROR, 400,
            open_section=section, draft=draft,
        )
    try:
        operation(user_id, values)
    except (TypeError, ValueError):
        return _settings_response(
            request, user_id, SETTINGS_ERROR, 400,
            open_section=section, draft=draft,
        )
    return RedirectResponse(
        url=f"/settings?saved=1&section={section}", status_code=303
    )


async def _urlencoded_form(request: Request) -> dict[str, list[str]]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != "application/x-www-form-urlencoded":
        return {}
    try:
        return parse_qs(
            (await request.body()).decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=20,
        )
    except (UnicodeDecodeError, ValueError):
        return {}


def _settings_response(
    request: Request,
    user_id: str,
    error: str,
    status_code: int,
    *,
    open_section: str,
    draft: dict[str, str],
) -> HTMLResponse:
    try:
        context = _settings_context(
            user_id,
            request.session["csrf_token"],
            error=error,
            open_section=open_section,
            draft=draft,
        )
    except (TypeError, ValueError):
        return _error_response(request, SETTINGS_ERROR, status_code)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=context,
        status_code=status_code,
    )


def _quick_entry_response(
    request: Request,
    user_id: str,
    draft: dict[str, str],
    error: str,
    status_code: int,
) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="home.html",
        context=_quick_entry_context(
            user_id,
            request.session["csrf_token"],
            draft=draft,
            error=error,
        ),
        status_code=status_code,
    )


def _calendar_month(value: str | None) -> date:
    month = value if value is not None else taiwan_today().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError
    parsed = date.fromisoformat(f"{month}-01")
    if parsed.strftime("%Y-%m") != month:
        raise ValueError
    if parsed > taiwan_today().replace(day=1):
        raise ValueError
    return parsed


def _calendar_day(value: str | None, month_start: date) -> date | None:
    if value is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError
    if parsed.replace(day=1) != month_start or parsed > taiwan_today():
        raise ValueError
    return parsed


def _search_date(value: str, *, allow_future: bool = False) -> date | None:
    if not value:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value or (not allow_future and parsed > taiwan_today()):
        raise ValueError
    return parsed


def _csv_safe_text(value: str) -> str:
    controls = False
    for character in value:
        control = unicodedata.category(character) in ('Cc', 'Cf')
        if character.isspace() or control:
            controls |= control
            continue
        return "'"+value if controls or character in '=+-@' else value
    return "'"+value if controls else value


def _expense_csv(items: list[dict]) -> bytes:
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['日期', '金額', '消費項目', '分類', '付款方式'])
    for item in items:
        writer.writerow([
            item['spent_on'], _format_amount(Decimal(item['cents']) / 100, grouping=False),
            *(_csv_safe_text(item[field]) for field in ('note', 'category', 'payment_source_name')),
        ])
    # ponytail: full CSV stays in memory; change only after isolated size measurements justify it.
    return output.getvalue().encode('utf-8-sig')


def _next_month(month_start: date) -> date:
    return (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _format_twd(cents: int) -> str:
    return _format_amount(Decimal(cents) / 100)


def _search_response(
    request: Request,
    values: dict[str, str],
    *,
    results: list[dict[str, str]] | None = None,
    searched: bool = False,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "values": values,
            "results": results,
            "searched": searched,
            "error": error,
            "deleted": request.query_params.get("deleted") == "1",
        },
        status_code=status_code,
    )


def _quick_entry_context(
    user_id: str,
    csrf_token: str,
    *,
    draft: dict[str, str] | None = None,
    error: str | None = None,
    saved: bool = False,
    as_of: date | None = None,
) -> dict[str, object]:
    as_of = as_of or life_service.get_today()
    categories = life_service.get_categories(user_id)
    payment_sources = life_service.get_payment_sources(user_id)
    active_source_ids = {str(source["id"]) for source in payment_sources}

    if draft is None:
        if "其他" in categories:
            selected_category = "其他"
        else:
            selected_category = categories[0] if categories else ""
        recent = life_service.get_recent_expenses(user_id)
        recent_source_id = str(recent[0].get("payment_source_id")) if recent else ""
        if recent_source_id in active_source_ids:
            selected_payment_source_id = recent_source_id
        else:
            selected_payment_source_id = str(
                next(
                    (
                        source["id"]
                        for source in payment_sources
                        if source["name"] == "未指定"
                    ),
                    "",
                )
            )
        values = {
            "amount": "",
            "note": "",
            "category": selected_category,
            "payment_source_id": selected_payment_source_id,
            "spent_on": as_of.isoformat(),
        }
    else:
        values = {
            name: draft.get(name, "")
            for name in (
                "amount",
                "note",
                "category",
                "payment_source_id",
                "spent_on",
            )
        }

    return {
        **_home_cards_context(user_id, as_of=as_of, active_categories=categories),
        "csrf_token": csrf_token,
        "categories": categories,
        "payment_sources": payment_sources,
        "values": values,
        "invalid_category": values["category"] not in categories,
        "invalid_payment_source": (
            values["payment_source_id"] not in active_source_ids
        ),
        "error": error,
        "saved": saved,
    }


def _calendar_grid_context(
    month_start: date,
    calendar_rows: list[dict],
    *,
    as_of: date,
) -> dict[str, object]:
    return {
        "month_title": month_start.strftime("%Y 年 %m 月"),
        "month_text": month_start.strftime("%Y-%m"),
        "leading_blanks": month_start.weekday(),
        "calendar_days": [
            {
                "date": row["date"],
                "day": date.fromisoformat(row["date"]).day,
                "has_expense": row["cents"] > 0,
                "is_future": date.fromisoformat(row["date"]) > as_of,
            }
            for row in calendar_rows
        ],
        "has_month_expenses": any(row["cents"] > 0 for row in calendar_rows),
    }


def _budget_display_context(summary: dict, active_categories: list[str]) -> dict[str, object]:
    def display(row):
        budget, spent = row['budget_cents'], row['spent_cents']
        if type(budget) is not int or budget < 0 or budget % 100:
            raise ValueError
        difference = budget - spent
        ratio = Decimal(spent) / Decimal(budget) * 100 if budget else None
        status = f'超支 {_format_twd(-difference)} 元' if difference < 0 else f'剩餘 {_format_twd(difference)} 元'
        if difference == 0 and budget > 0:
            status += '（已用完）'
        return {
            'budget': _format_twd(budget), 'spent': _format_twd(spent),
            'remaining': _format_twd(difference) if difference >= 0 else None,
            'overspent': _format_twd(-difference) if difference < 0 else None,
            'used_percent': _format_amount(ratio.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), grouping=False) if ratio is not None else None,
            'progress_value': _format_amount(min(Decimal(100), max(Decimal(0), ratio)), grouping=False) if ratio is not None else None,
            'status_text': status,
        }

    card = {
        'spent': _format_twd(summary['total_cents']), 'total_budget': None,
        'remaining': None, 'overspent': None, 'used_percent': None,
        'progress_value': None, 'status_text': None, 'category_budgets': [],
    }
    for row in sorted(summary['budgets'], key=lambda row: row['category']):
        values = display(row)
        if row['category'] == '總額':
            card.update(total_budget=values.pop('budget'), **values)
        else:
            card['category_budgets'].append({
                'name': row['category'], 'active': row['category'] in active_categories, **values,
            })
    return card


def _home_cards_context(user_id: str, *, as_of: date, active_categories: list[str]) -> dict[str, object]:
    try:
        month_start = as_of.replace(day=1)
        month = month_start.strftime("%Y-%m")
        summary = life_service.get_month_summary(user_id, month)
        calendar_rows = life_service.get_calendar_days(user_id, month)
        budget = _budget_display_context(summary, active_categories)
        budget['month_title'] = month_start.strftime('%Y 年 %m 月')
        return {
            "home_budget": budget,
            "home_calendar": _calendar_grid_context(month_start, calendar_rows, as_of=as_of),
            "home_cards_error": None,
        }
    except (TypeError, ValueError):
        return {
            "home_budget": None,
            "home_calendar": None,
            "home_cards_error": HOME_CARDS_ERROR,
        }


def _settings_context(
    user_id: str,
    csrf_token: str,
    *,
    error: str | None = None,
    saved: bool = False,
    open_section: str = "budget",
    draft: dict[str, str] | None = None,
) -> dict[str, object]:
    month = life_service.get_today().strftime("%Y-%m")
    summary = life_service.get_month_summary(user_id, month)
    active_categories = life_service.get_categories(user_id)
    all_categories = life_service.get_categories(user_id, include_inactive=True)
    active_set = set(active_categories)
    payment_sources = life_service.get_payment_sources(user_id, include_inactive=True)
    budgets = {row["category"]: row["budget"] for row in summary["budgets"]}

    budget_rows = []
    for name in active_categories:
        budget_rows.append(
            {
                "name": name,
                "active": True,
                "amount": _format_amount(budgets[name], grouping=False) if name in budgets else "",
                "display_amount": _format_amount(budgets[name]) if name in budgets else "",
                "has_budget": name in budgets,
            }
        )
    for name in all_categories:
        if name not in active_set and name in budgets:
            budget_rows.append(
                {
                    "name": name,
                    "active": False,
                    "amount": _format_amount(budgets[name], grouping=False),
                    "display_amount": _format_amount(budgets[name]),
                    "has_budget": True,
                }
            )

    return {
        "csrf_token": csrf_token,
        "month_title": f"{month[:4]} 年 {month[5:]} 月",
        "has_budgets": bool(budgets),
        "total_budget": (
            _format_amount(budgets["總額"], grouping=False) if "總額" in budgets else None
        ),
        "budget_rows": budget_rows,
        "has_category_budgets": any(name != "總額" for name in budgets),
        "categories": [
            {"name": name, "active": name in active_set}
            for name in all_categories
        ],
        "has_active_categories": bool(active_categories),
        "payment_sources": payment_sources,
        "error": error,
        "saved": saved,
        "open_section": open_section,
        "draft": draft or {},
    }


def _format_amount(value, *, grouping: bool = True) -> str:
    amount = Decimal(str(value)).normalize()
    return format(amount, ",f" if grouping else "f")


def _error_response(request: Request, message: str, status_code: int) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"message": message},
        status_code=status_code,
    )
