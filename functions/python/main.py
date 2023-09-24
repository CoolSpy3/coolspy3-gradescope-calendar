import asyncio
from typing import Any

from firebase_admin import db, initialize_app
from firebase_functions import https_fn, scheduler_fn
from firebase_functions.https_fn import FunctionsErrorCode
from firebase_functions.params import SecretParam
from googleapiclient.discovery import build as build_google_api_service

import utils

app = initialize_app(options={"databaseURL": "http://127.0.0.1:9000/?ns=coolspy3-gradescope-calendar-default-rtdb"})

OAUTH2_CLIENT_ID = SecretParam("GOOGLE_CLIENT_ID")
OAUTH2_CLIENT_SECRET = SecretParam("GOOGLE_CLIENT_SECRET")


@https_fn.on_call()
def update_gradescope_token(req: https_fn.CallableRequest) -> str | dict:
    if not req.auth:
        return utils.fn_response({"success": False}, FunctionsErrorCode.UNAUTHENTICATED)
    uid = req.auth.uid

    if "token" in req.data:
        if utils.check_raw_gradescope_token(req.data["token"]):
            db.reference(f'users/{uid}/gradescope/token').set(req.data["token"])
            db.reference(f'users/{uid}/gradescope/valid_auth').set(True)
            return utils.fn_response({"success": True})
        else:
            return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.INVALID_ARGUMENT)

    if "email" not in req.data or "password" not in req.data:
        return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.INVALID_ARGUMENT)

    token = utils.login_to_gradescope(req.data["email"], req.data["password"])

    if not token:
        return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.INVALID_ARGUMENT)

    db.reference(f'users/{uid}/gradescope/token').set(token)
    db.reference(f'users/{uid}/gradescope/valid_auth').set(True)

    if req.data.get("store-credentials", False):
        db.reference(f'users/{uid}/gradescope/email').set(req.data["email"])
        db.reference(f'users/{uid}/gradescope/password').set(req.data["password"])

    return utils.fn_response({"success": True})


@https_fn.on_call()
def refresh_course_list(req: https_fn.CallableRequest) -> str | dict:
    if not req.auth:
        return utils.fn_response({"success": False}, FunctionsErrorCode.UNAUTHENTICATED)
    uid = req.auth.uid

    gradescope_token = utils.get_gradescope_token(uid)
    if not gradescope_token:
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

    existing_courses = utils.get_db_ref_as_type(f'users/{uid}/settings/courses', dict)
    if not existing_courses:
        existing_courses = {}

    for course_id, course in gradescope_courses.items():
        if course_id in existing_courses:
            course["color"] = existing_courses[course_id]["color"]
        else:
            course["color"] = "1"

    db.reference(f'users/{uid}/settings/courses').set(gradescope_courses)

    return utils.fn_response({"success": True})


@https_fn.on_call()
@utils.sync
async def refresh_events(req: https_fn.CallableRequest) -> str | dict:
    if not req.auth:
        return utils.fn_response({"success": False}, FunctionsErrorCode.UNAUTHENTICATED)
    uid = req.auth.uid

    calendar_id = utils.get_db_ref_as_type(f'users/{uid}/google/calendar_id', str)

    if not calendar_id or not isinstance(calendar_id, str) or calendar_id == "invalid":
        return utils.fn_response("invalid_calendar_selection", FunctionsErrorCode.FAILED_PRECONDITION)

    gradescope_token = utils.get_gradescope_token(uid)
    if not gradescope_token:
        return utils.fn_response("invalid_gradescope_auth", FunctionsErrorCode.PERMISSION_DENIED)

    user_settings = utils.get_db_ref_as_type(f'users/{uid}/settings', dict)

    if not user_settings or "courses" not in user_settings or "completed_assignment_color" not in user_settings:
        return utils.fn_response("invalid_user_settings", FunctionsErrorCode.FAILED_PRECONDITION)

    completed_assignment_color = user_settings["completed_assignment_color"]

    with build_google_api_service('calendar', 'v3', credentials=req.auth.token) as calendar_service:
        calendar = utils.get_calendar(calendar_service, calendar_id)

        if not utils.validate_calendar(calendar):
            db.reference(f'users/{uid}/google/calendar_id').delete()
            return utils.fn_response("invalid_calendar_selection", FunctionsErrorCode.FAILED_PRECONDITION)

        loop = asyncio.get_running_loop()

        def enumerate_calendar_events():
            return list(utils.enumerate_calendar_events(calendar_service, calendar_id))

        event_task = loop.run_in_executor(None, enumerate_calendar_events)

        events, assignments = await asyncio.gather(event_task,
                                                   utils.enumerate_gradescope_assignments(
                                                       user_settings["courses"], gradescope_token))

        assignment_cache = get_updated_assignment_cache(uid, assignments)

        await update_calendar_from_cache(uid, calendar_service, calendar_id, completed_assignment_color, user_settings,
                                         assignment_cache)

    return utils.fn_response({"success": True})


