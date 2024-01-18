import asyncio
import functools
import json
import re
from lxml import etree as ElementTree
from datetime import datetime
from typing import Any, TypeVar, Callable, cast, Type, Optional

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

# The format of the datetime strings returned by Gradescope
GRADESCOPE_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S %z"
# The scopes required by the Google Calendar API
GOOGLE_API_SCOPES = [
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events"
]

# A bunch of type definitions
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
    """
    Checks if a Gradescope token is valid

    Args:
        token: The token to check

    Returns:
        True if the token is valid, False otherwise
    """
    # The token must be a string
    if not isinstance(token, str):
        return False

    # Check if we can access the Gradescope account page without being redirected to the login page
    # Note: If the user is logged in, this returns the course list, so this could be used to add a course update feature
    # whenever the Gradescope token is validated, but ATM, I don't know if I want to do that EVERY TIME the token is
    # validated, and I don't want to rewrite this function to add that functionality. Maybe later.
    with requests.get("https://www.gradescope.com/account", cookies={"signed_token": token},
                      allow_redirects=False) as response:
        return response.status_code == 200


def get_gradescope_token(uid: str) -> str | None:
    """
    Gets the Gradescope token for a user from the database. If the token is invalid, this method will attempt to log in
    to Gradescope with the user's credentials (if available) and refresh the token.

    Args:
        uid: The user's UID

    Returns:
        The user's Gradescope token, or None if a token could not be obtained
    """
    # Has the user linked their Gradescope account?
    if not db.reference(f'auth_status/{uid}/gradescope').get():
        return None
    gradescope_token = db.reference(f'credentials/{uid}/gradescope/token').get()

    # Is the saved token valid?
    if not check_gradescope_token(gradescope_token):
        # If not, do we have credentials to log in to Gradescope?
        gradescope_token = None
        gradescope_email = get_db_ref_as_type(f'credentials/{uid}/gradescope/email', str)
        gradescope_password = get_db_ref_as_type(f'credentials/{uid}/gradescope/password', str)
        if gradescope_email and gradescope_password:
            # If so, log in to Gradescope and get a new token
            gradescope_token, _ = login_to_gradescope(gradescope_email, gradescope_password)

        # If we still don't have a token, the user needs to relink their Gradescope account
        if not gradescope_token:
            db.reference(f'auth_status/{uid}/gradescope').set(False)
            return None

    return gradescope_token


def format_gradescope_url(url: str) -> str:
    """
    Takes a href from a Gradescope page and formats it into a full URL

    Args:
        url: The href to format

    Returns:
        The formatted URL
    """
    return f'https://www.gradescope.com{url if url.startswith("/") else f"/{url}"}'


async def get_async_data_from_gradescope(url: str, query: str, session: aiohttp.ClientSession) \
        -> list[ElementTree.Element]:
    """
    Downloads a Gradescope page asynchronously and parses it with XPath

    Args:
        url: The URL of the Gradescope page to download
        query: The XPath query to use to parse the page
        session: The aiohttp session to use to download the page

    Returns:
        The parsed elements

    Raises:
        RuntimeError: If the request fails
    """
    async with session.get(format_gradescope_url(url)) as response:
        if response.status != 200:
            raise RuntimeError(f"Gradescope Error: {response.status}! {await response.read()}")

        return ElementTree.HTML(await response.read(), None).findall(query)


def get_data_from_gradescope(url: str, query: str, gradescope_token: str) -> list[ElementTree.Element]:
    """
    Downloads a Gradescope page and parses it with XPath

    Args:
        url: The URL of the Gradescope page to download
        query: The XPath query to use to parse the page
        gradescope_token: The user's Gradescope token

    Returns:
        The parsed elements

    Raises:
        RuntimeError: If the request fails
    """
    gradescope_cookies = {"signed_token": gradescope_token}
    with requests.get(format_gradescope_url(url), cookies=gradescope_cookies) as response:
        if response.status_code != 200:
            raise RuntimeError(f"Gradescope Error: {response.status_code}! {response.content}")

        return ElementTree.HTML(response.content).findall(query)


