import asyncio
from typing import Any

from firebase_admin import db, initialize_app
from firebase_functions import https_fn, scheduler_fn
from firebase_functions.https_fn import FunctionsErrorCode
from firebase_functions.params import SecretParam
from googleapiclient.discovery import build as build_google_api_service
from google_auth_oauthlib.flow import Flow

import utils

app = initialize_app(options={"databaseURL": "http://127.0.0.1:9000/?ns=coolspy3-gradescope-calendar-default-rtdb"})

OAUTH2_CLIENT_ID = SecretParam("GOOGLE_CLIENT_ID")
OAUTH2_CLIENT_SECRET = SecretParam("GOOGLE_CLIENT_SECRET")


@https_fn.on_call(secrets=[OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET])
def oauth_callback(request: https_fn.CallableRequest) -> utils.CallableFunctionResponse:
    if not request.auth:
        return utils.fn_response({"success": False}, FunctionsErrorCode.UNAUTHENTICATED)
    uid = request.auth.uid

    if "code" not in request.data:
        return utils.fn_response({"success": False}, FunctionsErrorCode.INVALID_ARGUMENT)

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
    db.reference(f'credentials/{uid}/google/token').set(flow.credentials.refresh_token)
    db.reference(f'auth_status/{uid}/google').set(True)
    return utils.fn_response({"success": True})


@https_fn.on_call()
def update_gradescope_token(req: https_fn.CallableRequest) -> utils.CallableFunctionResponse:
    if not req.auth:
        return utils.fn_response({"success": False}, FunctionsErrorCode.UNAUTHENTICATED)
    uid = req.auth.uid

    if "token" in req.data:
        if utils.check_gradescope_token(req.data["token"]):
            db.reference(f'credentials/{uid}/gradescope/token').set(req.data["token"])
            db.reference(f'auth_status/{uid}/gradescope').set(True)
            return utils.fn_response({"success": True})
        else:
            return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.INVALID_ARGUMENT)

    if "email" not in req.data or "password" not in req.data:
        return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.INVALID_ARGUMENT)

    token, expiration = utils.login_to_gradescope(req.data["email"], req.data["password"])
    if not token:
        return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.INVALID_ARGUMENT)

    db.reference(f'credentials/{uid}/gradescope/token').set(token)
    db.reference(f'auth_status/{uid}/gradescope').set(True)

    if req.data.get("store-credentials", False):
        db.reference(f'credentials/{uid}/gradescope/email').set(req.data["email"])
        db.reference(f'credentials/{uid}/gradescope/password').set(req.data["password"])
    elif expiration:
        return utils.fn_response({"success": True, "expiration": expiration})

    return utils.fn_response({"success": True})


@https_fn.on_call()
def refresh_course_list(req: https_fn.CallableRequest) -> utils.CallableFunctionResponse:
    if not req.auth:
        return utils.fn_response({"success": False}, FunctionsErrorCode.UNAUTHENTICATED)
    uid = req.auth.uid

    if not (gradescope_token := utils.get_gradescope_token(uid)):
        return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.PERMISSION_DENIED)

    gradescope_courses = [
        {
            "name": utils.transform_or_default(course.find("./h3"), lambda course_name: course_name.text,
                                               "<Unknown Course>").strip(),
            "href": course.attrib["href"]
        }
        for course in utils.get_data_from_gradescope("",
                                                     ".//div[@class='courseList']/div["
                                                     "@class='courseList--coursesForTerm'][2]/a[@class='courseBox']",
                                                     gradescope_token)
    ]
    gradescope_courses = {
        course["href"][course["href"].rindex('/') + 1:]: course for course in
        gradescope_courses
    }

    existing_courses = utils.get_db_ref_as_type(f'settings/{uid}/courses', utils.CourseSettings) or {}

    for course_id, course in gradescope_courses.items():
        if course_id in existing_courses:
            course["color"] = existing_courses[course_id]["color"]
        else:
            course["color"] = "1"

    db.reference(f'settings/{uid}/courses').set(gradescope_courses)

    return utils.fn_response({"success": True})


