import asyncio
import functools
import json
import re
import xml.etree.ElementTree as ElementTree
from lxml.etree import XMLParser
from datetime import datetime
from typing import Any, TypeVar, Callable, cast, Type

import aiohttp
import requests
from aiohttp import CookieJar
from firebase_admin import db
from firebase_functions.https_fn import FunctionsErrorCode, HttpsError
from firebase_functions.params import SecretParam
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError

from google.oauth2.credentials import Credentials

GRADESCOPE_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S %z"
ILLEGAL_DATABASE_CHARS = "[\\$\\#\\[\\]\\/\\.\\\\]"

T = TypeVar('T')
U = TypeVar('U')
Assignment = dict[str, str | bool]
AssignmentList = dict[str, Assignment]
Course = dict[str, str]
CourseList = dict[str, Course]
CourseSettings = dict[str, str]
Calendar = dict
CallableFunctionResponse = str | dict
UserSettings = dict[str, Any]


# region Gradescope


def check_gradescope_token(token: Any) -> bool:
    if not isinstance(token, str):
        return False

    with requests.get("https://www.gradescope.com/account", cookies={"signed_token": token},
                      allow_redirects=False) as response:
        return response.status_code == 200


def get_gradescope_token(uid: str) -> str | None:
    if not db.reference(f'users/{uid}/gradescope/valid_auth').get():
        return None
    gradescope_token = db.reference(f'users/{uid}/gradescope/token').get()

    if not (gradescope_token := check_gradescope_token(gradescope_token)):
        gradescope_username = get_db_ref_as_type(f'users/{uid}/gradescope/username', str)
        gradescope_password = get_db_ref_as_type(f'users/{uid}/gradescope/password', str)
        if gradescope_username and gradescope_password:
            gradescope_token = login_to_gradescope(gradescope_username, gradescope_password)

        if not gradescope_token:
            db.reference(f'users/{uid}/gradescope/valid_auth').set(False)
            return None

    return gradescope_token


def format_gradescope_url(url: str) -> str:
    return f'https://www.gradescope.com{url if url.startswith("/") else f"/{url}"}'


async def get_async_data_from_gradescope(url: str, query: str, session: aiohttp.ClientSession) \
        -> list[ElementTree.Element]:
    async with session.get(format_gradescope_url(url)) as response:
        if response.status != 200:
            raise RuntimeError(f"Gradescope Error: {response.status}! {await response.read()}")

        return ElementTree.fromstring(await response.read()).findall(query)


def get_data_from_gradescope(url: str, query: str, gradescope_token: str) -> list[ElementTree.Element]:
    gradescope_cookies = {"signed_token": gradescope_token}
    with requests.get(format_gradescope_url(url), cookies=gradescope_cookies) as response:
        if response.status_code != 200:
            raise RuntimeError(f"Gradescope Error: {response.status_code}! {response.content}")

        return ElementTree.fromstring(response.content).findall(query)


def login_to_gradescope(email: str, password: str) -> str | None:
    print(email, password)
    session = requests.Session()
    with session.get("https://www.gradescope.com/login") as response:
        if response.status_code != 200:
            return None
        authenticity_token_el = ElementTree.fromstring(response.content, parser=XMLParser(recover=True)).find(
            ".//input[@name='authenticity_token']")
        if authenticity_token_el is None:
            return None
        authenticity_token = authenticity_token_el.get("value")

    form_data = {
        "utf8": "✓",
        "authenticity_token": authenticity_token,
        "session[email]": email,
        "session[password]": password,
        "session[remember_me]": "0",
        "commit": "Log In",
        "session[remember_me_sso]": "0",
    }
    with session.post("https://www.gradescope.com/login", data=form_data, allow_redirects=False) as response:
        print(response)
        print(response.headers)
        print(response.cookies)
        if response.status_code != 302 or response.headers.get("location", '') != "https://www.gradescope.com/account":
            return None  # Invalid credentials
        return response.cookies.get("signed_token")


# endregion

# region Google

def login_to_google(uid: str, oauth2_client_id: SecretParam, oauth2_client_secret: SecretParam) -> Any:
    refresh_token = get_db_ref_as_type(f'users/{uid}/google/refresh_token', str)
    if not refresh_token or refresh_token == "invalid":
        return None

    scopes = [
        "https://www.googleapis.com/auth/calendar.calendarlist",
        "https://www.googleapis.com/auth/calendar.calendars",
        "https://www.googleapis.com/auth/calendar.events"
    ]
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=oauth2_client_id.value,
        client_secret=oauth2_client_secret.value,
        scopes=scopes
    )
    try:
        credentials.refresh(Request())
    except RefreshError:
        db.reference(f'users/{uid}/google/refresh_token').set("invalid")
        return None
    db.reference(f'users/{uid}/google/refresh_token').set(credentials.refresh_token)

    return credentials


def create_assignment_event(calendar_service: Any, event_create_batch: Any, calendar_id: str, course: Course,
                            assignment: Assignment, completed_color: str | None,
                            callback: Callable[[Any, Any, Any], Any]) -> None:
    if not validate_object_with_keys(course, "name", "color", "href"):
        return

    event = {
        "summary": f'{assignment["name"]} [{assignment["course_id"]}]',
        "description": f'Assignment for <a href="{format_gradescope_url(course["href"])}">{course["name"]}</a> on '
                       f'Gradescope',
        "start": {
            "dateTime": assignment["due_date"]
        },
        "end": {
            "dateTime": assignment["due_date"]
        },
        "colorId": completed_color if completed_color and assignment["completed"] else course["color"],
    }
    event_create_batch.add(calendar_service.events().insert(calendarId=calendar_id, body=event), callback=callback)