def login_to_gradescope(email: str, password: str) -> Optional[str]:
    """
    Attempts to log in to Gradescope with the given credentials and returns the token and expiration date if successful

    Args:
        email: The user's email address
        password: The user's Gradescope password

    Returns:
        The user's token or None if the login failed
    """
    # We first have to make a GET request to the login page to get an authenticity token
    # We use a session because Gradescope checks the authenticity token against a cookie to prevent CSRF attacks
    session = requests.Session()
    with session.get("https://www.gradescope.com/login") as response:
        if response.status_code != 200:
            return None
        # Extract the authenticity token from the login page
        authenticity_token_el = ElementTree.HTML(response.content, parser=ElementTree.HTMLParser(recover=True)).find(
            ".//input[@name='authenticity_token']")
        if authenticity_token_el is None:
            return None
        authenticity_token = authenticity_token_el.get("value")

    # Build the form response data for the login request
    form_data = {
        "utf8": "✓",
        "authenticity_token": authenticity_token,
        "session[email]": email,
        "session[password]": password,
        "session[remember_me]": "1",
        "commit": "Log In",
        "session[remember_me_sso]": "0",
    }
    # Try to log in to Gradescope
    with (session.post("https://www.gradescope.com/login", data=form_data, allow_redirects=False) as response):
        # If we're successfully logged in, we should be redirected to the account page
        if response.status_code != 302 or response.headers.get("location", '') != "https://www.gradescope.com/account":
            return None  # Invalid credentials
        # Extract the token cookie from the response
        return response.cookies.get("signed_token", None)


def logout_of_gradescope(token: str) -> None:
    """
    Logs a user out of Gradescope by attempting to invalidate their token
    This function does not remove the user's credentials from the database

    Args:
        token: The user's Gradescope token

    Returns:
        None
    """
    gradescope_cookies = {"signed_token": token}
    with requests.get(format_gradescope_url("/logout?tfs_mode=false"), cookies=gradescope_cookies,
                      allow_redirects=False) as _response:
        pass  # Ignore the response


# endregion

# region Google

def login_to_google(uid: str, oauth2_client_id: SecretParam, oauth2_client_secret: SecretParam) -> Any:
    """
    Attempts to redeem a user's Google refresh token for an access token and returns the credentials if successful

    Args:
        uid: The user's UID
        oauth2_client_id: This app's Google OAuth2 client ID
        oauth2_client_secret: This app's Google OAuth2 client secret

    Returns:
        The user's Google credentials, or None if the login failed
    """
    # Has the user linked their Google account?
    if not db.reference(f'auth_status/{uid}/google').get():
        return None

    # Get the user's refresh token
    if not (refresh_token := get_db_ref_as_type(f'credentials/{uid}/google/token', str)):
        db.reference(f'auth_status/{uid}/google').set(False)
        return None

    # Create a Credentials object from the refresh token
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=oauth2_client_id.value,
        client_secret=oauth2_client_secret.value,
        scopes=GOOGLE_API_SCOPES
    )
    try:
        # Attempt to redeem the refresh token for an access token
        credentials.refresh(Request())
    except RefreshError:
        db.reference(f'auth_status/{uid}/google').set(False)
        return None

    # Save the new refresh token if it has changed
    if credentials.refresh_token != refresh_token:
        db.reference(f'credentials/{uid}/google/token').set(credentials.refresh_token)

    return credentials


def create_assignment_event(calendar_service: Any, event_create_batch: Any, calendar_id: str, course: Course,
                            assignment: Assignment, completed_color: str | None,
                            callback: Callable[[Any, Any, Any], Any]) -> None:
    """
    Creates a Google Calendar event for an assignment

    Args:
        calendar_service: The Google Calendar service
        event_create_batch: The batch to add the event creation request to
        calendar_id: The ID of the calendar to create the event in
        course: The course the assignment is for
        assignment: The assignment to create the event for
        completed_color: The color to use for completed assignments
        callback: The callback to pass to the batch to call when the event is created

    Returns:
        None
    """
    # Check that the associated course has enough information to create an event
    if not validate_object_with_keys(course, "name", "color", "href"):
        return

    # Create the event object
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
    # Add a request to create the event to the batch
    event_create_batch.add(calendar_service.events().insert(calendarId=calendar_id, body=event), callback=callback)


def patch_assignment_event(calendar_service: Any, event_update_batch: Any, calendar_id: str, course: Course,
                           assignment: Assignment, completed_color: str | None) -> None:
    """
    Patches a Google Calendar event for an assignment with updated information

    Args:
        calendar_service: The Google Calendar service
        event_update_batch: The batch to add the event patch request to
        calendar_id: The ID of the calendar to patch the event in
        course: The course the assignment is for
        assignment: The assignment to patch the event for
        completed_color: The color to use for completed assignments

    Returns:
        None
    """
    # Check that the associated course has enough information to patch an event
    if not validate_object_with_keys(course, "name", "color", "href"):
        return

    # Create the event object
    event = {
        "summary": f'{assignment["name"]} [{assignment["course_id"]}]',
        "start": {
            "dateTime": assignment["due_date"]
        },
        "end": {
            "dateTime": assignment["due_date"]
        },
        "colorId": completed_color if completed_color and assignment["completed"] else course["color"],
    }
    # Add a request to patch the event to the batch
    event_update_batch.add(calendar_service.events().patch(calendarId=calendar_id, eventId=assignment["event_id"],
                                                           body=event))


