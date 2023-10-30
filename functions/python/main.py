import asyncio
from typing import Any

from firebase_admin import db, initialize_app
from firebase_functions import db_fn, https_fn, scheduler_fn
from firebase_functions.https_fn import FunctionsErrorCode
from firebase_functions.params import SecretParam
from googleapiclient.discovery import build as build_google_api_service
from google_auth_oauthlib.flow import Flow

import utils

# Firebase Admin SDK initialization
app = initialize_app(options={"databaseURL": "http://127.0.0.1:9000/?ns=coolspy3-gradescope-calendar-default-rtdb"})

# Environment variable initialization
OAUTH2_CLIENT_ID = SecretParam("GOOGLE_CLIENT_ID")
OAUTH2_CLIENT_SECRET = SecretParam("GOOGLE_CLIENT_SECRET")


@https_fn.on_call(secrets=[OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET])
def oauth_callback(request: https_fn.CallableRequest) -> utils.CallableFunctionResponse:
    """
    This function is called by the client to complete the Google OAuth flow.
    """

    # Check that the user is authenticated, and the request is valid
    if not request.auth:
        return utils.fn_response({"success": False}, FunctionsErrorCode.UNAUTHENTICATED)
    uid = request.auth.uid

    if "code" not in request.data:
        return utils.fn_response({"success": False}, FunctionsErrorCode.INVALID_ARGUMENT)

    # Create a Google OAuth flow and use it to redeem the code for a refresh token
    flow = Flow.from_client_config(client_config={
        "web": {
            "client_id": OAUTH2_CLIENT_ID.value,
            "client_secret": OAUTH2_CLIENT_SECRET.value,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
    }, scopes=utils.GOOGLE_API_SCOPES)
    try:
        flow.fetch_token(code=request.data["code"])
    except Exception as e:
        print(e)
        return utils.fn_response({"success": False}, FunctionsErrorCode.INVALID_ARGUMENT)

    # Store the refresh token in the database
    db.reference(f'credentials/{uid}/google/token').set(flow.credentials.refresh_token)
    db.reference(f'auth_status/{uid}/google').set(True)

    return utils.fn_response({"success": True})


@https_fn.on_call()
def update_gradescope_token(req: https_fn.CallableRequest) -> utils.CallableFunctionResponse:
    """
    This function is called by the client to update the user's Gradescope token.
    """

    # Check that the user is authenticated
    if not req.auth:
        return utils.fn_response({"success": False}, FunctionsErrorCode.UNAUTHENTICATED)
    uid = req.auth.uid

    if "token" in req.data:  # If the request has a token parameter, we're authenticating by token

        if utils.check_gradescope_token(req.data["token"]):  # If the token is valid
            # Store it in the database
            gradescope_credentials = {
                "token": req.data["token"],
                # While we're at it, delete the email and password from the database (if they exist)
                "email": None,
                "password": None
            }
            db.reference(f'credentials/{uid}/gradescope').set(gradescope_credentials)
            db.reference(f'auth_status/{uid}/gradescope').set(True)
            return utils.fn_response({"success": True})
        else:
            return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.INVALID_ARGUMENT)

    # If the request doesn't have a token parameter, we're authenticating by email and password, so
    # the request must have an email and password parameter
    if "email" not in req.data or "password" not in req.data:
        return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.INVALID_ARGUMENT)

    # Try to log in to Gradescope with the given email and password
    token, expiration = utils.login_to_gradescope(req.data["email"], req.data["password"])
    if not token:
        return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.INVALID_ARGUMENT)

    # If the login was successful, store the token in the database
    store_credentials = req.data.get("store-credentials", False)
    gradescope_credentials = {
        "token": token,
        # If the user wants to store their credentials, store them, otherwise delete them (if they exist)
        "email": req.data["email"] if store_credentials else None,
        "password": req.data["password"] if store_credentials else None
    }

    db.reference(f'credentials/{uid}/gradescope').set(gradescope_credentials)
    db.reference(f'auth_status/{uid}/gradescope').set(True)

    # If the user doesn't want to store their credentials, tell them the expiration date (if we know it)
    if not store_credentials and expiration:
        return utils.fn_response({"success": True, "expiration": expiration})

    return utils.fn_response({"success": True})


@db_fn.on_value_written("credentials/{uid}/gradescope/token")
def invalidate_gradescope_token(event: db_fn.Event[Any]) -> None:
    """
    This function is called by the database when the user's Gradescope token is updated.
    """
    old_token = event.data["old"]
    if not old_token or not isinstance(old_token, str):
        return
    # Invalidate the user's token
    utils.logout_of_gradescope(old_token)