def patch_assignment_event(calendar_service: Any, event_update_batch: Any, calendar_id: str, event_id: str,
                           course: Course, assignment: Assignment, completed_color: str | None) -> None:
    if not validate_object_with_keys(course, "name", "color", "href"):
        return

    event = {
        "start": {
            "dateTime": assignment["due_date"]
        },
        "end": {
            "dateTime": assignment["due_date"]
        },
        "colorId": completed_color if completed_color and assignment["completed"] else course["color"],
    }
    event_update_batch.add(calendar_service.events().patch(calendarId=calendar_id, eventId=event_id, body=event))


# endregion

# region Firebase

def get_db_ref_as_type(path: str, datatype: Type[T], **kwargs) -> T:
    return cast(datatype, db.reference(path).get(**kwargs))


def fn_response(data: str | dict, code: FunctionsErrorCode = FunctionsErrorCode.OK) -> CallableFunctionResponse:
    if code == FunctionsErrorCode.OK:
        return data
    if isinstance(data, dict):
        data = json.dumps(data)
    raise HttpsError(code=code, message=data)


# endregion

# region Assignments


def due_date_from_progress_div(progress_div: ElementTree.Element) -> str:
    times = progress_div.findall("./time")
    return datetime.strptime(times[1].get("datetime"), GRADESCOPE_DATETIME_FORMAT).isoformat()


async def enumerate_gradescope_assignments(course_settings: CourseList, gradescope_token: str) -> AssignmentList:
    gradescope_cookies = {"signed_token": gradescope_token}
    async with aiohttp.ClientSession(cookies=gradescope_cookies, cookie_jar=CookieJar(quote_cookie=False)) as session:
        tasks = [fetch_course_assignments(course_id, course, session) for course_id, course in
                 course_settings.items()]
        assignments = await asyncio.gather(*tasks)

    assignments = {assignment_id: assignment for course_assignments in assignments for assignment_id, assignment in
                   course_assignments.items()}

    return assignments


async def fetch_course_assignments(course_id: str, course: Course, session: aiohttp.ClientSession) -> AssignmentList:
    assignments = {
        f'{course_id}-{re.sub(ILLEGAL_DATABASE_CHARS, "", get_assignment_name(assignment))}':
            parse_assignment(assignment, course_id)
        for assignment in
        await get_async_data_from_gradescope(course["href"], ".//table[@id='assignments-student-table']/tbody/tr",
                                             session)
        if len(assignment[2][0]) > 1  # If the assignment is past due, Gradescope will not include a progress bar div
    }
    # Filter out assignments that don't have a due date or were parsed incorrectly
    assignments = {
        assignment_id: assignment for assignment_id, assignment in assignments.items() if
        isinstance(assignment, dict) and assignment["due_date"]
    }

    return assignments


def get_assignment_name(assignment: ElementTree.Element) -> str:
    assignment_name = assignment.find("./th")
    return transform_or_default(assignment_name[0] if len(assignment_name) > 0 else assignment_name,
                                lambda name: name.text, "<Unknown Assignment>").strip()


def parse_assignment(assignment: ElementTree.Element, course_id: str) -> Assignment | None:
    try:
        return {
            "name": get_assignment_name(assignment),
            "due_date": due_date_from_progress_div(assignment[2][0][2]),
            "completed": assignment[1][1].text == "Submitted",
            "course_id": course_id,
            "due_date_changed": False
        }
    except Exception as e:
        print(e)
        return None


def update_gradescope_assignment(assignment: Assignment, old_assignment: Assignment | None) -> Assignment:
    if not old_assignment:
        assignment["event_id"] = ""
        assignment["due_date_changed"] = False
    else:
        assignment["event_id"] = old_assignment["event_id"]
        assignment["due_date_changed"] = (old_assignment["due_date_changed"]
                                          or assignment["due_date"] != old_assignment["due_date"])
    return assignment


# endregion

# region Database Helpers


def get_calendar_id(uid: str) -> str | None:
    calendar_id = get_db_ref_as_type(f'users/{uid}/google/calendar_id', str)

    if isinstance(calendar_id, str) and calendar_id != "invalid":
        return calendar_id
    return None


def validate_calendar_id(calendar_id: str, calendar_service: Any) -> bool:
    try:
        calendar = calendar_service.calendarList().get(calendarId=calendar_id).execute()
    except HttpError as e:
        if e.status_code == 404:
            return False
        else:
            raise e

    return calendar and not calendar.get("deleted", False) and calendar["accessRole"] in ("owner", "writer")


def get_user_settings(uid: str) -> UserSettings | None:
    user_settings = get_db_ref_as_type(f'users/{uid}/settings', UserSettings)
    if validate_object_with_keys(user_settings, "courses", "completed_assignment_color"):
        return user_settings
    return None


# region Util

def sync(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


def transform_or_default(data: T | None, transform: Callable[[T], U], default: U) -> U:
    return default if data is None else transform(data)


def validate_object_with_keys(obj: Any, *keys: str) -> bool:
    return obj and all(key in obj for key in keys)


def wrap_async_exceptions(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            print(e)
            from google.cloud import error_reporting
            error_reporting.Client().report_exception()

    return wrapper

# endregion