# endregion

# region Firebase

def get_db_ref_as_type(path: str, datatype: Type[T], **kwargs) -> T:
    """
    Gets a reference from the Firebase database, retrieves its value, and casts it to the given type

    Args:
        path: The path to the reference
        datatype: The type to cast the value to
        **kwargs: Additional keyword arguments to pass to the get method

    Returns:
        The value of the reference, cast to the given type
    """
    return cast(datatype, db.reference(path).get(**kwargs))


def fn_response(data: str | dict, code: FunctionsErrorCode = FunctionsErrorCode.OK) -> CallableFunctionResponse:
    """
    Formats a response to a Firebase callable function

    Args:
        data: The data to return
        code: The status code to return

    Returns:
        The formatted response

    Raises:
        HttpsError: If the status code is not OK
    """
    # If the code is OK, the data can be returned as-is
    if code == FunctionsErrorCode.OK:
        return data
    # Otherwise, the data must be returned as an error
    # Stringify the data if it is a dict
    if isinstance(data, dict):
        data = json.dumps(data)
    # Raise an error with the data as the message
    raise HttpsError(code=code, message=data)


# endregion

# region Assignments


def due_date_from_progress_div(progress_div: ElementTree.Element) -> str:
    """
    Parses a Gradescope progress div and returns the due date

    Args:
        progress_div: The progress div to parse

    Returns:
        The due date of the assignment
    """
    # HTML parsing nonsense
    times = progress_div.findall("./time")
    return datetime.strptime(times[1].get("datetime"), GRADESCOPE_DATETIME_FORMAT).isoformat()


async def enumerate_gradescope_assignments(course_settings: CourseList, gradescope_token: str) -> AssignmentList:
    """
    Downloads the Gradescope assignments for a user's courses and returns them in a dictionary

    Args:
        course_settings: The user's course settings
        gradescope_token: The user's Gradescope token

    Returns:
        The user's Gradescope assignments in a dictionary, mapping assignment IDs to assignments

    Raises:
        RuntimeError: If a request fails
    """
    # Create a single session to use for all the requests
    gradescope_cookies = {"signed_token": gradescope_token}
    async with aiohttp.ClientSession(cookies=gradescope_cookies, cookie_jar=CookieJar(quote_cookie=False)) as session:
        # Fetch the assignments for each course asynchronously
        tasks = [fetch_course_assignments(course_id, course, session) for course_id, course in
                 course_settings.items()]
        assignments = await asyncio.gather(*tasks)

    # Flatten the list of assignments into a single dictionary
    assignments = {assignment_id: assignment for course_assignments in assignments for assignment_id, assignment in
                   course_assignments.items()}

    return assignments


async def fetch_course_assignments(course_id: str, course: Course, session: aiohttp.ClientSession) -> AssignmentList:
    """
    Downloads the Gradescope assignments for a single course and returns them in a dictionary

    Args:
        course_id: The ID of the course
        course: The course to download the assignments for
        session: The aiohttp session to use to download the assignments (must be authenticated with Gradescope)

    Returns:
        The course's assignments in a dictionary, mapping assignment IDs to assignments

    Raises:
        RuntimeError: If the request fails
    """
    assignments = {
        # The assignment ID is the Gradescope assignment ID prefixed with the course ID
        f'{course_id}-{get_assignment_id(assignment)}': parse_assignment(assignment, course_id)
        for assignment in
        await get_async_data_from_gradescope(course["href"], ".//table[@id='assignments-student-table']/tbody/tr",
                                             session)
        # If the assignment is past due or does not have a due date, Gradescope will not include a progress bar div
        if len(assignment[2]) >= 1 and len(assignment[2][0]) > 1
    }
    # Filter out assignments that don't have a due date or were parsed incorrectly
    assignments = {
        assignment_id: assignment for assignment_id, assignment in assignments.items() if
        isinstance(assignment, dict) and assignment["due_date"] and not assignment_id.endswith("-Unknown")
    }

    return assignments


def get_assignment_id(assignment: ElementTree.Element) -> str:
    """
    Gets the Gradescope assignment ID from an assignment element

    Args:
        assignment: The assignment element to get the ID from

    Returns:
        The Gradescope assignment ID or "Unknown" if the ID could not be found
    """
    # HTML parsing nonsense
    assignment = assignment[0][0]
    # Gradescope will sometimes use a button and sometimes use a link to the assignment page
    if assignment.tag == "button":
        # If it's a button, the ID is in the data-assignment-id attribute
        return assignment.get("data-assignment-id", "Unknown")
    elif assignment.tag == "a":
        # If it's a link, the ID is in the href attribute
        href = assignment.get("href", "")
        if match := re.search(r'/assignments/(\d+)', href):
            return match.group(1)

    # If we can't find the ID, return "Unknown"
    return "Unknown"