@https_fn.on_call()
def refresh_course_list(req: https_fn.CallableRequest) -> utils.CallableFunctionResponse:
    """
    This function is called by the client to update the user's course list.
    """

    # Check that the user is authenticated
    if not req.auth:
        return utils.fn_response({"success": False}, FunctionsErrorCode.UNAUTHENTICATED)
    uid = req.auth.uid

    # Check that the user has a valid Gradescope token
    if not (gradescope_token := utils.get_gradescope_token(uid)):
        return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.PERMISSION_DENIED)

    # Get the user's courses from Gradescope
    gradescope_courses = [
        {
            "name": utils.transform_or_default(course.find("./h3"), lambda course_name: course_name.text,
                                               "<Unknown Course>").strip(),
            # The href is relative to the Gradescope domain (https://www.gradescope.com/<href>)
            "href": course.attrib["href"]
        }
        for course in utils.get_data_from_gradescope("",
                                                     # HTML parsing nonsense
                                                     ".//div[@class='courseList']/div["
                                                     "@class='courseList--coursesForTerm'][2]/a[@class='courseBox']",
                                                     gradescope_token)
    ]
    # Map each course's ID to the course object
    gradescope_courses = {
        # The course ID is part of the URL (/courses/<course id>)
        course["href"][course["href"].rindex('/') + 1:]: course for course in gradescope_courses
    }

    # Copy the color settings from the existing courses
    # If the course doesn't exist in the existing courses, default to color "1"
    existing_courses = utils.get_db_ref_as_type(f'settings/{uid}/courses', utils.CourseSettings) or {}

    for course_id, course in gradescope_courses.items():
        if course_id in existing_courses:
            course["color"] = existing_courses[course_id]["color"]
        else:
            course["color"] = "1"

    # Store the courses in the database
    db.reference(f'settings/{uid}/courses').set(gradescope_courses)

    return utils.fn_response({"success": True})


@https_fn.on_call()
@utils.sync
async def refresh_events(req: https_fn.CallableRequest) -> utils.CallableFunctionResponse:
    """
    This function is called by the client to force an update the user's assignment cache and calendar.
    """
    # Check that the user is authenticated
    if not req.auth:
        return utils.fn_response({"success": False}, FunctionsErrorCode.UNAUTHENTICATED)
    uid = req.auth.uid

    # Check that the user has valid settings and a valid Gradescope token
    if not (user_settings := utils.get_user_settings(uid)):
        return utils.fn_response("invalid_user_settings", FunctionsErrorCode.FAILED_PRECONDITION)

    # Validating the Gradescope token is more expensive, so we do it last
    if not (gradescope_token := utils.get_gradescope_token(uid)):
        return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.PERMISSION_DENIED)

    # Connect to the Google Calendar API
    with build_google_api_service('calendar', 'v3',
                                  credentials=utils.login_to_google(uid, OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET)
                                  ) as calendar_service:

        # Validate the user's calendar ID
        if not utils.validate_calendar_id(user_settings["calendar_id"], calendar_service):
            db.reference(f'settings/{uid}/calendar_id').delete()
            return utils.fn_response("invalid_calendar_selection", FunctionsErrorCode.FAILED_PRECONDITION)

        # Update the user's assignment cache and use the updated cache to update the user's calendar
        assignment_cache = await get_updated_assignment_cache(uid, user_settings, gradescope_token)

        await update_calendar_from_cache(uid, calendar_service, user_settings, assignment_cache)

    return utils.fn_response({"success": True})


# Run 4 times a day (every 6 hours) on the hour
@scheduler_fn.on_schedule(schedule="0 */6 * * *")
@utils.sync
async def update_event_caches(_event: scheduler_fn.ScheduledEvent) -> None:
    """
    This function is called by the periodically to update the assignment cache for all users.
    """
    # Get all users
    users = utils.get_db_ref_as_type("users", dict, shallow=True)
    if not users:
        return

    # Update the assignment cache for each user asynchronously
    tasks = [update_event_cache_for_user(uid) for uid in users.keys()]
    await asyncio.gather(*tasks)


# Run 4 times a day (every 6 hours) on the half hour
@scheduler_fn.on_schedule(schedule="30 */6 * * *", secrets=[OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET])
@utils.sync
async def update_calendars(_event: scheduler_fn.ScheduledEvent) -> None:
    """
    This function is called by the periodically to push updates from the assignment cache to users' calendars.
    """
    # Get all users
    users = utils.get_db_ref_as_type("users", dict, shallow=True)
    if not users:
        return

    # Update the calendar for each user asynchronously
    tasks = [update_calendar_for_user(uid) for uid in users.keys()]
    await asyncio.gather(*tasks)