@https_fn.on_call()
@utils.sync
async def refresh_events(req: https_fn.CallableRequest) -> utils.CallableFunctionResponse:
    if not req.auth:
        return utils.fn_response({"success": False}, FunctionsErrorCode.UNAUTHENTICATED)
    uid = req.auth.uid

    if not (calendar_id := utils.get_calendar_id(uid)):
        return utils.fn_response("invalid_calendar_selection", FunctionsErrorCode.FAILED_PRECONDITION)

    if not (user_settings := utils.get_user_settings(uid)):
        return utils.fn_response("invalid_user_settings", FunctionsErrorCode.FAILED_PRECONDITION)

    if not (gradescope_token := utils.get_gradescope_token(uid)):
        return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.PERMISSION_DENIED)

    completed_assignment_color = user_settings["completed_assignment_color"]

    with build_google_api_service('calendar', 'v3', credentials=req.auth.token) as calendar_service:
        if not utils.validate_calendar_id(calendar_id, calendar_service):
            db.reference(f'settings/{uid}/calendar_id').delete()
            return utils.fn_response("invalid_calendar_selection", FunctionsErrorCode.FAILED_PRECONDITION)

        assignment_cache = await get_updated_assignment_cache(uid, user_settings, gradescope_token)

        await update_calendar_from_cache(uid, calendar_service, calendar_id, completed_assignment_color, user_settings,
                                         assignment_cache)

    return utils.fn_response({"success": True})


# @scheduler_fn.on_schedule(schedule="0 0 * * *")
@utils.sync
async def update_event_caches(_event: scheduler_fn.ScheduledEvent) -> None:
    users = utils.get_db_ref_as_type("users", dict, shallow=True)
    if not users:
        return

    tasks = [update_event_cache_for_user(uid) for uid in users.keys()]
    await asyncio.gather(*tasks)


# @scheduler_fn.on_schedule(schedule="0 0 * * *", secrets=[OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET])
@utils.sync
async def update_calendars(_event: scheduler_fn.ScheduledEvent) -> None:
    users = utils.get_db_ref_as_type("users", dict, shallow=True)
    if not users:
        return

    tasks = [update_calendar_for_user(uid) for uid in users.keys()]
    await asyncio.gather(*tasks)


@utils.wrap_async_exceptions
async def update_event_cache_for_user(uid) -> None:
    if (gradescope_token := utils.get_gradescope_token(uid)) and (user_settings := utils.get_user_settings(uid)):
        assignment_cache = await get_updated_assignment_cache(uid, user_settings, gradescope_token)

        db.reference(f'assignments/{uid}').set(assignment_cache)


@utils.wrap_async_exceptions
async def update_calendar_for_user(uid) -> None:
    if not (calendar_id := utils.get_calendar_id(uid)) or \
            not (user_settings := utils.get_user_settings(uid)):
        return

    completed_assignment_color = user_settings["completed_assignment_color"]

    with build_google_api_service('calendar', 'v3',
                                  credentials=utils.login_to_google(uid, OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET)
                                  ) as calendar_service:

        if not utils.validate_calendar_id(calendar_id, calendar_service):
            db.reference(f'settings/{uid}/calendar_id').set("invalid")
            return

        if not (assignment_cache := utils.get_db_ref_as_type(f'assignments/{uid}', dict)):
            return

        await update_calendar_from_cache(uid, calendar_service, calendar_id, completed_assignment_color, user_settings,
                                         assignment_cache)


async def get_updated_assignment_cache(uid: str, user_settings: dict[str, Any], gradescope_token: str) \
        -> dict[str, Any]:
    assignments = await utils.enumerate_gradescope_assignments(user_settings["courses"], gradescope_token)

    assignment_cache = utils.get_db_ref_as_type(f'assignments/{uid}', dict) or {}

    for assignment_id, assignment in assignments.items():
        utils.update_gradescope_assignment(assignment, assignment_cache.get(assignment_id, None))

    return assignment_cache


async def update_calendar_from_cache(uid: str, calendar_service: Any, calendar_id: str, completed_assignment_color: str,
                                     user_settings: dict[str, Any], assignment_cache: utils.AssignmentList) -> None:
    event_update_batch = calendar_service.new_batch_http_request()

    for assignment_id, assignment in assignment_cache.copy().items():
        if assignment["completed"]:
            assignment_cache.pop(assignment_id, None)

        if assignment["event_id"]:
            if (completed_assignment_color and assignment["completed"]) or assignment["due_data_changed"]:
                utils.patch_assignment_event(calendar_service, event_update_batch, calendar_id, assignment["event_id"],
                                             user_settings["courses"].get(assignment["course_id"], {}), assignment,
                                             completed_assignment_color)

        elif not assignment["completed"]:

            def update_cache(updated_assignment):
                def update_cache_helper(_request_id, response, _exception):
                    updated_assignment["event_id"] = response["id"]

                return update_cache_helper

            utils.create_assignment_event(calendar_service, event_update_batch, calendar_id,
                                          user_settings["courses"].get(assignment["course_id"], {}), assignment,
                                          completed_assignment_color, update_cache(assignment))

    await asyncio.get_event_loop().run_in_executor(None, event_update_batch.execute)

    db.reference(f'assignments/{uid}/cache').set(assignment_cache)