# @scheduler_fn.on_schedule(schedule="0 0 * * *")
@utils.sync
async def update_event_caches(event: scheduler_fn.ScheduledEvent) -> None:
    users = utils.get_db_ref_as_type("users", dict, shallow=True)
    if not users:
        return

    tasks = [update_event_cache_for_user(uid) for uid in users.keys()]
    await asyncio.gather(*tasks)


# @scheduler_fn.on_schedule(schedule="0 0 * * *", secrets=[OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET])
@utils.sync
async def update_calendars(event: scheduler_fn.ScheduledEvent) -> None:
    users = utils.get_db_ref_as_type("users", dict, shallow=True)
    if not users:
        return

    tasks = [update_calendar_for_user(uid) for uid in users.keys()]
    await asyncio.gather(*tasks)


@utils.wrap_async_exceptions
async def update_event_cache_for_user(uid) -> None:
    gradescope_token = utils.get_gradescope_token(uid)
    if not gradescope_token:
        return

    user_settings = utils.get_db_ref_as_type(f'users/{uid}/settings', dict)

    if not user_settings or "courses" not in user_settings or "completed_assignment_color" not in user_settings:
        return

    assignments = await utils.enumerate_gradescope_assignments(user_settings["courses"], gradescope_token)

    assignment_cache = get_updated_assignment_cache(uid, assignments)

    db.reference(f'assignments/{uid}/cache').set(assignment_cache)


@utils.wrap_async_exceptions
async def update_calendar_for_user(uid) -> None:
    calendar_id = utils.get_db_ref_as_type(f'users/{uid}/google/calendar_id', str)

    if not calendar_id or not isinstance(calendar_id, str) or calendar_id == "invalid":
        return

    gradescope_token = utils.get_gradescope_token(uid)
    if not gradescope_token:
        return

    user_settings = utils.get_db_ref_as_type(f'users/{uid}/settings', dict)

    if not user_settings or "courses" not in user_settings or "completed_assignment_color" not in user_settings:
        return

    completed_assignment_color = user_settings["completed_assignment_color"]

    with build_google_api_service('calendar', 'v3',
                                  credentials=utils.login_to_google(uid, OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET)
                                  ) as calendar_service:
        calendar = utils.get_calendar(calendar_service, calendar_id)

        if not utils.validate_calendar(calendar):
            db.reference(f'users/{uid}/google/calendar_id').set("invalid")
            return

        assignment_cache = utils.get_db_ref_as_type(f'assignments/{uid}/cache', dict)
        if not assignment_cache:
            return

        await update_calendar_from_cache(uid, calendar_service, calendar_id, completed_assignment_color, user_settings,
                                         assignment_cache)


def get_updated_assignment_cache(uid: str, gradescope_assignments: dict[str, Any]) -> dict[str, Any]:
    assignment_cache = utils.get_db_ref_as_type(f'assignments/{uid}/cache', dict)
    if not assignment_cache:
        assignment_cache = {}

    return {
        assignment_id: utils.update_gradescope_assignment(assignment, assignment_cache.get(assignment_id, None))
        for assignment_id, assignment in gradescope_assignments.items()
    }


async def update_calendar_from_cache(uid: str, calendar_service: Any, calendar_id: str, completed_assignment_color: str,
                                     user_settings: dict[str, Any], assignment_cache: dict[str, Any]) -> None:
    event_update_batch = calendar_service.new_batch_http_request()

    for assignment_id, assignment in assignment_cache.copy().items():
        if assignment["completed"]:
            assignment_cache.pop(assignment_id, None)

        if assignment["event_id"]:
            if (completed_assignment_color and assignment["completed"]) or assignment["due_data_changed"]:
                utils.patch_assignment_event(calendar_service, event_update_batch, calendar_id, assignment["event_id"],
                                             user_settings["courses"].get(assignment["course_id"], {}),
                                             utils.import_gradescope_assignment(assignment),
                                             completed_assignment_color)

        elif not assignment["completed"]:

            def update_cache(updated_assignment):
                def update_cache_helper(_request_id, response, _exception):
                    updated_assignment["event_id"] = response["id"]

                return update_cache_helper

            utils.create_assignment_event(calendar_service, event_update_batch, calendar_id,
                                          user_settings["courses"].get(assignment["course_id"], {}),
                                          utils.import_gradescope_assignment(assignment),
                                          completed_assignment_color, update_cache(assignment))

    await asyncio.get_event_loop().run_in_executor(None, event_update_batch.execute)

    db.reference(f'assignments/{uid}/cache').set(assignment_cache)