@utils.wrap_async_exceptions
async def update_event_cache_for_user(uid) -> None:
    """
    Updates the assignment cache for a single user and stores the updated cache in the database.
    """
    # Check that the user has valid settings and a valid Gradescope token
    if (gradescope_token := utils.get_gradescope_token(uid)) and (user_settings := utils.get_user_settings(uid)):
        # Update the user's assignment cache
        assignment_cache = await get_updated_assignment_cache(uid, user_settings, gradescope_token)

        # Store the updated cache in the database
        db.reference(f'assignments/{uid}').set(assignment_cache)


@utils.wrap_async_exceptions
async def update_calendar_for_user(uid) -> None:
    """
    Updates the calendar for a single user using the user's assignment cache.
    """
    # Check that the user has valid settings
    if not (user_settings := utils.get_user_settings(uid)):
        return

    # Connect to the Google Calendar API
    with build_google_api_service('calendar', 'v3',
                                  credentials=utils.login_to_google(uid, OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET)
                                  ) as calendar_service:

        # Validate the user's calendar ID
        if not utils.validate_calendar_id(user_settings["calendar_id"], calendar_service):
            db.reference(f'settings/{uid}/calendar_id').set("invalid")
            return

        # Get the user's assignment cache (if it exists)
        if not (assignment_cache := utils.get_db_ref_as_type(f'assignments/{uid}', dict)):
            return

        # Update the user's calendar using the assignment cache
        await update_calendar_from_cache(uid, calendar_service, user_settings, assignment_cache)


async def get_updated_assignment_cache(uid: str, user_settings: dict[str, Any], gradescope_token: str) \
        -> dict[str, Any]:
    """
    Updates the user's assignment cache with new data from Gradescope and returns the updated cache.
    """
    # Get the user's assignments from Gradescope
    assignments = await utils.enumerate_gradescope_assignments(user_settings["courses"], gradescope_token)

    # Get the user's assignment cache (if it exists)
    assignment_cache = utils.get_db_ref_as_type(f'assignments/{uid}', dict) or {}
    # Filter out assignments that are not in the user's current course list
    assignment_cache = {assignment_id: assignment for assignment_id, assignment in assignment_cache.items() if
                        assignment["course_id"] in user_settings["courses"]}

    # For each assignment
    for assignment_id, assignment in assignments.items():
        # If the assignment is completed but not in the cache (there's no event for it), skip it
        if assignment["completed"] and assignment_id not in assignment_cache:
            continue

        # Update the assignment in the cache with the new data from Gradescope
        assignment_cache[assignment_id] = utils.update_gradescope_assignment(assignment,
                                                                             assignment_cache.get(assignment_id, None))

    return assignment_cache


async def update_calendar_from_cache(uid: str, calendar_service: Any, user_settings: dict[str, Any],
                                     assignment_cache: utils.AssignmentList) -> None:
    completed_assignment_color = user_settings["completed_assignment_color"]

    # Create a batch request to update the user's calendar
    event_update_batch = calendar_service.new_batch_http_request()

    def update_cache(updated_assignment):
        """
        Returns a callback that updates the assignment cache with the event ID of the updated assignment.
        This can be used as a callback for the Google Calendar API. When the request to create an event completes,
        it's response will contain the event ID, which we can use to update the assignment cache.
        """
        def update_cache_helper(_request_id, response, _exception):
            # updated_assignment is a reference to the assignment in the cache, so we can modify it directly
            updated_assignment["event_id"] = response["id"]

        return update_cache_helper

    # For each assignment in the cache (Create a copy, so we can modify the cache while iterating)
    for assignment_id, assignment in assignment_cache.copy().items():
        # If the assignment is completed, remove it from the cache
        # (We'll mark it as completed during the lope, but we don't need it later)
        if assignment["completed"]:
            assignment_cache.pop(assignment_id, None)

        # If the assignment has an event associated with it
        if assignment["event_id"]:
            # And something about the assignment has changed
            if (completed_assignment_color and assignment["completed"]) or assignment["outdated"]:
                assignment["outdated"] = False  # Mark the assignment as up-to-date
                # Update the event
                utils.patch_assignment_event(calendar_service, event_update_batch, user_settings["calendar_id"],
                                             user_settings["courses"].get(assignment["course_id"], {}), assignment,
                                             completed_assignment_color)

        # Otherwise, if the assignment doesn't have an event associated with it and is not yet completed
        elif not assignment["completed"]:

            # Create an event for it
            utils.create_assignment_event(calendar_service, event_update_batch, user_settings["calendar_id"],
                                          user_settings["courses"].get(assignment["course_id"], {}), assignment,
                                          completed_assignment_color, update_cache(assignment))

    # Execute the batch request asynchronously
    await asyncio.get_running_loop().run_in_executor(None, event_update_batch.execute)

    # Store the updated assignment cache in the database
    db.reference(f'assignments/{uid}').set(assignment_cache)