def get_assignment_name(assignment: ElementTree.Element) -> str:
    """
    Gets the name of an assignment from an assignment element

    Args:
        assignment: The assignment element to get the name from

    Returns:
        The name of the assignment or "<Unknown Assignment>" if the name could not be found
    """
    # HTML parsing nonsense
    assignment_name = assignment.find("./th")
    return transform_or_default(assignment_name[0] if len(assignment_name) > 0 else assignment_name,
                                lambda name: name.text, "<Unknown Assignment>").strip()


def parse_assignment(assignment: ElementTree.Element, course_id: str) -> Assignment | None:
    """
    Parses an assignment element and returns the assignment information in a dictionary

    Args:
        assignment: The assignment element to parse
        course_id: The ID of the course the assignment is for

    Returns:
        The assignment information in a dictionary, or None if the assignment could not be parsed
    """
    try:
        return {
            "name": get_assignment_name(assignment),
            "due_date": due_date_from_progress_div(assignment[2][0][2]),
            "completed": assignment[1][1].text == "Submitted",
            "course_id": course_id,
            # The event is not outdated if the assignment has not yet been added to the cache
            "outdated": False
        }
    except Exception as e:
        print(e)
        return None


def update_gradescope_assignment(assignment: Assignment, old_assignment: Assignment | None) -> Assignment:
    """
    Updates an assignment in the user's assignment cache with new information from Gradescope
    """
    if not old_assignment:
        # If the assignment is new, it has no event ID and is not associated with an outdated event
        assignment["event_id"] = ""
        assignment["outdated"] = False
    else:
        # Otherwise, the assignment has the same event ID and the event is outdated if something has changed
        assignment["event_id"] = old_assignment["event_id"]
        assignment["outdated"] = (old_assignment["outdated"] or assignment["due_date"] != old_assignment["due_date"]
                                  or assignment["name"] != old_assignment["name"])
    return assignment


# endregion

# region Database Helpers


def validate_calendar_id(calendar_id: str, calendar_service: Any) -> bool:
    """
    Checks if a calendar ID is valid and accessible by the user

    Args:
        calendar_id: The calendar ID to check
        calendar_service: The Google Calendar service

    Returns:
        True if the calendar ID is valid and the user has write access to the calendar, False otherwise
    """
    # Fetch the calendar from the Google Calendar API
    try:
        calendar = calendar_service.calendarList().get(calendarId=calendar_id).execute()
    except HttpError as e:
        if e.status_code == 404:
            # If the calendar doesn't exist, it is invalid
            return False
        else:
            # If an unexpected error occurred, raise it
            raise e

    # Check that the calendar is not deleted and the user has write access to it
    return calendar and not calendar.get("deleted", False) and calendar["accessRole"] in ("owner", "writer")


def get_user_settings(uid: str) -> UserSettings | None:
    """
    Gets and validates a user's settings from the database

    Args:
        uid: The user's UID

    Returns:
        The user's settings, or None if the settings could not be retrieved or are invalid
    """
    # Get the user's settings from the database
    user_settings = get_db_ref_as_type(f'settings/{uid}', UserSettings)
    # Validate the user's settings
    if validate_object_with_keys(user_settings, "calendar_id", "courses", "completed_assignment_color"):
        # If the settings are valid, return them
        return user_settings
    # Otherwise, return None
    return None


# endregion

# region Util

def sync(func: Callable) -> Callable:
    """
    Runs an async function synchronously
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


def transform_or_default(data: T | None, transform: Callable[[T], U], default: U) -> U:
    """
    Transforms data with transform if it is not None, otherwise returns a default value
    (This is mostly used for HTML parsing where it is uncertain if a tag exists)
    """
    return default if data is None else transform(data)


def validate_object_with_keys(obj: Any, *keys: str) -> bool:
    """
    Checks if an object exists and has all the specified keys
    """
    return obj and all(key in obj for key in keys)


def wrap_async_exceptions(func: Callable) -> Callable:
    """
    Wraps an async function so that any exceptions are silenced and reported to Google Cloud Error Reporting
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            try:
                print(e)
                from google.cloud import error_reporting
                error_reporting.Client().report_exception()
            except Exception as e2:
                print(e2)

    return wrapper

# endregion
